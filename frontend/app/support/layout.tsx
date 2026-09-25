"use client";

import { Shell } from "../components/shell/LegacyShellAdapter";

export default function CabinetLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="support">{children}</Shell>;
}
