"""Fail-closed local state permissions without process-global umask changes."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .errors import ValidationError


def secure_directory(path: str | Path) -> Path:
    """Create/validate a data directory and make it owner-only on POSIX."""

    root = Path(path)
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise ValidationError("data directory must be a regular non-symlink directory")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "posix":
        if root.is_symlink() or not root.is_dir():
            raise ValidationError("data directory must be a regular non-symlink directory")
        return root
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise ValidationError(f"cannot securely open data directory: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValidationError("data directory did not resolve to a directory")
        if metadata.st_uid != os.geteuid():
            raise ValidationError("data directory is not owned by the current user")
        os.fchmod(descriptor, 0o700)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
            raise ValidationError("data directory owner-only permissions could not be enforced")
    except OSError as exc:
        raise ValidationError(f"cannot enforce data directory permissions: {exc}") from exc
    finally:
        os.close(descriptor)
    return root


def secure_regular_file(path: str | Path, *, mode: int = 0o600) -> None:
    """Validate ownership/type before restricting an existing state file on POSIX."""

    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValidationError("local state path must be a regular non-symlink file")
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise ValidationError(f"cannot securely open local state file: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValidationError("local state file must be a singly-linked regular file")
        if metadata.st_uid != os.geteuid():
            raise ValidationError("local state file is not owned by the current user")
        os.fchmod(descriptor, mode)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != mode:
            raise ValidationError("local state file permissions could not be enforced")
    except OSError as exc:
        raise ValidationError(f"cannot enforce local state file permissions: {exc}") from exc
    finally:
        os.close(descriptor)
