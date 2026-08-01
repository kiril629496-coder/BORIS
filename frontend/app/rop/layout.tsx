"use client";

import { Shell } from "../ui/Sidebar";

export default function RopLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="sales">{children}</Shell>;
}
