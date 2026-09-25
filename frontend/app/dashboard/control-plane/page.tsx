"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/app/components/shell/AppShell";
import { apiGet } from "@/app/lib/api";
import {humanizeBusinessText} from "@/app/lib/humanizeBusinessText";
import styles from "./page.module.css";

type RuleCounts={active:number;working:number;violated:number;blocked:number;waiting_external:number;waiting_owner:number;stale:number;unverified:number};
type Health={status?:string;rules?:Partial<RuleCounts>;critical_incidents?:{P0?:number;P1?:number};waiting_dependencies?:{owner_money_policy?:number};modules_total?:number;auto_fixed_today?:number;checked_at?:string};
type Requirement={id:string;key:string;title:string;normalized_rule?:string;scope_type?:string;scope_id?:string;module?:string;priority?:string;status?:string;version?:number;last_verified_at?:string|null;last_applied_at?:string|null;desired_state?:Record<string,unknown>;verification?:unknown[]};
type Incident={id:string;type:string;priority:string;state:string;module:string;account_id?:string|null;requirement_id?:string|null;execution_id?:string|null;symptom:string;root_cause?:string;remediation?:string;details?:Record<string,unknown>;opened_at?:string;resolved_at?:string};
type OwnerInstruction={id:string;text:string;intent:string;status:string;account_id?:string|null;modules?:string[];requirement_ids?:string[];created_at?:string};
type Execution={id:string;requirement_id:string;correlation_id?:string;account_id?:string|null;module?:string;status:string;attempt?:number;error_code?:string|null;created_at?:string};
type History={version:number;snapshot?:Record<string,unknown>;reason?:string;created_at?:string};
type Evidence={id:number;level:string;type:string;source:string;passed:boolean;payload?:Record<string,unknown>;observed_at?:string};
type BrainWorld={status?:string;summary?:{modules?:number;pass?:number;degraded?:number;critical?:number;blocked?:number;open_p0?:number;open_p1?:number;modules_with_drift?:number;stale_modules?:number};modules?:Array<{module:string;health:string;diagnosis?:{classification?:string;safe_action?:string};incidents?:unknown[]}>};
type BrainPlan={status?:string;queue?:Record<"NOW"|"NEXT"|"WAITING"|"BLOCKED"|"DONE",Array<{module?:string;account_id?:string|null;classification?:string;action?:string;owner_action_required?:boolean;policy_field?:string;evidence_for?:unknown[]}>>};
type BrainAcceptance={BORIS_BRAIN_100_PERCENT?:string;score_percent?:number;hard_gates?:Record<string,number>;waiting_dependencies?:Record<string,number>;counts?:{pass?:number;partial?:number;fail?:number}};
type BrainThread={id:string;external_chat_ref?:string|null;status:string;current_state?:Record<string,any>;last_event_at?:string;updated_at?:string};

const STATUS:Record<string,string>={pass:"Работает",active:"Активно",degraded:"Исправляется",blocked:"Заблокировано",failed:"Ошибка",waiting:"Ожидает",queued:"В очереди",planning:"Планируется",running:"Выполняется",completed:"Выполнено",acceptance:"Приёмка",review:"Проверка",waiting_external:"Ждём внешнюю систему",waiting_owner:"Нужны данные",waiting_human:"Ожидает действия",resolved:"Исправлено"};
const humanStatus=(v?:string)=>STATUS[String(v||"").toLowerCase()]||humanizeBusinessText(v,"Не проверено");
const dateTime=(v?:string|null)=>{if(!v)return "Не проверено";const d=new Date(v);return Number.isNaN(d.getTime())?"Не проверено":d.toLocaleString("ru-RU")};
const moduleName=(v?:string)=>({marketing:"Маркетинг",mop:"МОП",crm:"CRM",messages:"Сообщения",telephony:"Звонки",rop:"РОП",feed_factory:"Объявления",social:"Соцсети",email:"Почта",telegram:"Telegram",analytics:"Аналитика",direct:"Яндекс Директ",billing:"Оплата",sitebuild:"Сайты",system:"Система"} as Record<string,string>)[String(v||"")]||humanizeBusinessText(v,"Система");
const priorityText=(v?:string)=>({P0:"Критический",P1:"Важный",P2:"Автоконтроль",P3:"Удобство",P4:"Улучшение"} as Record<string,string>)[String(v||"").toUpperCase()]||humanizeBusinessText(v,"Приоритет не указан");
const scopeText=(type?:string,id?:string)=>{const t=({account:"Аккаунт",global:"Вся система",module:"Модуль",client:"Клиент"} as Record<string,string>)[String(type||"").toLowerCase()]||humanizeBusinessText(type,"Область");return id?`${t}: ${id}`:t;};
function authHeaders(json=false):Record<string,string>{const token=localStorage.getItem("boris_token")||"";const h:Record<string,string>={Authorization:`Bearer ${token}`};if(json)h["Content-Type"]="application/json";return h;}

export default function ControlPlanePage(){
 const [health,setHealth]=useState<Health|null>(null),[requirements,setRequirements]=useState<Requirement[]>([]),[incidents,setIncidents]=useState<Incident[]>([]),[resolved,setResolved]=useState<Incident[]>([]),[instructions,setInstructions]=useState<OwnerInstruction[]>([]),[world,setWorld]=useState<BrainWorld|null>(null),[plan,setPlan]=useState<BrainPlan|null>(null),[acceptance,setAcceptance]=useState<BrainAcceptance|null>(null),[brainThread,setBrainThread]=useState<BrainThread|null>(null),[clientFleet,setClientFleet]=useState<any>(null);
 const [loading,setLoading]=useState(true),[error,setError]=useState(""),[filter,setFilter]=useState("all"),[query,setQuery]=useState(""),[brainPrompt,setBrainPrompt]=useState(""),[brainBusy,setBrainBusy]=useState(false),[brainNotice,setBrainNotice]=useState("");
 const [selected,setSelected]=useState<Requirement|null>(null),[history,setHistory]=useState<History[]>([]),[executions,setExecutions]=useState<Execution[]>([]),[evidence,setEvidence]=useState<Evidence[]>([]),[detailLoading,setDetailLoading]=useState(false);
 const load=useCallback(async()=>{setLoading(true);setError("");try{const[h,r,i,ri,intake,w,p,a,bt,cf]=await Promise.all([apiGet("/api/control-plane/health"),apiGet("/api/control-plane/requirements?limit=500"),apiGet("/api/control-plane/incidents?state=open&limit=500"),apiGet("/api/control-plane/incidents?state=resolved&limit=30"),apiGet("/api/control-plane/intake?limit=40"),apiGet("/api/control-plane/world-state"),apiGet("/api/control-plane/action-plan"),apiGet("/api/control-plane/acceptance"),apiGet("/api/control-plane/workspace/threads?q=system-brain%3Aowner-global&sync_runtime=false&refresh_executor=true&limit=10"),apiGet("/api/control-plane/workspace/client-supervisor")]);setHealth(h||null);setRequirements(Array.isArray(r)?r:[]);setIncidents(Array.isArray(i)?i:[]);setResolved(Array.isArray(ri)?ri:[]);setInstructions(Array.isArray(intake)?intake:[]);setWorld(w||null);setPlan(p||null);setAcceptance(a||null);setClientFleet(cf||null);const brain=Array.isArray(bt)?bt.find((x:any)=>x.external_chat_ref==="system-brain:owner-global"):null;setBrainThread(brain||null)}catch(e){setError(e instanceof Error?e.message:"Не удалось получить состояние BORIS")}finally{setLoading(false)}},[]);
 useEffect(()=>{void load()},[load]);
 useEffect(()=>{const timer=window.setInterval(()=>{void load()},15000);return()=>window.clearInterval(timer)},[load]);
 const openRule=async(r:Requirement)=>{setSelected(r);setDetailLoading(true);setHistory([]);setExecutions([]);setEvidence([]);try{const[h,e]=await Promise.all([apiGet(`/api/control-plane/requirements/${r.id}/history`),apiGet(`/api/control-plane/executions?requirement_id=${r.id}&limit=20`)]);const ex=Array.isArray(e)?e:[];setHistory(Array.isArray(h)?h:[]);setExecutions(ex);if(ex[0]?.id){const ev=await apiGet(`/api/control-plane/evidence/${ex[0].id}`);setEvidence(Array.isArray(ev)?ev:[])}}finally{setDetailLoading(false)}};
 async function sendBrainPrompt(){
  const text=brainPrompt.trim();if(!text||brainBusy)return;setBrainBusy(true);setBrainNotice("");setError("");
  try{
   let thread=brainThread;
   if(!thread){
    const cr=await fetch("/api/control-plane/workspace/threads",{method:"POST",headers:authHeaders(true),body:JSON.stringify({title:"Центральный Мозг BORIS",thread_type:"project",external_chat_ref:"system-brain:owner-global",status:"active",summary:"Глобальная owner-командная ветка единого Control Plane",source:{surface:"control-plane",source:"system-brain",owner_only:true}})});
    const cd=await cr.json();if(!cr.ok)throw new Error(cd?.detail||"Не удалось открыть командную ветку Мозга");thread=cd.thread||null;if(!thread?.id)throw new Error("Командная ветка Мозга не создана");setBrainThread(thread);
   }
   const commandId=typeof crypto!=="undefined"&&"randomUUID" in crypto?crypto.randomUUID():String(Date.now());
   const pr=await fetch(`/api/control-plane/workspace/threads/${thread.id}/prompt`,{method:"POST",headers:authHeaders(true),body:JSON.stringify({text,command_id:commandId})});
   const pd=await pr.json();if(!pr.ok)throw new Error(pd?.detail||"Команда Мозгу не поставлена");
   setBrainPrompt("");setBrainNotice(`Поставлено в работу · ${humanizeBusinessText(pd?.execution?.status||pd?.execution?.executor_status||"queued","в очереди")}`);
   const td=await apiGet(`/api/control-plane/workspace/threads/${thread.id}`);if(td?.thread)setBrainThread(td.thread);
   const intake=await apiGet("/api/control-plane/intake?limit=40");setInstructions(Array.isArray(intake)?intake:[]);
  }catch(e){setBrainNotice(e instanceof Error?e.message:"Ошибка постановки команды Мозгу");}finally{setBrainBusy(false)}
 }
 const modules=useMemo(()=>Array.from(new Set(requirements.map(r=>r.module).filter(Boolean) as string[])).sort(),[requirements]);
 const visible=useMemo(()=>{const q=query.trim().toLowerCase();return requirements.filter(r=>(filter==="all"||r.module===filter)&&(!q||`${r.title} ${r.key} ${r.module} ${r.scope_id}`.toLowerCase().includes(q)))},[requirements,filter,query]);
 const rules=health?.rules||{}; const blockedInc=incidents.filter(x=>x.type.includes("MONEY_GUARD")||x.state==="blocked");
 const moneyWaiting=(plan?.queue?.WAITING||[]).filter(x=>x.classification==="OWNER_MONEY_POLICY_REQUIRED");
 const autoFixed=resolved.filter(x=>x.resolved_at&&new Date(x.resolved_at).toDateString()===new Date().toDateString()).slice(0,8);
 const clientRows=Array.isArray(clientFleet?.accounts)?clientFleet.accounts:[];
 const clientNeedOwner=clientRows.filter((x:any)=>x?.overall==="waiting_owner").length;
 const clientSelfHeal=clientRows.filter((x:any)=>x?.overall==="needs_self_heal"||x?.overall==="read_error").length;
 const clientWorking=clientRows.filter((x:any)=>x?.overall==="working").length;
 const clientHealthy=clientRows.filter((x:any)=>x?.overall==="healthy").length;
 const clientTop=clientRows.find((x:any)=>String(x?.overall||"")==="read_error")||clientRows.find((x:any)=>String(x?.overall||"")==="needs_self_heal")||clientRows.find((x:any)=>String(x?.overall||"")==="waiting_owner")||clientRows.find((x:any)=>String(x?.overall||"")==="working")||null;
 return <AppShell active="control-plane" title="Правила BORIS" subtitle="Что BORIS обязан делать, что реально работает и где нужна причина">
  <main className={styles.page}>
   <section className={styles.hero}><div><p className={styles.eyebrow}>ЕДИНЫЙ МОЗГ BORIS</p><h1>Правила BORIS</h1><p>Зелёный статус появляется только после фактической проверки. Старая успешная проверка, работающий процесс или ответ сервера сами по себе не считаются результатом.</p><p><b>Онлайн:</b> состояние обновляется автоматически каждые 15 секунд · последняя проверка: {dateTime(health?.checked_at)}</p></div><button className={styles.refresh} onClick={()=>void load()} disabled={loading}>{loading?"Проверяем…":"Проверить сейчас"}</button></section>
   {error?<div className={styles.error}>{error}</div>:null}
   <section className={styles.ownerHub}>
    <div className={styles.ownerHubHead}><div><span>ПРОСТОЕ УПРАВЛЕНИЕ</span><h2>Куда смотреть владельцу</h2><p>Для ежедневной работы не нужно читать реестр правил. Сначала клиенты, потом системные проблемы, затем разработка.</p></div><a href="/dashboard/clients-control">Открыть управление клиентами</a></div>
    <div className={styles.ownerHubGrid}>
      <a href="/dashboard/clients-control"><small>Клиенты · нужно решение</small><strong>{clientNeedOwner}</strong><span>Открыть конкретную причину и действие</span></a>
      <a href="/dashboard/clients-control"><small>BORIS исправляет сам</small><strong>{clientSelfHeal}</strong><span>Внутренние клиентские сбои</span></a>
      <a href="/dashboard/clients-control"><small>Работают под контролем</small><strong>{clientWorking}</strong><span>Нужен контроль результата, не ручная работа</span></a>
      <a href="#system-problems"><small>Системные проблемы</small><strong>{incidents.length}</strong><span>Только реальные открытые инциденты</span></a>
      <a href="#boris-now"><small>BORIS делает сейчас</small><strong>{plan?.queue?.NOW?.length??0}</strong><span>Глобальные безопасные действия</span></a>
      <a href="/dashboard/development"><small>Разработка</small><strong>→</strong><span>Код, технические задачи и приёмка</span></a>
    </div>
    {clientTop?<a className={styles.ownerHubFocus} href={`/dashboard/clients-control?account_id=${encodeURIComponent(String(clientTop.account_id||""))}`}><b>Первым открыть: {clientTop.name||clientTop.account_id}</b><span>{clientTop.overall==="waiting_owner"?"Нужно решение владельца":clientTop.overall==="needs_self_heal"?"BORIS исправляет внутреннюю проблему":clientTop.overall==="read_error"?"Нужно восстановить чтение состояния":"Работает, но требует контроля результата"}</span></a>:<div className={styles.ownerHubOk}>Критичных клиентских задач сейчас нет.</div>}
   </section>
   <section className={styles.metrics}>
    <article><span>Активные правила</span><strong>{rules.active??"—"}</strong><small>{health?.modules_total??"—"} модулей под контролем</small></article>
    <article><span>Работают сейчас</span><strong>{rules.working??"—"}</strong><small>есть свежая фактическая проверка</small></article>
    <article><span>Нарушено</span><strong>{rules.violated??"—"}</strong><small>есть открытая проблема</small></article>
    <article><span>Заблокировано</span><strong>{rules.blocked??blockedInc.length}</strong><small>автодействие остановлено безопасно</small></article>
    <article><span>Автоисправлено сегодня</span><strong>{health?.auto_fixed_today??autoFixed.length}</strong><small>закрыто после повторной проверки</small></article>
    <article><span>Ждём внешнюю систему</span><strong>{rules.waiting_external??0}</strong><small>повтор без подтверждения не запускается</small></article>
    <article><span>Давно не проверялось</span><strong>{rules.stale??0}</strong><small>старая проверка не считается актуальной</small></article>
    <article><span>Критические</span><strong>{(health?.critical_incidents?.P0||0)+(health?.critical_incidents?.P1||0)}</strong><small>критические и важные</small></article>
   </section>

   {moneyWaiting.length?<section className={styles.panel}><div className={styles.panelHead}><div><h2>Нужно одно решение по расходам</h2><p>BORIS продолжает всю работу без дополнительных трат. Для автоматического изменения ставок этим аккаунтам нужен только подтверждённый суточный предел — система не будет придумывать его сама.</p></div><span className={styles.badgeWarn}>{moneyWaiting.length} аккаунта</span></div><div className={styles.incidents}>{moneyWaiting.map((x,idx)=><article className={styles.incident} key={`money-${x.account_id||idx}`}><div><b>{x.account_id||"Аккаунт"}</b><span>Автотраты удерживаются</span></div><p>Укажите максимальную сумму расходов за сутки в настройках KPI этого аккаунта. После сохранения BORIS сам перепроверит предохранитель и продолжит автопилот.</p><small>Сейчас лимит не подтверждён. Неденежные действия продолжаются автоматически; нулевой лимит не заменяется выдуманной суммой.</small></article>)}</div></section>:null}

   <section className={styles.brainGrid}>
    <article className={styles.brainCard}><span>Готовность мозга</span><strong>{acceptance?.score_percent??"—"}%</strong><small>{acceptance?.BORIS_BRAIN_100_PERCENT==="PASS"?"Обязательные технические проверки пройдены":"Есть незакрытые обязательные проверки"}</small></article>
    <article className={styles.brainCard}><span>Сейчас делает BORIS</span><strong>{plan?.queue?.NOW?.length??0}</strong><small>безопасных действий выполняются сейчас</small></article>
    <article className={styles.brainCard}><span>Ждёт владельца</span><strong>{plan?.queue?.WAITING?.length??0}</strong><small>только реальные зависимости/политики</small></article>
    <article className={styles.brainCard}><span>Критические модули</span><strong>{world?.summary?.critical??0}</strong><small>{world?.summary?.degraded??0} деградировано · {world?.summary?.stale_modules??0} устарело</small></article>
   </section>

   <section className={styles.panel}>
    <div className={styles.panelHead}><div><h2>Команда Центральному Мозгу</h2><p>Это глобальная команда системе. Она сохраняется в постоянной памяти и уходит в тот же исполнитель BORIS, что и остальные команды владельца — отдельного «второго мозга» нет.</p></div><span className={brainThread?.current_state?.executor_status?styles.badgeOk:styles.badgeWarn}>{humanStatus(String(brainThread?.current_state?.executor_status||brainThread?.status||"не запущен"))}</span></div>
    <div className={styles.surfaceSplit}>
     <a href="/dashboard/control-plane"><b>Мозг</b><span>Правила, состояние, инциденты, глобальные команды</span></a>
     <a href="/dashboard/clients-control"><b>Клиенты</b><span>Оплаченный период, KPI, лиды, сообщения, звонки, CRM, очереди</span></a>
     <a href="/dashboard/development"><b>Разработка</b><span>Код, задачи разработки, фактическое выполнение, проверки и технический прогресс</span></a>
    </div>
    <div className={styles.brainCommand}><textarea value={brainPrompt} onChange={e=>setBrainPrompt(e.target.value)} rows={4} placeholder="Например: проверь все критические и важные, выполни безопасное автовосстановление, перепроверь результат и эскалируй только настоящие внешние блокеры."/>
     <div><span>{brainNotice||`Сейчас: ${humanizeBusinessText(brainThread?.current_state?.current_step,"глобальная команда не запущена")} · дальше: ${humanizeBusinessText(brainThread?.current_state?.next_step,"ожидание команды владельца")}`}</span><button onClick={()=>void sendBrainPrompt()} disabled={brainBusy||!brainPrompt.trim()}>{brainBusy?"Ставлю…":"Дать команду Мозгу"}</button></div>
    </div>
   </section>

   <section className={styles.twoCol} id="boris-now">
    <section className={styles.panel}><div className={styles.panelHead}><div><h2>Что BORIS делает сейчас</h2><p>Единый план «Сейчас / Дальше / Ждёт / Заблокировано» из фактического состояния модулей.</p></div><span className={styles.badgeOk}>{humanStatus(plan?.status)}</span></div><div className={styles.actionQueue}>{(["NOW","NEXT","WAITING","BLOCKED"] as const).map(lane=><div key={lane} className={styles.lane}><h3>{({NOW:"СЕЙЧАС",NEXT:"ДАЛЬШЕ",WAITING:"ЖДЁТ",BLOCKED:"ЗАБЛОКИРОВАНО"} as Record<string,string>)[lane]}</h3>{(plan?.queue?.[lane]||[]).length===0?<small>Пусто</small>:(plan?.queue?.[lane]||[]).slice(0,8).map((x,idx)=>x.account_id?<a className={styles.actionLink} href={`/dashboard/clients-control?account_id=${encodeURIComponent(String(x.account_id))}`} key={`${lane}-${idx}`}><b>{moduleName(x.module)} · {humanizeBusinessText(x.classification,"Без отклонений")}</b><span>{x.account_id}</span><small>{humanizeBusinessText(x.action,"Наблюдение")}{x.owner_action_required?" · открыть решение владельца":" · BORIS продолжает сам"}</small></a>:<article key={`${lane}-${idx}`}><b>{moduleName(x.module)} · {humanizeBusinessText(x.classification,"Без отклонений")}</b><span>Вся система</span><small>{humanizeBusinessText(x.action,"Наблюдение")}{x.owner_action_required?" · требуется решение владельца":" · BORIS может продолжать сам"}</small></article>)}</div>)}</div></section>
    <section className={styles.panel}><div className={styles.panelHead}><div><h2>Состояние модулей</h2><p>Не процесс «жив», а бизнес-состояние, диагностика и следующий шаг.</p></div><span className={world?.summary?.critical?styles.badgeWarn:styles.badgeOk}>{world?.summary?.pass??0}/{world?.summary?.modules??0} без проблем</span></div><div className={styles.moduleGrid}>{(world?.modules||[]).map(m=><article key={m.module}><div><b>{moduleName(m.module)}</b><span>{humanStatus(m.health)}</span></div><small>{humanizeBusinessText(m.diagnosis?.classification,"Диагностика без отклонений")}</small>{m.diagnosis?.safe_action?<p>Следующий шаг: {humanizeBusinessText(m.diagnosis.safe_action,"Автоматический контроль")}</p>:null}</article>)}</div></section>
   </section>

   <section className={styles.twoCol} id="system-problems">
    <section className={styles.panel}><div className={styles.panelHead}><div><h2>Нарушенные правила</h2><p>Только фактические проблемы, а не технический шум.</p></div><span className={incidents.length?styles.badgeWarn:styles.badgeOk}>{incidents.length?`${incidents.length} открыто`:"Нарушений нет"}</span></div><div className={styles.incidents}>{incidents.length===0?<div className={styles.empty}>Сейчас открытых инцидентов нет.</div>:incidents.map(x=><article className={styles.incident} key={x.id}><div><b>{priorityText(x.priority)} · {moduleName(x.module)}</b><span>{x.account_id||"Вся система"}</span></div><p>{humanizeBusinessText(x.symptom,"Проблема зафиксирована")}</p><small>{x.root_cause&&x.root_cause!=="unknown"?`Причина: ${humanizeBusinessText(x.root_cause,"уточняется")}`:"Причина ещё не доказана"}</small></article>)}</div></section>
    <section className={styles.panel}><div className={styles.panelHead}><div><h2>Автоматически исправлено</h2><p>Инцидент закрывается только после новой проверки.</p></div><span className={styles.badgeOk}>{autoFixed.length} сегодня</span></div><div className={styles.incidents}>{autoFixed.length===0?<div className={styles.empty}>Сегодня автоматических исправлений пока нет.</div>:autoFixed.map(x=><article className={styles.incident} key={x.id}><div><b>{moduleName(x.module)}</b><span>{dateTime(x.resolved_at)}</span></div><p>{humanizeBusinessText(x.remediation||x.symptom,"Исправление подтверждено")}</p></article>)}</div></section>
   </section>

   <section className={styles.panel}><div className={styles.panelHead}><div><h2>Последние команды владельца</h2><p>Команды сохраняются в постоянном журнале команд, а не живут только в истории чата.</p></div><span className={styles.badgeOk}>{instructions.length} записано</span></div><div className={styles.instructions}>{instructions.length===0?<div className={styles.empty}>В новом контуре команд пока нет.</div>:instructions.slice(0,12).map(x=><article className={styles.instruction} key={x.id}><div className={styles.instructionText}><b>{x.text}</b><small>{dateTime(x.created_at)}</small></div><div className={styles.meta}><span>Тип: <b>{humanizeBusinessText(x.intent,"Команда")}</b></span><span>Статус: <b>{humanStatus(x.status)}</b></span>{x.account_id?<span>Аккаунт: <b>{x.account_id}</b></span>:null}<span>Модули: <b>{(x.modules||[]).map(moduleName).join(", ")||"Система"}</b></span></div></article>)}</div></section>

   <section className={styles.panel}><div className={styles.panelHead}><div><h2>Единый реестр требований</h2><p>Нажмите правило — увидите версии, последнее выполнение и подтверждающие проверки.</p></div></div><div className={styles.filters}><select value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">Все модули</option>{modules.map(m=><option key={m} value={m}>{moduleName(m)}</option>)}</select><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Найти правило"/></div><div className={styles.rules}>{visible.map(r=><button type="button" className={styles.rule} key={r.id} onClick={()=>void openRule(r)}><div className={styles.ruleTop}><div><h3>{r.title}</h3><p>{humanizeBusinessText(r.normalized_rule,"Правило BORIS")}</p></div><span className={styles.ruleStatus}>{humanStatus(r.status)}</span></div><div className={styles.meta}><span>Модуль: <b>{moduleName(r.module)}</b></span><span>Для кого: <b>{scopeText(r.scope_type,r.scope_id)}</b></span><span>Приоритет: <b>{priorityText(r.priority)}</b></span><span>Версия: <b>v{r.version}</b></span><span>Проверено: <b>{dateTime(r.last_verified_at)}</b></span></div></button>)}{!loading&&visible.length===0?<div className={styles.empty}>По выбранному фильтру правил нет.</div>:null}</div></section>

   {selected?<section className={styles.panel}><div className={styles.panelHead}><div><h2>{selected.title}</h2><p>{humanizeBusinessText(selected.normalized_rule,"Правило BORIS")}</p></div><button className={styles.close} onClick={()=>setSelected(null)}>Закрыть</button></div>{detailLoading?<div className={styles.empty}>Читаю фактическое выполнение…</div>:<div className={styles.detailGrid}><div><h3>Последнее выполнение</h3>{executions[0]?<><p className={styles.bigState}>{humanStatus(executions[0].status)}</p><div className={styles.meta}><span>Попыток: <b>{executions[0].attempt??0}</b></span><span>Когда: <b>{dateTime(executions[0].created_at)}</b></span>{executions[0].error_code?<span>Причина: <b>{humanizeBusinessText(executions[0].error_code,"техническая причина зафиксирована")}</b></span>:null}</div></>:<div className={styles.empty}>Выполнений ещё нет.</div>}</div><div><h3>Подтверждение последнего выполнения</h3>{evidence.length?<div className={styles.evidence}>{evidence.map(x=><div key={x.id} className={x.passed?styles.evOk:styles.evBad}><b>{humanizeBusinessText(x.level,"Проверка")} · {humanizeBusinessText(x.type,"результат")}</b><span>{x.passed?"Подтверждено":"Не подтверждено"}</span><small>{dateTime(x.observed_at)}</small></div>)}</div>:<div className={styles.empty}>Подтверждения пока нет — показывать успешное завершение нельзя.</div>}</div><div><h3>История версий</h3><div className={styles.history}>{history.slice(0,8).map(x=><div key={x.version}><b>v{x.version}</b><span>{humanizeBusinessText(x.reason,"Изменение правила")}</span><small>{dateTime(x.created_at)}</small></div>)}</div></div></div>}</section>:null}
  </main>
 </AppShell>
}
