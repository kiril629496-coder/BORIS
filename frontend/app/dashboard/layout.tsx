"use client";

/**
 * Общий слой для всех маршрутов /dashboard/*.
 * Раньше его не было вовсе — каждая страница проверяла токен сама.
 * Теперь одна точка: пока не проверили статус мастера, кабинет не рендерим.
 */
import React from "react";
import OnboardingGate from "../lib/useOnboardingGuard";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return <OnboardingGate>{children}</OnboardingGate>;
}
