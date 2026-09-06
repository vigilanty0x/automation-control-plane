import { AgentDashboard } from "@/app/components/agent-dashboard";
import { demoSnapshot } from "@/lib/fixtures";

export default function Home() {
  return <AgentDashboard initialSnapshot={demoSnapshot} view="overview" />;
}
