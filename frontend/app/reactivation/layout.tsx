"use client";
import { Shell } from "../ui/Sidebar";
export default function ReactivationLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="reactivation">{children}</Shell>;
}
