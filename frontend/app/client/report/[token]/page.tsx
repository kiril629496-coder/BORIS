"use client";

import React from "react";
import { useParams, useSearchParams } from "next/navigation";

type Report = {status?:string;message?:string;headline?:string;account_name?:string;period_label?:string;text?:string;totals?:Record<string,number>;client_safe?:boolean};

export default function ClientReportPage(){
  const params=useParams();
  const sp=useSearchParams();
  const token=String(params?.token||"");
  const period=Number(sp.get("period_days")||7);
  const reportType=sp.get("report_type")||"messages";
  const [report,setReport]=React.useState<Report|null>(null);
  const [auth,setAuth]=React.useState(true);

  React.useEffect(()=>{
    const bearer=localStorage.getItem("boris_token")||"";
    if(!bearer){setAuth(false);return;}
    fetch(`/api/inbox/daily_report/client_preview/${encodeURIComponent(token)}?period_days=${period}&report_type=${encodeURIComponent(reportType)}`,{headers:{Authorization:`Bearer ${bearer}`},cache:"no-store"})
      .then(async r=>{if(r.status===401||r.status===403){setAuth(false);return null;}return r.json();})
      .then(d=>{if(d)setReport(d);})
      .catch(()=>setReport({status:"error",message:"Не удалось открыть отчёт"}));
  },[token,period,reportType]);

  if(!auth)return <main style={{maxWidth:720,margin:"72px auto",padding:24,fontFamily:"system-ui,sans-serif"}}><h1>Отчёт BORIS</h1><p>Эта ссылка защищена. Войдите в BORIS под пользователем, у которого есть доступ к аккаунту клиента, затем откройте ссылку снова.</p><a href="/login" style={{display:"inline-flex",minHeight:44,alignItems:"center",padding:"0 16px",borderRadius:10,background:"#2563eb",color:"#fff",textDecoration:"none",fontWeight:700}}>Войти в BORIS</a></main>;
  if(!report)return <main style={{maxWidth:720,margin:"72px auto",padding:24,fontFamily:"system-ui,sans-serif"}}>Готовим отчёт…</main>;
  if(report.status!=="ok")return <main style={{maxWidth:720,margin:"72px auto",padding:24,fontFamily:"system-ui,sans-serif"}}><h1>Отчёт BORIS</h1><p>{report.message||"Отчёт недоступен"}</p></main>;

  return <main style={{maxWidth:820,margin:"40px auto",padding:24,fontFamily:"system-ui,sans-serif",color:"#101828"}} data-testid="client-safe-report">
    <div style={{fontSize:13,color:"#667085"}}>BORIS · клиентский отчёт</div>
    <h1 style={{margin:"6px 0 4px",fontSize:28}}>{report.account_name}</h1>
    <div style={{color:"#667085"}}>{report.period_label}</div>
    <section style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(140px,1fr))",gap:10,marginTop:22}}>
      {[["Новых диалогов",report.totals?.new_dialogs],["Новых сообщений",report.totals?.new_msgs],["Непрочитанных",report.totals?.unread],["Без ответа",report.totals?.unanswered]].map(([label,value])=><div key={String(label)} style={{border:"1px solid #e5e7eb",borderRadius:14,padding:16}}><div style={{fontSize:12,color:"#667085"}}>{label}</div><b style={{fontSize:24}}>{String(value??0)}</b></div>)}
    </section>
    <section style={{border:"1px solid #e5e7eb",borderRadius:16,padding:20,marginTop:16,background:"#fff"}}><h2 style={{fontSize:18,margin:"0 0 12px"}}>{report.headline}</h2><pre style={{whiteSpace:"pre-wrap",fontFamily:"inherit",fontSize:14,lineHeight:1.6,margin:0}}>{report.text}</pre></section>
    <div style={{marginTop:16,fontSize:12,color:"#667085"}}>В отчёте нет внутренних идентификаторов, технических ошибок, служебных заметок и системных данных BORIS.</div>
  </main>;
}
