"use client";

import { useEffect, useState } from "react";
import {
  Page, Card, Button, Input, Badge, Alert, EmptyState, Kpi, KpiRow, Icon,
} from "../ui";
import { color, space, font, radius } from "../ui/tokens";
import AppShell from "../components/shell/AppShell";
import { useAccounts } from "../lib/AccountContext";

type Fact = {
  id: number; stage: number; stage_label: string; usage: string; date: string;
  aliases: string[]; type: string; category: string; name: string; value: string;
  source: string; confidence: number; status: string; confirmed_by: string;
  requires_confirmation?: boolean;
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

const CATEGORY_RU: Record<string, string> = {
  campaign: "Кампания",
  campaign_price_benchmark: "Цены и ориентиры",
  campaign_reference: "Правила кампании",
  company: "Компания",
  rule: "Правила",
  service: "Услуги",
};

const TYPE_RU: Record<string, string> = {
  campaign: "Кампания",
  photo_rule: "Правило для фото",
  price_rule: "Правило цены",
  attribute_rule: "Правило параметров",
  campaign_price_benchmark: "Ценовой ориентир",
  campaign_reference: "Правило кампании",
  company: "Компания",
  rule: "Правило",
  service: "Услуга",
};

function categoryLabel(value: string) {
  return CATEGORY_RU[value] || "Прочее";
}
function typeLabel(value: string) {
  return TYPE_RU[value] || CATEGORY_RU[value] || "Знание";
}
const NAME_RU: Record<string, string> = {
  attribute_rule: "Правила заполнения параметров",
  directions: "Направления бизнеса",
  matrix: "Матрица объявлений",
  photo_rule: "Правила работы с фотографиями",
  price_rule: "Правила указания цены",
  bathroom: "Ванные и мебель для ванной", hallway: "Прихожие", kitchen: "Кухни", wardrobe: "Гардеробные",
  example_configuration_policy: "Правила примерной комплектации",
  brand: "Бренд", cities: "Города работы", experience: "Опыт", factories: "Производство", format: "Формат работы", order_segment: "Тип заказов", website: "Сайт",
  availability: "Доступность", pickup: "Самовывоз", assembly: "Сборка", design: "Дизайн", hardware: "Фурнитура", hardware_blum_capability: "Фурнитура Blum",
  material_ldsp_e1: "ЛДСП класса E1", materials: "Материалы", measurement: "Замер", production: "Производство", quality: "Качество", service_center: "Сервисный центр", support: "Поддержка",
};
function factNameLabel(value: string) { return NAME_RU[value] || (/^[a-z0-9_]+$/i.test(value || "") ? "Дополнительное правило" : value); }

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
  const { selectedAccountId } = useAccounts();
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
    const a = selectedAccountId || localStorage.getItem("boris_currentAccount") || "";
    setAcc(a);
    if (a) void load(a);
  }, [selectedAccountId]);

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
    <AppShell
      active="memory"
      title="База знаний компании"
      subtitle="Подтверждённые знания, которыми BORIS имеет право пользоваться в ответах клиентам."
      headerExtra={<>
            <Button kind="ghost" onClick={() => load(acc)} disabled={busy || !acc}>Обновить</Button>
            <Button kind="ghost" disabled={busy || !acc}
                    onClick={() => post("/api/memory/extract", { account_id: acc }, "Готово")}>
              Перечитать источники
            </Button>
          </>}
    >
    <Page className="boris-memory-reference-page">
      <section className="boris-reference-page-head boris-memory-page-head">
        <div className="boris-reference-kicker">БАЗА ЗНАНИЙ</div>
        <h1>Что BORIS знает о компании</h1>
        <p>Подтверждённые факты становятся единым источником правды для МОП, рекламы, аналитики и контента.</p>
      </section>

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
              <b data-boris-g32b-page="memory">Было → стало.</b> Подтверждено {was.confirmed} → <b>{now.confirmed}</b>,
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
              <div className="boris-f3-6253876a649a63">
                <div style={{ ...font.body, color: color.heading }}>{q.question}</div>
                {q.answer ? (
                  <div style={{ ...font.small, marginTop: 3 }}>Ответ: {q.answer.slice(0, 140)}</div>
                ) : (
                  <div style={{ ...font.small, color: color.orange, marginTop: 3 }}>
                    Готового ответа нет — BORIS ответит «уточню и вернусь»
                  </div>
                )}
                {editId === q.fact_id && q.fact_id ? <div className="boris-memory-inline-edit">
                  <Input value={editVal} onChange={(e) => setEditVal(e.target.value)} />
                  <Button disabled={busy || !editVal.trim()} onClick={() => post("/api/memory/edit", { account_id: acc, fact_id: q.fact_id, value: editVal }, "Сохранено")}>Сохранить в память BORIS</Button>
                  <Button kind="ghost" onClick={() => setEditId(null)}>Отмена</Button>
                </div> : null}
              </div>
              <div className="boris-memory-question-actions">
                {q.fact_id && !q.confirmed ? (
                  <Button disabled={busy}
                          onClick={() => post("/api/memory/confirm",
                            { account_id: acc, fact_id: q.fact_id }, "Подтверждено")}>
                    Верно
                  </Button>
                ) : q.confirmed ? <Badge kind="success">подтверждён</Badge> : null}
                {q.fact_id ? <Button kind="ghost" disabled={busy}
                  onClick={() => { setEditId(q.fact_id); setEditVal(q.answer || facts.find((f) => f.id === q.fact_id)?.value || ""); }}>
                  Изменить ответ
                </Button> : null}
              </div>
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
            <div data-boris-g32b-card="1" key={f.id} style={{ marginTop: space.md, padding: space.lg,
                                     borderRadius: radius.md, background: color.redSoft }}>
              <div style={{ fontSize: 20, fontWeight: 700, color: color.heading }}>{f.value} ₽</div>
              <div style={{ ...font.small, marginTop: 3 }}>{f.source} · {factNameLabel(f.name)}</div>
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
            {categoryLabel(c)}
          </Button>
        ))}
      </div>

      {shown.map((f) => (
        <Card key={f.id} padding={space.lg} style={{ marginTop: space.md }}>
          <div style={{ display: "flex", alignItems: "center", gap: space.sm, flexWrap: "wrap" }}>
            <Badge>{typeLabel(f.type)}</Badge>
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

          <div style={{ ...font.small, marginTop: space.md }}>{factNameLabel(f.name)}</div>
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
              <Button disabled={busy}
                      kind={(f.requires_confirmation ?? (f.status !== "confirmed")) ? undefined : "ghost"}
                      style={{ minHeight: 44 }}
                      onClick={() => post("/api/memory/confirm",
                        { account_id: acc, fact_id: f.id }, "Подтверждено и сохранено в память BORIS")}>
                Подтверждаю
              </Button>
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
    </AppShell>
  );
}
