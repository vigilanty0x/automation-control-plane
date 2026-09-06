from __future__ import annotations

from typing import Any

from ._common import (
    ValidationError,
    blocked,
    ensure_unique,
    evidence,
    expect_bool,
    expect_exact_keys,
    expect_list,
    expect_object,
    expect_str,
)

MAX_AGENTS = 256
MAX_ROUTES = 4096
MAX_CAPABILITIES = 256


def evaluate_routing(payload: Any) -> dict[str, Any]:
    try:
        return _evaluate_routing(payload)
    except ValidationError as exc:
        return blocked("routing", payload, exc)


def _evaluate_routing(payload: Any) -> dict[str, Any]:
    root = expect_object(payload)
    expect_exact_keys(
        root,
        required=("agents", "routes"),
        optional=("required_capabilities",),
    )
    raw_agents = expect_list(root["agents"], "$.agents", maximum=MAX_AGENTS)
    raw_routes = expect_list(root["routes"], "$.routes", maximum=MAX_ROUTES)
    if not raw_agents:
        raise ValidationError("$.agents: at least one agent is required")

    agents: dict[str, dict[str, Any]] = {}
    for index, raw_agent in enumerate(raw_agents):
        path = f"$.agents[{index}]"
        agent = expect_object(raw_agent, path)
        expect_exact_keys(
            agent,
            required=("id", "healthy", "owner", "capabilities"),
            path=path,
        )
        agent_id = expect_str(agent["id"], f"{path}.id", identifier=True)
        if agent_id in agents:
            raise ValidationError(f"{path}.id: duplicate agent id: {agent_id}")
        owner = expect_str(agent["owner"], f"{path}.owner", identifier=True)
        capabilities_raw = expect_list(
            agent["capabilities"], f"{path}.capabilities", maximum=MAX_CAPABILITIES
        )
        capabilities = [
            expect_str(value, f"{path}.capabilities[{cap_index}]", identifier=True)
            for cap_index, value in enumerate(capabilities_raw)
        ]
        ensure_unique(capabilities, f"{path}.capabilities")
        agents[agent_id] = {
            "id": agent_id,
            "healthy": expect_bool(agent["healthy"], f"{path}.healthy"),
            "owner": owner,
            "capabilities": tuple(sorted(capabilities)),
        }

    required_raw = expect_list(
        root.get("required_capabilities", []),
        "$.required_capabilities",
        maximum=MAX_CAPABILITIES,
    )
    required_capabilities = [
        expect_str(value, f"$.required_capabilities[{index}]", identifier=True)
        for index, value in enumerate(required_raw)
    ]
    ensure_unique(required_capabilities, "$.required_capabilities")

    active_routes: list[dict[str, str]] = []
    route_keys: list[str] = []
    for index, raw_route in enumerate(raw_routes):
        path = f"$.routes[{index}]"
        route = expect_object(raw_route, path)
        expect_exact_keys(
            route,
            required=("source", "target", "capability", "owner"),
            optional=("enabled",),
            path=path,
        )
        source = expect_str(route["source"], f"{path}.source", identifier=True)
        target = expect_str(route["target"], f"{path}.target", identifier=True)
        capability = expect_str(
            route["capability"], f"{path}.capability", identifier=True
        )
        owner = expect_str(route["owner"], f"{path}.owner", identifier=True)
        enabled = expect_bool(route.get("enabled", True), f"{path}.enabled")
        if source == target:
            raise ValidationError(f"{path}: self routes are not accepted")
        if source not in agents:
            raise ValidationError(f"{path}.source: unknown agent: {source}")
        if target not in agents:
            raise ValidationError(f"{path}.target: unknown agent: {target}")
        if capability not in agents[target]["capabilities"]:
            raise ValidationError(
                f"{path}.capability: target {target} does not declare {capability}"
            )
        if owner != agents[target]["owner"]:
            raise ValidationError(
                f"{path}.owner: route owner must match target agent owner"
            )
        route_key = f"{source}>{target}:{capability}"
        route_keys.append(route_key)
        if enabled:
            active_routes.append(
                {
                    "source": source,
                    "target": target,
                    "capability": capability,
                    "owner": owner,
                }
            )
    ensure_unique(route_keys, "$.routes")

    unhealthy_agents = sorted(
        agent_id for agent_id, agent in agents.items() if not agent["healthy"]
    )
    routed_capabilities = {route["capability"] for route in active_routes}
    missing_required = sorted(set(required_capabilities) - routed_capabilities)
    status = (
        "passed"
        if active_routes and not unhealthy_agents and not missing_required
        else "failed"
    )
    details = {
        "agent_count": len(agents),
        "healthy_agent_count": len(agents) - len(unhealthy_agents),
        "active_route_count": len(active_routes),
        "unhealthy_agents": unhealthy_agents,
        "missing_required_capabilities": missing_required,
        "active_routes": sorted(
            active_routes,
            key=lambda item: (
                item["source"],
                item["target"],
                item["capability"],
            ),
        ),
        "rule": "every agent must be healthy, at least one route must be active, and required capabilities must be routed",
    }
    return evidence("routing", status, payload, details)
