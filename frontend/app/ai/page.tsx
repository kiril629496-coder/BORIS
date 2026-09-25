"use client";

import { useEffect, useState } from "react";
import {
  Page, Card, Button, Choice, Badge, Alert,
} from "../ui";
import { color, space, font } from "../ui/tokens";
import AppShell from "../components/shell/AppShell";

type Acc = {
  account_id: string; name: string; avito_user_id: string; bound: boolean;
  memory_shared: boolean; group_key: string; memory_mode: string;
};
type Panel = { status: string; product: string; account_id: string; name: string; enabled: boolean; memory_shared: boolean; mop?: any; rop?: any; };
type Lic = { qty: number; active: boolean; paid_until: string; unlimited: boolean };

const PRODUCTS = [
  { key: "mop", title: "ИИ-менеджер по продажам", hint: "отвечает клиентам в переписке" },
  { key: "rop", title: "ИИ-руководитель отдела продаж", hint: "контролирует качество и обучает" },
];
const MODES: [string, string][] = [
  ["one", "Один аккаунт"],
  ["selected", "Несколько выбранных"],
  ["all", "Все аккаунты клиента"],
];

function api(path: string, init?: RequestInit) {
  const token =
    typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return fetch(path, {
    ...(init || {}),
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + token,
      ...((init && (init.headers as Record<string, string>)) || {}),
    },
  });
}

export default function AiSetupPage() {
  const [product, setProduct] = useState("rop");
  // Режим ответов МОПа по каждому аккаунту: аренда и продажа
  // могут работать по-разному.
  const [mopModes, setMopModes] = useState<any>({});
  const [accs, setAccs] = useState<Acc[]>([]);
  const [panels, setPanels] = useState<Record<string, Panel>>({});
  const [lic, setLic] = useState<Record<string, Lic>>({});
  const [owner, setOwner] = useState(false);
  const [mode, setMode] = useState("selected");
  const [picked, setPicked] = useState<string[]>([]);
  const [shared, setShared] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState(false);
  const [mopSettings, setMopSettings] = useState<Record<string, any>>({});
  const [editingSettings, setEditingSettings] = useState("");
  const [actionBusy, setActionBusy] = useState("");
  const [ropExpanded, setRopExpanded] = useState<Record<string, boolean>>({});
  const [ropPreview, setRopPreview] = useState<Record<string, any>>({});
  const [changeLifecycle, setChangeLifecycle] = useState<Record<string, any[]>>({});

  useEffect(() => { load(product); }, [product]);
  const panelIds = mode === "all" ? accs.map((a) => a.account_id) : picked;

  useEffect(() => {
    let cancelled = false;
    setPanels({}); // switching account must never show stale data from the previous account
    if (!panelIds.length) return () => { cancelled = true; };
    Promise.all(panelIds.map(async (accountId) => {
      const r = await api(`/api/ai/account_panel?product=${encodeURIComponent(product)}&account_id=${encodeURIComponent(accountId)}`);
      if (!r.ok) return [accountId, null] as const;
      return [accountId, await r.json()] as const;
    })).then((pairs) => { if (!cancelled) { const m: Record<string, Panel> = {}; pairs.forEach(([k,v]) => { if (v) m[k]=v; }); setPanels(m); } });
    return () => { cancelled = true; };
  }, [product, mode, picked.join("|"), accs.map((a) => a.account_id).join("|")]);

  useEffect(() => {
    let cancelled=false; setChangeLifecycle({});
    if (!panelIds.length) return () => { cancelled=true; };
    Promise.all(panelIds.map(async(accountId)=>{const r=await api(`/api/calltracking/mop_change_lifecycle?account_id=${encodeURIComponent(accountId)}`); return [accountId,r.ok?await r.json():null] as const;})).then(rows=>{if(cancelled)return;const x:Record<string,any[]>={};rows.forEach(([id,d])=>{if(d?.status==="ok")x[id]=d.items||[]});setChangeLifecycle(x)}).catch(()=>{});
    return ()=>{cancelled=true};
  }, [product, mode, picked.join("|"), accs.map((a) => a.account_id).join("|")]);

  useEffect(() => {
    let cancelled = false;
    setMopModes({});
    setMopSettings({});
    setEditingSettings("");
    if (product !== "mop" || !panelIds.length) return () => { cancelled = true; };
    Promise.all(panelIds.map(async (accountId) => {
      const [modeResponse, settingsResponse] = await Promise.all([
        api("/api/ai/mop_mode?account_id=" + encodeURIComponent(accountId)),
        api("/api/messenger/sales/settings?account_id=" + encodeURIComponent(accountId)),
      ]);
      return [accountId,
        modeResponse.ok ? await modeResponse.json() : null,
        settingsResponse.ok ? await settingsResponse.json() : null] as const;
    })).then((rows) => {
      if (cancelled) return;
      const modes: any = {}; const settings: any = {};
      rows.forEach(([id, modeData, settingsData]) => {
        if (modeData?.status === "ok") modes[id] = modeData;
        if (settingsData?.status === "ok") settings[id] = settingsData;
      });
      setMopModes(modes); setMopSettings(settings);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [product, mode, picked.join("|"), accs.map((a) => a.account_id).join("|")]);

  async function load(p: string) {
    try {
      const [a, l] = await Promise.all([
        api("/api/ai/accounts?product=" + p),
        api("/api/ai/licenses"),
      ]);
      if (a.ok) {
        const d = await a.json();
        const list: Acc[] = d.accounts || [];
        setAccs(list);
        const bound = list.filter((x) => x.bound).map((x) => x.account_id);
        setPicked(bound);
        if (bound.length) setShared(list.some((x) => x.bound && x.memory_shared));
        if (bound.length === 1) setMode("one");
        else if (bound.length && bound.length === list.length) setMode("all");
        else if (bound.length) setMode("selected");
      }
      if (l.ok) { const d = await l.json(); setLic(d.licenses || {}); setOwner(!!d.owner); }
    } catch { setErr(true); setNote("Не удалось загрузить аккаунты"); }
  }

  async function save() {
    setBusy(true); setNote(""); setErr(false);
    try {
      const r = await api("/api/ai/bind", {
        method: "POST",
        body: JSON.stringify({
          product, mode, account_ids: mode === "all" ? [] : picked, memory_shared: shared,
        }),
      });
      const d = await r.json().catch(() => ({}));
      setErr(!r.ok);
      setNote(d.message || d.detail || "Готово");
      await load(product);
    } catch { setErr(true); setNote("Ошибка сети"); }
    setBusy(false);
  }

  async function refreshPanel(accountId: string) {
    const r = await api(`/api/ai/account_panel?product=${encodeURIComponent(product)}&account_id=${encodeURIComponent(accountId)}`);
    if (!r.ok) return;
    const d = await r.json();
    setPanels((prev) => ({ ...prev, [accountId]: d }));
  }

  async function reloadMopSettings(accountId: string) {
    const r = await api(`/api/messenger/sales/settings?account_id=${encodeURIComponent(accountId)}`);
    if (!r.ok) return;
    const d = await r.json();
    if (d.status === "ok") setMopSettings((prev) => ({ ...prev, [accountId]: d }));
  }

  async function saveMopSettings(accountId: string, next: any, success = "Настройки МОП сохранены") {
    const key = `settings:${accountId}`;
    setActionBusy(key); setNote(""); setErr(false);
    try {
      const r = await api(`/api/messenger/sales/settings?account_id=${encodeURIComponent(accountId)}`, {
        method: "POST", body: JSON.stringify(next),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.status !== "ok") throw new Error(d.detail || d.message || "Не удалось сохранить настройки МОП");
      await Promise.all([reloadMopSettings(accountId), refreshPanel(accountId)]);
      setNote(success);
      setEditingSettings("");
    } catch (e: any) { setErr(true); setNote(e?.message || "Не удалось сохранить настройки МОП"); }
    finally { setActionBusy(""); }
  }

  async function toggleMop(accountId: string, enabled: boolean) {
    const current = mopSettings[accountId];
    if (!current) return;
    if (!enabled && !window.confirm("Выключить МОП для этого аккаунта? Новые диалоги будут передаваться человеку.")) return;
    await saveMopSettings(accountId, { ...current, enabled }, enabled ? "МОП включён для этого аккаунта" : "МОП выключен для этого аккаунта");
  }


  async function editRule(accountId: string, rule: any) {
    const value = window.prompt("Исправить правило МОП", String(rule?.text || ""));
    if (value == null || !value.trim()) return;
    const key = `rule:${accountId}:${rule.id}`; setActionBusy(key); setNote(""); setErr(false);
    try {
      const r = await api("/api/memory/edit", { method: "POST", body: JSON.stringify({ account_id: accountId, fact_id: rule.id, value: value.trim() }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.status !== "ok") throw new Error(d.detail || d.message || "Не удалось исправить правило");
      await refreshPanel(accountId); setNote("Правило МОП исправлено");
    } catch (e: any) { setErr(true); setNote(e?.message || "Не удалось исправить правило"); }
    finally { setActionBusy(""); }
  }

  async function rejectRule(accountId: string, rule: any) {
    if (!window.confirm("Отключить это правило? МОП больше не будет использовать его как подтверждённое.")) return;
    const key = `rule:${accountId}:${rule.id}`; setActionBusy(key); setNote(""); setErr(false);
    try {
      const r = await api("/api/memory/reject", { method: "POST", body: JSON.stringify({ account_id: accountId, fact_id: rule.id }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.status !== "ok") throw new Error(d.detail || d.message || "Не удалось отключить правило");
      await refreshPanel(accountId); setNote("Правило отключено");
    } catch (e: any) { setErr(true); setNote(e?.message || "Не удалось отключить правило"); }
    finally { setActionBusy(""); }
  }

  async function prepareRopPreview(accountId: string) {
    const key = `rop-preview:${accountId}`; setActionBusy(key); setNote(""); setErr(false);
    try {
      const r = await api(`/api/calltracking/train_manager?account_id=${encodeURIComponent(accountId)}`, { method: "POST", body: JSON.stringify({ apply: false }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.status !== "ok") throw new Error(d.detail || d.message || "Рекомендации пока недоступны");
      setRopPreview((prev) => ({ ...prev, [accountId]: d }));
    } catch (e: any) { setErr(true); setNote(e?.message || "Не удалось подготовить рекомендации"); }
    finally { setActionBusy(""); }
  }

  async function applyRopPreview(accountId: string) {
    const preview = ropPreview[accountId];
    if (!preview?.rules) return;
    if (!window.confirm("Передать МОП именно показанные правила?")) return;
    const key = `rop-apply:${accountId}`; setActionBusy(key); setNote(""); setErr(false);
    try {
      const r = await api(`/api/calltracking/train_manager?account_id=${encodeURIComponent(accountId)}`, { method: "POST", body: JSON.stringify({ apply: true, rules: preview.rules, preview_id: preview.preview_id }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.status !== "ok" || !d.used_preview) throw new Error(d.detail || d.message || "Не удалось передать показанные правила МОП");
      setRopPreview((prev) => ({ ...prev, [accountId]: null }));
      await refreshPanel(accountId); setNote("Показанные рекомендации переданы МОП без повторной генерации");
    } catch (e: any) { setErr(true); setNote(e?.message || "Не удалось передать рекомендации МОП"); }
    finally { setActionBusy(""); }
  }

  function go(path: string) { window.location.assign(path); }

  const cur = PRODUCTS.find((p) => p.key === product);
  const myLic = lic[product];
  const limit = owner ? "без ограничений" :
    myLic && myLic.active ? `оплачено ${myLic.qty} шт. до ${myLic.paid_until}` : "не оплачено";

  return (
    <AppShell
      active="aisetup"
      title="ИИ-сотрудники"
      subtitle="Виртуальный отдел продаж: менеджер и руководитель работают по выбранным аккаунтам."
    >
    <Page>
      <section className="boris-reference-page-head boris-ai-reference-head">
        <div>
          <div className="boris-reference-kicker">ВИРТУАЛЬНЫЙ ОТДЕЛ ПРОДАЖ</div>
          <h1>ИИ-сотрудники BORIS</h1>
          <p>Подключайте менеджера и руководителя к нужным аккаунтам, контролируйте результат и обучение в одном рабочем окне.</p>
        </div>
        <div className="boris-ai-head-status"><small>Сейчас открыт</small><b>{cur?.title || "ИИ-сотрудник"}</b><span>{limit}</span></div>
      </section>
      <nav className="boris-ai-product-tabs" aria-label="Роль ИИ-сотрудника">
        {PRODUCTS.map((item) => <button key={item.key} type="button" className={product === item.key ? "is-active" : ""} onClick={() => setProduct(item.key)}><b>{item.title}</b><span>{item.hint}</span></button>)}
      </nav>

      {note ? <Alert kind={err ? "danger" : "success"}>{note}</Alert> : null}

      <div data-boris-g33="ai" className="boris-ai-reference-workspace" style={{ display: "flex", gap: space.lg, alignItems: "flex-start", flexWrap: "wrap" }}>
        <Card className="boris-ai-config-card" style={{ flex: "0 1 360px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: space.md }}>
            <div style={{ flex: 1 }}>
              <div style={font.h3 as React.CSSProperties}>{cur ? cur.title : ""}</div>
              <div style={{ ...font.small, marginTop: 3 }}>{cur ? cur.hint : ""}</div>
            </div>
            <Badge kind={owner ? "info" : myLic && myLic.active ? "success" : "warning"}>
              {limit}
            </Badge>
          </div>

          <div style={{ ...font.bodyStrong, marginTop: space.xl, marginBottom: space.md }}>
            Какие аккаунты обслуживать
          </div>
          {MODES.map(([k, label]) => (
            <Choice key={k} type="radio" checked={mode === k}
                    onToggle={() => { setMode(k); if (k === "one") setPicked((p) => p.length ? [p[0]] : []); }} title={label} />
          ))}

          {mode !== "all" ? (
            <div style={{ marginTop: space.lg }}>
              <div style={{ ...font.bodyStrong, marginBottom: space.md }}>
                Аккаунты ({picked.length} из {accs.length})
              </div>
              {accs.map((a) => (
                <Choice key={a.account_id} type={mode === "one" ? "radio" : "checkbox"}
                        checked={picked.includes(a.account_id)}
                        onToggle={() => mode === "one"
                          ? setPicked([a.account_id])
                          : setPicked((p) => p.includes(a.account_id)
                              ? p.filter((x) => x !== a.account_id)
                              : [...p, a.account_id])}
                        title={a.name}
                        hint={a.bound ? "AI подключён" : "Можно подключить"} />
              ))}
            </div>
          ) : null}

          {(mode === "selected" && picked.length > 1) || (mode === "all" && accs.length > 1) ? (
          <div style={{ marginTop: space.lg }}>
            <Choice checked={shared} onToggle={() => setShared(!shared)}
                    title="Общая память для выбранных аккаунтов"
                    hint="Включается только явно. Каждый факт всё равно хранит исходный аккаунт." />
          </div>
          ) : null}

          <Button onClick={save} disabled={busy || (mode !== "all" && !picked.length)}
                  style={{ marginTop: space.lg }}>
            Сохранить подключение
          </Button>
        </Card>

        <div style={{ flex: "1 1 640px", minWidth: 0 }}>
          {panelIds.length ? panelIds.map((accountId) => {
            const p = panels[accountId];
            const a = accs.find((x) => x.account_id === accountId);
            if (!p) return <Card key={accountId}><div style={font.h3 as React.CSSProperties}>{a?.name || "Аккаунт"}</div><div style={{...font.small,marginTop:space.sm}}>Загружаю результат выбранного аккаунта…</div></Card>;
            const m = p.mop; const r = p.rop; const mm = mopModes[accountId]; const settings = mopSettings[accountId];
            const action = ownerAction(product, p); const health = businessHealth(product, p);
            const runPrimary = () => {
              if (action.kind === "enable_mop") void toggleMop(accountId, true);
              else if (action.kind === "connect_rop") { setMode("one"); setPicked([accountId]); }
              else if (action.kind === "settings") setEditingSettings(accountId);
              else if (action.kind === "training") go(`/messages/mop-training?account_id=${encodeURIComponent(accountId)}`);
              else if (action.kind === "attention") go(`/messages?account_id=${encodeURIComponent(accountId)}&attention=1`);
              else if (action.kind === "recommendations") setRopExpanded((x) => ({...x,[accountId]:true}));
              else if (action.kind === "billing") go("/dashboard/billing");
            };
            return <Card className="boris-ai-account-card" key={accountId} style={{ marginBottom: space.lg }}>
              <div style={{display:"flex",justifyContent:"space-between",gap:space.md,alignItems:"flex-start",flexWrap:"wrap"}}>
                <div><div style={font.h3 as React.CSSProperties}>{p.name}</div><div style={{...font.small,marginTop:3}}>{product === "mop" ? `МОП: ${m?.state || "нет данных"}` : `РОП: ${r?.state || "нет данных"}`}</div></div>
                <Badge kind={health.kind === "good" ? "success" : health.kind === "unset" ? "neutral" : "warning"}>{health.label}</Badge>
              </div>
              <div style={{...font.small,marginTop:6}}>{health.reason}</div>

              <div style={{marginTop:space.md,padding:space.md,border:`1px solid ${color.line}`,borderRadius:12,background:"#f8fafc"}}>
                <div style={font.bodyStrong as React.CSSProperties}>Что сделать сейчас</div>
                <div style={{...font.body,marginTop:5,color:color.heading,fontWeight:800}}>{action.title}</div>
                <div style={{...font.small,marginTop:4}}>{action.hint}</div>
                {action.expected ? <div style={{...font.small,marginTop:5}}><b>Ожидаемый результат:</b> {action.expected}</div> : null}
                {action.kind !== "none" ? <Button onClick={runPrimary} disabled={!!actionBusy} style={{marginTop:space.sm}}>{action.cta || action.title}</Button> : null}
              </div>

              {product === "mop" && m ? <>
                <div style={{marginTop:space.lg}}><div style={font.bodyStrong as React.CSSProperties}>Главная цель МОП</div><div style={{...font.body,marginTop:5}}>{m.goal || "Не задана"}</div></div>
                <div style={{...font.bodyStrong,marginTop:space.lg}}>Результат сегодня</div>
                <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(125px,1fr))",gap:8,marginTop:8}}>
                  <Metric label="Диалогов" value={m.dialogs_today} />
                  <Metric label="Продолжили общение" value={m.continued_today} />
                  <Metric label="Квалифицировано" value={m.qualified_today} unavailable={m.qualified_today == null} />
                  <Metric label="Получено телефонов" value={m.contacts_today} />
                  <Metric label="Целевых действий" value={m.target_actions} unavailable={m.target_actions == null} />
                  <Metric label="Передано человеку" value={m.handed_to_human_today} />
                  <Metric label="Требуют внимания" value={m.attention_now} />
                </div>
                <div style={{marginTop:space.md,padding:10,borderRadius:10,background:"#f8fafc",...font.small}}><b>Как справляется:</b> {mopPerformanceText(m)}</div>

                <div style={{display:"flex",gap:7,flexWrap:"wrap",marginTop:space.md}}>
                  <Button kind="secondary" onClick={()=>setEditingSettings(editingSettings===accountId?"":accountId)}>Изменить цель и график</Button>
                  <Button kind="ghost" onClick={()=>go(`/messages/mop-training?account_id=${encodeURIComponent(accountId)}`)}>Обучить МОП</Button>
                  <Button kind="ghost" onClick={()=>go(`/messages?account_id=${encodeURIComponent(accountId)}`)}>Открыть очередь клиентов</Button>
                  {m.owner_enabled ? <Button kind="ghost" onClick={()=>void toggleMop(accountId,false)} disabled={!!actionBusy}>Выключить МОП</Button> : (m.package_active ? <Button kind="ghost" onClick={()=>void toggleMop(accountId,true)} disabled={!!actionBusy}>Включить МОП</Button> : null)}
                </div>

                {editingSettings===accountId && settings ? <div style={{marginTop:space.md,padding:space.md,border:`1px solid ${color.line}`,borderRadius:12}}>
                  <div style={font.bodyStrong as React.CSSProperties}>Цель и рабочий график</div>
                  <select value={settings.dialog_goal || "other"} onChange={(e)=>setMopSettings((x)=>({...x,[accountId]:{...settings,dialog_goal:e.target.value}}))} style={fieldStyle}>
                    <option value="phone">Получить телефон</option><option value="measurement">Записать на замер</option><option value="calculation">Договориться о расчёте</option><option value="call">Назначить звонок</option><option value="parameters">Получить параметры заказа</option><option value="meeting">Довести до встречи</option><option value="lead">Получить заявку</option><option value="handoff">Передать менеджеру</option><option value="other">Своя цель</option>
                  </select>
                  <textarea value={settings.dialog_goal_text || ""} onChange={(e)=>setMopSettings((x)=>({...x,[accountId]:{...settings,dialog_goal_text:e.target.value}}))} rows={3} placeholder="Что именно МОП должен получить от клиента" style={{...fieldStyle,resize:"vertical"}} />
                  <label style={{...font.small,display:"flex",gap:7,alignItems:"center",marginTop:8}}><input type="checkbox" checked={!!settings.work_schedule?.enabled} onChange={(e)=>setMopSettings((x)=>({...x,[accountId]:{...settings,work_schedule:{...(settings.work_schedule||{}),enabled:e.target.checked}}}))}/>Работать только по графику</label>
                  {settings.work_schedule?.enabled ? <>
                    <div style={{display:"flex",gap:5,flexWrap:"wrap",marginTop:8}}>{["Пн","Вт","Ср","Чт","Пт","Сб","Вс"].map((day,i)=>{const days:number[]=settings.work_schedule?.days||[];const on=days.includes(i);return <Button key={day} kind={on?"secondary":"ghost"} onClick={()=>setMopSettings((x)=>({...x,[accountId]:{...settings,work_schedule:{...(settings.work_schedule||{}),days:on?days.filter((n:number)=>n!==i):[...days,i].sort()}}}))}>{day}</Button>})}</div>
                    <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8,marginTop:8}}><input type="time" value={settings.work_schedule?.start||"09:00"} onChange={(e)=>setMopSettings((x)=>({...x,[accountId]:{...settings,work_schedule:{...(settings.work_schedule||{}),start:e.target.value}}}))} style={fieldStyle}/><input type="time" value={settings.work_schedule?.end||"21:00"} onChange={(e)=>setMopSettings((x)=>({...x,[accountId]:{...settings,work_schedule:{...(settings.work_schedule||{}),end:e.target.value}}}))} style={fieldStyle}/></div>
                  </> : null}
                  <div style={{display:"flex",gap:7,marginTop:10}}><Button onClick={()=>void saveMopSettings(accountId,settings)} disabled={actionBusy===`settings:${accountId}`}>Сохранить</Button><Button kind="ghost" onClick={()=>setEditingSettings("")}>Отмена</Button></div>
                </div> : null}

                {(changeLifecycle[accountId]||[]).find((c:any)=>c.state==="OBSERVING" || c.state==="MEASURED") ? (()=>{const c=(changeLifecycle[accountId]||[]).find((x:any)=>x.state==="OBSERVING" || x.state==="MEASURED");return <div style={{marginTop:space.lg,padding:space.md,border:`1px solid ${color.line}`,borderRadius:12}}><div style={font.bodyStrong as React.CSSProperties}>{c.state==="OBSERVING" ? "Сейчас проверяется изменение РОП" : "Результат изменения РОП"}</div><div style={{...font.small,marginTop:5}}>{c.problem}</div><div style={{...font.small,marginTop:5}}>{c.effect==="IMPROVED"?"Стало лучше":c.effect==="WORSE"?"Стало хуже":c.effect==="NO_CLEAR_EFFECT"?"Пока без заметного эффекта":`Набрано данных: ${Math.max(c.after_metrics?.interactions||0,c.after_metrics?.inquiries||0)} из необходимых ${c.needed_sample||"—"}`}</div><Button kind="ghost" onClick={()=>go(`/rop?account_id=${encodeURIComponent(accountId)}&tab=settings`)} style={{marginTop:6}}>Посмотреть результат</Button></div>})() : null}

                <div style={{marginTop:space.lg}}><div style={font.bodyStrong as React.CSSProperties}>Чему научился МОП</div>
                  {m.learned_rules?.length ? m.learned_rules.map((rule:any)=><div key={rule.id} style={{borderTop:`1px solid ${color.line}`,padding:"9px 0"}}><div style={font.small as React.CSSProperties}>{rule.text}</div><div style={{display:"flex",gap:6,marginTop:6}}><Button kind="ghost" onClick={()=>void editRule(accountId,rule)} disabled={actionBusy===`rule:${accountId}:${rule.id}`}>Исправить</Button><Button kind="ghost" onClick={()=>void rejectRule(accountId,rule)} disabled={actionBusy===`rule:${accountId}:${rule.id}`}>Отключить</Button></div></div>) : <div style={{...font.small,marginTop:6}}>Подтверждённых правил пока нет. Можно провести спарринг и подтвердить полезное правило.</div>}
                  <div style={{...font.small,marginTop:6}}>Последнее обучение: {dateText(m.last_training_at)}</div>
                </div>
                {mm ? <details style={{marginTop:space.md}}><summary style={{...font.small,cursor:"pointer",fontWeight:700}}>Режим ответов</summary><div style={{display:"flex",gap:6,flexWrap:"wrap",marginTop:7}}>{mm.can_change ? <><Button kind={mm.contour === "legacy" ? "secondary":"ghost"} onClick={async()=>{await api("/api/ai/mop_mode",{method:"POST",body:JSON.stringify({account_id:accountId,contour:"legacy"})});setMopModes((x:any)=>({...x,[accountId]:{...mm,contour:"legacy"}}));}}>Отвечать сразу</Button><Button kind={mm.contour === "new" ? "secondary":"ghost"} onClick={async()=>{await api("/api/ai/mop_mode",{method:"POST",body:JSON.stringify({account_id:accountId,contour:"new"})});setMopModes((x:any)=>({...x,[accountId]:{...mm,contour:"new"}}));}}>Показывать перед отправкой</Button></>:<span style={font.small}>{mm.title}</span>}</div></details>:null}
              </> : null}

              {product === "rop" && r ? <>
                <div style={{...font.bodyStrong,marginTop:space.lg}}>Как работает отдел продаж за {Number(r.period_days || 30)} дней</div>
                <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(125px,1fr))",gap:8,marginTop:8}}>
                  <Metric label="Звонков" value={r.calls} /><Metric label="Переписок" value={r.chats} /><Metric label="Средняя оценка" value={r.avg_score} unavailable={r.avg_score==null} /><Metric label="Сильных" value={r.strong} /><Metric label="Слабых" value={r.weak} /><Metric label="Требуют внимания" value={r.attention} />
                </div>
                {r.main_problems?.length ? <div style={{marginTop:space.lg}}><div style={font.bodyStrong as React.CSSProperties}>Где теряем клиентов</div><InsightList items={r.main_problems.slice(0,3)} /></div> : null}
                {r.detailed_actions?.length ? <div style={{marginTop:space.lg}}>
                  <div style={font.bodyStrong as React.CSSProperties}>Подробный разбор РОП</div>
                  <div style={{...font.small,marginTop:5}}>Основано на {Number(r.calls||0)+Number(r.chats||0)} реальных взаимодействиях за {Number(r.period_days||30)} дней. Сначала показаны самые частые потери.</div>
                  {(ropExpanded[accountId] ? r.detailed_actions : r.detailed_actions.slice(0,3)).map((x:any,i:number)=><div key={x.code||i} style={{marginTop:10,padding:12,border:`1px solid ${color.line}`,borderRadius:12,background:"#fbfcfe"}}>
                    <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"flex-start",flexWrap:"wrap"}}>
                      <div style={{...font.bodyStrong,color:color.heading}}>{i+1}. {x.problem}</div>
                      <Badge kind="warning">Потеря: {x.count} из {x.total} проверенных · {x.share_pct}%</Badge>
                    </div>
                    <div style={{...font.small,marginTop:8,color:color.heading}}><b>Что делать менеджеру/МОП:</b> {x.action}</div>
                    {x.script ? <div style={{marginTop:8,padding:"9px 10px",borderRadius:10,background:"#f1f5ff",...font.small,color:color.heading}}><b>Пример фразы:</b> «{x.script}»</div> : null}
                    <div style={{...font.small,marginTop:8}}><b>Кому применять:</b> {x.applies_to}</div>
                    <div style={{...font.small,marginTop:5}}><b>Как проверим:</b> {x.verify}</div>
                    {x.evidence_examples?.length ? <details style={{marginTop:7}}><summary style={{...font.small,cursor:"pointer",fontWeight:700}}>Что уже увидел РОП в разборах</summary><InsightList items={x.evidence_examples.map((t:string)=>({text:t}))} /></details> : null}
                  </div>)}
                  {r.detailed_actions.length>3 ? <Button kind="ghost" onClick={()=>setRopExpanded((x)=>({...x,[accountId]:!x[accountId]}))} style={{marginTop:8}}>{ropExpanded[accountId]?"Свернуть":"Показать весь подробный разбор"}</Button> : null}
                </div> : r.recommendations?.length ? <div style={{marginTop:space.lg}}><div style={font.bodyStrong as React.CSSProperties}>Рекомендации РОП</div><InsightList items={r.recommendations} /></div> : <div style={{...font.small,marginTop:space.md}}>Новых рекомендаций пока нет.</div>}
                <div style={{display:"flex",gap:7,flexWrap:"wrap",marginTop:space.md}}>
                  {r.mop_recommendations ? <Button kind="secondary" onClick={()=>void prepareRopPreview(accountId)} disabled={actionBusy===`rop-preview:${accountId}`}>Подготовить изменение для МОП</Button> : null}
                  <Button kind="ghost" onClick={()=>go(`/messages?account_id=${encodeURIComponent(accountId)}&attention=1`)}>Проблемные клиенты</Button>
                  {r.reactivation_candidates ? <Button kind="ghost" onClick={()=>go(`/reactivation?account_id=${encodeURIComponent(accountId)}`)}>Вернуть потерянных · {r.reactivation_candidates}</Button> : null}
                  {!p.enabled ? <Button kind="ghost" onClick={()=>{setMode("one");setPicked([accountId]);}}>Выбрать для подключения РОП</Button> : null}
                </div>
                {ropPreview[accountId]?.rules ? <div style={{marginTop:space.md,padding:space.md,border:`1px solid ${color.line}`,borderRadius:12}}><div style={font.bodyStrong as React.CSSProperties}>Что именно изменится у МОП</div><div style={{...font.small,marginTop:4}}>РОП подготовил правила из уже сохранённых разборов. Ничего ещё не применено.</div><pre style={{whiteSpace:"pre-wrap",fontFamily:"inherit",fontSize:12,lineHeight:1.5,background:"#f8fafc",padding:10,borderRadius:10}}>{ropPreview[accountId].rules}</pre><div style={{display:"flex",gap:7,flexWrap:"wrap"}}><Button onClick={()=>void applyRopPreview(accountId)} disabled={actionBusy===`rop-apply:${accountId}`}>Передать эти правила МОП</Button><Button kind="ghost" onClick={()=>setRopPreview((x)=>({...x,[accountId]:null}))}>Отмена</Button></div></div> : null}
                <div style={{...font.small,marginTop:space.md}}>Последний анализ: {dateText(r.last_analysis_at)} · Последняя передача рекомендаций МОП: {dateText(r.last_mop_transfer_at)}</div>
              </> : null}

              {mode !== "one" ? <div style={{...font.small,marginTop:space.lg}}>{p.memory_shared ? "Общая память включена для выбранной группы; каждая карточка остаётся отдельной." : "Данные этого аккаунта не смешиваются с другими выбранными аккаунтами."}</div> : null}
            </Card>;
          }) : <div className="boris-ai-empty-compact"><div className="boris-ai-empty-icon">AI</div><div><b>Выберите аккаунт</b><p>Здесь появится результат работы МОП или РОП и одно главное действие владельца.</p></div></div>}
        </div>
      </div>
    </Page>
    </AppShell>
  );
}

function PanelRow({ k, v }: { k: string; v: any }) {
  return <div style={{display:"flex",justifyContent:"space-between",gap:space.md,padding:"7px 0",borderBottom:"1px solid "+color.line}}><span style={font.small as React.CSSProperties}>{k}</span><span style={{...font.small,color:color.heading,fontWeight:700,textAlign:"right"}}>{String(v ?? "—")}</span></div>;
}
function dateText(v: any) { if (!v) return "Нет данных"; const d=new Date(v); return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString("ru-RU"); }
function scheduleText(s: any) { if (!s || !s.enabled) return "Без ограничений"; const days=(s.days||[]).map((d:number)=>["Пн","Вт","Ср","Чт","Пт","Сб","Вс"][d]).join(", "); return `${days || "—"} · ${s.start || "—"}–${s.end || "—"}`; }

const fieldStyle: React.CSSProperties = { width:"100%", boxSizing:"border-box", marginTop:8, border:`1px solid ${color.line}`, borderRadius:10, padding:"10px 12px", ...font.body, color:color.heading, background:"#fff", minHeight:44 };

function Metric({label,value,unavailable=false}:{label:string;value:any;unavailable?:boolean}) {
  return <div className="boris-ai-metric" style={{border:`1px solid ${color.line}`,borderRadius:14,padding:space.md,minHeight:88,background:color.surface}}><div style={{...font.kpi,fontWeight:800}}>{unavailable ? "—" : String(value ?? 0)}</div><div style={{...font.small,marginTop:5}}>{label}</div>{unavailable ? <div style={{...font.small,color:color.muted,marginTop:3}}>Недостаточно данных</div> : null}</div>;
}

function InsightList({items}:{items:any[]}) {
  return <div style={{marginTop:6}}>{(items||[]).map((x:any,i:number)=><div key={i} style={{borderTop:`1px solid ${color.line}`,padding:"7px 0",...font.small}}>{x?.text || String(x)}{Number(x?.count||0)>1 ? ` · ${x.count}×` : ""}</div>)}</div>;
}

function mopPerformanceText(m:any) {
  const dialogs=Number(m?.dialogs_today||0), contacts=Number(m?.contacts_today||0), attention=Number(m?.attention_now||0);
  if (!dialogs) return "Сегодня новых диалогов пока нет — оценивать результат рано.";
  if (attention) return `${attention} клиент(а) требуют внимания. Из ${dialogs} диалогов получено ${contacts} контакт(а).`;
  if (contacts) return `Получено ${contacts} контакт(а) из ${dialogs} диалогов. Срочных проблем в очереди нет.`;
  return `МОП ведёт ${dialogs} диалог(а), но контактов пока нет — стоит посмотреть, где клиенты останавливаются.`;
}

function businessHealth(product:string,p:Panel) {
  if (product === "mop") {
    const m=p.mop||{};
    if (!m.package_active) return {kind:"unset",label:"Не настроено",reason:"Для этого аккаунта нет активного пакета МОП."};
    if (!m.owner_enabled) return {kind:"action",label:"Нужно действие",reason:"МОП выключен владельцем и не отвечает новым клиентам."};
    if (!m.goal || m.goal === "Не задана") return {kind:"action",label:"Нужно действие",reason:"МОП работает без заданной бизнес-цели."};
    if (Number(m.attention_now||0)>0) return {kind:"action",label:"Нужно действие",reason:`${m.attention_now} клиент(а) сейчас требуют внимания человека.`};
    if (m.state === "вне графика") return {kind:"attention",label:"Требует внимания",reason:"Сейчас МОП вне рабочего графика; новые обращения передаются человеку."};
    if (Number(m.dialogs_today||0)>=3 && Number(m.contacts_today||0)===0) return {kind:"attention",label:"Требует внимания",reason:"Диалоги идут, но сегодня ещё не получено ни одного контакта."};
    return {kind:"good",label:"Всё хорошо",reason:Number(m.dialogs_today||0)>0 ? "МОП работает по цели; срочных проблем в текущей очереди нет." : "МОП настроен и работает; сегодня новых диалогов пока нет."};
  }
  const r=p.rop||{};
  if (!p.enabled) return {kind:"unset",label:"Не настроено",reason:"РОП не подключён к этому аккаунту."};
  if (!r.last_analysis_at) return {kind:"attention",label:"Требует внимания",reason:"РОП подключён, но по текущему периоду ещё недостаточно данных для вывода."};
  if (Number(r.attention||0)>0 || (r.avg_score!=null && Number(r.avg_score)<60)) return {kind:"action",label:"Нужно действие",reason:`РОП нашёл ${r.attention||0} проблемных взаимодействий${r.avg_score!=null ? `; средняя оценка ${r.avg_score}` : ""}.`};
  return {kind:"good",label:"Всё хорошо",reason:"РОП не видит критичных проблем, требующих вмешательства владельца сейчас."};
}

function ownerAction(product: string, p: Panel) {
  if (product === "mop") {
    const m = p.mop || {};
    if (!m.package_active) return { kind:"billing", title:"Подключить МОП", cta:"Открыть тарифы", hint:"Пакет МОП для этого аккаунта сейчас не активен.", expected:"МОП сможет обрабатывать новые диалоги после активации пакета." };
    if (!m.owner_enabled) return { kind:"enable_mop", title:"Включить МОП", cta:"Включить МОП", hint:"МОП выключен владельцем.", expected:"Новые подходящие обращения снова будут обрабатываться МОП по текущим правилам." };
    if (!m.goal || m.goal === "Не задана") return { kind:"settings", title:"Задать главную цель МОП", cta:"Задать цель", hint:"Без цели нельзя оценить, приводит ли диалог к нужному бизнес-результату.", expected:"МОП будет вести клиента к выбранному действию: телефону, замеру, расчёту, встрече или передаче менеджеру." };
    if (Number(m.attention_now||0)>0) return { kind:"attention", title:`Разобрать клиентов, которым нужен человек · ${m.attention_now}`, cta:"Открыть проблемных клиентов", hint:"В очереди есть диалоги, где МОП остановился или требуется решение менеджера.", expected:"Клиенты не останутся без следующего действия." };
    if (m.state === "вне графика") return { kind:"settings", title:"Проверить рабочий график", cta:"Изменить график", hint:"Сейчас МОП не отвечает по заданному расписанию.", expected:"График будет соответствовать реальному времени работы бизнеса." };
    if (Number(m.dialogs_today||0)>=3 && Number(m.contacts_today||0)===0) return { kind:"attention", title:"Разобрать сегодняшние диалоги", cta:"Открыть диалоги", hint:`Есть ${m.dialogs_today} диалог(а), но ни одного полученного контакта.`, expected:"Понять, на каком шаге клиенты уходят и что улучшить в работе МОП." };
    if (m.avg_sparring_score!=null && Number(m.avg_sparring_score)<6) return { kind:"training", title:"Потренировать МОП", cta:"Открыть спарринг", hint:`Последняя средняя оценка спарринга ${m.avg_sparring_score}/10.`, expected:"Отработать слабый сценарий и подтвердить полезное правило после тренировки." };
    return { kind:"none", title:"МОП работает нормально. Сегодня вмешательство не требуется", hint:"Следите за целевыми действиями и новыми рекомендациями РОП.", expected:"" };
  }
  const r = p.rop || {};
  if (!p.enabled) return { kind:"connect_rop", title:"Подключить РОП к этому аккаунту", cta:"Выбрать аккаунт для РОП", hint:"РОП сейчас не подключён к этому аккаунту. Выберите этот аккаунт в блоке подключения слева и сохраните подключение.", expected:"После сохранения подключения новые звонки и готовые переписки будут попадать в штатный ROP-анализ." };
  if (Number(r.attention||0)>0) return { kind:"attention", title:`Разобрать проблемные контакты · ${r.attention}`, cta:"Открыть проблемных клиентов", hint:r.mop_recommendations ? `РОП уже подготовил ${r.mop_recommendations} рекомендаций по найденным проблемам.` : "Есть взаимодействия с низкой оценкой, которые требуют внимания.", expected:"Сначала закрыть самые рискованные клиентские ситуации, затем применить системные улучшения." };
  if (Number(r.mop_recommendations||0)>0 && !r.last_mop_transfer_at) return { kind:"recommendations", title:"Посмотреть рекомендации РОП", cta:"Показать рекомендации", hint:`Готово рекомендаций: ${r.mop_recommendations}.`, expected:"Выбрать полезное изменение и при необходимости передать его МОП через предпросмотр → применение." };
  if (!r.last_analysis_at) return { kind:"none", title:"Пока недостаточно данных", hint:"РОП подключён и ждёт подходящих реальных звонков или переписок.", expected:"" };
  return { kind:"none", title:"РОП не видит срочных проблем. Вмешательство не требуется", hint:"Новые результаты появятся автоматически после следующих анализов.", expected:"" };
}
