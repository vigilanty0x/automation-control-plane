import assert from "node:assert/strict";
import test from "node:test";
import { buildScenarioPayload, filterAgents, formatAge, isApiScenario, normalizeHttpPayload, summarizeAgents } from "../lib/adapter.mjs";

const snapshot = {
  provenance: { source: "synthetic-demo-api", fetchedAt: "2026-01-01T00:00:00.000Z", generatedBy: "test", schemaVersion: "1.0", synthetic: true },
  budgetUsd: 10,
  agents: [
    { id: "a", name: "Atlas", role: "Analyst", task: "Map modules", runId: "run-a", status: "running", costUsd: 1.2, tokens: 100 },
    { id: "b", name: "Sable", role: "Reviewer", task: "Check release", runId: "run-b", status: "failed", costUsd: 0.8, tokens: 50 },
  ],
  events: [],
};

test("accepts only bounded API scenarios", () => {
  assert.equal(isApiScenario("success"), true);
  assert.equal(isApiScenario("timeout"), true);
  assert.equal(isApiScenario("private"), false);
});

test("builds success, empty, degraded, timeout and error responses", () => {
  const now = new Date("2026-08-15T12:00:00.000Z");
  assert.equal(buildScenarioPayload(snapshot, "success", now).status, 200);
  assert.deepEqual(buildScenarioPayload(snapshot, "empty", now).body.agents, []);
  assert.equal(buildScenarioPayload(snapshot, "degraded", now).status, 206);
  assert.equal(buildScenarioPayload(snapshot, "timeout", now).status, 504);
  assert.equal(buildScenarioPayload(snapshot, "error", now).status, 503);
});

test("normalizes every transport state without false success", () => {
  assert.equal(normalizeHttpPayload(200, snapshot).state, "ready");
  assert.equal(normalizeHttpPayload(200, { ...snapshot, agents: [] }).state, "empty");
  assert.equal(normalizeHttpPayload(206, { ...snapshot, warnings: ["late"] }).state, "degraded");
  assert.equal(normalizeHttpPayload(504, { message: "late" }).state, "timeout");
  assert.equal(normalizeHttpPayload(503, { message: "down" }).state, "error");
  assert.equal(normalizeHttpPayload(200, { agents: [] }).state, "error");
});

test("summarizes fleet totals deterministically", () => {
  assert.deepEqual(summarizeAgents(snapshot.agents), { running: 1, waiting: 0, failed: 1, completed: 0, costUsd: 2, tokens: 150 });
});

test("filters by status and searchable fields", () => {
  assert.deepEqual(filterAgents(snapshot.agents, "failed", "").map((agent) => agent.id), ["b"]);
  assert.deepEqual(filterAgents(snapshot.agents, "all", "modules").map((agent) => agent.id), ["a"]);
  assert.deepEqual(filterAgents(snapshot.agents, "running", "sable"), []);
});

test("formats freshness at stable boundaries", () => {
  const start = new Date("2026-08-15T12:00:00.000Z").getTime();
  assert.equal(formatAge("2026-08-15T11:59:42.000Z", start), "18s ago");
  assert.equal(formatAge("2026-08-15T11:55:00.000Z", start), "5m ago");
  assert.equal(formatAge("2026-08-15T09:00:00.000Z", start), "3h ago");
});
