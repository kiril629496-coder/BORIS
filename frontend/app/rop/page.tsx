"use client";

import { useEffect, useState, useCallback } from "react";
import NpsKpi from "../components/NpsKpi";
import {
  Page, Card, Button, Badge, Alert, EmptyState, Kpi, KpiRow, Icon, Table,
} from "../ui";
import { color, space, font } from "../ui/tokens";
import SectionGuide from "../components/SectionGuide";

type Acc = {
  account_id: string; name: string; avito_user_id: string; bound: boolean;
  memory_shared: boolean; memory_mode: string;
};
type Stats = {
  status: string; total?: number; answered?: number; missed?: number;
  answered_pct?: number; missed_pct?: number; total_min?: number; message?: string;
};
type Rop = { minutes?: number; reports_calls?: number; reports_chats?: number;
  chats?: number; period?: any };

function api(path: string, init: RequestInit = {}) {
  const token =
    typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return fetch(path, {
    ...init,
    headers: {
      Authorization: "Bearer " + token,
      ...(init.headers || {}),
    },
  });
}

export default function RopPage() {
  const [accounts, setAccounts] = useState<Acc[]>([]);
  const [stats, setStats] = useState<Record<string, Stats>>({});
  const [limits, setLimits] = useState<Record<string, Rop>>({});
  const [reports, setReports] = useState<Record<string, any[]>>({});
  const [summaries, setSummaries] = useState<Record<string, any>>({});
  const [chatReviews, setChatReviews] = useState<Record<string, any[]>>({});
  const [trainPreview, setTrainPreview] = useState<Record<string, any>>({});
  const [changeLifecycle, setChangeLifecycle] = useState<Record<string, any[]>>({});
  const [scope, setScope] = useState("all");
  const [mode, setMode] = useState<"by" | "sum">("by");
  const [days, setDays] = useState(1);
  const [tab, setTab] = useState<"summary" | "calls" | "chats" | "reports" | "settings">("summary");
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState("");
  const [actionBusy, setActionBusy] = useState("");
  const [actionNote, setActionNote] = useState<Record<string, string>>({});

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const accountId = params.get("account_id") || "";
    const requestedTab = params.get("tab") || "";
    if (accountId) { setScope(accountId); setMode("by"); }
    if (["summary","calls","chats","reports","settings"].includes(requestedTab)) setTab(requestedTab as any);
  }, []);

  /** Состав аккаунтов приходит из ai_bindings — ничего не зашито. */
  const loadAccounts = useCallback(async () => {
    try {
      const r = await api("/api/ai/accounts?product=rop");
      if (!r.ok) { setNote("Не удалось получить список аккаунтов"); setLoading(false); return; }
      const d = await r.json();
      setAccounts((d.accounts || []).filter((a: Acc) => a.bound));
    } catch { setNote("Ошибка сети"); }
    setLoading(false);
  }, []);

  useEffect(() => { loadAccounts(); }, [loadAccounts]);

  // Разборы звонков: руководитель должен видеть работу менеджеров
  // в кабинете, а не переключаться на старый интерфейс.
  const [reviews, setReviews] = useState<any>({});
  const [openCall, setOpenCall] = useState<string>("");

  const loadData = useCallback(async (list: Acc[]) => {
    for (const a of list) {
      const q = "account_id=" + encodeURIComponent(a.account_id);
      try {
        const [s, l, rp, an, ch, sm, lc] = await Promise.all([
          api(`/api/calltracking/stats?${q}&days=${days}`),
          api(`/api/calltracking/all_limits?${q}`),
          api(`/api/calltracking/reports/list?${q}`),
          api(`/api/calltracking/analyzed?${q}&days=${days}`),
          api(`/api/calltracking/analyzed_chats?${q}&days=${days}`),
          api(`/api/calltracking/product_summary?${q}&days=${days}`),
          api(`/api/calltracking/mop_change_lifecycle?${q}`),
        ]);
        if (an.ok) { const d = await an.json();
          setReviews((p: any) => ({ ...p, [a.account_id]: d["звонки"] || [] })); }
        if (ch.ok) { const d = await ch.json(); setChatReviews((p: any) => ({ ...p, [a.account_id]: d["переписки"] || [] })); }
        if (sm.ok) { const d = await sm.json(); setSummaries((p: any) => ({ ...p, [a.account_id]: d })); }
        if (s.ok) { const d = await s.json(); setStats((p) => ({ ...p, [a.account_id]: d })); }
        if (l.ok) { const d = await l.json(); setLimits((p) => ({ ...p, [a.account_id]: d.rop || {} })); }
        if (rp.ok) { const d = await rp.json(); setReports((p) => ({ ...p, [a.account_id]: d.items || [] })); }
        if (lc.ok) { const d = await lc.json(); setChangeLifecycle((p) => ({ ...p, [a.account_id]: d.items || [] })); }
      } catch { /* аккаунт без доступа не должен ронять экран */ }
    }
  }, [days]);

  useEffect(() => { if (accounts.length) loadData(accounts); }, [accounts, loadData]);

  const downloadReport = async (accountId: string, report: any) => {
    const key = accountId + ":report:" + String(report.id ?? "");
    setActionBusy(key);
    try {
      const r = await api(`/api/calltracking/reports/download?account_id=${encodeURIComponent(accountId)}&id=${encodeURIComponent(String(report.id))}`);
      if (!r.ok) throw new Error("Не удалось скачать отчёт");
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = report.name || `rop-report-${report.id}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setActionNote((p) => ({ ...p, [accountId]: e?.message || "Ошибка скачивания отчёта" }));
    } finally {
      setActionBusy("");
    }
  };

  const previewTraining = async (accountId: string) => {
    setActionBusy(accountId + ":preview");
    try {
      const r = await api(`/api/calltracking/train_manager?account_id=${encodeURIComponent(accountId)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ apply: false }),
      });
      const d = await r.json(); if (!r.ok || d.status !== "ok") throw new Error(d.message || "Не удалось подготовить выводы");
      setTrainPreview((p) => ({ ...p, [accountId]: d }));
    } catch (e: any) { setActionNote((p) => ({ ...p, [accountId]: e?.message || "Ошибка подготовки" })); }
    finally { setActionBusy(""); }
  };

  const trainManager = async (accountId: string) => {
    const key = accountId + ":train"; setActionBusy(key);
    try {
      const r = await api(`/api/calltracking/train_manager?account_id=${encodeURIComponent(accountId)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ apply: true, rules: trainPreview[accountId]?.rules, preview_id: trainPreview[accountId]?.preview_id }),
      });
      const d = await r.json(); if (!r.ok || d.status !== "ok") throw new Error(d.message || "Не удалось передать рекомендации МОП");
      setActionNote((p) => ({ ...p, [accountId]: `Изменение применено. BORIS наблюдает следующие реальные взаимодействия · ${new Date().toLocaleString("ru-RU")}` }));
      setTrainPreview((p) => ({ ...p, [accountId]: null }));
      const lc = await api(`/api/calltracking/mop_change_lifecycle?account_id=${encodeURIComponent(accountId)}`);
      if (lc.ok) { const ld=await lc.json(); setChangeLifecycle((p)=>({...p,[accountId]:ld.items||[]})); }
    } catch (e: any) { setActionNote((p) => ({ ...p, [accountId]: e?.message || "Ошибка обучения МОП" })); }
    finally { setActionBusy(""); }
  };

  const rollbackChange = async (accountId:string, changeId:string) => {
    if (!window.confirm("Вернуть только это изменение РОП? Более новые изменения не будут затронуты.")) return;
    setActionBusy(accountId+":rollback:"+changeId);
    try {
      const r=await api(`/api/calltracking/mop_change_rollback?account_id=${encodeURIComponent(accountId)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({change_id:changeId})});
      const d=await r.json(); if(!r.ok || d.status!=="ok") throw new Error(d.message||"Не удалось вернуть изменение");
      const lc=await api(`/api/calltracking/mop_change_lifecycle?account_id=${encodeURIComponent(accountId)}`); if(lc.ok){const ld=await lc.json();setChangeLifecycle(p=>({...p,[accountId]:ld.items||[]}));}
      setActionNote(p=>({...p,[accountId]:"Предыдущее правило возвращено. Изменения после него не затронуты."}));
    } catch(e:any){setActionNote(p=>({...p,[accountId]:e?.message||"Ошибка возврата"}));}
    finally{setActionBusy("");}
  };

  const shown = scope === "all" ? accounts : accounts.filter((a) => a.account_id === scope);
  const sum = shown.reduce((acc, a) => {
    const s = stats[a.account_id] || {};
    acc.total += s.total || 0; acc.answered += s.answered || 0; acc.missed += s.missed || 0;
    acc.minutes += toNum((limits[a.account_id] || {}).minutes);
    return acc;
  }, { total: 0, answered: 0, missed: 0, minutes: 0 });
  const sumPct = sum.total ? Math.round((sum.answered / sum.total) * 100) : 0;

  if (loading) {
    return (
        <Page className="boris-rop-reference-page">
          <Card>
            <div style={font.h3 as React.CSSProperties}>Загружаю данные РОП…</div>
            <div style={{ ...font.small, marginTop: space.sm }}>
              Получаю реальные звонки, метрики, разборы и отчёты по подключённым аккаунтам.
            </div>
          </Card>
        </Page>
    );
  }

  if (!accounts.length) {
    return (
        <Page className="boris-rop-reference-page">
          {note ? (
            <Alert kind="danger">
              {note}
              <div style={{ marginTop: space.sm }}>
                <Button onClick={() => { setLoading(true); setNote(""); void loadAccounts(); }}>Повторить</Button>
              </div>
            </Alert>
          ) : (
          <EmptyState
            title="РОП пока не привязан к аккаунтам"
            hint="Администратор BORIS выбирает, какие аккаунты Авито обслуживает ИИ-РОП, на экране «ИИ-сотрудники»."
            action={<Button onClick={() => { window.location.href = "/ai"; }}>
              Перейти к подключению
            </Button>} />
          )}
        </Page>
    );
  }

  return (
      <Page className="boris-rop-reference-page">
        <div className="boris-rop-reference-head" style={{ display: "flex", justifyContent: "space-between", gap: space.md, alignItems: "center", flexWrap: "wrap", marginBottom: space.lg }}>
          <div>
            <h1 style={{ ...font.h1, margin: 0 }}>ИИ-руководитель отдела продаж</h1>
            <div style={{ ...font.small, marginTop: 4 }}>
              Обслуживает {accounts.length} {accounts.length === 1 ? "аккаунт" : "аккаунта"} · данные за {days} дней
            </div>
          </div>
          <div style={{ display: "flex", gap: space.sm }}>
            <Button kind={days === 1 ? "secondary" : "ghost"} onClick={() => setDays(1)}>Сегодня</Button>
            <Button kind={days === 7 ? "secondary" : "ghost"} onClick={() => setDays(7)}>7 дней</Button>
            <Button kind={days === 30 ? "secondary" : "ghost"} onClick={() => setDays(30)}>30 дней</Button>
          </div>
        </div>
        <SectionGuide section="sales" />
        <div className="boris-rop-tabs" style={{ display: "flex", gap: space.sm, flexWrap: "wrap", marginBottom: space.lg }}>
          {([['summary','Сводка РОПа'],['calls','Звонки'],['chats','Переписки'],['reports','Отчёты'],['settings','Настройки']] as const).map(([k,label]) =>
            <Button key={k} kind={tab === k ? "secondary" : "ghost"} onClick={() => setTab(k)}>{label}</Button>)}
        </div>

      {note ? <Alert kind="danger">{note}</Alert> : null}

      <div data-boris-g32b-page="rop" style={{ display: "flex", gap: space.sm, flexWrap: "wrap", marginBottom: space.lg }}>
        {accounts.length > 1 ? (
          <Button kind={scope === "all" ? "secondary" : "ghost"} onClick={() => setScope("all")}>
            Оба аккаунта
          </Button>
        ) : null}
        {accounts.map((a) => (
          <Button key={a.account_id} kind={scope === a.account_id ? "secondary" : "ghost"}
                  onClick={() => setScope(a.account_id)}>
            {a.name}
          </Button>
        ))}
        {scope === "all" && accounts.length > 1 ? (
          <span style={{ marginLeft: space.md, display: "flex", gap: space.sm }}>
            <Button kind={mode === "by" ? "secondary" : "ghost"} onClick={() => setMode("by")}>
              По аккаунтам
            </Button>
            <Button kind={mode === "sum" ? "secondary" : "ghost"} onClick={() => setMode("sum")}>
              Сводка бизнеса
            </Button>
          </span>
        ) : null}
      </div>

      {tab === "summary" ? <>
        {shown.map((a) => { const x=summaries[a.account_id] || {}; const m=x.summary || {}; const ins=x.insights || {}; const cmp=x.comparison || {};
          return <Card key={a.account_id} style={{ marginBottom: space.lg }}>
            <div style={font.h3 as React.CSSProperties}>{a.name}</div>
            <KpiRow><Kpi value={m.calls_analyzed ?? 0} label="Звонков проанализировано"/><Kpi value={m.chats_analyzed ?? 0} label="Переписок проанализировано"/><Kpi value={m.analyzed ?? 0} label="Всего взаимодействий"/><Kpi value={m.avg_score ?? "—"} label="Средняя оценка"/><Kpi value={m.strong ?? 0} label="Сильных" kind="success"/><Kpi value={m.weak ?? 0} label="Слабых" kind={m.weak ? "danger":"neutral"}/></KpiRow>
            {cmp.score_delta != null ? <Alert kind="info">Динамика к предыдущему периоду: средняя оценка {cmp.score_delta > 0 ? "+" : ""}{cmp.score_delta}; взаимодействий {cmp.analyzed_delta > 0 ? "+" : ""}{cmp.analyzed_delta}.</Alert> : null}
            {!m.analyzed ? <EmptyState title="За выбранный период разборов нет" hint="BORIS покажет сводку автоматически после штатного анализа звонков или переписок."/> : <>
              <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(260px,1fr))", gap:space.md, marginTop:space.lg }}>
                <Insight title="Основные ошибки" items={ins.main_problems}/><Insight title="Точки роста" items={ins.growth_points}/><Insight title="Рекомендации" items={ins.recommendations}/><Insight title="Клиентские триггеры" items={ins.client_triggers}/><Insight title="Возражения" items={ins.objections}/><Insight title="Стоп-слова" items={ins.stop_words}/><Insight title="Сильные приёмы" items={ins.strong_methods}/>
              </div>
              <div style={{ ...font.h3, marginTop:space.lg }}>Требует внимания</div>
              {(x.attention || []).length ? (x.attention || []).slice(0,8).map((it:any)=><div key={it.type+it.id} style={{...font.small,padding:"7px 0",borderTop:"1px solid "+color.line}}><b style={{color:color.red}}>{it.score}/100</b> · {it.type === "call" ? "звонок" : "переписка"} · {String(it.created_at||"").slice(0,16).replace("T"," ")} {it.recommendation ? "· "+it.recommendation : ""}</div>) : <div style={font.small as React.CSSProperties}>Критичных взаимодействий нет.</div>}
            </>}
          </Card>})}
      </> : null}

      {tab === "chats" ? <Card><div style={font.h3 as React.CSSProperties}>Переписки</div>{shown.flatMap(a => (chatReviews[a.account_id]||[]).map((z:any)=>({a,z}))).length ? shown.flatMap(a => (chatReviews[a.account_id]||[]).map((z:any)=>({a,z}))).map(({a,z}:any)=><details key={a.account_id+z.chat_id} style={{borderTop:"1px solid "+color.line,padding:"10px 0"}}><summary style={{cursor:"pointer",...font.body}}><b>{z["балл"] ?? "—"}/100</b> · {String(z["когда"]||"").slice(0,16).replace("T"," ")} · {a.name} {z["клиент"] ? "· "+z["клиент"] : ""}</summary><div style={{...font.small,marginTop:space.sm}}><b>Результат:</b> {z["результат"] || "Не указан"}<br/><b>Рекомендация:</b> {z["совет"] || "—"}<br/><b>Точки роста:</b> {(z["зоны_роста"]||[]).join(" · ") || "—"}<br/><b>Чек-лист:</b> {(z["чеклист"]||[]).map((i:any)=>(i.done?"✓ ":"✗ ")+i.item).join(" · ") || "—"}</div></details>) : <EmptyState title="За выбранный период разборов переписок нет" hint="Показываются только реальные сохранённые разборов переписок."/>}</Card> : null}

      {tab === "reports" ? <>{shown.map(a => <Card key={a.account_id} style={{marginBottom:space.md}}><div style={font.h3 as React.CSSProperties}>{a.name} · Отчёты</div><div style={{...font.small,marginBottom:space.md}}>PDF создаются только по явному действию. Сохранённые отчёты можно скачать повторно без нового анализа.</div><div style={{display:"flex",gap:space.sm,flexWrap:"wrap",marginBottom:space.md}}><Button onClick={()=>{const r=(reports[a.account_id]||[]).find((x:any)=>x.kind!=="chats"); if(r) downloadReport(a.account_id,r); else setActionNote(p=>({...p,[a.account_id]:"Готового отчёта по звонкам пока нет. Новый PDF формируется только по явному запросу через штатный механизм отчётов."}));}}>Скачать отчёт по звонкам</Button><Button kind="secondary" onClick={()=>{const r=(reports[a.account_id]||[]).find((x:any)=>x.kind==="chats"); if(r) downloadReport(a.account_id,r); else setActionNote(p=>({...p,[a.account_id]:"Готового отчёта по перепискам пока нет. Новый PDF формируется только по явному запросу через штатный механизм отчётов."}));}}>Скачать отчёт по перепискам</Button></div>{(reports[a.account_id]||[]).map((r:any)=><div key={r.id} style={{display:"flex",gap:space.sm,alignItems:"center",padding:"7px 0",borderTop:"1px solid "+color.line}}><span style={{flex:1,...font.small}}>{r.kind === "chats" ? "Переписки" : "Звонки"} · {r.date_from} — {r.date_to} · {r.size_kb} КБ</span><Button kind="ghost" onClick={()=>downloadReport(a.account_id,r)}>Скачать PDF</Button></div>)}</Card>)}</> : null}

      {tab === "settings" ? <>{shown.map(a => {const l=limits[a.account_id]||{}; const x=summaries[a.account_id]||{}; return <Card key={a.account_id} style={{marginBottom:space.md}}><div style={font.h3 as React.CSSProperties}>{a.name}</div><div style={{...font.small,lineHeight:1.8}}>РОП: <b>включён</b><br/>Автоматизация: <b>{l.period?.active ? "активна" : "период неактивен"}</b><br/>Анализируется: звонки и сохранённые переписки<br/>Последний успешный анализ: <b>{[...(x.calls||[]),...(x.chats||[])].map((v:any)=>v.created_at).filter(Boolean).sort().reverse()[0]?.slice(0,16).replace("T"," ") || "нет данных"}</b><br/>Последний отчёт: <b>{reports[a.account_id]?.[0]?.created_at || "нет отчётов"}</b></div>{(reviews[a.account_id]||[]).length || (chatReviews[a.account_id]||[]).length ? <div style={{marginTop:space.md}}><Button kind="secondary" onClick={()=>previewTraining(a.account_id)} disabled={actionBusy===a.account_id+":preview"}>Предпросмотр выводов для МОП</Button>{trainPreview[a.account_id] ? <div style={{marginTop:space.md}}><Alert kind="info"><b>Что РОП предлагает изменить</b><div style={{marginTop:space.sm}}><b>Проблема:</b> {trainPreview[a.account_id].problem}</div><div><b>Основание:</b> {trainPreview[a.account_id].evidence}</div><div style={{marginTop:space.sm}}><b>Сейчас МОП работает так:</b><div style={{whiteSpace:"pre-wrap"}}>{trainPreview[a.account_id].current_rule || "Отдельного правила РОП пока нет."}</div></div><div style={{marginTop:space.sm}}><b>После изменения:</b><div style={{whiteSpace:"pre-wrap"}}>{trainPreview[a.account_id].rules}</div></div><div style={{marginTop:space.sm}}>Изменится только инструкция МОП этого аккаунта. Применяется именно показанный текст без повторной ИИ-генерации.</div></Alert><Button onClick={()=>trainManager(a.account_id)} disabled={actionBusy===a.account_id+":train"}>Применить это изменение</Button></div>:null}{(changeLifecycle[a.account_id]||[]).length ? <div style={{marginTop:space.lg}}><div style={font.h3 as React.CSSProperties}>История изменений РОП → МОП</div>{(changeLifecycle[a.account_id]||[]).slice(0,5).map((c:any)=><div key={c.change_id} style={{borderTop:"1px solid "+color.line,padding:"10px 0",...font.small}}><b>{c.state === "OBSERVING" ? "Наблюдаем результат" : c.state === "MEASURED" ? (c.effect === "IMPROVED" ? "Стало лучше" : c.effect === "WORSE" ? "Стало хуже" : c.effect === "NO_CLEAR_EFFECT" ? "Пока без заметного эффекта" : "Нужно больше данных") : c.state === "ROLLED_BACK" ? "Изменение возвращено" : "Рекомендация подготовлена"}</b><div>{c.problem}</div>{c.state === "OBSERVING" ? <div>Новых взаимодействий: {Math.max(c.after_metrics?.interactions||0,c.after_metrics?.inquiries||0)} из необходимых {c.needed_sample||"—"}</div> : null}{c.state === "MEASURED" ? <div>До: оценка {c.before_metrics?.avg_score ?? "—"}, телефон {c.before_metrics?.phone_rate ?? "—"}% · После: оценка {c.after_metrics?.avg_score ?? "—"}, телефон {c.after_metrics?.phone_rate ?? "—"}%</div>:null}{c.effect === "IMPROVED" ? <div><b>Решение:</b> сохранить правило.</div> : c.effect === "NO_CLEAR_EFFECT" ? <div><b>Решение:</b> продолжить наблюдение или проверить следующую рекомендацию.</div> : c.effect === "INSUFFICIENT_DATA" ? <div><b>Решение:</b> продолжить наблюдение — данных пока недостаточно.</div> : null}{c.effect === "WORSE" && c.state !== "ROLLED_BACK" ? <Button kind="ghost" onClick={()=>rollbackChange(a.account_id,c.change_id)} disabled={actionBusy===a.account_id+":rollback:"+c.change_id}>Вернуть предыдущее правило</Button>:null}</div>)}</div>:null}{actionNote[a.account_id]?<Alert kind="success">{actionNote[a.account_id]}</Alert>:null}</div>:null}</Card>})}</> : null}

      {tab === "calls" && (scope === "all" && accounts.length > 1 && mode === "sum" ? (
        <>
          <Alert kind="info">
            <b>Режим: сводка бизнеса.</b> Показатели {shown.length} аккаунтов сложены вместе.
            Ниже — вклад каждого; нажмите на строку, чтобы открыть аккаунт отдельно.
          </Alert>
          <KpiRow>
            <Kpi value={sum.total} label="звонков всего" />
            <Kpi value={sum.answered} label="отвечено" kind="success" />
            <Kpi value={sum.missed} label="пропущено" kind={sum.missed ? "danger" : "neutral"} />
            <Kpi value={sumPct + "%"} label="доля отвеченных" kind="info" />
            <Kpi value={sum.minutes} label="минут в пакете" />
            <NpsKpi />
          </KpiRow>
          <div style={{ marginTop: space.lg }}>
            <Table head={["Аккаунт", "Звонков", "Отвечено", "Пропущено", "Доля"]}
                   onRowClick={(i) => { const a = shown[i]; if (a) { setScope(a.account_id); setMode("by"); } }}
                   rows={shown.map((a) => {
                     const s = stats[a.account_id] || {};
                     return [
                       <b key="n" style={{ color: color.heading }}>{a.name}</b>,
                       s.total ?? "—", s.answered ?? "—", s.missed ?? "—",
                       (s.answered_pct ?? 0) + "%",
                     ];
                   })} />
          </div>
        </>
      ) : (
        <div style={{ display: "flex", gap: space.lg, flexWrap: "wrap" }}>
          {shown.map((a) => {
            const s = stats[a.account_id] || {};
            const l = limits[a.account_id] || {};
            const rp = reports[a.account_id] || [];
            const bad = s.status && s.status !== "ok";
            return (
              <Card key={a.account_id} style={{ flex: "1 1 420px", minWidth: "min(320px, 100%)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: space.sm }}>
                  <div className="boris-f3-0070778df76ac3">
                    <div style={font.h3 as React.CSSProperties}>{a.name}</div>
                    <div style={{ ...font.small, marginTop: 2 }}>
                      Подключённый аккаунт Avito
                    </div>
                  </div>
                  <Badge kind={a.memory_mode === "strict" ? "success" : "neutral"}>
                    память {a.memory_mode}
                  </Badge>
                  <Badge kind="info">РОП включён</Badge>
                </div>

                {bad ? (
                  <div style={{ marginTop: space.md }}>
                    <Alert kind="warning">
                      {s.message || "Данные звонков недоступны для этого аккаунта"}
                    </Alert>
                  </div>
                ) : (
                  <div style={{ display: "flex", gap: space.xl, flexWrap: "wrap",
                                marginTop: space.lg }}>
                    <Metric v={s.total ?? "—"} l="звонков" />
                    <Metric v={s.answered ?? "—"} l="отвечено" c={color.green} />
                    <Metric v={s.missed ?? "—"} l="пропущено"
                            c={s.missed ? color.red : undefined} />
                    <Metric v={(s.answered_pct ?? 0) + "%"} l="доля отвеченных" c={color.blue} />
                  </div>
                )}

                <div style={{ height: 1, background: color.line, margin: `${space.lg}px 0` }} />

                <div style={{ display: "flex", gap: space.xl, flexWrap: "wrap", ...font.small }}>
                  <span>минут в пакете: <b style={{ color: color.heading }}>{num(l.minutes)}</b></span>
                  <span>отчётов по звонкам: <b style={{ color: color.heading }}>{num(l.reports_calls)}</b></span>
                  <span>по перепискам: <b style={{ color: color.heading }}>{num(l.reports_chats)}</b></span>
                  <span>разборов чатов: <b style={{ color: color.heading }}>{num(l.chats)}</b></span>
                </div>

                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                              gap: space.sm, marginTop: space.lg, marginBottom: space.sm }}>
                  <div style={font.bodyStrong as React.CSSProperties}>Отчёты этого аккаунта</div>
                  {(reviews[a.account_id] || []).length ? (
                    <Button kind="secondary" disabled={actionBusy === a.account_id + ":train"}
                            onClick={() => trainManager(a.account_id)}>
                      {actionBusy === a.account_id + ":train" ? "Обновляю МОП…" : "Передать выводы МОП"}
                    </Button>
                  ) : null}
                </div>
                {actionNote[a.account_id] ? (
                  <Alert kind={actionNote[a.account_id].startsWith("МОП обновлён") ? "success" : "warning"}>
                    {actionNote[a.account_id]}
                  </Alert>
                ) : null}
                {rp.length ? rp.slice(0, 5).map((r: any, i: number) => {
                  const reportKey = a.account_id + ":report:" + String(r.id ?? "");
                  return (
                    <div key={i} style={{ display: "flex", gap: space.sm, alignItems: "center",
                                          padding: "6px 0", ...font.small }}>
                      <Icon name="file" size={15} tone="muted" />
                      <span className="boris-f3-19024b772a87a5" style={{ flex: 1 }}>
                        {r.title || r.name || ("Отчёт #" + (r.id ?? i))}
                      </span>
                      <span>{r.created_at || r.date || ""}</span>
                      <Button kind="ghost" disabled={actionBusy === reportKey}
                              onClick={() => downloadReport(a.account_id, r)}>
                        {actionBusy === reportKey ? "Скачиваю…" : "Скачать PDF"}
                      </Button>
                    </div>
                  );
                }) : (
                  <div style={font.small as React.CSSProperties}>Отчётов пока нет</div>
                )}
              </Card>
            );
          })}
        </div>
      ))}
      {tab === "calls" && shown.some((a) => (reviews[a.account_id] || []).length) ? (
        <Card style={{ marginTop: space.lg }}>
          <div style={font.h3 as React.CSSProperties}>Разборы звонков</div>
          {shown.map((a) => (reviews[a.account_id] || []).map((z: any) => {
            const key = a.account_id + ":" + z.call_id;
            const weak = (z["балл"] || 0) < 60;
            return (
              <div key={key} style={{ borderTop: "1px solid " + color.line, padding: "10px 0" }}>
                <div onClick={() => setOpenCall(openCall === key ? "" : key)}
                     style={{ display: "flex", gap: space.md, alignItems: "center", cursor: "pointer" }}>
                  <span style={{ ...font.body, color: weak ? color.red : color.green, fontWeight: 700,
                                 minWidth: 44 }}>{z["балл"]}</span>
                  <span style={{ ...font.small, minWidth: 92 }}>
                    {z["выполнено"]} из {z["всего_пунктов"]}
                  </span>
                  <span style={{ ...font.small, flex: 1 }}>
                    {String(z["когда"] || "").slice(0, 16).replace("T", " ")} · {z["минут"]} мин
                    {z["клиент"] ? " · " + z["клиент"] : ""}
                  </span>
                  <span style={font.small}>{openCall === key ? "свернуть" : "подробнее"}</span>
                </div>
                {openCall === key ? (
                  <div style={{ marginTop: space.sm, paddingLeft: 44 }}>
                    {(z["чеклист"] || []).map((it: any, k: number) => (
                      <div key={k} style={{ ...font.small,
                                            color: it.done ? color.green : color.red }}>
                        {it.done ? "\u2713" : "\u2717"} {it.item}
                      </div>
                    ))}
                    {z["совет"] ? (
                      <div style={{ ...font.small, marginTop: space.sm, color: color.heading }}>
                        Совет: {z["совет"]}
                      </div>
                    ) : null}
                    {(z["зоны_роста"] || []).length ? (
                      <div style={{ ...font.small, marginTop: 4 }}>
                        Зоны роста: {(z["зоны_роста"] || []).join(" · ")}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          }))}
        </Card>
      ) : null}
      </Page>
  );
}

/** Лимиты приходят объектами вида {purchased, used, left} — берём осмысленное число. */
function num(v: any): React.ReactNode {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return v;
  if (typeof v === "string") return v;
  if (typeof v === "object") {
    const o = v as Record<string, any>;
    const left = o.left ?? o.remaining ?? o.available;
    const total = o.purchased ?? o.total ?? o.limit;
    if (left !== undefined && total !== undefined) return `${left} из ${total}`;
    if (left !== undefined) return left;
    if (total !== undefined) return total;
    const first = Object.values(o).find((x) => typeof x === "number");
    return first === undefined ? "—" : (first as number);
  }
  return "—";
}

/** То же, но всегда числом — для сложения в сводке. */
function toNum(v: any): number {
  if (typeof v === "number") return v;
  if (v && typeof v === "object") {
    const o = v as Record<string, any>;
    const c = o.left ?? o.remaining ?? o.available ?? o.purchased ?? o.total;
    if (typeof c === "number") return c;
  }
  return 0;
}

function Metric({ v, l, c }: { v: React.ReactNode; l: string; c?: string }) {
  return (
    <div>
      <div style={{ fontSize: 24, fontWeight: 700, color: c || color.heading }}>{v}</div>
      <div style={font.small as React.CSSProperties}>{l}</div>
    </div>
  );
}

function Insight({title,items}:{title:string;items:any[]}) { return <div style={{border:"1px solid "+color.line,borderRadius:12,padding:space.md}}><div style={font.bodyStrong as React.CSSProperties}>{title}</div>{(items||[]).length ? (items||[]).map((x:any,i:number)=><div key={i} style={{...font.small,marginTop:6}}>{x.text}{x.count>1 ? ` · ${x.count}×` : ""}</div>) : <div style={{...font.small,marginTop:6}}>Недостаточно данных</div>}</div>; }
