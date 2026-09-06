"""Canonical capability and permission registry."""

from __future__ import annotations

from .models import AgentProfile, ContractError


class AgentRegistry:
    def __init__(self, profiles: tuple[AgentProfile, ...]) -> None:
        if not 1 <= len(profiles) <= 512: raise ContractError("registry requires 1 to 512 agents")
        if len({p.agent_id for p in profiles}) != len(profiles): raise ContractError("agent ids must be unique")
        self._profiles = {p.agent_id: p for p in profiles}

    def get(self, agent_id: str) -> AgentProfile | None: return self._profiles.get(agent_id)
    def inventory(self) -> list[dict]: return [self._profiles[key].to_dict() for key in sorted(self._profiles)]

