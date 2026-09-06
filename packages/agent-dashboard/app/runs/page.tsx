import type { Metadata } from "next";
import { AgentDashboard } from "@/app/components/agent-dashboard";
import { demoSnapshot } from "@/lib/fixtures";

export const metadata: Metadata = { title: "Run timeline" };

export default function RunsPage() {
  return <AgentDashboard initialSnapshot={demoSnapshot} view="runs" />;
}
