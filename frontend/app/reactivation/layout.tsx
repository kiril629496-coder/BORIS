"use client";
import { Shell } from "../components/shell/LegacyShellAdapter";
export default function ReactivationLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="reactivation">{children}</Shell>;
}
