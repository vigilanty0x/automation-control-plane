import type { Metadata } from "next";
import { AgentDashboard } from "@/app/components/agent-dashboard";
import { demoSnapshot } from "@/lib/fixtures";

export const metadata: Metadata = { title: "Evidence" };

export default function EvidencePage() {
  return <AgentDashboard initialSnapshot={demoSnapshot} view="evidence" />;
}
