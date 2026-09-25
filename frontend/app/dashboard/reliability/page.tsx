"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPost } from "../../lib/api";
import { Alert, Button, Page } from "../../ui";
import AppShell from "@/app/components/shell/AppShell";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

const MODULES = [
  ["messages", "Сообщения / МОП"],
  ["crm", "CRM"],
  ["telephony", "Телефония"],
  ["marketing", "ИИ-маркетолог"],
  ["social", "Соцсети"],
  ["billing", "Платежи"],
  ["media", "Фото / медиа"],
] as const;

const stateText:any={ok:"Работает",degraded:"Есть внимание",critical:"Критично",warning:"Внимание",stale:"Не отвечает"};
const stateIcon:any={ok:"✓",degraded:"!",critical:"!",warning:"!",stale:"×"};

export default function ReliabilityPage(){
  const [health,setHealth]=useState<any>(null);
  const [guardian,setGuardian]=useState<any>(null);
  const [capacity,setCapacity]=useState<any>(null);
  const [events,setEvents]=useState<any[]>([]);
  const [dead,setDead]=useState<any[]>([]);
  const [switches,setSwitches]=useState<any[]>([]);
  const [loading,setLoading]=useState(false);
  const [notice,setNotice]=useState("");

  const load=async()=>{
    setLoading(true); setNotice("");
    try{
      const paths=["/api/reliability/health","/api/reliability/guardian","/api/reliability/capacity","/api/reliability/events?limit=40","/api/reliability/dead-letter?limit=30","/api/reliability/kill-switches"];
      const rs=await Promise.allSettled(paths.map(p=>apiGet(p)));
      if(rs[0].status==="fulfilled")setHealth(rs[0].value);
      if(rs[1].status==="fulfilled")setGuardian(rs[1].value);
      if(rs[2].status==="fulfilled")setCapacity(rs[2].value);
      if(rs[3].status==="fulfilled")setEvents(Array.isArray(rs[3].value)?rs[3].value:[]);
      if(rs[4].status==="fulfilled")setDead(Array.isArray(rs[4].value)?rs[4].value:[]);
      if(rs[5].status==="fulfilled")setSwitches(Array.isArray(rs[5].value)?rs[5].value:[]);
    }finally{setLoading(false)}
  };
  useEffect(()=>{void load()},[]);

  const setSwitch=async(module:string,blocked:boolean)=>{
    setNotice("");
    try{
      await apiPost("/api/reliability/kill-switches",{module,account_id:"*",blocked,reason:blocked?"Остановлено владельцем через System Guardian":""});
      setNotice(blocked?`Модуль «${module}» безопасно остановлен.`:`Модуль «${module}» снова разрешён.`);
      await load();
    }catch{setNotice("Не удалось изменить аварийный выключатель. Проверьте права владельца.")}
  };
  const retry=async(id:number)=>{try{await apiPost(`/api/reliability/dead-letter/${id}/retry`,{});setNotice(`Задача #${id} возвращена в очередь.`);await load()}catch{setNotice(`Не удалось повторить задачу #${id}.`)}};
  const sw=(m:string)=>switches.find((x:any)=>x.module===m&&x.account_id==="*");
  const checks=guardian?.checks||[];
  const workers=health?.workers?.workers||[];
  const modules=capacity?.breakdown?.modules||{};
  const noisy=capacity?.breakdown?.hot_accounts||[];
  const execution=health?.job_execution_24h||{};
  const scalePlan=capacity?.plan?.scenarios||[];

  return <AppShell active="reliability" title="System Guardian" subtitle="Надёжность, очереди, процессы и защита BORIS"><Page className="boris-reliability-page">
    <header className="boris-reliability-head">
      <div><span>Защита всей платформы</span><h1>Надёжность BORIS</h1><p>Изоляция модулей и клиентов, очередь задач, живые процессы, резервные копии, диск, аварийные выключатели и журнал отказов.</p></div>
      <Button onClick={()=>void load()} disabled={loading}>{loading?"Проверяю…":"Обновить"}</Button>
    </header>
    {notice&&<Alert kind="info">{notice}</Alert>}

    <section className="boris-reliability-status-strip">
      <div data-state={health?.database?.state||"degraded"}><i>{stateIcon[health?.database?.state||"degraded"]}</i><span>База данных</span><strong>{stateText[health?.database?.state||"degraded"]}</strong></div>
      <div data-state={health?.queue?.state||"degraded"}><i>{stateIcon[health?.queue?.state||"degraded"]}</i><span>Очередь задач</span><strong>{stateText[health?.queue?.state||"degraded"]}</strong></div>
      <div data-state={health?.workers?.state||"degraded"}><i>{stateIcon[health?.workers?.state||"degraded"]}</i><span>Процессы</span><strong>{stateText[health?.workers?.state||"degraded"]}</strong></div>
      <div data-state={health?.backup?.state||"degraded"}><i>{stateIcon[health?.backup?.state||"degraded"]}</i><span>Резервная копия</span><strong>{stateText[health?.backup?.state||"degraded"]}</strong></div>
      <div data-state={health?.storage?.state||"degraded"}><i>{stateIcon[health?.storage?.state||"degraded"]}</i><span>Диск</span><strong>{health?.storage?`${health.storage.free_gb} ГБ свободно`:"—"}</strong></div>
    </section>

    <div className="boris-reliability-main-grid">
      <section className="boris-reliability-panel"><div className="boris-reliability-panel-head"><span>Автопроверка</span><h2>Что реально работает сейчас</h2></div><div className="boris-reliability-checks">{checks.map((x:any)=><div key={x.key} data-state={x.state}><i>{stateIcon[x.state]||"!"}</i><p><b>{x.name}</b><small>{x.message}</small></p><strong>{stateText[x.state]||x.state}</strong></div>)}</div></section>
      <section className="boris-reliability-panel"><div className="boris-reliability-panel-head"><span>Изоляция отказов</span><h2>Аварийные выключатели</h2></div><p className="boris-reliability-hint">Останавливают действия только выбранного контура. Остальной BORIS продолжает работать.</p><div className="boris-reliability-switches">{MODULES.map(([m,n])=>{const x=sw(m);const blocked=!!x?.blocked;return <div key={m}><p><b>{n}</b><small>{blocked?(x?.reason||"Остановлен"):"Работает независимо"}</small></p><button className={blocked?"is-resume":"is-stop"} onClick={()=>void setSwitch(m,!blocked)}>{blocked?"Включить":"Аварийно остановить"}</button></div>})}</div></section>
    </div>

    <div className="boris-reliability-triple-grid">
      <section className="boris-reliability-panel"><div className="boris-reliability-panel-head"><span>Очереди</span><h2>Нагрузка по модулям</h2></div><div className="boris-reliability-compact-list">{Object.keys(modules).length?Object.entries(modules).map(([name,x]:any)=><div key={name}><b>{name}</b><span>в очереди {x.queued}</span><strong>{x.running} работают</strong></div>):<div className="empty">Активной очереди нет</div>}</div></section>
      <section className="boris-reliability-panel"><div className="boris-reliability-panel-head"><span>Изоляция клиентов</span><h2>Самые активные клиенты</h2></div><div className="boris-reliability-compact-list">{noisy.length?noisy.map((x:any)=><div key={x.account_id}><b>{x.account_id}</b><span>{x.queued} в очереди</span><strong>{x.running} работают</strong></div>):<div className="empty">Перегрузки клиентов нет</div>}</div></section>
      <section className="boris-reliability-panel"><div className="boris-reliability-panel-head"><span>Контроль процессов</span><h2>Живые процессы</h2></div><div className="boris-reliability-compact-list">{workers.length?workers.map((x:any)=><div key={`${x.module}-${x.worker_id}`}><b>{x.worker_id}</b><span>{x.module} · {x.age_sec} сек назад</span><strong className={x.state==="ok"?"ok":"bad"}>{stateText[x.state]||x.state}</strong></div>):<div className="empty">Данные о процессах ещё не получены</div>}</div></section>
    </div>

    <section className="boris-reliability-panel">
      <div className="boris-reliability-panel-head"><span>Запас по росту</span><h2>Готовность к большему числу клиентов</h2></div>
      <p className="boris-reliability-hint">Расчёт использует текущие ресурсы сервера и базы данных. Он не делает платных запросов к рекламным или AI-сервисам.</p>
      <div className="boris-reliability-compact-list">
        {scalePlan.length?scalePlan.map((x:any)=><div key={x.tenants}><b>{x.tenants} клиентов</b><span>{x.waves_min} волн при {x.worker_slots} фоновых процессах · пик БД около {x.peak_background_db_connections_est}</span><strong className={x.state==="ok"?"ok":"bad"}>{x.state==="ok"?"Ресурсов хватает":"Нужен запас"}</strong></div>):<div className="empty">Расчёт ёмкости ещё не получен</div>}
      </div>
    </section>

    <section className="boris-reliability-panel">
      <div className="boris-reliability-panel-head"><span>Последние 24 часа</span><h2>Работа и защита от повторных затрат</h2></div>
      <p className="boris-reliability-hint">BORIS отдельно считает обычное продолжение больших задач и реальные ошибки. Так обработка 20 объявлений не выглядит как 20 сбоев.</p>
      <div className="boris-reliability-compact-list">
        <div><b>Запусков обработки</b><span>все шаги фоновых задач</span><strong>{execution.executions??0}</strong></div>
        <div><b>Продолжений больших задач</b><span>нормальная пакетная обработка</span><strong>{execution.continuations??0}</strong></div>
        <div><b>Ожиданий внешних сервисов</b><span>без потери задачи и горячего цикла</span><strong>{execution.provider_deferrals??0}</strong></div>
        <div><b>Ограничений одного клиента</b><span>BORIS придержал задачу, чтобы клиент не занял все процессы</span><strong>{execution.tenant_deferrals??0}</strong></div>
        <div><b>Защит от двойного запуска</b><span>вторая нода увидела живой execution-lock и не стала дублировать работу</span><strong>{execution.execution_lock_deferrals??0}</strong></div>
        <div><b>Автовосстановлений задач</b><span>worker прервался, задача безопасно вернулась в очередь</span><strong>{execution.recoveries??0}</strong></div>
        <div><b>Реальных ошибок выполнения</b><span>не смешиваются с обычными шагами</span><strong>{execution.failures??0}</strong></div>
        <div><b>Не оплачено повторно</b><span>генераций повторно использовано из защищённого результата</span><strong>{execution.avoided_paid_generations??0}</strong></div>
      </div>
    </section>

    <section className="boris-reliability-panel"><div className="boris-reliability-panel-head"><span>Сохранённые задачи</span><h2>Задачи, которые BORIS не потерял</h2></div>{dead.length?<div className="boris-reliability-table"><div className="head"><b>Задача</b><b>Модуль</b><b>Клиент</b><b>Запусков</b><b>Состояние</b><b></b></div>{dead.map((x:any)=><div key={x.id}><strong>#{x.id}</strong><span>{humanizeBusinessText(x.kind,"Системная задача")}</span><span>{x.account_id||"—"}</span><span>{x.attempts}</span><span>{humanizeBusinessText(x.status,"Состояние уточняется")}</span>{x.retryable===false?<button disabled title="Итог внешней публикации уже получен. Исправьте отклонённые объявления и снова подготовьте кампанию.">Нужна правка</button>:<button onClick={()=>void retry(x.id)}>{x.retry_action==="recheck_existing_upload"?"Проверить статус":"Повторить"}</button>}</div>)}</div>:<div className="boris-reliability-empty">Необработанных аварийных задач нет.</div>}</section>

    <section className="boris-reliability-panel"><div className="boris-reliability-panel-head"><span>Журнал событий</span><h2>Последние системные события</h2></div>{events.length?<div className="boris-reliability-events">{events.map((x:any)=><div key={x.id} data-severity={x.severity}><i/><p><b>{humanizeBusinessText(x.message||x.event_type,"Системное событие")}</b><small>{humanizeBusinessText(x.module,"Система")} · {x.account_id||"система"} · код {String(x.trace_id||"").slice(0,12)}</small></p><time>{x.created_at?new Date(x.created_at).toLocaleString("ru-RU"):""}</time></div>)}</div>:<div className="boris-reliability-empty">Критических системных событий пока нет.</div>}</section>
  </Page></AppShell>
}
