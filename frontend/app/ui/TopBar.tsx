"use client";

/**
 * Верхняя панель кабинета по эталону BORIS.
 *
 * Живёт в Shell, поэтому появляется сразу на всех страницах: приветствие по
 * имени, колокольчик, справка и пользователь с ролью. Один элемент эталона —
 * один вид на весь продукт, без переписывания каждого экрана.
 */
import { useEffect, useState } from "react";
import { color, font, radius, shadow, space } from "./tokens";
import { getRole, logout } from "../lib/api";
import AccountPicker from "../components/AccountPicker";

const ROLE_RU: Record<string, string> = {
  owner: "Владелец", client: "Клиент", manager: "Менеджер",
  employee: "Сотрудник", admin: "Администратор",
};

export function TopBar({ back }: any) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("");
  const [menu, setMenu] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    setEmail(localStorage.getItem("boris_user_email") || "");
    setRole(getRole());
  }, []);

  const hour = new Date().getHours();
  const hello = hour < 6 ? "Доброй ночи" : hour < 12 ? "Доброе утро"
              : hour < 18 ? "Добрый день" : "Добрый вечер";
  const name = email ? email.split("@")[0] : "";
  const initials = (name || "B").slice(0, 2).toUpperCase();

  return (
    <div style={{ display: "flex", alignItems: "center", gap: space.lg,
                  padding: "14px 32px", background: color.surface,
                  borderBottom: `1px solid ${color.line}`,
                  position: "sticky", top: 0, zIndex: 30 }}>
      {back && (
        <button onClick={back.go} data-testid="nav-back" aria-label={back.label}
          style={{ background: color.surfaceAlt, color: color.text, border: "none",
                   borderRadius: radius.md, padding: "8px 14px", fontSize: 13.5,
                   fontWeight: 600, cursor: "pointer", flexShrink: 0 }}>
          {back.label}
        </button>
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ ...font.bodyStrong }}>
          {hello}{name ? ", " + name : ""}!
        </div>
        <div style={{ ...font.small }}>
          Выберите задачу — BORIS подключит нужные инструменты
        </div>
      </div>

      <AccountPicker />
      <button title="Уведомления"
        style={{ width: 38, height: 38, borderRadius: radius.pill, cursor: "pointer",
                 border: `1px solid ${color.line}`, background: color.surface,
                 fontSize: 16 }}>🔔</button>
      <button title="Справка" onClick={() => { window.location.href = "/support"; }}
        style={{ width: 38, height: 38, borderRadius: radius.pill, cursor: "pointer",
                 border: `1px solid ${color.line}`, background: color.surface,
                 fontSize: 16 }}>?</button>

      <div style={{ position: "relative" }}>
        <button onClick={() => setMenu((v) => !v)}
          style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer",
                   background: "none", border: "none", padding: 0 }}>
          <div style={{ width: 38, height: 38, borderRadius: radius.pill,
                        background: color.blueSoft, color: color.blue,
                        display: "flex", alignItems: "center",
                        justifyContent: "center", fontWeight: 700, fontSize: 13 }}>
            {initials}
          </div>
          <div style={{ textAlign: "left" }}>
            <div style={{ ...font.small, color: color.heading, fontWeight: 600 }}>
              {name || "Пользователь"}
            </div>
            <div style={{ ...font.small, fontSize: 11.5 }}>
              {ROLE_RU[role] || role || ""}
            </div>
          </div>
        </button>
        {menu && (
          <div style={{ position: "absolute", right: 0, top: 48, zIndex: 40,
                        background: color.surface, borderRadius: radius.md,
                        border: `1px solid ${color.line}`, boxShadow: shadow.pop,
                        minWidth: 200, overflow: "hidden" }}>
            {[["Профиль", "/dashboard/home"], ["Пользователи", "/team"],
              ["Поддержка", "/support"]].map(([label, href]) => (
              <button key={label} onClick={() => { window.location.href = href; }}
                style={{ display: "block", width: "100%", textAlign: "left",
                         padding: "10px 14px", background: "none", border: "none",
                         cursor: "pointer", ...font.body }}>{label}</button>
            ))}
            <button onClick={() => logout()}
              style={{ display: "block", width: "100%", textAlign: "left",
                       padding: "10px 14px", background: "none", border: "none",
                       cursor: "pointer", ...font.body, color: color.red }}>
              Выйти
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
