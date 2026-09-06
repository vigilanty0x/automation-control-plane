"""Bounded no-link reads for new template inputs, independent of legacy CLI IO."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

MAX_INPUT_BYTES=1024*1024


def input_path(value: Path) -> Path:
    path=Path(value)
    if '..' in path.parts or (path.drive and not path.is_absolute()):raise ValueError('template input path refused')
    path=path if path.is_absolute() else Path.cwd()/path
    if len(path.parts)>64 or len(str(path))>4096:raise ValueError('template input path limit')
    if os.name=='nt':
        if re.fullmatch(r'[A-Za-z]:\\',path.anchor) is None:raise ValueError('template input must be on a local drive')
        for name in path.parts[1:]:
            if (any(ord(c)<32 or c in '<>:"|?*' for c in name) or name.endswith((' ','.'))
                or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',name)):
                raise ValueError('template input path refused')
    return path


def _base(info):return (info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns)


def _file(info,maximum):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>maximum
        or getattr(info,'st_file_attributes',0)&0x400):raise ValueError('template input must be a bounded regular file without links')


def _read(fd,maximum):
    raw=bytearray()
    while len(raw)<=maximum:
        block=os.read(fd,min(65536,maximum+1-len(raw)))
        if not block:break
        raw.extend(block)
        if len(raw)>maximum:raise ValueError('template input grew beyond byte limit')
    return bytes(raw)


def _read_posix(path,maximum):
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
    descriptors=[os.open('/',flags)];chain=[];fd=None
    try:
        for name in path.parts[1:-1]:
            parent=descriptors[-1];child=os.open(name,flags,dir_fd=parent)
            descriptors.append(child);info=os.fstat(child)
            chain.append((parent,name,child,(info.st_dev,info.st_ino,info.st_mode)))
        parent=descriptors[-1];name=path.name
        expected=os.stat(name,dir_fd=parent,follow_symlinks=False);_file(expected,maximum)
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=parent)
        before=os.fstat(fd);_file(before,maximum)
        identity=lambda info:(_base(info),info.st_ctime_ns)
        if identity(expected)!=identity(before):raise ValueError('template input changed before read')
        raw=_read(fd,maximum)
        after=os.fstat(fd);entry=os.stat(name,dir_fd=parent,follow_symlinks=False)
        if identity(before)!=identity(after) or identity(after)!=identity(entry) or len(raw)!=before.st_size:raise ValueError('template input changed during read')
        for parent,name,child,expected_directory in chain:
            current=os.stat(name,dir_fd=parent,follow_symlinks=False);opened=os.fstat(child)
            if ((current.st_dev,current.st_ino,current.st_mode)!=expected_directory
                or (opened.st_dev,opened.st_ino,opened.st_mode)!=expected_directory):raise ValueError('template input parent changed')
        return raw
    finally:
        if fd is not None:os.close(fd)
        for descriptor in reversed(descriptors):os.close(descriptor)


def _windows_open(path):
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel=ctypes.WinDLL('kernel32.dll',use_last_error=True,winmode=0x800)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.GetFileType.argtypes=[wintypes.HANDLE];kernel.GetFileType.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    kernel.GetFileInformationByHandleEx.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.LPVOID,wintypes.DWORD]
    kernel.GetFileInformationByHandleEx.restype=wintypes.BOOL
    class Basic(ctypes.Structure):
        _fields_=[('CreationTime',ctypes.c_longlong),('LastAccessTime',ctypes.c_longlong),
                  ('LastWriteTime',ctypes.c_longlong),('ChangeTime',ctypes.c_longlong),('FileAttributes',wintypes.DWORD)]
    # Local absolute path already validated. Extended syntax avoids any need to
    # change the machine's long-path policy; caller device/UNC syntax is refused.
    # Share reads only. Existing writers are refused; new writes and deletes
    # cannot race the bounded read even when file timestamps are unchanged.
    handle=kernel.CreateFileW('\\\\?\\'+str(path),0x80000000,1,None,3,0x00200000|0x08000000,None)
    if handle==wintypes.HANDLE(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    fd=None
    try:
        if kernel.GetFileType(handle)!=1:raise ValueError('template input is not a disk file')
        def metadata():
            value=Basic()
            if not kernel.GetFileInformationByHandleEx(handle,0,ctypes.byref(value),ctypes.sizeof(value)):
                raise ctypes.WinError(ctypes.get_last_error())
            if value.FileAttributes&0x400:raise ValueError('template reparse point refused')
            return value.CreationTime,value.LastWriteTime,value.ChangeTime,value.FileAttributes
        metadata()
        fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
        return fd,metadata
    except BaseException:
        if fd is None:kernel.CloseHandle(handle)
        else:os.close(fd)
        raise


def _read_windows(path,maximum):
    chain=[]
    for entry in reversed((path,*path.parents)):
        info=entry.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('template input link refused')
        if entry!=path and not stat.S_ISDIR(info.st_mode):raise ValueError('template input parent is not a directory')
        chain.append((entry,info))
    expected=chain[-1][1];_file(expected,maximum)
    fd,metadata=_windows_open(path)
    try:
        before=os.fstat(fd);_file(before,maximum);native_before=metadata()
        if _base(expected)!=_base(before):raise ValueError('template input changed before read')
        raw=_read(fd,maximum)
        if (_base(before)!=_base(os.fstat(fd)) or native_before!=metadata() or len(raw)!=before.st_size):raise ValueError('template input changed during read')
        for entry,expected_entry in chain:
            after=entry.lstat()
            if (stat.S_ISLNK(after.st_mode) or getattr(after,'st_file_attributes',0)&0x400
                or (after.st_dev,after.st_ino,after.st_mode)!=(expected_entry.st_dev,expected_entry.st_ino,expected_entry.st_mode)):
                raise ValueError('template input path changed')
            if entry==path and (_base(after)!=_base(expected_entry) or after.st_ctime_ns!=expected_entry.st_ctime_ns):raise ValueError('template input changed during read')
        return raw
    finally:os.close(fd)


def read_input(value: Path, *, maximum: int) -> bytes:
    if type(maximum) is not int or not 1<=maximum<=MAX_INPUT_BYTES:raise ValueError('invalid template byte limit')
    path=input_path(value)
    if os.name=='posix' and hasattr(os,'O_NOFOLLOW'):return _read_posix(path,maximum)
    if os.name=='nt':return _read_windows(path,maximum)
    raise ValueError('template input reader unavailable on this platform')
