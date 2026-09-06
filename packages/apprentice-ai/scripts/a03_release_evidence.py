"""Generate dependency-free A03 release evidence for Apprentice AI."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import tomllib


MAX_ARTIFACT_BYTES = 256 * 1024 * 1024


class EvidenceError(ValueError):
    pass


def _digest(path: Path) -> tuple[int, str]:
    size = path.stat().st_size
    if size > MAX_ARTIFACT_BYTES:
        raise EvidenceError(f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes: {path.name}")
    value = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return size, value.hexdigest()


def _metadata(root: Path) -> tuple[dict, dict]:
    try:
        project = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        policy = json.loads(
            (root / "release-policy.a03.v1.json").read_text(encoding="utf-8")
        )
    except (
        OSError,
        UnicodeError,
        tomllib.TOMLDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ) as exc:
        raise EvidenceError("cannot read project/policy metadata") from exc
    return project, policy


def _artifacts(dist: Path) -> tuple[Path, Path]:
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise EvidenceError(
            "expected exactly one wheel and one sdist; "
            f"found wheels={len(wheels)} sdists={len(sdists)}"
        )
    return wheels[0], sdists[0]


def build(root: Path, dist: Path, output: Path) -> dict:
    root = root.resolve()
    dist = dist.resolve()
    output.mkdir(parents=True, exist_ok=True)
    project, policy = _metadata(root)
    name = project.get("name")
    version = project.get("version")
    if name != "apprentice-ai" or not isinstance(version, str) or not version:
        raise EvidenceError("unexpected Apprentice AI package identity/version")
    if policy.get("state") != "PREPARED" or policy.get("publish_enabled") is not False:
        raise EvidenceError("A03 policy must remain PREPARED with publication disabled")

    rows = []
    for path in _artifacts(dist):
        size, digest = _digest(path)
        rows.append({"name": path.name, "size": size, "sha256": digest})
    rows.sort(key=lambda row: row["name"])

    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{row['sha256']}  {row['name']}\n" for row in rows),
        encoding="utf-8",
    )
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "Apprentice AI",
                "version": version,
                "purl": f"pkg:pypi/{name}@{version}",
                "properties": [
                    {"name": "apprentice-ai:runtime-dependencies", "value": "0"},
                    {"name": "apprentice-ai:execution-boundary", "value": "preview-only"},
                ],
            }
        },
        "components": [],
        "properties": [
            {
                "name": f"apprentice-ai:artifact:{row['name']}:sha256",
                "value": row["sha256"],
            }
            for row in rows
        ],
    }
    (output / "apprentice-ai.cdx.json").write_text(
        json.dumps(bom, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    receipt = {
        "schema_version": "1.0",
        "product": "Apprentice AI",
        "repository": "vigilanty0x/apprentice-ai",
        "distribution": name,
        "version": version,
        "state": "PREPARED",
        "execution_boundary": "preview-only",
        "source_sha": os.environ.get("GITHUB_SHA") or "not-recorded",
        "source_ref": os.environ.get("GITHUB_REF") or "not-recorded",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "artifacts": rows,
        "tests_verified": os.environ.get("APPRENTICE_TESTS_VERIFIED") == "1",
        "counterproof_verified": os.environ.get("APPRENTICE_COUNTERPROOF_VERIFIED") == "1",
        "signed": False,
        "attested": False,
        "tagged": False,
        "published": False,
        "released": False,
    }
    (output / "RELEASE_EVIDENCE.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipt
