from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MONOREPO.json"
SHA = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_TOOL_COUNT = 23
EXPECTED_DIRECT_IMPORTS = 18
EXPECTED_NESTED_IMPORTS = 4


def fail(message: str) -> None:
    raise SystemExit(f"monorepo manifest: {message}")


def git(*arguments: str, allow_nonzero: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode and not allow_nonzero:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        fail(f"git {' '.join(arguments)} failed: {detail}")
    return result


def main() -> None:
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(str(exc))

    if data.get("schema_version") != 1:
        fail("schema_version must be 1")
    if data.get("delete_authorized") is not False:
        fail("source deletion must remain explicitly unauthorized")
    if data.get("status") != "LOCAL_PREPARATION":
        fail("status must remain LOCAL_PREPARATION")

    target = data.get("target")
    if not isinstance(target, dict):
        fail("target must be an object")
    if target.get("kind") != "PRODUCT":
        fail("target.kind must be PRODUCT")
    target_repository = target.get("repository")
    if not isinstance(target_repository, str) or target_repository.count("/") != 1:
        fail("target.repository is invalid")
    sources = target.get("sources")
    if not isinstance(sources, list) or len(sources) != EXPECTED_TOOL_COUNT:
        fail(f"target.sources must contain exactly {EXPECTED_TOOL_COUNT} tools")

    seen_repositories: set[str] = set()
    seen_paths: set[str] = set()
    keep_count = 0
    direct_count = 0
    nested_count = 0
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            fail(f"source {index} must be an object")
        repository = source.get("repository")
        if not isinstance(repository, str) or repository.count("/") != 1:
            fail(f"source {index} has an invalid repository")
        if repository in seen_repositories:
            fail(f"duplicate source {repository}")
        seen_repositories.add(repository)

        disposition = source.get("disposition")
        if disposition not in {"ABSORB", "KEEP"}:
            fail(f"{repository} has an invalid disposition")

        metadata = source.get("source")
        if not isinstance(metadata, dict):
            fail(f"{repository} has no source metadata")
        if not SHA.fullmatch(str(metadata.get("head", ""))):
            fail(f"{repository} has an invalid source head")
        if not SHA.fullmatch(str(metadata.get("tree", ""))):
            fail(f"{repository} has an invalid source tree")
        if metadata.get("visibility") not in {"PUBLIC", "PRIVATE", "INTERNAL"}:
            fail(f"{repository} has an invalid visibility")
        if not isinstance(metadata.get("archived"), bool):
            fail(f"{repository} has an invalid archived flag")
        default_branch = metadata.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch.strip():
            fail(f"{repository} has an invalid default branch")

        raw_path = source.get("target_path")
        if not isinstance(raw_path, str) or "\\" in raw_path:
            fail(f"{repository} has an invalid target path")
        pure = PurePosixPath(raw_path)
        if pure.is_absolute() or ".." in pure.parts:
            fail(f"{repository} target path escapes the repository")
        if raw_path in seen_paths:
            fail(f"duplicate target path {raw_path}")
        seen_paths.add(raw_path)

        if disposition == "KEEP":
            keep_count += 1
            if repository != target_repository or raw_path != ".":
                fail("the KEEP entry must be the target repository at '.'")
        else:
            if raw_path == "." or not pure.parts:
                fail(f"{repository} ABSORB target must be under packages/")
            if pure.parts[-1] != repository.rsplit("/", 1)[-1]:
                fail(f"{repository} target path does not preserve its repository name")
            if len(pure.parts) == 2 and pure.parts[0] == "packages":
                direct_count += 1
            elif (
                len(pure.parts) == 4
                and pure.parts[0] == "packages"
                and pure.parts[2] == "packages"
            ):
                nested_count += 1
            else:
                fail(f"{repository} must map to a direct or nested packages/ path")

        local = ROOT if raw_path == "." else ROOT.joinpath(*pure.parts)
        if not local.is_dir():
            fail(f"{repository} target path is missing: {raw_path}")

        head = str(metadata["head"])
        recorded_tree = str(metadata["tree"])
        actual_tree = git("rev-parse", f"{head}^{{tree}}").stdout.strip()
        if actual_tree != recorded_tree:
            fail(f"{repository} source tree does not match its source head")
        ancestry = git("merge-base", "--is-ancestor", head, "HEAD", allow_nonzero=True)
        if ancestry.returncode != 0:
            fail(f"{repository} source head is not preserved in target history")

    if keep_count != 1:
        fail("manifest must contain exactly one KEEP target")
    if direct_count != EXPECTED_DIRECT_IMPORTS:
        fail(f"manifest must contain exactly {EXPECTED_DIRECT_IMPORTS} direct imports")
    if nested_count != EXPECTED_NESTED_IMPORTS:
        fail(f"manifest must contain exactly {EXPECTED_NESTED_IMPORTS} nested imports")

    print(
        "monorepo manifest: "
        f"{len(sources)} tools mapped for {target_repository} "
        f"({direct_count} direct, {nested_count} nested, {keep_count} root)"
    )


if __name__ == "__main__":
    main()
