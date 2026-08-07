"use client";

import { useEffect, useState } from "react";
import { Mascot } from "../Mascot";
import css from "./shell.module.css";
import { apiGet, getAccount, setAccount, navigateToOld, openRoute, OldMode } from "../../lib/api";

/* Пункт меню: либо маршрут нового интерфейса, либо вкладка старого кабинета
   (при необходимости с булевым режимом — их открывает мост навигации). */
type Item = { key: string; icon: string; label: string; route?: string; tab?: string;
              mode?: OldMode; testid?: string };

const GROUPS: { title: string; items: Item[] }[] = [
  {
    title: "Управление",
    items: [
      { key: "director",   icon: "📊", label: "Директор",          tab: "stats",     mode: "director" },
      { key: "advisor",    icon: "🎯", label: "Советник",          tab: "marketing", mode: "advisor" },
      { key: "billing",    icon: "💳", label: "Тарифы и финансы",  tab: "billing" },
      { key: "wallet",     icon: "👛", label: "Кошелёк",           tab: "billing",   mode: "wallet" },
      { key: "requisites", icon: "🧾", label: "Реквизиты",         tab: "billing",   mode: "requisites" },
      { key: "accounts",   icon: "🏬", label: "Мои аккаунты",      route: "/agency" },
    ],
  },
  {
    title: "Работа",
    items: [
      { key: "home",     icon: "🏠", label: "Главная",       route: "/dashboard/home" },
      { key: "scenarios", icon: "⭐", label: "Бизнес-сценарии", route: "/dashboard/scenarios" },
      // nav-cabinet: любой пункт с tab уходит через navigateToOld на /dashboard,
      // и «Объявления» — первый рабочий раздел кабинета. Этот идентификатор
      // ждёт QA как доказательство, что переход в кабинет живой.
      { key: "listings", icon: "📋", label: "Объявления",    tab: "listings",
        testid: "nav-cabinet" },
      { key: "plan",     icon: "✅", label: "Задачи и план", tab: "plan" },
      { key: "settings", icon: "🛡", label: "Режим работы",  tab: "settings" },
    ],
  },
  {
    title: "Рост",
    items: [
      { key: "marketing", icon: "💼", label: "Маркетинг", tab: "marketing" },
      { key: "content",   icon: "🎨", label: "Контент",   tab: "webdesign" },
      { key: "sales",     icon: "💰", label: "Продажи",   tab: "sales" },
      { key: "analytics", icon: "📈", label: "Аналитика", tab: "stats", mode: "analytics" },
    ],
  },
];

type Props = { active?: string; open: boolean; onClose: () => void };

export default function Sidebar({ active, open, onClose }: Props) {
  const [min, setMin] = useState(false);
  const [accounts, setAccounts] = useState<any[]>([]);
  const [acc, setAcc] = useState("");

  useEffect(() => {
    setAcc(getAccount());
    setMin(typeof window !== "undefined" && localStorage.getItem("boris_sideMin") === "1");
    apiGet("/api/accounts/list").then((d: any) => {
      const list = d && d.accounts ? d.accounts : Array.isArray(d) ? d : [];
      setAccounts(list);
      if (!getAccount() && list.length) {
        const first = list[0].account_id || list[0].id;
        setAccount(first); setAcc(first);
      }
    });
  }, []);

  const toggle = () => {
    const v = !min; setMin(v);
    try { localStorage.setItem("boris_sideMin", v ? "1" : "0"); } catch {}
  };

  const go = (it: Item) => {
    onClose();
    if (it.route) openRoute(it.route);
    else if (it.tab) navigateToOld(it.tab, it.mode || null);
  };

  const cur = accounts.find((a: any) => (a.account_id || a.id) === acc);
  // Роль в ТЕКУЩЕМ аккаунте: сотруднику незачем видеть кошелёк,
  // тарифы, реквизиты и список аккаунтов владельца.
  const myRole = (cur && (cur as any).role) || "owner";
  const OWNER_ONLY = ["billing", "wallet", "requisites", "accounts"];
  const groups = GROUPS
    .map((g) => ({ ...g, items: g.items.filter(
      (it) => myRole === "owner" || OWNER_ONLY.indexOf(it.key) === -1) }))
    .filter((g) => g.items.length > 0);

  return (
    <aside className={css.side + " " + (min ? css.sideMin : "") + " " + (open ? css.sideOpen : "")}>
      <div className={css.brand}>
        <Mascot size={min ? 30 : 34} interactive={false} />
        {!min && (
          <div className={css.brandText}>
            <div className={css.brandName}>BORIS</div>
            <div className={css.brandSub}>Цифровой коммерческий директор</div>
          </div>
        )}
        <button className={css.collapse} onClick={toggle}
          aria-label={min ? "Развернуть меню" : "Свернуть меню"}>{min ? "»" : "«"}</button>
      </div>

      {!min && accounts.length === 0 && (
        <div data-testid="no-account-banner"
             style={{ margin: "10px 12px", padding: "12px 14px", borderRadius: "12px",
                      background: "#FEF6E7", border: "1px solid #F5D9A8" }}>
          <div style={{ fontSize: "13px", fontWeight: 700, color: "#B54708", marginBottom: "4px" }}>
            Аккаунт Авито не подключён
          </div>
          <div style={{ fontSize: "12px", color: "#7A5A20", lineHeight: 1.45, marginBottom: "10px" }}>
            Пока он не подключён, большинство разделов будут пустыми — это не ошибка.
          </div>
          <button onClick={() => openRoute("/dashboard?connect=avito")}
                  style={{ background: "#B54708", color: "#FFFFFF", border: "none",
                           borderRadius: "8px", padding: "7px 12px", fontSize: "12px",
                           fontWeight: 700, cursor: "pointer", width: "100%" }}>
            Подключить аккаунт
          </button>
        </div>
      )}
      {!min && (
        <div className={css.company}>
          <div className={css.companyBox}>
            <div className={css.companyName}>{(cur && (cur.name || cur.account_id)) || "Аккаунт не выбран"}</div>
            <div className={css.online}><span className={css.dot} />Онлайн</div>
            {accounts.length > 1 && (
              <select className={css.select} value={acc} aria-label="Выбор аккаунта"
                onChange={(e: any) => { setAccount(e.target.value); setAcc(e.target.value); window.location.reload(); }}>
                {accounts.map((a: any) => {
                  const id = a.account_id || a.id;
                  return <option key={id} value={id}>{a.name || id}</option>;
                })}
              </select>
            )}
          </div>
        </div>
      )}

      <nav className={css.nav}>
        {groups.map((g) => (
          <div className={css.group} key={g.title}>
            {!min && <div className={css.groupTitle}>{g.title}</div>}
            {g.items.map((it) => (
              <button key={it.key} title={min ? it.label : undefined}
                className={css.item + " " + (active === it.key ? css.itemActive : "")}
                data-testid={it.testid || ("nav-" + it.key)}
                onClick={() => go(it)}>
                <span className={css.ico}>{it.icon}</span>
                {!min && <span className={css.label}>{it.label}</span>}
              </button>
            ))}
          </div>
        ))}
      </nav>
    </aside>
  );
}
