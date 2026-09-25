"use client";

import { Shell } from "@/app/components/shell/LegacyShellAdapter";

export default function ConnectAvitoLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="accounts">{children}</Shell>;
}
