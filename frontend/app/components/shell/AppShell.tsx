"use client";

import { useEffect, useState, ReactNode } from "react";
import Sidebar from "./Sidebar";
import TopBar from "./TopBar";
import InterfaceSwitcher from "./InterfaceSwitcher";
import css from "./shell.module.css";
import { getToken, openRoute, OldMode } from "../../lib/api";

/* Каркас нового интерфейса: постоянное левое меню, верхняя панель,
   рабочая область и слот правой панели. Ставится КАЖДОЙ страницей явно —
   layout на уровне /dashboard создавать нельзя, он обернёт и монолит. */
type Props = {
  active?: string;
  title: string;
  subtitle?: string;
  aside?: ReactNode;
  oldTab?: string;
  oldMode?: OldMode | null;
  children: ReactNode;
};

export default function AppShell({ active, title, subtitle, aside, oldTab, oldMode, children }: Props) {
  const [menu, setMenu] = useState(false);
  const [sw, setSw] = useState(false);

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
    <div className={css.shell} data-testid="app-shell">
      <Sidebar active={active} open={menu} onClose={() => setMenu(false)} />
      <div className={css.main}>
        <TopBar title={title} subtitle={subtitle}
          onBurger={() => setMenu(!menu)} onSwitch={() => setSw(true)} />
        <div className={css.body}>
          <div className={css.content}>{children}</div>
          {aside && <div className={css.aside}>{aside}</div>}
        </div>
      </div>
      <InterfaceSwitcher open={sw} onClose={() => setSw(false)}
        tab={oldTab || "listings"} mode={oldMode || null} />
    </div>
  );
}
