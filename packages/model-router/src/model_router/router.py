from __future__ import annotations

from dataclasses import dataclass

from model_router.models import AgentProfile, RiskLevel, RouteDecision, TaskRequest
from model_router.registry import CapabilityRegistry


@dataclass(frozen=True, slots=True)
class _ScoredCandidate:
    profile: AgentProfile
    score: float
    estimated_cost: float


class ModelRouter:
    """Hard constraints first, explainable ranking second."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def route(self, task: TaskRequest) -> RouteDecision:
        if len(self.registry) == 0:
            return RouteDecision(
                task_id=task.task_id,
                selected_agent_id=None,
                selected_owner=None,
                score=0.0,
                estimated_cost_usd=None,
                candidate_scores={},
                rejections={"registry": ["no agents registered"]},
                explanations=("routing stopped before scoring",),
            )

        rejections: dict[str, list[str]] = {}
        candidates: list[_ScoredCandidate] = []
        for profile in self.registry.profiles():
            reasons = self._rejection_reasons(profile, task)
            if reasons:
                rejections[profile.agent_id] = reasons
                continue
            estimated_cost = profile.estimated_cost(task.context_tokens)
            candidates.append(
                _ScoredCandidate(
                    profile=profile,
                    score=self._score(profile, task, estimated_cost),
                    estimated_cost=estimated_cost,
                )
            )

        if not candidates:
            return RouteDecision(
                task_id=task.task_id,
                selected_agent_id=None,
                selected_owner=None,
                score=0.0,
                estimated_cost_usd=None,
                candidate_scores={},
                rejections=rejections,
                explanations=("all candidates failed at least one hard constraint",),
            )

        candidates.sort(key=lambda item: (-item.score, item.profile.agent_id))
        selected = candidates[0]
        disagreement = len(candidates) > 1 and selected.score - candidates[1].score <= 0.03
        human_approval = task.risk is RiskLevel.HIGH
        if human_approval:
            escalation_reason = "high-risk mission requires human approval"
        elif disagreement:
            escalation_reason = "top candidates are within 0.03 score"
        else:
            escalation_reason = None

        explanations = (
            f"selected {selected.profile.agent_id} owned by {selected.profile.owner}",
            f"historical success {selected.profile.historical_success_rate:.1%}",
            f"estimated cost ${selected.estimated_cost:.4f} within ${task.budget_usd:.4f} budget",
            f"p95 latency {selected.profile.p95_latency_ms}ms within {task.max_latency_ms}ms limit",
        )
        return RouteDecision(
            task_id=task.task_id,
            selected_agent_id=selected.profile.agent_id,
            selected_owner=selected.profile.owner,
            score=selected.score,
            estimated_cost_usd=selected.estimated_cost,
            candidate_scores={
                item.profile.agent_id: item.score for item in sorted(candidates, key=lambda x: x.profile.agent_id)
            },
            rejections=rejections,
            explanations=explanations,
            disagreement=disagreement,
            human_approval_required=human_approval,
            escalation_reason=escalation_reason,
        )

    @staticmethod
    def _rejection_reasons(profile: AgentProfile, task: TaskRequest) -> list[str]:
        if not profile.active:
            return ["agent is inactive"]
        if profile.current_load >= profile.max_concurrency:
            return ["concurrency limit reached"]

        reasons: list[str] = []
        missing_capabilities = task.required_capabilities - profile.capabilities
        if missing_capabilities:
            reasons.append(f"missing capabilities: {', '.join(sorted(missing_capabilities))}")
        required_permissions = set(task.required_permissions)
        if task.risk is RiskLevel.HIGH:
            required_permissions.add("high_risk")
        missing_permissions = required_permissions - profile.permissions
        if missing_permissions:
            reasons.append(f"missing permissions: {', '.join(sorted(missing_permissions))}")
        if profile.estimated_cost(task.context_tokens) > task.budget_usd:
            reasons.append("budget exceeded")
        if profile.p95_latency_ms > task.max_latency_ms:
            reasons.append("latency limit exceeded")
        if profile.context_window_tokens < task.context_tokens:
            reasons.append("context window exceeded")
        return reasons

    @staticmethod
    def _score(profile: AgentProfile, task: TaskRequest, estimated_cost: float) -> float:
        cost_fit = 1 - (estimated_cost / task.budget_usd)
        latency_fit = 1 - (profile.p95_latency_ms / task.max_latency_ms)
        context_fit = 1 - (task.context_tokens / profile.context_window_tokens)
        load_fit = 1 - (profile.current_load / profile.max_concurrency)
        score = (
            profile.historical_success_rate * 0.50
            + cost_fit * 0.20
            + latency_fit * 0.15
            + context_fit * 0.10
            + load_fit * 0.05
        )
        return round(max(0.0, min(1.0, score)), 6)
