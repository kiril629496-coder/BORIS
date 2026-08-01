"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Page, PageHeader, Card, Button, Input, Badge, Alert, EmptyState, Kpi, KpiRow, Icon, Table,
} from "../ui";
import { color, space, font } from "../ui/tokens";

type Src = {
  id: number; account_id: string; source_type: string; title: string; url: string;
  status: string; last_parsed_at: string; facts: number; confirmed: number;
  conflicts: number; error: string; scope: string; trust: number;
};
type QItem = {
  id: number; category: string; type: string; name: string; value: string;
  source: string; confidence: number; status: string; asked: number; why: string;
};
type Sum = {
  items: number; dialogs: number; facts: number; confirmed: number; usable: number;
  conflicts: number; questions: number; covered_strong: number; covered_all: number;
};

const ST: Record<string, { label: string; kind: "neutral" | "info" | "success" | "warning" | "danger" }> = {
  connected: { label: "Подключён", kind: "info" },
  queued: { label: "В очереди", kind: "warning" },
  processing: { label: "Разбирается", kind: "warning" },
  ready: { label: "Изучен", kind: "success" },
  needs_attention: { label: "Требует внимания", kind: "warning" },
  failed: { label: "Ошибка", kind: "danger" },
  disabled: { label: "Отключён", kind: "neutral" },
};

const ICON: Record<string, any> = {
  avito_item: "ads", dialog: "messages", site: "globe", price_list: "file",
  commercial_offer: "file", vk: "ads", telegram: "send", "2gis": "target",
  yandex_maps: "target", contract: "file", estimate: "file",
};

function api(path: string, init?: RequestInit) {
  const t = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return fetch(path, {
    ...(init || {}),
    headers: {
      "Content-Type": "application/json", Authorization: "Bearer " + t,
      ...((init && (init.headers as Record<string, string>)) || {}),
    },
  });
}

export default function TrainingPage() {
  const [acc, setAcc] = useState("");
  const [srcs, setSrcs] = useState<Src[]>([]);
  const [queue, setQueue] = useState<QItem[]>([]);
  const [hint, setHint] = useState("");
  const [inQueue, setInQueue] = useState(0);
  const [advice, setAdvice] = useState<any[]>([]);
  const [sum, setSum] = useState<Sum | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState(false);
  const [narrow, setNarrow] = useState(false);

  useEffect(() => {
    const c = () => setNarrow(window.innerWidth < 900);
    c(); window.addEventListener("resize", c);
    return () => window.removeEventListener("resize", c);
  }, []);

  const load = useCallback(async (a: string) => {
    if (!a) return;
    const q = "account_id=" + encodeURIComponent(a);
    try {
      const [s, c, m] = await Promise.all([
        api("/api/memory/sources?" + q),
        api("/api/memory/confirm_queue?" + q + "&limit=14"),
        api("/api/memory/summary?" + q),
      ]);
      if (s.ok) { const d = await s.json(); setSrcs(d.sources || []); setInQueue(d.in_queue || 0); setAdvice(d.recommend || []); }
      if (c.ok) {
        const d = await c.json();
        const items = (d.items || []).filter((x: QItem) =>
          String(x.value || "").trim().length > 25 || x.category === "cena");
        setQueue(items.slice(0, 10));
        setHint(d.hint || "");
      }
      if (m.ok) setSum(await m.json());
    } catch { setErr(true); setNote("Не удалось загрузить центр обучения"); }
  }, []);

  useEffect(() => {
    const a = localStorage.getItem("boris_currentAccount") || "";
    setAcc(a);
    if (a) load(a);
  }, [load]);

  async function post(path: string, body: unknown, ok: string) {
    setBusy(true); setNote(""); setErr(false);
    try {
      const r = await api(path, { method: "POST", body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      setErr(!r.ok);
      setNote(d.message || d.detail || ok);
      await load(acc);
    } catch { setErr(true); setNote("Ошибка сети"); }
    setBusy(false);
  }

  const know = sum && sum.questions
    ? Math.round((100 * sum.covered_strong) / sum.questions) : 0;
  const failed = srcs.filter((s) => s.status === "failed" || s.error);

  return (
    <Page>
      <PageHeader
        title="Центр обучения BORIS"
        subtitle={acc ? "Аккаунт: " + acc
                     : "Укажите аккаунт — BORIS покажет, что он о нём знает"}
        actions={
          <>
            <Input value={acc} onChange={(e) => setAcc(e.target.value)}
                   placeholder="account_id" style={{ width: 260 }} />
            <Button kind="ghost" onClick={() => load(acc)} disabled={busy || !acc}>
              Показать
            </Button>
            <Button kind="ghost" disabled={busy || !acc}
                    onClick={() => post("/api/memory/sources/sync", { account_id: acc }, "Источники обновлены")}>
              Обновить источники
            </Button>
            <Button disabled={busy || !acc}
                    onClick={() => post("/api/memory/extract", { account_id: acc }, "Изучение запущено")}>
              Изучить заново
            </Button>
          </>
        } />

      {note ? <Alert kind={err ? "danger" : "success"}>{note}</Alert> : null}

      {sum ? (
        <>
          <Card style={{ marginBottom: space.lg }}>
            <div style={font.h3 as React.CSSProperties}>
              BORIS знает ваш бизнес на {know}%
            </div>
            <div style={{ height: 10, borderRadius: 999, background: color.surfaceAlt,
                          marginTop: space.md, overflow: "hidden" }}>
              <div style={{ width: Math.max(know, 2) + "%", height: "100%",
                            background: color.blue }} />
            </div>
            <div style={{ ...font.small, marginTop: space.sm }}>
              Отвечает без подтверждения на {sum.covered_strong} из {sum.questions} типовых
              вопросов. Ещё {sum.covered_all - sum.covered_strong} вопросов ждут вашего
              подтверждения фактов.
            </div>
          </Card>

          <KpiRow>
            <Kpi value={srcs.length} label="источников" />
            <Kpi value={sum.facts} label="найдено фактов" />
            <Kpi value={sum.confirmed} label="подтверждено" kind="success" />
            <Kpi value={sum.facts - sum.confirmed} label="ждут подтверждения" kind="warning" />
            <Kpi value={sum.conflicts} label="конфликтов" kind={sum.conflicts ? "danger" : "neutral"} />
            <Kpi value={inQueue} label="в очереди разбора" kind={inQueue ? "info" : "neutral"} />
          </KpiRow>
        </>
      ) : null}

      {failed.length ? (
        <div style={{ marginTop: space.lg }}>
          <Alert kind="danger">
            Последние ошибки: {failed.map((f) => f.title + " — " + (f.error || "сбой")).join("; ")}
          </Alert>
        </div>
      ) : null}

      {queue.length ? (
        <Card style={{ marginTop: space.lg }}>
          <div style={{ display: "flex", alignItems: "center", gap: space.sm }}>
            <Icon name="zap" tone="accent" />
            <div style={font.h3 as React.CSSProperties}>Что подтвердить в первую очередь</div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: space.md, marginTop: 4 }}>
            <div style={{ ...font.small, flex: 1 }}>{hint}</div>
            <Button kind="ghost" onClick={() => { window.location.href = "/memory"; }}>
              Вся база знаний
            </Button>
          </div>
          {queue.map((it, i) => (
            <div key={it.id} style={{ display: "flex", gap: space.md, alignItems: "flex-start",
                                      padding: "12px 0",
                                      borderTop: i ? "1px solid " + color.line : "none" }}>
              <Badge kind={it.status === "conflict" ? "danger"
                          : it.status === "stale" ? "warning" : "info"}>{it.type}</Badge>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ ...font.bodyStrong, overflow: "hidden",
                              textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{it.name}</div>
                <div style={{ ...font.small, marginTop: 2, overflow: "hidden",
                              textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {it.value} · {it.source} · {it.why}
                </div>
              </div>
              <Button disabled={busy}
                      onClick={() => post("/api/memory/confirm",
                        { account_id: acc, fact_id: it.id }, "Подтверждено")}>
                Верно
              </Button>
            </div>
          ))}
        </Card>
      ) : null}

      <div style={{ ...font.h3, marginTop: space.xl, marginBottom: space.md }}>
        Подключённые источники
      </div>
      {srcs.length && narrow ? (
        <div>
          {srcs.map((s) => {
            const st = ST[s.status] || ST.connected;
            return (
              <Card key={s.id} padding={space.lg} style={{ marginBottom: space.sm }}>
                <div style={{ display: "flex", alignItems: "center", gap: space.sm }}>
                  <Icon name={ICON[s.source_type] || "file"} size={16} tone="muted" />
                  <b style={{ flex: 1, color: color.heading, fontSize: 13.5 }}>{s.title}</b>
                  <Badge kind={st.kind}>{st.label}</Badge>
                </div>
                <div style={{ ...font.small, marginTop: 6 }}>
                  доверие {s.trust}% · фактов {s.facts} · подтв. {s.confirmed}
                  {s.conflicts ? " · конфл. " + s.conflicts : ""}
                  {s.last_parsed_at ? " · " + s.last_parsed_at : ""}
                </div>
              </Card>
            );
          })}
        </div>
      ) : srcs.length ? (
        <Table head={["Источник", "Статус", "Доверие", "Фактов", "Подтв.", "Конфл.", "Разобран"]}
               rows={srcs.map((s) => {
                 const st = ST[s.status] || ST.connected;
                 return [
                   <span key="t" style={{ display: "flex", gap: 8, alignItems: "center" }}>
                     <Icon name={ICON[s.source_type] || "file"} size={16} tone="muted" />
                     <b style={{ color: color.heading }}>{s.title}</b>
                   </span>,
                   <Badge key="s" kind={st.kind}>{st.label}</Badge>,
                   s.trust + "%", s.facts, s.confirmed, s.conflicts,
                   s.last_parsed_at || "—",
                 ];
               })} />
      ) : (
        <EmptyState title="Источники ещё не описаны"
                    hint="Нажмите «Обновить источники» — BORIS найдёт то, что уже подключено."
                    action={<Button disabled={busy || !acc}
                      onClick={() => post("/api/memory/sources/sync", { account_id: acc }, "Готово")}>
                      Обновить источники
                    </Button>} />
      )}

      {advice.length ? (
        <Card style={{ marginTop: space.lg }}>
          <div style={font.h3 as React.CSSProperties}>Что подключить дальше</div>
          {advice.map((a, i) => (
            <div key={i} style={{ display: "flex", gap: space.md, alignItems: "center",
                                  padding: "10px 0",
                                  borderTop: i ? "1px solid " + color.line : "none" }}>
              <Icon name={ICON[a.type] || "plus"} size={18} tone="accent" />
              <div style={{ flex: 1 }}>
                <div style={font.bodyStrong as React.CSSProperties}>{a.title}</div>
                <div style={font.small as React.CSSProperties}>{a.why}</div>
              </div>
              <Badge kind="neutral">скоро</Badge>
            </div>
          ))}
        </Card>
      ) : null}
    </Page>
  );
}
