"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import AppShell from "@/app/components/shell/AppShell";

export default function CrmLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [tab, setTab] = useState("");

  useEffect(() => {
    const sync = () => setTab(new URLSearchParams(window.location.search).get("tab") || "");
    sync();
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, [pathname]);

  const active = pathname === "/dashboard/crm/integrations"
    ? "listings"
    : pathname === "/dashboard/crm/workspace" && tab === "tasks"
      ? "plan"
      : "sales";

  const title = active === "plan"
    ? "Задачи и план"
    : active === "listings"
      ? "Интеграции"
      : "Продажи";

  return <AppShell active={active} title={title}>{children}</AppShell>;
}
