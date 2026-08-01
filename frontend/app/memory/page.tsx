"use client";

import { useEffect, useState } from "react";
import {
  Page, PageHeader, Card, Button, Input, Badge, Alert, EmptyState, Kpi, KpiRow, Icon,
} from "../ui";
import { Shell } from "../ui/Sidebar";
import { color, space, font, radius } from "../ui/tokens";

type Fact = {
  id: number; stage: number; stage_label: string; usage: string; date: string;
  aliases: string[]; type: string; category: string; name: string; value: string;
  source: string; confidence: number; status: string; confirmed_by: string;
};
type Summary = {
  items: number; dialogs: number; facts: number; confirmed: number; usable: number;
  conflicts: number; answers: number; questions: number;
  covered_strong: number; covered_all: number;
};
type Snap = {
  facts: number; confirmed: number; usable: number; questions: number;
  covered_strong: number; covered_all: number; date?: string;
};
type TopQ = { question: string; count: number; fact_id: number; answer: string; confirmed: boolean };

const STAGES = ["Найдено", "Требует подтверждения", "Подтверждено", "Используется"];

function api(path: string, init?: RequestInit) {
  const token =
    typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return fetch(path, {
    ...(init || {}),
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + token,
      ...((init && (init.headers as Record<string, string>)) || {}),
    },
  });
}

export default function KnowledgePage() {
  const [acc, setAcc] = useState("");
  const [sum, setSum] = useState<Summary | null>(null);
  const [was, setWas] = useState<Snap | null>(null);
  const [now, setNow] = useState<Snap | null>(null);
  const [facts, setFacts] = useState<Fact[]>([]);
  const [conf, setConf] = useState<Fact[]>([]);
  const [listed, setListed] = useState<string[]>([]);
  const [top, setTop] = useState<TopQ[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState(false);
  const [filter, setFilter] = useState("");
  const [editId, setEditId] = useState<number | null>(null);
  const [editVal, setEditVal] = useState("");
  const [custom, setCustom] = useState("");

  useEffect(() => {
    const a = localStorage.getItem("boris_currentAccount") || "";
    setAcc(a);
    if (a) load(a);
  }, []);

  async function load(a: string) {
    if (!a) return;
    const q = "account_id=" + encodeURIComponent(a);
    try {
      const [s, f, c, p, t] = await Promise.all([
        api("/api/memory/summary?" + q), api("/api/memory/facts?" + q),
        api("/api/memory/conflicts?" + q), api("/api/memory/progress?" + q),
        api("/api/memory/top_questions?" + q + "&limit=10"),
      ]);
      if (s.ok) setSum(await s.json());
      if (f.ok) setFacts((await f.json()).facts || []);
      if (c.ok) { const d = await c.json(); setConf(d.conflicts || []); setListed(d.listed_prices || []); }
      if (p.ok) { const d = await p.json(); setWas(d.was || null); setNow(d.now || null); }
      if (t.ok) setTop((await t.json()).questions || []);
    } catch { setErr(true); setNote("Не удалось загрузить базу знаний"); }
  }

  async function post(path: string, body: unknown, fallback: string) {
    setBusy(true); setNote(""); setErr(false);
    try {
      const r = await api(path, { method: "POST", body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      setErr(!r.ok);
      setNote(d.message || d.detail || fallback);
      setEditId(null);
      await load(acc);
    } catch { setErr(true); setNote("Ошибка сети"); }
    setBusy(false);
  }

  const shown = filter ? facts.filter((f) => f.category === filter) : facts;
  const cats = Array.from(new Set(facts.map((f) => f.category)));
  const grew = was && now ? now.covered_strong - was.covered_strong : 0;

  return (
    <Shell activeKey="memory">
    <Page>
      <PageHeader
        title="База знаний компании"
        subtitle="Подтверждённые знания, которыми BORIS имеет право пользоваться в ответах клиентам."
        actions={
          <>
            <Input value={acc} onChange={(e) => setAcc(e.target.value)}
                   placeholder="account_id" style={{ width: 260 }} />
            <Button kind="ghost" onClick={() => load(acc)} disabled={busy}>Показать</Button>
            <Button kind="ghost" disabled={busy || !acc}
                    onClick={() => post("/api/memory/extract", { account_id: acc }, "Готово")}>
              Перечитать источники
            </Button>
          </>
        }
      />

      {note ? <Alert kind={err ? "danger" : "success"}>{note}</Alert> : null}

      {sum ? (
        <>
          <KpiRow>
            <Kpi value={sum.items} label="объявлений" />
            <Kpi value={sum.dialogs} label="диалогов" />
            <Kpi value={sum.facts} label="найдено фактов" />
            <Kpi value={sum.confirmed} label="подтверждено" kind="success" />
            <Kpi value={sum.conflicts} label="спорных"
                 kind={sum.conflicts ? "danger" : "neutral"} />
            <Kpi value={sum.usable} label="в надёжном пуле" kind="info" />
          </KpiRow>
          {was && now ? (
            <Alert kind={grew > 0 ? "success" : "info"}>
              <b>Было → стало.</b> Подтверждено {was.confirmed} → <b>{now.confirmed}</b>,
              надёжных фактов {was.usable} → <b>{now.usable}</b>, вопросов закрывается
              надёжно {was.covered_strong} → <b>{now.covered_strong}</b> из {now.questions}
              {grew > 0 ? <b> (+{grew})</b> : null}
              {was.date ? " · отсчёт от " + was.date : ""}
            </Alert>
          ) : null}
        </>
      ) : null}

      {top.length ? (
        <Card style={{ marginTop: space.lg }}>
          <div style={font.h3 as React.CSSProperties}>Что чаще всего спрашивают</div>
          {top.map((q, i) => (
            <div key={i} style={{ display: "flex", gap: space.md, alignItems: "flex-start",
                                  padding: "11px 0",
                                  borderTop: i ? "1px solid " + color.line : "none" }}>
              <div style={{ minWidth: 44, ...font.bodyStrong, color: color.blue }}>×{q.count}</div>
              <div style={{ flex: 1 }}>
                <div style={{ ...font.body, color: color.heading }}>{q.question}</div>
                {q.answer ? (
                  <div style={{ ...font.small, marginTop: 3 }}>Ответ: {q.answer.slice(0, 140)}</div>
                ) : (
                  <div style={{ ...font.small, color: color.orange, marginTop: 3 }}>
                    Готового ответа нет — BORIS ответит «уточню и вернусь»
                  </div>
                )}
              </div>
              {q.fact_id && !q.confirmed ? (
                <Button disabled={busy}
                        onClick={() => post("/api/memory/confirm",
                          { account_id: acc, fact_id: q.fact_id }, "Подтверждено")}>
                  Верно
                </Button>
              ) : q.confirmed ? <Badge kind="success">подтверждён</Badge> : null}
            </div>
          ))}
        </Card>
      ) : null}

      {conf.length ? (
        <Card style={{ marginTop: space.lg }}>
          <div style={{ display: "flex", alignItems: "center", gap: space.sm }}>
            <Icon name="alert" tone="danger" />
            <div style={{ ...font.h3, color: color.red }}>
              Найдены разные цены — какую считать актуальной?
            </div>
          </div>
          {listed.length ? (
            <div style={{ ...font.small, marginTop: space.sm }}>
              В объявлениях: {listed.join(" · ")} ₽
            </div>
          ) : null}
          {conf.map((f) => (
            <div key={f.id} style={{ marginTop: space.md, padding: space.lg,
                                     borderRadius: radius.md, background: color.redSoft }}>
              <div style={{ fontSize: 20, fontWeight: 700, color: color.heading }}>{f.value} ₽</div>
              <div style={{ ...font.small, marginTop: 3 }}>{f.source} · {f.name}</div>
              <Button style={{ marginTop: space.md }} disabled={busy}
                      onClick={() => post("/api/memory/resolve",
                        { account_id: acc, fact_id: f.id, mode: "pick" }, "Готово")}>
                Эта цена актуальна
              </Button>
            </div>
          ))}
          <div style={{ display: "flex", gap: space.sm, marginTop: space.md, flexWrap: "wrap" }}>
            <Input value={custom} onChange={(e) => setCustom(e.target.value)}
                   placeholder="или своя цена / диапазон" style={{ width: 260 }} />
            <Button kind="ghost" disabled={busy || !custom}
                    onClick={() => post("/api/memory/resolve",
                      { account_id: acc, mode: "new", value: custom }, "Готово")}>
              Указать новую
            </Button>
            <Button kind="ghost" disabled={busy || !custom}
                    onClick={() => post("/api/memory/resolve",
                      { account_id: acc, mode: "range", value: custom }, "Готово")}>
              Оставить диапазоном
            </Button>
          </div>
        </Card>
      ) : null}

      <div style={{ display: "flex", gap: space.sm, marginTop: space.xl, flexWrap: "wrap" }}>
        <Button kind={filter ? "ghost" : "secondary"} onClick={() => setFilter("")}>
          Все · {facts.length}
        </Button>
        {cats.map((c) => (
          <Button key={c} kind={filter === c ? "secondary" : "ghost"} onClick={() => setFilter(c)}>
            {(facts.find((f) => f.category === c) || { type: c }).type}
          </Button>
        ))}
      </div>

      {shown.map((f) => (
        <Card key={f.id} padding={space.lg} style={{ marginTop: space.md }}>
          <div style={{ display: "flex", alignItems: "center", gap: space.sm, flexWrap: "wrap" }}>
            <Badge>{f.type}</Badge>
            <span style={font.small as React.CSSProperties}>достоверность {f.confidence}%</span>
            {f.date ? <span style={font.small as React.CSSProperties}>· {f.date}</span> : null}
          </div>

          <div style={{ display: "flex", gap: 6, marginTop: space.md, flexWrap: "wrap" }}>
            {STAGES.map((s, i) => (
              <Badge key={s}
                     kind={i + 1 <= f.stage ? (f.stage >= 3 ? "success" : "info") : "neutral"}>
                {i + 1}. {s}
              </Badge>
            ))}
          </div>

          <div style={{ ...font.small, marginTop: space.md }}>{f.name}</div>
          <div style={{ ...font.bodyStrong, fontSize: 15, marginTop: 4, whiteSpace: "pre-wrap" }}>
            {f.value}
          </div>
          {f.aliases && f.aliases.length ? (
            <div style={{ ...font.small, marginTop: 6 }}>Также называют: {f.aliases.join(" · ")}</div>
          ) : null}
          <div style={{ ...font.small, marginTop: 6 }}>
            Источник: {f.source} · Где используется: {f.usage}
            {f.confirmed_by ? " · подтвердил " + f.confirmed_by : ""}
          </div>

          {editId === f.id ? (
            <div style={{ display: "flex", gap: space.sm, marginTop: space.md, flexWrap: "wrap" }}>
              <Input value={editVal} onChange={(e) => setEditVal(e.target.value)}
                     style={{ flex: "1 1 340px" }} />
              <Button disabled={busy || !editVal}
                      onClick={() => post("/api/memory/edit",
                        { account_id: acc, fact_id: f.id, value: editVal }, "Сохранено")}>
                Сохранить
              </Button>
              <Button kind="ghost" onClick={() => setEditId(null)}>Отмена</Button>
            </div>
          ) : (
            <div style={{ display: "flex", gap: space.sm, marginTop: space.md, flexWrap: "wrap" }}>
              {f.status !== "confirmed" ? (
                <Button disabled={busy}
                        onClick={() => post("/api/memory/confirm",
                          { account_id: acc, fact_id: f.id }, "Подтверждено")}>
                  Верно
                </Button>
              ) : null}
              <Button kind="ghost" disabled={busy}
                      onClick={() => { setEditId(f.id); setEditVal(f.value); }}>
                Изменить
              </Button>
              <Button kind="danger" disabled={busy}
                      onClick={() => post("/api/memory/reject",
                        { account_id: acc, fact_id: f.id }, "Удалено")}>
                Удалить
              </Button>
            </div>
          )}
        </Card>
      ))}

      {!shown.length ? (
        <div style={{ marginTop: space.lg }}>
          <EmptyState title="Фактов пока нет"
                      hint="Укажите аккаунт и нажмите «Перечитать источники» — BORIS соберёт знания из объявлений и переписок."
                      action={<Button disabled={busy || !acc}
                        onClick={() => post("/api/memory/extract", { account_id: acc }, "Готово")}>
                        Перечитать источники
                      </Button>} />
        </div>
      ) : null}
    </Page>
    </Shell>
  );
}
