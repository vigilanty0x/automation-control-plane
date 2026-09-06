import type { ApiScenario, DashboardSnapshot, NormalizedView } from "@/lib/contracts";

export function isApiScenario(value: unknown): value is ApiScenario;
export function buildScenarioPayload(
  snapshot: DashboardSnapshot,
  scenario: ApiScenario,
  now?: Date,
): { status: number; body: DashboardSnapshot | Record<string, unknown> };
export function normalizeHttpPayload(status: number, payload: unknown): NormalizedView;
export function summarizeAgents(agents: DashboardSnapshot["agents"]): {
  running: number;
  waiting: number;
  failed: number;
  completed: number;
  costUsd: number;
  tokens: number;
};
export function filterAgents(
  agents: DashboardSnapshot["agents"],
  status: "all" | DashboardSnapshot["agents"][number]["status"],
  query: string,
): DashboardSnapshot["agents"];
export function formatAge(iso: string, nowMs?: number): string;
