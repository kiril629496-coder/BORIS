"use client";

import { useCallback, useEffect, useState } from "react";
import { useAccounts } from "@/app/lib/AccountContext";
import AppShell from "@/app/components/shell/AppShell";
import { Alert, Button, Card, Page, Textarea } from "@/app/ui";

const PERSONAS = [
  { icon:"👤", title:"Новый предприниматель", text:"Первый день в BORIS. Не технарь. Проверяет, понятно ли с чего начать и куда нажать." },
  { icon:"📱", title:"Занятый клиент с телефона", text:"Проверяет мобильную версию, читаемость, размер действий и скорость принятия решения." },
  { icon:"🏢", title:"Агентство", text:"Работает с несколькими клиентами и проверяет, не путаются ли аккаунты, статусы и действия." },
  { icon:"💬", title:"Владелец продаж", text:"Ищет лиды, сообщения, сделки и хочет понимать, что BORIS делает самостоятельно." },
  { icon:"🔎", title:"Скептичный платящий клиент", text:"Ищет факты, результат, понятные статусы и раздражается от обещаний без доказательств." },
];

function headers(extra:Record<string,string>={}) {
  const token=typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return {"Content-Type":"application/json", Authorization:`Bearer ${token}`, ...extra};
}

export default function UxTesterPage(){
  const { loading: accountLoading } = useAccounts();
  const [privateOwner,setPrivateOwner]=useState(false);
  const [authLoading,setAuthLoading]=useState(true);
  const [task,setTask]=useState("Пройди BORIS как реальный клиент. Проверь Главную, подключение Avito, Объявления, Сообщения, Маркетинг, Продажи, Аналитику и оплату. Оцени удобство, логику, понятность названий и следующего шага, количество лишних действий, мобильную версию и то, понимает ли клиент, что BORIS делает сам. Дай конкретную обратную связь: что мешает, почему и что изменить.");
  const [running,setRunning]=useState(false);
  const [latest,setLatest]=useState<any>(null);
  const [reports,setReports]=useState<any[]>([]);
  const [error,setError]=useState("");
  const [message,setMessage]=useState("");

  useEffect(()=>{
    let alive=true;
    fetch("/api/auth/me",{headers:headers(),cache:"no-store"})
      .then(async r=>({ok:r.ok,data:await r.json().catch(()=>null)}))
      .then(({ok,data})=>{
        if(!alive) return;
        const u=data?.user || data || {};
        const uid=Number(u?.id || 0);
        const email=String(u?.email || "").trim().toLowerCase();
        setPrivateOwner(Boolean(ok && uid===2 && email==="ostapenko-kirill-86@yandex.ru"));
      })
      .catch(()=>{if(alive)setPrivateOwner(false)})
      .finally(()=>{if(alive)setAuthLoading(false)});
    return()=>{alive=false};
  },[]);

  const load=useCallback(async()=>{
    try{
      const [s,r]=await Promise.all([
        fetch("/api/qa/ux-client/status",{headers:headers(),cache:"no-store"}),
        fetch("/api/qa/ux-client/reports",{headers:headers(),cache:"no-store"}),
      ]);
      const sd=await s.json(); const rd=await r.json();
      if(!s.ok) throw new Error(sd?.detail || sd?.message || `HTTP ${s.status}`);
      setRunning(Boolean(sd.running)); setLatest(sd.latest || null);
      if(r.ok) setReports(rd.reports || []);
    }catch(e:any){ setError(e?.message || "Не удалось получить состояние UX-тестировщика"); }
  },[]);

  useEffect(()=>{if(!authLoading && privateOwner) void load()},[load,authLoading,privateOwner]);
  useEffect(()=>{
    if(!privateOwner || !running) return;
    const timer=window.setInterval(()=>void load(),5000);
    return ()=>window.clearInterval(timer);
  },[running,load]);

  async function run(){
    setError("");setMessage("");
    try{
      const res=await fetch("/api/qa/ux-client/run",{method:"POST",headers:headers(),body:JSON.stringify({task})});
      const data=await res.json();
      if(!res.ok) throw new Error(data?.detail || data?.message || `HTTP ${res.status}`);
      setRunning(true); setMessage(data?.message || "Тестирование запущено");
    }catch(e:any){setError(e?.message || "Не удалось запустить тестирование")}
  }

  const summary=latest?.summary || {};
  if(!accountLoading && !authLoading && !privateOwner) return <AppShell active="ux-tester" title="UX-тестировщик"><Page><Alert kind="danger">Этот внутренний модуль доступен только владельцу платформы BORIS.</Alert></Page></AppShell>;
  return <AppShell active="ux-tester" title="UX-тестировщик" subtitle="BORIS проходит продукт глазами разных клиентов и показывает, где интерфейс непонятен">
    <Page className="boris-ux-tester-page">
      {error?<Alert kind="danger">{error}</Alert>:null}
      {message?<Alert kind="success">{message}</Alert>:null}

      <Card className="boris-ux-tester-hero">
        <div className="boris-ux-tester-hero-copy">
          <span>ИИ-КОНТРОЛЬ УДОБСТВА</span>
          <h2>Посмотреть на BORIS глазами клиента</h2>
          <p>Тестировщик не меняет данные клиента и ничего не публикует. Он проходит реальные production-экраны в браузере, проверяет понятность, перегрузку, мобильную версию, следующий шаг и логику интерфейса.</p>
        </div>
        <div className={`boris-ux-tester-status ${running?"is-running":""}`}>
          <b>{running?"Тестирует сейчас":"Готов к тесту"}</b>
          <small>{running?"Проходит сценарии и собирает замечания":"Можно дать новую задачу"}</small>
        </div>
      </Card>

      <div className="boris-ux-tester-personas">
        {PERSONAS.map((p,i)=><Card key={p.title} className="boris-ux-persona-card">
          <div className="boris-ux-persona-icon">{p.icon}</div><b>{p.title}</b><p>{p.text}</p><span>Сценарий {String(i+1).padStart(2,"0")}</span>
        </Card>)}
      </div>

      <Card className="boris-ux-tester-task">
        <div><span>ЗАДАЧА ТЕСТИРОВЩИКУ</span><h2>Что проверить сейчас</h2><p>Можно менять формулировку перед каждым запуском. Базовые персоны и безопасные пользовательские маршруты сохраняются.</p></div>
        <Textarea value={task} onChange={e=>setTask(e.target.value)} rows={6}/>
        <div className="boris-ux-tester-actions">
          <Button onClick={run} disabled={running || !task.trim()}>{running?"Тестирование идёт…":"Начать UX-тестирование"}</Button>
          <Button kind="ghost" onClick={()=>void load()}>Обновить результат</Button>
        </div>
      </Card>

      <div className="boris-ux-tester-result-grid">
        <Card className="boris-ux-result-score">
          <span>ПОСЛЕДНИЙ РЕЗУЛЬТАТ</span>
          <strong>{latest?`${summary.score ?? 0}/100`:"—"}</strong>
          <p>{latest?`Проверено ${summary.personas || 0} типов клиентов и ${summary.pages || 0} экранов.`:"Первый отчёт ещё не сформирован."}</p>
          {latest?.finished_at?<small>{new Date(latest.finished_at).toLocaleString("ru-RU")}</small>:null}
        </Card>
        <Card className="boris-ux-result-issues">
          <div className="boris-ux-result-head"><span>ГЛАВНЫЕ ЗАМЕЧАНИЯ</span><b>{summary.issues || 0}</b></div>
          {summary.top_issues?.length?<ol>{summary.top_issues.slice(0,8).map((x:string,i:number)=><li key={`${i}-${x}`}>{x}</li>)}</ol>:<p>После первого прогона здесь появятся конкретные замечания глазами клиентов.</p>}
        </Card>
      </div>

      {latest?.personas?.length?<Card className="boris-ux-persona-results boris-ux-detailed-reports">
        <div><span>ПОДРОБНЫЕ ОТЧЁТЫ КАЖДОГО ТЕСТА</span><h2>Что увидел и понял каждый тип клиента</h2><p>Каждая персона раскрывается отдельно: маршрут, оценка каждой страницы, проблемы, сильные стороны и технические факты.</p></div>
        <div className="boris-ux-detail-list">{latest.personas.map((p:any,idx:number)=><details key={p.id} className="boris-ux-detail" open={idx===0}>
          <summary><span><b>{p.name}</b><small>{p.about}</small></span><strong>{p.score}/100</strong></summary>
          <div className="boris-ux-detail-body">
            <section><h3>Главные замечания этого клиента</h3>{p.top_issues?.length?<ol>{p.top_issues.map((x:string,i:number)=><li key={i}>{x}</li>)}</ol>:<p>Критичных замечаний не найдено.</p>}</section>
            <section><h3>Проверенные экраны</h3><div className="boris-ux-page-tests">{p.pages?.map((pg:any)=><article key={pg.route} className="boris-ux-page-test">
              <header><div><b>{pg.title || pg.route}</b><code>{pg.route}</code></div><strong>{pg.score}/100</strong></header>
              <div className="boris-ux-page-columns">
                <div><h4>Что мешает клиенту</h4>{pg.issues?.length?<ul>{pg.issues.map((x:string,i:number)=><li key={i}>{x}</li>)}</ul>:<p>Проблем не найдено.</p>}</div>
                <div><h4>Что сделано хорошо</h4>{pg.positives?.length?<ul>{pg.positives.map((x:string,i:number)=><li key={i}>{x}</li>)}</ul>:<p>Положительных сигналов нет.</p>}</div>
              </div>
              <footer><span>В DOM: {pg.visible_controls ?? 0}</span><span>В первом экране: {pg.viewport_controls ?? pg.visible_controls ?? 0}</span><span>Рабочая область: {pg.content_controls ?? "—"}</span><span>Shell: {pg.shell_controls ?? "—"}</span><span>Мелких mobile: {pg.mobile_tiny_controls ?? 0}</span><span>Overflow: {pg.overflow?"есть":"нет"}</span><span>Время: {pg.time ?? "—"} c</span></footer>
              {pg.shot?<a className="boris-ux-shot-link" href={`/images/qa/ux_client/${pg.shot}`} target="_blank" rel="noreferrer">Открыть скриншот этого теста →</a>:null}
            </article>)}</div></section>
          </div>
        </details>)}</div>
      </Card>:null}

      {reports.length?<Card className="boris-ux-history"><span>ИСТОРИЯ</span><h2>Предыдущие отчёты</h2><div>{reports.slice(0,8).map((r:any)=><a key={r.url} href={r.url} target="_blank" rel="noreferrer">{r.name}<span>Открыть →</span></a>)}</div></Card>:null}
    </Page>
  </AppShell>
}
