"""Deterministic LearnPack export, hostile archive validation and quarantined import."""

from __future__ import annotations

import copy
import hashlib
import io
import os
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .contracts import SPEC_VERSION, TrustState, utc_now
from .errors import IntegrityError, ValidationError
from .privacy import PrivacyGuard
from .skills import lint_skill, verify_compiled_skill
from .store import EventStore
from .strictjson import canonical_bytes, loads_bytes

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_FILE_COUNT = 128
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
REQUIRED_FILES = frozenset(
    {
        "learnpack.json",
        "SKILL.md",
        "README.md",
        "LICENSE",
        "workflow/skill-ir.json",
        "security/permissions.json",
        "security/sbom.spdx.json",
        "tests/cases.json",
        "evidence/synthetic/scenario.json",
        "provenance/attestation.json",
        "MANIFEST.sha256",
    }
)


def _safe_member_name(name: str) -> str:
    if not name or len(name) > 240 or "\x00" in name or "\\" in name:
        raise ValidationError("unsafe LearnPack member name")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValidationError(f"unsafe LearnPack path: {name!r}")
    if path.parts[0].endswith(":"):
        raise ValidationError(f"unsafe LearnPack drive path: {name!r}")
    return path.as_posix()


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _manifest_lines(files: dict[str, bytes]) -> bytes:
    return "".join(f"{_digest(files[name])}  {name}\n" for name in sorted(files)).encode("utf-8")


def _markdown_for_skill(skill: dict[str, Any]) -> bytes:
    lines = [
        f"# {skill['skill_id']}",
        "",
        str(skill["intent"]),
        "",
        "## Safety contract",
        "",
        "This release is preview-only. It does not execute actions, use the network, or create external effects.",
        "",
        "## Steps",
        "",
    ]
    for step in skill["steps"]:
        condition = ""
        if step.get("when"):
            item = step["when"]
            condition = f" when `{item['field']} {item['operator']} {item['value']}`"
        lines.append(f"1. `{step['action']}`{condition}")
    lines.extend(
        [
            "",
            "## Verification",
            "",
            "All holdout cases bundled in `tests/cases.json` must pass before compilation.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _canonical_reference_skill(skill: dict[str, Any]) -> dict[str, Any]:
    """Remove run-local identifiers from the fixed D1-D5 distributable artifact."""

    public = copy.deepcopy(skill)
    canonical_routine = "synthetic-reference-routine"
    verification = public["verification"]
    verification["routine_id"] = canonical_routine
    verification["induction_ids"] = ["synthetic:D1", "synthetic:D2", "synthetic:D3"]
    for case in verification["holdout_cases"]:
        case["episode_id"] = f"synthetic:{case['demo_id']}"
    provenance = public.setdefault("provenance", {})
    provenance["routine_id"] = canonical_routine
    provenance["evidence_refs"] = [f"synthetic:D{number}" for number in range(1, 6)]
    provenance["compiled_at"] = "2026-08-15T00:00:00Z"
    public["artifact_determinism"] = {
        "scope": "reference D1-D5 logical fixture",
        "run_local_identifiers_removed": True,
    }
    lint_skill(public)
    return public


def build_pack_files(skill: dict[str, Any]) -> dict[str, bytes]:
    lint_skill(skill)
    # Scan every source field before canonicalization so ignored extensions cannot smuggle secrets.
    scan_pack_files({"workflow/source-skill-ir.json": canonical_bytes(skill)})
    skill = _canonical_reference_skill(skill)
    compiled_at = str(skill.get("provenance", {}).get("compiled_at", "1970-01-01T00:00:00Z"))
    manifest = {
        "spec_version": SPEC_VERSION,
        "kind": "LearnPack",
        "metadata": {
            "id": skill["skill_id"],
            "version": skill["version"],
            "title": "Normalize synthetic laboratory exports",
            "summary": skill["intent"],
            "language": ["en", "fr"],
            "license": "Apache-2.0",
            "created_at": compiled_at,
            "authors": [{"identity": "vigilanty0x", "role": "maintainer"}],
        },
        "compatibility": {
            "operating_systems": ["windows", "macos", "linux"],
            "applications": [{"id": "org.apprentice.synthetic-office", "versions": ">=0.1 <1"}],
            "runtimes": {"apprentice": ">=0.1 <1"},
        },
        "intent": {"domain": "synthetic-data", "task": "data-normalization", "goal": skill["intent"]},
        "workflow": {"skill_ir": "workflow/skill-ir.json", "deterministic_preferred": True},
        "permissions": {"declared": "security/permissions.json", "network": "none"},
        "risk": skill["risk"],
        "verification": {
            "tests": "tests/cases.json",
            "required_postconditions": [
                "row_count_preserved",
                "known_units_normalized",
                "holdout_branch_matches",
            ],
        },
        "provenance": {
            "attestation": "provenance/attestation.json",
            "training_data_included": False,
            "raw_observation_included": False,
            "synthetic_only": True,
        },
        "privacy": {
            "personal_data": "none_declared",
            "scanner_version": "privacy-guard/0.1.0",
            "review_required": True,
        },
        "supply_chain": {
            "sbom": "security/sbom.spdx.json",
            "digest_manifest": "MANIFEST.sha256",
            "signature": None,
        },
    }
    tests = {
        "spec_version": SPEC_VERSION,
        "synthetic": True,
        "holdout_cases": skill["verification"]["holdout_cases"],
        "expected": {"all_holdout_passed": True, "execution_supported": False},
    }
    evidence = {
        "synthetic": True,
        "scenario": "D1-D5 laboratory export normalization",
        "induction": ["D1", "D2", "D3"],
        "holdout": ["D4", "D5"],
        "raw_observation_included": False,
    }
    attestation = {
        "predicate_type": "https://apprentice.local/attestation/learnpack/v0.1",
        "subject": {"skill_id": skill["skill_id"], "version": skill["version"]},
        "builder": "apprentice-ai/0.1.0",
        "compiled_at": compiled_at,
        "claims": {
            "synthetic_only": True,
            "privacy_scan_required": True,
            "execution_supported": False,
        },
    }
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{skill['skill_id']}-{skill['version']}",
        "documentNamespace": f"https://apprentice.local/spdx/{skill['skill_id']}/{skill['version']}",
        "packages": [
            {
                "name": skill["skill_id"],
                "SPDXID": "SPDXRef-Package",
                "versionInfo": skill["version"],
                "downloadLocation": "NOASSERTION",
                "licenseConcluded": "Apache-2.0",
                "filesAnalyzed": False,
            }
        ],
    }
    files = {
        "learnpack.json": canonical_bytes(manifest),
        "SKILL.md": _markdown_for_skill(skill),
        "README.md": (
            "# Synthetic laboratory normalization LearnPack\n\n"
            "A deterministic, preview-only reference pack built entirely from synthetic demonstrations.\n"
        ).encode(),
        "LICENSE": b"Apache License 2.0. See https://www.apache.org/licenses/LICENSE-2.0\n",
        "workflow/skill-ir.json": canonical_bytes(skill),
        "security/permissions.json": canonical_bytes(skill["permissions"]),
        "security/sbom.spdx.json": canonical_bytes(sbom),
        "tests/cases.json": canonical_bytes(tests),
        "evidence/synthetic/scenario.json": canonical_bytes(evidence),
        "provenance/attestation.json": canonical_bytes(attestation),
    }
    scan_pack_files(files)
    files["MANIFEST.sha256"] = _manifest_lines(files)
    return files


def scan_pack_files(files: dict[str, bytes]) -> dict[str, Any]:
    guard = PrivacyGuard()
    findings: list[dict[str, str]] = []
    for name, payload in files.items():
        _safe_member_name(name)
        if len(payload) > MAX_MEMBER_BYTES:
            raise ValidationError(f"LearnPack member exceeds limit: {name}")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            raise ValidationError(f"binary member is forbidden in release 0.1.0: {name}")
        _, categories = guard.scan_text(text)
        for category in categories:
            findings.append({"path": name, "category": category})
    if findings:
        summary = ", ".join(f"{item['path']}:{item['category']}" for item in findings[:8])
        raise ValidationError(f"LearnPack privacy scan failed: {summary}")
    return {"files_scanned": len(files), "findings": 0, "valid": True}


def export_learnpack(
    store: EventStore,
    profile_id: str,
    skill_id: str,
    version: str,
    destination: str | Path,
) -> dict[str, Any]:
    skill = store.get_skill(profile_id, skill_id, version)
    verify_compiled_skill(store, profile_id, skill)
    output = Path(destination)
    if output.exists() and (output.is_symlink() or not output.is_file()):
        raise ValidationError("LearnPack destination must be a regular non-symlink file")
    if output.parent.exists() and output.parent.is_symlink():
        raise ValidationError("LearnPack destination parent must not be a symlink")
    output.parent.mkdir(parents=True, exist_ok=True)
    files = build_pack_files(skill)
    temp_handle = tempfile.NamedTemporaryFile(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False
    )
    temp_path = Path(temp_handle.name)
    temp_handle.close()
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
            for name in sorted(files):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, files[name])
        archive_snapshot = _snapshot_archive(temp_path)
        digest = _digest(archive_snapshot)
        archive_bytes = len(archive_snapshot)
        os.replace(temp_path, output)
    except BaseException:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise
    return {
        "path": str(output),
        "digest": f"sha256:{digest}",
        "files": len(files),
        "bytes": archive_bytes,
        "deterministic": True,
        "privacy_scan": "passed",
    }


def _snapshot_archive(path: str | Path) -> bytes:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValidationError("LearnPack path must be a regular non-symlink file")
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValidationError("LearnPack path must resolve to a regular file")
        if before.st_size > MAX_ARCHIVE_BYTES:
            raise ValidationError("LearnPack archive exceeds size limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, MAX_ARCHIVE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ValidationError("LearnPack archive exceeds size limit")
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or total != before.st_size
        ):
            raise IntegrityError("LearnPack changed while snapshotting")
        return b"".join(chunks)
    except OSError as exc:
        raise ValidationError(f"cannot snapshot LearnPack: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_archive(snapshot: bytes) -> tuple[dict[str, bytes], str]:
    if len(snapshot) > MAX_ARCHIVE_BYTES:
        raise ValidationError("LearnPack archive exceeds size limit")
    archive_digest = _digest(snapshot)
    files: dict[str, bytes] = {}
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(snapshot), "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILE_COUNT:
                raise ValidationError("LearnPack contains too many members")
            for info in infos:
                name = _safe_member_name(info.filename)
                if name in files:
                    raise ValidationError(f"duplicate LearnPack member: {name}")
                mode = (info.external_attr >> 16) & 0o177777
                file_type = stat.S_IFMT(mode)
                if stat.S_ISLNK(mode) or file_type not in {0, stat.S_IFREG}:
                    raise ValidationError(f"non-regular LearnPack member: {name}")
                if info.flag_bits & 0x1:
                    raise ValidationError("encrypted LearnPack members are forbidden")
                if info.file_size > MAX_MEMBER_BYTES:
                    raise ValidationError(f"LearnPack member exceeds size limit: {name}")
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    raise ValidationError("LearnPack expanded size limit exceeded")
                if info.compress_size > 0 and info.file_size / info.compress_size > 100:
                    raise ValidationError(f"suspicious compression ratio: {name}")
                with archive.open(info, "r") as handle:
                    payload = handle.read(MAX_MEMBER_BYTES + 1)
                if len(payload) != info.file_size or len(payload) > MAX_MEMBER_BYTES:
                    raise IntegrityError(f"LearnPack member size mismatch: {name}")
                files[name] = payload
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise ValidationError(f"invalid LearnPack archive: {exc}") from exc
    return files, archive_digest


def _parse_digest_manifest(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValidationError("digest manifest must be ASCII") from exc
    entries: dict[str, str] = {}
    for line in text.splitlines():
        if len(line) < 67 or line[64:66] != "  ":
            raise ValidationError("invalid digest manifest line")
        digest, name = line[:64], line[66:]
        if not all(character in "0123456789abcdef" for character in digest):
            raise ValidationError("invalid SHA-256 digest")
        name = _safe_member_name(name)
        if name in entries or name == "MANIFEST.sha256":
            raise ValidationError("invalid duplicate/self digest manifest entry")
        entries[name] = digest
    return entries


def _validate_learnpack_files(files: dict[str, bytes], archive_digest: str) -> dict[str, Any]:
    missing = sorted(REQUIRED_FILES - files.keys())
    unknown = sorted(files.keys() - REQUIRED_FILES)
    if missing:
        raise ValidationError(f"LearnPack missing required members: {', '.join(missing)}")
    if unknown:
        raise ValidationError(f"LearnPack contains undeclared members: {', '.join(unknown)}")
    expected = _parse_digest_manifest(files["MANIFEST.sha256"])
    actual_names = set(files) - {"MANIFEST.sha256"}
    if set(expected) != actual_names:
        raise IntegrityError("digest manifest coverage mismatch")
    for name, digest in expected.items():
        if _digest(files[name]) != digest:
            raise IntegrityError(f"digest mismatch for {name}")
    manifest = loads_bytes(files["learnpack.json"], max_bytes=MAX_MEMBER_BYTES)
    skill = loads_bytes(files["workflow/skill-ir.json"], max_bytes=MAX_MEMBER_BYTES)
    permissions = loads_bytes(files["security/permissions.json"], max_bytes=MAX_MEMBER_BYTES)
    tests = loads_bytes(files["tests/cases.json"], max_bytes=MAX_MEMBER_BYTES)
    if not isinstance(manifest, dict) or manifest.get("kind") != "LearnPack":
        raise ValidationError("invalid LearnPack manifest kind")
    if manifest.get("spec_version") != SPEC_VERSION:
        raise ValidationError("unsupported LearnPack specification version")
    if manifest.get("metadata", {}).get("id") != skill.get("skill_id"):
        raise IntegrityError("manifest and Skill IR identifiers differ")
    if manifest.get("metadata", {}).get("version") != skill.get("version"):
        raise IntegrityError("manifest and Skill IR versions differ")
    if permissions != skill.get("permissions"):
        raise IntegrityError("permission manifest differs from Skill IR")
    if tests.get("synthetic") is not True or not tests.get("expected", {}).get("all_holdout_passed"):
        raise ValidationError("LearnPack tests are not approved synthetic holdouts")
    if tests.get("holdout_cases") != skill.get("verification", {}).get("holdout_cases"):
        raise IntegrityError("LearnPack tests and Skill IR holdout evidence differ")
    lint_skill(skill)
    scan_pack_files({name: payload for name, payload in files.items() if name != "MANIFEST.sha256"})
    return {
        "valid": True,
        "digest": f"sha256:{archive_digest}",
        "files": len(files),
        "skill_id": skill["skill_id"],
        "version": skill["version"],
        "privacy_scan": "passed",
        "signature": "not_present_local_reference_pack",
        "execution_supported": False,
    }


def validate_learnpack(path: str | Path) -> dict[str, Any]:
    snapshot = _snapshot_archive(path)
    files, archive_digest = _read_archive(snapshot)
    return _validate_learnpack_files(files, archive_digest)


def inspect_learnpack(path: str | Path) -> dict[str, Any]:
    snapshot = _snapshot_archive(path)
    files, digest = _read_archive(snapshot)
    report = _validate_learnpack_files(files, digest)
    return {
        "report": report,
        "manifest": loads_bytes(files["learnpack.json"], max_bytes=MAX_MEMBER_BYTES),
        "skill_ir": loads_bytes(files["workflow/skill-ir.json"], max_bytes=MAX_MEMBER_BYTES),
        "skill_md": files["SKILL.md"].decode("utf-8"),
        "tests": loads_bytes(files["tests/cases.json"], max_bytes=MAX_MEMBER_BYTES),
        "permissions": loads_bytes(files["security/permissions.json"], max_bytes=MAX_MEMBER_BYTES),
    }


def import_learnpack(
    store: EventStore,
    profile_id: str,
    path: str | Path,
) -> dict[str, Any]:
    inspection = inspect_learnpack(path)
    report = inspection["report"]
    receipt = {
        "spec_version": SPEC_VERSION,
        "source_digest": report["digest"],
        "skill_id": report["skill_id"],
        "version": report["version"],
        "trust_state": TrustState.DISABLED_UNTRUSTED.value,
        "execution_allowed": False,
        "quarantine": "validated_without_extraction_or_execution",
        "imported_at": utc_now(),
        "bundle": {
            "manifest": inspection["manifest"],
            "skill_ir": inspection["skill_ir"],
            "skill_md": inspection["skill_md"],
            "tests": inspection["tests"],
            "permissions": inspection["permissions"],
        },
    }
    import_id = store.put_import(profile_id, report["digest"], receipt)
    receipt["import_id"] = import_id
    return receipt
