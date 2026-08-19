"""Run the complete portable A03 candidate gate for Model Router."""

from __future__ import annotations

import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

from a03_release_evidence import build as build_release_evidence
from a03_verify_release_evidence import verify as verify_release_evidence


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EVIDENCE = ROOT / "release-evidence-a03"
EXPECTED_TOOLS = {
    "pip": "25.2",
    "setuptools": "80.9.0",
    "wheel": "0.45.1",
}


class CandidateError(RuntimeError):
    pass


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def project_metadata() -> dict:
    value = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = value.get("project")
    if not isinstance(project, dict):
        raise CandidateError("pyproject.toml has no [project] table")
    return project


def verify_toolchain() -> None:
    actual = {name: metadata.version(name) for name in EXPECTED_TOOLS}
    if actual != EXPECTED_TOOLS:
        raise CandidateError(f"build toolchain drift: {actual}")


def build_artifacts() -> tuple[Path, Path]:
    shutil.rmtree(DIST, ignore_errors=True)
    DIST.mkdir(parents=True)
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(DIST),
        ]
    )
    from setuptools.build_meta import build_sdist

    build_sdist(str(DIST))
    wheels = sorted(DIST.glob("*.whl"))
    sdists = sorted(DIST.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise CandidateError(
            f"expected one wheel and one sdist: {wheels!r} {sdists!r}"
        )
    return wheels[0], sdists[0]


def install_and_verify_wheel(wheel: Path) -> None:
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--force-reinstall",
            str(wheel),
        ]
    )
    project = project_metadata()
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise CandidateError("project identity/version must be strings")
    if metadata.version(name) != version:
        raise CandidateError("installed distribution version drift")
    scripts = {
        entry.name
        for entry in metadata.distribution(name).entry_points
        if entry.group == "console_scripts"
    }
    if "model-router" not in scripts:
        raise CandidateError(f"model-router console entry point missing: {scripts}")
    executable = shutil.which("model-router")
    if not executable:
        raise CandidateError("installed model-router executable is missing")
    run([executable, "--help"])


def verify_product_behavior() -> None:
    run([sys.executable, "scripts/check.py"])
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    executable = shutil.which("model-router")
    if not executable:
        raise CandidateError("model-router executable is missing")
    with tempfile.TemporaryDirectory(prefix="model-router-demo-") as tmp:
        database = Path(tmp) / "demo.sqlite3"
        run([executable, "demo", "--db", str(database)])


def verify_publication_disabled() -> None:
    policy = json.loads(
        (ROOT / "release-policy.a03.v1.json").read_text(encoding="utf-8")
    )
    if policy.get("state") != "PREPARED":
        raise CandidateError("A03 release policy must remain PREPARED")
    if policy.get("publish_enabled") is not False:
        raise CandidateError("A03 publication must remain disabled")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "publish-release:",
        "gh release create",
        "contents: write",
        "git tag",
        "twine upload",
        "pypa/gh-action-pypi-publish",
    )
    found = [marker for marker in forbidden if marker in workflow]
    if found:
        raise CandidateError(f"publication authority present in CI: {found}")


def verify_evidence(wheel: Path) -> None:
    shutil.rmtree(EVIDENCE, ignore_errors=True)
    receipt = build_release_evidence(ROOT, DIST, EVIDENCE)
    verify_release_evidence(DIST, EVIDENCE / "SHA256SUMS.txt")
    if receipt.get("state") != "PREPARED":
        raise CandidateError("release evidence must remain PREPARED")
    if not receipt.get("tests_verified") or not receipt.get("counterproof_verified"):
        raise CandidateError("release evidence is missing test/counter-proof claims")
    for field in ("signed", "attested", "tagged", "published", "released"):
        if receipt.get(field) is not False:
            raise CandidateError(f"release evidence falsely claims {field}")

    with tempfile.TemporaryDirectory(prefix="model-router-tamper-") as tmp:
        tampered = Path(tmp) / "dist"
        shutil.copytree(DIST, tampered)
        target = tampered / wheel.name
        target.write_bytes(target.read_bytes() + b"counter-proof")
        try:
            verify_release_evidence(tampered, EVIDENCE / "SHA256SUMS.txt")
        except ValueError:
            pass
        else:
            raise CandidateError("tampered wheel was accepted")


def verify_sdist(sdist: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="model-router-sdist-") as tmp:
        root = Path(tmp)
        venv = root / "venv"
        run([sys.executable, "-m", "venv", str(venv)])
        python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                str(sdist),
            ]
        )
        run([str(python), "-m", "model_router", "--help"])


def main() -> int:
    verify_toolchain()
    wheel, sdist = build_artifacts()
    install_and_verify_wheel(wheel)
    verify_product_behavior()
    verify_publication_disabled()
    verify_evidence(wheel)
    verify_sdist(sdist)
    print(f"A03 candidate verified: {wheel.name} + {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
