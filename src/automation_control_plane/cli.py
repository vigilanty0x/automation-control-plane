"""Machine-readable bounded JSON command-line interface."""

import json
from pathlib import Path
import sys

from .core import run

MAX_INPUT_BYTES = 25_000_000
MAX_ERROR_MESSAGE = 512


def _load_input():
    if len(sys.argv) > 2:
        raise ValueError("expected at most one input path")
    if len(sys.argv) == 2:
        path = Path(sys.argv[1])
        if not path.is_file():
            raise ValueError("input path must be a file")
        with path.open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
    else:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input byte limit exceeded")
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("input must be UTF-8 JSON") from exc


def main():
    try:
        result = run(_load_input())
        print(json.dumps({"success": True, "result": result}, sort_keys=True, allow_nan=False))
        raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as exc:
        message = str(exc).replace("\n", " ")[:MAX_ERROR_MESSAGE]
        print(json.dumps({"success": False, "error": type(exc).__name__, "message": message}, sort_keys=True))
        raise SystemExit(2)


if __name__ == "__main__":
    main()

