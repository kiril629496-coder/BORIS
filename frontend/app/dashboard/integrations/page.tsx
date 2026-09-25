import { redirect } from "next/navigation";

/** Legacy navigation compatibility: integrations live in the canonical CRM workspace. */
export default function DashboardIntegrationsRedirect() {
  redirect("/dashboard/crm/integrations");
}
