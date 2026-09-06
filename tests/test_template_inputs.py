"""Real bounded reads and controlled concurrent mutations of synthetic files."""
from contextlib import redirect_stdout,redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_software_factory import template_inputs as reader
from ai_software_factory.cli import main
from ai_software_factory.templates import CATALOG_FORMAT
from tests.support import spec


class TemplateInputTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.path=self.root/'input.json'
        self.path.write_bytes(b'12345678')

    def test_regular_exact_bytes_and_read_budget(self):
        native=os.read;consumed=[]
        def read(fd,n):
            raw=native(fd,n);consumed.append(len(raw));return raw
        with patch.object(reader.os,'read',side_effect=read):
            self.assertEqual(reader.read_input(self.path,maximum=8),b'12345678')
        self.assertLessEqual(sum(consumed),9)

    def test_invalid_limit_and_preexisting_oversize_do_not_read(self):
        with patch.object(reader.os,'read',side_effect=AssertionError('must not read')):
            for maximum in (True,0,-1,1.0,2*1024*1024,4):
                with self.subTest(maximum=maximum),self.assertRaises(ValueError):reader.read_input(self.path,maximum=maximum)

    def test_actual_concurrent_growth_never_reads_more_than_limit_plus_one(self):
        entered=threading.Event();finished=threading.Event();errors=[];native=os.read;consumed=[]
        def mutate():
            try:
                if not entered.wait(5):raise RuntimeError('reader did not start')
                with self.path.open('ab') as f:f.write(b'g'*1024)
            except BaseException as exc:errors.append(exc)
            finally:finished.set()
        worker=threading.Thread(target=mutate);worker.start()
        def read(fd,n):
            entered.set();self.assertTrue(finished.wait(5))
            raw=native(fd,n);consumed.append(len(raw));return raw
        try:
            with patch.object(reader.os,'read',side_effect=read),self.assertRaises(ValueError):reader.read_input(self.path,maximum=16)
        finally:entered.set();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[]);self.assertLessEqual(sum(consumed),17)

    def test_same_size_write_with_restored_mtime_is_refused(self):
        original=self.path.stat();native=os.read;changed=False
        def read(fd,n):
            nonlocal changed
            if not changed:
                self.path.write_bytes(b'abcdefgh');os.utime(self.path,ns=(original.st_atime_ns,original.st_mtime_ns));changed=True
            return native(fd,n)
        with patch.object(reader.os,'read',side_effect=read),self.assertRaises(ValueError):reader.read_input(self.path,maximum=16)

    def test_leaf_replacement_is_refused(self):
        replacement=self.root/'new.json';replacement.write_bytes(b'abcdefgh');native=os.read;changed=False
        if os.name=='nt':
            # Windows denies overwriting the already-open destination. Exercise
            # the real race between path validation and no-follow handle open.
            original_open=reader._windows_open
            def replace_before_open(path):
                os.replace(replacement,self.path);return original_open(path)
            with patch.object(reader,'_windows_open',side_effect=replace_before_open),self.assertRaises(ValueError):reader.read_input(self.path,maximum=16)
            self.assertEqual(self.path.read_bytes(),b'abcdefgh');return
        def read(fd,n):
            nonlocal changed
            if not changed:os.replace(replacement,self.path);changed=True
            return native(fd,n)
        with patch.object(reader.os,'read',side_effect=read),self.assertRaises(ValueError):reader.read_input(self.path,maximum=16)

    def test_parent_replacement_is_refused(self):
        directory=self.root/'parent';directory.mkdir();path=directory/'input.json';path.write_bytes(b'12345678')
        native=os.read;changed=False
        if os.name=='nt':
            original_open=reader._windows_open
            def replace_before_open(open_path):
                directory.rename(self.root/'retained-parent');directory.mkdir();(directory/'input.json').write_bytes(b'abcdefgh')
                return original_open(open_path)
            with patch.object(reader,'_windows_open',side_effect=replace_before_open),self.assertRaises(ValueError):reader.read_input(path,maximum=16)
            self.assertEqual(path.read_bytes(),b'abcdefgh');return
        def read(fd,n):
            nonlocal changed
            if not changed:
                directory.rename(self.root/'retained-parent');directory.mkdir();(directory/'input.json').write_bytes(b'abcdefgh');changed=True
            return native(fd,n)
        with patch.object(reader.os,'read',side_effect=read),self.assertRaises(ValueError):reader.read_input(path,maximum=16)

    def test_hardlink_is_refused_before_content_read(self):
        alias=self.root/'hardlink.json';os.link(self.path,alias)
        with patch.object(reader.os,'read',side_effect=AssertionError('linked data read')),self.assertRaises(ValueError):reader.read_input(alias,maximum=16)

    def test_symlink_leaf_and_parent_are_refused(self):
        alias=self.root/'symbolic.json'
        try:alias.symlink_to(self.path)
        except OSError as exc:
            if os.name=='nt' and getattr(exc,'winerror',None)==1314:self.skipTest('Windows account cannot create symbolic links; no privilege change')
            raise
        directory=self.root/'linked-parent';directory.symlink_to(self.root,target_is_directory=True)
        for path in (alias,directory/'input.json'):
            with self.subTest(path=path.name),self.assertRaises((ValueError,OSError)):reader.read_input(path,maximum=16)

    def test_special_file_and_unsafe_path_are_refused(self):
        special=self.root/'special'
        if os.name=='posix':os.mkfifo(special)
        else:special.mkdir()
        with self.assertRaises(ValueError):reader.read_input(special,maximum=16)
        with self.assertRaises(ValueError):reader.read_input(self.root/'..'/'input.json',maximum=16)
        if os.name=='nt':
            for path in ('C:relative.json',r'\\server\share\input.json',r'\\?\C:\input.json',str(self.root/'NUL')):
                with self.subTest(path=path),self.assertRaises(ValueError):reader.input_path(Path(path))

    def test_windows_reparse_metadata_rejected_before_open(self):
        native=Path.lstat
        def metadata(path):
            info=native(path)
            if path!=self.path:return info
            return SimpleNamespace(st_mode=info.st_mode,st_file_attributes=0x400)
        with patch.object(Path,'lstat',metadata),patch.object(reader,'_windows_open',side_effect=AssertionError('reparse opened')),self.assertRaises(ValueError):
            reader._read_windows(self.path,16)

    def test_descriptor_closed_after_read_failure(self):
        descriptors=[]
        def fail(fd,n):descriptors.append(fd);raise OSError('synthetic read failure')
        with patch.object(reader.os,'read',side_effect=fail),self.assertRaises(OSError):reader.read_input(self.path,maximum=16)
        self.assertEqual(len(descriptors),1)
        with self.assertRaises(OSError):os.fstat(descriptors[0])

    def test_template_cli_uses_new_reader_and_preserves_valid_compilation(self):
        source=self.root/'catalog.json';bindings=self.root/'bindings.json'
        source.write_text(json.dumps({'format':CATALOG_FORMAT,'templates':{'plain':spec()}}));bindings.write_text('{}')
        with patch('ai_software_factory.cli._read_text_bounded',side_effect=AssertionError('legacy reader used')),redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['template-compile',str(source),'--template','plain','--bindings',str(bindings)]),0)
        self.assertEqual(json.loads(out.getvalue())['origin']['template_id'],'plain')
        os.link(bindings,self.root/'bindings-alias.json');database=self.root/'not-created.sqlite3'
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(['template-plan',str(source),'--template','plain','--bindings',str(bindings),'--db',str(database)]),2)
        self.assertFalse(database.exists())


if __name__=='__main__':unittest.main()
