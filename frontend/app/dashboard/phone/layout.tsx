"use client";

import { Shell } from "@/app/components/shell/LegacyShellAdapter";

export default function PhoneLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="phone">{children}</Shell>;
}
