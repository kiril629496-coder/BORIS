"use client";

import { useEffect, useMemo, useState } from "react";
import { Button, Card as BorisCard, Icon } from "@/app/ui";
import { useAccounts } from "@/app/lib/AccountContext";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

type Item = {
  id: number;
  account_id: string;
  field: string;
  operation: string;
  status: string;
  payload?: any;
  readiness_pct?: number | null;
  launch_status?: string | null;
};

const FIELD_META: Record<string, { title: string; description: string; icon: string; tone: string }> = {
  "profile.tone_of_voice": {
    title: "Подтвердить тон общения",
    description: "BORIS будет использовать этот стиль в сообщениях, объявлениях и другом клиентском контенте.",
    icon: "messages",
    tone: "violet",
  },
  "schedule.autopilot": {
    title: "Разрешить автоматическую работу",
    description: "BORIS сможет выполнять разрешённые действия автоматически в рамках настроенных ограничений.",
    icon: "zap",
    tone: "blue",
  },
  "schedule.mop_poll": {
    title: "Подтвердить проверку сообщений МОПом",
    description: "BORIS будет регулярно проверять новые обращения и передавать их в рабочий контур МОПа.",
    icon: "messages",
    tone: "cyan",
  },
};

function metaFor(item: Item) {
  if (FIELD_META[item.field]) return FIELD_META[item.field];
  return {
    title: humanizeBusinessText(item.field, "Подтвердить изменение"),
    description: "BORIS подготовил изменение и применит его только после вашего подтверждения.",
    icon: "check",
    tone: "blue",
  };
}

function businessReason(item: Item): string {
  const evidence = item.payload?.decision?.source_action?.evidence || {};
  const raw = evidence.why || evidence.reason || item.payload?.reason || "";
  if (!raw) return "";
  const text = humanizeBusinessText(raw, "");
  if (!text || /текущая конфигурация/i.test(text)) return "";
  return text;
}

function api(path: string, options?: RequestInit) {
  const token = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return fetch(path, {
    credentials: "include",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: "Bearer " + token } : {}),
      ...(options?.headers || {}),
    },
  });
}

export default function FactoryDecisionsClient() {
  const { accounts } = useAccounts();
  const [items, setItems] = useState<Item[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState("");

  const accountNames = useMemo(() => {
    const out = new Map<string, string>();
    for (const account of accounts || []) out.set(account.account_id, account.name || "Аккаунт");
    return out;
  }, [accounts]);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const res = await api("/api/factory/confirmations");
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail ? JSON.stringify(data.detail) : "Не удалось загрузить решения");
      const pending = (data.items || []).filter((x: Item) => x.status === "pending");
      setItems(pending);
      setSelected((prev) => prev.filter((id) => pending.some((x: Item) => x.id === id)));
    } catch (e: any) {
      setError(humanizeBusinessText(e?.message, "Не удалось загрузить решения"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  const allSelected = items.length > 0 && selected.length === items.length;
  const technicalScheduleIds = useMemo(
    () => items
      .filter((item) => item.operation === "migrate.schedule" && ["schedule.autopilot", "schedule.mop_poll"].includes(item.field))
      .map((item) => item.id),
    [items],
  );
  const grouped = useMemo(() => {
    const map = new Map<string, Item[]>();
    for (const item of items) map.set(item.account_id, [...(map.get(item.account_id) || []), item]);
    return Array.from(map.entries());
  }, [items]);

  function toggle(id: number) {
    setSelected((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);
  }

  function toggleAll() {
    setSelected(allSelected ? [] : items.map((x) => x.id));
  }

  async function bulk(decision: "approved" | "rejected", idsOverride?: number[]) {
    const targetIds = idsOverride?.length ? idsOverride : selected;
    if (!targetIds.length) return;
    setActionLoading(true);
    setError("");
    try {
      const res = await api("/api/factory/confirmations/bulk", {
        method: "POST",
        body: JSON.stringify({ ids: targetIds, decision, apply_now: decision === "approved" }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail ? JSON.stringify(data.detail) : "Не удалось сохранить решение");
      setSelected([]);
      await load();
    } catch (e: any) {
      setError(humanizeBusinessText(e?.message, "Не удалось сохранить решение"));
    } finally {
      setActionLoading(false);
    }
  }

  if (loading) {
    return <main className="boris-decisions-page"><BorisCard className="boris-decisions-loading">Загружаю решения…</BorisCard></main>;
  }

  return (
    <main className="boris-decisions-page">
      <section className="boris-decisions-hero">
        <div className="boris-decisions-hero-copy">
          <span>ВАШЕ РЕШЕНИЕ</span>
          <h1>Требуют решения</h1>
          <p>BORIS показывает здесь только то, что нельзя безопасно применить без вашего подтверждения.</p>
        </div>
        <div className="boris-decisions-counter"><strong>{items.length}</strong><small>нужно решить</small></div>
      </section>

      {error ? <div className="boris-decisions-error">{error}</div> : null}

      {!error && !items.length ? (
        <section className="boris-decisions-empty">
          <div className="boris-decisions-empty-icon"><Icon name="check" size={24}/></div>
          <h2>Сейчас всё решено</h2>
          <p>Действий, которые требуют вашего подтверждения, нет. BORIS продолжает работать по разрешённым правилам.</p>
        </section>
      ) : (
        <>
          <section className="boris-decisions-toolbar">
            <label><input type="checkbox" checked={allSelected} onChange={toggleAll}/><span>Выбрать все решения</span></label>
            <div>
              {technicalScheduleIds.length ? (
                <Button
                  kind="ghost"
                  disabled={actionLoading}
                  onClick={() => bulk("approved", technicalScheduleIds)}
                >
                  {actionLoading ? "Применяю…" : `Подтвердить технические расписания · ${technicalScheduleIds.length}`}
                </Button>
              ) : null}
              <Button kind="ghost" disabled={!selected.length || actionLoading} onClick={() => bulk("rejected")}>Отклонить выбранные</Button>
              <Button disabled={!selected.length || actionLoading} onClick={() => bulk("approved")}>
                {actionLoading ? "Применяю…" : `Подтвердить выбранные${selected.length ? ` · ${selected.length}` : ""}`}
              </Button>
            </div>
          </section>

          <div className="boris-decisions-stack">
            {grouped.map(([accountId, accountItems]) => {
              const accountName = accountNames.get(accountId) || "Аккаунт";
              return (
                <section key={accountId} className="boris-decisions-account-card">
                  <header>
                    <div className="boris-decisions-account-icon"><Icon name="briefcase" size={20}/></div>
                    <div><span>АККАУНТ</span><h2>{accountName}</h2><p>Готовность рабочего контура: {accountItems[0]?.readiness_pct ?? "—"}%</p></div>
                    <div className="boris-decisions-account-count">{accountItems.length} {accountItems.length === 1 ? "решение" : "решения"}</div>
                  </header>

                  <div className="boris-decisions-grid">
                    {accountItems.map((item) => {
                      const meta = metaFor(item);
                      const reason = businessReason(item);
                      const checked = selected.includes(item.id);
                      return (
                        <label key={item.id} className={`boris-decision-card boris-decision-${meta.tone}${checked ? " is-selected" : ""}`}>
                          <input type="checkbox" checked={checked} onChange={() => toggle(item.id)}/>
                          <div className="boris-decision-icon"><Icon name={meta.icon as any} size={20}/></div>
                          <div className="boris-decision-copy">
                            <div className="boris-decision-top"><h3>{meta.title}</h3><span>Нужно подтверждение</span></div>
                            <p>{meta.description}</p>
                            {reason ? <div className="boris-decision-reason"><b>Почему нужно решение:</b> {reason}</div> : null}
                            <div className="boris-decision-after"><Icon name="check" size={14}/><span>После подтверждения BORIS применит изменение автоматически.</span></div>
                          </div>
                        </label>
                      );
                    })}
                  </div>
                </section>
              );
            })}
          </div>
        </>
      )}
    </main>
  );
}
