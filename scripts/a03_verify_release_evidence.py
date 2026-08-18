"""Verify A03 candidate hashes and fail closed on mutation."""
from __future__ import annotations
import argparse
from hashlib import sha256
from pathlib import Path

class VerificationError(ValueError):
    pass

def verify(dist: Path, sums: Path) -> None:
    expected = {}
    try:
        for line in sums.read_text(encoding="utf-8").splitlines():
            digest, name = line.split("  ", 1)
            if name in expected or len(digest) != 64:
                raise VerificationError("invalid or duplicate checksum entry")
            expected[name] = digest
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, VerificationError): raise
        raise VerificationError("cannot parse checksum file") from exc
    observed = {path.name for path in dist.iterdir() if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))}
    if observed != set(expected):
        raise VerificationError(f"artifact set mismatch: expected={sorted(expected)} observed={sorted(observed)}")
    for name, wanted in expected.items():
        try: actual = sha256((dist / name).read_bytes()).hexdigest()
        except OSError as exc: raise VerificationError(f"cannot read artifact: {name}") from exc
        if actual != wanted: raise VerificationError(f"artifact digest mismatch: {name}")

def main(argv=None)->int:
    p=argparse.ArgumentParser();p.add_argument("--dist",type=Path,required=True);p.add_argument("--sums",type=Path,required=True);a=p.parse_args(argv)
    try: verify(a.dist,a.sums)
    except (OSError,VerificationError) as exc:
        print(f"A03 verification blocked: {exc}")
        return 2
    print("A03 release evidence verified")
    return 0
if __name__=="__main__": raise SystemExit(main())
