"use client";

import { Shell } from "../ui/Sidebar";

export default function TrainingLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="memory">{children}</Shell>;
}
