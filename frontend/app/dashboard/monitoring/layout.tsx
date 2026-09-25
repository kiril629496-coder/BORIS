import { Shell } from "@/app/components/shell/LegacyShellAdapter";

export default function MonitoringLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="stats">{children}</Shell>;
}
