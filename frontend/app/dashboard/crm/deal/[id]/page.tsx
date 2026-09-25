"use client";

import { Button, Icon } from "@/app/ui";
import { use, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useAccounts } from "@/app/lib/AccountContext";
import { crmApi, crmHumanError } from "@/app/lib/crmApi";

async function api(url: string, options?: RequestInit) { return crmApi(url, options); }

function dtLocal(v?: string | null) {
  if (!v) return "";
  const d = new Date(v); const z = new Date(d.getTime() - d.getTimezoneOffset()*60000);
  return z.toISOString().slice(0,16);
}
function money(v?: number | null) { return new Intl.NumberFormat("ru-RU",{style:"currency",currency:"RUB",maximumFractionDigits:0}).format((v||0)/100); }
function recommendedByStage(stageName?: string | null, hasPhone?: boolean) {
  const s=String(stageName||"").toLowerCase();
  if(s.includes("нов")) return hasPhone?"Позвонить клиенту и уточнить потребность":"Ответить клиенту и уточнить потребность";
  if(s.includes("связ")) return "Уточнить размеры, бюджет и срок покупки";
  if(s.includes("квалиф")) return "Подготовить расчёт и коммерческое предложение";
  if(s.includes("в работе")) return "Зафиксировать следующий договорённый шаг";
  if(s.includes("кп")||s.includes("предлож")) return "Получить решение клиента по предложению";
  if(s.includes("договор")) return "Проконтролировать договор и оплату";
  return "Назначить следующий шаг по сделке";
}
function activityTitle(a:any) {
  if (a.activity_type === "stage_change") return "Изменён этап";
  if (a.activity_type === "task_created") return "Создан следующий шаг";
  if (a.activity_type === "message") return a.direction === "in" ? "Сообщение клиента" : "Ответ менеджера / МОП";
  if (a.activity_type === "call") return a.title || "Звонок клиента";
  return a.title || a.activity_type || "Событие";
}

export default function DealPage({params}:{params:Promise<{id:string}>}) {
  const {id}=use(params); const router=useRouter();
  const {selectedAccountId}=useAccounts();
  const [data,setData]=useState<any>(null); const [error,setError]=useState(""); const [saving,setSaving]=useState(false);
  const [amount,setAmount]=useState(""); const [responsible,setResponsible]=useState("");
  const [taskTitle,setTaskTitle]=useState(""); const [taskDue,setTaskDue]=useState("");

  async function load(){
    try{
      if(!selectedAccountId)return;
      setError("");
      const x=await api(`/api/crm/product/deals/${id}?account_id=${encodeURIComponent(selectedAccountId)}`);
      setData(x); setAmount(x.deal?.amount_kopeks ? String(Math.round(x.deal.amount_kopeks/100)) : ""); setResponsible(x.deal?.responsible_user_id ? String(x.deal.responsible_user_id) : "");
    }catch(e){setData(null);setError(e instanceof Error?e.message:"Ошибка системы продаж");}
  }
  useEffect(()=>{setData(null);setError("");void load();},[id,selectedAccountId]);

  const d=data?.deal; const openTasks=useMemo(()=>data?.tasks?.filter((t:any)=>t.status==="open")||[],[data]);
  const nextTask=openTasks[0];
  const recommendation=recommendedByStage(d?.stage_name,Boolean(d?.contact_phone));
  const nextAction = nextTask ? nextTask.title : d?.next_action_at ? "Запланировано следующее действие" : recommendation;
  const nextDue = nextTask?.due_at || d?.next_action_at;

  async function patchDeal(body:any){
    if(!selectedAccountId)return; setSaving(true);
    try{await api(`/api/crm/product/deals/${id}?account_id=${encodeURIComponent(selectedAccountId)}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});await load();}
    finally{setSaving(false);}
  }
  async function changeStage(stageId:number){
    const stage=data.stages.find((s:any)=>Number(s.id)===stageId); let lost_reason:string|undefined;
    if(stage?.semantic_type==="lost"){
      lost_reason=window.prompt("Почему сделка потеряна? Причина обязательна для аналитики.", d.lost_reason||"")?.trim()||"";
      if(!lost_reason)return;
    }
    setSaving(true);
    try{await api(`/api/crm/bridge/deal/${d.id}/stage`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({stage_id:stageId,lost_reason})});await load();}
    catch(e){setError(e instanceof Error?e.message:"Не удалось изменить этап");}
    finally{setSaving(false);}
  }
  async function createTask(){
    if(!selectedAccountId||!taskTitle.trim())return; setSaving(true);
    try{await api(`/api/crm/product/tasks?account_id=${encodeURIComponent(selectedAccountId)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({deal_id:Number(id),title:taskTitle.trim(),due_at:taskDue?new Date(taskDue).toISOString():null,assigned_user_id:responsible?Number(responsible):null})});setTaskTitle("");setTaskDue("");await load();}
    catch(e){setError(e instanceof Error?e.message:"Не удалось создать задачу");}
    finally{setSaving(false);}
  }
  async function completeTask(taskId:number){
    if(!selectedAccountId)return; await api(`/api/crm/product/tasks/${taskId}?account_id=${encodeURIComponent(selectedAccountId)}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:"done"})});await load();
  }
  async function createRecommendedTask(mode:"2h"|"tomorrow") {
    if(!selectedAccountId)return;
    const due=new Date();
    if(mode==="2h") due.setHours(due.getHours()+2); else { due.setDate(due.getDate()+1); due.setHours(10,0,0,0); }
    setSaving(true);
    try {
      await api(`/api/crm/product/tasks?account_id=${encodeURIComponent(selectedAccountId)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({deal_id:Number(id),title:recommendation,due_at:due.toISOString(),assigned_user_id:responsible?Number(responsible):null})});
      await load();
    } catch(e) { setError(e instanceof Error?e.message:"Не удалось поставить следующий шаг"); } finally { setSaving(false); }
  }

  if(!data){
    if(!error)return <div className="boris-product-route" style={{padding:24}}>Загрузка сделки…</div>;
    const view=crmHumanError(error,"deal");
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

  const statusLabel=d.status==="won"?"Выиграна":d.status==="lost"?"Потеряна":"В работе";
  const statusBg=d.status==="won"?"#ecfdf5":d.status==="lost"?"#fff1f2":"#eff6ff";
  const statusColor=d.status==="won"?"#047857":d.status==="lost"?"#b42318":"#1d4ed8";

  return <div className="boris-product-route boris-crm-deal-page" style={{minHeight:"100vh",background:"var(--boris-background)",padding:24,color:"var(--boris-text-primary)"}}>
    <div style={{maxWidth:1500,margin:"0 auto"}}>
      <div style={{display:"flex",justifyContent:"flex-end",gap:12,alignItems:"center",marginBottom:14,flexWrap:"wrap"}}>
        <div style={{display:"flex",gap:8,alignItems:"center"}}><span style={{padding:"7px 10px",borderRadius:999,background:statusBg,color:statusColor,fontSize:12,fontWeight:800}}>{statusLabel}</span><Button onClick={load} style={ghost}>Обновить</Button></div>
      </div>

      {error?<div style={{padding:10,borderRadius:10,background:"#fff1f2",color:"#b42318",marginBottom:12}}>{error}</div>:null}

      <div className="boris-crm-deal-layout" style={{display:"grid",gridTemplateColumns:"minmax(0,1fr) minmax(300px,380px)",gap:14,alignItems:"start"}}>
        <div style={{minWidth:0}}>
          <div className="boris-crm-detail-card boris-crm-deal-hero" style={card}>
            <div className="boris-crm-profile-head"><span className="boris-crm-profile-avatar boris-crm-profile-avatar-deal"><Icon name="briefcase" size={27}/></span><div style={{minWidth:0}}><div style={{fontSize:10,fontWeight:900,color:"#d97706",letterSpacing:'.06em'}}>СДЕЛКА #{d.id} · {d.pipeline_name||"Продажи"}</div><h1 style={{margin:"4px 0 3px",fontSize:25,lineHeight:1.2,overflowWrap:"anywhere"}}>{d.title}</h1><div style={{fontSize:13,color:"var(--boris-text-2)"}}>{d.contact_name||"Клиент"} · {d.source||"Источник не указан"}</div></div></div>
            <div className="boris-crm-stage-track" style={{display:"flex",gap:6,overflowX:"auto",padding:"12px 0 2px",marginTop:6}}>
              {data.stages.map((stage:any)=>{
                const active=Number(stage.id)===Number(d.stage_id);
                return <button key={stage.id} disabled={saving} onClick={()=>!active&&void changeStage(Number(stage.id))} className={`boris-crm-stage-step ${active?"boris-crm-stage-step-active":""} ${stage.semantic_type==="won"?"boris-crm-stage-step-won":stage.semantic_type==="lost"?"boris-crm-stage-step-lost":""}`}><span>{stage.name}</span></button>;
              })}
            </div>
            <div style={{marginTop:14,padding:14,borderRadius:12,background:nextDue&&new Date(nextDue)<new Date()?"#fff1f2":"#eff6ff",border:"1px solid var(--boris-border)"}}>
              <div style={{fontSize:10,fontWeight:900,letterSpacing:'.05em',color:nextDue&&new Date(nextDue)<new Date()?"#b42318":"#1d4ed8"}}>ЧТО СДЕЛАТЬ СЕЙЧАС</div>
              <div style={{fontSize:17,fontWeight:850,marginTop:4}}>{nextAction}</div>
              <div style={{fontSize:12,color:"var(--boris-text-2)",marginTop:3}}>{nextDue?`${new Date(nextDue)<new Date()?"Просрочено":"Срок"}: ${new Date(nextDue).toLocaleString("ru-RU")}`:"BORIS предлагает следующий логичный шаг по текущему этапу. Поставьте его в работу одним кликом."}</div>
              {!nextDue&&d.status==="open"?<div className="boris-crm-recommend-actions"><Button disabled={saving} onClick={()=>createRecommendedTask("2h")} style={primary}>Поставить через 2 часа</Button><Button disabled={saving} onClick={()=>createRecommendedTask("tomorrow")} style={ghost}>Завтра 10:00</Button></div>:null}
            </div>
            <div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:12}}>
              {data.conversation?<Button onClick={()=>router.push(`/messages?account_id=${encodeURIComponent(selectedAccountId||"")}&chat_id=${encodeURIComponent(data.conversation.external_chat_id||"")}`)} style={primary}>Открыть диалог</Button>:null}
              {d.contact_phone?<a href={`tel:${d.contact_phone}`} style={linkButton}>Позвонить {d.contact_phone}</a>:null}
              {d.contact_email?<a href={`mailto:${d.contact_email}`} style={linkButton}>Написать email</a>:null}
            </div>
          </div>

          <div className="boris-crm-detail-card" style={{...card,marginTop:14}}>
            <h3 style={{marginTop:0}}>История сделки</h3>
            <div style={{fontSize:12,color:"var(--boris-text-2)",marginBottom:10}}>Сообщения, смена этапов и действия менеджера — в одной хронологии.</div>
            {!data.activities.length?<div style={{color:"var(--boris-text-3)"}}>История пока пуста.</div>:data.activities.map((a:any)=>{const isCall=a.activity_type==="call";return <div key={a.id} style={{padding:"10px 0",borderBottom:"1px solid var(--boris-border-soft)"}}><div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"baseline"}}><div style={{display:"flex",alignItems:"center",gap:7}}>{isCall?<span style={{width:28,height:28,borderRadius:9,display:"grid",placeItems:"center",background:a.metadata_json?.missed?"#fff1f2":"#ecfdf5",color:a.metadata_json?.missed?"#be123c":"#047857"}}><Icon name="support" size={15}/></span>:null}<b style={{fontSize:13}}>{activityTitle(a)}</b></div><span style={{fontSize:11,color:"var(--boris-text-3)",whiteSpace:"nowrap"}}>{new Date(a.created_at).toLocaleString("ru-RU")}</span></div>{a.body?<div style={{fontSize:12,color:"var(--boris-text-2)",marginTop:4,whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{a.body}</div>:null}{isCall&&a.metadata_json?.analysis_available?<div style={{fontSize:11,color:"#2563eb",fontWeight:750,marginTop:4}}>Разбор разговора уже доступен в РОП → Звонки</div>:null}</div>})}
          </div>
        </div>

        <div style={{minWidth:0}}>
          <div className="boris-crm-detail-card" style={card}>
            <h3 style={{marginTop:0}}>Сделка</h3>
            <label style={label}>Этап</label>
            <select value={d.stage_id||""} disabled={saving} onChange={e=>changeStage(Number(e.target.value))} style={input}>{data.stages.map((s:any)=><option key={s.id} value={s.id}>{s.name}</option>)}</select>
            {d.lost_reason?<div style={{marginTop:8,padding:8,borderRadius:8,background:"#fff7ed",fontSize:12}}><b>Причина потери:</b> {d.lost_reason}</div>:null}

            <label style={labelBlock}>Ответственный</label>
            <select value={responsible} onChange={async e=>{const v=e.target.value;setResponsible(v);await patchDeal({responsible_user_id:v?Number(v):null});}} style={input}><option value="">Не назначен</option>{(data.team||[]).map((u:any)=><option key={u.id} value={u.id}>{u.email||`Сотрудник #${u.id}`}{u.role?` · ${u.role}`:""}</option>)}</select>

            <label style={labelBlock}>Сумма сделки, ₽</label>
            <div style={{display:"grid",gridTemplateColumns:"1fr auto",gap:6}}><input value={amount} onChange={e=>setAmount(e.target.value.replace(/[^0-9]/g,""))} placeholder="Введите сумму" style={input}/><Button disabled={saving} onClick={()=>patchDeal({amount_kopeks:amount?Number(amount)*100:0})} style={ghost}>Сохранить</Button></div>
            <div style={{fontSize:11,color:"var(--boris-text-3)",marginTop:4}}>Сейчас: {money(d.amount_kopeks)}</div>

            <label style={labelBlock}>Клиент</label>
            <Button onClick={()=>d.contact_id&&router.push(`/dashboard/crm/contact/${d.contact_id}`)} style={{...ghost,width:"100%",textAlign:"left"}}><b>{d.contact_name||"Клиент"}</b><br/><span style={{fontSize:12,color:"var(--boris-text-2)"}}>{d.contact_phone||"Телефон не получен"}{d.contact_email?` · ${d.contact_email}`:""}</span></Button>

            <div style={{marginTop:12,fontSize:11,color:"var(--boris-text-3)"}}>Источник: {d.source||"—"}{d.avito_item_id?` · объявление ${d.avito_item_id}`:""}</div>
          </div>

          <div className="boris-crm-detail-card" style={{...card,marginTop:14}}>
            <h3 style={{marginTop:0}}>Следующий шаг</h3>
            <div style={{fontSize:12,color:"var(--boris-text-2)",marginBottom:10}}>Каждая активная сделка должна иметь конкретное следующее действие и срок.</div>
            <input value={taskTitle} onChange={e=>setTaskTitle(e.target.value)} placeholder="Например: позвонить и согласовать замер" style={input}/>
            <input type="datetime-local" value={taskDue} onChange={e=>setTaskDue(e.target.value)} style={{...input,marginTop:7}}/>
            <Button disabled={saving||!taskTitle.trim()} onClick={createTask} style={{...primary,width:"100%",marginTop:8}}>Создать задачу</Button>

            <div style={{marginTop:14}}>{!data.tasks.length?<div style={{fontSize:12,color:"var(--boris-text-3)"}}>Задач пока нет.</div>:data.tasks.map((t:any)=><div key={t.id} style={{padding:"9px 0",borderTop:"1px solid var(--boris-border-soft)"}}><div style={{fontSize:13,fontWeight:750,textDecoration:t.status==="done"?"line-through":"none"}}>{t.title}</div><div style={{fontSize:11,color:t.status==="open"&&t.due_at&&new Date(t.due_at)<new Date()?"#b42318":"var(--boris-text-3)",marginTop:3}}>{t.status==="done"?"Выполнено":t.due_at?new Date(t.due_at).toLocaleString("ru-RU"):"Срок не задан"}</div>{t.status==="open"?<Button onClick={()=>completeTask(t.id)} style={{...ghost,marginTop:6,padding:"5px 8px"}}>Отметить выполненной</Button>:null}</div>)}</div>
          </div>
        </div>
      </div>
    </div>
  </div>;
}

const card:React.CSSProperties={background:"#fff",border:"1px solid var(--boris-border)",borderRadius:14,padding:16};
const ghost:React.CSSProperties={border:"1px solid var(--boris-border)",background:"#fff",borderRadius:9,padding:"8px 11px",cursor:"pointer"};
const primary:React.CSSProperties={border:0,background:"var(--boris-blue)",color:"#fff",borderRadius:9,padding:"9px 12px",cursor:"pointer",fontWeight:750};
const linkButton:React.CSSProperties={...ghost as any,textDecoration:"none",display:"inline-flex",alignItems:"center",fontSize:13,color:"var(--boris-text-primary)"};
const label:React.CSSProperties={display:"block",fontSize:11,color:"var(--boris-text-3)",fontWeight:800};
const labelBlock:React.CSSProperties={...label,marginTop:13};
const input:React.CSSProperties={width:"100%",minWidth:0,height:40,border:"1px solid var(--boris-border)",borderRadius:9,padding:"0 10px",marginTop:5,background:"#fff",boxSizing:"border-box"};
