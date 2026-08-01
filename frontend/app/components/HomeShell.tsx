"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./HomeShell.module.css";
import { apiGet, getAccount, setAccount, getToken, openTab, openRoute } from "../lib/api";

/* Меню повторяет разделы старого кабинета ОДИН В ОДИН - те же ключи activeTab,
   те же названия и иконки. Ничего не переименовано и ничего не потеряно.
   Переход в старый кабинет идёт штатно: ключ в localStorage + переход на /dashboard. */
const GROUPS: any[] = [
  {
    label: "Работа",
    items: [
      { key: "listings", icon: "📋", label: "Объявления", grad: "linear-gradient(135deg,#4C8DFF,#2F6FED)" },
      { key: "templates", icon: "📝", label: "Шаблоны объявлений", grad: "linear-gradient(135deg,#4C8DFF,#2F6FED)" },
      { key: "plan", icon: "✅", label: "Задачи и план", grad: "linear-gradient(135deg,#3DBE93,#12805C)" },
      { key: "settings", icon: "🛡", label: "Режим работы", grad: "linear-gradient(135deg,#7C5CFC,#5B3FD9)" },
    ],
  },
  {
    label: "Рост",
    items: [
      { key: "marketing", icon: "💼", label: "Маркетинг", grad: "linear-gradient(135deg,#F7A440,#E8850B)" },
      { key: "sales", icon: "💰", label: "Продажи", grad: "linear-gradient(135deg,#3DBE93,#0F9A6E)" },
      { key: "stats", icon: "📊", label: "Статистика", grad: "linear-gradient(135deg,#98A2B3,#667085)" },
    ],
  },
  {
    label: "Контент",
    items: [
      { key: "parser", icon: "📤", label: "Выгрузка с сайта", grad: "linear-gradient(135deg,#7C5CFC,#5B3FD9)" },
      { key: "webdesign", icon: "🎨", label: "Баннеры и картинки", grad: "linear-gradient(135deg,#F06AA8,#D93D86)" },
      { key: "social", icon: "📣", label: "Социальные сети", grad: "linear-gradient(135deg,#7C5CFC,#6344E8)", href: "/social" },
      { key: "sitebuild", icon: "🌐", label: "Создание сайтов", grad: "linear-gradient(135deg,#4C8DFF,#2F6FED)" },
    ],
  },
  {
    label: "Система",
    items: [
      { key: "billing", icon: "💳", label: "Лимиты подписки", grad: "linear-gradient(135deg,#3DBE93,#12805C)" },
      { key: "company", icon: "🏢", label: "О компании", grad: "linear-gradient(135deg,#98A2B3,#667085)" },
      { key: "agency", icon: "🏬", label: "Мои аккаунты", grad: "linear-gradient(135deg,#4C8DFF,#2F6FED)", href: "/agency" },
      { key: "support", icon: "💬", label: "Поддержка", grad: "linear-gradient(135deg,#F7A440,#E8850B)", href: "/support" },
    ],
  },
];

type Props = {
  active: "home" | "scenarios";
  account: string;
  onAccountChange: (id: string) => void;
  children: any;
};

export default function HomeShell({ active, account, onAccountChange, children }: Props) {
  const [accounts, setAccounts] = useState<any[]>([]);
  const [open, setOpen] = useState<string>("");
  const [navOpen, setNavOpen] = useState(false);
  const box = useRef<any>(null);

  useEffect(() => {
    if (!getToken()) {
      openRoute("/login");
      return;
    }
    apiGet("/api/accounts/list").then((d: any) => {
      // noAccountsRedirect: у клиента БЕЗ аккаунтов визард живёт в монолите и
      // поднимается там сам (page.tsx: setShowAddForm(true) + setStep(0) при
      // пустом списке). Уводим туда СРАЗУ, чтобы новичок не упирался в заглушку.
      // Редирект только при ПОЛОЖИТЕЛЬНОМ ответе "аккаунтов ноль": при ошибке
      // сети или неожиданном формате остаёмся на месте, иначе получим швыряние
      // между страницами на каждом сбое.
      const okShape = Array.isArray(d) || (d && Array.isArray(d.accounts));
      const list = d && d.accounts ? d.accounts : Array.isArray(d) ? d : [];
      if (okShape && list.length === 0) {
        openRoute("/dashboard");
        return;
      }
      setAccounts(list);
      if (!getAccount() && list.length > 0) {
        const first = list[0].id || list[0].account_id;
        setAccount(first);
        onAccountChange(first);
      }
    });
  }, []);

  useEffect(() => {
    const off = (e: any) => {
      if (box.current && !box.current.contains(e.target)) setOpen("");
    };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, []);

  const go = (it: any) => {
    setOpen("");
    setNavOpen(false);
    if (it.href) openRoute(it.href);
    else openTab(it.key);
  };

  return (
    <div className={styles.wrap} data-testid="home-shell">
      <div className={styles.top} ref={box}>
        <span className={styles.brand}>БОРИС</span>

        <button className={styles.burger} onClick={() => setNavOpen(!navOpen)}>☰ Меню</button>

        <div className={styles.nav + " " + (navOpen ? styles.navOpen : "")}>
          <button
            className={styles.navBtn + " " + (active === "home" ? styles.navActive : "")}
            onClick={() => openRoute("/dashboard/home")}
            data-testid="nav-home"
          >
            Главная
          </button>
          <button
            className={styles.navBtn + " " + (active === "scenarios" ? styles.navActive : "")}
            onClick={() => openRoute("/dashboard/scenarios")}
            data-testid="nav-scenarios"
          >
            Сценарии
          </button>

          {GROUPS.map((g: any) => (
            <div className={styles.drop} key={g.label}>
              <button
                className={styles.navBtn}
                onClick={() => setOpen(open === g.label ? "" : g.label)}
              >
                {g.label} <span className={styles.caret}>▼</span>
              </button>
              {open === g.label && (
                <div className={styles.menu}>
                  {g.items.map((it: any) => (
                    <button className={styles.item} key={it.key} onClick={() => go(it)}>
                      <span className={styles.ic} style={{ background: it.grad }}>{it.icon}</span>
                      <span>{it.label}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <span className={styles.spacer} />

        {accounts.length > 1 && (
          <select
            className={styles.select}
            value={account}
            onChange={(e: any) => {
              setAccount(e.target.value);
              onAccountChange(e.target.value);
            }}
            data-testid="account-select"
          >
            {accounts.map((a: any) => {
              const id = a.id || a.account_id;
              return <option key={id} value={id}>{a.name || id}</option>;
            })}
          </select>
        )}

        <button className={styles.ghost} onClick={() => openTab("listings")} data-testid="nav-cabinet">
          Открыть рабочий кабинет
        </button>
      </div>

      <div className={styles.body}>{children}</div>
    </div>
  );
}
