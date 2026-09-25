"use client";

import {useCallback,useEffect,useLayoutEffect,useMemo,useRef,useState,useSyncExternalStore} from "react";
import styles from "./page.module.css";
import AppShell from "@/app/components/shell/AppShell";
import {useAccounts} from "@/app/lib/AccountContext";
import {humanizeBusinessText} from "@/app/lib/humanizeBusinessText";

type AnyRow=Record<string,any>;

const STATUS:Record<string,string>={
  healthy:"Всё работает",
  working:"BORIS работает",
  waiting_owner:"Нужно твоё действие",
  needs_self_heal:"BORIS исправляет сбой",
  degraded:"Есть проблема",
  inactive_expected:"Период завершён",
  unknown_period:"Период не подтверждён",
  read_error:"Ошибка чтения",
  not_monitored:"Нет активного автоматического контроля",
};
const MOD:Record<string,string>={
  analytics:"Аналитика",crm:"CRM",feed_factory:"Объявления / фиды",marketing:"ИИ-маркетолог",
  messages:"Сообщения",mop:"МОП",reactivation:"Реактивация",rop:"РОП",telephony:"Телефония",ecs:"ЕЦО",
  billing:"Оплаченный период"
};
const MODULE_ROUTE:Record<string,string>={
  analytics:"/dashboard/analytics",crm:"/dashboard/crm",feed_factory:"/dashboard/campaign",marketing:"/dashboard/marketing",
  messages:"/messages",mop:"/messages",reactivation:"/reactivation",rop:"/rop",telephony:"/dashboard/phone",ecs:"/dashboard/command-center",
  billing:"/dashboard/billing"
};
const PERIOD:Record<string,string>={
  active:"оплачен и активен",
  expired:"завершён",
  inactive_unprovisioned:"активная оплаченная услуга не подключена",
  billing_gap:"нет подтверждения оплаченного периода",
  unknown:"не определён",
};
const REASON:Record<string,string>={
  PAID_PERIOD_EVIDENCE_MISSING:"Работа по аккаунту идёт, но в BORIS нет подтверждённого оплаченного периода",
  BUDGET_GUARD_BLOCKED:"Рекламный бюджет не задан или равен 0 ₽ — BORIS не может сам разрешить расход денег",
  MOP_AUTOSEND_POLICY_UNSET:"Не выбрана политика автоотправки МОПа — BORIS не отправляет сообщения клиентам без разрешения",
  KPI_WAITING_OWNER_DEPENDENCY:"Денежная оптимизация ждёт решения владельца. Без подтверждения BORIS не тратит бюджет.",
  CRM_OVERDUE_HUMAN_TASKS:"Есть просроченные задачи менеджера. BORIS держит их на контроле; это не техническая поломка.",
};
const ACTION_RU:Record<string,string>={
  observe_existing_plan:"Проверить план лидов и CPL, сверить текущую гипотезу и не менять её без подтверждённого результата",
  run_marketing_diagnosis:"Пересчитать план/факт, CPL и причину отклонения; выбрать безопасный следующий шаг",
  continue_account_scoped_kpi_recovery:"Продолжить восстановление KPI только в рамках этого аккаунта и затем перепроверить результат",
  surface_owner_policy_and_continue_nonmoney_recovery:"Продолжить безопасные проверки без расходов и отдельно показать, что именно должен подтвердить или пополнить владелец",
  surface_overdue_tasks:"Найти просроченные CRM-задачи, поднять их в работу и проконтролировать следующий шаг менеджера",
  create_or_repair_next_action:"Создать или восстановить следующий шаг по зависшему лиду и назначить контрольный срок",
  ensure_manager_handoff_task:"Передать обращение менеджеру через CRM-задачу и проконтролировать, что оно не потерялось",
  inspect_social_delivery_sla:"Проверить просроченные публикации соцсетей, состояние планировщика и восстановить его при внутреннем сбое",
  restart_social_scheduler:"Перезапустить планировщик соцсетей при подтверждённом сбое и проверить новый сигнал работы",
  inspect_social_contract_blockers:"Проверить, что мешает публикации в соцсетях, и отделить внешний блокер от внутренней ошибки",
  inspect_feed_failures:"Проверить ошибки фида и определить, какие из них BORIS может восстановить сам",
  recover_stale_feed_jobs:"Восстановить зависшие задачи фида и повторно проверить итоговое состояние",
  inspect_mop_queue:"Проверить очередь МОПа, зависшие ответы и состояние передачи менеджеру",
  MOP_WAITING_AI_PROVIDER:"AI-провайдер временно недоступен; BORIS сам перепроверит доступность и автоматически продолжит МОП без команды владельца",
  ensure_callback_task:"Создать контроль обратного звонка, если звонок клиента остался без следующего действия",
  inspect_reactivation_errors:"Проверить очередь реактивации и безопасно восстановить внутренние сбои без повторной отправки вслепую",
  REACTIVATION_AVITO_PARTIAL_CHAT_ACCESS:"Продолжать работу по доступным чатам; недоступные Avito-диалоги перепроверять автоматически каждые 15 минут, не дублируя отправки",
  owner_policy_required:"Ждать только подтверждение денежного лимита; все действия без расходов продолжать автоматически",
};
const actionText=(v:any)=>ACTION_RU[String(v||"")]||humanizeBusinessText(v,"Проверить состояние и выполнить безопасный следующий шаг");
const statusText=(v:any,fallback="Состояние уточняется")=>STATUS[String(v||"")]||humanizeBusinessText(v,fallback);
const periodText=(v:any,fallback="Период уточняется")=>PERIOD[String(v||"")]||humanizeBusinessText(v,fallback);
const moduleText=(v:any,fallback="Система")=>MOD[String(v||"")]||humanizeBusinessText(v,fallback);
const reasonText=(v:any,fallback="BORIS проверяет причину")=>REASON[String(v||"")]||humanizeBusinessText(v,fallback);

const moduleProblemText=(m:any,ai:any,finance?:any)=>{
  const c=String(m?.classification||"");const a=m?.actual_state||{};
  if(c==="KPI_WAITING_OWNER_DEPENDENCY"){
    const wallet=finance?.avito_wallet||{};const balance=Number(wallet.real_rub);const resume=Number(wallet.resume_balance_rub);
    const budget=Number(a.daily_budget_limit_rub||0);
    const goal=Number(a.target_leads_per_day||0);const fact=Number(a.actual_leads||0);
    if(wallet.low_balance_block&&Number.isFinite(balance))return `План по лидам сейчас ${fact}/${goal||"—"}. В кошельке Avito ${money(balance)}${resume>0?`, рабочий порог ${money(resume)}`:""}. Денежные действия остановлены безопасно из-за низкого баланса. Это не техническая поломка.`;
    return `План по лидам сейчас ${fact}/${goal||"—"}. Денежная оптимизация ждёт подтверждения дневного лимита${budget>0?` ${money(budget)}`:""}. Это не техническая поломка.`;
  }
  if(c==="CRM_OVERDUE_HUMAN_TASKS")return `В CRM ${num(a.overdue_tasks)} просроченных задач менеджера. Система работает, но людям нужно закрыть следующие действия.`;
  if(m?.health==="pass")return "Отклонений не найдено. Модуль работает в штатном режиме.";
  return reasonText(c,"BORIS зафиксировал отклонение и уточняет причину.");
};
const moduleBorisText=(m:any,ai:any,finance?:any)=>{
  const c=String(m?.classification||"");const a=m?.actual_state||{};
  if(c==="KPI_WAITING_OWNER_DEPENDENCY")return String(ai?.summary?.current_work||a.runtime_mode||"BORIS продолжает бесплатную диагностику и не выполняет денежные действия без разрешения владельца.");
  if(c==="CRM_OVERDUE_HUMAN_TASKS")return "BORIS поднимает просроченные задачи в контроль, напоминает менеджеру и продолжает проверять следующий шаг.";
  if(m?.safe_action)return actionText(m.safe_action);
  if(m?.health==="pass")return "Продолжает штатный мониторинг и перепроверку по расписанию.";
  return "Проверяет состояние и выполняет безопасное восстановление в пределах разрешённых действий.";
};
const moduleOwnerText=(m:any,ai:any,finance?:any)=>{
  const c=String(m?.classification||"");const a=m?.actual_state||{};
  if(!m?.owner_action_required)return "Ничего. BORIS ведёт этот модуль сам.";
  if(c==="KPI_WAITING_OWNER_DEPENDENCY"){
    const wallet=finance?.avito_wallet||{};const balance=Number(wallet.real_rub);const resume=Number(wallet.resume_balance_rub);
    if(wallet.low_balance_block&&wallet.owner_action_required)return `Пополнить кошелёк Avito${resume>0?` минимум до ${money(resume)}`:""}. Сейчас ${Number.isFinite(balance)?money(balance):"баланс ниже рабочего порога"}. После пополнения BORIS сам перепроверит баланс и продолжит; вручную перезапускать ничего не нужно.`;
    const budget=Number(a.daily_budget_limit_rub||0);
    return `Если расход разрешён и на Avito есть деньги — подтвердить дневной лимит${budget>0?` ${money(budget)}`:""} в ИИ-маркетологе. Если денег нет или тратить нельзя — ничего не подтверждать: BORIS продолжит работу без расходов.`;
  }
  return String(ai?.summary?.owner_action||reasonText(c,"Нужно внешнее решение владельца, которое BORIS не имеет права принимать сам."));
};

const moscowTime=(v:any,withDate=false)=>{
  if(!v)return "—";
  try{return new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",day:withDate?"2-digit":undefined,month:withDate?"2-digit":undefined,hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(new Date(v))}catch{return "—"}
};
const fmt=(v:any,fallback="—")=>v===null||v===undefined||v===""?fallback:String(v);
const num=(v:any)=>Number.isFinite(Number(v))?Number(v):0;
const money=(v:any,fallback="—")=>{
  if(v===null||v===undefined||v==="")return fallback;
  const n=Number(v);if(!Number.isFinite(n))return fallback;
  return `${new Intl.NumberFormat("ru-RU",{minimumFractionDigits:Number.isInteger(n)?0:2,maximumFractionDigits:2}).format(n)} ₽`;
};
const clientValue=(module?:string,action?:string)=>{
  const m=String(module||"").toLowerCase();
  const a=String(action||"").toLowerCase();
  if(m==="messages"||m==="mop"||/сообщ|ответ|диалог/.test(a))return "Быстрее отвечает на обращения и снижает риск потерять горячий лид.";
  if(m==="reactivation"||/реактив/.test(a))return "Возвращает старые обращения в работу и даёт дополнительные продажи без нового трафика.";
  if(m==="telephony"||/звон|телефон/.test(a))return "Не даёт терять звонки и фиксирует следующий шаг по каждому обращению.";
  if(m==="crm"||m==="rop"||/crm|сделк|ворон|задач менедж/.test(a))return "Не даёт лидам зависать без следующего действия и повышает управляемость продаж.";
  if(m==="feed_factory"||/объяв|публикац|фид/.test(a))return "Поддерживает нужное количество активных объявлений, чтобы реклама продолжала приводить обращения.";
  if(m==="marketing"||m==="analytics"||/kpi|cpl|маркет|конверс|ставк|продвиж/.test(a))return "Помогает удерживать план по лидам и CPL, не тратя бюджет вслепую.";
  if(m==="billing"||/оплат|тариф|период/.test(a))return "Не даёт оплаченному клиентскому контуру остановиться из-за ошибки статуса услуги.";
  if(m==="ecs")return "Сводит обращения и действия в единый поток, чтобы ничего не потерялось между каналами.";
  return "Поддерживает услугу клиента в рабочем состоянии и уменьшает ручной контроль владельца.";
};

const DIALOG_TYPE_RU:Record<string,string>={
  business_inquiry:"Похоже на запрос клиента",job_seeker:"Соискатель · не лид",spam_advertising:"Реклама / спам",
  system:"Системное сообщение",purchase:"Покупка / не лид",other_unknown:"Неясно",
};
const dialogTypeText=(v:any)=>DIALOG_TYPE_RU[String(v||"")]||"Неясно";
const dialogTypeClass=(v:any)=>String(v||"other_unknown").replace(/[^a-z_]/g,"");
const inferDialogType=(d:any,thread?:any)=>{
  if(d?.lead_purpose==="job_seeker"||d?.kpi_counted===false)return "job_seeker";
  if(d?.is_purchase)return "purchase";
  const messages=Array.isArray(thread?.messages)?thread.messages:(Array.isArray(thread?.dialog?.messages)?thread.dialog.messages:[]);
  const inbound=messages.filter((m:any)=>!["out","outgoing"].includes(String(m?.direction||"").toLowerCase())&&!["manager","business","mop","boris"].includes(String(m?.sender||m?.author_kind||"").toLowerCase())).slice(-5);
  const text=(inbound.length?inbound.map((m:any)=>String(m?.text??m?.body??m?.message??"")).join(" "):(!d?.answered?String(d?.last_text||""):"")).toLowerCase().replace(/ё/g,"е").replace(/\s+/g," ");
  if(!text.trim())return "other_unknown";
  const system=["это автоматическое сообщение","сообщение от avito","сообщение от авито","объявление снято","объявление заблокировано","служба поддержки avito","служба поддержки авито"];
  if(system.some(x=>text.includes(x)))return "system";
  const spam=["предлагаем свои услуги","предлагаю свои услуги","хотим предложить вам","предлагаем вам сотрудничество","предлагаю вам сотрудничество","продвижение вашего бизнеса","продвижение ваших объявлений","настроим вам рекламу","настрою вам рекламу","увеличим ваши продажи","база клиентов для вашего бизнеса","готовы предложить рекламу"];
  if(spam.some(x=>text.includes(x)))return "spam_advertising";
  return "business_inquiry";
};
const summarizeDialogs=(list:any[],threads:Record<string,any>={})=>{
  const out:Record<string,number>={business_inquiry:0,job_seeker:0,spam_advertising:0,system:0,purchase:0,other_unknown:0,total:0};
  for(const d of Array.isArray(list)?list:[]){const chatId=String(d?.avito_chat_id||"");const k=inferDialogType(d,threads[chatId]);out[k]=(out[k]||0)+1;out.total++;}
  return out;
};

const ACTIVITY_SOURCE_RU:Record<string,string>={workspace:"Память клиента",execution:"Исполнение BORIS",background_job:"Фоновая задача",supervisor:"Контроль клиента",client_supervisor:"Контроль клиента",control_plane:"Система контроля"};
const ACTIVITY_KIND_RU:Record<string,string>={task:"Задача",event:"Событие",prompt:"Команда владельца",execution:"Выполнение",job:"Фоновая работа",workspace:"Рабочая ветка"};
const ACTIVITY_STATUS_RU:Record<string,string>={running:"В работе",queued:"В очереди",waiting:"Ожидает",blocked:"Заблокировано",completed:"Выполнено",done:"Выполнено",passed:"Проверка пройдена",pass:"Проверка пройдена",failed:"Ошибка",error:"Ошибка",planning:"Планируется",acceptance:"Приёмка",review:"Проверка"};
const activitySourceText=(v:any)=>ACTIVITY_SOURCE_RU[String(v||"").toLowerCase()]||humanizeBusinessText(v,"BORIS");
const activityKindText=(v:any)=>ACTIVITY_KIND_RU[String(v||"").toLowerCase()]||humanizeBusinessText(v,"Задача");
const activityStatusText=(v:any)=>ACTIVITY_STATUS_RU[String(v||"").toLowerCase()]||humanizeBusinessText(v,"Состояние уточняется");
const activityTitleText=(a:any,clientName:string)=>{
  let t=String(a?.title||"Задача BORIS");
  const plan=t.match(/Живой план BORIS:\s*NOW\s*(\d+)\s*·\s*NEXT\s*(\d+)\s*·\s*WAITING\s*(\d+)\s*·\s*BLOCKED\s*(\d+)/i);
  if(plan)return `План BORIS: сейчас ${plan[1]} · дальше ${plan[2]} · ждёт решения ${plan[3]} · заблокировано ${plan[4]}`;
  if(/^ИИ-маркетолог:/i.test(t))return `ИИ-маркетолог · проверка клиента ${clientName}`;
  return t.replace(/\bNOW\b/gi,"СЕЙЧАС").replace(/\bNEXT\b/gi,"ДАЛЬШЕ").replace(/\bWAITING\b/gi,"ЖДЁТ").replace(/\bBLOCKED\b/gi,"ЗАБЛОКИРОВАНО").replace(/\bworkspace\b/gi,"рабочая ветка").replace(/\bexecution\b/gi,"выполнение").replace(/\bqueued\b/gi,"в очереди").replace(/\brunning\b/gi,"в работе");
};

const marketingActionStatus=(a:any)=>{
  const st=String(a?.status||"").toLowerCase(),eff=String(a?.effect_status||"").toLowerCase();
  if(eff==="rolled_back"||st.includes("rolled_back"))return "Изменение отменено после измерения";
  if(a?.changed_avito===true&&st==="published")return "Опубликовано в Avito";
  if(st==="safe_feed_ready"||eff==="waiting_publish")return "Подготовлено · ждёт публикации";
  if(st.includes("mapping")||eff.includes("mapping"))return "Не применено · нет надёжной связи с объявлением";
  if(st.includes("superseded")||eff==="superseded")return "Заменено более новым действием";
  if(a?.changed_avito===true)return "Изменение применено";
  return humanizeBusinessText(a?.status,"Действие подготовлено");
};
const marketingActionChange=(a:any)=>{
  const c=a?.changes||{};
  if(c?.title?.old!==undefined||c?.title?.new!==undefined)return {label:"Изменил заголовок",old:String(c?.title?.old||"—"),next:String(c?.title?.new||"—")};
  if(c?.description?.old!==undefined||c?.description?.new!==undefined)return {label:"Изменил описание",old:String(c?.description?.old||"—"),next:String(c?.description?.new||"—")};
  const key=Object.keys(c)[0];
  if(key){const v=c[key]||{};return {label:`Изменил ${humanizeBusinessText(key,key)}`,old:String(v?.old??"—"),next:String(v?.new??"—")};}
  return {label:"Действие по объявлению",old:"—",next:humanizeBusinessText(a?.reason,"изменение подготовлено")};
};
const marketingActionResult=(a:any)=>{
  const before=a?.kpi_before||{},after=a?.kpi_after||{},obs=a?.observation_window||{};
  if(after&&Object.keys(after).length)return `До: ${Number(before.views||0)} просмотров / ${Number(before.contacts||0)} обращений. После: ${Number(after.views||0)} просмотров / ${Number(after.contacts||0)} обращений.`;
  if(a?.changed_avito===true&&obs?.status==="active")return `Изменение уже в Avito. Эффект измеряется${obs?.end_date?` до ${new Date(`${obs.end_date}T00:00:00`).toLocaleDateString("ru-RU")}`:""}; раньше срока результат не придумывается.`;
  if(String(a?.status||"")==="safe_feed_ready")return "Новая версия подготовлена безопасно, но в Avito ещё не опубликована — эффекта пока быть не может.";
  return `До действия: ${Number(before.views||0)} просмотров / ${Number(before.contacts||0)} обращений. Результат появится только после фактического применения и измерения.`;
};
const isRoutineCheckActivity=(a:any)=>{
  const t=String(a?.title||"").toLowerCase();
  const k=String(a?.kind||"").toLowerCase();
  return /живой план boris|план boris|ии-маркетолог.*провер|проверка клиента|heartbeat|monitor|supervisor/.test(t)||/check|monitor|supervisor/.test(k);
};

type PeriodPreset="today"|"yesterday"|"7d"|"30d"|"month"|"custom";
const moscowTodayISO=()=>new Intl.DateTimeFormat("en-CA",{timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
const shiftISO=(iso:string,days:number)=>{const d=new Date(`${iso}T12:00:00Z`);d.setUTCDate(d.getUTCDate()+days);return d.toISOString().slice(0,10)};
const periodLabel=(preset:PeriodPreset,from:string,to:string)=>preset==="today"?"Сегодня":preset==="yesterday"?"Вчера":preset==="7d"?"Последние 7 дней":preset==="30d"?"Последние 30 дней":preset==="month"?"Этот месяц":`${new Date(`${from}T00:00:00`).toLocaleDateString("ru-RU")} — ${new Date(`${to}T00:00:00`).toLocaleDateString("ru-RU")}`;
const timestampMoscowDay=(value:any)=>{if(!value)return "";const d=new Date(String(value));if(Number.isNaN(d.getTime()))return "";return new Intl.DateTimeFormat("en-CA",{timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit"}).format(d)};

type OwnerPriority="P0"|"P1"|"P2"|"OK";
const PRIORITY_RANK:Record<OwnerPriority,number>={P0:0,P1:1,P2:2,OK:3};
const PRIORITY_TITLE:Record<OwnerPriority,string>={
  P0:"Срочно · клиент может не получать услугу",
  P1:"Важно · услуга работает неправильно или ждёт решения",
  P2:"Контроль · BORIS работает, но нужен контроль результата",
  OK:"Проверено · срочных действий нет",
};
const ownerPriority=(x:any):OwnerPriority=>{
  const overall=String(x?.overall||"");
  const clientState=String(x?.client_state||"");
  if(overall==="inactive_expected"||["expired","inactive_unprovisioned","suspended"].includes(clientState))return "OK";
  const modules=Array.isArray(x?.modules)?x.modules:[];
  const queue=x?.queue||{};
  if(overall==="read_error")return "P0";
  if(clientState==="active"&&modules.some((m:any)=>String(m?.health||"")==="critical"))return "P0";
  if(clientState==="active"&&num(queue?.stale_running)>0)return "P0";
  if(overall==="needs_self_heal"||modules.some((m:any)=>String(m?.health||"")==="blocked"))return "P1";
  if(overall==="waiting_owner"||clientState==="billing_gap")return "P1";
  if(overall==="working"||overall==="not_monitored"||modules.some((m:any)=>String(m?.health||"")==="degraded"))return "P2";
  return "OK";
};
const priorityReason=(x:any)=>{
  const modules=Array.isArray(x?.modules)?x.modules:[];
  const queue=x?.queue||{};
  if(String(x?.overall||"")==="read_error")return "BORIS не смог подтвердить фактическое состояние клиента — считаем это срочным до проверки.";
  const critical=modules.find((m:any)=>String(m?.health||"")==="critical");
  if(critical)return `${moduleText(critical.module)}: ${reasonText(critical.classification,"критическое отклонение")}`;
  if(num(queue?.stale_running)>0)return `Есть зависшие фоновые задачи: ${num(queue.stale_running)}. Нужно восстановить выполнение и перепроверить результат.`;
  const internal=Array.isArray(x?.internal_problems)?x.internal_problems[0]:null;
  if(internal)return `${moduleText(internal.module)}: ${reasonText(internal.reason,"BORIS исправляет внутреннюю проблему")}`;
  const owner=Array.isArray(x?.needs_owner)?x.needs_owner[0]:null;
  if(owner)return `${moduleText(owner.module)}: ${reasonText(owner.reason,"нужно решение владельца")}`;
  const nonPass=modules.find((m:any)=>!["pass",""] .includes(String(m?.health||"")));
  if(nonPass)return `${moduleText(nonPass.module)}: ${reasonText(nonPass.classification,"нужен контроль результата")}`;
  const human=Array.isArray(x?.human_work)?x.human_work[0]:null;
  if(human)return `${moduleText(human.module)}: ${reasonText(human.reason,"есть работа менеджера под контролем BORIS")}`;
  return statusText(x?.overall,"Состояние подтверждено");
};
const needsOwnerAction=(x:any)=>Array.isArray(x?.needs_owner)&&x.needs_owner.length>0;

function authHeaders(json=false):Record<string,string>{
  const token=localStorage.getItem("boris_token")||"";
  const headers:Record<string,string>={Authorization:`Bearer ${token}`};
  if(json)headers["Content-Type"]="application/json";
  return headers;
}

const isInternalFixtureAccount=(x:any)=>{
  const aid=String(x?.account_id||"").trim();
  const name=String(x?.name||"").trim();
  const probe=`${aid} ${name}`.toLowerCase();
  return /^(qa|test|demo|sandbox|inbox_|dev_|fixture_|mock_)/i.test(aid)
    || /(?:^|[_-])(qa|test|demo|sandbox|fixture|mock)(?:[_-]|$)/i.test(aid)
    || /^ostapenkokirill86(?:t\d+|onb)_/i.test(aid)
    || /(^|\s)(тест|тестовый|тестовая|тестовые|qa|demo|sandbox)(\s|$)/i.test(probe);
};
const dedupeAccounts=(items:any[])=>{
  const byId=new Map<string,any>();
  for(const row of Array.isArray(items)?items:[]){
    const id=String(row?.account_id||"").trim();
    if(!id)continue;
    const key=id.toLowerCase();
    const prev=byId.get(key);
    byId.set(key,prev?{...prev,...row}:row);
  }
  return Array.from(byId.values());
};
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

export default function ClientsControlPage(){
  const {setSelectedAccount}=useAccounts();
  const [authState,setAuthState]=useState<"checking"|"allowed"|"denied">("checking");
  // OWNER_AUTH_SERVER_CONFIRM_V1: localStorage email is only a UX hint.
  // Owner-only API requests start only after /api/auth/me confirms platform_admin.
  useSyncExternalStore(subscribeOwnerSession,getOwnerSessionSnapshot,getOwnerServerSnapshot);
  const allowed=authState==="allowed";
  const [loading,setLoading]=useState(true);
  const [fleet,setFleet]=useState<AnyRow|null>(null);
  const [allAccounts,setAllAccounts]=useState<any[]>([]);
  const [selectedId,setSelectedId]=useState("");
  const [detail,setDetail]=useState<AnyRow|null>(null);
  const [query,setQuery]=useState("");
  const [error,setError]=useState("");
  const [prompt,setPrompt]=useState("");
  const [promptBusy,setPromptBusy]=useState(false);
  const [notice,setNotice]=useState("");
  const [actionPlan,setActionPlan]=useState<AnyRow|null>(null);
  const [activity,setActivity]=useState<any[]>([]);
  const [workspace,setWorkspace]=useState<AnyRow|null>(null);
  const [workspaceEvents,setWorkspaceEvents]=useState<any[]>([]);
  const [taskAds,setTaskAds]=useState<any[]>([]);
  const [taskAdHistory,setTaskAdHistory]=useState<any[]>([]);
  const [taskBanners,setTaskBanners]=useState<AnyRow[]>([]);
  const [dialogs,setDialogs]=useState<any[]>([]);
  const [dialogSummary,setDialogSummary]=useState<Record<string,number>>({business_inquiry:0,job_seeker:0,spam_advertising:0,system:0,purchase:0,other_unknown:0,total:0});
  const [dialogThreads,setDialogThreads]=useState<Record<string,any>>({});
  const [liveLoading,setLiveLoading]=useState(false);
  const [aiWorkspace,setAiWorkspace]=useState<AnyRow|null>(null);
  const [aiRootCause,setAiRootCause]=useState<AnyRow|null>(null);
  const [aiReadiness,setAiReadiness]=useState<AnyRow|null>(null);
  const [aiActions,setAiActions]=useState<any[]>([]);
  const [financeByAccount,setFinanceByAccount]=useState<Record<string,AnyRow>>({});
  const [selectedModuleKey,setSelectedModuleKey]=useState("");
  const [periodPreset,setPeriodPreset]=useState<PeriodPreset>("today");
  const [periodFrom,setPeriodFrom]=useState(()=>moscowTodayISO());
  const [periodTo,setPeriodTo]=useState(()=>moscowTodayISO());
  const [periodStats,setPeriodStats]=useState<AnyRow|null>(null);
  const [periodLoading,setPeriodLoading]=useState(false);
  const [periodComms,setPeriodComms]=useState<AnyRow|null>(null);
  const [periodCommsLoading,setPeriodCommsLoading]=useState(false);
  const [callInventory,setCallInventory]=useState<AnyRow|null>(null);
  const [callInventoryLoading,setCallInventoryLoading]=useState(false);
  const [periodRefreshTick,setPeriodRefreshTick]=useState(0);
  const financeLoadedAt=useRef(0);
  const detailSeq=useRef(0);
  const detailAbort=useRef<AbortController|null>(null);

  useLayoutEffect(()=>{
    let alive=true;
    const check=async()=>{
      const token=localStorage.getItem("boris_token")||"";
      if(!token){if(alive)setAuthState("denied");return;}
      try{
        const r=await fetch("/api/auth/me?surface=clients-control",{headers:{Authorization:`Bearer ${token}`},cache:"no-store"});
        const d=await r.json();
        const ok=Boolean(r.ok&&d?.user?.platform_admin===true);
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

  const loadFinanceFleet=useCallback(async(force=false)=>{
    if(!allowed)return;
    if(!force&&Date.now()-financeLoadedAt.current<300000)return;
    try{
      const r=await fetch("/api/avito/director_overview",{headers:authHeaders(),cache:"no-store"});
      const d=await r.json().catch(()=>null);
      if(!r.ok||!d)return;
      const map:Record<string,AnyRow>={};
      for(const row of (Array.isArray(d?.accounts)?d.accounts:[]))if(row?.account_id)map[String(row.account_id)]=row;
      setFinanceByAccount(map);financeLoadedAt.current=Date.now();
    }catch{}
  },[allowed]);

  const loadFleet=useCallback(async()=>{
    if(!allowed)return;
    setLoading(true);setError("");
    void loadFinanceFleet(false);
    try{
      const accountsPromise=fetch("/api/accounts/list",{headers:authHeaders(),cache:"no-store"});
      const fleetPromise=fetch("/api/control-plane/workspace/client-supervisor",{headers:authHeaders(),cache:"no-store"});
      const ar=await accountsPromise;const ad=await ar.json();
      if(!ar.ok)throw new Error(ad?.detail||`Список клиентов: ${ar.status}`);
      const ownerAccounts=dedupeAccounts((Array.isArray(ad?.accounts)?ad.accounts:[]).filter((x:any)=>!isInternalFixtureAccount(x)));
      setAllAccounts(ownerAccounts);
      const requested=typeof window!=="undefined"?new URLSearchParams(window.location.search).get("account_id"):null;
      const requestedAllowed=requested&&ownerAccounts.some((x:any)=>String(x?.account_id)===String(requested))?String(requested):"";
      const saved=typeof window!=="undefined"?String(localStorage.getItem("boris_currentAccount")||"").trim():"";
      const savedAllowed=saved&&ownerAccounts.some((x:any)=>String(x?.account_id)===saved)?saved:"";
      setSelectedId(prev=>{
        if(requestedAllowed)return requestedAllowed;
        if(prev&&ownerAccounts.some((x:any)=>String(x?.account_id)===String(prev)))return prev;
        if(savedAllowed)return savedAllowed;
        return ownerAccounts[0]?.account_id?String(ownerAccounts[0].account_id):"";
      });
      const fr=await fleetPromise;const fd=await fr.json();
      if(!fr.ok)throw new Error(fd?.detail||`Supervisor клиентов: ${fr.status}`);
      setFleet(fd);
      const active=Array.isArray(fd?.accounts)?fd.accounts.find((x:any)=>x.client_state==="active"):null;
      setSelectedId(prev=>prev||(active?.account_id?String(active.account_id):""));
    }catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить клиентов");}
    finally{setLoading(false);}
  },[allowed,loadFinanceFleet]);

  useEffect(()=>{void loadFleet()},[loadFleet]);

  const choosePeriod=useCallback((preset:PeriodPreset)=>{
    const today=moscowTodayISO();
    let from=today,to=today;
    if(preset==="yesterday"){from=shiftISO(today,-1);to=from}
    else if(preset==="7d")from=shiftISO(today,-6);
    else if(preset==="30d")from=shiftISO(today,-29);
    else if(preset==="month")from=`${today.slice(0,7)}-01`;
    setPeriodPreset(preset);setPeriodFrom(from);setPeriodTo(to);
  },[]);

  useEffect(()=>{
    if(!allowed||!selectedId||!periodFrom||!periodTo)return;
    let alive=true;setPeriodLoading(true);
    const qs=new URLSearchParams({days:"30",account_id:selectedId});
    fetch(`/api/inbox/owner_daily/history?${qs.toString()}`,{headers:authHeaders(),cache:"no-store"})
      .then(async r=>{
        const d=await r.json().catch(()=>null);if(!alive)return;
        if(!r.ok||!d){setPeriodStats(null);return;}
        const items=(Array.isArray(d?.items)?d.items:[]).filter((x:any)=>String(x?.report_date||"")>=periodFrom&&String(x?.report_date||"")<=periodTo);
        const expectedDays=Math.max(1,Math.floor((Date.parse(`${periodTo}T12:00:00Z`)-Date.parse(`${periodFrom}T12:00:00Z`))/86400000)+1);
        const sum=(vals:any[])=>Math.round(vals.reduce((acc:number,v:any)=>acc+Number(v||0),0)*100)/100;
        const today=moscowTodayISO();
        const includesToday=periodFrom<=today&&periodTo>=today;
        const todaySnapshot=items.find((x:any)=>String(x?.report_date||"")===today);
        const historyItems=includesToday?items.filter((x:any)=>String(x?.report_date||"")!==today):items;
        const leadRows=historyItems.filter((x:any)=>x?.payload?.kpi?.actual_leads!=null);
        const spendRows=historyItems.filter((x:any)=>x?.payload?.kpi?.actual_spend_rub!=null);
        const avitoRows=historyItems.filter((x:any)=>x?.payload?.avito?.status==="ok"&&x?.payload?.avito?.views!=null&&x?.payload?.avito?.contacts!=null);
        const liveLeads=includesToday?(aiWorkspace?.summary?.actual_leads??detail?.plan_fact?.actual_leads??detail?.plan_fact?.contacts_today):null;
        const liveSpend=includesToday?(aiWorkspace?.summary?.spent_today_rub??null):null;
        const liveViews=includesToday?(aiRootCause?.account?.views_today??null):null;
        const liveAvitoComplete=includesToday&&liveLeads!=null&&liveViews!=null;
        const leadValues=leadRows.map((x:any)=>x.payload.kpi.actual_leads);if(liveLeads!=null)leadValues.push(liveLeads);
        const spendValues=spendRows.map((x:any)=>x.payload.kpi.actual_spend_rub);if(liveSpend!=null)spendValues.push(liveSpend);
        const viewValues=avitoRows.map((x:any)=>x.payload.avito.views);if(liveViews!=null)viewValues.push(liveViews);
        const leads=leadValues.length?sum(leadValues):null;
        const spend=spendValues.length?sum(spendValues):null;
        const views=viewValues.length?sum(viewValues):null;
        const confirmedStatsDays=avitoRows.length+(liveAvitoComplete?1:0);
        const presentDays=new Set(items.map((x:any)=>String(x?.report_date||"")).filter(Boolean));if(includesToday&&(liveLeads!=null||liveSpend!=null||liveViews!=null))presentDays.add(today);
        const newestRow=items.slice().sort((x:any,y:any)=>String(y.report_date).localeCompare(String(x.report_date)))[0]||null;
        const newest=newestRow?.payload||{};
        const liveBalance=includesToday?financeByAccount[String(selectedId)]?.balance?.real:null;
        const q=items.length?items.reduce((acc:any,x:any)=>{const z=x?.payload?.qualification||{};const t=z?.temperatures||{};const st=z?.stages||{};acc.hot+=Number(t.hot||0);acc.warm+=Number(t.warm||0);acc.cold+=Number(t.cold||0);acc.unclassified+=Number(z.unclassified||0);acc.inquiry+=Number(st.INQUIRY||0);acc.engaged+=Number(st.ENGAGED||0);acc.qualified+=Number(st.QUALIFIED||0);acc.target+=Number(st.TARGET_ACTION||0);acc.coverage+=Number(z.coverage||0);acc.dayDialogs+=Number(z.day_dialogs||0);return acc;},{hot:0,warm:0,cold:0,unclassified:0,inquiry:0,engaged:0,qualified:0,target:0,coverage:0,dayDialogs:0}):null;
        const historicalDeals=historyItems.length?sum(historyItems.map((x:any)=>x?.payload?.messages?.crm_chats_with_deal??x?.payload?.messages?.deals_created??0)):0;
        const liveDeals=includesToday?(detail?.operational?.crm?.deals_today??todaySnapshot?.payload?.messages?.crm_chats_with_deal??todaySnapshot?.payload?.messages?.deals_created??null):null;
        const hasAuxData=items.length>0;
        const deals=(historyItems.length||liveDeals!=null)?historicalDeals+Number(liveDeals||0):null;
        setPeriodStats({status:presentDays.size?"ok":"no_data",date_from:periodFrom,date_to:periodTo,expected_days:expectedDays,snapshot_days:presentDays.size,confirmed_stats_days:confirmedStatsDays,partial_provider_stats:presentDays.size<expectedDays||confirmedStatsDays<expectedDays,live_today:includesToday&&liveAvitoComplete,views,contacts:leads,ad_spend_rub:spend,cpl_rub:spend!=null&&leads!=null&&leads>0?Math.round(spend/leads*100)/100:null,incoming_dialogs:hasAuxData?sum(items.map((x:any)=>x?.payload?.messages?.dialogs||0)):null,incoming_messages:hasAuxData?sum(items.map((x:any)=>x?.payload?.messages?.incoming_messages||0)):null,calls_total:hasAuxData?sum(items.map((x:any)=>x?.payload?.calls_crm?.calls_total||0)):null,deals,won_deals:hasAuxData?sum(items.map((x:any)=>x?.payload?.messages?.won_deals||0)):null,qualification:q,aux_snapshot_at:newestRow?.created_at||null,latest_balance_rub:liveBalance??newest?.avito?.balance_rub,latest_balance_date:liveBalance!=null?today:newest?.report_date});
      })
      .catch(()=>{if(alive)setPeriodStats(null)})
      .finally(()=>{if(alive)setPeriodLoading(false)});
    return()=>{alive=false};
  },[allowed,selectedId,periodFrom,periodTo,aiWorkspace,aiRootCause,detail,financeByAccount]);

  useEffect(()=>{
    if(!allowed||!selectedId||!periodFrom||!periodTo)return;
    let alive=true;setPeriodCommsLoading(true);setPeriodComms(null);
    const qs=new URLSearchParams({account_id:selectedId,format:"json",date_from:periodFrom,date_to:periodTo,limit:"500"});
    fetch(`/api/inbox/export/dialogs?${qs.toString()}`,{headers:authHeaders(),cache:"no-store"})
      .then(async r=>{
        const d=await r.json().catch(()=>null);if(!alive)return;
        if(r.ok&&d?.status==="ok"){
          const list=Array.isArray(d?.dialogs)?d.dialogs:[];
          const incomingDialogs=list.filter((x:any)=>Number(x?.stats?.in||0)>0).length;
          const incomingMessages=list.reduce((acc:number,x:any)=>acc+Number(x?.stats?.in||0),0);
          setPeriodComms({status:"ok",incoming_dialogs:incomingDialogs,incoming_messages:incomingMessages,truncated:list.length>=500,returned_dialogs:list.length});
          return;
        }
        const message=String(d?.message||d?.detail||"");
        if(r.ok&&d?.status==="error"&&/не попал ни один диалог/i.test(message)){
          setPeriodComms({status:"ok",incoming_dialogs:0,incoming_messages:0,truncated:false,returned_dialogs:0});
          return;
        }
        setPeriodComms({status:"unavailable",message:message||`HTTP ${r.status}`});
      })
      .catch(()=>{if(alive)setPeriodComms({status:"unavailable",message:"Не удалось прочитать сообщения за период"})})
      .finally(()=>{if(alive)setPeriodCommsLoading(false)});
    return()=>{alive=false};
  },[allowed,selectedId,periodFrom,periodTo,periodRefreshTick]);

  useEffect(()=>{
    if(!allowed||!selectedId)return;
    let alive=true;setCallInventoryLoading(true);setCallInventory(null);
    fetch(`/api/calltracking/stats?account_id=${encodeURIComponent(selectedId)}&days=30`,{headers:authHeaders(),cache:"no-store"})
      .then(async r=>{
        const d=await r.json().catch(()=>null);if(!alive)return;
        if(r.ok&&d?.status==="ok"){
          const calls=Array.isArray(d?.calls)?d.calls:[];
          setCallInventory({status:"ok",calls,truncated:calls.length>=100,answered:d?.answered,missed:d?.missed});
          return;
        }
        setCallInventory({status:"unavailable",message:String(d?.message||d?.detail||"Калтрекинг недоступен")});
      })
      .catch(()=>{if(alive)setCallInventory({status:"unavailable",message:"Не удалось прочитать звонки"})})
      .finally(()=>{if(alive)setCallInventoryLoading(false)});
    return()=>{alive=false};
  },[allowed,selectedId,periodRefreshTick]);


  const loadDetail=useCallback(async(aid:string)=>{
    if(!aid||!allowed)return;
    const seq=++detailSeq.current;
    detailAbort.current?.abort();
    const controller=new AbortController();
    detailAbort.current=controller;
    const alive=()=>seq===detailSeq.current&&!controller.signal.aborted;
    setError("");setLiveLoading(true);
    // На смене клиента сразу очищаем только детальные данные старого аккаунта.
    // Базовая карточка выбранного клиента уже есть из /api/accounts/list.
    setDetail(null);setActionPlan(null);setActivity([]);setWorkspace(null);setWorkspaceEvents([]);
    setTaskAds([]);setTaskAdHistory([]);setTaskBanners([]);
    setDialogs([]);setDialogSummary({business_inquiry:0,job_seeker:0,spam_advertising:0,system:0,purchase:0,other_unknown:0,total:0});setDialogThreads({});setAiWorkspace(null);setAiRootCause(null);setAiReadiness(null);setAiActions([]);
    const request=async(url:string)=>{
      try{
        const r=await fetch(url,{headers:authHeaders(),cache:"no-store",signal:controller.signal});
        const d=await r.json().catch(()=>null);
        return {ok:r.ok,data:d,status:r.status};
      }catch(e){
        if((e as any)?.name==="AbortError")return {ok:false,data:null,status:0,aborted:true};
        return {ok:false,data:null,status:0};
      }
    };
    const qs=new URLSearchParams({account_id:aid,thread_type:"client",limit:"5",sync_runtime:"false",refresh_executor:"false"});

    const supervisorP=(async()=>{
      const x=await request(`/api/control-plane/workspace/client-supervisor?account_id=${encodeURIComponent(aid)}`);
      if(!alive())return;
      if(x.ok&&x.data){setDetail(x.data);return;}
      if(!(x as any)?.aborted)setError(`Не удалось проверить клиента ${aid}: supervisor ${x.status||"сеть"}. Нажми «Проверить клиента сейчас» для повтора.`);
    })();

    const workspaceP=(async()=>{
      const x=await request(`/api/control-plane/workspace/threads?${qs.toString()}`);
      if(!alive())return;
      const list=Array.isArray(x.data)?x.data:[];
      const thread=list.find((v:any)=>String(v.account_id)===String(aid)&&v.thread_type==="client")||null;
      if(!thread){setWorkspace(null);setWorkspaceEvents([]);return;}
      setWorkspace(thread);
      const d=await request(`/api/control-plane/workspace/threads/${thread.id}`);
      if(!alive())return;
      setWorkspace(d.ok?(d.data?.thread||thread):thread);
      setWorkspaceEvents(d.ok&&Array.isArray(d.data?.events)?d.data.events:[]);
    })();

    const planP=(async()=>{
      const x=await request(`/api/control-plane/action-plan?account_id=${encodeURIComponent(aid)}&client_scope=true`);
      if(alive()&&x.ok)setActionPlan(x.data);
    })();

    const activityP=(async()=>{
      const x=await request(`/api/control-plane/workspace/activity?account_id=${encodeURIComponent(aid)}&limit=30&sync_runtime=false`);
      if(alive()&&x.ok)setActivity(Array.isArray(x.data?.items)?x.data.items:[]);
    })();

    const marketingP=(async()=>{
      const x=await request(`/api/avito/ai_marketing_daily_workspace?account_id=${encodeURIComponent(aid)}`);
      if(alive()&&x.ok)setAiWorkspace(x.data);
    })();

    const rootCauseP=(async()=>{
      const x=await request(`/api/avito/kpi_root_cause?account_id=${encodeURIComponent(aid)}`);
      if(alive()&&x.ok)setAiRootCause(x.data);
    })();

    const readinessP=(async()=>{
      const x=await request(`/api/avito/ai_marketing_readiness?account_id=${encodeURIComponent(aid)}`);
      if(alive()&&x.ok)setAiReadiness(x.data);
    })();

    const actionsP=(async()=>{
      const x=await request(`/api/avito/kpi_action_memory?account_id=${encodeURIComponent(aid)}&limit=100`);
      if(alive()&&x.ok)setAiActions(Array.isArray(x.data?.items)?x.data.items:[]);
    })();

    const artifactsP=(async()=>{
      const [adsCurrent,adsHistory,banners]=await Promise.all([
        request(`/api/storage/load?account_id=${encodeURIComponent(aid)}&key=gen_ads`),
        request(`/api/storage/load?account_id=${encodeURIComponent(aid)}&key=gen_ads_history`),
        request(`/api/banners/list?account_id=${encodeURIComponent(aid)}`),
      ]);
      if(!alive())return;
      setTaskAds(Array.isArray(adsCurrent.data?.value)?adsCurrent.data.value:[]);
      setTaskAdHistory(Array.isArray(adsHistory.data?.value)?adsHistory.data.value:[]);
      const groups=banners.data?.banners&&typeof banners.data.banners==="object"?Object.values(banners.data.banners):[];
      const items=Array.isArray(banners.data?.banner_items)
        ?banners.data.banner_items
        :groups.flatMap((x:any)=>Array.isArray(x)?x:[]).filter(Boolean).map((url:any)=>{
          const value=String(url||"");
          const stamps=value.match(/(?:^|\D)(\d{10})(?=\D|$)/g)||[];
          const raw=stamps.length?stamps[stamps.length-1].replace(/\D/g,""):"";
          const seconds=Number(raw);
          const modified_at=Number.isFinite(seconds)&&seconds>1_500_000_000?new Date(seconds*1000).toISOString():null;
          return {url:value,modified_at};
        });
      setTaskBanners(items.slice(0,24));
    })();

    const dialogsP=(async()=>{
      const x=await request(`/api/inbox/dialogs?account_id=${encodeURIComponent(aid)}&q=`);
      if(!alive()||!x.ok)return;
      const list=Array.isArray(x.data)?x.data:(x.data?.dialogs||x.data?.items||[]);
      const all=Array.isArray(list)?list:[];
      const visible=all.slice(0,8);
      setDialogs(visible);
      const entries=await Promise.all(visible.slice(0,8).map(async(dialog:any)=>{
        const chatId=String(dialog?.avito_chat_id||"");if(!chatId)return null;
        const d=await request(`/api/inbox/thread?account_id=${encodeURIComponent(aid)}&avito_chat_id=${encodeURIComponent(chatId)}`);
        if(!d.ok)return [chatId,null] as const;
        const v=d.data;return [chatId,v?.dialog?{...v,...v.dialog,dialog:v.dialog}:v] as const;
      }));
      if(!alive())return;
      const map:Record<string,any>={};for(const entry of entries)if(entry)map[entry[0]]=entry[1];setDialogThreads(map);setDialogSummary(summarizeDialogs(visible,map));
    })();

    await Promise.allSettled([supervisorP,workspaceP,planP,activityP,marketingP,rootCauseP,readinessP,actionsP,artifactsP,dialogsP]);
    if(alive())setLiveLoading(false);
  },[allowed]);

  const openClient=useCallback((aid:string)=>{
    const next=String(aid||"").trim();
    if(!next)return;
    setSelectedAccount(next);
    setSelectedModuleKey("");
    if(next===selectedId){void loadDetail(next);return;}
    setSelectedId(next);
  },[selectedId,loadDetail,setSelectedAccount]);

  const openClientRoute=useCallback((route:string)=>{
    if(!selectedId||!route)return;
    setSelectedAccount(selectedId);
    window.location.href=route;
  },[selectedId,setSelectedAccount]);

  const refreshPage=useCallback(async()=>{
    setNotice("");
    const current=selectedId;
    setPeriodRefreshTick(v=>v+1);
    await Promise.allSettled([loadFleet(),loadFinanceFleet(true),current?loadDetail(current):Promise.resolve()]);
  },[loadFleet,loadFinanceFleet,loadDetail,selectedId]);

  const refreshFastDetail=useCallback(async(aid:string)=>{
    if(!aid||!allowed)return;
    const seq=detailSeq.current;
    const headers=authHeaders();
    try{
      const qs=new URLSearchParams({account_id:aid,thread_type:"client",limit:"5",sync_runtime:"false",refresh_executor:"false"});
      const [wr,actr,aamr,adsr,historyr,bannersr]=await Promise.all([
        fetch(`/api/control-plane/workspace/threads?${qs.toString()}`,{headers,cache:"no-store"}),
        fetch(`/api/control-plane/workspace/activity?account_id=${encodeURIComponent(aid)}&limit=30&sync_runtime=false`,{headers,cache:"no-store"}),
        fetch(`/api/avito/kpi_action_memory?account_id=${encodeURIComponent(aid)}&limit=100`,{headers,cache:"no-store"}),
        fetch(`/api/storage/load?account_id=${encodeURIComponent(aid)}&key=gen_ads`,{headers,cache:"no-store"}),
        fetch(`/api/storage/load?account_id=${encodeURIComponent(aid)}&key=gen_ads_history`,{headers,cache:"no-store"}),
        fetch(`/api/banners/list?account_id=${encodeURIComponent(aid)}`,{headers,cache:"no-store"})
      ]);
      if(seq!==detailSeq.current)return;
      if(wr.ok){
        const wd=await wr.json();const thread=Array.isArray(wd)?wd.find((x:any)=>String(x.account_id)===String(aid)&&x.thread_type==="client"):null;
        if(thread)setWorkspace((prev:any)=>prev&&prev.id===thread.id?{...prev,...thread}:thread);
      }
      if(actr.ok){const ac=await actr.json();setActivity(Array.isArray(ac?.items)?ac.items:[]);}
      if(aamr.ok){const aa=await aamr.json();setAiActions(Array.isArray(aa?.items)?aa.items:[]);}
      if(adsr.ok){const d=await adsr.json();setTaskAds(Array.isArray(d?.value)?d.value:[]);}
      if(historyr.ok){const d=await historyr.json();setTaskAdHistory(Array.isArray(d?.value)?d.value:[]);}
      if(bannersr.ok){
        const d=await bannersr.json();
        const groups=d?.banners&&typeof d.banners==="object"?Object.values(d.banners):[];
        const items=groups.flatMap((x:any)=>Array.isArray(x)?x:[]).filter(Boolean).map((url:any)=>{
          const value=String(url||"");
          const stamps=value.match(/(?:^|\D)(\d{10})(?=\D|$)/g)||[];
          const raw=stamps.length?stamps[stamps.length-1].replace(/\D/g,""):"";
          const seconds=Number(raw);
          return {url:value,modified_at:Number.isFinite(seconds)&&seconds>1_500_000_000?new Date(seconds*1000).toISOString():null};
        });
        setTaskBanners(items.slice(0,24));
      }
    }catch{}
  },[allowed]);

  useEffect(()=>{if(selectedId&&allowed)setSelectedAccount(selectedId)},[selectedId,allowed,setSelectedAccount]);
  useEffect(()=>{if(selectedId)void loadDetail(selectedId)},[selectedId,loadDetail]);
  useEffect(()=>{if(!allowed||!selectedId)return;const timer=window.setInterval(()=>{void refreshFastDetail(selectedId)},15000);return()=>window.clearInterval(timer)},[allowed,selectedId,refreshFastDetail]);
  useEffect(()=>{if(!allowed||!selectedId)return;const timer=window.setInterval(()=>{void loadDetail(selectedId)},60000);return()=>window.clearInterval(timer)},[allowed,selectedId,loadDetail]);
  useEffect(()=>{if(!allowed)return;const timer=window.setInterval(()=>{void loadFleet()},60000);return()=>window.clearInterval(timer)},[allowed,loadFleet]);

  const fleetRows=useMemo(()=>{
    const supervised=new Map<string,any>((Array.isArray(fleet?.accounts)?fleet!.accounts:[]).map((x:any)=>[String(x.account_id),x]));
    const base=allAccounts.length?allAccounts:(Array.isArray(fleet?.accounts)?fleet!.accounts:[]);
    return dedupeAccounts(base.filter((x:any)=>!isInternalFixtureAccount(x))).map((x:any)=>({...x,...(supervised.get(String(x.account_id))||{}),account_id:String(x.account_id),overall:supervised.get(String(x.account_id))?.overall||"not_monitored",client_state:supervised.get(String(x.account_id))?.client_state||"unknown"}))
      .sort((a:any,b:any)=>PRIORITY_RANK[ownerPriority(a)]-PRIORITY_RANK[ownerPriority(b)]||String(a.name||a.account_id).localeCompare(String(b.name||b.account_id),"ru"));
  },[fleet,allAccounts]);
  const rows=useMemo(()=>{
    const q=query.trim().toLowerCase();
    return q?fleetRows.filter((x:any)=>[x.name,x.company_niche,x.overall,x.client_state].some(v=>String(v||"").toLowerCase().includes(q))):fleetRows;
  },[fleetRows,query]);

  const counts=fleet?.counts||{};
  const activeCount=fleetRows.filter((x:any)=>x.client_state==="active").length;
  const problemCount=num(counts.needs_self_heal)+num(counts.read_error)+num(counts.degraded);
  const ownerCount=num(counts.waiting_owner);
  const priorityRows=fleetRows.filter((x:any)=>ownerPriority(x)!=="OK");
  const priorityCounts={
    P0:fleetRows.filter((x:any)=>ownerPriority(x)==="P0").length,
    P1:fleetRows.filter((x:any)=>ownerPriority(x)==="P1").length,
    P2:fleetRows.filter((x:any)=>ownerPriority(x)==="P2").length,
    OK:fleetRows.filter((x:any)=>ownerPriority(x)==="OK").length,
  };
  const topPriority=priorityRows[0]||null;
  const selected=detail||fleetRows.find((x:any)=>String(x.account_id)===String(selectedId))||null;

  async function giveTask(){
    const text=prompt.trim(); if(!selectedId||!text||promptBusy)return;
    setPromptBusy(true);setNotice("");
    try{
      const qr=await fetch(`/api/control-plane/workspace/threads?account_id=${encodeURIComponent(selectedId)}&thread_type=client&sync_runtime=false&refresh_executor=false&limit=20`,{headers:authHeaders(),cache:"no-store"});
      const qd=await qr.json(); if(!qr.ok)throw new Error(qd?.detail||"Не удалось открыть память клиента");
      let thread=Array.isArray(qd)?qd.find((v:any)=>String(v?.account_id)===String(selectedId)&&v?.thread_type==="client"):null;
      if(!thread){
        const cr=await fetch("/api/control-plane/workspace/threads",{method:"POST",headers:authHeaders(true),body:JSON.stringify({
          title:`Клиент · ${selectedId}`,thread_type:"client",account_id:selectedId,status:"active",
          summary:"Админский центр управления клиентом",source:{surface:"clients-control",owner_only:true}
        })});
        const cd=await cr.json();if(!cr.ok)throw new Error(cd?.detail||"Не удалось создать память клиента");thread=cd.thread;
      }
      const commandId=typeof crypto!=="undefined"&&"randomUUID" in crypto?crypto.randomUUID():String(Date.now());
      const pr=await fetch(`/api/control-plane/workspace/threads/${thread.id}/prompt`,{method:"POST",headers:authHeaders(true),body:JSON.stringify({text,command_id:commandId})});
      const pd=await pr.json();if(!pr.ok)throw new Error(pd?.detail||"Команда не поставлена");
      setPrompt("");setNotice(`Поставлено в работу · ${humanizeBusinessText(pd?.execution?.status||pd?.thread?.current_state?.executor_status||"queued","в очереди")}`);
      await loadDetail(selectedId);
    }catch(e){setNotice(e instanceof Error?e.message:"Ошибка постановки задачи");}
    finally{setPromptBusy(false);}
  }

  if(authState==="checking")return <AppShell active="clients-control" title="Управление клиентами"><main className={styles.page}><section className={styles.hero}><div><span className={styles.eyebrow}>BORIS · ПРИВАТНЫЙ ДОСТУП</span><h1>Проверяю админский доступ…</h1><p>Данные клиентов не загружаются, пока сервер не подтвердит приватного владельца платформы.</p></div></section></main></AppShell>;
  if(authState==="denied")return <AppShell active="clients-control" title="Управление клиентами"><main className={styles.page}><section className={styles.hero}><div><span className={styles.eyebrow}>BORIS · ДОСТУП</span><h1>Админский доступ не подтверждён</h1><p>Токен авторизации есть, но сервер не подтвердил платформенного администратора. Перезайди в основной админский аккаунт.</p><div className={styles.topLinks}><a href="/login">Войти</a><a href="/dashboard">Главная</a></div></div></section></main></AppShell>;

  const pf=selected?.plan_fact||{};
  const op=selected?.operational||{};
  const modules=Array.isArray(selected?.modules)?selected.modules:[];
  const focusedModule=modules.find((m:any)=>String(m.module)===selectedModuleKey)||modules.find((m:any)=>m.owner_action_required)||modules.find((m:any)=>["critical","blocked","degraded"].includes(String(m.health||"")))||modules[0]||null;
  const ownerAttentionModules=modules.filter((m:any)=>m.owner_action_required);
  const problems=[...(selected?.internal_problems||[]),...(selected?.needs_owner||[])];
  const human=selected?.human_work||[];
  const messageText=(m:any)=>String(m?.text??m?.body??m?.message??"").trim();
  const isOutgoing=(m:any)=>["out","outgoing"].includes(String(m?.direction||"").toLowerCase())||["manager","business","mop","boris"].includes(String(m?.sender||m?.author_kind||"").toLowerCase());
  const executionSchedule=actionPlan?.execution_schedule||{};
  const planItems=Array.isArray(actionPlan?.items)?actionPlan.items:[];
  const routinePlan=Array.isArray(actionPlan?.routine_plan)?actionPlan.routine_plan:[];
  const automaticPlanItems=planItems.filter((x:any)=>x?.execution?.auto_execute===true||x?.execution?.auto_recheck===true);
  const ownerPlanItems=planItems.filter((x:any)=>x?.owner_action_required===true);
  const planActionText=(x:any)=>{const base=x?.owner_action_required?(String(x?.owner_action_text||"").trim()||actionText(x?.action||x?.classification)):actionText(x?.action||x?.classification);return x?.execution?.auto_recheck&&x?.execution?.planned_for?`${base}. Следующая автоматическая проверка: ${moscowTime(x.execution.planned_for)} МСК`:base;};
  const factFromResult=(x:any)=>{
    const r=x?.execution?.last_module_result;if(!r)return `${MOD[x.module]||x.module}: подтверждённого результата последнего исполнения пока нет`;
    const facts=Object.entries(r?.facts||{}).filter(([,v])=>v!==null&&v!==undefined).slice(0,4).map(([k,v])=>`${humanizeBusinessText(k,k)}: ${String(v)}`).join(", ");
    const dep=String(r?.dependency_state||"");
    const state=dep==="waiting_owner"?"цикл реально выполнен; денежная часть остановлена до подтверждения владельца":dep.includes("external")?"проверено; внешний блокер остаётся, BORIS продолжает автоматическую перепроверку":r.passed?"выполнено и перепроверено":"цикл завершён с ошибкой";
    return `${MOD[x.module]||x.module}: ${state}${facts?` · ${facts}`:""}`;
  };
  const actionEvents=new Map<string,any[]>();
  automaticPlanItems.forEach((x:any)=>{
    const runs=(Array.isArray(x?.execution?.next_runs)&&x.execution.next_runs.length?x.execution.next_runs:[x?.execution?.planned_for]).filter(Boolean).slice(0,6);
    runs.forEach((ts:any)=>{const key=String(ts);const arr=actionEvents.get(key)||[];arr.push(x);actionEvents.set(key,arr)});
  });
  const cycleSchedule=Array.from(actionEvents.entries()).sort((a,b)=>new Date(a[0]).getTime()-new Date(b[0]).getTime()).slice(0,6).map(([ts,xs],index)=>({
    label:moscowTime(ts),
    plan:xs.map((x:any)=>`${MOD[x.module]||humanizeBusinessText(x.module,"BORIS")}: ${planActionText(x)}`).join(" · "),
    fact:index===0?xs.map(factFromResult).join(" · "):"Факт появится только после реального выполнения этого запуска",
    current:index===0,
  }));
  const nextActionAt=[...automaticPlanItems.map((x:any)=>x?.execution?.planned_for),...routinePlan.map((x:any)=>x?.planned_for)].filter(Boolean).sort((a:any,b:any)=>new Date(a).getTime()-new Date(b).getTime())[0];
  const routineCadence=(r:any)=>num(r?.every_seconds)>0&&num(r?.every_seconds)<60?`каждые ${num(r.every_seconds)} сек.`:num(r?.every_minutes)>0?`каждые ${num(r.every_minutes)} мин.`:"по реальному системному расписанию";
  const routineFact=(r:any)=>r?.running_now?`текущий цикл реально запущен${r?.last_cycle_at?` · ${moscowTime(r.last_cycle_at)} МСК`:""}; результат будет показан только после завершения`:r?.last_module_result?`${r.last_module_result.passed?"последний цикл выполнен и записан":"последний цикл завершён с ошибкой"}${r?.last_cycle_at?` · ${moscowTime(r.last_cycle_at)} МСК`:""}`:r?.last_cycle_at?`последний цикл зафиксирован · ${moscowTime(r.last_cycle_at)} МСК`:"подтверждено расписание; факт этого модуля появится только после реального цикла";
  const excludedJobSeekers=Number(aiWorkspace?.summary?.excluded_job_seekers_today??op?.leads?.excluded_job_seekers_today??0);
  const marketingActual=(modules.find((m:any)=>m.module==="marketing")?.actual_state||{}) as AnyRow;
  const leadsToday=pf.contacts_today??op?.kpi?.actual_leads??aiWorkspace?.summary?.actual_leads;
  const spentToday=aiWorkspace?.summary?.spent_today_rub??marketingActual.actual_spend_rub;
  const actualCpl=aiWorkspace?.summary?.actual_cpl??pf.cpl_today??marketingActual.cpl_today;
  const cplLimit=aiWorkspace?.summary?.red_cpl??pf.max_cost_per_lead_rub??marketingActual.max_cost_per_lead_rub;
  const dailyBudget=pf.daily_budget_limit_rub??marketingActual.daily_budget_limit_rub;
  const budgetMissing=String(marketingActual.daily_budget_policy_state||"")==="missing_unconfirmed";
  const budgetNeedsOwnerConfirmation=marketingActual.daily_budget_owner_confirmed===false&&num(dailyBudget)>0;
  const dailyBudgetText=budgetMissing&&num(dailyBudget)<=0?"Не задан":money(dailyBudget);
  const financeRow=financeByAccount[String(selected?.account_id||"")]||{};
  const billing=financeRow?.billing||{};
  const finance={avito_wallet:{
    real_rub:financeRow?.balance?.real,bonus_rub:financeRow?.balance?.bonus,snapshot_date:financeRow?.latest_stats_date,
    low_balance_block:String(aiWorkspace?.summary?.control_state||"")==="external_funding_pause",
    owner_action_required:Boolean(aiWorkspace?.summary?.owner_action),
    resume_balance_rub:aiWorkspace?.summary?.red_cpl,
  }};
  const wallet=finance.avito_wallet;
  const borisTariff=billing;
  const avitoTariff=aiReadiness?.tariff_confirmation||{};
  const tariffUsage=borisTariff?.usage||{};
  const selectedPeriodLabel=periodLabel(periodPreset,periodFrom,periodTo);
  const inSelectedPeriod=(value:any)=>{const d=timestampMoscowDay(value);return Boolean(d&&d>=periodFrom&&d<=periodTo)};
  const periodActions=aiActions.filter((a:any)=>inSelectedPeriod(a?.timestamp)).slice(0,30);
  const periodActivity=activity.filter((a:any)=>inSelectedPeriod(a?.created_at));
  const businessActivity=periodActivity.filter((a:any)=>!isRoutineCheckActivity(a)).slice(0,12);
  const realBusinessActivity=businessActivity.filter((a:any)=>{
    const title=String(a?.title||"").toLowerCase();
    const status=String(a?.status||"").toLowerCase();
    const source=String(a?.source||"").toLowerCase();
    if(/автопилот boris.*ожидает|планирован|критерии 0\//.test(title))return false;
    if(["waiting","queued","planning"].includes(status))return false;
    if(source==="workspace"&&/sync|памят/.test(String(a?.kind||"").toLowerCase()+" "+title))return false;
    return true;
  });
  // OWNER_CLIENT_TASK_RESULT_V1: one factual owner view for command -> executor -> artifacts.
  const taskState=(workspace?.current_state||{}) as AnyRow;
  const taskContract=(taskState.execution_contract||{}) as AnyRow;
  const taskIsContract=String(taskState.execution_mode||"")==="client_operation"&&Boolean(taskContract?.execution_id);
  const lastOwnerEvent=[...workspaceEvents].reverse().find((e:any)=>String(e?.actor||"").toLowerCase()==="owner"&&String(e?.text||"").trim());
  const taskCommandText=String(taskContract.command_text||taskState.last_command_text||lastOwnerEvent?.text||"").trim();
  const taskCommandAt=String(taskState.last_command_at||taskContract.command_at||lastOwnerEvent?.created_at||"").trim();
  const taskCommandId=String(taskContract.command_id||taskState.last_command_id||"").trim();
  const taskExecutorStatus=String(taskIsContract?(taskContract.status||taskState.executor_status):taskState.executor_status||"").toLowerCase();
  const taskOrderStatus=String(taskState.order_status||"").toLowerCase();
  const taskAccepted=taskIsContract
    ?Boolean(taskCommandText&&taskContract.execution_id&&taskContract.job_id&&taskExecutorStatus!=="failed")
    :Boolean(taskCommandText&&taskState.dev_job_id&&taskState.order_id&&taskExecutorStatus!=="enqueue_failed"&&!taskState.execution_enqueue_error);
  const taskDone=Boolean(taskAccepted&&taskExecutorStatus==="completed");
  const taskBlocked=Boolean((taskIsContract&&["blocked","failed"].includes(taskExecutorStatus))||(!taskIsContract&&taskAccepted&&(taskState.executor_blocker||["blocked","failed","blocked_infra","blocked_human"].includes(taskExecutorStatus)||["blocked_infra","blocked_human"].includes(taskOrderStatus))));
  const taskRunning=Boolean(taskAccepted&&!taskDone&&!taskBlocked&&(["queued","queued_retry","running","waiting_external","review","review_requested","testing","regression","acceptance"].includes(taskExecutorStatus)||taskOrderStatus==="running"));
  const taskTakeLabel=!taskCommandText?"Задача ещё не поставлена":taskDone?"Выполнено":taskBlocked?"Есть блокер":taskExecutorStatus==="queued_retry"?"Автоповтор":taskExecutorStatus==="waiting_external"?"Ждёт внешней проверки":taskRunning?"Исполнитель работает":taskAccepted?"Взято в работу":"Постановка не подтверждена";
  const taskTakeTone=taskDone?"done":taskBlocked?"blocked":taskRunning?"running":taskAccepted?"queued":"unknown";
  const taskCriteriaDone=num(taskContract.criteria_done??taskState.criteria_done),taskCriteriaTotal=num(taskContract.criteria_total??taskState.criteria_total);
  const taskRequirements=Array.isArray(taskContract.requirements)?taskContract.requirements:[];
  const taskArtifacts=Array.isArray(taskContract.artifacts)?taskContract.artifacts:[];
  const taskBatches=taskAdHistory
    .filter((b:any)=>Array.isArray(b?.ads)&&b.ads.length&&taskCommandId&&String(b?.command_id||"")===taskCommandId)
    .slice()
    .sort((a:any,b:any)=>Date.parse(String(b?.created_at||0))-Date.parse(String(a?.created_at||0)));
  const taskCommandBatch=taskBatches[0]||null;
  const taskTextRows=Array.isArray(taskCommandBatch?.ads)?taskCommandBatch.ads.slice(0,20):[];
  const taskTextsScoped=Boolean(taskCommandBatch&&taskCommandId);
  const contractBannerRows=taskArtifacts.flatMap((a:any)=>{
    if(String(a?.command_id||"")!==taskCommandId)return [];
    if(a?.kind==="banner_selection"&&Array.isArray(a?.selected))return a.selected.map((x:any)=>({url:x?.url,modified_at:x?.selected_at||a?.created_at,source_created_at:x?.source_created_at,theme_label:x?.theme_label,proof:"selection"}));
    if(a?.kind==="banner_attachments"&&Array.isArray(a?.pairings))return a.pairings.map((x:any)=>({url:x?.banner_url,modified_at:x?.created_at||a?.created_at,source_created_at:x?.banner_created_at,theme_label:x?.listing_title,proof:"application"}));
    return [];
  }).filter((x:any)=>x?.url);
  const visibleTaskBanners=contractBannerRows.slice(0,12);
  const taskBannersScoped=visibleTaskBanners.length>0;
  const historicalTaskBanners=taskBanners.slice(0,12);
  const taskPhase=humanizeBusinessText(taskContract.current_step||taskState.current_step||taskState.current_phase,"Ожидаю первый подтверждённый шаг");
  const latestRealAction=realBusinessActivity[0]||null;
  const taskTextRequirement=taskRequirements.find((r:any)=>String(r?.kind||"")==="text_ads");
  const taskBannerRequirement=taskRequirements.find((r:any)=>["banner_selection","banner_attachments"].includes(String(r?.kind||"")));
  const completedSummaryParts:string[]=[];
  if(num(taskTextRequirement?.completed_count)>0)completedSummaryParts.push(String(num(taskTextRequirement?.completed_count))+" новых текстов");
  if(num(taskBannerRequirement?.completed_count)>0)completedSummaryParts.push(String(num(taskBannerRequirement?.completed_count))+" баннеров");
  const ownerDoneConfirmed=Boolean(taskDone&&taskCriteriaTotal>0&&taskCriteriaDone>=taskCriteriaTotal);
  const ownerDoneText=ownerDoneConfirmed&&completedSummaryParts.length
    ?"Готово: "+completedSummaryParts.join(" · ")
    :latestRealAction
      ?humanizeBusinessText(latestRealAction?.payload?.result||latestRealAction?.payload?.action||latestRealAction?.payload?.summary||latestRealAction?.title,"Подтверждённое действие выполнено")
      :"Ничего ещё не сделано";
  const ownerDoneAt=String(taskContract.updated_at||latestRealAction?.created_at||"");
  const ownerExecutionText=taskIsContract
    ?String(taskContract.execution_id||"")+" · критерии "+String(taskCriteriaDone)+"/"+String(taskCriteriaTotal)
    :taskAccepted
      ?"Наряд "+String(taskState.order_id||"—")+" · исполнитель "+fmt(taskState.assigned_to||taskState.executor,"BORIS")
      :"Активный исполнительный наряд не подтверждён.";
  const ownerNowText=taskDone
    ?"Задача выполнена и принята по факту"
    :taskBlocked
      ?"Исполнение остановлено блокером"
      :taskState.current_step
        ?humanizeBusinessText(taskState.current_step,"BORIS выполняет текущий шаг")
        :taskAccepted
          ?(taskPhase||"BORIS готовит первый рабочий шаг")
          :"Нет активного исполнения";
  const ownerBlockerText=taskBlocked
    ?humanizeBusinessText(taskContract.blocker||taskState.executor_blocker||taskState.blocker||taskState.execution_enqueue_error,"Нужен разбор блокера")
    :"Блокеров по этой задаче нет";

  const periodCalls=callInventory?.status==="ok"
    ?(Array.isArray(callInventory.calls)?callInventory.calls:[]).filter((c:any)=>{const d=timestampMoscowDay(c?.call_time);return Boolean(d&&d>=periodFrom&&d<=periodTo)}).length
    :null;
  const periodCallsPartial=Boolean(callInventory?.status==="ok"&&callInventory?.truncated);
  const exactDialogs=periodComms?.status==="ok"?Number(periodComms.incoming_dialogs||0):null;
  const exactIncomingMessages=periodComms?.status==="ok"?Number(periodComms.incoming_messages||0):null;
  const periodAnyLoading=periodLoading||periodCommsLoading||callInventoryLoading;
  const periodDays=Number(periodStats?.expected_days||1);
  const periodTarget=pf.target_leads_per_day!=null?num(pf.target_leads_per_day)*periodDays:null;

  return <AppShell active="clients-control" title="Управление клиентами" subtitle="Кто получает услугу, что BORIS делает сейчас и где нужен владелец"><main className={styles.page}>
    <section className={styles.hero}>
      <div>
        <span className={styles.eyebrow}>BORIS · ВЛАДЕЛЕЦ · УПРАВЛЕНИЕ КЛИЕНТАМИ</span>
        <h1>Все клиенты, их план/факт, модули и работа BORIS — в одном окне</h1>
        <p><b>Что это даёт:</b> сразу видно, получает ли каждый клиент оплаченную услугу, выполняется ли план по лидам и где BORIS уже сам исправляет проблему.</p>
        <p>Владельцу не нужно вручную открывать кабинеты, очереди и журналы: сюда сводится фактическое состояние работы по клиентам.</p>
        <div className={styles.chips}><span>Только владелец</span><span>Реальные показатели</span><span>Очереди и сбои</span><span>Команды BORIS</span></div>
      </div>
      <aside className={styles.safeCard}>
        <span>🔒</span><strong>Контроль владельца</strong>
        <p>Здесь только управление работой BORIS. Клиентский кабинет от этого экрана не меняется.</p>
        <div className={styles.topLinks}><a href="/dashboard/control-plane">Мозг</a><a href="/dashboard/development">Разработка</a><a href="/dashboard">Главная</a></div>
      </aside>
    </section>

    <section className={styles.priorityBoard}>
      <div className={styles.priorityHeader}>
        <div><span className={styles.eyebrow}>ЧТО ДЕЛАТЬ СЕЙЧАС</span><h2>Очередь владельца по реальному состоянию клиентов</h2><p>P0 и P1 всегда выше улучшений интерфейса. P3/P4 сюда не попадают, пока есть клиентская проблема.</p></div>
        <span className={styles.priorityLive}>{fleet?.checked_at?`проверено ${moscowTime(fleet.checked_at)} МСК`:loading?"проверяю production…":"жду факт"}</span>
      </div>
      <div className={styles.prioritySummary}>
        {(["P0","P1","P2","OK"] as OwnerPriority[]).map(p=>{const first=fleetRows.find((x:any)=>ownerPriority(x)===p);return <article role="button" tabIndex={0} aria-label={`Открыть первого клиента ${p}`} onClick={()=>{if(first)openClient(String(first.account_id))}} onKeyDown={e=>{if((e.key==="Enter"||e.key===" ")&&first)openClient(String(first.account_id))}} key={p} data-priority={p}><small>{p==="P0"?"P0 · СРОЧНО":p==="P1"?"P1 · ВАЖНО":p==="P2"?"P2 · КОНТРОЛЬ":"БЕЗ СРОЧНОГО"}</small><strong>{priorityCounts[p]}</strong><p>{p==="P0"?"клиент может не получать услугу":p==="P1"?"неправильная работа / нужен владелец":p==="P2"?"работает, но результат надо контролировать":"здоровы либо ожидаемо неактивны"}</p></article>})}
      </div>
      {loading?<div className={styles.priorityEmpty}>Собираю фактическое состояние клиентов…</div>:topPriority?<div className={styles.priorityFocus} data-priority={ownerPriority(topPriority)}>
        <div className={styles.priorityFocusTop}><span className={styles.priorityBadge} data-priority={ownerPriority(topPriority)}>{ownerPriority(topPriority)}</span><b>№1 СЕЙЧАС</b><small>{PRIORITY_TITLE[ownerPriority(topPriority)]}</small></div>
        <h3>{topPriority.name||topPriority.account_id}</h3>
        <p>{priorityReason(topPriority)}</p>
        <div className={styles.priorityAction} data-owner={needsOwnerAction(topPriority)?"yes":"no"}><strong>{needsOwnerAction(topPriority)?"НУЖНО ОТ ТЕБЯ":"BORIS ДЕЛАЕТ САМ"}</strong><span>{needsOwnerAction(topPriority)?"Открой клиента: там показано конкретное внешнее решение. Всё безопасное BORIS продолжает сам.":"Тебе ничего нажимать не надо. Открой карточку только чтобы видеть ход исправления и факт проверки."}</span></div>
        <button type="button" onClick={()=>openClient(String(topPriority.account_id))}>Открыть клиента</button>
      </div>:<div className={styles.priorityGreen}><strong>✓ Срочных клиентских проблем нет</strong><span>BORIS не видит P0/P1/P2 по текущему production-факту.</span></div>}
      {priorityRows.length>1?<div className={styles.priorityQueue}>{priorityRows.slice(1,6).map((x:any)=><button type="button" key={`priority-${x.account_id}`} onClick={()=>openClient(String(x.account_id))}><span className={styles.priorityBadge} data-priority={ownerPriority(x)}>{ownerPriority(x)}</span><div><strong>{x.name||x.account_id}</strong><small>{priorityReason(x)}</small></div><b>{needsOwnerAction(x)?"нужен ты":"BORIS сам"}</b></button>)}</div>:null}
    </section>

    <section className={styles.metrics}>
      <article><span>◎</span><div><small>Всего клиентов</small><strong>{fleetRows.length}</strong><p>{fleet?`${Array.isArray(fleet?.accounts)?fleet.accounts.length:0} под автоматическим контролем`:"автоматический контроль загружается"}</p></div></article>
      <article><span>✓</span><div><small>Активны</small><strong>{activeCount}</strong><p>оплаченный/активный период</p></div></article>
      <article><span>⚙</span><div><small>BORIS исправляет</small><strong>{problemCount}</strong><p>внутренние проблемы</p></div></article>
      <article><span>!</span><div><small>Нужно действие</small><strong>{ownerCount}</strong><p>только внешний блокер</p></div></article>
    </section>

    {error?<div className={styles.error}>{error}</div>:null}

    <section className={styles.workspace}>
      <aside className={styles.rail}>
        <div className={styles.railHead}><div><span>КЛИЕНТЫ</span><h2>{rows.length}</h2></div><button onClick={()=>void refreshPage()} disabled={loading||liveLoading}>{loading||liveLoading?"…":"Обновить всё"}</button></div>
        <input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Найти клиента"/>
        <div className={styles.clientList}>
          {rows.map((x:any)=><button key={x.account_id} className={selectedId===x.account_id?styles.activeClient:""} onClick={()=>openClient(String(x.account_id))}>
            <div><strong>{x.name||"Клиент BORIS"}</strong></div>
            <div className={styles.clientStateLine}><span data-status={x.overall}>{statusText(x.overall,"—")}</span><b className={styles.priorityBadge} data-priority={ownerPriority(x)}>{ownerPriority(x)}</b></div>
          </button>)}
          {!loading&&rows.length===0?<div className={styles.empty}>Клиенты не найдены</div>:null}
        </div>
      </aside>

      <div className={styles.detail}>
        {!selected?<div className={styles.empty}>Выберите клиента слева.</div>:<>
          <section className={styles.clientHero}>
            <div><span className={styles.eyebrow}>КЛИЕНТ</span><h2>{selected.name||"Клиент BORIS"}</h2><p>Состояние: <b>{statusText(selected.overall,"Данные загружаются")}</b> · период услуги: <b>{periodText(selected.client_state,"—")}</b>{liveLoading?" · обновляю онлайн-данные…":""}</p></div>
            <div className={styles.clientHeroActions}><div className={styles.statusPill} data-status={selected.overall}>{statusText(selected.overall)}</div><button type="button" onClick={()=>void loadDetail(String(selected.account_id))} disabled={liveLoading}>{liveLoading?"Проверяю…":"Проверить клиента сейчас"}</button></div>
          </section>

          <section className={styles.periodPanel}>
            <div className={styles.periodHead}><div><span>ПЕРИОД ДАННЫХ</span><h2>{selectedPeriodLabel}</h2><p>Сегодня: живой KPI/Avito. Сообщения и звонки считаются по выбранным датам. Прошлые KPI-дни — из сохранённой истории BORIS.</p></div>{periodAnyLoading?<b>Обновляю…</b>:periodStats?.live_today?<b className={styles.periodOk}>Сегодня — живые данные · подтверждено {fmt(periodStats?.confirmed_stats_days)} из {fmt(periodStats?.expected_days)} дн.</b>:periodStats?.partial_provider_stats?<b className={styles.periodWarn}>Подтверждено {fmt(periodStats?.confirmed_stats_days)} из {fmt(periodStats?.expected_days)} дн.</b>:<b className={styles.periodOk}>Данные периода подтверждены</b>}</div>
            <div className={styles.periodButtons}>
              {([['today','Сегодня'],['yesterday','Вчера'],['7d','7 дней'],['30d','30 дней'],['month','Этот месяц']] as [PeriodPreset,string][]).map(([key,label])=><button key={key} type="button" data-active={periodPreset===key?"yes":"no"} onClick={()=>choosePeriod(key)}>{label}</button>)}
              <label>С <input type="date" value={periodFrom} min={shiftISO(moscowTodayISO(),-29)} max={periodTo} onChange={e=>{setPeriodPreset("custom");setPeriodFrom(e.target.value)}}/></label>
              <label>По <input type="date" value={periodTo} min={periodFrom} max={moscowTodayISO()} onChange={e=>{setPeriodPreset("custom");setPeriodTo(e.target.value)}}/></label>
            </div>
            <div className={styles.periodMetrics}>
              <article><small>Обращения Avito</small><strong>{periodStats?.contacts==null?"—":fmt(periodStats.contacts)}</strong><p>{periodStats?.partial_provider_stats?"только подтверждённые Avito дни":"факт за весь период"}</p></article>
              <article><small>Просмотры</small><strong>{periodStats?.views==null?"—":fmt(periodStats.views)}</strong><p>просмотры Avito за период</p></article>
              <article><small>Расход</small><strong>{periodStats?.ad_spend_rub==null?"—":money(periodStats.ad_spend_rub)}</strong><p>продвижение + присутствие</p></article>
              <article><small>CPL</small><strong>{periodStats?.cpl_rub==null?"—":money(periodStats.cpl_rub)}</strong><p>расход ÷ обращения</p></article>
              <article><small>Входящие диалоги</small><strong>{periodCommsLoading?"…":exactDialogs==null?"—":fmt(exactDialogs)}</strong><p>{periodComms?.status==="ok"?(periodComms?.truncated?"минимум за период · достигнут лимит 500 диалогов":"точно по сообщениям за выбранные даты"):humanizeBusinessText(periodComms?.message,"нет подтверждённых данных сообщений")}</p></article>
              <article><small>Входящие сообщения</small><strong>{periodCommsLoading?"…":exactIncomingMessages==null?"—":fmt(exactIncomingMessages)}</strong><p>{periodComms?.status==="ok"?(periodComms?.truncated?"минимум · период ограничен 500 диалогами":"без системных сообщений Avito"):"нет точных данных за период"}</p></article>
              <article><small>Квалифицировано</small><strong>{periodStats?.qualification==null?"—":fmt(Number(periodStats.qualification.qualified||0)+Number(periodStats.qualification.target||0))}</strong><p>{periodStats?.qualification==null?"нет подтверждённого дневного среза":"квалифицированы / дошли до цели"}</p></article>
              <article><small>Сделки CRM</small><strong>{periodStats?.deals==null?"—":fmt(periodStats.deals)}</strong><p>диалоги со сделкой</p></article>
              <article><small>Звонки</small><strong>{callInventoryLoading?"…":periodCalls==null?"—":fmt(periodCalls)}</strong><p>{callInventory?.status==="ok"?(periodCallsPartial?"минимум за период · Avito вернул лимит 100 звонков":"живой calltracking · отфильтровано по выбранным датам"):humanizeBusinessText(callInventory?.message,"калтрекинг не подключён или временно недоступен")}</p></article>
              <article><small>Действия маркетолога</small><strong>{periodActions.length}</strong><p>изменения объявлений за период</p></article>
            </div>
          </section>

          <section className={`${styles.section} ${styles.managePanel}`} id="client-management">
            <div className={styles.sectionHead}><span>УПРАВЛЕНИЕ КЛИЕНТОМ</span><h2>Открыть нужный рабочий раздел</h2><p>Все кнопки ниже переключают BORIS на <b>{selected.name||"Клиент BORIS"}</b> и открывают именно его данные.</p></div>
            <div className={styles.manageButtons}>
              <button onClick={()=>openClientRoute("/dashboard/home")}>Обзор клиента</button>
              <button onClick={()=>openClientRoute("/dashboard/marketing")}>ИИ-маркетолог</button>
              <button onClick={()=>openClientRoute("/dashboard/crm")}>CRM</button>
              <button onClick={()=>openClientRoute("/messages")}>Сообщения / МОП</button>
              <button onClick={()=>openClientRoute("/dashboard/campaign")}>Объявления</button>
              <button onClick={()=>openClientRoute("/reactivation")}>Реактивация</button>
              <button onClick={()=>openClientRoute("/rop")}>РОП</button>
              <button onClick={()=>openClientRoute("/dashboard/analytics")}>Аналитика</button>
            </div>
          </section>

          {ownerAttentionModules.length?<section className={styles.ownerDecision} id="owner-action">
            <div><span>НУЖНО РЕШЕНИЕ ВЛАДЕЛЬЦА</span><h2>{moduleText(ownerAttentionModules[0]?.module)} · не техническая ошибка</h2><p>{moduleProblemText(ownerAttentionModules[0],aiWorkspace,finance)}</p></div>
            <div className={styles.ownerDecisionGrid}><article><small>Что делает BORIS</small><p>{moduleBorisText(ownerAttentionModules[0],aiWorkspace,finance)}</p></article><article><small>Что нужно от тебя</small><p>{moduleOwnerText(ownerAttentionModules[0],aiWorkspace,finance)}</p></article></div>
            <button type="button" onClick={()=>{setSelectedModuleKey(String(ownerAttentionModules[0]?.module||""));document.getElementById("module-detail")?.scrollIntoView({behavior:"smooth",block:"start"})}}>Разобрать причину</button>
          </section>:null}

          <section className={`${styles.grid4} ${styles.moneyGrid}`}>
            <article><small>План лидов · период</small><strong>{periodTarget==null?"—":fmt(periodTarget)}</strong><p>{fmt(pf.target_leads_per_day)} в день × {periodDays} дн.</p></article>
            <article><small>Обращения Avito · период</small><strong>{periodStats?.contacts==null?"—":fmt(periodStats.contacts)}</strong><p>{periodStats?.partial_provider_stats?`подтверждено ${fmt(periodStats?.confirmed_stats_days)} из ${fmt(periodStats?.expected_days)} дн.`:"подтверждено за весь период"}</p></article>
            <article className={styles.moneyFact}><small>Расход Avito · период</small><strong>{periodStats?.ad_spend_rub==null?"—":money(periodStats.ad_spend_rub)}</strong><p>все подтверждённые списания Avito</p></article>
            <article className={styles.moneyFact}><small>CPL · период</small><strong>{periodStats?.cpl_rub==null?"—":money(periodStats.cpl_rub)}</strong><p>{periodStats?.contacts?"расход ÷ обращения Avito":"нет подтверждённых обращений для расчёта"}</p></article>
            <article><small>Лимит расхода · день</small><strong>{dailyBudgetText}</strong><p>{budgetMissing?"лимит не задан":budgetNeedsOwnerConfirmation?"записан, но ещё не подтверждён владельцем":"максимальный дневной расход"}</p></article>
            <article><small>Цена лида · предел</small><strong>{money(cplLimit)}</strong><p>выше этой цены BORIS должен тормозить расход</p></article>
          </section>

          <section className={`${styles.section} ${styles.financePanel}`} id="client-finance">
            <div className={styles.sectionHead}><span>ДЕНЬГИ И ТАРИФЫ</span><h2>Сколько денег есть и что оплачено</h2><p>Только сохранённый фактический баланс и подтверждённые тарифы. Если Avito не отдал свежие данные, это будет указано явно.</p></div>
            <div className={styles.financeGrid}>
              <button type="button" className={wallet.low_balance_block?styles.financeWarn:""} onClick={()=>openClientRoute("/dashboard/marketing")}><small>Кошелёк Avito</small><strong>{money(wallet.real_rub)}</strong><span>{wallet.snapshot_date?`снимок за ${new Date(`${wallet.snapshot_date}T00:00:00`).toLocaleDateString("ru-RU")}`:"свежий баланс не подтверждён"}</span>{wallet.low_balance_block?<b>Мало денег · денежные действия остановлены</b>:null}</button>
              <button type="button" onClick={()=>openClientRoute("/dashboard/campaign")}><small>Тариф Avito</small><strong>{avitoTariff.actual_tariff||"Не подтверждён"}</strong><span>{(avitoTariff.required_tariff||aiReadiness?.required_avito_tariff)?`нужен: ${avitoTariff.required_tariff||aiReadiness?.required_avito_tariff}`:"требование не задано"}</span><b>{(avitoTariff.state||aiReadiness?.tariff_confirmation_state)==="CONFIRMED_ACTIVE"?"Подтверждён и активен":"Нужно проверить статус"}</b></button>
              <button type="button" onClick={()=>openClientRoute("/dashboard/billing")}><small>Тариф BORIS</small><strong>{borisTariff.tier_name||"Нет данных"}</strong><span>{borisTariff.paid_until?`оплачен до ${new Date(borisTariff.paid_until).toLocaleDateString("ru-RU")}`:"срок оплаты не подтверждён"}</span><b>{borisTariff.days_left!=null?`осталось ${borisTariff.days_left} дн.`:borisTariff.active?"активен":"не активен"}</b></button>
              <button type="button" onClick={()=>openClientRoute("/dashboard/billing")}><small>Остаток тарифа BORIS</small><strong>{tariffUsage.listings?.remaining!=null?`${tariffUsage.listings.remaining} объявл.`:"—"}</strong><span>{tariffUsage.banners?.remaining!=null?`баннеров: ${tariffUsage.banners.remaining}`:"лимиты не подтверждены"}</span><b>Открыть все лимиты</b></button>
            </div>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHead}><span>АВТОНОМНЫЙ КОНТРОЛЬ</span><h2>Получает ли клиент всё, что должен получать</h2></div>
            <div className={styles.grid4}>
              <article><small>Ожидаемых модулей</small><strong>{(selected.expected_modules||[]).length}</strong><p>{(selected.expected_modules||[]).map((x:string)=>moduleText(x,"Модуль BORIS")).join(", ")||"—"}</p></article>
              <article><small>Очередь</small><strong>{num(selected.queue?.queued)} / {num(selected.queue?.running)}</strong><p>ждут / выполняются</p></article>
              <article><small>Зависшие</small><strong>{num(selected.queue?.stale_running)}</strong><p>выполняются более 20 минут без обновления</p></article>
              <article><small>Ошибки 24ч</small><strong>{num(selected.queue?.failed_24h)}</strong><p>фоновые задачи</p></article>
            </div>
            <div className={styles.moduleGrid}>{modules.map((m:any)=><article role="button" tabIndex={0} onClick={()=>setSelectedModuleKey(String(m.module))} onKeyDown={e=>{if(e.key==="Enter"||e.key===" ")setSelectedModuleKey(String(m.module))}} className={`${styles.moduleCard} ${focusedModule?.module===m.module?styles.moduleCardActive:""}`} key={m.module}>
              <div><span className={styles.dot} data-health={m.health}/><strong>{moduleText(m.module,"Модуль BORIS")}</strong></div>
              <b>{m.health==="pass"?"Работает":m.owner_action_required?"Нужно решение владельца":"BORIS контролирует"}</b>
              <p>{moduleProblemText(m,aiWorkspace,finance)}</p>
              <small><b>Польза клиенту:</b> {clientValue(m.module,m.classification)}</small>
              <div className={styles.moduleCardActions}><button type="button" className={styles.moduleExplain} onClick={e=>{e.stopPropagation();setSelectedModuleKey(String(m.module))}}>Разобрать</button>{MODULE_ROUTE[String(m.module)]?<button type="button" className={styles.moduleOpen} onClick={e=>{e.stopPropagation();openClientRoute(MODULE_ROUTE[String(m.module)])}}>Открыть раздел</button>:null}</div>
            </article>)}</div>
            {focusedModule?<div className={styles.moduleDetail} id="module-detail">
              <div className={styles.moduleDetailHead}><div><span>РАЗБОР МОДУЛЯ</span><h3>{moduleText(focusedModule.module)}</h3></div><div className={styles.statusPill} data-status={focusedModule.owner_action_required?"waiting_owner":focusedModule.health==="pass"?"healthy":"working"}>{focusedModule.owner_action_required?"Нужно твоё решение":focusedModule.health==="pass"?"Работает":"BORIS контролирует"}</div></div>
              <div className={styles.moduleExplainGrid}>
                <article><small>Что происходит</small><p>{moduleProblemText(focusedModule,aiWorkspace,finance)}</p></article>
                <article><small>Что делает BORIS</small><p>{moduleBorisText(focusedModule,aiWorkspace,finance)}</p></article>
                <article data-owner={focusedModule.owner_action_required?"yes":"no"}><small>Что нужно от тебя</small><p>{moduleOwnerText(focusedModule,aiWorkspace,finance)}</p></article>
              </div>
              <div className={styles.moduleFacts}>
                {focusedModule.module==="marketing"?<><span>Лиды: <b>{fmt(focusedModule.actual_state?.actual_leads)}/{fmt(focusedModule.actual_state?.target_leads_per_day)}</b></span><span>Дневной лимит: <b>{money(focusedModule.actual_state?.daily_budget_limit_rub)}</b></span><span>Подтверждён владельцем: <b>{focusedModule.actual_state?.daily_budget_owner_confirmed===true?"да":"нет"}</b></span><span>Активных объявлений: <b>{fmt(focusedModule.actual_state?.active_items)}</b></span></>:null}
                {focusedModule.module==="crm"?<><span>Сделок: <b>{fmt(focusedModule.actual_state?.deals)}</b></span><span>Просроченных задач: <b>{fmt(focusedModule.actual_state?.overdue_tasks)}</b></span></>:null}
              </div>
              <div className={styles.moduleDetailActions}><button type="button" onClick={()=>void loadDetail(String(selected.account_id))}>Перепроверить сейчас</button>{MODULE_ROUTE[String(focusedModule.module)]?<button type="button" onClick={()=>openClientRoute(MODULE_ROUTE[String(focusedModule.module)])}>Открыть {moduleText(focusedModule.module)}</button>:null}</div>
            </div>:null}
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHead}><span>ОПЕРАЦИОННЫЙ ФАКТ</span><h2>Сообщения, продажи, публикации и реактивация</h2></div>
            <div className={styles.grid4}>
              <article><small>Входящие 24ч</small><strong>{fmt(op?.messages?.inbound_24h)}</strong><p>без ответа: {fmt(op?.messages?.unanswered)}</p></article>
              <article><small>CRM просрочено</small><strong>{fmt(op?.crm?.overdue_tasks)}</strong><p>следующих действий: {fmt(op?.crm?.overdue_next_actions)}</p></article>
              <article><small>Публикации</small><strong>{fmt(op?.publications?.active_items??pf.active_items)}</strong><p>активных объявлений</p></article>
              <article><small>Реактивация</small><strong>{fmt(op?.reactivation?.queued??0)}</strong><p>в очереди</p></article>
            </div>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHead}><span>КОМАНДА</span><h2>Дать задачу BORIS по этому клиенту</h2><p>Команда записывается в постоянную память клиента и сразу становится исполняемой задачей.</p></div>
            <textarea value={prompt} onChange={e=>setPrompt(e.target.value)} rows={4} placeholder="Например: проверь KPI, объявления и переписки; исправь всё, что можно исправить автоматически; владельца тревожь только при внешнем блокере."/>
            <div className={styles.actionRow}><span>{notice||`Клиент: ${selected.name||"Клиент BORIS"}`}</span><button onClick={()=>void giveTask()} disabled={promptBusy||!prompt.trim()}>{promptBusy?"Ставлю…":"Дать задачу"}</button></div>
          </section>

          <section className={styles.section+" "+styles.taskResultPanel}>
            <div className={styles.taskResultHead}>
              <div><span>ЗАДАЧА ВЛАДЕЛЬЦА · ЖИВОЙ РЕЗУЛЬТАТ</span><h2>Понял ли BORIS задачу, взял ли её и что уже сделал</h2><p>Здесь нет обещаний: статус берётся из реального executor-наряда, а тексты и баннеры — из сохранённых материалов этого клиента.</p></div>
              <b data-tone={taskTakeTone}>{taskTakeLabel}</b>
            </div>
            {taskCommandText?<div className={styles.taskCommandBox}><small>ЧТО Я ПОСТАВИЛ</small><p>{taskCommandText}</p><span>{taskCommandAt?"Поставлено "+new Date(taskCommandAt).toLocaleString("ru-RU"):"Время команды уточняется"}</span></div>:<div className={styles.empty}>По этому клиенту ещё нет команды владельца в постоянной памяти.</div>}
            <div className={styles.taskTruthGrid}>
              <article data-ok={taskCommandText?"yes":"no"}><small>BORIS понял команду</small><strong>{taskCommandText?"Да · команда сохранена":"Нет команды"}</strong><p>{taskCommandText?"Исполнитель получает точный текст команды без пересказа и потери контекста.":"Сначала поставь задачу выше."}</p></article>
              <article data-ok={taskAccepted?"yes":"no"}><small>Исполнительный наряд</small><strong>{taskAccepted?(taskIsContract?String(taskContract.execution_id)+" · job "+String(taskContract.job_id):String(taskState.dev_job_id)+" · №"+String(taskState.order_id)):"Не подтверждён"}</strong><p>{taskAccepted?(taskIsContract?"Строгий контракт · "+taskTakeLabel:"Исполнитель: "+fmt(taskState.assigned_to||taskState.executor,"gpt-executor")+" · "+taskTakeLabel):"Если постановка не подтверждена, задача не считается взятой в работу."}</p></article>
              <article data-ok={taskRunning||taskDone?"yes":"no"}><small>Текущий этап</small><strong>{taskPhase}</strong><p>{taskContract.current_step?humanizeBusinessText(taskContract.current_step,"BORIS продолжает обязательные результаты"):taskState.current_step?humanizeBusinessText(taskState.current_step,"Исполнитель продолжает работу"):taskAccepted?"Жду первый подтверждённый шаг исполнителя":"Исполнение не стартовало"}</p></article>
              <article data-ok={taskDone?"yes":"no"}><small>Приёмка результата</small><strong>{taskCriteriaTotal?String(taskCriteriaDone)+"/"+String(taskCriteriaTotal):taskDone?"Выполнено":"Ещё нет факта"}</strong><p>{taskDone?"Все обязательные артефакты этой команды подтверждены.":taskContract.next_step?humanizeBusinessText(taskContract.next_step,"Следующий шаг уточняется"):taskState.next_step?humanizeBusinessText(taskState.next_step,"Следующий шаг уточняется"):"Результат появится только после фактического выполнения."}</p></article>
            </div>

            <div className={styles.taskArtifacts}>
              <div className={styles.taskArtifactBlock}>
                <div className={styles.taskArtifactTitle}><div><span>ТЕКСТЫ / ОБЪЯВЛЕНИЯ</span><h3>{taskTextRows.length?"Результат этой команды: "+String(taskTextRows.length):"НЕ ВЫПОЛНЕНО · тексты 0"}</h3></div><b data-scoped={taskTextsScoped?"yes":"no"}>{taskTextsScoped?"Подтверждено command_id":"Старые тексты не засчитываются"}</b></div>
                {taskTextRows.length?<div className={styles.taskTextList}>{taskTextRows.slice(0,15).map((ad:any,i:number)=><article key={"task-ad-"+String(i)}><div><small>{"Объявление "+String(i+1)}</small><strong>{String(ad?.title||ad?.name||"Без заголовка")}</strong></div><p>{String(ad?.description||ad?.text||ad?.body||"Описание ещё не записано")}</p></article>)}</div>:<div className={styles.empty}>По этой конкретной команде нет ни одного сохранённого текста. Материалы других задач и старый gen_ads в приёмку не входят.</div>}
              </div>
              <div className={styles.taskArtifactBlock}>
                <div className={styles.taskArtifactTitle}><div><span>БАННЕРЫ</span><h3>{visibleTaskBanners.length?"Результат этой команды: "+String(visibleTaskBanners.length):"НЕ ВЫПОЛНЕНО · баннеры 0"}</h3></div><b data-scoped={taskBannersScoped?"yes":"no"}>{taskBannersScoped?"Подтверждено command_id":"Архив не засчитывается"}</b></div>
                {visibleTaskBanners.length?<div className={styles.taskBannerGrid}>{visibleTaskBanners.slice(0,12).map((banner:any,i:number)=><a href={String(banner?.url||"")} target="_blank" rel="noreferrer" key={"task-banner-"+String(i)}><img src={String(banner?.url||"")} alt={"Баннер "+String(i+1)} /><span>{banner?.proof==="application"?"Применён этой командой":banner?.proof==="selection"?"Выбран этой командой":"Подтверждено"}{banner?.modified_at?" · "+new Date(banner.modified_at).toLocaleString("ru-RU"):""}</span></a>)}</div>:<div className={styles.empty}>По этой конкретной команде нет подтверждённого баннерного результата.</div>}
                {historicalTaskBanners.length?<><div className={styles.taskArtifactTitle}><div><span>АРХИВ АККАУНТА · НЕ ПРИЁМКА</span><h3>Ранее созданные баннеры: {historicalTaskBanners.length}</h3></div><b data-scoped="no">Только источник</b></div><div className={styles.taskBannerGrid}>{historicalTaskBanners.map((banner:any,i:number)=><a href={String(banner?.url||"")} target="_blank" rel="noreferrer" key={"archive-banner-"+String(i)}><img src={String(banner?.url||"")} alt={"Архивный баннер "+String(i+1)} /><span>{banner?.modified_at?new Date(banner.modified_at).toLocaleString("ru-RU"):"Ранее создан"}</span></a>)}</div></>:null}
              </div>
            </div>
            <div className={styles.taskResultActions}><button type="button" onClick={()=>void loadDetail(String(selected.account_id))}>Обновить результат сейчас</button><button type="button" onClick={()=>openClientRoute("/dashboard/campaign")}>Открыть объявления и баннеры</button></div>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHead}><span>ПЛАН BORIS</span><h2>Что именно делает и в какое время</h2><p>BORIS перепроверяет состояние клиента каждые {fmt(executionSchedule?.every_minutes,"5")} минут. Ниже сначала показана постоянная работа по оплаченным модулям, затем — конкретные отклонения, которые BORIS сейчас исправляет или перепроверяет.</p></div>
            <div className={styles.moduleGrid}>{routinePlan.map((r:any)=><article key={`routine-${r.module}`}><div><span className={styles.dot} data-health="pass"/><strong>{MOD[r.module]||humanizeBusinessText(r.module,"Модуль BORIS")}</strong></div><b>{r.execution_kind==="monitor_and_selfheal"?"Контроль + самовосстановление":"Автоматическое выполнение"}</b><p>{r.task}</p><small><b>Следующий запуск:</b> {r.running_now?`выполняется сейчас${r.last_cycle_at?` · старт ${moscowTime(r.last_cycle_at)} МСК`:""}; следующее точное время systemd назначит сразу после завершения текущего цикла`:r.planned_for?`${moscowTime(r.planned_for)} МСК`:"системный таймер ещё не назначил точное время"}</small><small><b>Частота:</b> {routineCadence(r)}</small><small><b>Последний факт:</b> {routineFact(r)}</small></article>)}{!routinePlan.length?<div className={styles.empty}>Для этого клиента нет активных оплаченных модулей с автоматическим расписанием.</div>:null}</div>
            <div className={styles.planClock}><span><b>Последняя проверка</b>{moscowTime(executionSchedule?.last_cycle_at)} МСК</span><span><b>Следующая проверка</b>{moscowTime(executionSchedule?.next_run_at)} МСК</span><span><b>Следующее действие</b>{nextActionAt?`${moscowTime(nextActionAt)} МСК`:"нет отдельного действия"}</span><span><b>Нужен владелец</b>{ownerPlanItems.length}</span></div>
            <div className={styles.laneGrid}>{(["NOW","NEXT","WAITING","BLOCKED"] as const).map(lane=><article className={styles.lane} key={lane}><strong>{({NOW:"СЕЙЧАС",NEXT:"ДАЛЬШЕ",WAITING:"ЖДЁТ",BLOCKED:"БЛОКЕР"} as Record<string,string>)[lane]}</strong><div>{(actionPlan?.queue?.[lane]||[]).slice(0,6).map((x:any,i:number)=><div key={i}><b>{MOD[x.module]||humanizeBusinessText(x.module,"BORIS")}</b><p>{planActionText(x)}</p><small><b>Когда:</b> {x?.execution?.auto_execute?`${moscowTime(x.execution.planned_for)} МСК; далее по расписанию модуля${x.execution.execute_every_minutes?` каждые ${x.execution.execute_every_minutes} мин.`:""}. Контроль состояния каждые ${fmt(x.execution.monitor_every_minutes,"5")} мин.`:x.owner_action_required?`сейчас; после подтверждения BORIS перепроверит решение на ближайшем цикле${x?.execution?.monitor_planned_for?` ${moscowTime(x.execution.monitor_planned_for)} МСК`:""}; безопасная работа без расходов продолжается отдельно`:"контрольируется по системному расписанию"}</small><small><b>Исполнение:</b> {x?.execution?.auto_execute?"автоматически, без команды владельца":x.owner_action_required?"BORIS не может сам подтвердить денежное решение владельца":"контролируется автоматически"}</small><small><b>Последний факт:</b> {x.owner_action_required?"денежное действие не выполнено без разрешения владельца":x?.execution?.last_module_result?`${x.execution.last_module_result.dependency_state==="waiting_owner"?"цикл выполнен; денежная часть безопасно остановлена":String(x.execution.last_module_result.dependency_state||"").includes("external")?"проверено; внешний блокер остаётся, BORIS продолжает авто-перепроверку":x.execution.last_module_result.passed?"выполнено и перепроверено":"цикл завершён с ошибкой"} · ${moscowTime(x.execution.last_cycle_at)} МСК`:"подтверждённый результат появится после реального исполнения"}</small><small><b>Что это даёт клиенту:</b> {clientValue(x.module,x.action||x.classification)}</small></div>)}{!(actionPlan?.queue?.[lane]||[]).length?<small>Нет задач</small>:null}</div></article>)}</div>
          </section>

          <section className={`${styles.section} ${styles.realActions}`}>
            <div className={styles.sectionHead}><span>ФАКТ ПО КЛИЕНТУ</span><h2>Что реально происходит сейчас</h2><p>Без служебных статусов и повторов. Только результат, текущая работа и блокеры.</p></div>
            <div className={styles.ownerRealityGrid}>
              <article data-tone={ownerDoneConfirmed||latestRealAction?"done":"empty"}><small>СДЕЛАНО</small><strong>{ownerDoneText}</strong><p>{ownerDoneAt?"Подтверждено "+new Date(ownerDoneAt).toLocaleString("ru-RU"):"Подтверждённых действий за выбранный период ещё нет."}</p></article>
              <article data-tone={taskDone?"done":taskRunning?"running":taskAccepted?"queued":"empty"}><small>СЕЙЧАС</small><strong>{ownerNowText}</strong><p>{ownerExecutionText}</p></article>
              <article data-tone={taskBlocked?"blocked":"ok"}><small>МЕШАЕТ</small><strong>{ownerBlockerText}</strong><p>{taskBlocked?"BORIS не может продолжить этот шаг без снятия блокера.":"Владелец сейчас не требуется."}</p></article>
            </div>
            {periodActions.length?<div className={styles.marketingActions}>
              <h3>Подтверждённые изменения объявлений</h3>
              {periodActions.slice(0,8).map((a:any)=>{const c=marketingActionChange(a);return <article key={String(a.operation_id||a.timestamp)}>
                <div className={styles.realActionTop}><div><span>ФАКТ</span><strong>{c.label} · объявление {a.avito_item_id||"—"}</strong></div><b data-applied={a.changed_avito===true?"yes":"no"}>{marketingActionStatus(a)}</b></div>
                <p className={styles.actionResult}>{marketingActionResult(a)}</p>
                <div className={styles.realActionBottom}><time>{a.timestamp?new Date(a.timestamp).toLocaleString("ru-RU"):"—"}</time><button type="button" onClick={()=>openClientRoute("/dashboard/campaign")}>Открыть объявление</button></div>
              </article>})}
            </div>:null}
            <details className={styles.techLog}><summary>Показать технический журнал ({periodActivity.length})</summary><div className={styles.activityRows}>{periodActivity.slice(0,30).map((a:any)=><article key={`tech-${a.source}-${a.id}`}><div><strong>{activityTitleText(a,String(selected.name||selected.account_id))}</strong><small>{activitySourceText(a.source)} · {activityKindText(a.kind)} · {activityStatusText(a.status)}</small></div><time>{a.created_at?new Date(a.created_at).toLocaleString("ru-RU"):"—"}</time></article>)}</div></details>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHead}><span>ТОЧНОЕ ВРЕМЯ · ПЛАН / ФАКТ</span><h2>Ближайшие автоматические циклы</h2><p>Здесь показано время реального запуска. После каждого цикла BORIS записывает факт и повторно проверяет состояние; будущий факт заранее не считается выполнением.</p></div>
            <div className={styles.scheduleList}>{cycleSchedule.length?cycleSchedule.map((slot:{label:string;plan:string;fact:string;current:boolean})=><article className={slot.current?styles.currentSchedule:""} key={slot.label}><strong>{slot.label} МСК</strong><div><small>ЧТО БУДЕТ СДЕЛАНО</small><p>{slot.plan}</p></div><div><small>ФАКТ</small><p>{slot.fact}</p></div></article>):<div className={styles.empty}>Жду подтверждение системного цикла. Пустое расписание не выдаётся за выполненную работу.</div>}</div>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHead}><span>ПАМЯТЬ КЛИЕНТА</span><h2>Постоянная ветка, команды и события</h2><p>Не зависит от сохранности чата ChatGPT.</p></div>
            <div className={styles.grid4}>
              <article><small>Событий памяти</small><strong>{workspaceEvents.length}</strong><p>в клиентской ветке</p></article>
              <article><small>Текущая работа</small><strong>{fmt(workspace?.current_state?.dev_job_id)}</strong><p>{humanizeBusinessText(workspace?.current_state?.executor_status,"нет активной работы")}</p></article>
              <article><small>Сейчас</small><strong className={styles.smallStrong}>{humanizeBusinessText(workspace?.current_state?.current_step,"Не зафиксировано")}</strong><p>текущий шаг</p></article>
              <article><small>Дальше</small><strong className={styles.smallStrong}>{humanizeBusinessText(workspace?.current_state?.next_step,"Не зафиксировано")}</strong><p>следующий шаг</p></article>
            </div>
            <div className={styles.eventMini}>{workspaceEvents.slice().reverse().slice(0,8).map((e:any)=><div key={e.id}><span><b>{humanizeBusinessText(e.text||e.event_type,"Системное событие")}</b><small>{humanizeBusinessText(e.actor,"Система")} · {humanizeBusinessText(e.event_type,"событие")}</small></span><time>{e.created_at?new Date(e.created_at).toLocaleString("ru-RU"):""}</time></div>)}</div>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHead}><span>ДИАЛОГИ И МОП</span><h2>Кто реально пишет и что это за обращения</h2><p>Классификация нужна только для контроля владельца. Она не переписывает KPI: соискатели по-прежнему исключаются отдельным подтверждённым правилом.</p></div>
            <div className={styles.dialogSummary}>
              <button type="button" onClick={()=>openClientRoute("/messages")}><small>Последние диалоги</small><strong>{dialogSummary.total}</strong></button>
              <button type="button" onClick={()=>openClientRoute("/messages")}><small>Похожи на запрос клиента</small><strong>{dialogSummary.business_inquiry||0}</strong></button>
              <button type="button" onClick={()=>openClientRoute("/messages")}><small>Соискатели</small><strong>{dialogSummary.job_seeker||0}</strong></button>
              <button type="button" onClick={()=>openClientRoute("/messages")}><small>Реклама / спам</small><strong>{dialogSummary.spam_advertising||0}</strong></button>
              <button type="button" onClick={()=>openClientRoute("/messages")}><small>Не лиды / прочее</small><strong>{(dialogSummary.system||0)+(dialogSummary.purchase||0)+(dialogSummary.other_unknown||0)}</strong></button>
            </div>
            <p className={styles.dialogNote}>Подтверждённо исключено из бизнес-KPI сегодня как соискатели: <b>{excludedJobSeekers}</b>.</p>
            <div className={styles.dialogGrid}>{dialogs.map((dialog:any,dialogIndex:number)=>{
              const chatId=String(dialog?.avito_chat_id||"");const thread=dialogThreads[chatId]||{};const messages=Array.isArray(thread?.messages)?thread.messages:(Array.isArray(thread?.dialog?.messages)?thread.dialog.messages:[]);
              const incoming=messages.slice().reverse().find((m:any)=>!isOutgoing(m));const outgoing=messages.slice().reverse().find((m:any)=>isOutgoing(m));
              const dtype=inferDialogType(dialog,thread);
              const isJob=dtype==="job_seeker";
              return <article className={`${isJob?styles.jobDialog:""} ${styles[`dialog_${dialogTypeClass(dtype)}`]||""}`} key={chatId||dialog?.last_at||dialogIndex}><div><strong>{dialog?.client_name||"Диалог Avito"}</strong><span>{dialogTypeText(dtype)}</span></div><p><b>Что написал человек:</b> {messageText(incoming)||dialog?.last_text||"—"}</p><p><b>Ответ МОП / BORIS:</b> {messageText(outgoing)||thread?.draft?.text||"Ответ ещё не отправлен"}</p>{thread?.draft?.text?<small>Черновик МОПа: {humanizeBusinessText(thread.draft.status,"готовится")}</small>:null}<button type="button" className={styles.dialogOpen} onClick={()=>openClientRoute("/messages")}>Открыть диалог</button></article>
            })}{!dialogs.length?<div className={styles.empty}>{liveLoading?"Загружаю диалоги…":"Новых диалогов нет."}</div>:null}</div>
          </section>

          {(problems.length||human.length)?<section className={styles.section}>
            <div className={styles.sectionHead}><span>ТРЕБУЕТ ВНИМАНИЯ</span><h2>Что нельзя потерять</h2></div>
            <div className={styles.issueGrid}>
              {problems.map((x:any,i:number)=><article key={`p-${i}`}><b>{moduleText(x.module)}</b><p>{reasonText(x.reason)}</p><small>{x.safe_action?"BORIS: "+humanizeBusinessText(x.safe_action,"выполняется безопасная проверка"):x.owner_action_required?"Нужно подтвердить учётный / платёжный статус":"Внешнее действие / проверка"}</small></article>)}
              {human.map((x:any,i:number)=>{const mc=x?.manager_control||{};const reminded=Number(mc?.reminded_tasks||0);const events=Number(mc?.reminder_events||0);const unassigned=Number(mc?.unassigned_tasks||0);const items=(Array.isArray(x?.items)?x.items:[]).filter((t:any)=>t?.due_at).slice().sort((a:any,b:any)=>new Date(a.due_at).getTime()-new Date(b.due_at).getTime());const nextDue=items[0]?.due_at||x?.oldest_due_at;return <article key={`h-${i}`}><b>Работа менеджера · {moduleText(x.module,"Модуль BORIS")}</b><p>{humanizeBusinessText(x.reason,"Нужен контроль следующего действия менеджера")}</p>{nextDue?<small><b>Срок менеджера:</b> {moscowTime(nextDue,true)} МСК</small>:null}<small><b>Контроль BORIS:</b> каждые 5 минут{reminded?` · уже напомнил по ${reminded}`:""}{events?` · всего напоминаний: ${events}`:""}</small>{items.slice(0,3).map((t:any,ti:number)=><small key={`ht-${ti}`}>{humanizeBusinessText(t.title,"Связаться с клиентом")} · {moscowTime(t.due_at,true)} МСК</small>)}{mc?.last_reminded_at?<small>Последний контроль: {moscowTime(mc.last_reminded_at,true)} МСК</small>:null}<small>{unassigned?`Без ответственного: ${unassigned}`:"Ответственные назначены"} · владельцу действие не требуется</small></article>})}
            </div>
          </section>:null}
        </>}
      </div>
    </section>
  </main></AppShell>;
}
