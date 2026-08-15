from __future__ import annotations

from collections.abc import Iterable

from model_router.models import AgentProfile


class CapabilityRegistry:
    """In-memory registry with explicit ownership and replacement semantics."""

    def __init__(self, profiles: Iterable[AgentProfile] = ()) -> None:
        self._profiles: dict[str, AgentProfile] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: AgentProfile, *, replace: bool = False) -> None:
        if profile.agent_id in self._profiles and not replace:
            raise ValueError(f"agent already registered: {profile.agent_id}")
        self._profiles[profile.agent_id] = profile

    def get(self, agent_id: str) -> AgentProfile:
        try:
            return self._profiles[agent_id]
        except KeyError as exc:
            raise KeyError(f"unknown agent: {agent_id}") from exc

    def profiles(self) -> tuple[AgentProfile, ...]:
        return tuple(self._profiles[key] for key in sorted(self._profiles))

    def to_dict(self) -> list[dict[str, object]]:
        return [profile.to_dict() for profile in self.profiles()]

    @classmethod
    def from_dict(cls, payload: list[dict[str, object]]) -> CapabilityRegistry:
        return cls(AgentProfile.from_dict(item) for item in payload)

    def __len__(self) -> int:
        return len(self._profiles)
