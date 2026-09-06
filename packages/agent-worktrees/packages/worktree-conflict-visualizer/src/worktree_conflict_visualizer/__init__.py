"""Bounded overlap analysis for declared worktree path sets."""

import argparse
import hashlib
import html
import json
import re
from pathlib import PurePosixPath

NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
MAX_WORKTREES = 100
MAX_FILES_PER_WORKTREE = 5_000
MAX_TOTAL_FILES = 10_000


def _safe_path(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 512 or "\\" in value
            or any(ord(c) < 32 for c in value)):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and value not in {".", ".."} and ".." not in path.parts and path.as_posix() == value


def analyze(worktrees):
    if not isinstance(worktrees, list) or len(worktrees) > MAX_WORKTREES:
        return {"ok": False, "errors": ["worktree_bound"]}
    names, parsed, total = [], [], 0
    for worktree in worktrees:
        if not isinstance(worktree, dict) or set(worktree) != {"name", "files"}:
            return {"ok": False, "errors": ["invalid_worktree"]}
        name, files = worktree["name"], worktree["files"]
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
            return {"ok": False, "errors": ["invalid_or_duplicate_name"]}
        if not isinstance(files, list) or len(files) > MAX_FILES_PER_WORKTREE:
            return {"ok": False, "errors": ["file_bound"]}
        if any(not _safe_path(path) for path in files):
            return {"ok": False, "errors": ["invalid_path"]}
        if len(files) != len(set(files)):
            return {"ok": False, "errors": ["duplicate_path_in_worktree"]}
        total += len(files)
        if total > MAX_TOTAL_FILES:
            return {"ok": False, "errors": ["global_file_bound"]}
        names.append(name)
        parsed.append((name, set(files)))
    owners = {}
    for name, files in parsed:
        for path in files:
            owners.setdefault(path, set()).add(name)
    overlaps = [{"path": path, "worktrees": sorted(owner_set)}
                for path, owner_set in sorted(owners.items()) if len(owner_set) > 1]
    pairs = {}
    for overlap in overlaps:
        owner_list = overlap["worktrees"]
        for index, left in enumerate(owner_list):
            for right in owner_list[index + 1:]:
                if left != right:
                    pairs[(left, right)] = pairs.get((left, right), 0) + 1
    ids = {name: f"wt_{index:03d}" for index, name in enumerate(sorted(names))}
    lines = ["flowchart LR"]
    for name in sorted(names):
        label = html.escape(name, quote=True)
        lines.append(f"  {ids[name]}[{json.dumps(label)}]")
    for (left, right), count in sorted(pairs.items()):
        lines.append(f"  {ids[left]} ---|{count} files| {ids[right]}")
    body = {"overlaps": overlaps,
            "pairs": [{"left": left, "right": right, "files": count}
                      for (left, right), count in sorted(pairs.items())],
            "mermaid": "\n".join(lines),
            "risk": "high" if len(overlaps) > 10 else "medium" if overlaps else "low"}
    return {"ok": True, **body,
            "analysis_sha256": hashlib.sha256(json.dumps(body, sort_keys=True,
                                                           separators=(",", ":")).encode()).hexdigest()}


def probe():
    good = analyze([{"name": "a", "files": ["x"]}, {"name": "b", "files": ["x"]}])
    bad = analyze([{"name": "a", "files": ["../x"]}])
    return {"ok": good["ok"] and len(good["overlaps"]) == 1 and not bad["ok"],
            "path_counter_proof": not bad["ok"]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("analyze", "probe"))
    parser.add_argument("--input")
    args = parser.parse_args(argv)
    try:
        data = json.load(open(args.input, encoding="utf-8")) if args.input else {}
        out = probe() if args.command == "probe" else analyze(data.get("worktrees") if isinstance(data, dict) else None)
    except (OSError, UnicodeError, json.JSONDecodeError):
        out = {"ok": False, "errors": ["input_unreadable"]}
    print(json.dumps(out, sort_keys=True))
    return 0 if out["ok"] else 2
