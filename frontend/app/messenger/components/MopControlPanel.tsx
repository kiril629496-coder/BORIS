"use client";

import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { BrainCircuit, CheckCircle2, LockKeyhole, Pencil, RefreshCw, ShieldCheck, UserRoundCheck, XCircle } from "lucide-react";

type ModuleStatus = {
  active:boolean;
  state:string;
  pending_reviews:number;
  manual_alerts_count?:number;
  entitlement?:{purchased?:number;used?:number;left?:number;days_left?:number;unlimited?:boolean};
  settings?:{review_mode?:"all"|"smart";manual_message_alerts?:boolean};
};
type Feedback = { action?:string; correction?:string; comment?:string; created_at?:string };
type ReviewItem = {
  draft_id:number;
  chat_id:string;
  incoming:string;
  reply:string;
  item_title?:string;
  sent_at?:string|null;
  reviewed:boolean;
  feedback?:Feedback|null;
};
type ManualAlert = { chat_id:string; message_id:string; text:string; created_at?:string|null; source?:string };
type MemoryData = {
  style?:{preferred_length?:string;median_chars?:number;typical_sentences?:number;question_rate_pct?:number;cta_rate_pct?:number};
  recent_feedback?:Feedback[];
};

function authHeaders(){
  const token=typeof window==="undefined"?"":(localStorage.getItem("boris_token")||localStorage.getItem("boris_messenger_token")||"");
  return token?{Authorization:"Bearer "+token}:{};
}
async function request<T>(path:string, options:RequestInit={}):Promise<T>{
  const h=new Headers(options.headers);
  Object.entries(authHeaders()).forEach(([k,v])=>h.set(k,v));
  if(options.body&&!h.has("Content-Type"))h.set("Content-Type","application/json");
  const r=await fetch(path,{...options,headers:h,cache:"no-store"});
  const raw=await r.text();let d:any={};try{d=raw?JSON.parse(raw):{}}catch{}
  if(r.status===401){window.location.replace("/messenger/login");throw new Error("Сессия истекла.");}
  if(!r.ok)throw new Error(String(d?.detail?.message||d?.detail||d?.message||"Не удалось выполнить действие."));
  return d as T;
}
function when(value?:string|null){
  if(!value)return "";
  const d=new Date(value);
  return Number.isNaN(d.getTime())?"":d.toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
}
const box:CSSProperties={border:"1px solid var(--boris-border-soft)",borderRadius:22,background:"var(--boris-surface, #fff)",padding:18};
const button:CSSProperties={border:"1px solid var(--boris-border-soft)",borderRadius:14,padding:"10px 13px",background:"var(--boris-surface, #fff)",cursor:"pointer",fontWeight:700};
const primary:CSSProperties={...button,background:"var(--boris-accent, #111827)",color:"#fff",borderColor:"transparent"};

export default function MopControlPanel({accountId,accountName,onStatus}:{accountId:string;accountName:string;onStatus:(text:string)=>void}){
  const [status,setStatus]=useState<ModuleStatus|null>(null);
  const [items,setItems]=useState<ReviewItem[]>([]);
  const [manualAlerts,setManualAlerts]=useState<ManualAlert[]>([]);
  const [memory,setMemory]=useState<MemoryData|null>(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  const [editing,setEditing]=useState<{id:number;mode:"correct"|"reject";text:string}|null>(null);
  const [busy,setBusy]=useState<number|null>(null);
  const [memoryOpen,setMemoryOpen]=useState(false);

  const load=async(silent=false)=>{
    if(!accountId)return;
    if(!silent)setLoading(true);setError("");
    try{
      const st=await request<ModuleStatus>("/api/mop-control/status?account_id="+encodeURIComponent(accountId));
      setStatus(st);
      if(st.active){
        const [feed,mem]=await Promise.all([
          request<{items?:ReviewItem[];manual_alerts?:ManualAlert[]}>("/api/mop-control/feed?account_id="+encodeURIComponent(accountId)+"&limit=50"),
          request<MemoryData>("/api/mop-control/memory?account_id="+encodeURIComponent(accountId)),
        ]);
        setItems(feed.items||[]);
        setManualAlerts(feed.manual_alerts||[]);
        setMemory(mem);
      }else{
        setItems([]);setManualAlerts([]);setMemory(null);
      }
    }catch(e){setError((e as Error).message);}finally{if(!silent)setLoading(false);}
  };
  useEffect(()=>{
    setStatus(null);setItems([]);setManualAlerts([]);setMemory(null);setEditing(null);void load();
    const timer=window.setInterval(()=>void load(true),30_000);
    return()=>window.clearInterval(timer);
  },[accountId]);

  const pending=useMemo(()=>items.filter(x=>!x.reviewed).length,[items]);

  const sendFeedback=async(item:ReviewItem,action:"approve"|"correct"|"reject"|"handoff",text="")=>{
    setBusy(item.draft_id);setError("");
    try{
      const payload:any={account_id:accountId,draft_id:item.draft_id,action};
      if(action==="correct")payload.correction=text.trim();
      if(action==="reject")payload.comment=text.trim();
      await request("/api/mop-control/feedback",{method:"POST",body:JSON.stringify(payload)});
      setEditing(null);onStatus("BORIS запомнил обратную связь владельца.");
      await load();
    }catch(e){setError((e as Error).message);}finally{setBusy(null);}
  };

  const updateSettings=async(mode:"all"|"smart",manual:boolean)=>{
    if(!status?.active)return;
    setLoading(true);setError("");
    try{
      await request("/api/mop-control/settings",{method:"POST",body:JSON.stringify({
        account_id:accountId,review_mode:mode,manual_message_alerts:manual
      })});
      onStatus("Настройки контроля МОП сохранены.");
      await load();
    }catch(e){setError((e as Error).message);}finally{setLoading(false);}
  };
  const updateMode=(mode:"all"|"smart")=>updateSettings(mode,status?.settings?.manual_message_alerts!==false);
  const toggleManualAlerts=()=>updateSettings(status?.settings?.review_mode==="smart"?"smart":"all",status?.settings?.manual_message_alerts===false);

  if(!accountId)return <div style={box}>Выберите аккаунт.</div>;
  return <div style={{display:"grid",gap:14,maxWidth:1040,margin:"0 auto",paddingBottom:40}}>
    <section style={{...box,display:"flex",gap:16,alignItems:"center",justifyContent:"space-between",flexWrap:"wrap"}}>
      <div style={{display:"flex",gap:13,alignItems:"center"}}>
        <span style={{width:46,height:46,borderRadius:16,display:"grid",placeItems:"center",background:"rgba(59,130,246,.10)"}}><BrainCircuit size={24}/></span>
        <div><div style={{fontSize:12,opacity:.65,fontWeight:800,letterSpacing:".08em"}}>BORIS MESSENGER</div><h2 style={{margin:"3px 0 4px"}}>Контроль МОП</h2><div style={{opacity:.72}}>{accountName}</div></div>
      </div>
      <div style={{display:"flex",gap:8,alignItems:"center"}}>
        <span style={{padding:"8px 12px",borderRadius:999,fontWeight:800,background:status?.active?"rgba(16,185,129,.12)":"rgba(148,163,184,.14)",color:status?.active?"#047857":"inherit"}}>
          {status?.active?"● Активен":"○ Не активирован"}
        </span>
        <button style={button} onClick={()=>void load()} disabled={loading}><RefreshCw size={15} style={{verticalAlign:"middle",marginRight:6}}/>{loading?"Обновляем…":"Обновить"}</button>
      </div>
    </section>

    {error&&<div style={{...box,borderColor:"rgba(239,68,68,.35)",color:"#b91c1c"}}>{error}</div>}

    {!loading&&status&&!status.active&&<section style={{...box,textAlign:"center",padding:"38px 24px"}}>
      <LockKeyhole size={34} style={{marginBottom:10,opacity:.7}}/>
      <h3 style={{margin:"0 0 8px"}}>Модуль доступен после оплаты МОП</h3>
      <p style={{margin:"0 auto",maxWidth:620,opacity:.72,lineHeight:1.6}}>Раздел всегда остаётся в Messenger. После активации пакета МОП здесь появятся ответы BORIS, оценка качества, исправления и память владельца.</p>
      <div style={{marginTop:16,fontSize:13,opacity:.65}}>Остаток пакета: {status.entitlement?.unlimited?"безлимит":String(status.entitlement?.left??0)+" сообщений"}</div>
    </section>}

    {status?.active&&<>
      <section style={{...box,display:"flex",gap:18,justifyContent:"space-between",alignItems:"center",flexWrap:"wrap"}}>
        <div><strong style={{fontSize:17}}>Управление контролем</strong><div style={{opacity:.68,marginTop:4}}>На проверке: {pending} · всего в ленте: {items.length} · ручных предупреждений: {manualAlerts.length}</div></div>
        <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
          <button style={status.settings?.review_mode!=="smart"?primary:button} onClick={()=>void updateMode("all")}>Все ответы</button>
          <button style={status.settings?.review_mode==="smart"?primary:button} onClick={()=>void updateMode("smart")}>Умный контроль</button>
          <button style={status.settings?.manual_message_alerts!==false?primary:button} onClick={()=>void toggleManualAlerts()}>Ручные сообщения {status.settings?.manual_message_alerts!==false?"вкл":"выкл"}</button>
          <button style={button} onClick={()=>setMemoryOpen(v=>!v)}><BrainCircuit size={15} style={{verticalAlign:"middle",marginRight:6}}/>Что BORIS запомнил</button>
        </div>
      </section>

      {manualAlerts.length>0&&<section style={{...box,borderColor:"rgba(239,68,68,.38)",background:"rgba(254,242,242,.55)"}}>
        <div style={{display:"flex",gap:10,alignItems:"center",marginBottom:10}}><XCircle size={20} color="#b91c1c"/><div><strong style={{color:"#b91c1c"}}>Ручные сообщения сотрудников вне BORIS</strong><div style={{fontSize:13,opacity:.72}}>BORIS не мог остановить их до отправки, но обнаружил после синхронизации Avito.</div></div></div>
        <div style={{display:"grid",gap:8}}>{manualAlerts.map(alert=><div key={alert.message_id} style={{padding:"10px 12px",borderRadius:14,background:"rgba(255,255,255,.7)"}}><div style={{fontSize:12,opacity:.6,marginBottom:4}}>{when(alert.created_at)} · {alert.chat_id}</div><b>{alert.text}</b></div>)}</div>
      </section>}

      {memoryOpen&&<section style={box}>
        <h3 style={{marginTop:0}}>Что BORIS запомнил</h3>
        <div style={{display:"flex",gap:8,flexWrap:"wrap",marginBottom:12}}>
          <span style={{padding:"7px 10px",borderRadius:999,background:"rgba(148,163,184,.12)"}}>Стиль: {memory?.style?.preferred_length==="short"?"короткий":memory?.style?.preferred_length==="detailed"?"подробный":"обычный"}</span>
          <span style={{padding:"7px 10px",borderRadius:999,background:"rgba(148,163,184,.12)"}}>Медиана: {memory?.style?.median_chars??"—"} симв.</span>
          <span style={{padding:"7px 10px",borderRadius:999,background:"rgba(148,163,184,.12)"}}>Вопрос: {memory?.style?.question_rate_pct??"—"}%</span>
          <span style={{padding:"7px 10px",borderRadius:999,background:"rgba(148,163,184,.12)"}}>CTA: {memory?.style?.cta_rate_pct??"—"}%</span>
        </div>
        {(memory?.recent_feedback||[]).length?<div style={{display:"grid",gap:8}}>{(memory?.recent_feedback||[]).slice(0,8).map((x,i)=><div key={i} style={{padding:"10px 12px",borderRadius:14,background:"rgba(148,163,184,.08)"}}><b>{x.action==="approve"?"Подтверждено":x.action==="correct"?"Исправлено":x.action==="reject"?"Запрещено":"Передать человеку"}</b>{x.correction?<div>Правильный вариант: {x.correction}</div>:null}{x.comment?<div>Комментарий: {x.comment}</div>:null}</div>)}</div>:<div style={{opacity:.65}}>Обратной связи владельца пока нет.</div>}
      </section>}

      <section style={{display:"grid",gap:12}}>
        {items.length===0?<div style={{...box,textAlign:"center",opacity:.7}}>Ответов МОП для проверки пока нет.</div>:items.map(item=><article key={item.draft_id} style={{...box,borderColor:item.reviewed?"rgba(16,185,129,.28)":"var(--boris-border-soft)"}}>
          <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"flex-start",flexWrap:"wrap"}}>
            <div><strong>{item.item_title||"Диалог Avito"}</strong><div style={{fontSize:12,opacity:.6,marginTop:3}}>{when(item.sent_at)} · #{item.draft_id}</div></div>
            {item.reviewed?<span style={{fontSize:12,fontWeight:800,color:"#047857"}}>✓ Проверено</span>:<span style={{fontSize:12,fontWeight:800,opacity:.6}}>Ждёт оценки</span>}
          </div>
          <div style={{display:"grid",gap:8,marginTop:14}}>
            <div style={{padding:"11px 13px",borderRadius:15,background:"rgba(148,163,184,.10)"}}><small style={{display:"block",opacity:.6,marginBottom:4}}>Клиент</small>{item.incoming||"—"}</div>
            <div style={{padding:"11px 13px",borderRadius:15,background:"rgba(59,130,246,.08)"}}><small style={{display:"block",opacity:.6,marginBottom:4}}>BORIS</small>{item.reply}</div>
          </div>
          {!item.reviewed&&<div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:12}}>
            <button style={button} disabled={busy===item.draft_id} onClick={()=>void sendFeedback(item,"approve")}><CheckCircle2 size={15} style={{verticalAlign:"middle",marginRight:5}}/>Верно</button>
            <button style={button} disabled={busy===item.draft_id} onClick={()=>setEditing({id:item.draft_id,mode:"correct",text:""})}><Pencil size={15} style={{verticalAlign:"middle",marginRight:5}}/>Исправить</button>
            <button style={button} disabled={busy===item.draft_id} onClick={()=>setEditing({id:item.draft_id,mode:"reject",text:""})}><XCircle size={15} style={{verticalAlign:"middle",marginRight:5}}/>Так нельзя</button>
            <button style={button} disabled={busy===item.draft_id} onClick={()=>void sendFeedback(item,"handoff")}><UserRoundCheck size={15} style={{verticalAlign:"middle",marginRight:5}}/>Нужен человек</button>
          </div>}
          {editing?.id===item.draft_id&&<div style={{marginTop:12,display:"grid",gap:8}}>
            <textarea value={editing.text} onChange={e=>setEditing({...editing,text:e.target.value})} rows={3} placeholder={editing.mode==="correct"?"Напишите, как BORIS должен был ответить":"Напишите, что именно было неверно"} style={{width:"100%",resize:"vertical",border:"1px solid var(--boris-border-soft)",borderRadius:14,padding:12,background:"transparent",color:"inherit"}}/>
            <div style={{display:"flex",gap:8}}><button style={primary} disabled={!editing.text.trim()||busy===item.draft_id} onClick={()=>void sendFeedback(item,editing.mode,editing.text)}>Запомнить</button><button style={button} onClick={()=>setEditing(null)}>Отмена</button></div>
          </div>}
          {item.reviewed&&item.feedback?<div style={{marginTop:10,fontSize:13,opacity:.7}}><ShieldCheck size={14} style={{verticalAlign:"middle",marginRight:5}}/>Обратная связь сохранена{item.feedback.correction?": "+item.feedback.correction:item.feedback.comment?": "+item.feedback.comment:""}.</div>:null}
        </article>)}
      </section>
    </>}
  </div>;
}
