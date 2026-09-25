"use client";

/**
 * Общий слой для всех маршрутов /dashboard/*.
 * Раньше его не было вовсе — каждая страница проверяла токен сама.
 * Теперь одна точка: пока не проверили статус мастера, кабинет не рендерим.
 */
import React, { useLayoutEffect } from "react";
import OnboardingGate from "../lib/useOnboardingGuard";
import { usePathname } from "next/navigation";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  // BORIS_CANONICAL_DASHBOARD_ENTRY_LAYOUT_V2
  // Layout always mounts even when OnboardingGate blocks child rendering.
  // Only an exact bare /dashboard redirects; query-driven legacy flows stay intact.
  useLayoutEffect(() => {
    if (pathname === "/dashboard" && window.location.search === "") {
      window.location.replace("/dashboard/command-center");
    }
  }, [pathname]);

  // Public presentation pages under /dashboard must stay accessible without BORIS login.
  // IMPORTANT: only the exact showcase route is public. /dashboard/prospecting/manage
  // stays behind OnboardingGate and its backend APIs keep server-side authorization.
  if (pathname === "/dashboard/andrey-campaigns" || pathname === "/dashboard/prospecting") {
    return <>{children}</>;
  }

  // CRM workspace is a redirect-only compatibility route. Let its redirect execute first; the canonical destination remains guarded.
  if (pathname === "/dashboard/crm/workspace") return <>{children}</>;

  // OWNER_PRIVATE_CENTERS_SKIP_CLIENT_ONBOARDING_V1
  // Эти два экрана относятся к владельцу платформы, а не к клиентскому мастеру
  // первого запуска. Они сами строго проверяют /api/auth/me.platform_admin.
  // Поэтому onboarding-гейт здесь только создаёт лишнюю блокировку и не даёт
  // дополнительной безопасности.
  if (pathname === "/dashboard/clients-control" || pathname === "/dashboard/development") {
    return <>{children}</>;
  }

  return <OnboardingGate>{children}</OnboardingGate>;
}
