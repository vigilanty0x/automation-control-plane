const validScenarios = new Set(["success", "empty", "degraded", "timeout", "error"]);

export function isApiScenario(value) {
  return validScenarios.has(value);
}

export function buildScenarioPayload(snapshot, scenario, now = new Date()) {
  const fresh = {
    ...snapshot,
    provenance: { ...snapshot.provenance, fetchedAt: now.toISOString() },
  };

  if (scenario === "empty") {
    return { status: 200, body: { ...fresh, agents: [], events: [] } };
  }
  if (scenario === "degraded") {
    return {
      status: 206,
      body: {
        ...fresh,
        warnings: ["Cost telemetry is delayed; operational states remain current."],
      },
    };
  }
  if (scenario === "timeout") {
    return {
      status: 504,
      body: { error: "upstream_timeout", message: "The demo adapter reached its 2s boundary." },
    };
  }
  if (scenario === "error") {
    return {
      status: 503,
      body: { error: "source_unavailable", message: "The synthetic source is unavailable." },
    };
  }
  return { status: 200, body: fresh };
}

export function normalizeHttpPayload(status, payload) {
  if (status === 504) {
    return { state: "timeout", message: payload?.message ?? "The request timed out.", data: null, retryable: true };
  }
  if (status >= 500) {
    return { state: "error", message: payload?.message ?? "The source returned an error.", data: null, retryable: true };
  }
  if (!payload || !payload.provenance || !Array.isArray(payload.agents)) {
    return { state: "error", message: "The source returned an invalid view contract.", data: null, retryable: false };
  }
  if (payload.agents.length === 0) {
    return { state: "empty", message: "No agent runs match this snapshot.", data: payload, retryable: false };
  }
  if (status === 206 || Array.isArray(payload.warnings)) {
    return { state: "degraded", message: payload.warnings?.[0] ?? "Some telemetry is delayed.", data: payload, retryable: true };
  }
  return { state: "ready", message: "Snapshot loaded.", data: payload, retryable: false };
}

export function summarizeAgents(agents) {
  return agents.reduce(
    (summary, agent) => {
      summary[agent.status] += 1;
      summary.costUsd += agent.costUsd;
      summary.tokens += agent.tokens;
      return summary;
    },
    { running: 0, waiting: 0, failed: 0, completed: 0, costUsd: 0, tokens: 0 },
  );
}

export function filterAgents(agents, status, query) {
  const normalizedQuery = query.trim().toLowerCase();
  return agents.filter((agent) => {
    const matchesStatus = status === "all" || agent.status === status;
    const haystack = `${agent.name} ${agent.role} ${agent.task} ${agent.runId}`.toLowerCase();
    return matchesStatus && (!normalizedQuery || haystack.includes(normalizedQuery));
  });
}

export function formatAge(iso, nowMs = Date.now()) {
  const seconds = Math.max(0, Math.floor((nowMs - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}
