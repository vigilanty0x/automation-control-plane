import assert from "node:assert/strict";
import test from "node:test";

async function loadWorker() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("api-test", `${process.pid}-${Date.now()}`);
  return (await import(workerUrl.href)).default;
}

function environment() {
  return { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } };
}

const context = { waitUntil() {}, passThroughOnException() {} };

test("serves a fresh synthetic snapshot with provenance headers", async () => {
  const worker = await loadWorker();
  const response = await worker.fetch(new Request("http://localhost/api/snapshot?scenario=success"), environment(), context);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("x-data-provenance"), "synthetic-demo-api");
  const body = await response.json();
  assert.equal(body.provenance.synthetic, true);
  assert.equal(body.agents.length, 4);
});

test("exposes explicit degraded, timeout and error statuses", async () => {
  const worker = await loadWorker();
  const degraded = await worker.fetch(new Request("http://localhost/api/snapshot?scenario=degraded"), environment(), context);
  const timeout = await worker.fetch(new Request("http://localhost/api/snapshot?scenario=timeout"), environment(), context);
  const error = await worker.fetch(new Request("http://localhost/api/snapshot?scenario=error"), environment(), context);
  assert.equal(degraded.status, 206);
  assert.equal(timeout.status, 504);
  assert.equal(error.status, 503);
});

test("rejects unknown scenarios", async () => {
  const worker = await loadWorker();
  const response = await worker.fetch(new Request("http://localhost/api/snapshot?scenario=secret"), environment(), context);
  assert.equal(response.status, 400);
  assert.match((await response.json()).message, /scenario must be one of/);
});
