"""Generate dependency-free A03 release evidence from one wheel and one sdist."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import tomllib

MAX_BYTES = 256 * 1024 * 1024

class EvidenceError(ValueError):
    pass

def digest(path: Path) -> tuple[int, str]:
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise EvidenceError(f"artifact exceeds {MAX_BYTES} bytes: {path.name}")
    h = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            h.update(chunk)
    return size, h.hexdigest()

def build(root: Path, dist: Path, out: Path) -> dict:
    root, dist = root.resolve(), dist.resolve(); out.mkdir(parents=True, exist_ok=True)
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        policy = json.loads((root / "release-policy.a03.v1.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise EvidenceError("cannot read project/policy metadata") from exc
    name, version = project.get("name"), project.get("version")
    if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
        raise EvidenceError("project name/version must be explicit strings")
    if policy.get("state") != "PREPARED" or policy.get("publish_enabled") is not False:
        raise EvidenceError("A03 policy must remain PREPARED with publication disabled")
    wheels, sdists = sorted(dist.glob("*.whl")), sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise EvidenceError(f"expected exactly one wheel and one sdist; found wheels={len(wheels)} sdists={len(sdists)}")
    rows = []
    for path in (wheels[0], sdists[0]):
        size, value = digest(path); rows.append({"name": path.name, "size": size, "sha256": value})
    rows.sort(key=lambda row: row["name"])
    (out / "SHA256SUMS.txt").write_text("".join(f"{r['sha256']}  {r['name']}\n" for r in rows), encoding="utf-8")
    bom = {"bomFormat":"CycloneDX","specVersion":"1.6","version":1,"metadata":{"component":{"type":"application","name":"Model Router","version":version,"purl":f"pkg:pypi/{name}@{version}"}},"components":[],"properties":[{"name":f"model-router:artifact:{r['name']}:sha256","value":r["sha256"]} for r in rows]}
    (out / "model-router.cdx.json").write_text(json.dumps(bom, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    receipt = {"schema_version":"1.0","product":"Model Router","repository":"vigilanty0x/model-router","distribution":name,"version":version,"state":"PREPARED","source_sha":os.environ.get("GITHUB_SHA") or "not-recorded","source_ref":os.environ.get("GITHUB_REF") or "not-recorded","generated_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"python":platform.python_version(),"platform":platform.platform(),"artifacts":rows,"tests_verified":os.environ.get("MODEL_ROUTER_TESTS_VERIFIED")=="1","counterproof_verified":os.environ.get("MODEL_ROUTER_COUNTERPROOF_VERIFIED")=="1","signed":False,"attested":False,"tagged":False,"published":False,"released":False}
    (out / "RELEASE_EVIDENCE.json").write_text(json.dumps(receipt, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    return receipt

def main(argv=None)->int:
    p=argparse.ArgumentParser();p.add_argument("--root",type=Path,default=Path("."));p.add_argument("--dist",type=Path,default=Path("dist"));p.add_argument("--output",type=Path,default=Path("release-evidence-a03"));a=p.parse_args(argv)
    try:r=build(a.root,a.dist,a.output)
    except (OSError,EvidenceError) as exc: raise SystemExit(f"A03 release evidence: {exc}") from exc
    print(f"A03 release evidence prepared: {r['distribution']} {r['version']} state={r['state']}");return 0
if __name__=="__main__": raise SystemExit(main())
