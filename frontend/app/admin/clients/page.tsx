"use client";

/* Администрирование → Клиенты.
   Отдельная страница: свои инлайн-стили и свой fetch, без зависимости от
   оболочки кабинета (её сейчас переписывают) и от app/lib/api.ts.
   Ключ токена тот же, что у всего фронта: boris_token. */

import { useRef, useState } from "react";

type Client = { id: number; email: string; role: string; status: string };

const PILOT_SERVICES = [
  ["mop", "МОП"], ["rop", "РОП"], ["ecs", "ЕЦС"], ["crm", "CRM"],
  ["reactivation", "Реактивация"], ["campaign", "Campaign"], ["marketing", "ИИ-маркетолог"],
] as const;

function pilotFormRequest(userId: number, days: string, services: string[],
  mopMessages: string, reactivationMessages: string, renew: boolean, dryRun: boolean) {
  const integer = (value: string, maximum: number, message: string) => {
    const number = Number(value);
    if (!value.trim() || !Number.isInteger(number) || number < 1 || number > maximum) throw new Error(message);
    return number;
  };
  if (!Number.isInteger(userId) || userId < 1) throw new Error("Сначала выберите клиента.");
  if (!services.length || new Set(services).size !== services.length ||
      services.some((service) => !PILOT_SERVICES.some(([key]) => key === service))) {
    throw new Error("Выберите услуги пилота.");
  }
  const request: any = {
    user_id: userId,
    days: integer(days, 30, "Укажите срок от 1 до 30 дней."),
    services: [...services],
    renew,
    dry_run: dryRun,
  };
  if (services.includes("mop")) {
    request.mop_messages = integer(mopMessages, 100000, "Укажите лимит МОП от 1 до 100 000 ответов.");
  }
  if (services.includes("reactivation")) {
    request.reactivation_messages = integer(reactivationMessages, 100000,
      "Укажите отдельный лимит сообщений реактивации.");
  }
  return request;
}

function previewRequestKey(request: any) {
  return JSON.stringify({ ...request, dry_run: true });
}

function pilotRequestError(data: any, status: number) {
  if (status === 503) return "Запуск пилота пока недоступен: требуется завершить обновление сервиса.";
  const detail = data?.detail;
  if (detail === "reactivation_quota_required_no_default") return "Укажите отдельный лимит сообщений реактивации.";
  if (typeof detail === "string" && !detail.includes("_")) return detail;
  if (status === 422) return "Проверьте выбранные услуги, срок и лимиты пилота.";
  return "Не удалось выполнить запрос пилота. Код ответа: " + status + ".";
}


const S: any = {
  page: { maxWidth: 1000, margin: "0 auto", padding: 24, fontFamily: "system-ui, sans-serif", color: "#1B2437" },
  h1: { fontSize: 24, fontWeight: 800, margin: "0 0 4px" },
  sub: { color: "#6B7688", fontSize: 14, margin: "0 0 20px" },
  card: { background: "#fff", border: "1px solid var(--boris-border)", borderRadius: 16, padding: 20, marginBottom: 16 },
  row: { display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" },
  input: { border: "1px solid #D6DCE8", borderRadius: 10, padding: "10px 12px", fontSize: 14, minWidth: 180 },
  label: { fontSize: 12, color: "#6B7688", display: "block", marginBottom: 4 },
  btn: { background: "#2F6BFF", color: "#fff", border: 0, borderRadius: 10, padding: "10px 18px", fontSize: 14, fontWeight: 600, cursor: "pointer" },
  btnGhost: { background: "#fff", color: "#2F6BFF", border: "1px solid #2F6BFF", borderRadius: 10, padding: "10px 18px", fontSize: 14, fontWeight: 600, cursor: "pointer" },
  btnDanger: { background: "#E5484D", color: "#fff", border: 0, borderRadius: 10, padding: "10px 18px", fontSize: 14, fontWeight: 700, cursor: "pointer" },
  item: { padding: "10px 12px", border: "1px solid var(--boris-border)", borderRadius: 10, marginBottom: 8, cursor: "pointer", background: "#fff" },
  tag: { display: "inline-block", fontSize: 12, padding: "2px 8px", borderRadius: 999, background: "var(--boris-blue-soft)", color: "#2F6BFF", marginLeft: 8 },
  plan: { background: "#FFF8E6", border: "1px solid #F5D98B", borderRadius: 12, padding: 16, marginTop: 12 },
  err: { background: "#FDECEC", border: "1px solid #F3B5B5", borderRadius: 12, padding: 12, marginTop: 12, color: "#9B1C1C" },
  okBox: { background: "#EAF7EE", border: "1px solid #A9DDB9", borderRadius: 12, padding: 12, marginTop: 12, color: "#14663A" },
  th: { textAlign: "left", fontSize: 12, color: "#6B7688", fontWeight: 600, padding: "6px 8px" },
  td: { fontSize: 13, padding: "6px 8px", borderTop: "1px solid var(--boris-border-soft)" },
};

async function api(path: string, method = "GET", body?: any) {
  const token = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: any = {};
  try { data = await res.json(); } catch { data = {}; }
  return { status: res.status, data };
}

export default function AdminClientsPage() {
  const [q, setQ] = useState("");
  const [list, setList] = useState<Client[]>([]);
  const [card, setCard] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [plan, setPlan] = useState<string[] | null>(null);
  const [result, setResult] = useState<any>(null);
  const [runtime, setRuntime] = useState<any>(null);
  const [pilotPreview, setPilotPreview] = useState<any>(null);
  const [pilotDays, setPilotDays] = useState("7");
  const [pilotServices, setPilotServices] = useState<string[]>(["mop", "ecs", "crm", "reactivation", "campaign"]);
  const [pilotMopMessages, setPilotMopMessages] = useState("2000");
  const [pilotReactivationMessages, setPilotReactivationMessages] = useState("");
  const pilotRenew = false;
  const selectionEpoch = useRef(0);
  const pilotEpoch = useRef(0);
  const selectedClientId = useRef<number | null>(null);
  const provisionPreviewRequest = useRef<any>(null);
  const pilotPreviewRequest = useRef<any>(null);
  const [avitoHealth, setAvitoHealth] = useState<Record<string, any>>({});

  const [tier, setTier] = useState("");
  const [slots, setSlots] = useState("");
  const [slotDays, setSlotDays] = useState("30");
  const [amount, setAmount] = useState("");
  const [periodDays, setPeriodDays] = useState("30");
  const [paidAt, setPaidAt] = useState("");
  const [paymentId, setPaymentId] = useState("");
  const [comment, setComment] = useState("");
  const [rop, setRop] = useState(false);
  const [ropRenew, setRopRenew] = useState(false);
  const [ropMinutes, setRopMinutes] = useState("1500");
  const [ropChats, setRopChats] = useState("450");
  const [ropRepCalls, setRopRepCalls] = useState("30");
  const [ropRepChats, setRopRepChats] = useState("30");
  const [ropDays, setRopDays] = useState("30");
  const [mop, setMop] = useState(false);
  const [mopMessages, setMopMessages] = useState("2000");
  const [mopDays, setMopDays] = useState("30");
  const [ecs, setEcs] = useState(false);
  const [ecsDays, setEcsDays] = useState("30");
  const [autopilot, setAutopilot] = useState(false);
  const [autopilotMode, setAutopilotMode] = useState("always_auto");

  function reset() {
    provisionPreviewRequest.current = null;
    setPlan(null); setResult(null); setError("");
  }

  async function doSearch() {
    const epoch = ++selectionEpoch.current;
    selectedClientId.current = null; pilotEpoch.current++;
    setBusy(true); reset(); setCard(null); setRuntime(null); setPilotPreview(null); pilotPreviewRequest.current = null;
    try {
      const r = await api("/api/admin/clients/search?q=" + encodeURIComponent(q));
      if (epoch !== selectionEpoch.current) return;
      if (r.status !== 200) { setError("Поиск не удался: " + r.status); return; }
      setList(r.data.clients || []);
    } catch {
      if (epoch === selectionEpoch.current) setError("Не удалось выполнить поиск. Повторите запрос.");
    } finally {
      if (epoch === selectionEpoch.current) setBusy(false);
    }
  }

  async function openCard(id: number) {
    const epoch = ++selectionEpoch.current;
    selectedClientId.current = id; pilotEpoch.current++;
    setBusy(true); reset(); setCard(null); setRuntime(null); setPilotPreview(null); pilotPreviewRequest.current = null; setAvitoHealth({});
    try {
      const [r, rt] = await Promise.all([
        api("/api/admin/clients/card?user_id=" + id),
        api("/api/admin/clients/runtime?user_id=" + id),
      ]);
      if (epoch !== selectionEpoch.current || selectedClientId.current !== id) return;
      if (r.status !== 200) { setError("Карточка не открылась: " + r.status); return; }
      setCard(r.data);
      if (rt.status === 200) setRuntime(rt.data);
    } catch {
      if (epoch === selectionEpoch.current) setError("Не удалось загрузить карточку клиента. Повторите запрос.");
    } finally {
      if (epoch === selectionEpoch.current) setBusy(false);
    }
  }

  async function refreshRuntime() {
    const userId = card?.user?.id;
    const epoch = selectionEpoch.current;
    if (!userId) return;
    const rt = await api("/api/admin/clients/runtime?user_id=" + userId);
    if (epoch === selectionEpoch.current && selectedClientId.current === userId && rt.status === 200) setRuntime(rt.data);
  }

  function openClientAccount(accountId: string) {
    const id = String(accountId || "").trim();
    if (!id || typeof window === "undefined") return;
    // OWNER_CLIENT_SWITCH_V1:
    // keep the platform-owner session and switch only the active account.
    // AccountProvider validates the linked account against /api/accounts/list,
    // so this never expands access and never needs the client's password/OTP.
    localStorage.setItem("boris_currentAccount", id);
    window.location.assign("/dashboard/home?account_id=" + encodeURIComponent(id));
  }

  function payload(dry: boolean) {
    return {
      user_id: card.user.id,
      tier, slots: Number(slots) || 0, slot_days: Number(slotDays) || 30,
      amount_rub: Number(amount) || 0, period_days: Number(periodDays) || 30,
      paid_at: paidAt || null, payment_id: paymentId, comment,
      rop, rop_renew: ropRenew,
      rop_minutes: Number(ropMinutes) || 0, rop_chats: Number(ropChats) || 0,
      rop_rep_calls: Number(ropRepCalls) || 0, rop_rep_chats: Number(ropRepChats) || 0,
      rop_days: Number(ropDays) || 30,
      mop, mop_messages: Number(mopMessages) || 2000, mop_days: Number(mopDays) || 30,
      ecs, ecs_days: Number(ecsDays) || 30,
      autopilot, autopilot_mode: autopilotMode,
      dry_run: dry,
    };
  }

  async function preview() {
    const request = payload(true);
    const epoch = selectionEpoch.current;
    setBusy(true); reset();
    try {
      const r = await api("/api/admin/clients/provision", "POST", request);
      if (epoch !== selectionEpoch.current || selectedClientId.current !== request.user_id) return;
      if (r.status !== 200) { setError("Предпросмотр не удался: " + r.status); return; }
      if (r.data.status === "error") { setError(r.data.message || "Нечего применять"); return; }
      provisionPreviewRequest.current = request;
      setPlan(r.data.plan || []);
    } catch {
      if (epoch === selectionEpoch.current) setError("Предпросмотр не получен. Повторите проверку.");
    } finally {
      if (epoch === selectionEpoch.current) setBusy(false);
    }
  }

  async function apply() {
    const checked = provisionPreviewRequest.current;
    if (!plan || !checked) return;
    if (checked.user_id !== card?.user?.id || checked.user_id !== selectedClientId.current ||
        previewRequestKey(checked) !== previewRequestKey(payload(false))) {
      reset(); setError("Параметры изменились. Сначала получите новый предпросмотр."); return;
    }
    if (!confirm("Применить проверенные изменения клиенту #" + checked.user_id + "? Слоты и оплата будут записаны.")) return;
    const epoch = selectionEpoch.current;
    setBusy(true); setError("");
    setPlan(null); provisionPreviewRequest.current = null;
    try {
      const r = await api("/api/admin/clients/provision", "POST", { ...checked, dry_run: false });
      if (epoch !== selectionEpoch.current || selectedClientId.current !== checked.user_id) return;
      if (r.status !== 200) { setError("Применение не удалось: " + r.status); return; }
      setResult(r.data); setPlan(null); provisionPreviewRequest.current = null;
      if (r.data.after) setCard({ status: "ok", ...r.data.after });
      await refreshRuntime();
    } catch {
      if (epoch === selectionEpoch.current) setError("Подтверждение не получено. Обновите карточку перед повторным действием.");
    } finally {
      if (epoch === selectionEpoch.current) setBusy(false);
    }
  }

  function pilotPayload(dry: boolean) {
    return pilotFormRequest(card?.user?.id, pilotDays, pilotServices,
      pilotMopMessages, pilotReactivationMessages, pilotRenew, dry);
  }

  function resetPilotPreview() {
    pilotEpoch.current++;
    setPilotPreview(null); pilotPreviewRequest.current = null; setResult(null); setError("");
  }

  let pilotFormError = "";
  if (card?.user?.id) {
    try { pilotPayload(true); } catch (err) { pilotFormError = (err as Error).message; }
  }

  async function previewPilot() {
    let request: any;
    try { request = pilotPayload(true); } catch (err) { setError((err as Error).message); return; }
    const epoch = selectionEpoch.current;
    const check = ++pilotEpoch.current;
    setBusy(true); setError(""); setPilotPreview(null); pilotPreviewRequest.current = null;
    try {
      const r = await api("/api/admin/clients/pilot", "POST", request);
      if (epoch !== selectionEpoch.current || check !== pilotEpoch.current ||
          selectedClientId.current !== request.user_id) return;
      if (r.status !== 200 || r.data.status !== "ok") {
        setError(pilotRequestError(r.data, r.status)); return;
      }
      pilotPreviewRequest.current = request;
      setPilotPreview({ ...r.data, checkedRequest: request });
    } catch {
      if (epoch === selectionEpoch.current && check === pilotEpoch.current) setError("Предпросмотр пилота не получен. Повторите проверку.");
    } finally {
      if (epoch === selectionEpoch.current && check === pilotEpoch.current) setBusy(false);
    }
  }

  async function applyPilot() {
    const checked = pilotPreviewRequest.current;
    if (!checked) return;
    let current: any;
    try { current = pilotPayload(false); } catch (err) { setError((err as Error).message); return; }
    if (checked.user_id !== card?.user?.id || checked.user_id !== selectedClientId.current ||
        previewRequestKey(checked) !== previewRequestKey(current)) {
      resetPilotPreview(); setError("Параметры пилота изменились. Сначала получите новый предпросмотр."); return;
    }
    const labels = PILOT_SERVICES.filter(([key]) => checked.services.includes(key)).map(([, label]) => label).join(", ");
    if (!confirm("Запустить пилот на " + checked.days + " дн. для клиента #" + checked.user_id + "? Услуги: " + labels +
        (checked.renew ? ". Начнётся новый срок, квоты прежнего пилота будут обнулены." : "."))) return;
    const epoch = selectionEpoch.current;
    setBusy(true); setError("");
    setPilotPreview(null); pilotPreviewRequest.current = null;
    try {
      const r = await api("/api/admin/clients/pilot", "POST", { ...checked, dry_run: false });
      if (epoch !== selectionEpoch.current || selectedClientId.current !== checked.user_id) return;
      if (r.status !== 200 || r.data.status === "error") { setError(pilotRequestError(r.data, r.status)); return; }
      setResult(r.data); setPilotPreview(null); pilotPreviewRequest.current = null;
      await refreshRuntime();
    } catch {
      if (epoch === selectionEpoch.current) setError("Подтверждение запуска не получено. Обновите статусы перед повторным действием.");
    } finally {
      if (epoch === selectionEpoch.current) setBusy(false);
    }
  }

  async function checkAvito(accountId: string) {
    const userId = card?.user?.id;
    const epoch = selectionEpoch.current;
    if (!userId || selectedClientId.current !== userId) return;
    setBusy(true); setError("");
    try {
      const r = await api("/api/admin/clients/avito-health?user_id=" + userId +
        "&account_id=" + encodeURIComponent(accountId));
      if (epoch !== selectionEpoch.current || selectedClientId.current !== userId) return;
      if (r.status !== 200) { setError("Avito не проверен: " + r.status); return; }
      setAvitoHealth((prev) => ({ ...prev, [accountId]: r.data }));
    } catch {
      if (epoch === selectionEpoch.current) setError("Проверка Avito не завершена. Повторите запрос.");
    } finally {
      if (epoch === selectionEpoch.current) setBusy(false);
    }
  }

  return (
    <div style={S.page}>
      <h1 style={S.h1}>Администрирование · Клиенты</h1>
      <p style={S.sub}>Поиск клиента, тариф, слоты, оплата и комментарий — одним действием.</p>

      <div style={S.card}>
        <div style={S.row}>
          <div>
            <label style={S.label}>Email, название или account_id</label>
            <input style={{ ...S.input, minWidth: 320 }} value={q}
                   onChange={(e) => setQ(e.target.value)}
                   onKeyDown={(e) => { if (e.key === "Enter") doSearch(); }}
                   placeholder="например client@mail.ru" />
          </div>
          <button style={S.btn} onClick={doSearch} disabled={busy}>Найти</button>
        </div>

        {list.length > 0 && (
          <div className="boris-f3-76977cc9f24231">
            {list.map((c) => (
              <div key={c.id} style={S.item} onClick={() => openCard(c.id)}>
                <b>{c.email}</b>
                <span style={S.tag}>{c.role}</span>
                <span style={S.tag}>{c.status}</span>
                <span style={{ ...S.tag, background: "#F3F4F6", color: "#6B7688" }}>id {c.id}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {error && <div style={S.err}>{error}</div>}

      {card && card.user && (
        <>
          <div style={S.card}>
            <h2 className="boris-f3-d678338adc1bb6">{card.user.email}</h2>
            <div className="boris-f3-b33f84f1a7045a">
              id {card.user.id} · роль {card.user.role} · статус {card.user.status} ·
              основной аккаунт {card.user.account_id || "—"}
            </div>

            <div className="boris-f3-7f3ff6713fa965">
              Аккаунты ({(card.accounts || []).length})
            </div>
            <table className="boris-f3-fa7edf809321fe">
              <thead><tr>
                <th style={S.th}>account_id</th><th style={S.th}>название</th><th style={S.th}>биллинг</th>
                <th style={S.th}>кабинет</th>
              </tr></thead>
              <tbody>
                {(card.accounts || []).map((a: any) => (
                  <tr key={a.account_id}>
                    <td style={S.td}>{a.account_id}</td>
                    <td style={S.td}>{a.name}</td>
                    <td style={S.td}>{a.billing_mode}</td>
                    <td style={S.td}>
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <button
                          style={S.btnGhost}
                          onClick={() => openClientAccount(a.account_id)}
                          disabled={busy}
                        >
                          Открыть в BORIS
                        </button>
                        <button
                          style={S.btnGhost}
                          onClick={() => checkAvito(a.account_id)}
                          disabled={busy}
                        >
                          Проверить Avito
                        </button>
                      </div>
                      {avitoHealth[a.account_id] && (
                        <div style={{ marginTop: 8, fontSize: 12 }}>
                          <b>{avitoHealth[a.account_id].overall}</b>
                          {" · "}{avitoHealth[a.account_id].message}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {runtime && (
              <div style={{ marginTop: 18 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <b>Состояние услуг</b>
                  <button style={S.btnGhost} onClick={refreshRuntime} disabled={busy}>Обновить статусы</button>
                </div>
                {(runtime.accounts || []).map((r: any) => (
                  <div key={r.account_id} style={{ marginTop: 10, padding: 12, border: "1px solid #E6EAF0", borderRadius: 12 }}>
                    <div style={{ fontWeight: 700 }}>{r.account_id}</div>
                    <div style={{ fontSize: 13, marginTop: 6, lineHeight: 1.6 }}>
                      Пилот: <b>{r.pilot?.state || "NOT_STARTED"}</b>
                      {" · "}МОП: <b>{r.services?.mop?.active ? "активен" : "выключен"}</b>
                      {" · "}ЕЦС: <b>{r.services?.ecs?.active ? "активен" : "выключен"}</b>
                      {" · "}CRM: <b>{r.services?.crm?.active ? "активна" : "выключена"}</b>
                      {" · "}Реактивация: <b>{r.services?.reactivation?.active ? "активна" : "не активна"}</b>
                      {" · "}Campaign: <b>{r.services?.campaign?.active ? "активен" : "не активен"}</b>
                    </div>
                    <div style={{ marginTop: 8, fontSize: 12, lineHeight: 1.6, color: "#475569" }}>
                      Avito: <b>{r.avito?.wallet?.real_rub !== null && r.avito?.wallet?.real_rub !== undefined
                        ? Math.round(Number(r.avito.wallet.real_rub)) + " ₽"
                        : "баланс неизвестен"}</b>
                      {" · "}активных объявлений: <b>{r.avito?.wallet?.items_count ?? "—"}</b>
                      {" · "}сообщения: <b>{r.avito?.messaging?.messages_ok ? "работают" : "требуют проверки"}</b>
                    </div>
                    <div style={{ marginTop: 4, fontSize: 12, lineHeight: 1.6, color: "#475569" }}>
                      Последняя кампания: <b>{r.campaign?.latest?.id ? "#" + r.campaign.latest.id : "—"}</b>
                      {" · "}карточек: <b>{r.campaign?.latest?.total ?? 0}</b>
                      {" · "}контент: <b>{r.campaign?.latest?.content_pct ?? 0}%</b>
                      {" · "}media: <b>{r.campaign?.latest?.items_with_media ?? 0}</b>
                      {" · "}баннеры: <b>{r.campaign?.latest?.banner_refs ?? 0}</b>
                    </div>
                    {r.pilot?.expires_at && (
                      <div style={{ fontSize: 12, color: "#6B7688" }}>
                        Пилот до {String(r.pilot.expires_at).slice(0, 10)}
                      </div>
                    )}
                  </div>
                ))}
                {(runtime.needs_attention || []).map((x: any, i: number) => (
                  <div key={i} style={S.err}>{x.account_id}: {x.message}</div>
                ))}
              </div>
            )}

            <div className="boris-f3-1a03fde4a44c2a">
              Слоты ({(card.slots || []).length})
            </div>
            {(card.slots || []).length === 0
              ? <div className="boris-f3-82259d267eb8c6">слотов нет</div>
              : (
                <table className="boris-f3-fa7edf809321fe">
                  <thead><tr>
                    <th style={S.th}>№</th><th style={S.th}>статус</th><th style={S.th}>аккаунт</th>
                    <th style={S.th}>цена</th><th style={S.th}>оплачен до</th>
                  </tr></thead>
                  <tbody>
                    {(card.slots || []).map((s: any) => (
                      <tr key={s.id}>
                        <td style={S.td}>{s.slot_no}</td>
                        <td style={S.td}>{s.status}</td>
                        <td style={S.td}>{s.account_id || "—"}</td>
                        <td style={S.td}>{s.price_rub} ₽</td>
                        <td style={S.td}>{(s.paid_until || "").slice(0, 10) || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
          </div>

          <div style={S.card}>
            <h2 className="boris-f3-d678338adc1bb6">Пилот BORIS</h2>
            <p style={S.sub}>Выберите услуги, срок и отдельные лимиты на каждый аккаунт клиента. Оплаченные права сохраняются.</p>
            <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: "0 0 16px" }}>
              <legend style={S.label}>Услуги пилота</legend>
              <div style={{ ...S.row, alignItems: "center", marginBottom: 14 }}>
                {PILOT_SERVICES.map(([key, label]) => (
                  <label key={key} style={{ display: "flex", gap: 6, alignItems: "center", minHeight: 44 }}>
                    <input type="checkbox" checked={pilotServices.includes(key)}
                      onChange={(e) => {
                        setPilotServices((current) => e.target.checked ? [...current, key] : current.filter((x) => x !== key));
                        resetPilotPreview();
                      }} />{label}
                  </label>
                ))}
              </div>
              <div style={S.row}>
                <label style={S.label}>Срок, дней
                  <input aria-label="Срок пилота, дней" type="number" min="1" max="30" step="1"
                    style={{ ...S.input, display: "block", minWidth: 110, width: 120 }} value={pilotDays}
                    onChange={(e) => { setPilotDays(e.target.value); resetPilotPreview(); }} />
                </label>
                {pilotServices.includes("mop") && (
                  <label style={S.label}>Ответов МОП на аккаунт
                    <input aria-label="Лимит ответов МОП" type="number" min="1" max="100000" step="1"
                      style={{ ...S.input, display: "block", minWidth: 150, width: 180 }} value={pilotMopMessages}
                      onChange={(e) => { setPilotMopMessages(e.target.value); resetPilotPreview(); }} />
                  </label>
                )}
                {pilotServices.includes("reactivation") && (
                  <label style={S.label}>Сообщений реактивации на аккаунт
                    <input aria-label="Лимит сообщений реактивации" type="number" min="1" max="100000" step="1"
                      required placeholder="Укажите лимит" style={{ ...S.input, display: "block", minWidth: 160, width: 190 }}
                      value={pilotReactivationMessages}
                      onChange={(e) => { setPilotReactivationMessages(e.target.value); resetPilotPreview(); }} />
                  </label>
                )}
              </div>

            </fieldset>
            {pilotFormError && <p role="status" style={{ fontSize: 13, color: "#9B1C1C" }}>{pilotFormError}</p>}
            <div style={S.row}>
              <button style={S.btnGhost} onClick={previewPilot} disabled={busy || !!pilotFormError}>Проверить запуск пилота</button>
              <button style={pilotPreview ? S.btn : { ...S.btn, opacity: 0.4, cursor: "not-allowed" }}
                onClick={applyPilot} disabled={!pilotPreview || busy || !!pilotFormError}>Запустить проверенный пилот</button>
            </div>
            {pilotPreview && (
              <div style={S.plan}>
                <b>Проверен пилот для клиента #{pilotPreview.checkedRequest.user_id} на {pilotPreview.checkedRequest.days} дн.</b>
                <p style={{ fontSize: 13, lineHeight: 1.6 }}>
                  Услуги: {PILOT_SERVICES.filter(([key]) => pilotPreview.checkedRequest.services.includes(key)).map(([, label]) => label).join(", ")}.
                  {pilotPreview.checkedRequest.services.includes("mop") && " МОП: " + pilotPreview.checkedRequest.mop_messages + " ответов на аккаунт."}
                  {pilotPreview.checkedRequest.services.includes("reactivation") && " Реактивация: " + pilotPreview.checkedRequest.reactivation_messages + " сообщений на аккаунт."}
                </p>
                {(pilotPreview.plan || []).map((line: string, index: number) => <div key={index} style={{ marginTop: 8 }}>{line}</div>)}
                <p style={{ fontSize: 12, color: "#6B7688", marginBottom: 0 }}>Срок начинается при успешном запуске. Существующий пилот сохраняется без повторной выдачи.</p>
              </div>
            )}
          </div>

          <div style={S.card}>
            <h2 className="boris-f3-d678338adc1bb6">Оформление</h2>
            <div style={S.row}>
              <div>
                <label style={S.label}>Тариф</label>
                <select style={S.input} value={tier} onChange={(e) => { setTier(e.target.value); reset(); }}>
                  <option value="">не менять</option>
                  <option value="tariff_1">Автопилот 2.0 (tariff_1)</option>
                  <option value="tariff_2">Автопилот MAX (tariff_2)</option>
                  <option value="none">снять тариф</option>
                </select>
              </div>
              <div>
                <label style={S.label}>Слотов всего должно быть</label>
                <input style={{ ...S.input, minWidth: 110 }} type="number" min={0} max={50}
                       value={Number(slots) === 0 ? "" : slots} onChange={(e) => { setSlots(e.target.value); reset(); }} />
              </div>
              <div>
                <label style={S.label}>Слоты на дней</label>
                <input style={{ ...S.input, minWidth: 110 }} type="number" min={1}
                       value={Number(slotDays) === 0 ? "" : slotDays} onChange={(e) => { setSlotDays(e.target.value); reset(); }} />
              </div>
            </div>

            <div style={{ ...S.row, marginTop: 12 }}>
              <div>
                <label style={S.label}>Оплачено, ₽</label>
                <input style={{ ...S.input, minWidth: 140 }} type="number" min={0}
                       value={Number(amount) === 0 ? "" : amount} onChange={(e) => { setAmount(e.target.value); reset(); }} />
              </div>
              <div>
                <label style={S.label}>Период, дней</label>
                <input style={{ ...S.input, minWidth: 110 }} type="number" min={1}
                       value={Number(periodDays) === 0 ? "" : periodDays} onChange={(e) => { setPeriodDays(e.target.value); reset(); }} />
              </div>
              <div>
                <label style={S.label}>Дата оплаты</label>
                <input style={{ ...S.input, minWidth: 150 }} type="date"
                       value={paidAt} onChange={(e) => { setPaidAt(e.target.value); reset(); }} />
              </div>
              <div>
                <label style={S.label}>ID платежа</label>
                <input style={{ ...S.input, minWidth: 150 }} value={paymentId}
                       onChange={(e) => { setPaymentId(e.target.value); reset(); }} />
              </div>
            </div>

            <div style={{ marginTop: 18, padding: 14, border: "1px solid #E6EAF0", borderRadius: 12 }}>
              <b>Дополнительные модули</b>
              <div style={{ ...S.row, marginTop: 10 }}>
                <label><input type="checkbox" checked={mop} onChange={(e) => { setMop(e.target.checked); reset(); }} /> МОП</label>
                {mop && <>
                  <input style={{ ...S.input, minWidth: 110 }} value={mopMessages}
                         onChange={(e) => { setMopMessages(e.target.value); reset(); }} placeholder="сообщений" />
                  <input style={{ ...S.input, minWidth: 90 }} value={mopDays}
                         onChange={(e) => { setMopDays(e.target.value); reset(); }} placeholder="дней" />
                </>}
                <label><input type="checkbox" checked={ecs} onChange={(e) => { setEcs(e.target.checked); reset(); }} /> ЕЦС</label>
                {ecs && <input style={{ ...S.input, minWidth: 90 }} value={ecsDays}
                               onChange={(e) => { setEcsDays(e.target.value); reset(); }} placeholder="дней" />}
                <label><input type="checkbox" checked={autopilot} onChange={(e) => { setAutopilot(e.target.checked); reset(); }} /> Автопилот</label>
                {autopilot && (
                  <select style={S.input} value={autopilotMode} onChange={(e) => { setAutopilotMode(e.target.value); reset(); }}>
                    <option value="always_auto">автоматически</option>
                    <option value="always_ask">с подтверждением</option>
                    <option value="dates">по датам</option>
                  </select>
                )}
              </div>
            </div>

            <div className="boris-f3-873d518dcb64ce">
              <label className="boris-f3-ba1ecfece147d2">
                <input type="checkbox" checked={rop}
                       onChange={(e) => { setRop(e.target.checked); reset(); }} />
                <b className="boris-f3-905228b6d52d18">Начислить пакет РОП</b>
              </label>
              {rop && (
                <>
                  <div className="boris-f3-50be097abf2e34">
                    РОП будет начислен на {(card.accounts || []).length} аккаунт(ов).
                    Каждый аккаунт получит ОТДЕЛЬНЫЙ лимит: {ropMinutes} минут,
                    {" "}{ropChats} разборов, {ropRepCalls} отчётов по звонкам и
                    {" "}{ropRepChats} по перепискам. Лимиты между аккаунтами не делятся.
                  </div>
                  <div style={S.row}>
                    <div>
                      <label style={S.label}>Минут</label>
                      <input style={{ ...S.input, minWidth: 110 }} value={ropMinutes}
                             onChange={(e) => { setRopMinutes(e.target.value); reset(); }} />
                    </div>
                    <div>
                      <label style={S.label}>Разборов переписок</label>
                      <input style={{ ...S.input, minWidth: 110 }} value={ropChats}
                             onChange={(e) => { setRopChats(e.target.value); reset(); }} />
                    </div>
                    <div>
                      <label style={S.label}>Отчётов по звонкам</label>
                      <input style={{ ...S.input, minWidth: 110 }} value={ropRepCalls}
                             onChange={(e) => { setRopRepCalls(e.target.value); reset(); }} />
                    </div>
                    <div>
                      <label style={S.label}>Отчётов по перепискам</label>
                      <input style={{ ...S.input, minWidth: 110 }} value={ropRepChats}
                             onChange={(e) => { setRopRepChats(e.target.value); reset(); }} />
                    </div>
                    <div>
                      <label style={S.label}>Дней</label>
                      <input style={{ ...S.input, minWidth: 90 }} value={ropDays}
                             onChange={(e) => { setRopDays(e.target.value); reset(); }} />
                    </div>
                  </div>
                  <label className="boris-f3-c8c25d7165b46c">
                    <input type="checkbox" checked={ropRenew}
                           onChange={(e) => { setRopRenew(e.target.checked); reset(); }} />
                    <span className="boris-f3-dcbaeca76a4328">
                      Начать новый период РОП (перезапишет активный период, израсходованное обнулится)
                    </span>
                  </label>
                </>
              )}
            </div>

            <div className="boris-f3-b6d781cbbc10a4">
              <label style={S.label}>Комментарий (уходит в журнал)</label>
              <textarea style={{ ...S.input, width: "100%", minHeight: 70 }} value={comment}
                        onChange={(e) => { setComment(e.target.value); reset(); }}
                        placeholder="например: РОП — индивидуальный пакет на 2 аккаунта, 20 000 ₽" />
            </div>

            <div style={{ ...S.row, marginTop: 16 }}>
              <button style={S.btnGhost} onClick={preview} disabled={busy}>Показать план</button>
              <button style={plan ? S.btnDanger : { ...S.btnDanger, opacity: 0.4, cursor: "not-allowed" }}
                      onClick={apply} disabled={!plan || busy}>Применить</button>
              {!plan && <span className="boris-f3-ed1523ac8dbf33">
                сначала предпросмотр — без него запись невозможна
              </span>}
            </div>

            {plan && (
              <div style={S.plan}>
                <b>Будет выполнено:</b>
                <ul className="boris-f3-1f06a917afdbc7">
                  {plan.map((p, i) => <li key={i} className="boris-f3-b946ddb8eedb4f">{p}</li>)}
                </ul>
              </div>
            )}

            {result && (
              <div style={(result.errors || []).length ? S.err : S.okBox}>
                <b>Готово.</b>
                <ul className="boris-f3-1f06a917afdbc7">
                  {(result.done || []).map((d: string, i: number) => <li key={i}>{d}</li>)}
                </ul>
                {(result.errors || []).length > 0 && (
                  <>
                    <b>Ошибки:</b>
                    <ul className="boris-f3-1f06a917afdbc7">
                      {(result.errors || []).map((d: string, i: number) => <li key={i}>{d}</li>)}
                    </ul>
                  </>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
