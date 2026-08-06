"use client";
import { useEffect, useState, useCallback } from "react";
import { apiGet, apiPost } from "../lib/api";

type Money = { price: number | null; deposit: number | null; delivery: number | null; rent: number | null };
type Card = {
  id: number; account_id: string; chat: string; title: string | null; reason: string;
  status: string; age_days: number; score: number; confidence: number | null;
  summary: string | null; phone_received: boolean; queue: string;
  message_id: number | null; draft: string | null; money: Money | null;
};
type QInfo = { title: string; count: number; amount: number; overdue_14: number; overdue_30: number };
type Detail = Card & {
  history: { id: number; from: string; at: string; text: string }[];
  evidence: number[] | null;
  events: { event: string; at: string }[];
};

const TABS = [
  { key: "hot", label: "Клиенты ждут ответа", dot: "#DC2626" },
  { key: "late", label: "Просроченные обязательства", dot: "#F59E0B" },
  { key: "reactivation", label: "Вернуть потерянных", dot: "#2563EB" },
];

const REASON_RU: Record<string, string> = {
  seller_action_missing: "мы не выполнили обещание",
  price_requested: "спросил цену и пропал",
  estimate_sent: "получил расчёт и замолчал",
  no_reply: "перестал отвечать",
  asked_to_contact_later: "просил написать позже",
  other: "другое",
};

function rub(n: number | null | undefined) {
  if (!n) return null;
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " \u20bd";
}

function moneyLine(m: Money | null) {
  if (!m) return "цена не определена";
  const parts: string[] = [];
  if (m.price) parts.push("цена " + rub(m.price));
  if (m.rent && m.rent !== m.price) parts.push("аренда " + rub(m.rent));
  if (m.deposit) parts.push("залог " + rub(m.deposit));
  if (m.delivery) parts.push("доставка " + rub(m.delivery));
  return parts.length ? parts.join(" · ") : "цена не определена";
}

export default function ReactivationPage() {
  const [queues, setQueues] = useState<Record<string, QInfo>>({});
  const [tab, setTab] = useState("hot");
  const [items, setItems] = useState<Card[]>([]);
  const [open, setOpen] = useState<Detail | null>(null);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<any>(null);
  const [err, setErr] = useState("");

  const loadQueues = useCallback(async () => {
    try {
      const d = await apiGet("/api/reactivation/queues");
      setQueues(d || {});
    } catch (e: any) { setErr(String(e?.message || e)); }
  }, []);

  const loadItems = useCallback(async (q: string) => {
    try {
      const d = await apiGet("/api/reactivation/candidates?queue=" + q);
      setItems((d && d.items) || []);
    } catch (e: any) { setErr(String(e?.message || e)); }
  }, []);

  useEffect(() => {
    apiGet("/api/reactivation/health").then(setHealth).catch(() => {});
  }, []);
  useEffect(() => { loadQueues(); }, [loadQueues]);
  useEffect(() => { loadItems(tab); setOpen(null); }, [tab, loadItems]);

  async function openCard(id: number) {
    setErr("");
    try { setOpen(await apiGet("/api/reactivation/candidate/" + id)); }
    catch (e: any) { setErr(String(e?.message || e)); }
  }

  async function act(id: number, what: string) {
    setBusy(true); setErr("");
    try {
      await apiPost("/api/reactivation/candidate/" + id + "/" + what, {});
      setOpen(null);
      await loadItems(tab); await loadQueues();
    } catch (e: any) { setErr(String(e?.message || e)); }
    setBusy(false);
  }

  const S = {
    wrap: { padding: 24, maxWidth: 1180, margin: "0 auto" } as any,
    h1: { fontSize: 24, fontWeight: 800, margin: "0 0 4px" } as any,
    sub: { color: "#6B7280", fontSize: 14, margin: "0 0 20px" } as any,
    tabs: { display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 20 } as any,
    tab: (on: boolean) => ({
      display: "flex", alignItems: "center", gap: 8, padding: "12px 16px",
      border: "1px solid " + (on ? "#1A56DB" : "#E3E7F0"), borderRadius: 14,
      background: on ? "#EFF4FF" : "#fff", cursor: "pointer", textAlign: "left",
    }) as any,
    dot: (c: string) => ({ width: 10, height: 10, borderRadius: 5, background: c }) as any,
    card: {
      border: "1px solid #E3E7F0", borderRadius: 16, background: "#fff",
      padding: 16, marginBottom: 12, cursor: "pointer",
    } as any,
    money: { color: "#047857", fontWeight: 700, fontSize: 14 } as any,
    meta: { color: "#6B7280", fontSize: 13 } as any,
    btn: (kind: string) => ({
      padding: "10px 14px", borderRadius: 10, border: "1px solid #E3E7F0", cursor: "pointer",
      background: kind === "main" ? "#1A56DB" : "#fff", color: kind === "main" ? "#fff" : "#111827",
      fontWeight: 600, marginRight: 8,
    }) as any,
    modal: {
      position: "fixed", inset: 0, background: "rgba(15,23,42,.45)", display: "flex",
      alignItems: "center", justifyContent: "center", padding: 20, zIndex: 50,
    } as any,
    sheet: {
      background: "#fff", borderRadius: 18, maxWidth: 760, width: "100%",
      maxHeight: "86vh", overflowY: "auto", padding: 24,
    } as any,
    msg: (mine: boolean) => ({
      background: mine ? "#EFF4FF" : "#F6F7F9", borderRadius: 12, padding: "8px 12px",
      margin: "6px 0", fontSize: 14,
    }) as any,
  };

  return (
    <div style={S.wrap}>
      <h1 style={S.h1}>Возврат клиентов</h1>
      <p style={S.sub}>
        БОРИС читает каждую переписку, находит тех, кто ждёт ответа или молча ушёл,
        и готовит продолжение разговора. Отправка выключена — сообщения уходят только после вашего решения.
      </p>

      {err ? (
        <div style={{ background: "#FEF2F2", border: "1px solid #FECACA", color: "#991B1B",
          padding: 12, borderRadius: 12, marginBottom: 16 }}>{err}</div>
      ) : null}

      {health && health.score != null ? (
        <div style={{
          border: "1px solid #E3E7F0", borderRadius: 16, padding: 16, marginBottom: 20,
          background: health.light === "red" ? "#FEF2F2"
            : health.light === "yellow" ? "#FFFBEB" : "#F0FDF4",
        }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <span style={{ fontSize: 26 }}>
              {health.light === "red" ? "\u{1F534}" : health.light === "yellow" ? "\u{1F7E1}" : "\u{1F7E2}"}
            </span>
            <span style={{ fontSize: 22, fontWeight: 800 }}>{health.score}/100</span>
            <span style={{ color: "#6B7280" }}>здоровье отдела продаж</span>
          </div>
          {health.parts ? (
            <div style={{ ...S.meta, marginTop: 6 }}>
              снижение:{" "}
              {Object.entries(health.parts)
                .filter(([, v]: any) => v)
                .map(([k, v]: any) => k + " \u2212" + v)
                .join(" · ") || "нет"}
            </div>
          ) : null}
          {health.todo && health.todo.length ? (
            <div style={{ marginTop: 8, fontSize: 14 }}>
              <b>Чтобы поднять:</b> {health.todo.join("; ")}
            </div>
          ) : null}
        </div>
      ) : null}

      <div style={S.tabs}>
        {TABS.map((t) => {
          const q = queues[t.key];
          return (
            <button key={t.key} style={S.tab(tab === t.key)} onClick={() => setTab(t.key)}>
              <span style={S.dot(t.dot)} />
              <span>
                <div style={{ fontWeight: 700 }}>{t.label}</div>
                <div style={S.meta}>
                  {q ? q.count + " задач" : "—"}
                  {q && q.amount ? " · " + rub(q.amount) : ""}
                  {q && q.overdue_30 ? " · 30+ дней: " + q.overdue_30 : ""}
                </div>
              </span>
            </button>
          );
        })}
      </div>

      {items.length === 0 ? (
        <div style={{ ...S.card, cursor: "default", color: "#6B7280" }}>
          Здесь пусто — значит по этой очереди сейчас ничего не требуется.
        </div>
      ) : null}

      {items.map((x) => (
        <div key={x.id} style={S.card} onClick={() => openCard(x.id)}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            <div style={{ fontWeight: 700 }}>{x.title || "Без объявления"}</div>
            <div style={S.meta}>{x.age_days} дн</div>
          </div>
          <div style={S.money}>{moneyLine(x.money)}</div>
          <div style={{ margin: "6px 0", fontSize: 14 }}>{x.summary || ""}</div>
          <div style={S.meta}>
            {REASON_RU[x.reason] || x.reason} · {x.account_id}
            {x.draft ? " · черновик готов" : ""}
            {x.phone_received ? " · телефон есть" : ""}
          </div>
        </div>
      ))}

      {open ? (
        <div style={S.modal} onClick={() => setOpen(null)}>
          <div style={S.sheet} onClick={(e) => e.stopPropagation()}>
            <h2 style={{ margin: "0 0 4px", fontSize: 20 }}>{open.title || "Без объявления"}</h2>
            <div style={S.meta}>
              {open.account_id} · ждёт {open.age_days} дн · {REASON_RU[open.reason] || open.reason}
            </div>
            <div style={{ ...S.money, margin: "10px 0" }}>{moneyLine(open.money)}</div>
            <p style={{ fontSize: 15 }}>{open.summary}</p>

            <h3 style={{ fontSize: 15, margin: "18px 0 6px" }}>Переписка</h3>
            {(open.history || []).map((m) => (
              <div key={m.id} style={S.msg(m.from !== "клиент")}>
                <b>{m.from}:</b> {m.text}
              </div>
            ))}

            {open.draft ? (
              <>
                <h3 style={{ fontSize: 15, margin: "18px 0 6px" }}>Предлагаемое сообщение</h3>
                <div style={{ border: "1px solid #E3E7F0", borderRadius: 12, padding: 12,
                  background: "#FAFBFC", fontSize: 14 }}>{open.draft}</div>
                <div style={{ ...S.meta, marginTop: 6 }}>
                  Отправка недоступна: сообщение показывается для проверки.
                </div>
              </>
            ) : null}

            <div style={{ marginTop: 20 }}>
              <button style={S.btn("main")} disabled={busy}
                onClick={() => act(open.id, "take")}>Взять в работу</button>
              <button style={S.btn("")} disabled={busy}
                onClick={() => act(open.id, "exclude")}>Исключить</button>
              <button style={S.btn("")} disabled={busy}
                onClick={() => act(open.id, "dnc")}>Больше не писать</button>
              <button style={S.btn("")} onClick={() => setOpen(null)}>Закрыть</button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
