"use client";

import { useEffect, useState } from "react";
import css from "./shell.module.css";
import { apiGet, getAccount, getRole, openRoute } from "../../lib/api";

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
  // Задачи мини-CRM: колокольчик показывает их по ВСЕМ аккаунтам,
  // поэтому берём отдельную сводку, а не метки одного диалога.
  const [crm, setCrm] = useState<any>(null);
  const [bellOpen, setBellOpen] = useState(false);
  useEffect(() => {
    apiGet("/api/crm/today").then((d: any) => {
      if (d && d.status === "ok") setCrm(d);
    }).catch(() => {});
  }, []);

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
        <span style={{ position: "relative" }}>
          <button className={css.iconBtn} data-testid="crm-bell"
                  aria-label="Напоминания" title="Напоминания"
                  onClick={() => setBellOpen(!bellOpen)}>
            {(crm && crm["всего"] > 0) || unread > 0 ? "🔔" : "🔕"}
            {crm && crm["всего"] > 0 ? (
              <span style={{ position: "absolute", top: -2, right: -2, minWidth: 16,
                             height: 16, borderRadius: 999, fontSize: 11, fontWeight: 700,
                             lineHeight: "16px", color: "#FFFFFF", padding: "0 4px",
                             background: crm["просрочено"] > 0 ? "#D92D20" : "#067647" }}>
                {crm["всего"]}
              </span>
            ) : null}
          </button>
          {bellOpen ? (
            <div style={{ position: "absolute", right: 0, top: 40, width: 320, zIndex: 60,
                          background: "#FFFFFF", border: "1px solid #E3E7F0",
                          borderRadius: 12, boxShadow: "0 8px 24px rgba(16,24,40,0.12)",
                          padding: "12px 14px", textAlign: "left" }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>
                Напоминания
              </div>
              {!crm || !crm["задачи"] || crm["задачи"].length === 0 ? (
                <div style={{ fontSize: 13, color: "#98A2B3" }}>На сегодня задач нет</div>
              ) : crm["задачи"].map((t: any) => (
                <div key={t.id} onClick={() => openRoute("/messages")}
                     style={{ borderTop: "1px solid #F2F4F7", padding: "8px 0",
                              cursor: "pointer" }}>
                  <div style={{ fontSize: 13, fontWeight: 600,
                                color: t["просрочено"] ? "#D92D20" : "#067647" }}>
                    {t["время"] ? t["время"] + " · " : ""}{t["заголовок"]}
                  </div>
                  <div style={{ fontSize: 12, color: "#98A2B3" }}>{t["аккаунт"]}</div>
                </div>
              ))}
            </div>
          ) : null}
        </span>
        <button className={css.iconBtn} aria-label="Помощь" title="Помощь">?</button>
        <div className={css.user}>
          <span className={css.userName}>{name || "Аккаунт"}</span>
          <span className={css.userRole}>{roleRu}</span>
        </div>
      </div>
    </header>
  );
}
