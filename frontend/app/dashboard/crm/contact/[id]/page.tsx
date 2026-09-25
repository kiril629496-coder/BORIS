"use client";

import { Button, Icon } from "@/app/ui";
import { use, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useAccounts } from "@/app/lib/AccountContext";
import { crmApi, crmHumanError } from "@/app/lib/crmApi";

async function api(url:string, options?:RequestInit){ return crmApi(url,options); }
function statusLabel(s:string){ return s==="won"?"Выиграна":s==="lost"?"Потеряна":"В работе"; }

export default function ContactPage({params}:{params:Promise<{id:string}>}){
  const {id}=use(params); const router=useRouter(); const {selectedAccountId}=useAccounts();
  const [data,setData]=useState<any>(null); const [form,setForm]=useState<any>({}); const [error,setError]=useState(""); const [saving,setSaving]=useState(false);
  const [taskTitle,setTaskTitle]=useState(""); const [taskDue,setTaskDue]=useState("");
  async function load(){
    try{if(!selectedAccountId)return;setError("");const d=await api(`/api/crm/product/contacts/${id}?account_id=${encodeURIComponent(selectedAccountId)}`);setData(d);setForm({display_name:d.contact.display_name||"",primary_phone:d.contact.primary_phone||"",primary_email:d.contact.primary_email||"",company_name:d.contact.company_name||"",visual_type:d.contact.visual_type||"auto",business_activity:d.contact.business_activity||""});}
    catch(e){setData(null);setError(e instanceof Error?e.message:"Ошибка системы продаж");}
  }
  useEffect(()=>{setData(null);setError("");void load();},[id,selectedAccountId]);
  const openTasks=useMemo(()=>data?.tasks?.filter((t:any)=>t.status==="open")||[],[data]);
  const openDeals=useMemo(()=>data?.deals?.filter((d:any)=>d.status==="open")||[],[data]);
  async function save(){if(!selectedAccountId)return;setSaving(true);try{await api(`/api/crm/product/contacts/${id}?account_id=${encodeURIComponent(selectedAccountId)}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(form)});await load();}finally{setSaving(false);}}
  async function createTask(){if(!selectedAccountId||!taskTitle.trim())return;setSaving(true);try{await api(`/api/crm/product/tasks?account_id=${encodeURIComponent(selectedAccountId)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({contact_id:Number(id),title:taskTitle.trim(),due_at:taskDue?new Date(taskDue).toISOString():null})});setTaskTitle("");setTaskDue("");await load();}catch(e){setError(e instanceof Error?e.message:"Не удалось создать задачу");}finally{setSaving(false);}}
  async function doneTask(tid:number){if(!selectedAccountId)return;await api(`/api/crm/product/tasks/${tid}?account_id=${encodeURIComponent(selectedAccountId)}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:"done"})});await load();}
  if(!data){
    if(!error)return <div className="boris-product-route" style={{padding:24}}>Загрузка клиента…</div>;
    const view=crmHumanError(error,"client");
    return <div className="boris-product-route" data-testid="crm-detail-error" style={{minHeight:"60vh",display:"grid",placeItems:"center",padding:24,background:"var(--boris-background)"}}>
      <div style={{width:"min(100%,560px)",background:"#fff",border:"1px solid var(--boris-border)",borderRadius:14,padding:18}}>
        <div style={{fontSize:20,fontWeight:850,marginBottom:8}}>{view.title}</div>
        <div style={{fontSize:14,lineHeight:1.55,color:"var(--boris-text-2)"}}>{view.message}</div>
        <div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:14}}>
          <Button onClick={()=>void load()} style={primary}>Повторить</Button>
          <Button onClick={()=>router.push("/dashboard/crm")} style={ghost}>Вернуться в CRM</Button>
        </div>
        <details style={{marginTop:14,fontSize:11,color:"var(--boris-text-3)"}}><summary>Техническая причина</summary><div style={{marginTop:6,overflowWrap:"anywhere"}}>{view.technical}</div></details>
      </div>
    </div>;
  }
  const c=data.contact; const latestConversation=data.conversations?.[0]; const currentAction=openTasks[0]?.title || (openDeals.length?"Назначить следующий шаг по активной сделке":"Проверить, нужен ли следующий контакт");

  return <div className="boris-product-route boris-crm-contact-page" style={{minHeight:"100vh",background:"var(--boris-background)",padding:24,color:"var(--boris-text-primary)"}}>
    <div style={{maxWidth:1450,margin:"0 auto"}}>
      <div style={{display:"flex",justifyContent:"flex-end",gap:10,alignItems:"center",marginBottom:14,flexWrap:"wrap"}}><Button onClick={load} style={ghost}>Обновить</Button></div>
      {error?<div style={{padding:10,borderRadius:10,background:"#fff1f2",color:"#b42318",marginBottom:12}}>{error}</div>:null}
      <div className="boris-crm-detail-card boris-crm-contact-hero" style={card}>
        <div className="boris-crm-profile-head"><span className="boris-crm-profile-avatar">{c.company_name?<Icon name="building" size={28}/>:<Icon name="user" size={28}/>}</span><div style={{minWidth:0}}><div style={{fontSize:10,fontWeight:900,color:"#7c5cff",letterSpacing:'.06em'}}>{c.company_name?"КОМПАНИЯ":"КЛИЕНТ"} #{id}</div><h1 style={{margin:"4px 0 3px",fontSize:25,overflowWrap:"anywhere"}}>{c.display_name||c.company_name||"Клиент"}</h1><div style={{fontSize:13,color:"var(--boris-text-2)"}}>{c.company_name||"Частный клиент"} · {openDeals.length} сделок в работе · {openTasks.length} открытых задач</div></div></div>
        <div style={{marginTop:14,padding:13,borderRadius:11,background:"#eff6ff",border:"1px solid #bfdbfe"}}><div style={{fontSize:10,fontWeight:900,color:"#1d4ed8",letterSpacing:'.05em'}}>ЧТО СДЕЛАТЬ СЕЙЧАС</div><div style={{fontSize:16,fontWeight:850,marginTop:4}}>{currentAction}</div>{openTasks[0]?.due_at?<div style={{fontSize:12,color:new Date(openTasks[0].due_at)<new Date()?"#b42318":"var(--boris-text-2)",marginTop:3}}>{new Date(openTasks[0].due_at)<new Date()?"Просрочено: ":"Срок: "}{new Date(openTasks[0].due_at).toLocaleString("ru-RU")}</div>:null}</div>
        <div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:12}}>{latestConversation?<Button onClick={()=>router.push(`/messages?account_id=${encodeURIComponent(selectedAccountId||"")}&chat_id=${encodeURIComponent(latestConversation.external_chat_id||"")}`)} style={primary}>Открыть диалог</Button>:null}{c.primary_phone?<a href={`tel:${c.primary_phone}`} style={linkButton}>Позвонить {c.primary_phone}</a>:null}{c.primary_email?<a href={`mailto:${c.primary_email}`} style={linkButton}>Эл. почта</a>:null}</div>
      </div>

      <div className="boris-crm-contact-layout" style={{display:"grid",gridTemplateColumns:"minmax(280px,380px) minmax(0,1fr)",gap:14,marginTop:14,alignItems:"start"}}>
        <div style={{minWidth:0}}>
          <div className="boris-crm-detail-card" style={card}><h3 style={{marginTop:0}}>Карточка клиента</h3>{[["display_name","Имя"],["primary_phone","Телефон"],["primary_email","Эл. почта"],["company_name","Компания"]].map(([key,labelText])=><label key={key} style={{display:"block",marginBottom:11}}><span style={label}>{labelText}</span><input value={form[key]||""} onChange={e=>setForm({...form,[key]:e.target.value})} style={input}/></label>)}<label style={{display:"block",marginBottom:11}}><span style={label}>Вид карточки</span><select value={form.visual_type||"auto"} onChange={e=>setForm({...form,visual_type:e.target.value})} style={input}><option value="auto">Авто</option><option value="neutral">Человек — нейтрально</option><option value="male">Мужчина</option><option value="female">Женщина</option><option value="company">Компания</option></select></label>{form.visual_type==="company"?<label style={{display:"block",marginBottom:11}}><span style={label}>Деятельность компании</span><select value={form.business_activity||""} onChange={e=>setForm({...form,business_activity:e.target.value})} style={input}><option value="">Не указана</option><option value="production">Производство</option><option value="trade">Торговля</option><option value="services">Услуги</option><option value="transport">Транспорт / логистика</option><option value="marketing">Маркетинг / реклама</option><option value="other">Другое</option></select></label>:null}<div style={{fontSize:11,color:"var(--boris-text-3)",marginBottom:10}}>Визуальный тип нужен только для удобства работы с клиентами и не влияет на автоматизацию продаж.</div><Button disabled={saving} onClick={save} style={{...primary,width:"100%"}}>Сохранить</Button></div>
          <div className="boris-crm-detail-card" style={{...card,marginTop:14}}><h3 style={{marginTop:0}}>Следующий контакт</h3><div style={{fontSize:12,color:"var(--boris-text-2)",marginBottom:8}}>Задачу можно поставить даже до создания сделки.</div><input value={taskTitle} onChange={e=>setTaskTitle(e.target.value)} placeholder="Например: уточнить размеры кухни" style={input}/><input type="datetime-local" value={taskDue} onChange={e=>setTaskDue(e.target.value)} style={{...input,marginTop:7}}/><Button disabled={saving||!taskTitle.trim()} onClick={createTask} style={{...primary,width:"100%",marginTop:8}}>Создать задачу</Button>{data.tasks?.map((t:any)=><div key={t.id} style={{padding:"9px 0",borderTop:"1px solid var(--boris-border-soft)"}}><b style={{fontSize:13,textDecoration:t.status==="done"?"line-through":"none"}}>{t.title}</b><div style={{fontSize:11,color:t.status==="open"&&t.due_at&&new Date(t.due_at)<new Date()?"#b42318":"var(--boris-text-3)",marginTop:3}}>{t.status==="done"?"Выполнено":t.due_at?new Date(t.due_at).toLocaleString("ru-RU"):"Срок не задан"}</div>{t.status==="open"?<Button onClick={()=>doneTask(t.id)} style={{...ghost,marginTop:6,padding:"5px 8px"}}>Готово</Button>:null}</div>)}</div>
        </div>

        <div style={{minWidth:0}}>
          <div className="boris-crm-detail-card" style={card}><h3 style={{marginTop:0}}>Сделки клиента</h3>{!data.deals?.length?<div style={{color:"var(--boris-text-3)",fontSize:13}}>Сделок пока нет. Клиент при этом остаётся в системе и не теряется.</div>:data.deals.map((d:any)=><Button key={d.id} onClick={()=>router.push(`/dashboard/crm/deal/${d.id}`)} style={{width:"100%",textAlign:"left",border:0,borderBottom:"1px solid var(--boris-border-soft)",background:"#fff",padding:"11px 2px",cursor:"pointer"}}><div style={{display:"flex",justifyContent:"space-between",gap:10}}><b>{d.title}</b><span style={{fontSize:11,fontWeight:800}}>{statusLabel(d.status)}</span></div><div style={{fontSize:12,color:"var(--boris-text-2)",marginTop:4}}>{d.stage_name||"Этап не задан"}{d.next_action_at?` · следующий шаг ${new Date(d.next_action_at).toLocaleString("ru-RU")}`:" · следующий шаг не назначен"}</div></Button>)}</div>

          <div className="boris-crm-detail-card" style={{...card,marginTop:14}}><h3 style={{marginTop:0}}>Диалоги</h3>{!data.conversations?.length?<div style={{fontSize:13,color:"var(--boris-text-3)"}}>Диалогов пока нет.</div>:data.conversations.map((cv:any)=><Button key={cv.id} onClick={()=>router.push(`/messages?account_id=${encodeURIComponent(cv.account_id||selectedAccountId||"")}&chat_id=${encodeURIComponent(cv.external_chat_id||"")}`)} style={{width:"100%",textAlign:"left",border:0,borderBottom:"1px solid var(--boris-border-soft)",background:"#fff",padding:"10px 2px",cursor:"pointer"}}><b>{cv.title||cv.channel||"Диалог"}</b><div style={{fontSize:11,color:"var(--boris-text-3)",marginTop:3}}>{cv.channel||""}{cv.last_message_at?` · ${new Date(cv.last_message_at).toLocaleString("ru-RU")}`:""}{cv.unread_count?` · непрочитано ${cv.unread_count}`:""}</div></Button>)}</div>

          <div className="boris-crm-detail-card" style={{...card,marginTop:14}}><h3 style={{marginTop:0}}>История клиента</h3><div style={{fontSize:12,color:"var(--boris-text-2)",marginBottom:8}}>Сообщения, этапы и действия по продажам в одной хронологии.</div>{!data.activities?.length?<div style={{fontSize:13,color:"var(--boris-text-3)"}}>История пока пуста.</div>:data.activities.slice(0,100).map((a:any)=>{const isCall=a.activity_type==="call";return <div key={a.id} style={{padding:"9px 0",borderBottom:"1px solid var(--boris-border-soft)"}}><div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center"}}><div style={{display:"flex",alignItems:"center",gap:7}}>{isCall?<span style={{width:28,height:28,borderRadius:9,display:"grid",placeItems:"center",background:a.metadata_json?.missed?"#fff1f2":"#ecfdf5",color:a.metadata_json?.missed?"#be123c":"#047857"}}><Icon name="support" size={15}/></span>:null}<b style={{fontSize:12}}>{a.title||a.activity_type||"Событие"}</b></div><span style={{fontSize:11,color:"var(--boris-text-3)",whiteSpace:"nowrap"}}>{new Date(a.created_at).toLocaleString("ru-RU")}</span></div>{a.body?<div style={{fontSize:12,color:"var(--boris-text-2)",marginTop:3,whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{a.body}</div>:null}{isCall&&a.metadata_json?.analysis_available?<div style={{fontSize:11,color:"#2563eb",fontWeight:750,marginTop:4}}>Разбор разговора доступен в РОП → Звонки</div>:null}</div>})}</div>
        </div>
      </div>
    </div>
  </div>;
}

const card:React.CSSProperties={background:"#fff",border:"1px solid var(--boris-border)",borderRadius:14,padding:16};
const input:React.CSSProperties={width:"100%",height:40,border:"1px solid var(--boris-border)",borderRadius:9,padding:"0 10px",marginTop:5,boxSizing:"border-box",minWidth:0};
const ghost:React.CSSProperties={border:"1px solid var(--boris-border)",background:"#fff",borderRadius:9,padding:"8px 11px",cursor:"pointer"};
const primary:React.CSSProperties={border:0,background:"var(--boris-blue)",color:"#fff",borderRadius:9,padding:"9px 12px",cursor:"pointer",fontWeight:750};
const linkButton:React.CSSProperties={...ghost as any,textDecoration:"none",display:"inline-flex",alignItems:"center",fontSize:13,color:"var(--boris-text-primary)"};
const label:React.CSSProperties={fontSize:11,fontWeight:800,color:"var(--boris-text-3)"};
