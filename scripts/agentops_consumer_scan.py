from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import PurePosixPath
import re
import sys
import tarfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from automation_control_plane.agentops import inventory_consumers

API_ROOT = "https://api.github.com"
USER_AGENT = "agentops-public-consumer-evidence/1"
MAX_PAGE = 100
MAX_MEMBER_BYTES = 1_000_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 50_000_000
MAX_REFERENCES_PER_SOURCE = 500
TEXT_SUFFIXES = {
    ".c", ".cc", ".cfg", ".conf", ".cpp", ".css", ".go", ".h", ".hpp", ".html",
    ".ini", ".java", ".js", ".json", ".jsx", ".md", ".mjs", ".py", ".rb", ".rs",
    ".rst", ".sh", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}
CODE_SUFFIXES = {".c", ".cc", ".cpp", ".go", ".h", ".hpp", ".java", ".js", ".jsx", ".mjs", ".py", ".rb", ".rs", ".sh", ".ts", ".tsx"}
DOC_SUFFIXES = {".md", ".rst", ".txt", ".html"}
PACKAGE_NAMES = {
    "cargo.toml",
    "go.mod",
    "package-lock.json",
    "package.json",
    "poetry.lock",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
}
WORKFLOW_PREFIX = ".github/workflows/"
PILOT_KIND = "pilot"
STATIC_KINDS = ("documentation", "fork", "import", "package", "workflow")


class ScanError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _write_json(path: str, value: Any) -> None:
    rendered = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)


def _digest_file(path: str) -> str:
    digest = sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request_json(path: str, auth_value: str, *, query: dict[str, str | int] | None = None) -> Any:
    url = API_ROOT + path
    if query:
        url += "?" + urlencode(query)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if auth_value:
        headers["Authorization"] = "Bearer " + auth_value
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read(MAX_MEMBER_BYTES + 1)
    except HTTPError as exc:
        body = exc.read(2048).decode("utf-8", errors="replace")
        raise ScanError(f"GitHub API {exc.code} for {path}: {body[:400]}") from exc
    except URLError as exc:
        raise ScanError(f"GitHub API unavailable for {path}: {exc.reason}") from exc
    if len(payload) > MAX_MEMBER_BYTES:
        raise ScanError(f"GitHub API response exceeds {MAX_MEMBER_BYTES} bytes for {path}")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScanError(f"invalid GitHub API JSON for {path}") from exc


def _open_public_tarball(owner: str, repository: str, git_sha: str, auth_value: str):
    path = f"/repos/{quote(owner)}/{quote(repository)}/tarball/{quote(git_sha)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if auth_value:
        headers["Authorization"] = "Bearer " + auth_value
    request = Request(API_ROOT + path, headers=headers, method="GET")
    try:
        return urlopen(request, timeout=60)
    except HTTPError as exc:
        body = exc.read(2048).decode("utf-8", errors="replace")
        raise ScanError(f"GitHub tarball {exc.code} for {repository}: {body[:400]}") from exc
    except URLError as exc:
        raise ScanError(f"GitHub tarball unavailable for {repository}: {exc.reason}") from exc


def _public_repositories(owner: str, auth_value: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = _request_json(
            f"/users/{quote(owner)}/repos",
            auth_value,
            query={"type": "owner", "sort": "full_name", "direction": "asc", "per_page": MAX_PAGE, "page": page},
        )
        if type(batch) is not list:
            raise ScanError("public repository endpoint did not return an array")
        if not batch:
            break
        for item in batch:
            if type(item) is not dict:
                raise ScanError("public repository endpoint returned a non-object")
            if item.get("private") is not False:
                raise ScanError("public repository enumeration returned a private repository")
            name = item.get("name")
            default_branch = item.get("default_branch")
            if type(name) is not str or not name or type(default_branch) is not str or not default_branch:
                raise ScanError("public repository metadata is incomplete")
            output.append({"name": name, "default_branch": default_branch})
        if len(batch) < MAX_PAGE:
            break
        page += 1
        if page > 100:
            raise ScanError("public repository pagination exceeded safety bound")
    names = [item["name"] for item in output]
    if len(names) != len(set(names)):
        raise ScanError("duplicate public repository name returned by GitHub")
    return output


def _branch_head(owner: str, repository: str, branch: str, auth_value: str) -> str:
    payload = _request_json(
        f"/repos/{quote(owner)}/{quote(repository)}/commits/{quote(branch, safe='')}",
        auth_value,
    )
    if type(payload) is not dict or type(payload.get("sha")) is not str:
        raise ScanError(f"cannot resolve default branch head for {repository}")
    git_sha = payload["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", git_sha):
        raise ScanError(f"invalid Git SHA for {repository}")
    return git_sha


def _aliases(repository: str) -> tuple[str, ...]:
    values = {repository.casefold(), repository.replace("-", "_").casefold()}
    compact = repository.replace("-", "").casefold()
    if len(compact) >= 5:
        values.add(compact)
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


def classify_path(path: str) -> str | None:
    normalized = path.casefold().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    name = PurePosixPath(normalized).name
    suffix = PurePosixPath(normalized).suffix
    if normalized.startswith(WORKFLOW_PREFIX):
        return "workflow"
    if name in PACKAGE_NAMES or name.startswith("requirements") and name.endswith(".txt"):
        return "package"
    if normalized.startswith("docs/") or suffix in DOC_SUFFIXES:
        return "documentation"
    if suffix in CODE_SUFFIXES:
        return "import"
    if suffix in TEXT_SUFFIXES:
        return "documentation"
    return None


def _strip_tar_prefix(name: str) -> str:
    parts = PurePosixPath(name).parts
    if len(parts) <= 1:
        return ""
    return PurePosixPath(*parts[1:]).as_posix()


def _scan_archive(
    owner: str,
    repository: str,
    git_sha: str,
    source_aliases: dict[str, tuple[str, ...]],
    auth_value: str,
) -> dict[str, list[dict[str, str]]]:
    found: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen: set[tuple[str, str, str]] = set()
    consumed = 0
    response = _open_public_tarball(owner, repository, git_sha, auth_value)
    try:
        with tarfile.open(fileobj=response, mode="r|gz") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    continue
                consumed += member.size
                if consumed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise ScanError(f"archive scan exceeded {MAX_ARCHIVE_UNCOMPRESSED_BYTES} bytes for {repository}")
                relative_path = _strip_tar_prefix(member.name)
                if not relative_path:
                    continue
                kind = classify_path(relative_path)
                if kind is None:
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                raw = extracted.read(MAX_MEMBER_BYTES + 1)
                if len(raw) > MAX_MEMBER_BYTES:
                    continue
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                for line_number, line in enumerate(text.splitlines(), start=1):
                    folded = line.casefold()
                    for source, aliases in source_aliases.items():
                        if source == repository:
                            continue
                        if not any(alias in folded for alias in aliases):
                            continue
                        evidence_ref = f"github://{owner}/{repository}@{git_sha}/{relative_path}#L{line_number}"
                        signature = (source, kind, evidence_ref)
                        if signature in seen:
                            continue
                        seen.add(signature)
                        if len(found[source]) >= MAX_REFERENCES_PER_SOURCE:
                            raise ScanError(f"reference limit exceeded for source {source}")
                        found[source].append({"consumer": repository, "kind": kind, "evidence": evidence_ref})
    except tarfile.TarError as exc:
        raise ScanError(f"invalid tarball for {repository}: {exc}") from exc
    finally:
        response.close()
    return found


def _fork_references(owner: str, source: str, auth_value: str) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    page = 1
    while True:
        batch = _request_json(
            f"/repos/{quote(owner)}/{quote(source)}/forks",
            auth_value,
            query={"sort": "newest", "per_page": MAX_PAGE, "page": page},
        )
        if type(batch) is not list:
            raise ScanError(f"fork endpoint did not return an array for {source}")
        if not batch:
            break
        for item in batch:
            if type(item) is not dict:
                raise ScanError(f"invalid fork metadata for {source}")
            if item.get("private") is not False:
                raise ScanError(f"fork endpoint returned a private fork for {source}")
            full_name = item.get("full_name")
            html_url = item.get("html_url")
            if type(full_name) is not str or type(html_url) is not str:
                raise ScanError(f"incomplete fork metadata for {source}")
            output.append({"consumer": full_name, "kind": "fork", "evidence": html_url})
            if len(output) > MAX_REFERENCES_PER_SOURCE:
                raise ScanError(f"fork reference limit exceeded for {source}")
        if len(batch) < MAX_PAGE:
            break
        page += 1
        if page > 10:
            raise ScanError(f"fork pagination exceeded safety bound for {source}")
    return output


def _load_source_inventory(path: str) -> tuple[list[str], dict[str, str]]:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScanError(f"cannot load source inventory: {exc}") from exc
    if type(payload) is not dict or type(payload.get("sources")) is not list:
        raise ScanError("source inventory must contain a sources array")
    names: list[str] = []
    shas: dict[str, str] = {}
    for item in payload["sources"]:
        if type(item) is not dict:
            raise ScanError("invalid source inventory entry")
        repository = item.get("repository")
        git_sha = item.get("main_sha")
        if type(repository) is not str or not repository or type(git_sha) is not str:
            raise ScanError("source inventory entry is incomplete")
        if not re.fullmatch(r"[0-9a-f]{40}", git_sha):
            raise ScanError(f"invalid source inventory SHA for {repository}")
        if repository in shas:
            raise ScanError(f"duplicate source inventory repository: {repository}")
        names.append(repository)
        shas[repository] = git_sha
    if len(names) != 13:
        raise ScanError(f"expected exactly 13 AgentOps sources, got {len(names)}")
    return names, shas


def _pilot_manifest_from_env(env_name: str | None, source_names: set[str]) -> tuple[bool, list[tuple[str, dict[str, str]]]]:
    if not env_name:
        return False, []
    raw = os.environ.get(env_name, "")
    if not raw:
        return False, []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ScanError(f"pilot manifest environment value is invalid JSON: {exc}") from exc
    if type(payload) is not dict or set(payload) != {"schema_version", "complete", "pilots"}:
        raise ScanError("pilot manifest must contain schema_version, complete, and pilots only")
    if payload["schema_version"] != 1 or type(payload["complete"]) is not bool or type(payload["pilots"]) is not list:
        raise ScanError("pilot manifest has invalid field types")
    output: list[tuple[str, dict[str, str]]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in payload["pilots"]:
        if type(item) is not dict or set(item) != {"source", "consumer", "evidence"}:
            raise ScanError("pilot entry must contain source, consumer, and evidence only")
        source = item["source"]
        consumer = item["consumer"]
        evidence_ref = item["evidence"]
        if type(source) is not str or source not in source_names:
            raise ScanError("pilot entry references an unknown source")
        if type(consumer) is not str or not consumer or len(consumer) > 128:
            raise ScanError("pilot consumer is invalid")
        if type(evidence_ref) is not str or not evidence_ref or len(evidence_ref) > 512:
            raise ScanError("pilot evidence is invalid")
        signature = (source, consumer, evidence_ref)
        if signature in seen:
            raise ScanError("duplicate pilot entry")
        seen.add(signature)
        output.append((source, {"consumer": consumer, "kind": PILOT_KIND, "evidence": evidence_ref}))
    return payload["complete"], output


def _markdown_report(
    receipt: dict[str, Any],
    *,
    owner: str,
    public_count: int,
    scanned_count: int,
    source_sha_drift: list[dict[str, str]],
    inventory_sha256: str,
    receipt_sha256: str,
) -> str:
    details = receipt.get("details", {}) if type(receipt) is dict else {}
    kind_counts = details.get("kind_counts", {}) if type(details) is dict else {}
    lines = [
        "# AgentOps public consumer evidence",
        "",
        f"- status: `{receipt.get('status', 'blocked')}`",
        f"- public owner: `{owner}`",
        f"- public repositories enumerated: `{public_count}`",
        f"- public repositories scanned: `{scanned_count}`",
        f"- source SHA drift count: `{len(source_sha_drift)}`",
        f"- consumer references: `{details.get('reference_count', 0)}`",
        f"- unique consumers: `{details.get('unique_consumer_count', 0)}`",
        f"- coverage complete: `{details.get('coverage_complete', False)}`",
        f"- inventory SHA-256: `{inventory_sha256}`",
        f"- receipt SHA-256: `{receipt_sha256}`",
        "",
        "## Reference classes",
        "",
    ]
    for kind in ("import", "package", "workflow", "documentation", "fork", "pilot"):
        lines.append(f"- {kind}: `{kind_counts.get(kind, 0)}`")
    lines.extend([
        "",
        "## Gate semantics",
        "",
        "A passing structural receipt does not authorize migration, redirect, release, rollback, or archive.",
        "If pilot coverage is not explicitly declared complete, the evidence remains failed rather than silently assuming zero pilots.",
    ])
    if source_sha_drift:
        lines.extend(["", "## Source SHA drift", ""])
        for item in source_sha_drift:
            lines.append(f"- `{item['repository']}` expected `{item['expected']}` observed `{item['observed']}`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only public AgentOps consumer evidence collector.")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--auth-env", default="GH_PUBLIC_EVIDENCE_AUTH")
    parser.add_argument("--source-inventory", required=True)
    parser.add_argument("--pilot-manifest-env")
    parser.add_argument("--inventory-output", required=True)
    parser.add_argument("--receipt-output", required=True)
    parser.add_argument("--report-output", required=True)
    parser.add_argument("--ttl-days", type=int, default=30)
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", args.owner):
        print("invalid owner", file=sys.stderr)
        return 2
    if args.ttl_days < 1 or args.ttl_days > 90:
        print("ttl-days must be between 1 and 90", file=sys.stderr)
        return 2

    auth_value = os.environ.get(args.auth_env, "")
    observed_at = _utc_now()
    expires_at = observed_at + timedelta(days=args.ttl_days)

    try:
        source_names, expected_source_shas = _load_source_inventory(args.source_inventory)
        source_set = set(source_names)
        source_aliases = {name: _aliases(name) for name in source_names}
        public_repos = _public_repositories(args.owner, auth_value)
        public_by_name = {item["name"]: item for item in public_repos}
        missing_sources = sorted(source_set - set(public_by_name))
        if missing_sources:
            raise ScanError("source repositories missing from public enumeration: " + ", ".join(missing_sources))

        head_shas: dict[str, str] = {}
        for repo in public_repos:
            head_shas[repo["name"]] = _branch_head(args.owner, repo["name"], repo["default_branch"], auth_value)

        source_sha_drift = [
            {"repository": source, "expected": expected_source_shas[source], "observed": head_shas[source]}
            for source in source_names
            if head_shas[source] != expected_source_shas[source]
        ]

        references: dict[str, list[dict[str, str]]] = {name: [] for name in source_names}
        scanned_count = 0
        archive_errors: list[str] = []
        for repo in public_repos:
            try:
                found = _scan_archive(args.owner, repo["name"], head_shas[repo["name"]], source_aliases, auth_value)
            except ScanError as exc:
                archive_errors.append(str(exc))
                continue
            scanned_count += 1
            for source, items in found.items():
                references[source].extend(items)

        fork_errors: list[str] = []
        for source in source_names:
            try:
                references[source].extend(_fork_references(args.owner, source, auth_value))
            except ScanError as exc:
                fork_errors.append(str(exc))

        pilot_complete, pilots = _pilot_manifest_from_env(args.pilot_manifest_env, source_set)
        for source, item in pilots:
            references[source].append(item)

        for source in source_names:
            deduped: list[dict[str, str]] = []
            seen: set[tuple[str, str, str]] = set()
            for item in references[source]:
                signature = (item["consumer"], item["kind"], item["evidence"])
                if signature in seen:
                    continue
                seen.add(signature)
                deduped.append(item)
            deduped.sort(key=lambda item: (item["kind"], item["consumer"], item["evidence"]))
            if len(deduped) > MAX_REFERENCES_PER_SOURCE:
                raise ScanError(f"reference limit exceeded after aggregation for {source}")
            references[source] = deduped

        complete_kinds = list(STATIC_KINDS)
        if fork_errors:
            complete_kinds.remove("fork")
        if archive_errors or source_sha_drift:
            complete_kinds = [kind for kind in complete_kinds if kind == "fork"]
        if pilot_complete:
            complete_kinds.append(PILOT_KIND)
        complete_kinds = sorted(set(complete_kinds))

        inventory_payload = {
            "scan_scope": {
                "observed_at": _iso(observed_at),
                "expires_at": _iso(expires_at),
                "repositories_expected": len(public_repos),
                "repositories_scanned": scanned_count,
                "complete_kinds": complete_kinds,
            },
            "sources": [{"repository": source, "references": references[source]} for source in source_names],
        }

        receipt = inventory_consumers(inventory_payload)
        _write_json(args.inventory_output, inventory_payload)
        _write_json(args.receipt_output, receipt)
        inventory_digest = _digest_file(args.inventory_output)
        receipt_digest = _digest_file(args.receipt_output)
        report = _markdown_report(
            receipt,
            owner=args.owner,
            public_count=len(public_repos),
            scanned_count=scanned_count,
            source_sha_drift=source_sha_drift,
            inventory_sha256=inventory_digest,
            receipt_sha256=receipt_digest,
        )
        with open(args.report_output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(report)

        if archive_errors:
            print("archive scan errors:", file=sys.stderr)
            for error in archive_errors[:20]:
                print("- " + error, file=sys.stderr)
        if fork_errors:
            print("fork scan errors:", file=sys.stderr)
            for error in fork_errors[:20]:
                print("- " + error, file=sys.stderr)
        if source_sha_drift:
            print("source inventory SHA drift detected", file=sys.stderr)
        print(_canonical_json({
            "status": receipt.get("status"),
            "public_repositories": len(public_repos),
            "scanned_repositories": scanned_count,
            "source_sha_drift": len(source_sha_drift),
            "inventory_sha256": inventory_digest,
            "receipt_sha256": receipt_digest,
        }))
        return 0 if receipt.get("status") == "passed" and not archive_errors and not fork_errors and not source_sha_drift else 2
    except ScanError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
