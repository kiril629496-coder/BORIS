import { Shell } from "@/app/components/shell/LegacyShellAdapter";

export default function ScenariosLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="scenarios">{children}</Shell>;
}
