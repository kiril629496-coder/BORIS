"use client";

import React from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/app/components/shell/AppShell";
import { useAccounts } from "@/app/lib/AccountContext";

type Report = {status?:string;message?:string;text?:string;headline?:string;period_label?:string;report_type_name?:string;totals?:Record<string,number>;account_name?:string};
type SmartLink = {key:string;label:string;url:string;classification:string;share_allowed:boolean;security?:string};
type Prepared = {status?:string;message?:string;confirmation_id?:string;account_name?:string;period_label?:string;report_type_name?:string;preview?:string;recipient?:string};

const box:React.CSSProperties={padding:20,border:"1px solid var(--boris-border,#e5e7eb)",borderRadius:16,background:"var(--boris-surface,#fff)"};
const btn:React.CSSProperties={minHeight:44,padding:"0 16px",border:"1px solid var(--boris-border,#d0d5dd)",borderRadius:10,fontWeight:700,cursor:"pointer",background:"var(--boris-surface,#fff)",color:"var(--boris-text,#101828)"};
const primary:React.CSSProperties={...btn,border:0,background:"var(--boris-blue,#2563eb)",color:"#fff"};

export default function ClientSuccessPage(){
  const router=useRouter();
  const {selectedAccountId,selectedAccount,mode}=useAccounts();
  const accountId=mode==="single"?(selectedAccountId||""):"";
  const [period,setPeriod]=React.useState(7);
  const reportType="messages";
  const [report,setReport]=React.useState<Report|null>(null);
  const [links,setLinks]=React.useState<SmartLink[]>([]);
  const [loading,setLoading]=React.useState(false);
  const [preparing,setPreparing]=React.useState(false);
  const [prepared,setPrepared]=React.useState<Prepared|null>(null);
  const [sending,setSending]=React.useState(false);
  const [notice,setNotice]=React.useState("");
  const [sent,setSent]=React.useState("");

  const auth=()=>({Authorization:`Bearer ${localStorage.getItem("boris_token")||""}`});
  const load=React.useCallback(async()=>{
    if(!accountId){setReport(null);setLinks([]);return;}
    setLoading(true);setNotice("");setPrepared(null);setSent("");
    try{
      const qs=`account_id=${encodeURIComponent(accountId)}&period_days=${period}&report_type=${reportType}`;
      const [rr,lr]=await Promise.all([
        fetch(`/api/inbox/daily_report/preview?${qs}`,{headers:auth(),cache:"no-store"}),
        fetch(`/api/inbox/daily_report/smart_links?${qs}`,{headers:auth(),cache:"no-store"}),
      ]);
      setReport(await rr.json());
      const ld=await lr.json();setLinks(ld?.links||[]);
    }catch{setReport({status:"error",message:"Не удалось подготовить отчёт"});}
    finally{setLoading(false);}
  },[accountId,period]);
  React.useEffect(()=>{load();},[load]);

  const clientLink=links.find(l=>l.key==="client_report"&&l.classification==="CLIENT_SAFE_LINK"&&l.share_allowed);
  const fullUrl=(path:string)=>new URL(path,window.location.origin).toString();
  const copy=async(text:string,ok:string)=>{try{await navigator.clipboard.writeText(text);setNotice(ok);}catch{setNotice("Не удалось скопировать");}};
  const share=async()=>{if(!clientLink)return;const url=fullUrl(clientLink.url);try{if(navigator.share)await navigator.share({title:"Отчёт BORIS",text:"Клиентский отчёт BORIS",url});else await navigator.clipboard.writeText(url);setNotice("Клиентская ссылка подготовлена");}catch{}};

  const prepare=async()=>{
    if(!accountId)return;setPreparing(true);setNotice("");
    try{const r=await fetch("/api/inbox/daily_report/delivery/prepare",{method:"POST",headers:{...auth(),"Content-Type":"application/json"},body:JSON.stringify({account_id:accountId,period_days:period,report_type:reportType})});const d=await r.json();if(!r.ok||d.status!=="ok"){setNotice(d.detail||d.message||"Не удалось подготовить отправку");return;}setPrepared(d);}catch{setNotice("Не удалось подготовить отправку");}finally{setPreparing(false);}
  };
  const confirm=async()=>{
    if(!accountId||!prepared?.confirmation_id)return;setSending(true);setNotice("");
    try{const r=await fetch("/api/inbox/daily_report/delivery/confirm",{method:"POST",headers:{...auth(),"Content-Type":"application/json"},body:JSON.stringify({account_id:accountId,confirmation_id:prepared.confirmation_id})});const d=await r.json();if(!r.ok||d.status!=="ok"){setNotice(d.detail||d.message||"Не удалось отправить отчёт");return;}setSent(`Отчёт отправлен: ${d.recipient||"Telegram"}`);setPrepared(null);}catch{setNotice("Не удалось отправить отчёт");}finally{setSending(false);}
  };

  return <AppShell active="home" title="Клиентский успех" subtitle="Отчёт → проверка → подтверждение → существующий канал доставки">
    <div className="boris-client-success-workspace" data-testid="client-success-workspace">
      <section className="boris-client-success-hero" style={box}>
        <div style={{display:"flex",justifyContent:"space-between",gap:16,alignItems:"flex-start",flexWrap:"wrap"}}>
          <div><div style={{fontSize:13,color:"var(--boris-text-3,#667085)"}}>Выбранный аккаунт</div><div style={{fontSize:20,fontWeight:750,marginTop:4}}>{selectedAccount?.name||"Выберите один аккаунт"}</div><div style={{fontSize:13,marginTop:8,color:"var(--boris-text-3,#667085)"}}>Отправка всегда проходит через предпросмотр и отдельное подтверждение владельца.</div></div>
          <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
            <label style={{fontSize:13}}>Тип отчёта<br/><select value={reportType} disabled style={{marginTop:5,minHeight:40,borderRadius:9,padding:"0 10px"}}><option value="messages">Обращения и продажи</option></select></label>
            <label style={{fontSize:13}}>Период<br/><select value={period} onChange={e=>setPeriod(Number(e.target.value))} style={{marginTop:5,minHeight:40,borderRadius:9,padding:"0 10px"}}><option value={1}>Сутки</option><option value={7}>7 дней</option><option value={30}>30 дней</option></select></label>
          </div>
        </div>
        <button onClick={load} disabled={!accountId||loading} style={{...primary,marginTop:14}}>{loading?"Готовлю…":"Обновить отчёт"}</button>
      </section>
      {notice&&<section role="status" style={{...box,padding:14}}>{notice}</section>}
      {sent&&<section role="status" style={{...box,padding:14,borderColor:"#86efac"}}>✅ {sent}</section>}
      {report?.status==="error"&&<section style={box}>{report.message}</section>}
      {report?.status==="ok"&&<>
        <section className="boris-client-success-kpis">{[["Новых диалогов",report.totals?.new_dialogs],["Новых сообщений",report.totals?.new_msgs],["Непрочитанных",report.totals?.unread],["Без ответа",report.totals?.unanswered]].map(([l,v])=><div className="boris-client-success-kpi" key={String(l)} style={{...box,padding:16}}><div style={{fontSize:12,color:"var(--boris-text-3,#667085)"}}>{l}</div><b style={{fontSize:24}}>{String(v??0)}</b></div>)}</section>
        <section className="boris-client-success-report" style={box}>
          <div className="boris-client-success-report-head" style={{display:"flex",justifyContent:"space-between",gap:12,flexWrap:"wrap"}}><h2 style={{margin:0,fontSize:18}}>Предпросмотр клиентского отчёта</h2><span style={{fontSize:13,color:"var(--boris-text-3,#667085)"}}>{report.period_label}</span></div>
          <pre style={{whiteSpace:"pre-wrap",fontFamily:"inherit",fontSize:14,lineHeight:1.55,margin:"14px 0 0"}}>{(report.text||"Нет данных").replace(/<[^>]+>/g, "")}</pre>
          <div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:16}}>
            <button style={btn} onClick={()=>copy((report.text||"").replace(/<[^>]+>/g, ""),"Текст отчёта скопирован")}>Скопировать текст</button>
            {clientLink&&<button style={btn} onClick={()=>router.push(clientLink.url)}>Открыть клиентский вид</button>}
            {clientLink&&<button style={btn} onClick={()=>copy(fullUrl(clientLink.url),"Клиентская ссылка скопирована")}>Скопировать ссылку</button>}
            {clientLink&&<button style={btn} onClick={share}>Подготовить клиенту</button>}
            <button style={primary} onClick={prepare} disabled={preparing}>{preparing?"Проверяю канал…":"Подготовить отправку"}</button>
          </div>
          {clientLink&&<div style={{fontSize:12,color:"var(--boris-text-3,#667085)",marginTop:10}}>Клиентская ссылка защищена: вход в BORIS и доступ к аккаунту обязательны. Публичной ссылки без авторизации нет.</div>}
        </section>
      </>}
      {prepared&&<div role="dialog" aria-modal="true" aria-label="Подтверждение отправки отчёта" style={{position:"fixed",inset:0,background:"rgba(15,23,42,.38)",zIndex:1600,display:"grid",placeItems:"center",padding:20}} onClick={()=>!sending&&setPrepared(null)}>
        <div style={{...box,width:520,maxWidth:"100%",boxShadow:"0 20px 60px rgba(15,23,42,.22)"}} onClick={e=>e.stopPropagation()}>
          <h2 style={{margin:"0 0 8px",fontSize:20}}>Отправить отчёт?</h2><p style={{margin:"0 0 14px",color:"var(--boris-text-2,#475467)"}}>BORIS подготовил клиентскую версию. До нажатия кнопки ниже ничего наружу не отправляется.</p>
          <div style={{display:"grid",gap:7,fontSize:14}}><div><b>Клиент:</b> {prepared.account_name}</div><div><b>Период:</b> {prepared.period_label}</div><div><b>Тип:</b> {prepared.report_type_name}</div><div><b>Канал:</b> Telegram</div><div><b>Получатель:</b> {prepared.recipient}</div></div>
          <pre style={{whiteSpace:"pre-wrap",maxHeight:260,overflow:"auto",fontFamily:"inherit",fontSize:13,lineHeight:1.45,background:"var(--boris-bg,#f8fafc)",padding:12,borderRadius:10,marginTop:14}}>{prepared.preview}</pre>
          <div style={{display:"flex",gap:8,justifyContent:"flex-end",marginTop:16,flexWrap:"wrap"}}><button style={btn} onClick={()=>setPrepared(null)} disabled={sending}>Отмена</button><button style={primary} onClick={confirm} disabled={sending}>{sending?"Отправляю…":"Отправить в Telegram"}</button></div>
        </div>
      </div>}
    </div>
  </AppShell>;
}
