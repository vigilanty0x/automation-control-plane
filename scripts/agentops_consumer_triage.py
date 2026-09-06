from __future__ import annotations

import argparse
import base64
from hashlib import sha256
import json
import os
from pathlib import PurePosixPath
import re
import sys
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
USER_AGENT = "agentops-public-consumer-triage/1"
MAX_FILE_BYTES = 1_000_000
MAX_REFERENCES = 2_000
EVIDENCE_RE = re.compile(
    r"^github://(?P<owner>[A-Za-z0-9-]{1,39})/(?P<repo>[A-Za-z0-9._-]{1,100})@"
    r"(?P<sha>[0-9a-f]{40})/(?P<path>.+)#L(?P<line>[1-9][0-9]*)$"
)


class TriageError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _aliases(repository: str) -> tuple[str, ...]:
    values = {repository.casefold(), repository.replace("-", "_").casefold()}
    compact = repository.replace("-", "").casefold()
    if len(compact) >= 5:
        values.add(compact)
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


def parse_evidence_ref(value: str) -> dict[str, Any]:
    if not isinstance(value, str) or len(value) > 700:
        raise TriageError("evidence reference must be a bounded string")
    match = EVIDENCE_RE.fullmatch(value)
    if not match:
        raise TriageError("evidence reference is not a SHA-bound github:// line reference")
    path = match.group("path")
    if path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise TriageError("evidence path must be a safe relative path")
    return {
        "owner": match.group("owner"),
        "repo": match.group("repo"),
        "sha": match.group("sha"),
        "path": path,
        "line": int(match.group("line")),
    }


def is_strong_import_reference(path: str, line: str, aliases: tuple[str, ...]) -> bool:
    if not isinstance(path, str) or not isinstance(line, str):
        return False
    folded = line.casefold()
    if not any(alias in folded for alias in aliases):
        return False
    stripped = folded.lstrip()
    if not stripped:
        return False
    if stripped.startswith(("//", "/*", "* ", "# ")):
        return False

    suffix = PurePosixPath(path.casefold()).suffix
    if suffix == ".py":
        return bool(re.match(r"^(?:from|import)\s+", stripped))
    if suffix in {".js", ".jsx", ".mjs", ".ts", ".tsx"}:
        return bool(
            re.search(r"^(?:import|export)\b", stripped)
            or re.search(r"\brequire\s*\(", stripped)
            or re.search(r"\bimport\s*\(", stripped)
            or re.search(r"\bfrom\s*['\"]", stripped)
        )
    if suffix == ".rb":
        return bool(re.match(r"^(?:require|require_relative)\b", stripped))
    if suffix == ".rs":
        return bool(re.match(r"^(?:use|extern\s+crate)\b", stripped))
    if suffix == ".go":
        return bool(re.match(r"^import\b", stripped) or re.match(r"^[\"'][^\"']+[\"']\s*$", stripped))
    if suffix == ".java":
        return bool(re.match(r"^import\s+", stripped))
    if suffix in {".c", ".cc", ".cpp", ".h", ".hpp"}:
        return bool(re.match(r"^#\s*include\b", stripped))
    if suffix == ".sh":
        return bool(re.match(r"^(?:source|\.)\s+", stripped))
    return False


def _request_file(owner: str, repository: str, git_sha: str, path: str, auth_value: str) -> str:
    endpoint = f"/repos/{quote(owner)}/{quote(repository)}/contents/{quote(path, safe='/')}"
    url = API_ROOT + endpoint + "?" + urlencode({"ref": git_sha})
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if auth_value:
        headers["Authorization"] = "Bearer " + auth_value
    try:
        with urlopen(Request(url, headers=headers, method="GET"), timeout=30) as response:
            payload = response.read(MAX_FILE_BYTES * 2 + 1)
    except HTTPError as exc:
        body = exc.read(1024).decode("utf-8", errors="replace")
        raise TriageError(f"GitHub API {exc.code} for {repository}/{path}: {body[:240]}") from exc
    except URLError as exc:
        raise TriageError(f"GitHub API unavailable for {repository}/{path}: {exc.reason}") from exc
    if len(payload) > MAX_FILE_BYTES * 2:
        raise TriageError(f"GitHub API response too large for {repository}/{path}")
    try:
        item = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TriageError(f"invalid GitHub API JSON for {repository}/{path}") from exc
    if not isinstance(item, dict) or item.get("encoding") != "base64" or not isinstance(item.get("content"), str):
        raise TriageError(f"GitHub contents response is not a base64 file for {repository}/{path}")
    try:
        raw = base64.b64decode(item["content"], validate=False)
    except ValueError as exc:
        raise TriageError(f"invalid base64 file content for {repository}/{path}") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise TriageError(f"file too large for triage: {repository}/{path}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TriageError(f"file is not UTF-8: {repository}/{path}") from exc


def triage_inventory(
    inventory: Any,
    *,
    fetch_file: Callable[[str, str, str, str], str],
) -> dict[str, Any]:
    if not isinstance(inventory, dict) or not isinstance(inventory.get("sources"), list):
        raise TriageError("inventory must contain a sources array")
    source_results: list[dict[str, Any]] = []
    total = 0
    verified = 0
    mentions = 0
    unresolved = 0

    for source_item in inventory["sources"]:
        if not isinstance(source_item, dict):
            raise TriageError("source inventory entry must be an object")
        source = source_item.get("repository")
        references = source_item.get("references")
        if not isinstance(source, str) or not source or not isinstance(references, list):
            raise TriageError("source inventory entry is incomplete")
        aliases = _aliases(source)
        candidates: list[dict[str, str]] = []
        for reference in references:
            if not isinstance(reference, dict) or reference.get("kind") != "import":
                continue
            if total >= MAX_REFERENCES:
                raise TriageError("import candidate count exceeds safety bound")
            total += 1
            consumer = reference.get("consumer")
            evidence = reference.get("evidence")
            if not isinstance(consumer, str) or not isinstance(evidence, str):
                raise TriageError("import candidate is incomplete")
            classification = "unresolved"
            try:
                parsed = parse_evidence_ref(evidence)
                if parsed["repo"] != consumer:
                    raise TriageError("consumer name does not match evidence repository")
                text = fetch_file(parsed["owner"], parsed["repo"], parsed["sha"], parsed["path"])
                lines = text.splitlines()
                if parsed["line"] > len(lines):
                    raise TriageError("evidence line is outside fetched file")
                classification = (
                    "verified_import"
                    if is_strong_import_reference(parsed["path"], lines[parsed["line"] - 1], aliases)
                    else "code_mention"
                )
            except TriageError:
                classification = "unresolved"
            if classification == "verified_import":
                verified += 1
            elif classification == "code_mention":
                mentions += 1
            else:
                unresolved += 1
            candidates.append({
                "consumer": consumer,
                "evidence": evidence,
                "classification": classification,
            })
        source_results.append({"repository": source, "candidates": candidates})

    payload = {
        "schema_version": 1,
        "status": "passed" if unresolved == 0 else "failed",
        "import_candidates": total,
        "verified_imports": verified,
        "code_mentions": mentions,
        "unresolved": unresolved,
        "sources": source_results,
        "mutation_performed": False,
        "migration_authorized": False,
        "rule": "coarse code-file matches require syntax triage before they may be treated as runtime import evidence",
    }
    payload["evidence_sha256"] = sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refine coarse AgentOps code-file references into syntax-backed import evidence.")
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--auth-env", default="GH_PUBLIC_EVIDENCE_AUTH")
    args = parser.parse_args(argv)
    try:
        with open(args.inventory, encoding="utf-8") as handle:
            inventory = json.load(handle)
        auth_value = os.environ.get(args.auth_env, "")
        result = triage_inventory(
            inventory,
            fetch_file=lambda owner, repo, git_sha, path: _request_file(owner, repo, git_sha, path, auth_value),
        )
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        print(_canonical({
            "status": result["status"],
            "import_candidates": result["import_candidates"],
            "verified_imports": result["verified_imports"],
            "code_mentions": result["code_mentions"],
            "unresolved": result["unresolved"],
            "evidence_sha256": result["evidence_sha256"],
        }))
        return 0 if result["status"] == "passed" else 2
    except (OSError, json.JSONDecodeError, TriageError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
