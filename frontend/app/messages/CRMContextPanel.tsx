"use client";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

import { useEffect, useMemo, useState } from "react";
import styles from "./CRMContextPanel.module.css";


// BORIS_CRM_BRIDGE_AUTH_V1
function crmAuthHeaders(
  extra?: Record<string, string>
): Record<string, string> {
  const token =
    typeof window !== "undefined"
      ? (
          localStorage.getItem("boris_token") ||
          localStorage.getItem("token") ||
          ""
        )
      : "";

  return {
    ...(extra || {}),
    ...(token
      ? { Authorization: "Bearer " + token }
      : {}),
  };
}


type Stage = {
  id: number;
  name: string;
  position: number;
  semantic_type: string;
};

type Task = {
  id: number;
  title: string;
  description?: string | null;
  due_at?: string | null;
  status: string;
};

type Deal = {
  id: number;
  title: string;
  amount_kopeks?: number | null;
  stage_id?: number | null;
  stage_name?: string | null;
  contact_name?: string | null;
  contact_phone?: string | null;
  contact_email?: string | null;
  source?: string | null;
  status: string;
};

type Context = {
  ok: boolean;
  deal: Deal | null;
  stages: Stage[];
  tasks: Task[];
  conversation?: { id: number; contact_id?: number | null; contact_name?: string | null; contact_phone?: string | null } | null;
  deal_trigger?: string;
  deal_eligible?: boolean;
  maturity?: Record<string, any>;
  qualification?: { intent?: string; qualification_stage?: string; lead_temperature?: string; target_action?: string; phone_received?: boolean; crm_action?: string; next_action?: string; reactivation_candidate?: boolean; human_handoff?: boolean; handoff_reason?: string | null; qualification_fields?: Record<string,string|null> };
  dialog_goal?: string | null;
  goal_progress?: string | null;
  manager_next_action?: { kind?: string; title?: string; reason?: string; task_id?: number; due_at?: string | null; suggested_due_hours?: number; requires_confirmation?: boolean };
  lead?: {
    id: number;
    stage?: string | null;
    stage_source?: string | null;
    has_phone?: boolean | null;
    msg_count?: number | null;
    assigned_user_id?: number | null;
    contact_name?: string | null;
  } | null;
  mop?: {
    id: number;
    status?: string | null;
    ai_summary?: string | null;
    reply_text?: string | null;
    updated_at?: string | null;
  } | null;
  rop?: {
    id: number;
    analysis?: {
      score?: number;
      outcome?: string;
      recommendation?: string;
    } | null;
    created_at?: string | null;
  } | null;
  history?: Array<{
    id: number;
    activity_type?: string | null;
    title?: string | null;
    body?: string | null;
    created_at?: string | null;
  }>;
};

type Props = {
  accountId: string;
  chatId: string;
};

async function api<T>(
  url: string,
  options?: RequestInit,
): Promise<T> {
  // BORIS_CRM_PANEL_FULL_AUTH_V2
  // Every CRM request from this panel uses the same BORIS Bearer token.
  const token =
    typeof window !== "undefined"
      ? (
          localStorage.getItem("boris_token") ||
          localStorage.getItem("token") ||
          ""
        )
      : "";

  const headers = new Headers(options?.headers || {});

  if (token) {
    headers.set("Authorization", "Bearer " + token);
  }

  const response = await fetch(url, {
    credentials: "include",
    cache: "no-store",
    ...options,
    headers,
  });

  const body = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(
      body?.detail ||
      body?.message ||
      `Ошибка сервера, код ${response.status}`,
    );
  }

  return body as T;
}

function money(value?: number | null) {
  if (value === null || value === undefined) return "—";

  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(value / 100);
}

export default function CRMContextPanel({
  accountId,
  chatId,
}: Props) {
  const [data, setData] = useState<Context | null>(null);
  const [loadedKey, setLoadedKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [amount, setAmount] = useState("");
  const [task, setTask] = useState("");
  const [taskDueAt, setTaskDueAt] = useState("");
  const [phoneInput, setPhoneInput] = useState("");
  const [nextActionInput, setNextActionInput] = useState("");
  const [intentInput, setIntentInput] = useState("");
  const [targetActionInput, setTargetActionInput] = useState("");
  const taskAssignee = data?.lead?.assigned_user_id ? `Ответственный #${data.lead.assigned_user_id}` : "Я";

  async function sync() {
    if (!accountId || !chatId) return;

    setLoading(true);
    setError("");

    try {
      const result = await api<Context>(
        "/api/crm/bridge/context/sync",
        {
          method: "POST",
          headers: crmAuthHeaders({
            "Content-Type": "application/json",
          }),
          body: JSON.stringify({
            account_id: accountId,
            avito_chat_id: chatId,
          }),
        },
      );

      setData(result);
      setLoadedKey(`${accountId}:${chatId}`);

      const v = result.deal?.amount_kopeks;

      setAmount(
        v === null || v === undefined
          ? ""
          : String(Math.round(v / 100)),
      );
    } catch (e) {
      setData(null);
      setLoadedKey("");
      setError(
        e instanceof Error
          ? e.message
          : "Ошибка системы продаж",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setData(null);
    setLoadedKey("");
    sync();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId, chatId]);

  useEffect(() => {
    const later = (event: Event) => { const d=(event as CustomEvent).detail||{}; if(d.account_id!==accountId||d.avito_chat_id!==chatId)return; setTask(data?.qualification?.next_action || "Вернуться к клиенту"); const x=new Date(Date.now()+2*60*60*1000); setTaskDueAt(new Date(x.getTime()-x.getTimezoneOffset()*60000).toISOString().slice(0,16)); };
    window.addEventListener("boris-manager-remind-later",later as EventListener);
    return ()=>window.removeEventListener("boris-manager-remind-later",later as EventListener);
  }, [accountId,chatId,data?.qualification?.next_action]);

  const visibleData = loadedKey === `${accountId}:${chatId}` ? data : null;
  const deal = visibleData?.deal || null;

  const openTasks = useMemo(
    () =>
      (visibleData?.tasks || []).filter(
        (item) => item.status === "open",
      ),
    [data],
  );

  async function changeStage(stageId: number) {
    if (!deal) return;

    await api(
      `/api/crm/bridge/deal/${deal.id}/stage`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          stage_id: stageId,
        }),
      },
    );

    await sync();
  }

  async function saveAmount() {
    if (!deal) return;

    const rubles = Number(
      amount.replace(/\s/g, "").replace(",", "."),
    );

    if (!Number.isFinite(rubles) || rubles < 0) {
      setError("Некорректная сумма");
      return;
    }

    await api(
      `/api/crm/bridge/deal/${deal.id}`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          amount_kopeks: Math.round(rubles * 100),
        }),
      },
    );

    await sync();
  }

  async function completeTask(taskId: number) {
    try {
      await api(`/api/crm/product/tasks/${taskId}`, {
        method: "PATCH",
        headers: crmAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ status: "done" }),
      });
      await sync();
      if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("boris-manager-action-completed", {detail:{account_id:accountId,avito_chat_id:chatId}}));
    } catch (e: any) { setError(e?.message || "Не удалось завершить задачу"); }
  }

  async function updateDialogState(body: Record<string, any>) {
    try {
      setError("");
      const result = await api<any>(`/api/messenger/sales/dialog-state?account_id=${encodeURIComponent(accountId)}&avito_chat_id=${encodeURIComponent(chatId)}`, {
        method: "POST",
        headers: crmAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      });
      if (result?.status !== "ok") throw new Error(result?.message || "Не удалось обновить состояние клиента");
      setPhoneInput("");
      setNextActionInput("");
      await sync();
    } catch (e: any) { setError(e?.message || "Не удалось обновить состояние клиента"); }
  }

  async function createTask() {
    const contactId = (deal as any)?.contact_id || visibleData?.conversation?.contact_id || undefined;
    if ((!deal && !contactId) || !task.trim()) return;

    await api(
      "/api/crm/sales-tasks",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          deal_id: deal?.id || undefined,
          contact_id: contactId,
          title: task.trim(),
          due_at: taskDueAt ? new Date(taskDueAt).toISOString() : undefined,
          assigned_user_id: visibleData?.lead?.assigned_user_id || undefined,
        }),
      },
    );

    setTask("");
    setTaskDueAt("");
    await sync();
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("boris-manager-action-completed", {detail:{account_id:accountId,avito_chat_id:chatId}}));
  }

  async function createReminderNow(hours = 24, title?: string) {
    const contactId = (deal as any)?.contact_id || visibleData?.conversation?.contact_id || undefined;
    if (!deal && !contactId) { setError("Сначала дождитесь создания контекста клиента"); return; }
    const due = new Date(Date.now() + Math.max(1, hours) * 60 * 60 * 1000);
    try {
      setError("");
      await api("/api/crm/sales-tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          deal_id: deal?.id || undefined,
          contact_id: contactId,
          title: title || visibleData?.qualification?.next_action || "Вернуться к клиенту",
          due_at: due.toISOString(),
          assigned_user_id: visibleData?.lead?.assigned_user_id || undefined,
        }),
      });
      await sync();
      if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("boris-manager-action-completed", {detail:{account_id:accountId,avito_chat_id:chatId}}));
    } catch (e:any) { setError(e?.message || "Не удалось поставить напоминание"); }
  }

  async function runPrimaryAction() {
    const a = visibleData?.manager_next_action;
    if (!a) return;
    if (a.kind === "reminder") { await createReminderNow(Number(a.suggested_due_hours || 24), a.title || visibleData?.qualification?.next_action); return; }
    if (a.kind === "call") {
      const phone = deal?.contact_phone || visibleData?.conversation?.contact_phone;
      if (phone && typeof window !== "undefined") window.location.href = `tel:${phone}`;
      return;
    }
    if (a.kind === "complete_task" && a.task_id) { await completeTask(a.task_id); return; }
    if (a.kind === "reactivation") {
      const reason = a.reason || visibleData?.qualification?.next_action || "Клиенту нужен follow-up";
      await api<any>("/api/reactivation/handoff", {method:"POST",headers:crmAuthHeaders({"Content-Type":"application/json"}),body:JSON.stringify({account_id:accountId,avito_chat_id:chatId,reason:"manager_followup",summary:reason})});
      await sync();
      if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("boris-manager-action-completed", {detail:{account_id:accountId,avito_chat_id:chatId}}));
      return;
    }
    if (a.kind === "take_human") {
      if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("boris-manager-primary-action", {detail:{kind:"take_human",account_id:accountId,avito_chat_id:chatId}}));
      return;
    }
    if (a.kind === "reply") {
      if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("boris-manager-primary-action", {detail:{kind:"focus_reply",account_id:accountId,avito_chat_id:chatId}}));
      return;
    }
    const title = a.title || "Следующее действие по клиенту";
    setTask(title);
    const hours = Number(a.suggested_due_hours || 0);
    if (hours > 0) {
      const d = new Date(Date.now()+hours*60*60*1000);
      setTaskDueAt(new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16));
    }
  }

  const q = visibleData?.qualification || {};
  const funnelStage = q.qualification_stage === "TARGET_ACTION" ? "Целевое действие" : q.phone_received || visibleData?.lead?.has_phone ? "Получен контакт" : q.qualification_stage === "QUALIFIED" ? "Квалифицирован" : Number(visibleData?.maturity?.incoming_count || 0) >= 2 ? "В диалоге" : "Первичное обращение — клиент ещё не вовлечён в диалог";
  const goalProgressLabel = ({
    GOAL_NOT_STARTED: "Не начата",
    GOAL_IN_PROGRESS: "В работе",
    GOAL_ACHIEVED: "Достигнута",
    GOAL_DECLINED: "Клиент отказался",
    HUMAN_HANDOFF: "Передано человеку",
  } as Record<string,string>)[String(visibleData?.goal_progress || "")] || "Пока недостаточно данных";
  const clientWant = q.intent || q.target_action || visibleData?.mop?.ai_summary || "Пока недостаточно данных";
  const qualificationFields = Object.entries(q.qualification_fields || {}).filter(([,value]) => value !== null && value !== undefined && String(value).trim()).slice(0, 8);
  const mopDid = visibleData?.mop?.status === "human_required"
    ? "Передал диалог человеку"
    : visibleData?.mop?.reply_text
      ? (String(visibleData?.mop.status || "").toLowerCase() === "sent" ? "Ответил клиенту" : "Подготовил ответ клиенту")
      : visibleData?.mop?.ai_summary
        ? visibleData?.mop.ai_summary
        : "Пока недостаточно данных";
  const qualificationLabel = ({INQUIRY:"Новый",ENGAGED:"В диалоге",QUALIFIED:"Квалифицирован",TARGET_ACTION:"Целевое действие"} as Record<string,string>)[String(q.qualification_stage||"").toUpperCase()] || "Пока недостаточно данных";
  const currentAction = visibleData?.manager_next_action?.title || openTasks[0]?.title || q.next_action || "Продолжить диалог и уточнить потребность";
  const dealReason = deal
    ? (visibleData?.deal_eligible
        ? `Сделка существует · текущее правило «${visibleData?.deal_trigger || "настроенное условие"}» выполнено`
        : `Сделка уже существовала ранее · текущее правило «${visibleData?.deal_trigger || "настроенное условие"}» сейчас не выполнено`)
    : visibleData?.deal_eligible
      ? "Условие сделки выполнено; система обновляет сделку"
      : `Сделка ещё не создана: условие «${visibleData?.deal_trigger || "после вовлечения"}» пока не выполнено`;

  return (
    <div

     className={`${styles.root} boris-f3-c4949a9ffeeb90`}>
      <div

       className="boris-f3-2c5c6d8b7ddbf2">
        <div>
          <div

           className="boris-f3-f66dbb9eae1772">
            Сделка
          </div>

          <div

           className="boris-f3-c3ef85fe05ffde">
            BORIS · Avito
          </div>
        </div>

        <button
          onClick={sync}
          disabled={loading}

         className="boris-f3-4a7c2ccf111957">
          {loading ? "…" : "Обновить"}
        </button>
      </div>

      <div className="boris-f3-3b2cdac893f750">
        {error ? (
          <div

           className="boris-f3-6b53d4fc62ba94">
            {error}
          </div>
        ) : null}

        {visibleData ? <div style={{padding:14,borderRadius:12,background:"#eff6ff",border:"1px solid #bfdbfe",marginBottom:10}}>
          <div style={{fontSize:10,fontWeight:900,color:"#64748b",letterSpacing:'.06em'}}>КЛИЕНТ ЗА 5 СЕКУНД</div>
          <div style={{fontSize:16,fontWeight:900,marginTop:4}}>{visibleData?.conversation?.contact_name || visibleData?.lead?.contact_name || deal?.contact_name || "Клиент Avito"}</div>
          <div style={{fontSize:12,color:"#334155",marginTop:4}}><b>Что хочет:</b> {clientWant}</div>
          {qualificationFields.length ? <div style={{display:"grid",gap:4,marginTop:8,padding:"8px 9px",borderRadius:8,background:"#fff",border:"1px solid #dbeafe"}}>
            <div style={{fontSize:10,fontWeight:900,color:"#1d4ed8",letterSpacing:'.04em'}}>СОБРАННЫЕ ДАННЫЕ ЗАЯВКИ</div>
            {qualificationFields.map(([key,value]) => <div key={key} style={{fontSize:11,color:"#334155"}}><b>{key}:</b> {String(value)}</div>)}
          </div> : null}
          {(deal?.contact_phone || visibleData?.conversation?.contact_phone || visibleData?.lead?.has_phone) ? <div style={{marginTop:7,padding:"7px 9px",borderRadius:8,background:"#ecfdf5",fontSize:12,fontWeight:850}}>Телефон получен{(deal?.contact_phone || visibleData?.conversation?.contact_phone) ? <> · <a href={`tel:${deal?.contact_phone || visibleData?.conversation?.contact_phone}`}>{deal?.contact_phone || visibleData?.conversation?.contact_phone}</a></> : null}</div> : null}
          {visibleData?.mop?.status === "human_required" ? <div style={{marginTop:7,fontSize:11,fontWeight:800,color:"#92400e"}}>МОП запросил человека — диалог ждёт решения менеджера{q.handoff_reason ? ` · Причина: ${q.handoff_reason}` : ""}</div> : null}
          {q.reactivation_candidate ? <div style={{marginTop:7,fontSize:11,fontWeight:800,color:"#9a3412"}}>Клиент отмечен для реактивации</div> : null}
          <div style={{marginTop:10,paddingTop:10,borderTop:"1px solid #bfdbfe"}}><div style={{fontSize:10,fontWeight:900,color:"#1d4ed8",letterSpacing:'.06em'}}>ЧТО СДЕЛАТЬ СЕЙЧАС</div><div style={{fontSize:16,fontWeight:900,marginTop:4}}>{currentAction}</div></div>
          {visibleData?.manager_next_action?.reason ? <div style={{fontSize:11,color:"#475569",marginTop:4,lineHeight:1.45}}>{humanizeBusinessText(visibleData?.manager_next_action.reason, "Следующее действие определено BORIS") }</div> : null}
          <div className="boris-manager-brief-grid" style={{display:"grid",gridTemplateColumns:"repeat(2,minmax(0,1fr))",gap:7,marginTop:10,fontSize:11,lineHeight:1.4}}>
            <div><b>Что уже произошло:</b><br/>{visibleData?.maturity?.incoming_count || 0} входящих · {visibleData?.maturity?.outgoing_count || 0} исходящих</div><div><b>МОП сделал:</b><br/>{mopDid}</div>
            <div><b>Стадия:</b><br/>{funnelStage}</div><div><b>Цель:</b><br/>{visibleData?.dialog_goal || "Пока недостаточно данных"} · {goalProgressLabel}</div>
          </div>
          {visibleData?.manager_next_action ? <div><button onClick={runPrimaryAction} style={{marginTop:10,border:0,borderRadius:9,background:"#2563eb",color:"#fff",fontWeight:850,padding:"9px 13px",cursor:"pointer"}}>{visibleData?.manager_next_action.kind === "call" ? "Позвонить" : visibleData?.manager_next_action.kind === "complete_task" ? "Отметить выполненной" : visibleData?.manager_next_action.kind === "reactivation" ? "Передать в реактивацию" : visibleData?.manager_next_action.kind === "take_human" ? "Взять диалог" : visibleData?.manager_next_action.kind === "reply" ? "Перейти к ответу" : visibleData?.manager_next_action.kind === "reminder" ? "Поставить напоминание" : "Создать задачу"}</button>{visibleData?.manager_next_action.requires_confirmation ? <span style={{fontSize:10,color:"#64748b",marginLeft:8}}>BORIS подготовит; изменение подтвердит менеджер</span> : null}</div> : null}
          <details style={{marginTop:9}}><summary style={{fontSize:10,fontWeight:800,color:"#64748b",cursor:"pointer"}}>Другие действия</summary><div style={{display:"flex",gap:7,flexWrap:"wrap",marginTop:7}}>{(deal?.contact_phone || visibleData?.conversation?.contact_phone) ? <button onClick={()=>navigator.clipboard.writeText(String(deal?.contact_phone || visibleData?.conversation?.contact_phone))} style={{border:"1px solid #dbe3ee",background:"#fff",borderRadius:8,padding:"6px 8px",fontSize:10,cursor:"pointer"}}>Скопировать телефон</button> : null}{deal ? <a href={`/dashboard/crm/deal/${deal.id}`} style={{fontSize:10}}>Карточка клиента</a> : null}<a href={`/messages/mop-training?account_id=${encodeURIComponent(accountId)}&chat_id=${encodeURIComponent(chatId)}`} style={{fontSize:10}}>Обучить МОП</a></div></details>
        </div> : null}

        {visibleData ? <details style={{padding:10,borderRadius:10,background:"#f8fafc",marginBottom:10,fontSize:11,lineHeight:1.55}}><summary style={{fontWeight:850,cursor:"pointer"}}>Подробнее о клиенте и сделке</summary>
          <div style={{fontWeight:850,fontSize:12}}>Воронка: Новый → В диалоге → Квалифицирован → Получен контакт → Целевое действие → Сделка / Реактивация</div>
          <div style={{marginTop:5}}><b>Сейчас:</b> {funnelStage}</div>
          <div><b>Намерение:</b> {q.intent || "Пока недостаточно данных"} · <b>Температура:</b> {q.lead_temperature || "Пока недостаточно данных"}</div>
          <div><b>Этап квалификации:</b> {qualificationLabel} · <b>Цель диалога:</b> {visibleData?.dialog_goal || "Пока недостаточно данных"}</div>
          <div><b>Источник:</b> Avito</div>
          <div><b>Телефон:</b> {deal?.contact_phone || visibleData?.conversation?.contact_phone || (visibleData?.lead?.has_phone ? "получен" : "Пока недостаточно данных")} · <b>Ответственный:</b> {visibleData?.lead?.assigned_user_id ? "назначен менеджер" : "не назначен"}</div>
          <div><b>Следующее действие:</b> {openTasks[0]?.title || q.next_action || "Пока недостаточно данных"}</div>
          <div><b>Ближайшее напоминание:</b> {openTasks.find((x:any)=>x.due_at)?.due_at ? `${openTasks.find((x:any)=>x.due_at)?.title} · ${new Date(String(openTasks.find((x:any)=>x.due_at)?.due_at || "")).toLocaleString("ru-RU")}` : "нет"}</div>
          <div><b>Сделка:</b> {dealReason} · <b>Реактивация:</b> {q.reactivation_candidate ? "рекомендуется BORIS" : "не отмечена"}</div>
          <div><b>Краткая память:</b> {visibleData?.history?.[0]?.body || visibleData?.mop?.ai_summary || "история диалога сохранена; отдельной заметки пока нет"}</div>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6,marginTop:8}}>
            <select value={q.qualification_stage || ""} onChange={(e)=>e.target.value && updateDialogState({qualification_stage:e.target.value})} style={{border:"1px solid #dbe3ee",borderRadius:8,padding:7,fontSize:11}}><option value="">Изменить этап…</option><option value="INQUIRY">Новый</option><option value="ENGAGED">В диалоге</option><option value="QUALIFIED">Квалифицирован</option><option value="TARGET_ACTION">Целевое действие</option></select>
            <select value={q.lead_temperature || ""} onChange={(e)=>e.target.value && updateDialogState({lead_temperature:e.target.value})} style={{border:"1px solid #dbe3ee",borderRadius:8,padding:7,fontSize:11}}><option value="">Температура…</option><option value="cold">Холодный</option><option value="warm">Тёплый</option><option value="hot">Горячий</option></select>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"1fr auto",gap:6,marginTop:6}}><input value={intentInput} onChange={(e)=>setIntentInput(e.target.value)} placeholder={q.intent || "Что хочет клиент"} style={{border:"1px solid #dbe3ee",borderRadius:8,padding:7,fontSize:11}}/><button onClick={()=>intentInput.trim()&&updateDialogState({intent:intentInput})} style={{border:"1px solid #dbe3ee",borderRadius:8,background:"#fff",padding:"7px 9px",fontSize:11,cursor:"pointer"}}>Сохранить</button></div>
          <div style={{display:"grid",gridTemplateColumns:"1fr auto",gap:6,marginTop:6}}><input value={targetActionInput} onChange={(e)=>setTargetActionInput(e.target.value)} placeholder={q.target_action || "Целевое действие"} style={{border:"1px solid #dbe3ee",borderRadius:8,padding:7,fontSize:11}}/><button onClick={()=>targetActionInput.trim()&&updateDialogState({target_action:targetActionInput})} style={{border:"1px solid #dbe3ee",borderRadius:8,background:"#fff",padding:"7px 9px",fontSize:11,cursor:"pointer"}}>Цель</button></div><div style={{display:"flex",gap:6,marginTop:6,alignItems:"center",fontSize:11}}><span style={{fontWeight:750}}>Реактивация:</span><button onClick={()=>updateDialogState({reactivation_candidate:true})} style={{border:"1px solid #dbe3ee",borderRadius:8,background:q.reactivation_candidate?"#fff7ed":"#fff",padding:"6px 8px",cursor:"pointer"}}>Нужна</button><button onClick={()=>updateDialogState({reactivation_candidate:false})} style={{border:"1px solid #dbe3ee",borderRadius:8,background:q.reactivation_candidate===false?"#f8fafc":"#fff",padding:"6px 8px",cursor:"pointer"}}>Не нужна</button></div>
          <div style={{display:"grid",gridTemplateColumns:"1fr auto",gap:6,marginTop:6}}><input value={phoneInput} onChange={(e)=>setPhoneInput(e.target.value)} placeholder="Сохранить телефон" style={{border:"1px solid #dbe3ee",borderRadius:8,padding:7,fontSize:11}}/><button onClick={()=>phoneInput.trim()&&updateDialogState({phone:phoneInput})} style={{border:"1px solid #dbe3ee",borderRadius:8,background:"#fff",padding:"7px 9px",fontSize:11,cursor:"pointer"}}>Сохранить</button></div>
          <div style={{display:"grid",gridTemplateColumns:"1fr auto",gap:6,marginTop:6}}><input value={nextActionInput} onChange={(e)=>setNextActionInput(e.target.value)} placeholder={q.next_action || "Задать следующее действие"} style={{border:"1px solid #dbe3ee",borderRadius:8,padding:7,fontSize:11}}/><button onClick={()=>nextActionInput.trim()&&updateDialogState({next_action:nextActionInput})} style={{border:"1px solid #dbe3ee",borderRadius:8,background:"#fff",padding:"7px 9px",fontSize:11,cursor:"pointer"}}>Сохранить</button></div>
          <div style={{display:"flex",gap:6,flexWrap:"wrap",marginTop:8}}>
            {(deal?.contact_phone || visibleData?.conversation?.contact_phone) ? <><a href={`tel:${deal?.contact_phone || visibleData?.conversation?.contact_phone}`} style={{fontSize:11,fontWeight:800}}>Позвонить</a><button onClick={()=>navigator.clipboard.writeText(String(deal?.contact_phone || visibleData?.conversation?.contact_phone))} style={{border:"none",background:"transparent",padding:0,fontSize:11,fontWeight:800,cursor:"pointer",color:"#2563eb"}}>Скопировать телефон</button></> : null}
            <button onClick={()=>{setTask(q.next_action || "Связаться с клиентом");}} style={{border:"none",background:"transparent",padding:0,fontSize:11,fontWeight:800,cursor:"pointer",color:"#2563eb"}}>Создать задачу</button>
            <button onClick={()=>{setTask(q.next_action || "Перезвонить клиенту"); if(!taskDueAt){const d=new Date(Date.now()+60*60*1000);setTaskDueAt(new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16));}}} style={{border:"none",background:"transparent",padding:0,fontSize:11,fontWeight:800,cursor:"pointer",color:"#2563eb"}}>Напомнить</button>
            {deal ? <a href={`/dashboard/crm/deal/${deal.id}`} style={{fontSize:11,fontWeight:800}}>Открыть карточку клиента</a> : visibleData?.conversation?.contact_id ? <a href={`/dashboard/crm/contact/${visibleData?.conversation.contact_id}`} style={{fontSize:11,fontWeight:800}}>Открыть карточку клиента</a> : null}
            <a href={`/reactivation?account_id=${encodeURIComponent(accountId)}&chat_id=${encodeURIComponent(chatId)}`} style={{fontSize:11,fontWeight:800}}>Перейти к реактивации</a>
            <a href={`/messages/mop-training?account_id=${encodeURIComponent(accountId)}&chat_id=${encodeURIComponent(chatId)}`} style={{fontSize:11,fontWeight:800}}>Обучить МОП на этом диалоге</a>
          </div>
        </details> : null}

        {!deal ? (
          <div

           className="boris-f3-498e792763208b">
            {loading ? "Связываем диалог с клиентом…" : visibleData?.lead ? (
              <><b>Обращение сохранено · сделка пока не создана</b><br />
              Клиент: {visibleData?.conversation?.contact_name || visibleData?.lead?.contact_name || "Клиент Avito"} · стадия: {visibleData?.lead?.stage || "новый"} · сообщений: {visibleData?.lead?.msg_count ?? 0}.<br />
              Правило сделки: {visibleData?.deal_trigger || "after_engaged"}. {visibleData?.mop?.ai_summary ? `МОП: ${visibleData?.mop.ai_summary}` : "Квалификация появится в результате штатного ответа МОП."}
              {(visibleData?.history || []).length ? <><br />История сохранена: {(visibleData?.history || []).length} событий.</> : null}
              {visibleData?.conversation?.contact_id ? <div style={{display:"grid",gap:6,marginTop:9}}>
                {openTasks.slice(0,3).map((item)=><div key={`predeal-task-${item.id}`} style={{fontSize:11}}>{item.title}{item.due_at ? ` · ${new Date(item.due_at).toLocaleString("ru-RU")}` : ""}<button onClick={()=>completeTask(item.id)} style={{marginLeft:7,border:"none",background:"transparent",cursor:"pointer"}}>✓ Выполнено</button></div>)}
                <input value={task} onChange={(e)=>setTask(e.target.value)} placeholder={q.next_action || "Задача по обращению"} style={{border:"1px solid #dbe3ee",borderRadius:8,padding:7,fontSize:11}} />
                <input type="datetime-local" value={taskDueAt} onChange={(e)=>setTaskDueAt(e.target.value)} aria-label="Дата и время напоминания" style={{border:"1px solid #dbe3ee",borderRadius:8,padding:7,fontSize:11}} />
                <button onClick={createTask} disabled={!task.trim()} style={{border:"1px solid #dbe3ee",borderRadius:8,background:"#fff",padding:7,fontSize:11,cursor:"pointer"}}>{taskDueAt ? "Создать напоминание" : "Создать задачу"}</button>
              </div> : null}</>
            ) : "Контекст клиента пока не создан"}
          </div>
        ) : (
          <>
            <div

             className="boris-f3-6f9f802d7b3c18">
              {deal.title}
            </div>

            <div

             className="boris-f3-a13d02a318d979">
              Сделка #{deal.id} ·{" "}
              {deal.contact_name || "Клиент Avito"}
            </div>

            <div className="boris-f3-8d842990309a24">
              <div

               className="boris-f3-e60f9b654ccb5f">
                ЭТАП
              </div>

              <select
                value={deal.stage_id || ""}
                onChange={(e) =>
                  changeStage(Number(e.target.value))
                }

               className="boris-f3-ffc54e8f8c8b2d">
                {(visibleData?.stages || []).map((stage) => (
                  <option
                    key={stage.id}
                    value={stage.id}
                  >
                    {stage.name}
                  </option>
                ))}
              </select>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3,minmax(0,1fr))",
                gap: 8,
                marginTop: 10,
              }}
            >
              <div style={{ padding: 10, borderRadius: 10, background: "#f8fafc" }}>
                <div style={{ fontSize: 10, color: "#64748b", fontWeight: 800 }}>ЛИД</div>
                <div style={{ marginTop: 4, fontSize: 12, fontWeight: 700 }}>
                  {visibleData?.lead?.stage || "—"}
                </div>
                <div style={{ marginTop: 2, fontSize: 10, color: "#94a3b8" }}>
                  {visibleData?.lead?.msg_count ?? 0} сообщений
                </div>
              </div>

              <div style={{ padding: 10, borderRadius: 10, background: "#f8fafc" }}>
                <div style={{ fontSize: 10, color: "#64748b", fontWeight: 800 }}>МОП</div>
                <div style={{ marginTop: 4, fontSize: 12, fontWeight: 700 }}>
                  {humanizeBusinessText(visibleData?.mop?.status, "нет черновика")}
                </div>
                <div style={{ marginTop: 2, fontSize: 10, color: "#94a3b8" }}>
                  {visibleData?.mop?.reply_text ? "контекст связан" : "история доступна"}
                </div>
              </div>

              <div style={{ padding: 10, borderRadius: 10, background: "#f8fafc" }}>
                <div style={{ fontSize: 10, color: "#64748b", fontWeight: 800 }}>РОП</div>
                <div style={{ marginTop: 4, fontSize: 12, fontWeight: 700 }}>
                  {visibleData?.rop?.analysis?.score !== undefined
                    ? `${visibleData?.rop.analysis.score}/100`
                    : "нет разбора"}
                </div>
                <div style={{ marginTop: 2, fontSize: 10, color: "#94a3b8" }}>
                  {(visibleData?.history || []).length} событий по клиенту
                </div>
              </div>
            </div>

            <div style={{ marginTop: 10, padding: 10, borderRadius: 10, background: "#f8fafc" }}>
              <div style={{ fontSize: 10, color: "#64748b", fontWeight: 800 }}>
                СЛЕДУЮЩЕЕ ДЕЙСТВИЕ
              </div>
              <div style={{ marginTop: 4, fontSize: 12, fontWeight: 700 }}>
                {openTasks[0]?.title || visibleData?.qualification?.next_action || visibleData?.rop?.analysis?.recommendation || "Связаться с клиентом и уточнить следующий шаг"}
              </div>
              {visibleData?.qualification?.qualification_stage ? (
                <div style={{ marginTop: 6, fontSize: 11, color: "#475569", lineHeight: 1.4 }}>
                  МОП: {funnelStage} · {visibleData?.qualification.lead_temperature || "температура не определена"}{visibleData?.qualification.intent ? ` · ${visibleData?.qualification.intent}` : ""}{visibleData?.qualification.target_action ? ` · цель: ${visibleData?.qualification.target_action}` : ""}
                </div>
              ) : visibleData?.mop?.ai_summary ? (
                <div style={{ marginTop: 6, fontSize: 11, color: "#475569", lineHeight: 1.4 }}>МОП: {visibleData?.mop.ai_summary}</div>
              ) : null}
              {visibleData?.rop?.analysis?.outcome ? (
                <div style={{ marginTop: 4, fontSize: 11, color: "#475569", lineHeight: 1.4 }}>
                  РОП: {visibleData?.rop.analysis.outcome}
                </div>
              ) : null}
            </div>

            {(visibleData?.history || []).length ? (
              <div style={{ marginTop: 10, padding: 10, borderRadius: 10, background: "#f8fafc" }}>
                <div style={{ fontSize: 10, color: "#64748b", fontWeight: 800 }}>
                  ИСТОРИЯ КЛИЕНТА
                </div>
                {(visibleData?.history || []).slice(0, 3).map((event) => (
                  <div key={event.id} style={{ marginTop: 6, fontSize: 11, lineHeight: 1.35 }}>
                    <strong>{event.title || event.activity_type || "Событие"}</strong>
                    {event.body ? ` · ${event.body}` : ""}
                  </div>
                ))}
              </div>
            ) : null}

            <div className="boris-f3-8d842990309a24">
              <div

               className="boris-f3-e60f9b654ccb5f">
                СУММА
              </div>

              <div

               className="boris-f3-ccc58d95e35d92">
                <input
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  placeholder="Введите сумму"

                 className="boris-f3-d01070ab13a19e"/>

                <button
                  onClick={saveAmount}

                 className="boris-f3-a122ebe11b0046">
                  OK
                </button>
              </div>

              <div

               className="boris-f3-bbe9134a2c00dc">
                {money(deal.amount_kopeks)}
              </div>
            </div>

            <div

             className="boris-f3-0f445ca363883c">
              <div

               className="boris-f3-cf13c7e924d5de">
                КЛИЕНТ
              </div>

              <div

               className="boris-f3-010f4a5aa9ce9f">
                {deal.contact_name || "Клиент Avito"}
              </div>

              {deal.contact_phone ? (
                <div

                 className="boris-f3-6e9ec83579afee">
                  {deal.contact_phone}
                </div>
              ) : null}
            </div>

            <div

             className="boris-f3-0f445ca363883c">
              <div

               className="boris-f3-0bd4472dd16fd0">
                <span>ЗАДАЧИ</span>
                <span>{openTasks.length}</span>
              </div>

              {openTasks.slice(0, 4).map((item) => (
                <div
                  key={item.id}

                 className="boris-f3-2fb72d88840e0b">
                  <span>{item.title}{item.due_at ? ` · ${new Date(item.due_at).toLocaleString("ru-RU")}` : ""}</span>
                  <button onClick={() => completeTask(item.id)} style={{ marginLeft: 8, border: "none", background: "transparent", cursor: "pointer", fontSize: 11 }}>✓ Выполнено</button>
                </div>
              ))}

              <div className="boris-f3-a4fd46d562e45c" style={{ display: "grid", gap: 6 }}>
                {visibleData?.qualification?.next_action && !task ? <button onClick={()=>setTask(visibleData?.qualification?.next_action || "")} style={{border:"1px solid #dbe3ee",background:"#eff6ff",borderRadius:9,padding:8,cursor:"pointer",fontSize:11,textAlign:"left"}}>Создать из рекомендации BORIS: {visibleData?.qualification.next_action}</button> : null}
                <input
                  value={task}
                  onChange={(e) => setTask(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !taskDueAt) createTask(); }}
                  placeholder="Следующий шаг / вернуться позже"
                  className="boris-f3-b0121d9a975c1f"/>
                <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6}}><input value={taskAssignee} readOnly aria-label="Ответственный" title="Задача создаётся на текущего менеджера в существующем CRM contract" className="boris-f3-b0121d9a975c1f"/>
                <input
                  type="datetime-local"
                  value={taskDueAt}
                  onChange={(e) => setTaskDueAt(e.target.value)}
                  aria-label="Дата и время напоминания"
                  className="boris-f3-b0121d9a975c1f"/></div>
                <button onClick={createTask} className="boris-f3-ae1a3a5677f5aa">
                  {taskDueAt ? "Создать напоминание" : "Создать задачу"}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
