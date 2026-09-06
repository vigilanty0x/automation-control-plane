import { NextResponse } from "next/server";
import { buildScenarioPayload, isApiScenario } from "@/lib/adapter.mjs";
import { demoSnapshot } from "@/lib/fixtures";

export const dynamic = "force-dynamic";

export function GET(request: Request) {
  const requested = new URL(request.url).searchParams.get("scenario") ?? "success";
  if (!isApiScenario(requested)) {
    return NextResponse.json(
      {
        error: "invalid_scenario",
        message: "scenario must be one of: success, empty, degraded, timeout, error",
      },
      { status: 400 },
    );
  }

  const result = buildScenarioPayload(demoSnapshot, requested);
  return NextResponse.json(result.body, {
    status: result.status,
    headers: {
      "Cache-Control": "no-store",
      "X-Data-Provenance": "synthetic-demo-api",
    },
  });
}
