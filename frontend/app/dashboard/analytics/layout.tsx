"use client";

import { Shell } from "../../components/shell/LegacyShellAdapter";

export default function AnalyticsLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="stats">{children}</Shell>;
}
