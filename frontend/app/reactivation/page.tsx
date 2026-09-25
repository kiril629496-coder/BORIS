"use client";
import { useEffect, useState, useCallback } from "react";

import { apiGet, apiPost, apiFetch } from "../lib/api";
import { useAccounts } from "../lib/AccountContext";

type Money = { price: number | null; deposit: number | null; delivery: number | null; rent: number | null };
type Card = {
  id: number; account_id: string; chat: string; title: string | null; reason: string;
  status: string; age_days: number; score: number; confidence: number | null;
  summary: string | null; phone_received: boolean; queue: string; lead_temperature?: string | null; last_contact_at?: string | null; recommendation?: string | null;
  intent?: string | null; qualification_stage?: string | null; target_action?: string | null; next_action?: string | null; had_calculation?: boolean;
  message_id: number | null; draft: string | null; money: Money | null;
  message_status?: string | null; message_error_code?: string | null; message_error?: string | null; attempt_number?: number | null;
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
  const { selectedAccountId } = useAccounts();
  const [scopedAccount, setScopedAccount] = useState("");
  const [settings, setSettings] = useState<any>(null);
  const [draftText, setDraftText] = useState("");
  const [scopedChat, setScopedChat] = useState("");
  const [queues, setQueues] = useState<Record<string, any>>({});
  const [tab, setTab] = useState("hot");
  const [items, setItems] = useState<Card[]>([]);
  const [open, setOpen] = useState<Detail | null>(null);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<any>(null);
  const [accountStatus, setAccountStatus] = useState<any>(null);
  const [operationalHealth, setOperationalHealth] = useState<any>(null);
  const [err, setErr] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkInfo, setBulkInfo] = useState("");
  const [bulkPreview, setBulkPreview] = useState<{sendable:number;blocked:number;results:any[]} | null>(null);
  const [segment, setSegment] = useState("all");
  const [workbench, setWorkbench] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setScopedAccount(params.get("account_id") || selectedAccountId || "");
    setScopedChat(params.get("chat_id") || "");
    const queue = params.get("queue");
    if (queue && TABS.some(x => x.key === queue)) { setTab(queue); setWorkbench(true); setScopedChat(""); }
  }, [selectedAccountId]);

  const loadQueues = useCallback(async () => {
    try {
      const d = await apiGet("/api/reactivation/queues" + (scopedAccount ? "?account_id=" + encodeURIComponent(scopedAccount) : ""));
      setQueues(d || {});
    } catch (e: any) { setErr(String(e?.message || e)); }
  }, [scopedAccount]);

  const loadItems = useCallback(async (q: string) => {
    try {
      const d = await apiGet("/api/reactivation/candidates?queue=" + q + (scopedAccount ? "&account_id=" + encodeURIComponent(scopedAccount) : ""));
      const rows = (d && d.items) || [];
      const scoped = rows.filter((item: Card) =>
        (!scopedAccount || item.account_id === scopedAccount) &&
        (!scopedChat || item.chat === scopedChat)
      );
      setItems((scopedAccount || scopedChat) ? scoped : rows);
    } catch (e: any) { setErr(String(e?.message || e)); }
  }, [scopedAccount, scopedChat]);

  useEffect(() => {
    apiGet("/api/reactivation/health" + (scopedAccount ? "?account_id=" + encodeURIComponent(scopedAccount) : "")).then(setHealth).catch(() => {});
    apiGet("/api/reactivation/account-status" + (scopedAccount ? "?account_id=" + encodeURIComponent(scopedAccount) : "")).then(setAccountStatus).catch(() => setAccountStatus(null));
    apiGet("/api/reactivation/operational-health" + (scopedAccount ? "?account_id=" + encodeURIComponent(scopedAccount) : "")).then(setOperationalHealth).catch(() => setOperationalHealth(null));
  }, [scopedAccount]);
  useEffect(() => { loadQueues(); }, [loadQueues]);
  useEffect(() => { loadItems(tab); setOpen(null); setSelected(new Set()); setBulkInfo(""); }, [tab, loadItems]);
  function openQueue(key: string) {
    setScopedChat("");
    setTab(key); setWorkbench(true); setOpen(null); setBulkPreview(null);
    const u = new URL(window.location.href); u.searchParams.delete("chat_id"); u.searchParams.set("queue", key); window.history.replaceState({}, "", u.toString());
  }

  async function openCard(id: number) {
    setErr("");
    try {
      const d = await apiGet("/api/reactivation/candidate/" + id);
      setOpen(d);
      setDraftText(d?.draft || "");
    }
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

  useEffect(() => {
    if (!scopedAccount) { setSettings(null); return; }
    apiGet("/api/reactivation/settings?account_id=" + encodeURIComponent(scopedAccount))
      .then(setSettings).catch(() => setSettings(null));
  }, [scopedAccount]);

  async function saveMode(mode: "recommend" | "auto") {
    if (!scopedAccount || !settings) return;
    setBusy(true); setErr("");
    try {
      const body = { ...settings, mode };
      const r = await apiFetch("/api/reactivation/settings?account_id=" + encodeURIComponent(scopedAccount), {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await r.text());
      setSettings({ ...settings, mode });
      await loadQueues();
    } catch (e: any) { setErr(String(e?.message || e)); }
    setBusy(false);
  }

  async function generateDraft() {
    if (!open) return;
    setBusy(true); setErr("");
    try {
      await apiPost("/api/reactivation/candidate/" + open.id + "/generate", {});
      await openCard(open.id);
      await loadItems(tab);
    } catch (e: any) { setErr(String(e?.message || e)); }
    setBusy(false);
  }

  async function saveDraft() {
    if (!open?.message_id || !draftText.trim()) return;
    setBusy(true); setErr("");
    try {
      await apiPost("/api/reactivation/message/" + open.message_id + "/text", { text: draftText.trim() });
      await openCard(open.id);
    } catch (e: any) { setErr(String(e?.message || e)); }
    setBusy(false);
  }

  async function sendDraft() {
    if (!open?.message_id) return;
    setBusy(true); setErr("");
    try {
      if (draftText.trim() && draftText.trim() !== (open.draft || "").trim()) {
        await apiPost("/api/reactivation/message/" + open.message_id + "/text", { text: draftText.trim() });
      }
      await apiPost("/api/reactivation/message/" + open.message_id + "/send", {});
      setOpen(null);
      await loadItems(tab); await loadQueues();
    } catch (e: any) { setErr(String(e?.message || e)); }
    setBusy(false);
  }


  function toggleSelected(id: number) {
    setBulkPreview(null);
    setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }
  const visibleItems = items.filter(x => segment === "all" || (segment === "hot" && (x.lead_temperature === "hot" || x.phone_received || x.had_calculation)) || (segment === "price" && x.reason === "price_requested") || (segment === "calc" && (x.reason === "estimate_sent" || x.had_calculation)) || (segment === "later" && x.reason === "asked_to_contact_later") || (segment === "30d" && x.age_days >= 30));
  function selectAllVisible() { setBulkPreview(null); setSelected(new Set(visibleItems.map(x => x.id))); }
  function selectRecommendedToday() {
    setBulkPreview(null);
    const cap = Math.max(0, Number((queues as any)?.remaining_today || 0));
    const rank = (x: Card) => (x.reason === "price_requested" ? 50 : x.reason === "estimate_sent" ? 45 : x.reason === "asked_to_contact_later" ? 40 : x.phone_received ? 35 : x.reason === "no_reply" ? 20 : 10) + Math.min(20, x.age_days) + Math.min(20, Number(x.score || 0));
    setSelected(new Set([...visibleItems].sort((a,b)=>rank(b)-rank(a)).slice(0, cap || 0).map(x=>x.id)));
    setBulkInfo(cap ? `Выбрано до дневного лимита: ${Math.min(cap, visibleItems.length)}. Перед отправкой обязательно выполните проверку.` : "Дневной лимит на сегодня исчерпан.");
  }
  function clearSelected() { setBulkPreview(null); setSelected(new Set()); }
  async function bulkPrepare() {
    if (!selected.size) return;
    setBusy(true); setErr(""); setBulkInfo("");
    try {
      const r = await apiPost("/api/reactivation/bulk/prepare", { candidate_ids: Array.from(selected) });
      setBulkPreview(null);
      setBulkInfo(`Подготовлено сообщений: ${r?.prepared ?? 0} из ${r?.requested ?? selected.size}`);
      await loadItems(tab);
    } catch (e: any) { setErr(String(e?.message || e)); }
    setBusy(false);
  }
  async function bulkPreflight() {
    if (!selected.size) return;
    setBusy(true); setErr(""); setBulkInfo("");
    try {
      const r = await apiPost("/api/reactivation/bulk/preflight", { candidate_ids: Array.from(selected) });
      setBulkPreview({sendable:r?.sendable ?? 0, blocked:r?.blocked ?? 0, results:r?.results ?? []});
      setBulkInfo(`Готовы к отправке сейчас: ${r?.sendable ?? 0}. Заблокированы проверками: ${r?.blocked ?? 0}.`);
    } catch (e: any) { setErr(String(e?.message || e)); }
    setBusy(false);
  }
  async function bulkSend() {
    if (!selected.size) return;
    if (!bulkPreview) { setErr("Сначала нажмите «Проверить перед отправкой». BORIS покажет, сколько диалогов реально можно отправить сейчас."); return; }
    const sendableIds = bulkPreview.results.filter((x:any) => x.ok).map((x:any) => x.candidate_id);
    if (!sendableIds.length) { setErr("Сейчас нет выбранных диалогов, прошедших проверку безопасности."); return; }
    const ok = window.confirm(`Отправить сообщения ${sendableIds.length} клиентам, прошедшим проверку? Заблокированные (${bulkPreview.blocked}) отправлены не будут.`);
    if (!ok) return;
    setBusy(true); setErr(""); setBulkInfo("");
    try {
      const r = await apiPost("/api/reactivation/bulk/send", { candidate_ids: sendableIds });
      setBulkInfo(`Отправлено: ${r?.sent ?? 0}. Не отправлено после повторной проверки: ${r?.failed ?? 0}.`);
      setSelected(new Set());
      setBulkPreview(null);
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
      border: "1px solid " + (on ? "#1A56DB" : "var(--boris-border)"), borderRadius: 14,
      background: on ? "#EFF4FF" : "#fff", cursor: "pointer", textAlign: "left",
    }) as any,
    dot: (c: string) => ({ width: 10, height: 10, borderRadius: 5, background: c }) as any,
    card: {
      border: "1px solid var(--boris-border)", borderRadius: 16, background: "#fff",
      padding: 16, marginBottom: 12, cursor: "pointer",
    } as any,
    money: { color: "#047857", fontWeight: 700, fontSize: 14 } as any,
    meta: { color: "#6B7280", fontSize: 13 } as any,
    btn: (kind: string) => ({
      padding: "10px 14px", borderRadius: 10, border: "1px solid var(--boris-border)", cursor: "pointer",
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
    <div className="boris-reactivation-reference" data-boris-g33="reactivation" style={S.wrap}>
      <h1 data-boris-g33-heading="1" style={S.h1}>Кого можно вернуть</h1>
      <p style={S.sub}>
        БОРИС читает переписки, находит тех, кого можно вернуть, готовит персональное продолжение разговора
        и либо показывает его вам перед отправкой, либо отправляет сам в режиме автопилота.
      </p>

      {scopedAccount && settings ? (
        <div data-boris-g33-card="1" style={{ ...S.card, cursor: "default", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontWeight: 800 }}>Режим реактивации</div>
            <div style={S.meta}>
              {settings.mode === "auto"
                ? "Автопилот: безопасные сообщения отправляются автоматически в разрешённые часы."
                : "С подтверждением: BORIS готовит сообщение, вы проверяете и нажимаете «Отправить»."}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button style={S.btn(settings.mode === "recommend" ? "main" : "")} disabled={busy}
              onClick={() => saveMode("recommend")}>С подтверждением</button>
            <button style={S.btn(settings.mode === "auto" ? "main" : "")} disabled={busy}
              onClick={() => saveMode("auto")}>Автопилот</button>
          </div>
        </div>
      ) : null}

      {operationalHealth?.accounts?.length ? (
        <div data-boris-g33-card="1" style={{...S.card,cursor:"default",marginBottom:16,background:operationalHealth.status==="degraded"?"#FEF2F2":operationalHealth.status==="waiting_external"?"#FFF7ED":"#F0FDF4"}}>
          <div style={{fontWeight:800}}>Автоконтроль реактивации: {operationalHealth.status==="ok"?"работает":operationalHealth.status==="waiting_external"?"ждёт Avito":"есть проблема"}</div>
          <div style={{...S.meta,marginTop:4}}>BORIS сам проверяет scheduler, доступ к Avito, зависшие отправки, ответы клиентов и восстановление после сбоев. Ручной перезапуск не требуется.</div>
          <div style={{...S.meta,marginTop:4}}>Последняя проверка scheduler: {operationalHealth.scheduler?.age_sec!=null ? `${operationalHealth.scheduler.age_sec} сек. назад` : "нет подтверждения"} · внутреннее состояние: {operationalHealth.scheduler?.state || "неизвестно"}</div>
          {Number(operationalHealth.degraded_accounts||0)>0 ? <div style={{fontSize:12,fontWeight:700,color:"#991B1B",marginTop:6}}>Требуют внимания BORIS: {operationalHealth.degraded_accounts}. Система не маскирует неопределённую доставку и не делает слепую повторную отправку.</div> : null}
          {Number(operationalHealth.waiting_external_accounts||0)>0 ? <div style={{fontSize:12,fontWeight:700,color:"#9A3412",marginTop:6}}>Внешнее ограничение Avito: {operationalHealth.waiting_external_accounts} аккаунт(а). BORIS продолжает автоматическую перепроверку и возобновит безопасные действия после восстановления доступа.</div> : null}
          {Number(operationalHealth.partial_external_accounts||0)>0 ? <div style={{fontSize:12,fontWeight:700,color:"#9A3412",marginTop:6}}>Частичное ограничение Avito: {operationalHealth.partial_external_accounts} аккаунт(а). Доступные чаты продолжают работать; недоступные BORIS удерживает без дублей и перепроверяет автоматически.</div> : null}
        </div>
      ) : null}

      {accountStatus?.accounts?.length ? (
        <div data-boris-g33-card="1" style={{...S.card,cursor:"default",marginBottom:16}}>
          <div style={{fontWeight:800,marginBottom:8}}>{scopedAccount ? "Статус реактивации аккаунта" : "Реактивация по всем аккаунтам"}</div>
          {!scopedAccount ? <div style={{...S.meta,marginBottom:10}}>Рабочие аккаунты показаны вместе. Выключенные, завершившие оплаченный период и исчерпавшие лимит не скрываются — BORIS показывает точную причину остановки.</div> : null}
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(250px,1fr))",gap:8}}>
            {accountStatus.accounts.map((a:any)=><div key={a.account_id} style={{border:"1px solid var(--boris-border)",borderRadius:12,padding:10,background:a.state==="ready"?"#F0FDF4":a.state==="ready_empty"?"#F8FAFC":"#FFF7ED"}}>
              <div style={{fontWeight:750}}>{a.name || a.account_id}</div>
              <div style={{fontSize:12,color:a.state==="ready"?"#047857":a.state==="ready_empty"?"#64748B":"#9A3412",marginTop:3}}>{a.reason}</div>
              <div style={{...S.meta,marginTop:4}}>{a.active_candidates} клиентов · подготовлено {a.ready_messages} · автопилот может отправить {a.auto_sendable || 0} · удержано {a.hold_total || 0} · сегодня {a.sent_today}/{a.daily_limit}</div>
              {Number(a.awaiting_revalidation||0)>0 ? <div style={{...S.meta,marginTop:3}}>BORIS сам перепроверяет перед автозапуском: {a.awaiting_revalidation}</div> : null}
              {Number(a.needs_review_candidates||0)>0 ? <div style={{...S.meta,marginTop:3}}>Не пускаются в автопилот из-за смысла диалога/низкой уверенности: {a.needs_review_candidates}</div> : null}
              {Number(a.legacy_requires_refresh||0)>0 ? <div style={{...S.meta,marginTop:3}}>Старые тексты без доказательства авто-безопасности: {a.legacy_requires_refresh} — BORIS удерживает их до безопасного обновления.</div> : null}
              {Number(a.ai_fallback_requires_confirmation||0)>0 ? <div style={{...S.meta,marginTop:3}}>Резервные тексты AI без права автоотправки: {a.ai_fallback_requires_confirmation}</div> : null}
              {Number(a.qualification_blocked||0)>0 ? <div style={{...S.meta,marginTop:3}}>Удержаны по текущей квалификации/передаче менеджеру: {a.qualification_blocked}</div> : null}
              {Number(a.customer_recontact_blocked||0)>0 ? <div style={{...S.meta,marginTop:3}}>Клиент сам обещал вернуться и написать — BORIS не догоняет: {a.customer_recontact_blocked}</div> : null}
              {Number(a.factual_safety_blocked||0)>0 ? <div style={{...S.meta,marginTop:3}}>Удержаны из-за неподтверждённого условия в тексте: {a.factual_safety_blocked}</div> : null}
              {Number(a.duplicate_blocked||0)>0 ? <div style={{...S.meta,marginTop:3}}>Заблокированы как точный повтор старого исходящего: {a.duplicate_blocked}</div> : null}
              {Number(a.recommend_mode_requires_confirmation||0)>0 ? <div style={{...S.meta,marginTop:3}}>Подготовлены безопасно, но аккаунт работает в режиме подтверждения: {a.recommend_mode_requires_confirmation}</div> : null}
              {a.send_window_open===false && Number(a.auto_sendable||0)>0 ? <div style={{...S.meta,marginTop:3}}>Сейчас окно отправки закрыто расписанием. Разрешённая очередь продолжится автоматически в рабочие часы.</div> : null}
              {a.state==="waiting_external" ? <div style={{fontSize:12,fontWeight:700,color:"#9A3412",marginTop:5}}>Действие владельца требуется только снаружи BORIS: восстановить тариф/доступ Avito API Мессенджера. После восстановления BORIS сам перепроверит историю, ответы и безопасно вернёт актуальные черновики в работу — ручной перезапуск не нужен.</div> : null}
              {a.state==="partial_external" ? <div style={{fontSize:12,fontWeight:700,color:"#9A3412",marginTop:5}}>Останавливать весь аккаунт не нужно: BORIS проверяет доступ отдельно для каждого чата. Доступные диалоги продолжают работу, недоступные ждут восстановления Avito без платной генерации и без повторной отправки.</div> : null}
              {a.state==="needs_attention" ? <div style={{fontSize:12,fontWeight:700,color:"#991B1B",marginTop:5}}>Есть неопределённая доставка. BORIS удерживает её без повтора, автоматически сверяет историю Avito и не создаёт риск дубля.</div> : null}
              {a.state==="degraded" ? <div style={{fontSize:12,fontWeight:700,color:"#991B1B",marginTop:5}}>Avito отвечает нестабильно. BORIS продолжает безопасные проверки и не отправляет повторно без подтверждения состояния диалога.</div> : null}
              {a.state==="ready" && a.last_transport_restoration?.restored_at ? <div style={{fontSize:12,fontWeight:700,color:"#047857",marginTop:5}}>Доступ Avito восстановлен и проверен BORIS. Реактивация продолжена автоматически.</div> : null}
              {Number(a.delivery_unknown||0)>0 ? <div style={{...S.meta,marginTop:3}}>Неопределённая доставка без автоповтора: {a.delivery_unknown}</div> : null}
              {Number(a.blocked_transport||0)>0 ? <div style={{...S.meta,marginTop:3}}>Безопасно удерживается из-за Avito: {a.blocked_transport}</div> : null}
              {Number(a.transport_restored_total||0)>0 ? <div style={{...S.meta,marginTop:3}}>Автоматически восстановлено после сбоев Avito: {a.transport_restored_total}{a.last_transport_restored_at?` · последнее ${new Date(a.last_transport_restored_at).toLocaleString("ru-RU")}`:``}</div> : null}
              {Number(a.sent_total||0)>0 ? <div style={{...S.meta,marginTop:3}}>Отправлено через Avito: {a.sent_total} · подтверждено ID: {a.receipted_total} · ответили: {a.replied_total}{a.reply_rate_pct!=null?` (${a.reply_rate_pct}%)`:``}</div> : null}
            </div>)}
          </div>
        </div>
      ) : null}

      {err ? (
        <div data-boris-g33-card="1" style={{ background: "#FEF2F2", border: "1px solid #FECACA", color: "#991B1B",
          padding: 12, borderRadius: 12, marginBottom: 16 }}>{err}</div>
      ) : null}

      {health && health.score != null ? (
        <div data-boris-g33-card="1" style={{
          border: "1px solid var(--boris-border)", borderRadius: 16, padding: 16, marginBottom: 20,
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
            <button key={t.key} style={S.tab(tab === t.key && workbench)} onClick={() => openQueue(t.key)}>
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

      {workbench ? (
        <div style={{position:"fixed",inset:0,zIndex:45,background:"rgba(15,23,42,.42)",padding:24,overflowY:"auto"}} onClick={()=>{setWorkbench(false);setOpen(null);const u=new URL(window.location.href);u.searchParams.delete("queue");window.history.replaceState({},"",u.toString())}}>
        <div data-boris-g33-card="1" style={{border:"1px solid var(--boris-border)",borderRadius:18,background:"#fff",padding:18,maxWidth:1180,margin:"20px auto",minHeight:"70vh",boxShadow:"0 20px 60px rgba(15,23,42,.20)"}} onClick={(e)=>e.stopPropagation()}>
          <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",marginBottom:14}}>
            <div><div style={{fontSize:20,fontWeight:800}}>{TABS.find(x=>x.key===tab)?.label}</div><div style={S.meta}>Рабочее окно: выберите клиентов, откройте карточку, подготовьте или измените сообщение и отправьте после проверки BORIS.</div></div>
            <button style={S.btn("")} onClick={()=>{setWorkbench(false);setOpen(null);setSelected(new Set());const u=new URL(window.location.href);u.searchParams.delete("queue");window.history.replaceState({},"",u.toString())}}>Закрыть окно</button>
          </div>
      {tab === "reactivation" && items.length > 0 ? (
        <>
        <div style={{display:"flex",gap:8,flexWrap:"wrap",marginBottom:10}}>
          {[ ["all","Все"], ["hot","Приоритетные"], ["price","Спрашивали цену"], ["calc","Получили расчёт"], ["later","Написать позже"], ["30d","30+ дней"] ].map(([k,l]) => <button key={k} style={S.btn(segment===k?"main":"")} onClick={()=>{setSegment(k);setSelected(new Set());setBulkPreview(null)}}>{l}</button>)}
          <span style={S.meta}>Показано: {visibleItems.length}</span>
        </div>
        <div data-boris-g33-card="1" style={{ ...S.card, cursor: "default", display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <b>Выбрано: {selected.size}</b>
          <button style={S.btn("")} disabled={busy || Number((queues as any)?.remaining_today || 0) <= 0} onClick={selectRecommendedToday}>Выбрать рекомендуемые на сегодня ({Number((queues as any)?.remaining_today || 0)})</button>
          <button style={S.btn("")} disabled={busy || selected.size === visibleItems.length} onClick={selectAllVisible}>Выбрать все показанные</button>
          <button style={S.btn("")} disabled={busy || !selected.size} onClick={clearSelected}>Снять выбор</button>
          <button style={S.btn("")} disabled={busy || !selected.size} onClick={bulkPrepare}>Подготовить сообщения</button>
          <button style={S.btn("")} disabled={busy || !selected.size} onClick={bulkPreflight}>Проверить перед отправкой</button>
          <button style={S.btn("main")} disabled={busy || !selected.size || !bulkPreview || bulkPreview.sendable === 0} onClick={bulkSend}>Отправить прошедшим проверку{bulkPreview ? ` (${bulkPreview.sendable})` : ""}</button>
          <span style={S.meta}>Перед каждой отправкой BORIS проверяет отказ, новый ответ клиента, дневной лимит и доступ Авито.</span>
          {bulkInfo ? <div style={{width:"100%",fontSize:13,fontWeight:700,color:"#047857"}}>{bulkInfo}</div> : null}
          {bulkPreview && bulkPreview.blocked > 0 ? <div style={{width:"100%",fontSize:12,color:"#92400E"}}>Не прошли проверку: {Object.entries(bulkPreview.results.filter((x:any)=>!x.ok).reduce((a:any,x:any)=>{a[x.reason]=(a[x.reason]||0)+1;return a;},{})).map(([k,v])=>`${k}: ${v}`).join(" · ")}</div> : null}
        </div>
        </>
      ) : null}

      {workbench && items.length === 0 ? (
        <div style={{ ...S.card, cursor: "default", color: "#6B7280" }}>
          Здесь пусто — значит по этой очереди сейчас ничего не требуется.
        </div>
      ) : null}

      {workbench && visibleItems.map((x) => (
        <div data-boris-g33-interactive="1" data-boris-g33-card="1" key={x.id} style={{...S.card, border: selected.has(x.id) ? "2px solid #2563EB" : S.card.border}} onClick={() => openCard(x.id)}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            {tab === "reactivation" ? <input type="checkbox" checked={selected.has(x.id)} onClick={(e) => e.stopPropagation()} onChange={() => toggleSelected(x.id)} aria-label="Выбрать клиента для реактивации" /> : null}
            <div style={{ fontWeight: 700 }}>{x.title || "Без объявления"}</div>
            <div style={S.meta}>{x.age_days} дн</div>
          </div>
          <div style={S.money}>{moneyLine(x.money)}</div>
          <div style={{ margin: "6px 0", fontSize: 14 }}>{x.summary || ""}</div>
          <div style={S.meta}>
            {REASON_RU[x.reason] || x.reason} · {x.account_id}
            {x.draft ? " · черновик готов" : ""}
            {x.phone_received ? " · телефон есть" : " · телефона нет"}{x.lead_temperature ? ` · ${x.lead_temperature}` : ""}{x.last_contact_at ? ` · последний контакт ${new Date(x.last_contact_at).toLocaleString("ru-RU")}` : ""}
          </div>
          {(x.intent || x.qualification_stage || x.target_action) ? <div style={{...S.meta,marginTop:5}}>Хотел: {x.intent || "не зафиксировано"} · Остановился: {x.qualification_stage || x.target_action || "этап не зафиксирован"}</div> : null}
          <div style={{fontSize:13,marginTop:6}}><b>Следующее действие:</b> {x.recommendation || x.next_action || "Вернуть клиента в диалог с учётом сохранённого контекста."}</div>
          {x.message_error_code === "transport_unavailable" ? (
            <div style={{marginTop:8,padding:"8px 10px",borderRadius:10,background:"#FFF7ED",color:"#9A3412",fontSize:12}}>
              Avito не разрешил отправку через API для этого аккаунта. BORIS не повторяет её автоматически — проверьте тариф или доступ к API Мессенджера.
            </div>
          ) : null}
        </div>
      ))}



        </div>
        </div>
      ) : null}

      {open ? (
        <div style={S.modal} onClick={() => setOpen(null)}>
          <div style={S.sheet} onClick={(e) => e.stopPropagation()}>
            <h2 data-boris-g33-heading="1" style={{ margin: "0 0 4px", fontSize: 20 }}>{open.title || "Без объявления"}</h2>
            <div style={S.meta}>
              {open.account_id} · ждёт {open.age_days} дн · {REASON_RU[open.reason] || open.reason}
            </div>
            <div style={{ ...S.money, margin: "10px 0" }}>{moneyLine(open.money)}</div>
            <p style={{ fontSize: 15 }}>{open.summary}</p>
            <div style={{...S.meta,margin:"8px 0"}}>Что хотел: {open.intent || "не зафиксировано"} · Где остановился: {open.qualification_stage || open.target_action || "этап не зафиксирован"}</div>
            <div style={{...S.meta,margin:"8px 0"}}>Почему в реактивации: {REASON_RU[open.reason] || open.reason} · {open.phone_received ? "телефон получен" : "телефон не получен"}{open.lead_temperature ? ` · температура ${open.lead_temperature}` : ""}{open.last_contact_at ? ` · последний контакт ${new Date(open.last_contact_at).toLocaleString("ru-RU")}` : ""}</div>
            <div style={{padding:10,borderRadius:10,background:"#f8fafc",fontSize:13}}><b>Что BORIS рекомендует сделать:</b> {open.recommendation || "Продолжить работу с учётом сохранённого контекста клиента."}</div>
            {open.message_error_code === "transport_unavailable" ? (
              <div style={{marginTop:10,padding:10,borderRadius:10,background:"#FFF7ED",color:"#9A3412",fontSize:13}}>
                <b>Отправка остановлена безопасно.</b> Avito не разрешил API Мессенджера для этого чата. BORIS не делает слепой повтор. После восстановления доступа BORIS сам перепроверит историю и вернёт актуальный черновик в работу, если диалог не изменился — ручной перезапуск не нужен.
              </div>
            ) : null}

            <h3 data-boris-g33-heading="1" style={{ fontSize: 15, margin: "18px 0 6px" }}>Переписка</h3>
            {(open.history || []).map((m) => (
              <div key={m.id} style={S.msg(m.from !== "клиент")}>
                <b>{m.from}:</b> {m.text}
              </div>
            ))}

            <h3 data-boris-g33-heading="1" style={{ fontSize: 15, margin: "18px 0 6px" }}>Сообщение для возврата клиента</h3>
            {open.draft ? (
              <>
                <textarea value={draftText} onChange={(e) => setDraftText(e.target.value)} maxLength={400}
                  style={{ width: "100%", minHeight: 110, resize: "vertical", border: "1px solid var(--boris-border)", borderRadius: 12, padding: 12, background: "#fff", fontSize: 14, lineHeight: 1.5 }} />
                <div style={{ ...S.meta, marginTop: 6 }}>
                  {settings?.mode === "auto"
                    ? "Автопилот отправит только актуальный безопасный текст в разрешённые часы."
                    : "Проверьте текст, при необходимости измените его и отправьте клиенту."}
                </div>
                <div style={{ marginTop: 10 }}>
                  <button style={S.btn("")} disabled={busy || !draftText.trim()} onClick={saveDraft}>Сохранить текст</button>
                  <button style={S.btn("main")} disabled={busy || !draftText.trim()} onClick={sendDraft}>Отправить клиенту</button>
                </div>
              </>
            ) : (
              <div>
                <div style={{ ...S.meta, marginBottom: 10 }}>Готового текста пока нет. BORIS составит его по этой переписке без выдуманных цен, скидок и обещаний.</div>
                <button style={S.btn("main")} disabled={busy} onClick={generateDraft}>Подготовить сообщение</button>
              </div>
            )}

            <div style={{ marginTop: 20 }}>
              <button style={S.btn("")} disabled={busy}
                onClick={() => act(open.id, "take")}>Взять в работу вручную</button>
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
