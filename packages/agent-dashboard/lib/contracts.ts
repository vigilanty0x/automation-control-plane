export type AgentStatus = "running" | "waiting" | "failed" | "completed";
export type RunStatus = AgentStatus | "queued";
export type ApiScenario = "success" | "empty" | "degraded" | "timeout" | "error";
export type ViewState = "ready" | "empty" | "degraded" | "timeout" | "error" | "loading";

export interface DataProvenance {
  source: "synthetic-demo-api";
  fetchedAt: string;
  generatedBy: string;
  schemaVersion: "1.0";
  synthetic: true;
}

export interface AgentRecord {
  id: string;
  name: string;
  role: string;
  status: AgentStatus;
  runId: string;
  task: string;
  startedAt: string | null;
  updatedAt: string;
  durationSeconds: number;
  costUsd: number;
  tokens: number;
  progress: number;
  retries: number;
  maxRetries: number;
  result: string | null;
  logs: string[];
  allowedActions: Array<"inspect" | "retry" | "export">;
}

export interface TimelineEvent {
  id: string;
  runId: string;
  agentId: string;
  occurredAt: string;
  kind: "started" | "checkpoint" | "waiting" | "failed" | "retried" | "completed";
  title: string;
  detail: string;
}

export interface DashboardSnapshot {
  provenance: DataProvenance;
  budgetUsd: number;
  agents: AgentRecord[];
  events: TimelineEvent[];
}

export interface NormalizedView {
  state: ViewState;
  message: string;
  data: DashboardSnapshot | null;
  retryable: boolean;
}
