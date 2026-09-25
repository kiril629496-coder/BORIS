"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/app/components/shell/AppShell";
import { useAccounts } from "@/app/lib/AccountContext";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";
import { Alert, Button, EmptyState, Page } from "@/app/ui";
import styles from "./workMode.module.css";

type Mode = "always_ask" | "always_auto" | "dates" | "goal_auto";

type AuditItem = { id?: number | string; ts?: string; created_at?: string; human?: string; action?: string; details?: string; actor?: string };

const MODE_OPTIONS: Array<{ value: Mode; icon: string; title: string; hint: string; badge: string }> = [
  { value: "always_ask", icon: "✓", title: "С подтверждением", hint: "BORIS предложит действие и выполнит его только после вашего подтверждения.", badge: "Полный контроль" },
  { value: "always_auto", icon: "⚡", title: "Самостоятельно", hint: "BORIS выполняет разрешённые действия сам в пределах настроенных правил и ограничений.", badge: "Автоматическая работа" },
  { value: "goal_auto", icon: "◎", title: "По бизнес-цели", hint: "BORIS самостоятельно работает на заданный результат и меняет тактику по фактическим показателям.", badge: "По результату" },
  { value: "dates", icon: "▣", title: "Самостоятельно в выбранный период", hint: "Автоматическая работа включается только между указанными датами.", badge: "По расписанию" },
];

function authHeaders(extra: Record<string, string> = {}) {
  const token = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return { ...extra, Authorization: `Bearer ${token}` };
}

function formatAuditTime(value?: string) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? `Сегодня, ${d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`
    : d.toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function cleanAudit(item: AuditItem) {
  const rawDetails = String(item.details || "").trim();
  const raw = rawDetails || item.human || item.action || "BORIS выполнил действие";
  let text = humanizeBusinessText(raw, "BORIS выполнил действие по аккаунту")
    .replace(/^🤖\s*Борис\s*[—·-]?\s*/i, "")
    .replace(/^👤\s*Вы\s*[—·-]?\s*/i, "Вы: ")
    .replace(/^Борис\s*[—·-]?\s*/i, "")
    .replace(/\s*\((?:повышение|снижение)\)\s*$/i, "")
    .trim();
  if (/^режим\s*=\s*always[_ -]?auto\.?$/i.test(rawDetails)) text = "Режим работы изменён: Самостоятельно";
  if (/^режим\s*=\s*always[_ -]?ask\.?$/i.test(rawDetails)) text = "Режим работы изменён: С подтверждением";
  if (/^режим\s*=\s*goal[_ -]?auto\.?$/i.test(rawDetails)) text = "Режим работы изменён: По бизнес-цели";
  if (/^режим\s*=\s*dates\.?$/i.test(rawDetails)) text = "Режим работы изменён: По расписанию";
  if (item.action === "cpx_apply_bid") {
    text = text.replace(/^объявление\s+(\d+):\s*ставка\s*(\d+)→(\d+)₽/i, "Изменил ставку объявления №$1: $2 → $3 ₽");
  }
  if (item.action === "scenario_step") {
    text = text
      .replace(/BORIS/gi, "BORIS")
      .replace(/без ручного перезапуска/gi, "автоматически")
      .replace(/без участия владельца/gi, "самостоятельно")
      .replace(/действий владельца/gi, "ваших действий");
  }
  return text;
}

function visibleAudit(items: AuditItem[]) {
  const seen = new Set<string>();
  const out: AuditItem[] = [];
  for (const item of items) {
    const text = cleanAudit(item);
    const key = `${item.action || ""}|${text}`;
    const looksTechnical = /(?:^|[\s=:])(?:always_auto|always_ask|goal_auto|daily_stats|source_status|feed_mode|publication_mode|current_sha|validated_sha)(?:$|[\s,.;])/i.test(text);
    if (!text || looksTechnical || seen.has(key)) continue;
    seen.add(key);
    out.push(item);
    if (out.length >= 12) break;
  }
  return out;
}

export default function WorkModePage() {
  const { selectedAccountId, selectedAccount, loading: accountsLoading } = useAccounts();
  const [mode, setMode] = useState<Mode>("always_ask");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [audit, setAudit] = useState<AuditItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  const load = useCallback(async () => {
    if (!selectedAccountId) { setAudit([]); return; }
    setLoading(true); setError(""); setAudit([]);
    const accountAtRequest = selectedAccountId;
    const id = encodeURIComponent(accountAtRequest);
    try {
      const [settingsRes, auditRes] = await Promise.all([
        fetch(`/api/avito/autopilot_settings?account_id=${id}`, { headers: authHeaders(), cache: "no-store" }),
        fetch(`/api/avito/audit_log?account_id=${id}&limit=40`, { headers: authHeaders(), cache: "no-store" }),
      ]);
      if (!settingsRes.ok) throw new Error(`HTTP ${settingsRes.status}`);
      const settingsData = await settingsRes.json();
      const settings = settingsData?.settings || {};
      setMode((["always_ask", "always_auto", "dates", "goal_auto"].includes(settings.mode) ? settings.mode : "always_ask") as Mode);
      setFrom(settings.date_from || "");
      setTo(settings.date_to || "");
      if (auditRes.ok) {
        const auditData = await auditRes.json();
        setAudit(Array.isArray(auditData?.log) ? auditData.log : []);
      }
    } catch {
      setError("Не удалось загрузить настройки BORIS. Попробуйте обновить страницу.");
    } finally {
      setLoading(false);
    }
  }, [selectedAccountId]);

  useEffect(() => { void load(); }, [load]);

  const current = useMemo(() => MODE_OPTIONS.find((item) => item.value === mode) || MODE_OPTIONS[0], [mode]);
  const shownAudit = useMemo(() => visibleAudit(audit), [audit]);

  async function save() {
    if (!selectedAccountId) return;
    if (mode === "dates" && (!from || !to || from > to)) {
      setError("Укажите корректный период работы BORIS."); return;
    }
    setSaving(true); setError(""); setSaved("");
    try {
      const body: Record<string, string> = { account_id: selectedAccountId, mode };
      if (mode === "dates") { body.date_from = from; body.date_to = to; }
      const res = await fetch("/api/avito/set_autopilot_settings", {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSaved("Режим BORIS сохранён");
      await load();
    } catch {
      setError("Не удалось сохранить режим BORIS. Попробуйте ещё раз.");
    } finally { setSaving(false); }
  }

  return (
    <AppShell active="settings" title="Режим BORISа" subtitle="Настройте уровень самостоятельности для выбранного аккаунта">
      <Page>
        <div className={styles.page}>
          {error ? <Alert kind="danger">{error}</Alert> : null}
          {saved ? <Alert kind="success">{saved}</Alert> : null}
          {!accountsLoading && !selectedAccountId ? <EmptyState title="Не выбран аккаунт" hint="Выберите аккаунт в верхней панели." /> : null}
          {selectedAccountId ? <>
            <section className={styles.hero}>
              <div>
                <div className={styles.eyebrow}>Режим BORISа</div>
                <h1>Вы решаете, сколько BORIS делает сам</h1>
                <p>Для каждого аккаунта можно выбрать свой режим. BORIS соблюдает заданные ограничения и показывает результат выполненной работы.</p>
              </div>
              <div className={styles.statusCard}>
                <span className={styles.statusIcon}>{current.icon}</span>
                <div><small>Сейчас включено</small><strong>{current.title}</strong><span>{selectedAccount?.name || "Текущий аккаунт"}</span></div>
              </div>
            </section>

            <section className={styles.panel}>
              <div className={styles.sectionHead}><div><h2>Как BORIS будет работать</h2><p>Выберите удобный уровень самостоятельности. Настройка действует только в этом аккаунте.</p></div></div>
              {loading ? <div className={styles.loading}>Загружаю настройки…</div> : <div className={styles.modeGrid}>
                {MODE_OPTIONS.map((item) => <button key={item.value} type="button" className={`${styles.modeCard} ${mode === item.value ? styles.active : ""}`} onClick={() => setMode(item.value)} aria-pressed={mode === item.value}>
                  <span className={styles.modeIcon}>{item.icon}</span>
                  <span className={styles.modeText}><span className={styles.modeTop}><strong>{item.title}</strong><em>{item.badge}</em></span><span>{item.hint}</span></span>
                  <span className={styles.radio}>{mode === item.value ? "✓" : ""}</span>
                </button>)}
              </div>}
              {mode === "dates" ? <div className={styles.dateBox}><div><label htmlFor="boris-from">Начать</label><input id="boris-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></div><span>→</span><div><label htmlFor="boris-to">Завершить</label><input id="boris-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></div></div> : null}
              <div className={styles.saveRow}><Button onClick={save} disabled={saving || loading}>{saving ? "Сохраняю…" : "Сохранить режим"}</Button><span>Другие аккаунты останутся без изменений.</span></div>
            </section>

            <section className={styles.panel}>
              <div className={styles.sectionHead}><div><h2>Последние действия BORIS</h2><p>Здесь только понятные клиенту действия и изменения по выбранному аккаунту.</p></div><span className={styles.count}>{shownAudit.length ? `${shownAudit.length} последних` : "Нет действий"}</span></div>
              {shownAudit.length ? <div className={styles.timeline}>{shownAudit.map((item, index) => <article className={styles.actionCard} key={String(item.id ?? item.ts ?? index)}><span className={styles.actionIcon}>{String(item.actor || "").startsWith("user") ? "👤" : "B"}</span><div><strong>{cleanAudit(item)}</strong><span>{formatAuditTime(item.ts || item.created_at)}</span></div></article>)}</div> : <div className={styles.empty}><span>✓</span><div><strong>История пока пустая</strong><p>Когда BORIS выполнит действие по этому аккаунту, оно появится здесь понятным языком.</p></div></div>}
            </section>
          </> : null}
        </div>
      </Page>
    </AppShell>
  );
}
