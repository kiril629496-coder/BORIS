"use client";

import { useEffect, useMemo, useState } from "react";

type AccountMeta = {
  account_id: string;
  name: string;
  mop_enabled?: boolean;
  mop_mode?: string;
};

type ThreadMeta = {
  account_id: string;
  avito_chat_id: string;
  client_name?: string;
  mop?: { status?: string | null };
  dialog?: {
    assigned_user_id?: number | null;
    assigned_name?: string;
    assigned_mine?: boolean;
  };
};

type AttentionItem = {
  account_id: string;
  account_name: string;
  avito_chat_id: string;
  client_name: string;
  assigned_user_id?: number | null;
  assigned_name?: string;
  assigned_mine?: boolean;
  priority: number;
  why_first?: string;
  wait_label?: string;
  last_text?: string;
  item_title?: string;
  reasons: Array<{ kind: string; label: string; detail?: string }>;
};

type Props = {
  account: AccountMeta | null;
  thread: ThreadMeta | null;
  onOpenDialog: (accountId: string, chatId: string) => void;
  onRefreshThread: () => void;
  onPrimaryActionDone?: (accountId: string, chatId: string) => void;
};

function headers(json = false) {
  const token = typeof window !== "undefined" ? localStorage.getItem("boris_token") || localStorage.getItem("token") || "" : "";
  return {
    ...(json ? { "Content-Type": "application/json" } : {}),
    ...(token ? { Authorization: "Bearer " + token } : {}),
  };
}

async function getJson(path: string) {
  const r = await fetch(path, { headers: headers(), cache: "no-store", credentials: "include" });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || j.message || `Ошибка сервера, код ${r.status}`);
  return j;
}

async function postJson(path: string, body: any) {
  const r = await fetch(path, { method: "POST", headers: headers(true), credentials: "include", body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || j.message || `Ошибка сервера, код ${r.status}`);
  return j;
}

const box: React.CSSProperties = { border: "1px solid #e5e7eb", borderRadius: 12, background: "#fff", padding: 12 };
const muted: React.CSSProperties = { color: "#64748b", fontSize: 11, lineHeight: 1.45 };
const btn: React.CSSProperties = { border: "1px solid #dbe3ee", background: "#fff", borderRadius: 9, padding: "8px 10px", cursor: "pointer", fontSize: 11, fontWeight: 750 };

export default function ManagerWorkspacePanel({ account, thread, onOpenDialog, onRefreshThread, onPrimaryActionDone }: Props) {
  const [tab, setTab] = useState<"mop" | "rop" | "memory" | "attention">("mop");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const [mopMode, setMopMode] = useState<any>(null);
  const [prompts, setPrompts] = useState<any[]>([]);
  const [instructions, setInstructions] = useState("");
  const [salesSettings, setSalesSettings] = useState<any>({ enabled: true, dialog_goal: "other", dialog_goal_text: "", secondary_goals: [], fallback_goal: "", tone: "", answer_length: "normal", handoff_rules: [], forbidden_promises: [], required_questions: [], deal_trigger: "after_engaged", work_schedule: { enabled: false, days: [0,1,2,3,4,5,6], start: "09:00", end: "21:00", timezone: "Europe/Moscow" } });
  const [economics, setEconomics] = useState<any>(null);

  const [preview, setPreview] = useState<any>(null);
  const [memory, setMemory] = useState<any>({ facts: [], summary: null });
  const [attention, setAttention] = useState<AttentionItem[]>([]);
  const [attentionAccountId, setAttentionAccountId] = useState("");
  const [attentionSummary, setAttentionSummary] = useState<any>({});
  const [attentionFilter, setAttentionFilter] = useState<string>("all");
  const [memoryNoteName, setMemoryNoteName] = useState("");
  const [memoryNoteValue, setMemoryNoteValue] = useState("");
  const [reactivationReason, setReactivationReason] = useState("");

  const accountId = account?.account_id || thread?.account_id || "";
  const chatId = thread?.avito_chat_id || "";

  async function loadMop() {
    if (!accountId) return;
    const [mode, list, sales, econ] = await Promise.all([
      getJson(`/api/ai/mop_mode?account_id=${encodeURIComponent(accountId)}`),
      getJson(`/api/messenger_prompts/list?account_id=${encodeURIComponent(accountId)}`),
      getJson(`/api/messenger/sales/settings?account_id=${encodeURIComponent(accountId)}`),
      getJson(`/api/messenger/sales/economics?account_id=${encodeURIComponent(accountId)}`),
    ]);
    setMopMode(mode);
    const rows = list.prompts || [];
    setPrompts(rows);
    setSalesSettings(sales);
    setEconomics(econ);
    const active = rows.find((x: any) => x.is_active) || rows[0];
    setInstructions(active?.custom_instructions || "");
  }

  async function loadMemory() {
    if (!accountId) return;
    const [facts, summary] = await Promise.all([
      getJson(`/api/memory/facts?account_id=${encodeURIComponent(accountId)}&limit=80`),
      getJson(`/api/memory/summary?account_id=${encodeURIComponent(accountId)}`),
    ]);
    setMemory({ facts: facts.facts || [], summary });
  }

  async function loadAttention() {
    if (!accountId) return;
    const requestedAccountId = accountId;
    const j = await getJson(`/api/inbox/attention?account_id=${encodeURIComponent(requestedAccountId)}`);
    setAttention((j.items || []).filter((x: AttentionItem) => x.account_id === requestedAccountId));
    setAttentionAccountId(requestedAccountId);
    setAttentionSummary(j.summary || {});
  }

  useEffect(() => {
    setNote("");
    setPreview(null);
    setMopMode(null); setPrompts([]); setInstructions(""); setEconomics(null);
    setMemory({facts:[],summary:null}); setAttention([]); setAttentionAccountId(""); setAttentionSummary({}); setAttentionFilter("all");
    if (!accountId) return;
    loadMop().catch((e) => setNote(e.message));
    loadMemory().catch(() => {});
    loadAttention().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId]);

  async function changeMode(contour: "legacy" | "new") {
    if (!accountId || busy) return;
    setBusy(true); setNote("");
    try {
      await postJson("/api/ai/mop_mode", { account_id: accountId, contour });
      await loadMop();
      setNote(contour === "new" ? "Режим обновлён: ответ показывается перед отправкой." : "Режим обновлён: МОП работает по штатному автоматическому контуру.");
    } catch (e: any) { setNote(e.message); } finally { setBusy(false); }
  }

  async function saveInstructions() {
    if (!accountId || busy) return;
    setBusy(true); setNote("");
    try {
      const active = prompts.find((x: any) => x.is_active) || prompts[0];
      if (active) {
        await postJson(`/api/messenger_prompts/${active.id}/update`, { custom_instructions: instructions });
      } else {
        await postJson("/api/messenger_prompts/create", { account_id: accountId, label: "Инструкции МОП", item_ids: [], custom_instructions: instructions });
      }
      await loadMop();
      setNote("Инструкции МОП сохранены для выбранного аккаунта.");
    } catch (e: any) { setNote(e.message); } finally { setBusy(false); }
  }

  async function saveSalesSettings() {
    if (!accountId || busy) return;
    setBusy(true); setNote("");
    try {
      const j = await postJson(`/api/messenger/sales/settings?account_id=${encodeURIComponent(accountId)}`, salesSettings);
      if (j.status !== "ok") throw new Error(j.message || "Не удалось сохранить цель и правило создания сделки");
      await loadMop();
      setNote("Цель диалога и момент создания сделки сохранены для выбранного аккаунта.");
    } catch (e: any) { setNote(e.message); } finally { setBusy(false); }
  }

  async function previewRop() {
    if (!accountId || busy) return;
    setBusy(true); setNote("");
    try {
      const j = await postJson(`/api/calltracking/train_manager?account_id=${encodeURIComponent(accountId)}`, { apply: false });
      if (j.status !== "ok") throw new Error(j.message || "Рекомендации пока недоступны");
      setPreview(j);
    } catch (e: any) { setNote(e.message); } finally { setBusy(false); }
  }

  async function applyRop() {
    if (!accountId || !preview?.rules || busy) return;
    if (!window.confirm("Применить показанные рекомендации РОПа к инструкциям МОП?")) return;
    setBusy(true); setNote("");
    try {
      const j = await postJson(`/api/calltracking/train_manager?account_id=${encodeURIComponent(accountId)}`, { apply: true, rules: preview.rules });
      if (j.status !== "ok" || !j.used_preview) throw new Error(j.message || "Не удалось применить показанные рекомендации");
      setPreview(null);
      await loadMop();
      setNote("Показанные рекомендации применены к текущим инструкциям МОП.");
    } catch (e: any) { setNote(e.message); } finally { setBusy(false); }
  }

  useEffect(() => {
    const fn = (event: Event) => {
      const d = (event as CustomEvent).detail || {};
      if (d.kind === "take_human" && d.account_id === thread?.account_id && d.avito_chat_id === thread?.avito_chat_id) handoff("take");
    };
    window.addEventListener("boris-manager-primary-action", fn as EventListener);
    return () => window.removeEventListener("boris-manager-primary-action", fn as EventListener);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thread?.account_id, thread?.avito_chat_id]);

  async function handoff(action: "take" | "release") {
    if (!thread || busy) return;
    setBusy(true); setNote("");
    try {
      const j = await postJson(`/api/inbox/${action}`, { account_id: thread.account_id, avito_chat_id: thread.avito_chat_id });
      if (j.status !== "ok") throw new Error(j.message || "Действие не выполнено");
      onRefreshThread();
      await loadAttention();
      setNote(action === "take" ? "Диалог взят в работу." : "Диалог освобождён.");
      if (action === "take") onPrimaryActionDone?.(thread.account_id, thread.avito_chat_id);
    } catch (e: any) { setNote(e.message); } finally { setBusy(false); }
  }

  async function mopHandoff(action: "human" | "mop") {
    if (!thread || busy) return;
    setBusy(true); setNote("");
    try {
      const j = await postJson(`/api/inbox/mop-handoff?action=${action}`, { account_id: thread.account_id, avito_chat_id: thread.avito_chat_id });
      if (j.status !== "ok" && j.status !== "noop") throw new Error(j.message || "Не удалось изменить режим ведения диалога");
      onRefreshThread(); await loadAttention();
      setNote(action === "human" ? "МОП передал диалог человеку." : "Диалог возвращён МОП в существующий draft flow.");
    } catch (e:any) { setNote(e.message); } finally { setBusy(false); }
  }

  async function sendToReactivation() {
    if (!thread || busy) return;
    const reason = reactivationReason.trim() || selectedAttention?.reasons?.map(x=>x.detail || x.label).filter(Boolean).join("; ") || "Клиенту нужен follow-up";
    if (!window.confirm("Передать этот диалог в существующую очередь реактивации? Сообщение клиенту сейчас отправлено не будет.")) return;
    setBusy(true); setNote("");
    try {
      const j = await postJson("/api/reactivation/handoff", { account_id: thread.account_id, avito_chat_id: thread.avito_chat_id, reason: "manager_followup", summary: reason });
      if (!j.ok) throw new Error(j.message || "Не удалось передать на реактивацию");
      setReactivationReason(""); await loadAttention();
      setNote(j.result === "exists" ? "Диалог уже находится в реактивации; существующий кандидат сохранён." : "Диалог передан в существующую реактивацию с текущим контекстом. Отправки клиенту не было.");
    } catch(e:any) { setNote(e.message); } finally { setBusy(false); }
  }

  async function addMemoryNote() {
    if (!accountId || !memoryNoteName.trim() || !memoryNoteValue.trim() || busy) return;
    setBusy(true); setNote("");
    try {
      const j = await postJson("/api/memory/fact_add", {
        account_id: accountId,
        category: "usluga",
        name: memoryNoteName.trim(),
        value: memoryNoteValue.trim(),
        scope: "account",
      });
      if (j.status !== "ok") throw new Error(j.message || "Не удалось добавить факт");
      setMemoryNoteName(""); setMemoryNoteValue("");
      await loadMemory();
      setNote("Знание сохранено в существующую память аккаунта.");
    } catch (e: any) { setNote(e.message); } finally { setBusy(false); }
  }

  const visibleAttentionRows = attentionAccountId === accountId ? attention : [];
  const selectedAttention = useMemo(() => visibleAttentionRows.find((x) => x.avito_chat_id === chatId), [visibleAttentionRows, chatId]);
  const usableFacts = (memory.facts || []).filter((f: any) => f.status === "confirmed" || (f.status === "draft" && Number(f.confidence || 0) >= 80));
  const learnedRules = usableFacts.filter((f: any) => f.category === "rule");
  const ruleOrigin = (f: any) => { const r=String(f?.source_ref||"").toLowerCase(); const s=String(f?.source_type||"").toLowerCase(); if(r.startsWith("mop_sparring:")) return "Спарринг"; if(r.startsWith("mop_dialog:")) return "Оценка менеджера"; if(r.includes("rop")||s.includes("rop")) return "РОП"; if(s.includes("crm")) return "CRM"; if(s.includes("dialog")) return "Реальный диалог"; return "Настройка владельца"; };
  const attentionFilters = [
    ["all","Все"],["unread","Клиент ждёт"],["draft_ready","Черновик готов"],["human_required","Нужен человек"],
    ["phone_received","Получен телефон"],["next_action","Нужно следующее действие"],["reminder_due","Напоминание"],["overdue_task","Просрочена задача"],["reactivation","Реактивация"],
  ];
  const visibleAttention = attentionFilter === "all" ? visibleAttentionRows : visibleAttentionRows.filter((x) => x.reasons.some((r) => r.kind === attentionFilter));
  const scheduleNow = (() => {
    const ws=salesSettings.work_schedule || {}; if (!ws.enabled) return {on:true,label:"МОП сейчас работает · график не ограничен"};
    try { const parts=new Intl.DateTimeFormat("en-GB",{timeZone:ws.timezone||"Europe/Moscow",weekday:"short",hour:"2-digit",minute:"2-digit",hour12:false}).formatToParts(new Date()); const wd=parts.find(x=>x.type==="weekday")?.value||""; const map:any={Mon:0,Tue:1,Wed:2,Thu:3,Fri:4,Sat:5,Sun:6}; const hm=`${parts.find(x=>x.type==="hour")?.value}:${parts.find(x=>x.type==="minute")?.value}`; const on=(ws.days||[]).includes(map[wd]) && hm >= ws.start && hm < ws.end; return {on,label:on?"МОП сейчас работает":"МОП сейчас вне графика — диалоги передаются человеку"}; } catch { return {on:false,label:"Проверьте часовой пояс рабочего графика"}; }
  })();

  if (!accountId) return null;

  return (
    <div style={{ marginTop: 12, borderTop: "1px solid #eef2f7", paddingTop: 10 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
        {([
          ["mop", "МОП"], ["rop", "Рекомендации РОП"], ["memory", "Память"], ["attention", `Требует внимания${visibleAttentionRows.length ? ` · ${visibleAttentionRows.length}` : ""}`],
        ] as const).map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)} style={{ ...btn, background: tab === key ? "#eff6ff" : "#fff", color: tab === key ? "#1d4ed8" : "#334155" }}>{label}</button>
        ))}
      </div>

      {note ? <div style={{ ...box, marginBottom: 9, background: "#f8fafc", fontSize: 11 }}>{note}</div> : null}

      {tab === "mop" ? <div style={box}>
        <div style={{ fontWeight: 850, fontSize: 13 }}>МОП для {account?.name || "выбранного аккаунта"}</div>
        <div style={{ ...muted, marginTop: 4 }}>МОП для выбранного аккаунта: <b>{account?.mop_enabled ? "включён" : "выключен / пакет не активен"}</b>. Цель, график и правило создания сделки ниже относятся только к этому аккаунту и сохраняются после перезагрузки и перезапуска.</div>
        <label style={{ ...muted, display: "flex", gap: 6, alignItems: "center", marginTop: 9 }}><input type="checkbox" checked={salesSettings.enabled !== false} onChange={(e)=>setSalesSettings({...salesSettings,enabled:e.target.checked})}/>МОП включён для этого аккаунта</label>
        <div style={{ marginTop: 10, display: "flex", gap: 7, flexWrap: "wrap", alignItems: "center" }}>
          <span style={muted}>Режим ответа:</span>
          <button disabled={busy || mopMode?.can_change === false} onClick={() => changeMode("new")} style={{ ...btn, background: mopMode?.contour === "new" ? "#e0f2fe" : "#fff" }}>Показывать перед отправкой</button>
          <button disabled={busy || mopMode?.can_change === false} onClick={() => changeMode("legacy")} style={{ ...btn, background: mopMode?.contour === "legacy" ? "#e0f2fe" : "#fff" }}>Штатный авто-режим</button>
        </div>
        <div style={{ marginTop: 12, fontSize: 11, fontWeight: 850 }}>Цель диалога</div>
        <select value={salesSettings.dialog_goal || "other"} onChange={(e) => setSalesSettings({...salesSettings, dialog_goal: e.target.value})} style={{ width: "100%", marginTop: 6, border: "1px solid #dbe3ee", borderRadius: 9, padding: 8, fontSize: 11 }}>
          <option value="phone">Получить номер телефона</option><option value="measurement">Записать на замер</option><option value="calculation">Договориться о расчёте</option><option value="call">Назначить звонок</option><option value="parameters">Получить параметры заказа</option><option value="meeting">Записать на встречу</option><option value="lead">Получить заявку</option><option value="handoff">Передать менеджеру</option><option value="other">Своя цель</option>
        </select>
        <textarea value={salesSettings.dialog_goal_text || ""} onChange={(e) => setSalesSettings({...salesSettings, dialog_goal_text: e.target.value})} rows={3} placeholder="Уточните основную цель и необходимые параметры для этого аккаунта" style={{ width: "100%", boxSizing: "border-box", marginTop: 6, border: "1px solid #dbe3ee", borderRadius: 9, padding: 8, fontSize: 11 }} />
        <textarea value={(salesSettings.secondary_goals || []).join("; ")} onChange={(e)=>setSalesSettings({...salesSettings,secondary_goals:e.target.value.split(";").map(x=>x.trim()).filter(Boolean)})} rows={2} placeholder="Дополнительные цели по приоритету — через ;" style={{ width: "100%", boxSizing: "border-box", marginTop: 6, border: "1px solid #dbe3ee", borderRadius: 9, padding: 8, fontSize: 11 }} />
        <input value={salesSettings.fallback_goal || ""} onChange={(e)=>setSalesSettings({...salesSettings,fallback_goal:e.target.value})} placeholder="Если клиент пока не готов — следующая цель" style={{ width: "100%", boxSizing: "border-box", marginTop: 6, border: "1px solid #dbe3ee", borderRadius: 9, padding: 8, fontSize: 11 }} />
        <div style={{ marginTop: 10, fontSize: 11, fontWeight: 850 }}>Стиль и ограничения МОП</div>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6,marginTop:6}}><select value={salesSettings.tone || ""} onChange={(e)=>setSalesSettings({...salesSettings,tone:e.target.value})} style={{border:"1px solid #dbe3ee",borderRadius:9,padding:8,fontSize:11}}><option value="">Тон из профиля компании</option><option value="деловой">Деловой</option><option value="дружелюбный">Дружелюбный</option><option value="экспертный">Экспертный</option><option value="краткий">Краткий</option></select><select value={salesSettings.answer_length || "normal"} onChange={(e)=>setSalesSettings({...salesSettings,answer_length:e.target.value})} style={{border:"1px solid #dbe3ee",borderRadius:9,padding:8,fontSize:11}}><option value="short">Коротко</option><option value="normal">Обычно</option><option value="detailed">Подробно</option></select></div>
        <textarea value={(salesSettings.required_questions || []).join("; ")} onChange={(e)=>setSalesSettings({...salesSettings,required_questions:e.target.value.split(";").map(x=>x.trim()).filter(Boolean)})} rows={2} placeholder="Обязательные вопросы — через ;" style={{ width: "100%", boxSizing: "border-box", marginTop: 6, border: "1px solid #dbe3ee", borderRadius: 9, padding: 8, fontSize: 11 }} />
        <textarea value={(salesSettings.handoff_rules || []).join("; ")} onChange={(e)=>setSalesSettings({...salesSettings,handoff_rules:e.target.value.split(";").map(x=>x.trim()).filter(Boolean)})} rows={2} placeholder="Когда передавать человеку — через ;" style={{ width: "100%", boxSizing: "border-box", marginTop: 6, border: "1px solid #dbe3ee", borderRadius: 9, padding: 8, fontSize: 11 }} />
        <textarea value={(salesSettings.forbidden_promises || []).join("; ")} onChange={(e)=>setSalesSettings({...salesSettings,forbidden_promises:e.target.value.split(";").map(x=>x.trim()).filter(Boolean)})} rows={2} placeholder="Что МОП не имеет права обещать — через ;" style={{ width: "100%", boxSizing: "border-box", marginTop: 6, border: "1px solid #dbe3ee", borderRadius: 9, padding: 8, fontSize: 11 }} />
        <div style={{ marginTop: 10, fontSize: 11, fontWeight: 850 }}>Создавать сделку</div>
        <select value={salesSettings.deal_trigger || "after_engaged"} onChange={(e) => setSalesSettings({...salesSettings, deal_trigger: e.target.value})} style={{ width: "100%", marginTop: 6, border: "1px solid #dbe3ee", borderRadius: 9, padding: 8, fontSize: 11 }}>
          <option value="first_inquiry">С первого обращения</option><option value="after_engaged">После содержательного продолжения диалога</option><option value="after_qualification">После квалификации</option><option value="after_contact">После получения контакта</option><option value="after_target_action">После целевого действия</option>
        </select>
        <div style={{ ...muted, marginTop: 5 }}>Обращение, история и контактный контекст сохраняются всегда. Эта настройка управляет только моментом признания обращения полноценной сделкой.</div>
        <div style={{ marginTop: 12, fontSize: 11, fontWeight: 850 }}>Рабочее время МОП</div>
        <label style={{ ...muted, display: "flex", gap: 6, alignItems: "center", marginTop: 6 }}><input type="checkbox" checked={!!salesSettings.work_schedule?.enabled} onChange={(e) => setSalesSettings({...salesSettings, work_schedule:{...(salesSettings.work_schedule||{}), enabled:e.target.checked}})} />Ограничить генерацию рабочим графиком</label>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 7 }}>{["Пн","Вт","Ср","Чт","Пт","Сб","Вс"].map((d,i) => { const days:number[] = salesSettings.work_schedule?.days || []; const on=days.includes(i); return <button type="button" key={d} onClick={() => setSalesSettings({...salesSettings, work_schedule:{...(salesSettings.work_schedule||{}), days:on?days.filter(x=>x!==i):[...days,i].sort()}})} style={{...btn,padding:"6px 8px",background:on?"#e0f2fe":"#fff"}}>{d}</button>; })}</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 7 }}><input type="time" value={salesSettings.work_schedule?.start || "09:00"} onChange={(e)=>setSalesSettings({...salesSettings,work_schedule:{...(salesSettings.work_schedule||{}),start:e.target.value}})} style={{border:"1px solid #dbe3ee",borderRadius:9,padding:8,fontSize:11}}/><input type="time" value={salesSettings.work_schedule?.end || "21:00"} onChange={(e)=>setSalesSettings({...salesSettings,work_schedule:{...(salesSettings.work_schedule||{}),end:e.target.value}})} style={{border:"1px solid #dbe3ee",borderRadius:9,padding:8,fontSize:11}}/></div>
        <input value={salesSettings.work_schedule?.timezone || "Europe/Moscow"} onChange={(e)=>setSalesSettings({...salesSettings,work_schedule:{...(salesSettings.work_schedule||{}),timezone:e.target.value}})} placeholder="Часовой пояс, например Europe/Moscow" style={{width:"100%",boxSizing:"border-box",marginTop:6,border:"1px solid #dbe3ee",borderRadius:9,padding:8,fontSize:11}} />
        <div style={{ marginTop: 7, padding: 8, borderRadius: 9, background: scheduleNow.on ? "#ecfdf5" : "#fff7ed", color: scheduleNow.on ? "#047857" : "#c2410c", fontSize: 11, fontWeight: 800 }}>{scheduleNow.label}</div>
        <div style={{ ...muted, marginTop: 5 }}>Вне выбранных дней и времени входящий сохраняется, но МОП не запускает ИИ-ответ: диалог передаётся человеку через существующий передачу менеджеру.</div>
        <button disabled={busy} onClick={saveSalesSettings} style={{ ...btn, marginTop: 7 }}>Сохранить цель, график и правило создания сделки</button>
        {economics?.metrics ? <div style={{ marginTop: 10, padding: 9, borderRadius: 10, background: "#f8fafc", fontSize: 11, lineHeight: 1.55 }}>
          <b>Воронка и ИИ-себестоимость</b><br />Обращения: {economics.metrics.new_inquiries} → Продолжили: {economics.metrics.continued_dialog} → Квалифицированы: {economics.metrics.qualified ?? "недостаточно данных"} → Получен контакт: {economics.metrics.phones_received} → Целевое действие: {economics.metrics.target_actions ?? "недостаточно данных"} → Сделки: {economics.metrics.deals_created} → Реактивация: {economics.metrics.reactivation}<br />
          Конверсия: обращение→диалог {economics.conversions_pct?.inquiry_to_dialog ?? "недостаточно данных"}% · диалог→квалификация {economics.conversions_pct?.dialog_to_qualified ?? "недостаточно данных"}% · квалификация→контакт {economics.conversions_pct?.qualified_to_contact ?? "недостаточно данных"}% · контакт→целевое действие {economics.conversions_pct?.contact_to_target_action ?? "недостаточно данных"}% · целевое действие→сделка {economics.conversions_pct?.target_action_to_deal ?? "недостаточно данных"}%<br />
          Квалифицировано: {economics.metrics.qualified ?? "недостаточно данных"} · целевых действий: {economics.metrics.target_actions ?? "недостаточно данных"}<br />
          ИИ-затраты: {economics.ai_cost?.cost_rub ?? 0} ₽ · ₽/обращение: {economics.ai_cost?.cost_per_dialog_rub ?? "недостаточно данных"} · ₽/продолжившийся диалог: {economics.ai_cost?.cost_per_engaged_dialog_rub ?? "недостаточно данных"} · ₽/квалифицированного: {economics.ai_cost?.cost_per_qualified_lead_rub ?? "недостаточно данных"} · ₽/полученный контакт: {economics.ai_cost?.cost_per_contact_received_rub ?? economics.ai_cost?.cost_per_contact_rub ?? "недостаточно данных"} · ₽/целевое действие: {economics.ai_cost?.cost_per_target_action_rub ?? "недостаточно данных"} · ₽/сделку: {economics.ai_cost?.cost_per_crm_deal_rub ?? "недостаточно данных"}
        </div> : null}
        <div style={{ marginTop: 10, fontSize: 11, fontWeight: 800 }}>Инструкции МОП</div>
        <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={7} placeholder="Правила и ограничения для МОП" style={{ width: "100%", boxSizing: "border-box", marginTop: 6, border: "1px solid #dbe3ee", borderRadius: 10, padding: 10, fontSize: 12, lineHeight: 1.45 }} />
        <button disabled={busy} onClick={saveInstructions} style={{ ...btn, marginTop: 7 }}>Сохранить инструкции</button>
      </div> : null}

      {tab === "rop" ? <div style={box}>
        <div style={{ fontWeight: 850, fontSize: 13 }}>Рекомендации РОП → МОП</div>
        <div style={{ ...muted, marginTop: 4 }}>Источник — сохранённые разборы звонков и переписок выбранного аккаунта. Предпросмотр ничего не применяет. Применение — только отдельной кнопкой.</div>
        {!preview ? <button disabled={busy} onClick={previewRop} style={{ ...btn, marginTop: 9 }}>Подготовить рекомендации</button> : <>
          <div style={{ ...muted, marginTop: 9 }}>Аккаунт: {preview.account_id || accountId} · источник: {preview.source || "сохранённые разборы РОП"}{preview.generated_at ? ` · подготовлено ${new Date(preview.generated_at).toLocaleString("ru-RU")}` : ""}.<br />Основание: звонков {preview.based_on?.calls ?? 0}, переписок {preview.based_on?.chats ?? 0}{preview.based_on?.avg_score != null ? `, средняя оценка ${preview.based_on.avg_score}` : ""}.</div>
          <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: 12, lineHeight: 1.5, background: "#f8fafc", borderRadius: 10, padding: 10, marginTop: 8 }}>{preview.rules}</pre>
          <div style={{ display: "flex", gap: 7 }}><button disabled={busy} onClick={applyRop} style={{ ...btn, background: "#eff6ff", color: "#1d4ed8" }}>Применить показанные правила</button><button onClick={() => setPreview(null)} style={btn}>Отмена</button></div>
        </>}
      </div> : null}

      {tab === "memory" ? <div style={box}>
        <div style={{ fontWeight: 850, fontSize: 13 }}>Память клиента и бизнеса</div>
        <div style={{ ...muted, marginTop: 4 }}>Используется существующая подтверждённая память аккаунта и контекст клиента. Неподтверждённые факты не выдаются как истина.</div>
        <div style={{ ...muted, marginTop: 7 }}>Известно фактов: {memory.summary?.facts ?? usableFacts.length} · подтверждено: {memory.summary?.confirmed ?? 0} · конфликтов: {memory.summary?.conflicts ?? 0}</div>
        {(learnedRules.length || prompts.some((p:any)=>String(p.custom_instructions||"").includes("=== Опыт от ИИ Руководителя отдела продаж ==="))) ? <div style={{ marginTop: 9, padding: 9, borderRadius: 10, background: "#f0fdf4" }}><div style={{ fontSize: 11, fontWeight: 850 }}>Что запомнил МОП</div>{prompts.filter((p:any)=>String(p.custom_instructions||"").includes("=== Опыт от ИИ Руководителя отдела продаж ===")).slice(0,3).map((p:any)=><div key={`rop-${p.id}`} style={{borderTop:"1px solid #dcfce7",padding:"7px 0",fontSize:11}}><div style={{fontSize:10,color:"#047857",fontWeight:800}}>РОП</div><div style={{whiteSpace:"pre-wrap",marginTop:3}}>{String(p.custom_instructions||"").split("=== Опыт от ИИ Руководителя отдела продаж ===").slice(1).join("=== Опыт от ИИ Руководителя отдела продаж ===").trim()}</div></div>)}{learnedRules.slice(0,12).map((f:any)=><div key={`rule-${f.id}`} style={{borderTop:"1px solid #dcfce7",padding:"7px 0",fontSize:11}}><div style={{fontSize:10,color:"#047857",fontWeight:800}}>{ruleOrigin(f)}</div><div style={{display:"flex",gap:6,alignItems:"start",marginTop:3}}><span style={{flex:1}}>{f.value || f.name}</span><button disabled={busy} onClick={async()=>{const v=window.prompt("Изменить правило МОП",String(f.value||""));if(v==null||!v.trim())return;setBusy(true);try{await postJson("/api/memory/edit",{account_id:accountId,fact_id:f.id,value:v.trim()});await loadMemory();setNote("Правило обновлено.");}catch(e:any){setNote(e.message)}finally{setBusy(false)}}} style={{...btn,padding:"4px 7px"}}>Изменить</button><button disabled={busy} onClick={async()=>{if(!window.confirm("Отключить это правило МОП?"))return;setBusy(true);try{await postJson("/api/memory/reject",{account_id:accountId,fact_id:f.id});await loadMemory();setNote("Правило отключено и больше не используется МОП.");}catch(e:any){setNote(e.message)}finally{setBusy(false)}}} style={{...btn,padding:"4px 7px"}}>Отключить</button></div></div>)}</div> : null}
        <div style={{ marginTop: 8, maxHeight: 220, overflowY: "auto" }}>
          {usableFacts.slice(0, 18).map((f: any) => <div key={f.id} style={{ borderTop: "1px solid #f1f5f9", padding: "7px 0", fontSize: 11 }}><b>{f.name || f.category}</b>{f.value ? ` — ${f.value}` : ""}</div>)}
          {!usableFacts.length ? <div style={{ ...muted, padding: "10px 0" }}>Подтверждённых фактов пока нет.</div> : null}
        </div>
        <details style={{ marginTop: 8 }}><summary style={{ fontSize: 11, fontWeight: 800, cursor: "pointer" }}>Добавить подтверждённое знание</summary>
          <div style={{ display: "grid", gap: 6, marginTop: 7 }}><input value={memoryNoteName} onChange={(e) => setMemoryNoteName(e.target.value)} placeholder="Что запомнить" style={{ border: "1px solid #dbe3ee", borderRadius: 9, padding: 8, fontSize: 11 }} /><textarea value={memoryNoteValue} onChange={(e) => setMemoryNoteValue(e.target.value)} placeholder="Значение / договорённость / важный факт" rows={3} style={{ border: "1px solid #dbe3ee", borderRadius: 9, padding: 8, fontSize: 11 }} /><button disabled={busy} onClick={addMemoryNote} style={btn}>Сохранить в память</button></div>
        </details>
      </div> : null}

      {tab === "attention" ? <div style={box}>
        <div style={{ fontWeight: 850, fontSize: 13 }}>Требует внимания</div>
        <div style={{ ...muted, marginTop: 4 }}>Только реальные состояния: непрочитанные, состояния МОП, просроченные задачи по клиентам и существующая реактивация.</div>
        <div style={{display:"grid",gridTemplateColumns:"repeat(5,minmax(0,1fr))",gap:6,marginTop:8}}>{[["all","Сегодня требуют внимания",attentionAccountId === accountId ? (attentionSummary.attention ?? visibleAttentionRows.length) : 0],["overdue_task","Просрочено",attentionAccountId === accountId ? (attentionSummary.overdue ?? 0) : 0],["phone_received","Получен телефон",attentionAccountId === accountId ? (attentionSummary.phone ?? 0) : 0],["unread","Ждут ответа",attentionAccountId === accountId ? (attentionSummary.waiting ?? 0) : 0],["reactivation","На реактивацию",attentionAccountId === accountId ? (attentionSummary.reactivation ?? 0) : 0]].map(([k,l,n]:any)=><button key={k} onClick={()=>setAttentionFilter(k)} style={{...btn,padding:7,textAlign:"left",background:attentionFilter===k?"#eff6ff":"#fff"}}><div style={{fontSize:15,fontWeight:900}}>{n}</div><div style={{fontSize:9,color:"#64748b",marginTop:2}}>{l}</div></button>)}</div>
        {thread ? <div style={{ marginTop: 8, padding: 9, borderRadius: 10, background: "#f8fafc" }}>
          <div style={{ fontSize: 11, fontWeight: 800 }}>Текущий диалог: {thread.client_name || "Клиент"}</div>
          <div style={{fontSize:11,fontWeight:800,marginTop:4,color:thread.mop?.status === "human_required" ? "#b45309" : thread.dialog?.assigned_user_id ? "#1d4ed8" : "#047857"}}>{thread.mop?.status === "human_required" ? "Нужен человек" : thread.dialog?.assigned_user_id ? "Менеджер ведёт диалог" : "МОП ведёт диалог"}</div>
          <div style={{ ...muted, marginTop: 3 }}>{thread.dialog?.assigned_user_id ? `В работе у ${thread.dialog.assigned_name || "сотрудника"}` : "Свободен — сотрудник не назначен"}{selectedAttention?.reasons?.length ? ` · ${selectedAttention.reasons.map(x => x.label).join(" · ")}` : ""}</div>
          {selectedAttention?.reasons?.length ? <div style={{...muted,marginTop:4}}><b>Почему внимание:</b> {selectedAttention.reasons.map(x=>x.detail || x.label).join(" · ")}</div> : null}
          <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap:"wrap" }}>
            {!thread.dialog?.assigned_mine ? <button disabled={busy || !!thread.dialog?.assigned_user_id} onClick={() => handoff("take")} style={btn}>Взять диалог</button> : <button disabled={busy} onClick={() => handoff("release")} style={btn}>Вернуть в общую очередь</button>}
            <button disabled={busy} onClick={()=>mopHandoff("human")} style={btn}>Передать человеку</button>
            <button disabled={busy} onClick={()=>mopHandoff("mop")} style={btn}>Вернуть МОП</button>
            <a href={`/messages/mop-training?account_id=${encodeURIComponent(accountId)}&chat_id=${encodeURIComponent(chatId)}`} style={{...btn,textDecoration:"none"}}>Обучить МОП</a>
          </div><div style={{display:"grid",gridTemplateColumns:"1fr auto",gap:6,marginTop:7}}><input value={reactivationReason} onChange={e=>setReactivationReason(e.target.value)} placeholder="Причина/контекст реактивации: цена, расчёт, размеры, замер, КП…" style={{border:"1px solid #dbe3ee",borderRadius:9,padding:8,fontSize:11}}/><button disabled={busy} onClick={sendToReactivation} style={btn}>В реактивацию</button></div>
        </div> : null}
        <div style={{display:"flex",gap:5,flexWrap:"wrap",marginTop:8}}>{attentionFilters.map(([k,l])=><button key={k} onClick={()=>setAttentionFilter(k)} style={{...btn,padding:"5px 7px",background:attentionFilter===k?"#eff6ff":"#fff",color:attentionFilter===k?"#1d4ed8":"#334155"}}>{l}</button>)}</div>
        <div style={{ marginTop: 8, maxHeight: 290, overflowY: "auto" }}>
          {visibleAttention.map((x) => <button key={`${x.account_id}:${x.avito_chat_id}`} onClick={() => onOpenDialog(x.account_id, x.avito_chat_id)} style={{ width: "100%", textAlign: "left", border: "none", borderTop: "1px solid #f1f5f9", background: x.avito_chat_id === chatId ? "#f8fbff" : "#fff", padding: "9px 4px", cursor: "pointer" }}><div style={{display:"flex",justifyContent:"space-between",gap:8}}><div style={{ fontSize: 11, fontWeight: 800 }}>{x.client_name || "Клиент"}</div>{x.wait_label ? <span style={{fontSize:9,color:"#64748b"}}>{x.wait_label}</span>:null}</div><div style={{ ...muted, marginTop: 2 }}>{x.reasons[0]?.label || "Требует внимания"}{x.reasons.length>1 ? ` · ещё ${x.reasons.length-1}`:""}{x.assigned_name ? ` · ведёт ${x.assigned_name}` : ""}</div>{x.item_title ? <div style={{fontSize:9,color:"#64748b",marginTop:2}}>{x.item_title}</div>:null}{x.last_text ? <div style={{fontSize:10,color:"#475569",marginTop:3,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{x.last_text}</div>:null}{x.why_first ? <div style={{fontSize:10,color:"#334155",marginTop:3}}><b>Почему сейчас:</b> {x.why_first}</div> : null}</button>)}
          {!visibleAttention.length ? <div style={{ ...muted, padding: "12px 0" }}>По выбранному фильтру сейчас нет диалогов.</div> : null}
        </div>
      </div> : null}
    </div>
  );
}
