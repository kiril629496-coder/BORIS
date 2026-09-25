"use client";

import { Shell } from "@/app/components/shell/LegacyShellAdapter";

export default function RevenueAnalyticsLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="revenue-analytics">{children}</Shell>;
}
