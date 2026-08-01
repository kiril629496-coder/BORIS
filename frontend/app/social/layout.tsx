"use client";

import { Shell } from "../ui/Sidebar";

export default function CabinetLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="social">{children}</Shell>;
}
