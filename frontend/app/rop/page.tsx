"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Page, PageHeader, Card, Button, Badge, Alert, EmptyState, Kpi, KpiRow, Icon, Table,
} from "../ui";
import { color, space, font } from "../ui/tokens";

type Acc = {
  account_id: string; name: string; avito_user_id: string; bound: boolean;
  memory_shared: boolean; memory_mode: string;
};
type Stats = {
  status: string; total?: number; answered?: number; missed?: number;
  answered_pct?: number; missed_pct?: number; total_min?: number; message?: string;
};
type Rop = { minutes?: number; reports_calls?: number; reports_chats?: number;
  chats?: number; period?: any };

function api(path: string) {
  const token =
    typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return fetch(path, { headers: { Authorization: "Bearer " + token } });
}

export default function RopPage() {
  const [accounts, setAccounts] = useState<Acc[]>([]);
  const [stats, setStats] = useState<Record<string, Stats>>({});
  const [limits, setLimits] = useState<Record<string, Rop>>({});
  const [reports, setReports] = useState<Record<string, any[]>>({});
  const [scope, setScope] = useState("all");
  const [mode, setMode] = useState<"by" | "sum">("by");
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState("");

  /** Состав аккаунтов приходит из ai_bindings — ничего не зашито. */
  const loadAccounts = useCallback(async () => {
    try {
      const r = await api("/api/ai/accounts?product=rop");
      if (!r.ok) { setNote("Не удалось получить список аккаунтов"); setLoading(false); return; }
      const d = await r.json();
      setAccounts((d.accounts || []).filter((a: Acc) => a.bound));
    } catch { setNote("Ошибка сети"); }
    setLoading(false);
  }, []);

  useEffect(() => { loadAccounts(); }, [loadAccounts]);

  const loadData = useCallback(async (list: Acc[]) => {
    for (const a of list) {
      const q = "account_id=" + encodeURIComponent(a.account_id);
      try {
        const [s, l, rp] = await Promise.all([
          api(`/api/calltracking/stats?${q}&days=${days}`),
          api(`/api/calltracking/all_limits?${q}`),
          api(`/api/calltracking/reports/list?${q}`),
        ]);
        if (s.ok) { const d = await s.json(); setStats((p) => ({ ...p, [a.account_id]: d })); }
        if (l.ok) { const d = await l.json(); setLimits((p) => ({ ...p, [a.account_id]: d.rop || {} })); }
        if (rp.ok) { const d = await rp.json(); setReports((p) => ({ ...p, [a.account_id]: d.items || [] })); }
      } catch { /* аккаунт без доступа не должен ронять экран */ }
    }
  }, [days]);

  useEffect(() => { if (accounts.length) loadData(accounts); }, [accounts, loadData]);

  const shown = scope === "all" ? accounts : accounts.filter((a) => a.account_id === scope);
  const sum = shown.reduce((acc, a) => {
    const s = stats[a.account_id] || {};
    acc.total += s.total || 0; acc.answered += s.answered || 0; acc.missed += s.missed || 0;
    acc.minutes += toNum((limits[a.account_id] || {}).minutes);
    return acc;
  }, { total: 0, answered: 0, missed: 0, minutes: 0 });
  const sumPct = sum.total ? Math.round((sum.answered / sum.total) * 100) : 0;

  if (loading) {
    return <Page><PageHeader title="AI-руководитель отдела продаж" /></Page>;
  }

  if (!accounts.length) {
    return (
      <Page>
        <PageHeader title="AI-руководитель отдела продаж" />
        <EmptyState
          title="РОП пока не привязан к аккаунтам"
          hint="Администратор BORIS выбирает, какие аккаунты Авито обслуживает AI-РОП, на экране «AI-сотрудники»."
          action={<Button onClick={() => { window.location.href = "/ai"; }}>
            Перейти к подключению
          </Button>} />
      </Page>
    );
  }

  return (
    <Page>
      <PageHeader
        title="AI-руководитель отдела продаж"
        subtitle={`Обслуживает ${accounts.length} ${accounts.length === 1 ? "аккаунт" : "аккаунта"} · данные за ${days} дней`}
        actions={
          <>
            <Button kind={days === 7 ? "secondary" : "ghost"} onClick={() => setDays(7)}>7 дней</Button>
            <Button kind={days === 30 ? "secondary" : "ghost"} onClick={() => setDays(30)}>30 дней</Button>
          </>
        } />

      {note ? <Alert kind="danger">{note}</Alert> : null}

      <div style={{ display: "flex", gap: space.sm, flexWrap: "wrap", marginBottom: space.lg }}>
        {accounts.length > 1 ? (
          <Button kind={scope === "all" ? "secondary" : "ghost"} onClick={() => setScope("all")}>
            Оба аккаунта
          </Button>
        ) : null}
        {accounts.map((a) => (
          <Button key={a.account_id} kind={scope === a.account_id ? "secondary" : "ghost"}
                  onClick={() => setScope(a.account_id)}>
            {a.name}
          </Button>
        ))}
        {scope === "all" && accounts.length > 1 ? (
          <span style={{ marginLeft: space.md, display: "flex", gap: space.sm }}>
            <Button kind={mode === "by" ? "secondary" : "ghost"} onClick={() => setMode("by")}>
              По аккаунтам
            </Button>
            <Button kind={mode === "sum" ? "secondary" : "ghost"} onClick={() => setMode("sum")}>
              Сводка бизнеса
            </Button>
          </span>
        ) : null}
      </div>

      {scope === "all" && accounts.length > 1 && mode === "sum" ? (
        <>
          <Alert kind="info">
            <b>Режим: сводка бизнеса.</b> Показатели {shown.length} аккаунтов сложены вместе.
            Ниже — вклад каждого; нажмите на строку, чтобы открыть аккаунт отдельно.
          </Alert>
          <KpiRow>
            <Kpi value={sum.total} label="звонков всего" />
            <Kpi value={sum.answered} label="отвечено" kind="success" />
            <Kpi value={sum.missed} label="пропущено" kind={sum.missed ? "danger" : "neutral"} />
            <Kpi value={sumPct + "%"} label="доля отвеченных" kind="info" />
            <Kpi value={sum.minutes} label="минут в пакете" />
          </KpiRow>
          <div style={{ marginTop: space.lg }}>
            <Table head={["Аккаунт", "Звонков", "Отвечено", "Пропущено", "Доля"]}
                   onRowClick={(i) => { const a = shown[i]; if (a) { setScope(a.account_id); setMode("by"); } }}
                   rows={shown.map((a) => {
                     const s = stats[a.account_id] || {};
                     return [
                       <b key="n" style={{ color: color.heading }}>{a.name}</b>,
                       s.total ?? "—", s.answered ?? "—", s.missed ?? "—",
                       (s.answered_pct ?? 0) + "%",
                     ];
                   })} />
          </div>
        </>
      ) : (
        <div style={{ display: "flex", gap: space.lg, flexWrap: "wrap" }}>
          {shown.map((a) => {
            const s = stats[a.account_id] || {};
            const l = limits[a.account_id] || {};
            const rp = reports[a.account_id] || [];
            const bad = s.status && s.status !== "ok";
            return (
              <Card key={a.account_id} style={{ flex: "1 1 420px", minWidth: 320 }}>
                <div style={{ display: "flex", alignItems: "center", gap: space.sm }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={font.h3 as React.CSSProperties}>{a.name}</div>
                    <div style={{ ...font.small, marginTop: 2 }}>
                      {a.account_id}{a.avito_user_id ? " · Avito ID " + a.avito_user_id : ""}
                    </div>
                  </div>
                  <Badge kind={a.memory_mode === "strict" ? "success" : "neutral"}>
                    память {a.memory_mode}
                  </Badge>
                  <Badge kind="info">РОП включён</Badge>
                </div>

                {bad ? (
                  <div style={{ marginTop: space.md }}>
                    <Alert kind="warning">
                      {s.message || "Данные звонков недоступны для этого аккаунта"}
                    </Alert>
                  </div>
                ) : (
                  <div style={{ display: "flex", gap: space.xl, flexWrap: "wrap",
                                marginTop: space.lg }}>
                    <Metric v={s.total ?? "—"} l="звонков" />
                    <Metric v={s.answered ?? "—"} l="отвечено" c={color.green} />
                    <Metric v={s.missed ?? "—"} l="пропущено"
                            c={s.missed ? color.red : undefined} />
                    <Metric v={(s.answered_pct ?? 0) + "%"} l="доля отвеченных" c={color.blue} />
                  </div>
                )}

                <div style={{ height: 1, background: color.line, margin: `${space.lg}px 0` }} />

                <div style={{ display: "flex", gap: space.xl, flexWrap: "wrap", ...font.small }}>
                  <span>минут в пакете: <b style={{ color: color.heading }}>{num(l.minutes)}</b></span>
                  <span>отчётов по звонкам: <b style={{ color: color.heading }}>{num(l.reports_calls)}</b></span>
                  <span>по перепискам: <b style={{ color: color.heading }}>{num(l.reports_chats)}</b></span>
                  <span>разборов чатов: <b style={{ color: color.heading }}>{num(l.chats)}</b></span>
                </div>

                <div style={{ ...font.bodyStrong, marginTop: space.lg, marginBottom: space.sm }}>
                  Отчёты этого аккаунта
                </div>
                {rp.length ? rp.slice(0, 5).map((r: any, i: number) => (
                  <div key={i} style={{ display: "flex", gap: space.sm, alignItems: "center",
                                        padding: "6px 0", ...font.small }}>
                    <Icon name="file" size={15} tone="muted" />
                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden",
                                   textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {r.title || r.name || ("Отчёт #" + (r.id ?? i))}
                    </span>
                    <span>{r.created_at || r.date || ""}</span>
                  </div>
                )) : (
                  <div style={font.small as React.CSSProperties}>Отчётов пока нет</div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </Page>
  );
}

/** Лимиты приходят объектами вида {purchased, used, left} — берём осмысленное число. */
function num(v: any): React.ReactNode {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return v;
  if (typeof v === "string") return v;
  if (typeof v === "object") {
    const o = v as Record<string, any>;
    const left = o.left ?? o.remaining ?? o.available;
    const total = o.purchased ?? o.total ?? o.limit;
    if (left !== undefined && total !== undefined) return `${left} из ${total}`;
    if (left !== undefined) return left;
    if (total !== undefined) return total;
    const first = Object.values(o).find((x) => typeof x === "number");
    return first === undefined ? "—" : (first as number);
  }
  return "—";
}

/** То же, но всегда числом — для сложения в сводке. */
function toNum(v: any): number {
  if (typeof v === "number") return v;
  if (v && typeof v === "object") {
    const o = v as Record<string, any>;
    const c = o.left ?? o.remaining ?? o.available ?? o.purchased ?? o.total;
    if (typeof c === "number") return c;
  }
  return 0;
}

function Metric({ v, l, c }: { v: React.ReactNode; l: string; c?: string }) {
  return (
    <div>
      <div style={{ fontSize: 24, fontWeight: 700, color: c || color.heading }}>{v}</div>
      <div style={font.small as React.CSSProperties}>{l}</div>
    </div>
  );
}
