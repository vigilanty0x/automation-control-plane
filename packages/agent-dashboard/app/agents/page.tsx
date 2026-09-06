import type { Metadata } from "next";
import { AgentDashboard } from "@/app/components/agent-dashboard";
import { demoSnapshot } from "@/lib/fixtures";

export const metadata: Metadata = { title: "Agents" };

export default function AgentsPage() {
  return <AgentDashboard initialSnapshot={demoSnapshot} view="agents" />;
}
