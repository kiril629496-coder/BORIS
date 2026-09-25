"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon, type IconName } from "../../ui";
import css from "./shell.module.css";

type DockItem = { href: string; label: string; icon: IconName; match: (path: string) => boolean };

const ITEMS: DockItem[] = [
  { href: "/dashboard/command-center", label: "Главная", icon: "clients", match: p => p === "/dashboard/command-center" || p === "/dashboard/home" },
  { href: "/messages", label: "Сообщения", icon: "messages", match: p => p === "/messages" || p.startsWith("/messages/") || p.startsWith("/reactivation") },
  { href: "/command", label: "BORIS", icon: "ai", match: p => p === "/command" || p.startsWith("/command/") },
  { href: "/dashboard/campaign", label: "Объявления", icon: "list", match: p => p === "/dashboard/campaign" || p.startsWith("/dashboard/campaign/") },
];

export default function MobileDock({ onMenu }: { onMenu: () => void }) {
  const pathname = usePathname();
  return (
    <nav className={css.mobileDock} aria-label="Быстрая навигация">
      {ITEMS.map(item => {
        const active = item.match(pathname);
        return (
          <Link key={item.href} href={item.href} data-testid={item.href === "/dashboard/command-center" ? "nav-home-mobile" : undefined} className={[css.mobileDockItem, active ? css.mobileDockItemActive : ""].filter(Boolean).join(" ")} aria-current={active ? "page" : undefined}>
            <span className={css.mobileDockIcon}><Icon name={item.icon} size={20} /></span>
            <span>{item.label}</span>
          </Link>
        );
      })}
      <button type="button" className={css.mobileDockItem} onClick={onMenu} aria-label="Открыть все разделы">
        <span className={css.mobileDockIcon} aria-hidden="true">•••</span>
        <span>Ещё</span>
      </button>
    </nav>
  );
}
