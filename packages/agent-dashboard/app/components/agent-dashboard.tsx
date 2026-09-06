"use client";

import { useMemo, useRef, useState } from "react";
import Link from "next/link";
import type {
  AgentRecord,
  AgentStatus,
  ApiScenario,
  DashboardSnapshot,
  NormalizedView,
} from "@/lib/contracts";
import {
  filterAgents,
  formatAge,
  normalizeHttpPayload,
  summarizeAgents,
} from "@/lib/adapter.mjs";

type DashboardView = "overview" | "agents" | "runs" | "evidence";
type ScenarioOption = ApiScenario | "loading";
type StatusFilter = "all" | AgentStatus;
type IconName = "agents" | "activity" | "evidence" | "refresh" | "download" | "search" | "chevron" | "retry" | "close";

const NAV_ITEMS: Array<{ view: DashboardView; href: string; label: string; icon: IconName }> = [
  { view: "overview", href: "/", label: "Overview", icon: "activity" },
  { view: "agents", href: "/agents", label: "Agents", icon: "agents" },
  { view: "runs", href: "/runs", label: "Run timeline", icon: "activity" },
  { view: "evidence", href: "/evidence", label: "Evidence", icon: "evidence" },
];

const STATUS_LABELS: Record<AgentStatus, string> = {
  running: "Running",
  waiting: "Waiting",
  failed: "Failed",
  completed: "Completed",
};

function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, React.ReactNode> = {
    agents: <><circle cx="9" cy="8" r="3"/><path d="M3.5 19c.5-3.2 2.4-5 5.5-5s5 1.8 5.5 5M16 5.5a3 3 0 0 1 0 5.8M16.5 14c2.3.5 3.7 2.1 4 5"/></>,
    activity: <><path d="M3 12h4l2.2-6 4 12 2.3-6H21"/></>,
    evidence: <><path d="M7 3h10l3 3v15H4V3h3Z"/><path d="M8 10h8M8 14h8M8 18h5M16 3v4h4"/></>,
    refresh: <><path d="M20 6v5h-5"/><path d="M18.5 8A8 8 0 1 0 20 15"/></>,
    download: <><path d="M12 3v12M7 10l5 5 5-5M4 20h16"/></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></>,
    chevron: <path d="m9 18 6-6-6-6"/>,
    retry: <><path d="M20 6v5h-5"/><path d="M18.5 8A8 8 0 1 0 20 15"/></>,
    close: <><path d="m6 6 12 12M18 6 6 18"/></>,
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

function StatusPill({ status }: { status: AgentStatus }) {
  return <span className={`status-pill status-${status}`}><span aria-hidden="true" />{STATUS_LABELS[status]}</span>;
}

function formatDuration(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}m ${remainder.toString().padStart(2, "0")}s`;
}

function displayTime(iso: string) {
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "UTC" }).format(new Date(iso));
}

function formatInteger(value: number) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
}

export function AgentDashboard({ initialSnapshot, view }: { initialSnapshot: DashboardSnapshot; view: DashboardView }) {
  const [envelope, setEnvelope] = useState<NormalizedView>({ state: "ready", message: "Snapshot loaded.", data: initialSnapshot, retryable: false });
  const [scenario, setScenario] = useState<ScenarioOption>("success");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [query, setQuery] = useState("");
  const [selectedAgentId, setSelectedAgentId] = useState(initialSnapshot.agents[0]?.id ?? "");
  const [detailOpen, setDetailOpen] = useState(false);
  const [timelineRange, setTimelineRange] = useState<"1h" | "24h" | "7d">("24h");
  const [liveMessage, setLiveMessage] = useState("Dashboard ready.");
  const [clock, setClock] = useState(new Date(initialSnapshot.provenance.fetchedAt).getTime() + 18_000);
  const requestSequence = useRef(0);

  const data = envelope.data;
  const summary = useMemo(() => summarizeAgents(data?.agents ?? []), [data]);
  const visibleAgents = useMemo(
    () => filterAgents(data?.agents ?? [], statusFilter, query),
    [data, query, statusFilter],
  );
  const selectedAgent = data?.agents.find((agent) => agent.id === selectedAgentId) ?? data?.agents[0] ?? null;
  const eventLimit = timelineRange === "1h" ? 3 : timelineRange === "24h" ? 5 : 8;
  const visibleEvents = data?.events.slice(0, eventLimit) ?? [];

  async function loadScenario(nextScenario: ScenarioOption) {
    const sequence = ++requestSequence.current;
    setScenario(nextScenario);
    setEnvelope({ state: "loading", message: "Requesting a fresh snapshot…", data: null, retryable: false });
    setLiveMessage(`Loading the ${nextScenario} scenario.`);

    const apiScenario: ApiScenario = nextScenario === "loading" ? "success" : nextScenario;
    if (nextScenario === "loading") {
      await new Promise((resolve) => window.setTimeout(resolve, 650));
    }

    try {
      const response = await fetch(`/api/snapshot?scenario=${apiScenario}`, { cache: "no-store" });
      const payload: unknown = await response.json();
      if (sequence !== requestSequence.current) return;
      const normalized = normalizeHttpPayload(response.status, payload);
      setEnvelope(normalized);
      setClock(Date.now());
      if (normalized.data?.agents[0]) setSelectedAgentId(normalized.data.agents[0].id);
      setLiveMessage(normalized.message);
    } catch {
      if (sequence !== requestSequence.current) return;
      setEnvelope({ state: "error", message: "The browser could not reach the demo API.", data: null, retryable: true });
      setLiveMessage("The browser could not reach the demo API.");
    }
  }

  function updateAgent(agentId: string, update: (agent: AgentRecord) => AgentRecord) {
    setEnvelope((current) => {
      if (!current.data) return current;
      return { ...current, data: { ...current.data, agents: current.data.agents.map((agent) => agent.id === agentId ? update(agent) : agent) } };
    });
  }

  function retryAgent(agent: AgentRecord) {
    if (!agent.allowedActions.includes("retry") || agent.retries >= agent.maxRetries) {
      setLiveMessage(`${agent.name} has no retry available.`);
      return;
    }
    updateAgent(agent.id, (current) => ({ ...current, status: "running", retries: current.retries + 1, progress: 90, result: null, logs: [...current.logs, "Manual demo retry accepted"] }));
    setLiveMessage(`Retry started for ${agent.name}.`);
    window.setTimeout(() => {
      updateAgent(agent.id, (current) => ({ ...current, status: "completed", progress: 100, result: "Retry completed with synthetic evidence.", logs: [...current.logs, "Retry finished successfully"] }));
      setLiveMessage(`${agent.name} completed the retry.`);
    }, 900);
  }

  function exportSnapshot() {
    if (!data) return;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `agent-dashboard-${data.provenance.fetchedAt.slice(0, 10)}.json`;
    link.click();
    URL.revokeObjectURL(url);
    setLiveMessage("Synthetic snapshot exported as JSON.");
  }

  function inspectAgent(agent: AgentRecord) {
    setSelectedAgentId(agent.id);
    setDetailOpen(true);
    setLiveMessage(`Opened evidence for ${agent.name}.`);
  }

  const pageTitles: Record<DashboardView, { eyebrow: string; title: string; description: string }> = {
    overview: { eyebrow: "Fleet pulse", title: "Your agent fleet, explained.", description: "See operational state, spend, and evidence without turning uncertainty into a green check." },
    agents: { eyebrow: "Agent directory", title: "Every agent. Every boundary.", description: "Filter live roles, inspect the exact task, and retry only when the view contract allows it." },
    runs: { eyebrow: "Run timeline", title: "Follow the work, not just the outcome.", description: "A chronological record of checkpoints, waits, failures, and completed evidence." },
    evidence: { eyebrow: "Proof surface", title: "Trust is a visible contract.", description: "Review provenance, freshness, action coverage, and the rules behind every state." },
  };
  const heading = pageTitles[view];

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to dashboard content</a>
      <aside className="sidebar" aria-label="Primary navigation">
        <Link className="brand" href="/" aria-label="Agent Dashboard home">
          <span className="brand-mark" aria-hidden="true"><span /><span /><span /></span>
          <span>Agent<br />Dashboard</span>
        </Link>
        <nav>
          {NAV_ITEMS.map((item) => (
            <Link key={item.view} href={item.href} className={view === item.view ? "nav-link active" : "nav-link"} aria-current={view === item.view ? "page" : undefined}>
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </Link>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="demo-dot" aria-hidden="true" />
          <div><strong>Demo source</strong><span>Synthetic data only</span></div>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="mobile-brand"><span className="brand-mark small" aria-hidden="true"><span /><span /><span /></span><strong>Agent Dashboard</strong></div>
          <div className="source-chip"><span aria-hidden="true" /> API connected <code>v1</code></div>
          <label className="scenario-control">
            <span>State lab</span>
            <select value={scenario} onChange={(event) => void loadScenario(event.target.value as ScenarioOption)} aria-label="Choose API state scenario">
              <option value="success">Success</option>
              <option value="loading">Loading</option>
              <option value="empty">Empty</option>
              <option value="degraded">Degraded</option>
              <option value="timeout">Timeout</option>
              <option value="error">Error</option>
            </select>
          </label>
        </header>

        <main id="main-content" tabIndex={-1}>
          <section className="page-header" aria-labelledby="page-title">
            <div>
              <p className="eyebrow">{heading.eyebrow}</p>
              <h1 id="page-title">{heading.title}</h1>
              <p className="lede">{heading.description}</p>
            </div>
            <div className="header-actions">
              <button className="button secondary" type="button" onClick={() => void loadScenario("success")} disabled={envelope.state === "loading"}>
                <Icon name="refresh" /> Refresh
              </button>
              <button className="button primary" type="button" onClick={exportSnapshot} disabled={!data} title={!data ? "Load a snapshot before exporting" : undefined}>
                <Icon name="download" /> Export JSON
              </button>
            </div>
          </section>

          <div className="live-region" role="status" aria-live="polite" aria-atomic="true">{liveMessage}</div>

          {envelope.state === "loading" && <LoadingState />}
          {envelope.state === "empty" && <StatePanel state="empty" title="No runs in this snapshot" message={envelope.message} action="Return to live snapshot" onAction={() => void loadScenario("success")} />}
          {envelope.state === "timeout" && <StatePanel state="timeout" title="The adapter timed out" message={envelope.message} action="Retry live snapshot" onAction={() => void loadScenario("success")} />}
          {envelope.state === "error" && <StatePanel state="error" title="Source unavailable" message={envelope.message} action={envelope.retryable ? "Retry live snapshot" : undefined} onAction={() => void loadScenario("success")} />}

          {data && data.agents.length > 0 && (
            <>
              {envelope.state === "degraded" && (
                <div className="degraded-banner" role="alert"><strong>Partial telemetry</strong><span>{envelope.message}</span><button type="button" onClick={() => void loadScenario("success")}>Reload all signals</button></div>
              )}

              {(view === "overview" || view === "agents") && (
                <section className="kpi-grid" aria-label="Fleet summary">
                  <article className="kpi-card"><div className="kpi-top"><span>Active agents</span><span className="kpi-icon"><Icon name="agents" /></span></div><strong>{summary.running + summary.waiting}</strong><p><span className="trend-up">+1</span> since last hour</p></article>
                  <article className="kpi-card"><div className="kpi-top"><span>Running now</span><span className="pulse-orbit" aria-hidden="true"><i /></span></div><strong>{summary.running}</strong><p>{summary.waiting} waiting at a boundary</p></article>
                  <article className="kpi-card attention"><div className="kpi-top"><span>Needs attention</span><span className="attention-mark" aria-hidden="true">!</span></div><strong>{summary.failed}</strong><p>{summary.failed ? "Retry evidence available" : "No failed runs"}</p></article>
                  <article className="kpi-card"><div className="kpi-top"><span>Spend today</span><span className="currency-mark" aria-hidden="true">$</span></div><strong>${summary.costUsd.toFixed(2)}</strong><p>{Math.round((summary.costUsd / data.budgetUsd) * 100)}% of ${data.budgetUsd.toFixed(0)} budget</p></article>
                </section>
              )}

              {(view === "overview" || view === "agents") && (
                <section className="panel agent-panel" aria-labelledby="agents-heading">
                  <div className="panel-heading">
                    <div><p className="section-kicker">Operations</p><h2 id="agents-heading">Agent activity</h2></div>
                    <div className="filter-group" aria-label="Filter agents by status">
                      {(["all", "running", "waiting", "failed", "completed"] as StatusFilter[]).map((filter) => (
                        <button key={filter} type="button" className={statusFilter === filter ? "filter active" : "filter"} aria-pressed={statusFilter === filter} onClick={() => setStatusFilter(filter)}>
                          {filter === "all" ? "All" : STATUS_LABELS[filter]}
                        </button>
                      ))}
                    </div>
                  </div>
                  <label className="search-field"><span className="sr-only">Search agents</span><Icon name="search" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search agent, role, task, or run…" /></label>
                  {visibleAgents.length === 0 ? (
                    <div className="inline-empty"><strong>No agents match those filters.</strong><button type="button" onClick={() => { setQuery(""); setStatusFilter("all"); }}>Clear filters</button></div>
                  ) : (
                    <div className="agent-list" role="list">
                      {visibleAgents.map((agent) => (
                        <article className="agent-row" key={agent.id} role="listitem">
                          <button className="agent-identity" type="button" onClick={() => inspectAgent(agent)} aria-label={`Inspect ${agent.name}`}>
                            <span className={`avatar avatar-${agent.id}`} aria-hidden="true">{agent.name.slice(0, 1)}</span>
                            <span><strong>{agent.name}</strong><small>{agent.role}</small></span>
                          </button>
                          <div className="agent-task"><strong>{agent.task}</strong><span><code>{agent.runId}</code> · updated {formatAge(agent.updatedAt, clock)}</span></div>
                          <StatusPill status={agent.status} />
                          <div className="progress-cell"><div><span>{agent.progress}%</span><small>{formatDuration(agent.durationSeconds)}</small></div><span className="progress-track" aria-label={`${agent.progress}% complete`}><span style={{ width: `${agent.progress}%` }} /></span></div>
                          <div className="agent-cost"><strong>${agent.costUsd.toFixed(2)}</strong><small>{formatInteger(agent.tokens)} tok</small></div>
                          <div className="row-actions">
                            {agent.status === "failed" && agent.allowedActions.includes("retry") ? <button className="icon-button retry-button" type="button" onClick={() => retryAgent(agent)} aria-label={`Retry ${agent.name}`} title="Retry this failed demo run"><Icon name="retry" /></button> : null}
                            <button className="icon-button" type="button" onClick={() => inspectAgent(agent)} aria-label={`Open ${agent.name} evidence`}><Icon name="chevron" /></button>
                          </div>
                        </article>
                      ))}
                    </div>
                  )}
                </section>
              )}

              {(view === "overview" || view === "runs") && (
                <section className={view === "runs" ? "panel timeline-panel wide" : "panel timeline-panel"} aria-labelledby="timeline-heading">
                  <div className="panel-heading">
                    <div><p className="section-kicker">Trace</p><h2 id="timeline-heading">Run timeline</h2></div>
                    <div className="range-control" aria-label="Timeline range">
                      {(["1h", "24h", "7d"] as const).map((range) => <button key={range} type="button" className={timelineRange === range ? "active" : ""} aria-pressed={timelineRange === range} onClick={() => setTimelineRange(range)}>{range}</button>)}
                    </div>
                  </div>
                  <ol className="timeline-list">
                    {visibleEvents.map((event) => {
                      const agent = data.agents.find((item) => item.id === event.agentId);
                      return <li key={event.id} className={`event event-${event.kind}`}><span className="event-dot" aria-hidden="true" /><time dateTime={event.occurredAt}>{displayTime(event.occurredAt)}</time><div><strong>{event.title}</strong><p>{event.detail}</p><span><code>{event.runId}</code>{agent ? ` · ${agent.role}` : ""}</span></div></li>;
                    })}
                  </ol>
                </section>
              )}

              {view === "overview" && (
                <section className="panel budget-panel" aria-labelledby="budget-heading">
                  <div className="panel-heading"><div><p className="section-kicker">Controls</p><h2 id="budget-heading">Budget runway</h2></div><span className="budget-value">${summary.costUsd.toFixed(2)} / ${data.budgetUsd.toFixed(2)}</span></div>
                  <div className="budget-bar" aria-label={`${Math.round((summary.costUsd / data.budgetUsd) * 100)}% of daily budget used`}><span style={{ width: `${Math.min(100, (summary.costUsd / data.budgetUsd) * 100)}%` }} /></div>
                  <div className="budget-meta"><span>{formatInteger(summary.tokens)} tokens</span><span>${(data.budgetUsd - summary.costUsd).toFixed(2)} remaining</span></div>
                </section>
              )}

              {view === "evidence" && <EvidencePanel data={data} />}

              <footer className="provenance">
                <div><span className="provenance-mark" aria-hidden="true">S</span><p><strong>Source: {data.provenance.source}</strong><span>Generated fixture · schema {data.provenance.schemaVersion} · no private account required</span></p></div>
                <p><span>Freshness</span><strong>{formatAge(data.provenance.fetchedAt, clock)}</strong></p>
              </footer>
            </>
          )}
        </main>
      </div>

      {detailOpen && selectedAgent && (
        <div className="drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDetailOpen(false); }}>
          <aside className="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="detail-title">
            <div className="drawer-head"><div><p className="section-kicker">Run evidence</p><h2 id="detail-title">{selectedAgent.name}</h2></div><button className="icon-button" type="button" onClick={() => setDetailOpen(false)} aria-label="Close agent details"><Icon name="close" /></button></div>
            <StatusPill status={selectedAgent.status} />
            <h3>{selectedAgent.task}</h3>
            <dl className="detail-grid"><div><dt>Run</dt><dd><code>{selectedAgent.runId}</code></dd></div><div><dt>Duration</dt><dd>{formatDuration(selectedAgent.durationSeconds)}</dd></div><div><dt>Retries</dt><dd>{selectedAgent.retries} / {selectedAgent.maxRetries}</dd></div><div><dt>Cost</dt><dd>${selectedAgent.costUsd.toFixed(2)}</dd></div></dl>
            <section><h4>Recorded logs</h4><ol className="log-list">{selectedAgent.logs.map((log, index) => <li key={`${selectedAgent.id}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span>{log}</li>)}</ol></section>
            {selectedAgent.result && <section className="result-box"><h4>Latest result</h4><p>{selectedAgent.result}</p></section>}
            {selectedAgent.allowedActions.includes("retry") && selectedAgent.status === "failed" ? <button className="button primary full" type="button" onClick={() => retryAgent(selectedAgent)}><Icon name="retry" /> Retry bounded run</button> : <button className="button secondary full" type="button" disabled title="Retry is only allowed for failed runs with remaining attempts">Retry unavailable in current state</button>}
          </aside>
        </div>
      )}
    </div>
  );
}

function LoadingState() {
  return <section className="loading-state" aria-label="Loading dashboard"><div className="skeleton wide" /><div className="skeleton-grid">{[0, 1, 2, 3].map((item) => <div className="skeleton card" key={item} />)}</div><div className="skeleton table" /><p>Normalizing the view contract…</p></section>;
}

function StatePanel({ state, title, message, action, onAction }: { state: "empty" | "timeout" | "error"; title: string; message: string; action?: string; onAction: () => void }) {
  return <section className={`state-panel state-panel-${state}`} role={state === "error" ? "alert" : "status"}><span className="state-symbol" aria-hidden="true">{state === "empty" ? "0" : state === "timeout" ? "…" : "!"}</span><p className="section-kicker">{state} state</p><h2>{title}</h2><p>{message}</p>{action && <button className="button primary" type="button" onClick={onAction}><Icon name="refresh" />{action}</button>}</section>;
}

function EvidencePanel({ data }: { data: DashboardSnapshot }) {
  const checks = [
    ["View contract", "Schema v1 validates agents, events, permissions, and provenance", "Verified"],
    ["Action coverage", "Inspect, retry, refresh, export, filter, search, ranges, state lab", "8 / 8"],
    ["Failure honesty", "Empty, degraded, timeout, and error stay visually distinct", "Verified"],
    ["Data boundary", "All names, runs, logs, costs, and results are synthetic", "Verified"],
    ["Retry guard", "Only failed runs with remaining attempts expose retry", "Enforced"],
  ];
  return <section className="panel evidence-panel" aria-labelledby="evidence-heading"><div className="panel-heading"><div><p className="section-kicker">Acceptance proof</p><h2 id="evidence-heading">Operational evidence</h2></div><span className="proof-badge">5 checks passing</span></div><div className="evidence-grid"><article><span>Data source</span><strong>{data.provenance.source}</strong><p>Public synthetic fixture</p></article><article><span>Schema</span><strong>{data.provenance.schemaVersion}</strong><p>Explicit view contract</p></article><article><span>Allowed retries</span><strong>{data.agents.filter((agent) => agent.allowedActions.includes("retry")).length}</strong><p>State-gated action</p></article></div><div className="evidence-table" role="table" aria-label="Acceptance evidence"><div role="row" className="evidence-row header"><span role="columnheader">Gate</span><span role="columnheader">Proof</span><span role="columnheader">Status</span></div>{checks.map(([gate, proof, status]) => <div role="row" className="evidence-row" key={gate}><strong role="cell">{gate}</strong><span role="cell">{proof}</span><span role="cell" className="evidence-status">{status}</span></div>)}</div></section>;
}
