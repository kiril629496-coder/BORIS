"use client";

import { useEffect, useState } from "react";
import styles from "../messenger.module.css";

type SessionItem = {
  session_id: string;
  user_agent?: string | null;
  device_label?: string | null;
  created_at?: string | null;
  last_seen_at?: string | null;
  expires_at?: string | null;
  revoked?: boolean;
  current?: boolean;
};

type LegalItem = {
  document_type: string;
  title: string;
  version: string;
  hash: string;
  accepted: boolean;
  accepted_at?: string | null;
};

function headers(): HeadersInit {
  const token = localStorage.getItem("boris_messenger_token") || localStorage.getItem("boris_token") || "";
  return token ? { Authorization: "Bearer " + token, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
}

function shortAgent(value?: string | null) {
  const raw = String(value || "");
  if (!raw) return "Неизвестное устройство";
  if (/iphone|ipad/i.test(raw)) return "iPhone / iPad";
  if (/android/i.test(raw)) return "Android";
  if (/macintosh|mac os/i.test(raw)) return "Mac";
  if (/windows/i.test(raw)) return "Windows";
  return "Браузер";
}

function dateTime(value?: string | null) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

export default function SecurityPanel() {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [legal, setLegal] = useState<LegalItem[]>([]);
  const [legacyCurrent, setLegacyCurrent] = useState(false);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    setError("");
    try {
      const [sr, lr] = await Promise.all([
        fetch("/api/auth/sessions", { cache: "no-store", headers: headers() }),
        fetch("/api/legal/messenger/status", { cache: "no-store", headers: headers() }),
      ]);
      if (sr.status === 401 || lr.status === 401) {
        window.location.replace("/login");
        return;
      }
      if (!sr.ok || !lr.ok) throw new Error("Не удалось загрузить состояние безопасности.");
      const s = await sr.json();
      const l = await lr.json();
      setSessions(s.sessions || []);
      setLegacyCurrent(Boolean(s.legacy_current));
      setLegal(l.documents || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить состояние безопасности.");
    }
  };

  useEffect(() => { void load(); }, []);

  const revoke = async (sessionId: string) => {
    setBusy(sessionId); setError(""); setNote("");
    try {
      const r = await fetch("/api/auth/sessions/revoke", {
        method: "POST", headers: headers(), body: JSON.stringify({ session_id: sessionId }),
      });
      if (!r.ok) throw new Error("Не удалось завершить сеанс.");
      setNote("Сеанс завершён.");
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось завершить сеанс."); }
    finally { setBusy(""); }
  };

  const revokeAll = async () => {
    if (!window.confirm("Завершить все предыдущие сеансы BORIS и оставить только это устройство?")) return;
    setBusy("all"); setError(""); setNote("");
    try {
      const r = await fetch("/api/auth/sessions/revoke-all", { method: "POST", headers: headers() });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.access_token) throw new Error("Не удалось обновить сеансы.");
      localStorage.setItem("boris_token", data.access_token);
      localStorage.setItem("boris_messenger_token", data.access_token);
      setNote("Все предыдущие сеансы завершены. Это устройство осталось активным.");
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось обновить сеансы."); }
    finally { setBusy(""); }
  };

  const logout = async () => {
    setBusy("logout");
    try { await fetch("/api/auth/logout", { method: "POST", headers: headers() }); } catch {}
    localStorage.removeItem("boris_token");
    localStorage.removeItem("boris_messenger_token");
    localStorage.removeItem("boris_account_id");
    window.location.replace("/login");
  };

  return <div className={styles.securityPanel}>
    <div className={styles.securityHero}>
      <div><h2>Безопасность</h2><p>Устройства, активные сеансы и принятые документы.</p></div>
      <button type="button" onClick={() => void load()}>Обновить</button>
    </div>

    {error && <div className={styles.securityError}>{error}</div>}
    {note && <div className={styles.securityNote}>{note}</div>}

    <section className={styles.securityCard}>
      <div className={styles.securityCardHead}>
        <div><strong>Устройства и сеансы</strong><small>{legacyCurrent ? "Текущий старый сеанс будет заменён после команды «выйти со всех»." : "Каждый новый вход получает отдельный отзываемый сеанс."}</small></div>
        <button type="button" onClick={() => void revokeAll()} disabled={busy === "all"}>{busy === "all" ? "Завершаем…" : "Выйти со всех остальных"}</button>
      </div>
      <div className={styles.sessionList}>
        {sessions.length ? sessions.map(item => <div className={styles.sessionRow} key={item.session_id}>
          <span className={styles.sessionIcon}>▣</span>
          <span><strong>{item.current ? "Это устройство" : shortAgent(item.user_agent)}</strong><small>Последняя активность: {dateTime(item.last_seen_at)} · до {dateTime(item.expires_at)}</small></span>
          <span className={styles.sessionState}>{item.revoked ? "Завершён" : item.current ? "Текущий" : "Активен"}</span>
          {!item.current && !item.revoked && <button type="button" onClick={() => void revoke(item.session_id)} disabled={busy === item.session_id}>{busy === item.session_id ? "…" : "Завершить"}</button>}
        </div>) : <div className={styles.securityEmpty}>Новых server-side сеансов пока нет. После следующего входа это устройство появится здесь.</div>}
      </div>
    </section>

    <section className={styles.securityCard}>
      <div className={styles.securityCardHead}><div><strong>Документы</strong><small>Принятие хранится на сервере отдельно для каждого пользователя.</small></div></div>
      <div className={styles.legalRows}>
        {legal.map(item => <div key={item.document_type}><span><strong>{item.title}</strong><small>Версия {item.version}</small></span><em>{item.accepted ? "Принято · " + dateTime(item.accepted_at) : "Не принято"}</em></div>)}
      </div>
      <div className={styles.securityLinks}><a href="/messenger/legal">Документы BORIS Messenger</a><a href="/privacy" target="_blank" rel="noreferrer">Политика конфиденциальности ↗</a></div>
    </section>

    <section className={styles.securityCard}>
      <div className={styles.securityCardHead}><div><strong>Текущее устройство</strong><small>При выходе локальные токены Messenger будут очищены.</small></div><button className={styles.securityDanger} type="button" onClick={() => void logout()} disabled={busy === "logout"}>{busy === "logout" ? "Выходим…" : "Выйти"}</button></div>
    </section>
  </div>;
}
