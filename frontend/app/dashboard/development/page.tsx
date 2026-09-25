"use client";

import {useCallback,useEffect,useLayoutEffect,useMemo,useState,useSyncExternalStore} from "react";
import styles from "./page.module.css";
import AppShell from "@/app/components/shell/AppShell";
import {humanizeBusinessText} from "@/app/lib/humanizeBusinessText";

type Thread={id:string;thread_type:string;title:string;account_id?:string|null;external_chat_ref?:string|null;status:string;summary?:string;current_state?:Record<string,any>;plan?:any[];source?:Record<string,any>;event_count?:number;last_event_at?:string;updated_at?:string};
type EventRow={id:number|string;event_type:string;actor?:string;text?:string;payload?:Record<string,any>;created_at?:string};
type Activity={source:string;id:string;thread_id?:string|null;account_id?:string|null;kind:string;status:string;title:string;created_at?:string;payload?:Record<string,any>};

const STATUS:Record<string,string>={active:"В работе",waiting:"Ожидает",blocked:"Заблокирована",done:"Готово",archived:"Архив"};
const TYPE:Record<string,string>={task:"Задача",chat:"Рабочий чат",project:"Проект"};
const RUNTIME:Record<string,string>={queued:"В очереди",planning:"Подготовка",running:"Выполняется",review:"Проверка результата",review_requested:"Ждёт проверки результата",testing:"Проверка",fixing:"Исправление",acceptance:"Приёмка результата",completed:"Выполнено",done:"Выполнено",blocked:"Заблокировано",blocked_infra:"Технический блокер",waiting:"Ожидает",pass:"Работает",failed:"Ошибка"};
// Видимые пользователю технические фразы переводятся одним общим helper.
const runtimeText=(v:any)=>RUNTIME[String(v||"").toLowerCase()]||humanizeBusinessText(v,"Не запущено");

function authHeaders(json=false):Record<string,string>{
  const token=localStorage.getItem("boris_token")||"";
  const out:Record<string,string>={Authorization:`Bearer ${token}`};
  if(json)out["Content-Type"]="application/json";
  return out;
}
const dt=(v?:string)=>v?new Date(v).toLocaleString("ru-RU"):"—";
const progress=(t?:Thread|null)=>{
  const v=t?.current_state?.progress_percent;
  return v===null||v===undefined||Number.isNaN(Number(v))?null:Math.max(0,Math.min(100,Number(v)));
};
const remainingTime=(t?:Thread|null)=>{
  const v=t?.current_state?.remaining_minutes;
  if(v!==null&&v!==undefined&&Number.isFinite(Number(v))&&Number(v)>=0)return Number(v)<60?`около ${Math.ceil(Number(v))} мин`:`около ${Math.ceil(Number(v)/60)} ч`;
  const eta=t?.current_state?.estimated_completion_at||t?.current_state?.eta_at;
  if(eta){const ms=new Date(String(eta)).getTime()-Date.now();if(Number.isFinite(ms)&&ms>0)return ms<3600000?`около ${Math.ceil(ms/60000)} мин`:`около ${Math.ceil(ms/3600000)} ч`;}
  if(progress(t)===100)return "0 — задача подтверждённо завершена";
  if(opsLinkPending(t))return "не рассчитывается — BORIS восстанавливает связь с существующей задачей";
  if(!t?.current_state?.dev_job_id)return "не рассчитывается — исполняемая работа ещё не запущена";
  if(ownerNeeded(t))return "не рассчитывается — требуется внешнее действие владельца";
  if(providerWaiting(t)){
    const retry=t?.current_state?.provider_retry_at;
    if(retry){
      const ms=new Date(String(retry)).getTime()-Date.now();
      if(Number.isFinite(ms)&&ms>0){
        const total=Math.max(1,Math.ceil(ms/60000));
        const hours=Math.floor(total/60), mins=total%60;
        const wait=hours>0?(hours+" ч"+(mins?(" "+mins+" мин"):"")):(total+" мин");
        const at=new Date(String(retry)).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"});
        return "до следующей автоматической попытки "+wait+" · в "+at;
      }
    }
    return "не рассчитывается — BORIS ждёт восстановления ИИ и продолжит сам";
  }
  if(selfManagedWait(t)&&!actuallyRunning(t))return "не рассчитывается — задача ждёт очереди, проверки или автоматического исправления";
  return "BORIS ещё не рассчитал точное время";
};
const blockerOf=(t?:Thread|null)=>humanizeBusinessText(t?.current_state?.executor_blocker||t?.current_state?.executor_wait_reason||t?.current_state?.block_reason,"");
const phaseDone=(t?:Thread|null)=>t?.current_state?.ops_phase_passed??t?.current_state?.phase_passed;
const phaseTotal=(t?:Thread|null)=>t?.current_state?.ops_phase_total??t?.current_state?.phase_total;
const autopilotActive=(t?:Thread|null)=>Boolean(t?.current_state?.dev_job_id&&t?.current_state?.autopilot_enabled===true);
const opsLinkPending=(t?:Thread|null)=>Boolean(t?.current_state?.ops_job_id&&!t?.current_state?.dev_job_id);
const autopilotText=(t?:Thread|null)=>{
  const s=t?.current_state||{};
  if(s.autopilot_enabled===false)return "На паузе после текущего шага";
  if(opsLinkPending(t))return "BORIS восстанавливает связь с существующей задачей";
  if(!s.dev_job_id)return "Ещё не запущен";
  const state=String(s.autopilot_state||"").toLowerCase();
  if(state==="done")return "Завершён — результат подтверждён";
  if(state==="waiting")return humanizeBusinessText(s.executor_wait_reason,"Ждёт свободного ИИ-исполнителя");
  if(state==="blocked")return humanizeBusinessText(s.executor_blocker||s.executor_wait_reason,"Остановлен из-за подтверждённого блокера");
  return "Автопилот работает — сам до результата";
};
const rawBlockerOf=(t?:Thread|null)=>String(t?.current_state?.executor_blocker||t?.current_state?.executor_wait_reason||t?.current_state?.block_reason||"");
const orderState=(t?:Thread|null)=>String(t?.current_state?.order_status||"").toLowerCase();
const ownerNeeded=(t?:Thread|null)=>{
  const s=t?.current_state||{};
  const order=orderState(t);
  if(s.owner_action_required===true||order==="blocked_human")return true;
  if(order==="blocked_infra")return false;
  const b=rawBlockerOf(t).toLowerCase();
  return Boolean(b&&/(owner approval|owner-only|admin approval|credential required|secret required|требуется решение владельца|нужен владелец|нужен admin|юридическое решение|финансовое решение|платное действие)/i.test(b));
};
const providerWaiting=(t?:Thread|null)=>/WAITING_FREE_CODING_PROVIDER|provider_unavailable|all free development providers unavailable|review provider quota|daily quota|RATE_LIMITED/i.test(rawBlockerOf(t));
const actuallyRunning=(t?:Thread|null)=>{
  const s=t?.current_state||{};
  if(!t||t.status==="done"||s.autopilot_enabled===false||ownerNeeded(t)||rawBlockerOf(t))return false;
  const order=orderState(t);
  if(order)return order==="running";
  const st=String(s.executor_status||s.runtime_status||"").toLowerCase();
  return ["running","testing","fixing"].includes(st);
};
const selfManagedWait=(t?:Thread|null)=>{
  if(!t||t.status==="done"||actuallyRunning(t)||ownerNeeded(t))return false;
  const s=t.current_state||{};
  const st=String(s.executor_status||s.runtime_status||t.status||"").toLowerCase();
  return Boolean(rawBlockerOf(t)||["active","waiting","blocked"].includes(t.status)||["queued","waiting","review","review_requested","blocked","blocked_infra","planning","acceptance"].includes(st));
};
const operationalLabel=(t?:Thread|null)=>{
  if(t?.status==="done"||progress(t)===100)return "ГОТОВО";
  if(ownerNeeded(t))return "НУЖНО ВАШЕ ДЕЙСТВИЕ";
  if(actuallyRunning(t))return "ВЫПОЛНЯЕТСЯ СЕЙЧАС";
  if(providerWaiting(t))return "ЖДЁТ ИИ — ВКЛЮЧИТСЯ САМ";
  if(t?.current_state?.autopilot_enabled===false)return "ПАУЗА";
  return "BORIS ЖДЁТ / ИСПРАВЛЯЕТ САМ";
};
const factCount=(done:any,total:any,label:string)=>{
  if(total===null||total===undefined)return null;
  const d=Number(done||0),t=Number(total||0);
  if(!Number.isFinite(t)||t<=0)return null;
  return `${label}: ${Math.max(0,d)} из ${t}`;
};
const completedFacts=(t?:Thread|null)=>{
  const s=t?.current_state||{};
  const parts=[
    factCount(s.steps_done,s.steps_total,"шагов"),
    factCount(phaseDone(t),phaseTotal(t),"этапов"),
    factCount(s.criteria_done,s.criteria_total,"критериев"),
    factCount(s.evidence_passed,s.evidence_total,"подтверждений"),
  ].filter(Boolean);
  if(progress(t)===100)return "Работа завершена и подтверждена.";
  return parts.length?parts.join(" · "):"Фактическое выполнение идёт, но измеримые счётчики ещё не записаны.";
};
const remainingFacts=(t?:Thread|null)=>{
  const s=t?.current_state||{};
  const parts:string[]=[];
  const add=(done:any,total:any,label:string)=>{
    if(total===null||total===undefined)return;
    const d=Number(done||0),tt=Number(total||0);
    if(Number.isFinite(tt)&&tt>0)parts.push(`${label}: ${Math.max(0,tt-d)}`);
  };
  add(s.steps_done,s.steps_total,"шагов осталось");
  add(phaseDone(t),phaseTotal(t),"этапов осталось");
  add(s.criteria_done,s.criteria_total,"критериев осталось");
  add(s.evidence_passed,s.evidence_total,"подтверждений осталось");
  if(progress(t)===100)return "Ничего — результат подтверждён.";
  return parts.length?parts.join(" · "):humanizeBusinessText(s.next_step,"Следующий измеримый шаг ещё не зафиксирован.");
};
const progressStateText=(t?:Thread|null)=>{
  const p=progress(t);
  if(p!==null)return `Подтверждённый прогресс: ${p}%`;
  if(ownerNeeded(t))return "Сейчас остановлено — требуется конкретное действие владельца.";
  if(actuallyRunning(t))return "Выполняется сейчас. Процент появится только после измеримого подтверждённого результата.";
  if(providerWaiting(t))return "Сейчас не выполняется — BORIS ждёт свободный ИИ и сам продолжит после восстановления.";
  if(selfManagedWait(t))return "Сейчас не выполняется — BORIS сам ждёт очередь, проверку или устраняет внутренний блокер.";
  return "Процент ещё не подтверждён фактическим выполнением.";
};
const taskTitleText=(value:any)=>{
  const raw=String(value||"").trim();
  if(!raw)return "Задача BORIS";
  const exact:Record<string,string>={
    "V8.2 — real autonomous tool canary":"V8.2 — проверка реальной автономной работы инструмента",
    "Replace the fragile autonomous development lifecycle with one deterministic server-side state machine.":"Заменить хрупкий цикл разработки на один надёжный серверный механизм состояний",
    "Apply and production-verify the 20 percent active-listing title experiment program, exact mapping self-heal, batching an":"Проверить в production эксперимент заголовков на 20% активных объявлений и автоматическое восстановление соответствий",
    "RESTORE_EXISTING_BORIS_PRODUCTION_AI":"Восстановить существующие рабочие ИИ-контуры BORIS",
    "Title cohort 20 percent runtime":"Проверка эксперимента заголовков на 20% объявлений",
    "Email outreach":"Почтовые касания и отправка писем",
    "ONE_ACTION_AUTONOMOUS_DEVELOPMENT":"Автономная разработка по одной команде",
  };
  if(exact[raw])return exact[raw];
  const dash=raw.indexOf(" — ");
  if(dash>0){
    const tail=raw.slice(dash+3).trim();
    if(/[А-Яа-яЁё]/.test(tail))return humanizeBusinessText(tail,tail);
  }
  const human=humanizeBusinessText(raw,"");
  if(human&&/[А-Яа-яЁё]/.test(human))return human;
  return "Техническая задача BORIS";
};
const businessValue=(text?:string,explicit?:unknown)=>{
  if(explicit!==null&&explicit!==undefined&&String(explicit).trim())return String(explicit).trim();
  const s=String(text||"").toLowerCase();
  if(/авито|объяв|публикац|фид|реклам/.test(s))return "Клиент не теряет рекламный трафик и обращения из-за ошибок публикации или управления объявлениями.";
  if(/сообщ|моп|диалог|ответ/.test(s))return "Клиент быстрее получает ответы на входящие, а бизнес теряет меньше горячих лидов.";
  if(/crm|лид|сделк|ворон|роп/.test(s))return "Лиды не зависают без следующего шага, а продажи становятся управляемыми и измеримыми.";
  if(/телефон|звон|атс/.test(s))return "Снижается риск пропущенных обращений, а звонки попадают в общий контроль продаж.";
  if(/supervisor|health|monitor|self-heal|watchdog|контрол|восстанов/.test(s))return "BORIS сам обнаруживает сбой, пытается исправить его и зовёт владельца только когда без человека нельзя.";
  if(/меню|страниц|интерфейс|ui|ux/.test(s))return "Владелец быстрее понимает состояние системы и меньше работает оператором вместо BORIS.";
  if(/оплат|billing|тариф|период/.test(s))return "Оплаченная услуга не должна остановиться из-за ошибки учёта периода или тарифа.";
  return "Уменьшает ручной контроль владельца и делает оказание услуги клиенту стабильнее и проверяемее.";
};

// DEVELOPMENT_PROJECTION_NO_CLIENT_OVERLAP_V1:
// один Workspace/Control Plane остаётся источником истины, но это окно показывает
// только работу разработки. Клиентские runtime Task/ControlExecution остаются в
// Мозге и в «Клиентах» и не превращаются здесь в дублирующие задачи разработки.
const DEVELOPMENT_SURFACES=new Set(["development","journal"]);
function isDevelopmentThread(t:Thread){
  if(t.thread_type==="client")return false;
  const surface=String(t.source?.surface||"");
  const source=String(t.source?.source||"");
  const ref=String(t.external_chat_ref||"");
  if(surface==="control-plane"||source==="system-brain")return false;
  if(Boolean(t.current_state?.dev_job_id)||ref.startsWith("ops-job:"))return true;
  // Восстановленная из истории память без реального dev/OPS job — не онлайн-задача.
  // Она остаётся в Мозге, но не создаёт ложную карточку "Автопилот ещё не запущен".
  if(source=="recovered_context"&&!t.current_state?.ops_job_id)return false;
  if(DEVELOPMENT_SURFACES.has(surface))return true;
  if(source==="legacy_task"||source==="control_execution")return false;
  if(t.account_id)return false;
  return ["task","chat","project"].includes(t.thread_type);
}

const PRIVATE_OWNER_EMAIL="ostapenko-kirill-86@yandex.ru";
const subscribeOwnerSession=(onStoreChange:()=>void)=>{
  if(typeof window==="undefined")return()=>{};
  const handler=()=>onStoreChange();
  const timer=window.setTimeout(handler,0);
  window.addEventListener("storage",handler);
  window.addEventListener("boris-owner-session",handler as EventListener);
  return()=>{window.clearTimeout(timer);window.removeEventListener("storage",handler);window.removeEventListener("boris-owner-session",handler as EventListener)};
};
const getOwnerSessionSnapshot=()=>{
  if(typeof window==="undefined")return false;
  try{return String(localStorage.getItem("boris_user_email")||"").trim().toLowerCase()===PRIVATE_OWNER_EMAIL}catch{return false}
};
const getOwnerServerSnapshot=()=>false;

export default function DevelopmentPage(){
  const [authState,setAuthState]=useState<"checking"|"allowed"|"denied">("checking");
  // OWNER_AUTH_SERVER_CONFIRM_V1: localStorage email is only a UX hint.
  // Owner-only API requests start only after /api/auth/me confirms platform_admin.
  useSyncExternalStore(subscribeOwnerSession,getOwnerSessionSnapshot,getOwnerServerSnapshot);
  const allowed=authState==="allowed";
  const [loading,setLoading]=useState(true);
  const [threads,setThreads]=useState<Thread[]>([]);
  const [activity,setActivity]=useState<Activity[]>([]);
  const [selectedId,setSelectedId]=useState("");
  const [selected,setSelected]=useState<Thread|null>(null);
  const [events,setEvents]=useState<EventRow[]>([]);
  const [query,setQuery]=useState("");
  const [kind,setKind]=useState("all");
  const [status,setStatus]=useState("open");
  const [error,setError]=useState("");
  const [syncText,setSyncText]=useState("");
  const [prompt,setPrompt]=useState("");
  const [promptBusy,setPromptBusy]=useState(false);
  const [autopilotBusy,setAutopilotBusy]=useState(false);
  const [createOpen,setCreateOpen]=useState(false);
  const [newTitle,setNewTitle]=useState("");
  const [newSummary,setNewSummary]=useState("");
  const [newType,setNewType]=useState("task");
  const [ownerNote,setOwnerNote]=useState("");
  const [lastRefresh,setLastRefresh]=useState("");

  useLayoutEffect(()=>{
    let alive=true;
    const check=async()=>{
      const token=localStorage.getItem("boris_token")||"";
      if(!token){if(alive)setAuthState("denied");return;}
      try{
        const r=await fetch("/api/auth/me?surface=development",{headers:{Authorization:`Bearer ${token}`},cache:"no-store"});
        const d=await r.json();const ok=Boolean(r.ok&&d?.user?.platform_admin===true);
        if(!alive)return;
        if(ok){
          if(d?.user?.email)localStorage.setItem("boris_user_email",String(d.user.email));
          localStorage.setItem("boris_user_role",String(d.user.role||"owner"));
          window.dispatchEvent(new Event("boris-owner-session"));
          setAuthState("allowed");
        }else setAuthState("denied");
      }catch{if(alive)setAuthState("denied");}
    };
    void check();return()=>{alive=false};
  },[]);

  const openThread=useCallback(async(id:string)=>{
    if(!id||!allowed)return;
    try{
      const r=await fetch(`/api/control-plane/workspace/threads/${id}`,{headers:authHeaders(),cache:"no-store"});
      const d=await r.json();
      if(!r.ok)throw new Error(d?.detail||`Не удалось открыть задачу: ${r.status}`);
      setSelectedId(id);setSelected(d.thread||null);setEvents(Array.isArray(d.events)?d.events:[]);
    }catch(e){setError(e instanceof Error?e.message:"Ошибка открытия задачи");}
  },[allowed]);

  const loadAll=useCallback(async()=>{
    if(!allowed)return;
    setLoading(true);setError("");setSyncText("Подключаю незавершённые задачи…");
    try{
      // FIRST PAINT: читаем уже синхронизированную проекцию без тяжёлой
      // глобальной материализации и без N executor-readback на каждую строку.
      const fast=await fetch("/api/control-plane/workspace/threads?status=open&projection=development&sync_runtime=false&refresh_executor=false&limit=500",{headers:authHeaders(),cache:"no-store"});
      const fastData=await fast.json();
      if(!fast.ok)throw new Error(fastData?.detail||`Незавершённые задачи: ${fast.status}`);
      const openList=(Array.isArray(fastData)?fastData:[]).filter(isDevelopmentThread);
      setThreads(openList);setLastRefresh(new Date().toLocaleTimeString("ru-RU"));
      const preferred=openList[0];
      if(!selectedId&&preferred)void openThread(preferred.id);
      else if(selectedId&&openList.some((x:Thread)=>x.id===selectedId))void openThread(selectedId);
      setLoading(false);
      setSyncText(`Онлайн: ${openList.length} незавершённых задач. Сверяю полную историю…`);

      // DEEP READ: development truth синхронизирует сам BORIS dispatcher
      // через ext_dev_jobs/ext_a2a_orders -> workspace_sync. Открытие страницы
      // только читает эту проекцию и никогда не материализует клиентский runtime.
      const [tr,ar]=await Promise.all([
        fetch("/api/control-plane/workspace/threads?projection=development&sync_runtime=false&refresh_executor=false&limit=2000",{headers:authHeaders(),cache:"no-store"}),
        fetch("/api/control-plane/workspace/activity?projection=development&limit=120&sync_runtime=false",{headers:authHeaders(),cache:"no-store"})
      ]);
      const td=await tr.json();const ad=await ar.json();
      if(!tr.ok)throw new Error(td?.detail||`Журнал задач: ${tr.status}`);
      if(!ar.ok)throw new Error(ad?.detail||`Лента задач: ${ar.status}`);
      const list=(Array.isArray(td)?td:[]).filter(isDevelopmentThread);
      const devIds=new Set(list.map((x:Thread)=>x.id));
      const devActivity=(Array.isArray(ad?.items)?ad.items:[]).filter((x:Activity)=>Boolean(x.thread_id&&devIds.has(String(x.thread_id))));
      setThreads(list);setActivity(devActivity);setLastRefresh(new Date().toLocaleTimeString("ru-RU"));
      setSyncText(`Синхронизировано: ${list.length} веток разработки · незавершённых ${list.filter((x:Thread)=>["active","waiting","blocked"].includes(x.status)).length}`);
      if(selectedId&&list.some((x:Thread)=>x.id===selectedId))void openThread(selectedId);
    }catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить разработку");setSyncText("");}
    finally{setLoading(false);}
  },[allowed,selectedId,openThread]);

  useEffect(()=>{void loadAll()},[allowed]); // быстрый первый экран + глубокая сверка

  const refreshOpen=useCallback(async()=>{
    if(!allowed)return;
    try{
      // ONLINE_TRUTH_REPLACE_V1: fetch the whole lightweight development
      // projection, not only status=open. Merging only open rows left a task
      // visually "running" forever after it actually became done/blocked.
      // Replacing the snapshot makes every 15-second tick reflect terminal and
      // waiting transitions without requiring the owner to press Refresh.
      const r=await fetch("/api/control-plane/workspace/threads?projection=development&sync_runtime=false&refresh_executor=false&limit=2000",{headers:authHeaders(),cache:"no-store"});
      const d=await r.json();if(!r.ok)return;
      const live=(Array.isArray(d)?d:[]).filter(isDevelopmentThread);
      setThreads(live);
      setLastRefresh(new Date().toLocaleTimeString("ru-RU"));
      if(selectedId&&live.some((x:Thread)=>x.id===selectedId))void openThread(selectedId);
    }catch{}
  },[allowed,selectedId,openThread]);
  useEffect(()=>{if(!allowed)return;const timer=window.setInterval(()=>{void refreshOpen()},15000);return()=>window.clearInterval(timer)},[allowed,refreshOpen]);

  const filtered=useMemo(()=>{
    const q=query.trim().toLowerCase();
    return threads.filter(t=>{
      if(kind!=="all"&&t.thread_type!==kind)return false;
      if(status==="open"&&!["active","waiting","blocked"].includes(t.status))return false;
      if(status==="running_now"&&!actuallyRunning(t))return false;
      if(status==="self_managed"&&!selfManagedWait(t))return false;
      if(status==="owner_action"&&!ownerNeeded(t))return false;
      if(!["all","open","running_now","self_managed","owner_action"].includes(status)&&t.status!==status)return false;
      if(!q)return true;
      return [t.title,t.summary,t.account_id,t.external_chat_ref,t.id].some(v=>String(v||"").toLowerCase().includes(q));
    });
  },[threads,query,kind,status]);

  const runningNow=threads.filter(actuallyRunning).length;
  const selfManaged=threads.filter(selfManagedWait).length;
  const ownerActions=threads.filter(ownerNeeded).length;
  const providerWaitCount=threads.filter(providerWaiting).length;
  const nextProviderRetryThread=threads
    .filter(t=>providerWaiting(t)&&t.current_state?.provider_retry_at)
    .sort((a,b)=>new Date(String(a.current_state?.provider_retry_at)).getTime()-new Date(String(b.current_state?.provider_retry_at)).getTime())[0]||null;
  const done=threads.filter(x=>x.status==="done").length;

  async function sendPrompt(){
    if(!selected||!prompt.trim()||promptBusy)return;
    if(opsLinkPending(selected)){setError("BORIS восстанавливает связь с существующей задачей. Новая команда временно не создаётся, чтобы не получить дубль.");return;}
    setPromptBusy(true);setError("");
    try{
      const commandId=typeof crypto!=="undefined"&&"randomUUID" in crypto?crypto.randomUUID():String(Date.now());
      const r=await fetch(`/api/control-plane/workspace/threads/${selected.id}/prompt`,{method:"POST",headers:authHeaders(true),body:JSON.stringify({text:prompt.trim(),command_id:commandId})});
      const d=await r.json();if(!r.ok)throw new Error(d?.detail||"Команда не поставлена");
      setPrompt("");await openThread(selected.id);await loadAll();
    }catch(e){setError(e instanceof Error?e.message:"Ошибка постановки команды");}
    finally{setPromptBusy(false);}
  }

  async function setAutopilot(enabled:boolean){
    if(!selected||autopilotBusy)return;
    setAutopilotBusy(true);setError("");
    try{
      const r=await fetch(`/api/control-plane/workspace/threads/${selected.id}/autopilot`,{method:"POST",headers:authHeaders(true),body:JSON.stringify({enabled})});
      const d=await r.json().catch(()=>null);if(!r.ok)throw new Error(d?.detail||"Не удалось изменить автопилот");
      await openThread(selected.id);await loadAll();
    }catch(e){setError(e instanceof Error?e.message:"Ошибка управления автопилотом");}
    finally{setAutopilotBusy(false);}
  }

  async function patchStatus(next:string){
    if(!selected)return;
    try{
      const r=await fetch(`/api/control-plane/workspace/threads/${selected.id}`,{method:"PATCH",headers:authHeaders(true),body:JSON.stringify({status:next})});
      const d=await r.json().catch(()=>null);if(!r.ok)throw new Error(d?.detail||"Статус не изменён");
      await openThread(selected.id);await loadAll();
    }catch(e){setError(e instanceof Error?e.message:"Ошибка статуса");}
  }

  async function saveOwnerNote(){
    if(!selected||!ownerNote.trim())return;
    try{
      const r=await fetch(`/api/control-plane/workspace/threads/${selected.id}/progress`,{method:"POST",headers:authHeaders(true),body:JSON.stringify({progress_percent:null,note:ownerNote.trim(),actor:"owner"})});
      const d=await r.json();if(!r.ok)throw new Error(d?.detail||"Комментарий не сохранён");
      setOwnerNote("");await openThread(selected.id);
    }catch(e){setError(e instanceof Error?e.message:"Ошибка сохранения");}
  }

  async function createThread(){
    const title=newTitle.trim(),summary=newSummary.trim();
    if(!title)return;
    try{
      const r=await fetch("/api/control-plane/workspace/threads",{method:"POST",headers:authHeaders(true),body:JSON.stringify({
        title,thread_type:newType,status:"active",summary,source:{surface:"development",owner_only:true,autopilot:true}
      })});
      const d=await r.json();if(!r.ok)throw new Error(d?.detail||"Не удалось создать задачу");
      const threadId=String(d?.thread?.id||"");
      if(threadId){
        const commandId=typeof crypto!=="undefined"&&"randomUUID" in crypto?crypto.randomUUID():`start-${Date.now()}`;
        const command=summary||title;
        const start=await fetch(`/api/control-plane/workspace/threads/${threadId}/prompt`,{method:"POST",headers:authHeaders(true),body:JSON.stringify({text:command,command_id:commandId})});
        const started=await start.json().catch(()=>null);
        if(!start.ok)throw new Error(started?.detail||"Задача создана, но автопилот не запустился");
      }
      setCreateOpen(false);setNewTitle("");setNewSummary("");await loadAll();if(threadId)await openThread(threadId);
    }catch(e){setError(e instanceof Error?e.message:"Ошибка создания задачи");}
  }

  if(authState==="denied")return <AppShell active="journal" title="Разработка и задачи"><main className={styles.page}><section className={styles.hero}><div><span className={styles.eyebrow}>BORIS · ДОСТУП</span><h1>Админский доступ не подтверждён</h1><p>Сервер не подтвердил твой платформенный админский аккаунт.</p><div className={styles.topLinks}><a href="/login">Войти</a><a href="/dashboard">Главная</a></div></div></section></main></AppShell>;

  return <AppShell active="journal" title="Онлайн-задачи и разработка" subtitle="Что делаем, зачем это нужно и какой результат уже получен"><main className={styles.page}>
    <section className={styles.hero}>
      <div>
        <span className={styles.eyebrow}>BORIS · ВЛАДЕЛЕЦ · РАЗРАБОТКА</span>
        <h1>Онлайн-задачи и разработка: что делаем, зачем и какой результат</h1>
        <p><b>Зачем это делаем:</b> чтобы задачи BORIS доводились до результата без ручного диспетчерства владельца, а по каждой работе было понятно, какую пользу она даёт системе и клиенту.</p>
        <p>Здесь видно текущий шаг, фактический прогресс, следующий шаг и настоящий блокер — без необходимости разбираться в технических журналах.</p>
        <div className={styles.chips}><span>Рабочие задачи</span><span>Обновляется автоматически</span><span>Проверка результата</span><span>Команды BORIS</span></div>
      </div>
      <aside className={styles.safeCard}>
        <span>🛠</span><strong>Контроль разработки</strong><p>{providerWaitCount>0?`Сейчас реально выполняются: ${runningNow}. Ещё ${providerWaitCount} задач ждут свободный ИИ и запустятся сами после восстановления.`:syncText||`Сейчас реально выполняются: ${runningNow}. BORIS сам ведёт очередь и внутренние блокеры.`}</p>{nextProviderRetryThread?<p><b>Следующая попытка ИИ:</b> {remainingTime(nextProviderRetryThread)}</p>:null}<p><b>Онлайн:</b> {lastRefresh?`обновлено ${lastRefresh}, автопроверка каждые 15 сек · от вас действий: ${ownerActions}`:"подключаю…"}</p>
        <div className={styles.topLinks}><a href="/dashboard/control-plane">Мозг</a><a href="/dashboard/clients-control">Клиенты</a><a href="/dashboard">Главная</a></div>
      </aside>
    </section>

    <section className={styles.metrics}>
      <article><span>▶</span><div><small>Выполняются сейчас</small><strong>{runningNow}</strong><p>есть живой исполнитель и реальное выполнение</p></div></article>
      <article><span>◴</span><div><small>BORIS ждёт / исправляет сам</small><strong>{selfManaged}</strong><p>вам ничего нажимать не нужно</p></div></article>
      <article><span>!</span><div><small>Нужно ваше действие</small><strong>{ownerActions}</strong><p>только настоящий внешний блокер</p></div></article>
      <article><span>✓</span><div><small>Готово</small><strong>{done}</strong><p>результат подтверждён</p></div></article>
    </section>

    {error?<div className={styles.error}>{error}</div>:null}

    <section className={styles.toolbar}>
      <div className={styles.filters}>
        <input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Найти задачу, проект, аккаунт или номер…"/>
        <select value={kind} onChange={e=>setKind(e.target.value)}><option value="all">Все типы</option><option value="task">Задачи</option><option value="project">Проекты</option><option value="chat">Рабочие чаты</option></select>
        <select value={status} onChange={e=>setStatus(e.target.value)}><option value="open">Все незавершённые</option><option value="running_now">Выполняются сейчас</option><option value="self_managed">BORIS ждёт / исправляет сам</option><option value="owner_action">Нужно моё действие</option><option value="all">Все статусы</option><option value="done">Готово</option><option value="archived">Архив</option></select>
      </div>
      <div className={styles.toolbarActions}><button className={styles.ghost} onClick={()=>setCreateOpen(v=>!v)}>+ Новая задача</button><button className={styles.ghost} onClick={()=>void loadAll()} disabled={loading}>{loading?"Синхронизация…":"Автообновление · обновить сейчас"}</button></div>
    </section>

    {createOpen?<section className={styles.createBox}>
      <div><span className={styles.eyebrow}>НОВАЯ РАБОЧАЯ ВЕТКА</span><h2>Зафиксировать задачу или проект</h2></div>
      <div className={styles.createGrid}><select value={newType} onChange={e=>setNewType(e.target.value)}><option value="task">Задача</option><option value="project">Проект</option><option value="chat">Рабочий чат</option></select><input value={newTitle} onChange={e=>setNewTitle(e.target.value)} placeholder="Название"/></div>
      <textarea value={newSummary} onChange={e=>setNewSummary(e.target.value)} rows={3} placeholder="Что нужно сделать, где остановились, критерий готовности"/>
      <div className={styles.actionRow}><span>После создания BORIS сразу начнёт работу и будет сам продолжать её до подтверждённого результата или настоящего блокера.</span><button onClick={()=>void createThread()} disabled={!newTitle.trim()}>Создать и запустить</button></div>
    </section>:null}

    <section className={styles.workspace}>
      <aside className={styles.rail}>
        <div className={styles.railHead}><div><span>РАБОЧИЕ ВЕТКИ</span><h2>{filtered.length}</h2></div><small>из {threads.length}</small></div>
        <div className={styles.threadList}>
          {filtered.map(t=><button key={t.id} className={selectedId===t.id?styles.activeThread:""} onClick={()=>void openThread(t.id)}>
            <div className={styles.threadTop}><strong>{taskTitleText(t.title)}</strong><span>{operationalLabel(t)}</span></div>
            <p>{TYPE[t.thread_type]||humanizeBusinessText(t.thread_type,"Задача")}{t.account_id?` · клиент ${t.account_id}`:""}</p>
            <div className={styles.liveTaskState}><span>{operationalLabel(t)}</span><b>{humanizeBusinessText(t.current_state?.current_step,"Текущий шаг ещё не зафиксирован")}</b></div>
            <div className={styles.liveTaskMeta}><span>Автопилот: {autopilotText(t)}</span><span>Выполнение: {runtimeText(t.current_state?.executor_status||t.current_state?.runtime_status)}</span>{t.current_state?.steps_total!=null?<span>Шаги: {t.current_state?.steps_done??0} из {t.current_state?.steps_total}</span>:null}{phaseTotal(t)!=null?<span>Этапы: {phaseDone(t)??0} из {phaseTotal(t)}</span>:null}{t.current_state?.criteria_total!=null?<span>Проверено: {t.current_state?.criteria_done??0} из {t.current_state?.criteria_total}</span>:null}{t.current_state?.evidence_total!=null?<span>Подтверждения: {t.current_state?.evidence_passed??0} из {t.current_state?.evidence_total}</span>:null}</div>
            {blockerOf(t)?<div className={styles.taskBlocker}><b>{ownerNeeded(t)?"НУЖНО ВАШЕ ДЕЙСТВИЕ":providerWaiting(t)?"ЖДЁТ ИИ — ВКЛЮЧИТСЯ САМ":"ВНУТРЕННИЙ БЛОКЕР — BORIS РЕШАЕТ САМ"}</b><span>{blockerOf(t)}</span></div>:null}
            {progress(t)!==null?<div className={styles.progressLine}><i style={{width:`${progress(t)}%`}}/><span>{progress(t)}%</span></div>:<div className={styles.progressUnknown}>{progressStateText(t)}</div>}
            <small>{t.event_count||0} событий · обновлено {dt(t.last_event_at||t.updated_at)} · осталось: {remainingTime(t)}</small>
          </button>)}
          {!loading&&filtered.length===0?<div className={styles.empty}>По фильтру задач нет. Общая база синхронизирована: {threads.length}.</div>:null}
        </div>
      </aside>

      <div className={styles.detail}>
        {!selected?<div className={styles.emptyCard}>Выберите задачу слева.</div>:<>
          <section className={styles.taskHero}>
            <div><span className={styles.eyebrow}>{TYPE[selected.thread_type]||humanizeBusinessText(selected.thread_type,"Задача")} · {operationalLabel(selected)}</span><h2>{taskTitleText(selected.title)}</h2><p>{humanizeBusinessText(selected.summary,"Описание задачи пока не зафиксировано простым языком.")}</p><p><b>Зачем это делаем:</b> {businessValue(`${selected.title} ${selected.summary||""} ${selected.current_state?.current_step||""}`,selected.current_state?.business_value||selected.current_state?.client_value||selected.source?.business_value)}</p><small>Номер задачи: {selected.id}</small></div>
            <div className={styles.taskActions}>{autopilotActive(selected)?<><button disabled>{autopilotText(selected)}</button><button className={styles.ghost} onClick={()=>void setAutopilot(false)} disabled={autopilotBusy}>{autopilotBusy?"Меняю…":"Пауза после текущего шага"}</button></>:opsLinkPending(selected)?<button disabled title="Новая задача не создаётся: BORIS восстанавливает связь с уже существующей рабочей задачей">BORIS восстанавливает связь</button>:<button onClick={()=>void setAutopilot(true)} disabled={autopilotBusy}>{autopilotBusy?"Запускаю…":"Запустить автопилот"}</button>}{(/^(task:|execution:|ops-job:)/.test(String(selected.external_chat_ref||""))||Boolean(selected.current_state?.dev_job_id))?<button className={styles.ghost} disabled title="Задача закрывается автоматически только после подтверждённого завершения">Готово — автоматически</button>:<button className={styles.ghost} onClick={()=>void patchStatus("done")}>Готово</button>}</div>
          </section>

          <section className={styles.progressBox}>
            <div className={styles.sectionHead}><span>ФАКТИЧЕСКИЙ ПРОГРЕСС</span><h2>{progressStateText(selected)}</h2><p>BORIS не придумывает проценты. Число появляется только когда есть измеримый факт выполнения или подтверждённый результат.</p></div>
            {progress(selected)!==null?<div className={styles.bigProgress}><i style={{width:`${progress(selected)}%`}}/></div>:<div className={styles.progressNotice}>{progressStateText(selected)}</div>}
            <div className={styles.stateGrid}>
              <article><small>Автопилот</small><strong>{autopilotText(selected)}</strong></article>
              <article><small>Режим</small><strong>Довести до подтверждённого результата</strong></article>
              {selected.current_state?.autopilot_iteration_limit?<article><small>Попытка текущего шага</small><strong>{Number(selected.current_state?.autopilot_iteration||0)+1} из {selected.current_state.autopilot_iteration_limit}</strong></article>:null}
              <article><small>Сейчас</small><strong>{humanizeBusinessText(selected.current_state?.current_step,"Не зафиксировано")}</strong></article>
              <article><small>Дальше</small><strong>{humanizeBusinessText(selected.current_state?.next_step,"Не зафиксировано")}</strong></article>
              <article><small>Что уже сделано</small><strong>{completedFacts(selected)}</strong></article>
              <article><small>Что осталось</small><strong>{remainingFacts(selected)}</strong></article>
              <article><small>Нужно ваше участие</small><strong>{ownerNeeded(selected)?`Да — ${blockerOf(selected)}`:"Нет. BORIS должен продолжать сам."}</strong></article>
              <article><small>Осталось времени</small><strong>{remainingTime(selected)}</strong></article>
              <article><small>Исполнитель</small><strong>{humanizeBusinessText(selected.current_state?.assigned_to||selected.current_state?.executor,"BORIS")}</strong></article>
              <article><small>Выполнение</small><strong>{runtimeText(selected.current_state?.executor_status||selected.current_state?.runtime_status)}</strong></article>
              <article><small>Этапы</small><strong>{phaseTotal(selected)!=null?`${phaseDone(selected)??0} из ${phaseTotal(selected)}`:"Пока не измерены"}</strong></article>
              <article><small>Критерии приёмки</small><strong>{selected.current_state?.criteria_total!=null?`${selected.current_state?.criteria_done??0} из ${selected.current_state?.criteria_total}`:"Пока не зафиксированы"}</strong></article>
              <article><small>Подтверждения результата</small><strong>{selected.current_state?.evidence_total!=null?`${selected.current_state?.evidence_passed??0} из ${selected.current_state?.evidence_total}`:"Пока нет измеримых подтверждений"}</strong></article>
              <article><small>Последняя проверка</small><strong>{selected.current_state?.last_evidence_type?humanizeBusinessText(`${selected.current_state.last_evidence_level||""} · ${selected.current_state.last_evidence_type}`,"Проверка выполнена"):"Пока не зафиксирована"}</strong></article>
            </div>
            {blockerOf(selected)?<div className={styles.detailBlocker}><b>{ownerNeeded(selected)?"НУЖНО ВАШЕ ДЕЙСТВИЕ":providerWaiting(selected)?"ЖДЁТ ИИ — ВКЛЮЧИТСЯ САМ":"ВНУТРЕННИЙ БЛОКЕР — BORIS РЕШАЕТ САМ"}</b><span>{blockerOf(selected)}</span></div>:null}
            <div className={styles.ownerNote}><input value={ownerNote} onChange={e=>setOwnerNote(e.target.value)} placeholder="Комментарий владельца — только если хотите добавить контекст"/><button onClick={()=>void saveOwnerNote()} disabled={!ownerNote.trim()}>Сохранить комментарий</button></div>
          </section>

          <section className={styles.commandBox}>
            <div className={styles.sectionHead}><span>ДОПОЛНИТЕЛЬНАЯ КОМАНДА</span><h2>{autopilotActive(selected)?"Вмешиваться не нужно: BORIS уже продолжает сам":opsLinkPending(selected)?"BORIS восстанавливает связь с существующей задачей":"Автопилот ещё не запущен"}</h2><p>{autopilotActive(selected)?"Пишите сюда только если хотите изменить задачу, добавить условие или новый приоритет. Для обычного «продолжай» ничего нажимать не надо.":opsLinkPending(selected)?"Новая задача специально не создаётся, чтобы не получить дубль. После восстановления связи эта карточка сама начнёт показывать фактическое состояние существующей рабочей задачи BORIS.":"Для этой старой ветки ещё нет активной исполняемой работы. Кнопка «Запустить автопилот» выше создаст её и BORIS продолжит задачу сам."}</p></div>
            <textarea value={prompt} onChange={e=>setPrompt(e.target.value)} rows={4} disabled={opsLinkPending(selected)} placeholder={opsLinkPending(selected)?"Сначала BORIS восстановит связь с существующей рабочей задачей.":"Например: дополнительно проверь мобильную версию и не закрывай задачу, пока сценарий регистрации не пройдёт полностью."}/>
            <div className={styles.actionRow}><span>{selected.current_state?.dev_job_id?`Исполняемая работа №${String(selected.current_state.dev_job_id).replace("dev_","")} · ${runtimeText(selected.current_state.executor_status)}`:opsLinkPending(selected)?"Новая команда временно заблокирована: BORIS восстанавливает связь без создания дубля.":"Дополнительная команда создаст новую исполняемую работу"}</span><button onClick={()=>void sendPrompt()} disabled={opsLinkPending(selected)||promptBusy||!prompt.trim()}>{opsLinkPending(selected)?"Ждём восстановления связи":promptBusy?"Ставлю…":"Добавить команду"}</button></div>
          </section>

          <section className={styles.eventsBox}>
            <div className={styles.sectionHead}><span>ИСТОРИЯ</span><h2>События постоянной ветки</h2></div>
            <div className={styles.events}>{events.slice().reverse().map(e=><article key={e.id}><div><strong>{humanizeBusinessText(e.text||e.event_type,"Системное событие")}</strong><small>{humanizeBusinessText(e.actor,"Система")} · {humanizeBusinessText(e.event_type,"событие")}</small></div><span>{dt(e.created_at)}</span></article>)}</div>
          </section>
        </>}
      </div>
    </section>

    <section className={styles.activityBox}>
      <div className={styles.sectionHead}><span>ФАКТИЧЕСКАЯ ЛЕНТА</span><h2>Последние реальные события разработки</h2><p>Показываются только задачи разработки и их фактическое выполнение. Работа по клиентам остаётся в своём окне и в общем Мозге.</p></div>
      <div className={styles.activityGrid}>{activity.slice(0,60).map(a=><article key={`${a.source}-${a.id}`}><div><strong>{taskTitleText(a.title)}</strong><small>{humanizeBusinessText(a.source,"Система")} · {humanizeBusinessText(a.kind,"задача")} · {runtimeText(a.status)}{a.account_id?` · клиент ${a.account_id}`:""}</small>{a.source==="execution"?<p>Шаги: {String(a.payload?.steps_done??0)} из {String(a.payload?.steps_total??0)}{a.payload?.block_reason?` · ${humanizeBusinessText(a.payload.block_reason,"Причина блокировки уточняется")}`:""}</p>:null}<p><b>Практический смысл:</b> {businessValue(a.title,a.payload?.business_value||a.payload?.client_value)}</p></div><span>{dt(a.created_at)}</span></article>)}</div>
    </section>
  </main></AppShell>;
}
