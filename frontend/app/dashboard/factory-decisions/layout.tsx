import { Shell } from "@/app/components/shell/LegacyShellAdapter";

export default function DecisionsLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="factory-decisions">{children}</Shell>;
}
