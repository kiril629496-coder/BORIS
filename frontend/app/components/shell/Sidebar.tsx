"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import css from "./shell.module.css";
import { Icon, type IconName } from "../../ui";
import { NAVIGATION } from "../../ui/navigation";
import { useAccounts } from "../../lib/AccountContext";
import { apiGet } from "../../lib/api";

type SessionUser = { role?: string; id?: number; email?: string; user?: { role?: string; id?: number; email?: string } };

type Props = { active?: string; open?: boolean; onClose?: () => void; onSelect?: (key: string) => void; };

const OWNER_ONLY_NAV_KEYS = new Set(["clients-control", "journal", "ux-tester", "reliability", "audit-growth"]);

function isActive(pathname: string, itemKey: string, href?: string, explicitActive?: string) {
  if (explicitActive) return explicitActive === itemKey;

  // Simplified business parents must stay highlighted while the user works
  // inside nested tools that no longer have their own top-level menu item.
  if (itemKey === "sales") {
    if (pathname === "/dashboard/crm/integrations" || pathname.startsWith("/dashboard/crm/integrations/")) return false;
    return pathname === "/rop" || pathname.startsWith("/rop/") || pathname === "/dashboard/crm" || pathname.startsWith("/dashboard/crm/");
  }
  if (itemKey === "messages" && (pathname === "/reactivation" || pathname.startsWith("/reactivation/") || pathname === "/messages" || pathname.startsWith("/messages/"))) return true;
  if (itemKey === "listings" && (pathname === "/dashboard/crm/integrations" || pathname.startsWith("/dashboard/crm/integrations/"))) return true;

  if (!href) return false;
  const hrefPath = href.split("?")[0];
  return pathname === hrefPath || pathname.startsWith(`${hrefPath}/`);
}

export default function Sidebar({ active, open = false, onClose, onSelect }: Props) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [badges, setBadges] = useState<Record<string, number>>({});
  const [platformOwner, setPlatformOwner] = useState(false);
  const navRef = useRef<HTMLElement | null>(null);
  const { accounts, selectedAccountId, selectedAccount, setSelectedAccount } = useAccounts();

  useEffect(() => {
    try { setCollapsed(sessionStorage.getItem("boris_sidebar_collapsed") === "1"); } catch {}
  }, []);

  useEffect(() => {
    let alive = true;
    // FRONTEND_OWNER_MENU_FAST_PATH_V1:
    // login already persists the authenticated email. Reuse it immediately so
    // owner-only links do not disappear for a frame on every AppShell remount.
    // Server /api/auth/me remains authoritative and corrects the value below.
    try {
      const savedEmail = String(localStorage.getItem("boris_user_email") || "").trim().toLowerCase();
      if (savedEmail === "ostapenko-kirill-86@yandex.ru") setPlatformOwner(true);
    } catch {}
    apiGet("/api/auth/me").then((u: SessionUser) => {
      const user = u?.user || u;
      const uid = Number(user?.id || 0);
      const email = String(user?.email || "").trim().toLowerCase();
      if (alive) setPlatformOwner(uid === 2 && email === "ostapenko-kirill-86@yandex.ru");
    }).catch(() => { if (alive) setPlatformOwner(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    async function loadBadges() {
      const next: Record<string, number> = {};
      const requests: Promise<void>[] = [
        apiGet("/api/factory/confirmations").then((d: any) => {
          const n = Array.isArray(d?.items) ? d.items.filter((x: any) => x?.status === "pending").length : 0;
          if (n > 0) next["factory-decisions"] = n;
        }).catch(() => {}),
        apiGet("/api/crm/today").then((d: any) => {
          const n = Number(d?.["всего"] || 0);
          if (n > 0) next.plan = n;
        }).catch(() => {}),
      ];
      if (selectedAccountId) {
        requests.push(apiGet(`/api/chat/notifications?account_id=${encodeURIComponent(selectedAccountId)}&unread_only=true`).then((d: any) => {
          const n = Array.isArray(d?.notifications) ? d.notifications.length : 0;
          if (n > 0) next.messages = n;
        }).catch(() => {}));
      }
      await Promise.all(requests);
      if (alive) setBadges(next);
    }
    loadBadges();
    const timer = window.setInterval(loadBadges, 60000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [selectedAccountId]);

  useEffect(() => {
    try {
      const saved = Number(sessionStorage.getItem("boris_sidebar_scroll") || 0);
      if (navRef.current && Number.isFinite(saved)) navRef.current.scrollTop = saved;
    } catch {}
  }, [pathname]);

  const toggleCollapsed = () => setCollapsed((value) => {
    const next = !value;
    try { sessionStorage.setItem("boris_sidebar_collapsed", next ? "1" : "0"); } catch {}
    return next;
  });
  const accountName = selectedAccount?.name || "Текущий аккаунт";
  const accountRole = selectedAccount?.role === "owner" ? "Руководитель"
    : selectedAccount?.role === "employee" ? "Сотрудник"
    : selectedAccount?.role === "manager" ? "Менеджер"
    : "Аккаунт";
  const initial = accountName.trim().charAt(0).toUpperCase() || "B";

  return <>
    <aside className={[css.side, collapsed ? css.sideMin : "", open ? css.sideOpen : ""].filter(Boolean).join(" ")} data-testid="boris-sidebar">
      <div className={css.brand}>
        <div className={css.brandMark} aria-hidden="true">B</div>
        <div className={css.brandText}><div className={css.brandName}>БОРИС</div><div className={css.brandSub}>Ваш виртуальный отдел продаж и маркетинга</div></div>
        <button type="button" className={css.collapse} onClick={toggleCollapsed} aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"}>{collapsed ? "›" : "‹"}</button>
        {onClose && <button type="button" className={css.mobileClose} onClick={onClose} aria-label="Закрыть меню">×</button>}
      </div>
      <nav ref={navRef} className={css.nav} aria-label="Основная навигация" onScroll={(e) => { try { sessionStorage.setItem("boris_sidebar_scroll", String(e.currentTarget.scrollTop)); } catch {} }}>
        {NAVIGATION.map(group => <div className={css.group} key={group.group}>
          <div className={css.groupTitle}>{group.group}</div>
          {group.items.filter(item => platformOwner || !OWNER_ONLY_NAV_KEYS.has(item.key)).map(item => {
            const selected = isActive(pathname, item.key, item.href, active);
            const tone = item.tone || "blue";
            const badge = badges[item.key] || 0;
            const badgeTone = item.key === "factory-decisions" ? "critical" : item.key === "plan" ? "attention" : "info";
            const content = <><span className={css.ico} data-tone={tone}><Icon name={item.icon as IconName} size={18} /></span><span className={css.label}>{item.label}</span>{badge > 0 ? <span className={css.navBadge} data-badge-tone={badgeTone} aria-label={`${badge} новых`}>{badge > 99 ? "99+" : badge}</span> : null}</>;
            const className=[css.item, selected ? css.itemActive : ""].filter(Boolean).join(" ");
            if (onSelect) return <button type="button" key={item.key} className={className} title={collapsed ? item.label : undefined} onClick={() => { onSelect(item.key); onClose?.(); }}>{content}</button>;
            return <Link key={item.key} href={item.href} className={className} title={collapsed ? item.label : undefined} aria-current={selected ? "page" : undefined} data-testid={item.key === "home" ? "nav-home" : undefined} aria-label={item.key === "home" ? "Главная — клиенты" : undefined} onClick={() => onClose?.()}>{item.key === "home" ? <><span className={css.ico} data-tone={tone}><Icon name={item.icon as IconName} size={18} /></span><span className={css.label}>Главная</span>{badge > 0 ? <span className={css.navBadge} data-badge-tone={badgeTone} aria-label={`${badge} новых`}>{badge > 99 ? "99+" : badge}</span> : null}</> : content}</Link>;
          })}
        </div>)}
      </nav>
      <div className={css.accountArea}>
        {accountOpen && accounts.length > 1 ? <div className={css.accountMenu} role="menu" aria-label="Выбор аккаунта">
          {accounts.map((account) => <button type="button" role="menuitemradio" aria-checked={account.account_id === selectedAccountId} key={account.account_id} className={css.accountMenuItem} onClick={() => { setSelectedAccount(account.account_id); setAccountOpen(false); }}>
            <span className={css.accountMiniAvatar}>{(account.name || account.account_id).trim().charAt(0).toUpperCase()}</span>
            <span>{account.name || account.account_id}</span>
            {account.account_id === selectedAccountId ? <span className={css.accountCheck}>✓</span> : null}
          </button>)}
        </div> : null}
        <button type="button" className={css.accountFooter} title={collapsed ? accountName : undefined} aria-haspopup={accounts.length > 1 ? "menu" : undefined} aria-expanded={accounts.length > 1 ? accountOpen : undefined} onClick={() => accounts.length > 1 && setAccountOpen(v => !v)}>
          <span className={css.accountAvatar}>{initial}</span>
          <span className={css.accountMeta}><strong>{accountName}</strong><small>{accountRole}</small></span>
          {accounts.length > 1 ? <span className={[css.accountChevron, accountOpen ? css.accountChevronOpen : ""].filter(Boolean).join(" ")} aria-hidden="true">⌄</span> : null}
        </button>
      </div>
    </aside>
    {open && <button type="button" className={css.mobileBackdrop} aria-label="Закрыть меню" onClick={onClose} />}
  </>;
}
