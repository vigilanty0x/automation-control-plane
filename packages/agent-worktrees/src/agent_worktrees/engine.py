from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .models import EvidenceBundle, MissionRecord, MissionRequest, MissionState, normalize_owned_path
from .store import SQLiteMissionStore


class GitCommandError(RuntimeError):
    """Raised when a bounded Git command fails."""


class SafetyError(RuntimeError):
    """Raised when an operation could lose work or violate ownership."""


class AgentWorktreeService:
    def __init__(self, store: SQLiteMissionStore) -> None:
        self.store = store

    @staticmethod
    def _git(
        cwd: str | Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=Path(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "git command failed"
            raise GitCommandError(f"git {' '.join(args)}: {message}")
        return result

    @classmethod
    def repository_root(cls, path: str | Path) -> Path:
        result = cls._git(path, "rev-parse", "--show-toplevel")
        return Path(result.stdout.strip()).resolve()

    @classmethod
    def default_db_path(cls, repo: str | Path) -> Path:
        root = cls.repository_root(repo)
        common = cls._git(root, "rev-parse", "--git-common-dir").stdout.strip()
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = root / common_path
        return common_path.resolve() / "agent-worktrees.sqlite3"

    @classmethod
    def default_worktree_root(cls, repo: str | Path) -> Path:
        root = cls.repository_root(repo)
        return root.parent / f".{root.name}-agent-worktrees"

    def register(
        self,
        request: MissionRequest,
        *,
        repo: str | Path,
        worktree_root: str | Path,
    ) -> tuple[MissionRecord, bool]:
        root = self.repository_root(repo)
        self._git(root, "check-ref-format", "--branch", request.base_ref)
        self._git(root, "show-ref", "--verify", f"refs/heads/{request.base_ref}")
        worktrees = Path(worktree_root).resolve()
        if worktrees == root or root in worktrees.parents:
            raise SafetyError("worktree root must be outside the primary repository")
        mission, created = self.store.register(
            request,
            repo_root=root,
            worktree_root=worktrees,
        )
        if created and self.branch_exists(root, mission.branch):
            self.store.transition(
                mission.mission_id,
                MissionState.FAILED,
                actor="registry",
                reason="deterministic mission branch already existed before registration",
            )
            raise SafetyError("deterministic mission branch already exists")
        return mission, created

    @classmethod
    def worktrees(cls, repo: str | Path) -> dict[str, dict[str, str | None]]:
        root = cls.repository_root(repo)
        output = cls._git(root, "worktree", "list", "--porcelain").stdout
        records: dict[str, dict[str, str | None]] = {}
        current: dict[str, str | None] = {}
        for line in [*output.splitlines(), ""]:
            if not line:
                if current.get("path"):
                    records[str(Path(str(current["path"])).resolve())] = current
                current = {}
                continue
            key, _, value = line.partition(" ")
            if key == "worktree":
                current["path"] = value
            elif key == "HEAD":
                current["head"] = value
            elif key == "branch":
                current["branch"] = value.removeprefix("refs/heads/")
            elif key == "detached":
                current["branch"] = None
        return records

    @classmethod
    def branch_exists(cls, repo: str | Path, branch: str) -> bool:
        result = cls._git(
            repo,
            "show-ref",
            "--verify",
            f"refs/heads/{branch}",
            check=False,
        )
        return result.returncode == 0

    def provision(self, mission_id: str, *, actor: str) -> MissionRecord:
        record = self.store.get(mission_id)
        if record.state == MissionState.REJECTED:
            raise SafetyError(record.last_error or "mission was rejected")
        if record.state not in {MissionState.QUEUED, MissionState.RUNNING}:
            raise SafetyError(f"cannot provision a {record.state.value} mission")
        repo = Path(record.repo_root)
        path = Path(record.worktree_path)
        registered = self.worktrees(repo)
        existing = registered.get(str(path.resolve()))
        if existing is not None:
            if existing.get("branch") != record.branch:
                raise SafetyError("worktree path is registered to a different branch")
            if record.state == MissionState.RUNNING:
                return record
            return self.store.transition(
                mission_id,
                MissionState.RUNNING,
                actor=actor,
                reason="existing isolated worktree resumed without duplication",
            )
        if path.exists():
            raise SafetyError("worktree path exists but is not registered with Git")
        for info in registered.values():
            if info.get("branch") == record.branch:
                raise SafetyError("mission branch is already checked out in another worktree")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.branch_exists(repo, record.branch):
                self._git(repo, "worktree", "add", str(path), record.branch)
            else:
                self._git(
                    repo,
                    "worktree",
                    "add",
                    "-b",
                    record.branch,
                    str(path),
                    record.request.base_ref,
                )
        except (GitCommandError, OSError) as exc:
            self.store.transition(
                mission_id,
                MissionState.FAILED,
                actor="engine",
                reason=f"worktree provisioning failed: {exc}",
            )
            raise
        return self.store.transition(
            mission_id,
            MissionState.RUNNING,
            actor=actor,
            reason="isolated branch and worktree provisioned",
        )

    def complete(
        self,
        mission_id: str,
        evidence: EvidenceBundle,
        *,
        actor: str,
    ) -> MissionRecord:
        record = self.store.get(mission_id)
        if record.state != MissionState.RUNNING:
            raise SafetyError("only a running mission can complete")
        path = Path(record.worktree_path)
        info = self.worktrees(record.repo_root).get(str(path.resolve()))
        if info is None or info.get("branch") != record.branch:
            raise SafetyError("mission worktree is missing or points to another branch")
        if self._git(path, "status", "--porcelain", "--untracked-files=all").stdout.strip():
            raise SafetyError("worktree must be clean before completion")
        if evidence.produced_by != record.request.agent_id:
            raise SafetyError("evidence producer does not match the assigned agent")
        head = self._git(path, "rev-parse", "HEAD").stdout.strip()
        if evidence.commit_sha != head:
            raise SafetyError("evidence commit does not match the worktree HEAD")
        changed = self._git(
            path,
            "diff",
            "--name-only",
            "-z",
            f"{record.request.base_ref}...HEAD",
        ).stdout.split("\0")
        for changed_path in filter(None, changed):
            normalized = normalize_owned_path(changed_path)
            allowed = any(
                normalized == owned or normalized.startswith(f"{owned}/")
                for owned in record.request.owned_paths
            )
            if not allowed:
                raise SafetyError(f"commit changes a path outside mission ownership: {normalized}")
        for artifact in evidence.artifacts:
            relative = normalize_owned_path(artifact)
            candidate = (path / relative).resolve()
            try:
                candidate.relative_to(path.resolve())
            except ValueError as exc:
                raise SafetyError("artifact escapes the mission worktree") from exc
            if not candidate.is_file():
                raise SafetyError(f"evidence artifact does not exist: {relative}")
        return self.store.transition(
            mission_id,
            MissionState.DONE,
            actor=actor,
            reason="clean commit, tests, artifacts, and criteria verified",
            evidence=evidence,
        )

    def fail(self, mission_id: str, *, actor: str, reason: str) -> MissionRecord:
        return self.store.transition(
            mission_id,
            MissionState.FAILED,
            actor=actor,
            reason=reason,
        )

    def retry(self, mission_id: str, *, actor: str) -> MissionRecord:
        return self.store.retry(mission_id, actor=actor)

    def wait(self, mission_id: str, *, actor: str, reason: str) -> MissionRecord:
        return self.store.transition(
            mission_id,
            MissionState.WAITING,
            actor=actor,
            reason=reason,
        )

    def resume(self, mission_id: str, *, actor: str, reason: str) -> MissionRecord:
        return self.store.transition(
            mission_id,
            MissionState.RUNNING,
            actor=actor,
            reason=reason,
        )

    def cleanup(
        self,
        mission_id: str,
        *,
        actor: str,
        keep_branch: bool = False,
    ) -> MissionRecord:
        record = self.store.get(mission_id)
        if record.state != MissionState.DONE:
            raise SafetyError("cleanup requires a done mission")
        if record.cleaned:
            return record
        repo = Path(record.repo_root)
        path = Path(record.worktree_path)
        registered = self.worktrees(repo)
        info = registered.get(str(path.resolve()))
        if info is not None:
            if info.get("branch") != record.branch:
                raise SafetyError("cleanup target belongs to a different branch")
            if self._git(path, "status", "--porcelain", "--untracked-files=all").stdout.strip():
                raise SafetyError("cleanup refuses a dirty worktree")
        elif path.exists():
            raise SafetyError("cleanup refuses an unregistered existing directory")
        ancestor_integrated = True
        if self.branch_exists(repo, record.branch):
            merged = self._git(
                repo,
                "merge-base",
                "--is-ancestor",
                record.branch,
                record.request.base_ref,
                check=False,
            )
            if merged.returncode != 0:
                ancestor_integrated = False
                owned_tree = self._git(
                    repo,
                    "diff",
                    "--quiet",
                    record.branch,
                    record.request.base_ref,
                    "--",
                    *record.request.owned_paths,
                    check=False,
                )
                if owned_tree.returncode != 0:
                    raise SafetyError("mission branch is not integrated into its base ref")
                if not keep_branch:
                    raise SafetyError(
                        "squash-equivalent integration requires --keep-branch to avoid force deletion"
                    )
        if info is not None:
            self._git(repo, "worktree", "remove", str(path))
        if not keep_branch and ancestor_integrated and self.branch_exists(repo, record.branch):
            self._git(repo, "branch", "-d", record.branch)
        return self.store.mark_cleaned(mission_id, actor=actor)

    def recover(self, *, actor: str) -> list[MissionRecord]:
        recovered: list[MissionRecord] = []
        for record in self.store.list():
            if record.state not in {MissionState.RUNNING, MissionState.WAITING}:
                continue
            path = str(Path(record.worktree_path).resolve())
            info = self.worktrees(record.repo_root).get(path)
            if info is None or info.get("branch") != record.branch:
                recovered.append(
                    self.store.transition(
                        record.mission_id,
                        MissionState.FAILED,
                        actor=actor,
                        reason="registered worktree is missing or mismatched",
                    )
                )
        return recovered

    def audit(self, *, repo: str | Path) -> dict[str, Any]:
        root = self.repository_root(repo)
        actual = self.worktrees(root)
        records = [item for item in self.store.list() if Path(item.repo_root) == root]
        managed: list[dict[str, Any]] = []
        expected_paths: set[str] = set()
        for record in records:
            path = str(Path(record.worktree_path).resolve())
            if record.cleaned or record.state == MissionState.REJECTED:
                continue
            expected_paths.add(path)
            info = actual.get(path)
            status = "missing" if info is None else "ok"
            if info is not None and info.get("branch") != record.branch:
                status = "branch-mismatch"
            managed.append(
                {
                    "mission_id": record.mission_id,
                    "path": path,
                    "branch": record.branch,
                    "state": record.state.value,
                    "status": status,
                }
            )
        primary = str(root.resolve())
        unmanaged = sorted(path for path in actual if path not in expected_paths and path != primary)
        return {
            "repo_root": primary,
            "managed": managed,
            "unmanaged": unmanaged,
            "metrics": self.store.metrics(),
        }
