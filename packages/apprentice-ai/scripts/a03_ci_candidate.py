"""Run the complete portable A03 candidate gate for Apprentice AI."""

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
from a03_verify_release_evidence import VerificationError, verify as verify_release_evidence


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EVIDENCE = ROOT / "release-evidence-a03"
EXPECTED_TOOLS = {
    "pip": "25.2",
    "setuptools": "83.0.0",
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
            f"expected one wheel and one sdist: wheels={wheels!r} sdists={sdists!r}"
        )
    return wheels[0], sdists[0]


def installed_cli() -> str:
    executable = shutil.which("apprentice")
    if not executable:
        raise CandidateError("installed apprentice console entry point is missing")
    return executable


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
    if "apprentice" not in scripts:
        raise CandidateError(f"apprentice console entry point missing: {scripts}")


def verify_existing_suite() -> None:
    run([sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts"])
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])


def smoke_cli(executable: str) -> None:
    with tempfile.TemporaryDirectory(prefix="apprentice-wheel-smoke-") as tmp:
        root = Path(tmp)
        data = root / "data"
        learnpack = root / "reference.learnpack"
        run([executable, "--data-dir", str(data), "version"], cwd=root)
        run([executable, "--data-dir", str(data), "capabilities"], cwd=root)
        run(
            [
                executable,
                "--data-dir",
                str(data),
                "demo",
                "--output",
                str(learnpack),
            ],
            cwd=root,
        )
        if not learnpack.is_file() or learnpack.stat().st_size == 0:
            raise CandidateError("installed wheel demo did not create a LearnPack")


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


def verify_candidate_evidence(wheel: Path) -> None:
    shutil.rmtree(EVIDENCE, ignore_errors=True)
    receipt = build_release_evidence(ROOT, DIST, EVIDENCE)
    verify_release_evidence(DIST, EVIDENCE / "SHA256SUMS.txt")
    if receipt.get("execution_boundary") != "preview-only":
        raise CandidateError("release evidence lost the preview-only boundary")
    if not receipt.get("tests_verified") or not receipt.get("counterproof_verified"):
        raise CandidateError("release evidence is missing test/counter-proof proof")
    for field in ("signed", "attested", "tagged", "published", "released"):
        if receipt.get(field) is not False:
            raise CandidateError(f"release evidence falsely claims {field}")

    with tempfile.TemporaryDirectory(prefix="apprentice-tamper-") as tmp:
        tampered = Path(tmp) / "dist"
        shutil.copytree(DIST, tampered)
        target = tampered / wheel.name
        target.write_bytes(target.read_bytes() + b"counter-proof")
        try:
            verify_release_evidence(tampered, EVIDENCE / "SHA256SUMS.txt")
        except VerificationError:
            pass
        else:
            raise CandidateError("tampered wheel was accepted")


def verify_sdist(sdist: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="apprentice-sdist-") as tmp:
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
                "setuptools==83.0.0",
                "wheel==0.45.1",
            ],
            cwd=root,
        )
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-build-isolation",
                "--no-deps",
                str(sdist),
            ],
            cwd=root,
        )
        scripts_dir = python.parent
        executable = scripts_dir / ("apprentice.exe" if sys.platform == "win32" else "apprentice")
        if not executable.is_file():
            raise CandidateError("sdist-derived apprentice executable is missing")
        data = root / "sdist-data"
        learnpack = root / "sdist-reference.learnpack"
        run([str(executable), "--data-dir", str(data), "version"], cwd=root)
        run([str(executable), "--data-dir", str(data), "capabilities"], cwd=root)
        run(
            [
                str(executable),
                "--data-dir",
                str(data),
                "demo",
                "--output",
                str(learnpack),
            ],
            cwd=root,
        )
        if not learnpack.is_file() or learnpack.stat().st_size == 0:
            raise CandidateError("sdist-derived demo did not create a LearnPack")


def main() -> int:
    verify_toolchain()
    wheel, sdist = build_artifacts()
    install_and_verify_wheel(wheel)
    verify_existing_suite()
    smoke_cli(installed_cli())
    verify_publication_disabled()
    verify_candidate_evidence(wheel)
    verify_sdist(sdist)
    print(f"A03 Apprentice candidate verified: {wheel.name} + {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
