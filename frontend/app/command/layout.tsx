import { Shell } from "../components/shell/LegacyShellAdapter";

export default function CommandLayout({ children }: { children: React.ReactNode }) {
  return <Shell activeKey="command">{children}</Shell>;
}
