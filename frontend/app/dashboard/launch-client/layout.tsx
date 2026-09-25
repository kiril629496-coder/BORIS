import { Shell } from "@/app/components/shell/LegacyShellAdapter";

export default function LaunchClientLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="launch-status">{children}</Shell>;
}
