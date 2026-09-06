from agent_inbox.contract import (
    AgentProfile, ArtifactEvidence, CommitEvidence, CompletionEvidence, MissionSpec, TestEvidence,
)


class Clock:
    def __init__(self, value=1_700_000_000.0): self.value = value
    def __call__(self): return self.value


def profile(**changes):
    values = dict(agent_id="worker", capabilities=("python", "review"), permissions=("read", "write"), ownership=("demo",), max_running=1, max_lease_seconds=60)
    values.update(changes); return AgentProfile(**values)


def spec(key="task-1", **changes):
    values = dict(idempotency_key=key, title="Synthetic mission", payload={"fixture": key}, priority=50, owner_scope="demo", required_capabilities=("python",), required_permissions=("write",), max_retries=2)
    values.update(changes); return MissionSpec(**values)


def evidence(**changes):
    values = dict(
        summary="Verified synthetic result",
        commits=(CommitEvidence("1" * 40, "synthetic/example"),),
        tests=(TestEvidence("unit", "passed", "python -m unittest"),), artifacts=(),
    )
    values.update(changes); return CompletionEvidence(**values)


def artifact(): return ArtifactEvidence("report", "a" * 64, "artifacts/report.json")

