"use client";

import { useEffect, useState } from "react";
import css from "./shell.module.css";
import { apiGet, getAccount, getRole } from "../../lib/api";

type Props = { title: string; subtitle?: string; onBurger: () => void; onSwitch: () => void };

function hello() {
  const h = new Date().getHours();
  if (h < 5) return "Доброй ночи";
  if (h < 12) return "Доброе утро";
  if (h < 18) return "Добрый день";
  return "Добрый вечер";
}

export default function TopBar({ title, subtitle, onBurger, onSwitch }: Props) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    setRole(getRole());
    const acc = getAccount();
    if (!acc) return;
    apiGet("/api/home/overview?account_id=" + encodeURIComponent(acc)).then((d: any) => {
      if (d && d.status === "ok") {
        setName((d.account && d.account.name) || "");
        const n = (d.attention || []).find((c: any) => c.key === "notifications");
        if (n) setUnread(parseInt(String(n.title).replace(/\D/g, ""), 10) || 0);
      }
    });
  }, []);

  const roleRu = role === "owner" ? "Владелец" : role === "manager" ? "Менеджер" : "Клиент";

  return (
    <header className={css.top}>
      <button className={css.iconBtn + " " + css.burger} onClick={onBurger} aria-label="Меню">☰</button>

      <div className={css.hello}>
        <div className={css.helloTitle}>{title || hello() + "!"}</div>
        {subtitle && <div className={css.helloSub}>{subtitle}</div>}
      </div>

      <div className={css.topActions}>
        <button className={css.ghost} onClick={onSwitch} data-testid="to-old">Старый интерфейс</button>
        <button className={css.iconBtn} aria-label="Уведомления" title="Уведомления">
          {unread > 0 ? "🔔" : "🔕"}
        </button>
        <button className={css.iconBtn} aria-label="Помощь" title="Помощь">?</button>
        <div className={css.user}>
          <span className={css.userName}>{name || "Аккаунт"}</span>
          <span className={css.userRole}>{roleRu}</span>
        </div>
      </div>
    </header>
  );
}
