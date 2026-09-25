"use client";

import { usePathname } from "next/navigation";
import { Shell } from "../components/shell/LegacyShellAdapter";

export default function MessagesLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const activeKey = pathname.startsWith("/messages/setup") ? "slots" : "messages";
  return <Shell activeKey={activeKey}>{children}</Shell>;
}
