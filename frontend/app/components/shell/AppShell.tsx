"use client";

import { useEffect, useState, ReactNode } from "react";
import Sidebar from "./Sidebar";
import TopBar from "./TopBar";
import css from "./shell.module.css";
import { getToken, openRoute, OldMode } from "../../lib/api";
import { IconChip, type IconName } from "../../ui";

import GlobalBackButton from "@/app/components/navigation/GlobalBackButton";
import ContextualBoris from "@/app/components/ContextualBoris";
import MobileDock from "./MobileDock";

/* Каркас нового интерфейса: постоянное левое меню, верхняя панель,
   рабочая область и слот правой панели. Ставится КАЖДОЙ страницей явно —
   layout на уровне /dashboard создавать нельзя, он обернёт и монолит. */

/* BORIS UI G3.1 ACTIVE ICON MAP */
const ACTIVE_ICON: Record<string, IconName> = {
  home: "home",
  "clients-control": "clients",

  scenarios: "star",
  command: "ai",

  messages: "messages",
  crm: "clients",

  listings: "list",
  plan: "tasks",
  settings: "settings",

  marketing: "ads",
  prospecting: "messages",
  acquisition: "target",
  sales: "analytics",
  "revenue-analytics": "target",
  phone: "support",

  parser: "upload",
  webdesign: "palette",
  social: "ads",

  slots: "plug",
  memory: "memory",
  "control-plane": "memory",
  aisetup: "ai",

  sitebuild: "globe",

  journal: "file",
  team: "clients",

  stats: "chart",
  integrations: "settings",
  "factory-decisions": "shield",
  "launch-status": "shield",
  reliability: "shield",
  billing: "card",
  company: "building",

  rop: "analytics",
  training: "award",
  support: "support",
  reactivation: "refresh",
  campaign: "layers",
};

type Props = {
  active?: string;
  title: string;
  subtitle?: string;
  aside?: ReactNode;
  oldTab?: string;
  oldMode?: OldMode | null;
  children: ReactNode;
  headerExtra?: ReactNode;
};

export default function AppShell({ active, title, subtitle, aside, oldTab, oldMode, children,
  headerExtra,
}: Props) {
  const [menu, setMenu] = useState(false);

  /* Токен пишется в localStorage при входе, но между router.push и монтированием
     каркаса бывает зазор — мгновенная проверка отправляла ВОШЕДШЕГО клиента
     обратно на /login и получался цикл. Даём странице ожить, проверяем один раз
     с задержкой. Настоящую невалидность токена всё равно ловит apiFetch по 401. */
  useEffect(() => {
    if (getToken()) return;
    const _authTimer = setTimeout(() => {
      if (!getToken()) openRoute("/login");
    }, 1500);
    return () => clearTimeout(_authTimer);
  }, []);

  return (
    <div className={css.shell} data-testid="app-shell" data-boris-area={active || "product"}>
      <Sidebar active={active} open={menu} onClose={() => setMenu(false)} />
      <div className={css.main}>
        <TopBar title={title} subtitle={subtitle}
          extra={
            <>
              {active && ACTIVE_ICON[active] ? (
                <span className={css.headerIcon}>
                  <IconChip
                    name={ACTIVE_ICON[active]}
                    kind="info"
                    size={36}
                  />
                </span>
              ) : null}
              {headerExtra}
            </>
          }
          onBurger={() => setMenu(!menu)} />
        <div className={css.body}>
          <div className={css.content}>{/* BORIS_GLOBAL_BACK_MOUNT_V1 */}
        <GlobalBackButton />
        {children}</div>
          {aside && <div className={css.aside}>{aside}</div>}
        </div>
        <ContextualBoris />
        <MobileDock onMenu={() => setMenu(true)} />
      </div>
    </div>
  );
}
