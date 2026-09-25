import { Shell } from "@/app/components/shell/LegacyShellAdapter";

export default function LaunchStatusLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="launch-status">{children}</Shell>;
}
