"use client";

import { Shell } from "../components/shell/LegacyShellAdapter";

export default function RopLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="sales">{children}</Shell>;
}
