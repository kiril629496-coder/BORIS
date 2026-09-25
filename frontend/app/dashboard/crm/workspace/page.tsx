import { redirect } from "next/navigation";

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function CRMWorkspaceCompatibility({ searchParams }: Props) {
  const params = await searchParams;
  const rawTab = Array.isArray(params.tab) ? params.tab[0] : params.tab;
  const accountId = Array.isArray(params.account_id) ? params.account_id[0] : params.account_id;
  if (rawTab === "reactivation") {
    redirect(accountId ? `/reactivation?account_id=${encodeURIComponent(accountId)}` : "/reactivation");
  }
  const map: Record<string,string> = {
    clients:"contacts",
    deals:"kanban",
    tasks:"tasks",
    calendar:"calendar",
    pipeline:"kanban",
  };
  const tab = map[String(rawTab || "").toLowerCase()] || "today";
  const qs = new URLSearchParams();
  if (tab !== "today") qs.set("tab", tab);
  if (accountId) qs.set("account_id", accountId);
  redirect(`/dashboard/crm${qs.toString() ? `?${qs.toString()}` : ""}`);
}
