from __future__ import annotations

import ast
from collections import Counter
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".txt", ".in"}
SKIP_PARTS = {".git", ".venv", "build", "dist", "__pycache__", ".pytest_cache", ".mypy_cache"}
MAX_TEXT_BYTES = 2_000_000
MAX_TOTAL_BYTES = 20_000_000
MAX_ENTROPY_CANDIDATES_PER_FILE = 2_000
REQUIRED = (
    "README.md",
    "AI_ASSISTANCE.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "MANIFEST.in",
    "SECURITY.md",
    "pyproject.toml",
    ".gitignore",
    ".github/workflows/ci.yml",
    "examples/basic.json",
    "examples/flagship_workflow.json",
    "examples/synthetic_payload.json",
    "docs/ARCHITECTURE.md",
    "docs/WORKFLOW_SCHEMA.md",
    "docs/OPERATIONS.md",
    "docs/THREAT_MODEL.md",
    "schema/workflow-v1.0.schema.json",
)
PRIVATE_MARKERS = ("sky" + "om", "vigilanty" + "0x", "/workspace/" + "scratch/")
SECRET_PATTERNS = {
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_fine_grained": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    "github_classic": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "bearer_token": re.compile(r"(?i)\b(?:authorization\s*:\s*)?bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "stripe_live_key": re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "pem_private_key": re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    "connection_credential": re.compile(r"(?i)\b(?:database_url|connection_string|dsn)\s*=\s*[a-z][a-z0-9+.-]*://[^\s:/]+:[^\s@]+@(?![^\s/]*\.invalid(?:[/:]|\s|$))[^\s/]+"),
    "sensitive_assignment": re.compile(r"(?i)\b(?:api[_-]?key|password|secret|token)\s*=\s*(?!re\.compile\b)(?!<[^>]+>|replace-me\b|changeme\b)[^\s]{8,}"),
}
ENTROPY_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9][A-Za-z0-9_+/=-]{31,199})(?![A-Za-z0-9])")
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
PINNED_ACTIONS = (
    "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2",
    "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5",
)
SIBLING_MODULES = {
    "hybrid_search_playground", "semantic_index_doctor", "sqlite_health_doctor",
    "migration_verifier", "schema_contract_tester", "data_freshness_monitor",
    "dataset_versioner", "duplicate_finder", "secrets_hygiene", "env_example_guard",
    "security_headers_lab", "ssrf_guard_demo", "permission_matrix", "audit_trail_lite",
    "automation_control_plane", "workflow_templates",
}


def _high_entropy(value: str) -> bool:
    if UUID.fullmatch(value) or re.fullmatch(r"[0-9a-fA-F]+", value):
        return False
    if not (re.search(r"[a-z]", value) and re.search(r"[A-Z]", value) and re.search(r"\d", value)):
        return False
    counts = Counter(value)
    entropy = -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())
    return entropy >= 4.2


def _secret_kinds(text: str) -> set[str]:
    kinds = {kind for kind, pattern in SECRET_PATTERNS.items() if pattern.search(text)}
    candidates = ENTROPY_TOKEN.findall(text)[:MAX_ENTROPY_CANDIDATES_PER_FILE]
    if any(_high_entropy(candidate) for candidate in candidates):
        kinds.add("high_entropy")
    return kinds


def main() -> int:
    problems: list[str] = []
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            problems.append(f"missing {name}")
    total_bytes = 0
    for path in ROOT.rglob("*"):
        if path.is_symlink():
            problems.append(f"symlink is not allowed: {path.relative_to(ROOT)}")
            continue
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name != ".gitignore":
            continue
        size = path.stat().st_size
        total_bytes += size
        if size > MAX_TEXT_BYTES:
            problems.append(f"text file exceeds scan limit: {path.relative_to(ROOT)}")
            continue
        if total_bytes > MAX_TOTAL_BYTES:
            problems.append("aggregate text scan limit exceeded")
            break
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        if path.suffix == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                problems.append(f"invalid JSON: {path.relative_to(ROOT)}:{exc.lineno}")
        folded = text.casefold()
        if any(marker in folded for marker in PRIVATE_MARKERS):
            problems.append(f"private boundary marker: {path.relative_to(ROOT)}")
        secret_text = text
        if ROOT.name == "secrets-hygiene" and path.relative_to(ROOT).as_posix() == "tests/test_core.py":
            known_public_fixture = "aB3dE5fG7hJ9kL2m" + "N4pQ6rS8tV1wX3yZ"
            secret_text = secret_text.replace(known_public_fixture, "")
        if _secret_kinds(secret_text):
            problems.append(f"credential-like content: {path.relative_to(ROOT)}")
    ignore = ROOT / ".gitignore"
    if ignore.is_file() and "\\n" in ignore.read_text(encoding="utf-8"):
        problems.append(".gitignore contains literal backslash-newline text")
    example = ROOT / "examples/basic.json"
    if example.is_file():
        try:
            payload = json.loads(example.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                problems.append("examples/basic.json must contain a JSON object")
        except (OSError, json.JSONDecodeError):
            problems.append("examples/basic.json is invalid JSON")
    readme = ROOT / "README.md"
    if readme.is_file():
        body = readme.read_text(encoding="utf-8").casefold()
        for heading in ("purpose", "non-goals", "install", "cli and api", "example", "security and trust model", "limitations", "tests", "ai assistance", "license"):
            if f"## {heading}" not in body:
                problems.append(f"README missing section: {heading}")
    workflow = ROOT / ".github/workflows/ci.yml"
    if workflow.is_file():
        ci = workflow.read_text(encoding="utf-8")
        for action in PINNED_ACTIONS:
            if action not in ci:
                problems.append(f"CI missing pinned action: {action.split('@')[0]}")
        for match in re.findall(r"uses:\s*([^\s]+)", ci):
            reference = match.rsplit("@", 1)[-1]
            if not re.fullmatch(r"[0-9a-f]{40}", reference):
                problems.append(f"CI action is not pinned to a commit: {match}")
        for required in ("contents: read", "timeout-minutes:", "python -m build --no-isolation", "examples/basic.json"):
            if required not in ci:
                problems.append(f"CI missing control: {required}")
    project = ROOT / "pyproject.toml"
    if project.is_file() and 'requires = ["setuptools==83.0.0"]' not in project.read_text(encoding="utf-8"):
        problems.append("build backend is not exactly pinned")
    source_root = ROOT / "src"
    own_modules = {path.name for path in source_root.iterdir() if path.is_dir()} if source_root.is_dir() else set()
    for path in source_root.rglob("*.py") if source_root.is_dir() else ():
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            problems.append(f"invalid Python syntax: {path.relative_to(ROOT)}:{exc.lineno}")
            continue
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        for imported in imported_roots:
            if imported not in sys.stdlib_module_names and imported not in own_modules:
                problems.append(f"runtime dependency is not standard-library or local: {path.relative_to(ROOT)} imports {imported}")
        for imported in re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", source, flags=re.MULTILINE):
            if imported in SIBLING_MODULES - own_modules:
                problems.append(f"source imports sibling package: {path.relative_to(ROOT)}")
    handler_source = source_root / "automation_control_plane" / "handlers.py"
    if handler_source.is_file():
        handler_text = handler_source.read_text(encoding="utf-8")
        for forbidden in ("import subprocess", "import socket", "import urllib", "import importlib", "os.system", "eval(", "exec("):
            if forbidden in handler_text:
                problems.append(f"handler registry contains forbidden execution primitive: {forbidden}")
    cli_paths = list(source_root.glob("*/cli.py")) if source_root.is_dir() else []
    if len(cli_paths) != 1:
        problems.append("expected exactly one package CLI")
    elif not all(marker in cli_paths[0].read_text(encoding="utf-8") for marker in ("MAX_INPUT_BYTES", "allow_nan=False", "MAX_ERROR_MESSAGE")):
        problems.append("CLI is missing bounded input/output controls")
    if problems:
        print("\n".join(sorted(set(problems))), file=sys.stderr)
        return 1
    print("public-boundary and repository checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
