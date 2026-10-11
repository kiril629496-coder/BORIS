"use client";
import {useEffect,useState} from "react";
import {apiGet} from "../../lib/api";
import {CardComparison,CARD_FIELD_LABELS} from "./CampaignConflictDialog";

export default function CampaignCardHistory({available=false,campaignId,item,onMutation,onSaved,disabled=false}:{available?:boolean;campaignId:string;item:any;onMutation:(url:string,body:any)=>Promise<any>;onSaved:()=>Promise<void>;disabled?:boolean}) {
  const [entries,setEntries]=useState<any[]>([]);
  const [legacy,setLegacy]=useState<any[]>([]);
  const [cursor,setCursor]=useState<number|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [preview,setPreview]=useState<any>(null);
  const [expanded,setExpanded]=useState("");
  const [timeline,setTimeline]=useState<any>(null);
  async function load(next=0){
    if(!available){
      setEntries([]);setCursor(null);
      setLegacy([...(item.payload?.edit_history||[])].reverse().map((old:any)=>({created_at:old.at,actor:old.actor,reason:old.note,message:"Полный снимок до и после для этой старой записи не сохранён."})));
      return;
    }
    setBusy(true);setError("");
    try{
      const result=await apiGet("/api/campaign/"+campaignId+"/item/"+item.id+"/revisions?limit=30&cursor="+next);
      if(result?.status!=="ok")throw new Error(result?.message||"Не удалось загрузить историю");
      setEntries(old=>next?[...old,...result.items]:result.items);
      if(!next)setLegacy(result.legacy||[]);
      setCursor(result.next_cursor??null);
    }catch(e:any){setError(e.message);}finally{setBusy(false);}
  }
  async function loadTimeline(){
    try{
      const acc=String(item?.account_id||"").trim();
      if(!acc){setTimeline(null);return;}
      const result=await apiGet("/api/accounts/campaign-item-timeline?account_id="+encodeURIComponent(acc)+"&campaign_id="+encodeURIComponent(campaignId)+"&item_id="+encodeURIComponent(String(item.id)));
      setTimeline(result?.status==="ok"?result:null);
    }catch{
      setTimeline(null);
    }
  }
  useEffect(()=>{void load();void loadTimeline();},[available,campaignId,item.id,item?.account_id]);
  async function prepare(record:any,side:string){
    setBusy(true);setError("");
    try{
      const url="/api/campaign/"+campaignId+"/item/"+item.id+"/revisions/"+record.revision+"/restore";
      const result=await onMutation(url,{preview_only:true,side});
      setPreview({...result,url,side});
    }catch(e:any){setError(e.message);}finally{setBusy(false);}
  }
  async function restore(){
    setBusy(true);setError("");
    try{
      await onMutation(preview.url,{preview_only:false,confirmed:true,side:preview.side,expected_updated_at:preview.expected_updated_at});
      setPreview(null);await onSaved();await load();
    }catch(e:any){setError(e.message);}finally{setBusy(false);}
  }
  return <div style={{display:"grid",gap:12}}>
    <strong>История изменений карточки</strong>
    <p>Сохранённые изменения текста, цены, категории и фотографий. Откат меняет карточку в BORIS; размещение требует отдельной проверки и подтверждения.</p>
    {timeline?<section data-testid="campaign-item-timeline" style={{border:"1px solid #dbe5f2",borderRadius:14,padding:14,background:"#fbfdff",display:"grid",gap:12}}>
      <div>
        <strong>Путь объявления</strong>
        <p style={{margin:"4px 0 0",fontSize:12,color:"#667085"}}>Только события с сохранённым временем. Текущее состояние показывается отдельно и не выдаётся за историческое событие.</p>
      </div>
      <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
        {[
          ["text_ready","Текст"],
          ["price_ready","Цена"],
          ["category_ready","Категория"],
          ["media_ready","Фото"],
          ["banner_ready","Баннер"],
          ["banner_qa_pass","QA баннера"],
          ["feed_identity_ready","Feed"],
          ["live","Avito LIVE"],
        ].map(([key,label])=>{
          const ready=Boolean(timeline?.current?.milestones?.[key]);
          return <span key={key} style={{fontSize:11.5,fontWeight:800,padding:"5px 8px",borderRadius:999,border:"1px solid "+(ready?"#ABEFC6":"#E4E7EC"),background:ready?"#ECFDF3":"#F9FAFB",color:ready?"#067647":"#667085"}}>{ready?"✓ ":""}{label}</span>;
        })}
      </div>
      <div style={{display:"grid",gap:8}}>
        {(timeline.events||[]).map((entry:any,index:number)=><div key={(entry.revision||entry.event)+"-"+index} style={{display:"grid",gridTemplateColumns:"118px minmax(0,1fr)",gap:10,alignItems:"start"}}>
          <div style={{fontSize:11,color:"#667085",paddingTop:2}}>{entry.timestamp?new Date(entry.timestamp).toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}):"—"}</div>
          <div style={{borderLeft:"2px solid #D6E4FF",paddingLeft:10}}>
            <div style={{fontSize:12.5,fontWeight:850,color:"#172033"}}>{entry.title}</div>
            {entry.detail?<div style={{fontSize:11.5,color:"#667085",marginTop:2}}>{entry.detail}</div>:null}
            {entry.actor&&entry.actor!=="BORIS"?<div style={{fontSize:11,color:"#98A2B3",marginTop:2}}>{entry.actor}</div>:null}
          </div>
        </div>)}
      </div>
      {timeline.technical?<details>
        <summary style={{cursor:"pointer",fontSize:12,fontWeight:850,color:"#344054"}}>Технические данные владельца</summary>
        <div style={{display:"grid",gap:10,marginTop:10,fontSize:11.5,color:"#475467"}}>
          <div><b>Jobs:</b> {(timeline.technical.jobs||[]).length} · <b>Media:</b> {(timeline.technical.media||[]).length} · <b>AI-вызовы:</b> {(timeline.technical.provider_usage||[]).length}</div>
          {(timeline.technical.jobs||[]).slice(-8).map((job:any)=><div key={"job-"+job.job_id} style={{padding:"8px 9px",border:"1px solid #E4E7EC",borderRadius:9,background:"#fff"}}>
            Job #{job.job_id} · {job.kind} · {job.status}{job.stage?" · "+job.stage:""}{job.attempts>1?" · попыток "+job.attempts:""}
          </div>)}
          {(timeline.technical.provider_usage||[]).slice(-8).map((usage:any)=><div key={"usage-"+usage.id} style={{padding:"8px 9px",border:"1px solid #E4E7EC",borderRadius:9,background:"#fff",overflowWrap:"anywhere"}}>
            {usage.operation} · {usage.provider}{usage.model?" / "+usage.model:""} · {Number(usage.cost_rub||0).toFixed(4)} ₽{usage.request_id?<><br/><span style={{color:"#98A2B3"}}>request: {usage.request_id}</span></>:null}
          </div>)}
          {(timeline.technical.media||[]).slice(0,12).map((media:any)=><div key={"media-"+media.media_id} style={{padding:"8px 9px",border:"1px solid #E4E7EC",borderRadius:9,background:"#fff",overflowWrap:"anywhere"}}>
            Media #{media.media_id} · {media.role} · {media.source_provider||"source"}{media.sha256?<><br/><span style={{color:"#98A2B3"}}>SHA: {String(media.sha256).slice(0,24)}…</span></>:null}
          </div>)}
        </div>
      </details>:null}
    </section>:null}
    {error?<p role="alert">{error}</p>:null}
    {preview?<section aria-label="Предпросмотр отката" style={{border:"2px solid #315bea",borderRadius:12,padding:12}}>
      <h3>Что изменится при откате</h3>
      <CardComparison beforeLabel="Сейчас" afterLabel="После отката" before={preview.before} after={preview.after} keys={preview.changed_fields}/>
      <p>{preview.message}</p>
      {preview.unavailable_media?.length?<p>Часть фотографий больше недоступна. Откат этой версии заблокирован.</p>:null}
      <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
        <button type="button" onClick={()=>setPreview(null)}>Отмена</button>
        <button type="button" disabled={disabled||busy||!!preview.unavailable_media?.length} onClick={()=>void restore()}>Подтвердить откат в BORIS</button>
      </div>
    </section>:null}
    {entries.map(entry=><section key={entry.revision} style={{border:"1px solid #e4e7ec",borderRadius:10,padding:12,overflowWrap:"anywhere"}}>
      <strong>{entry.reason||"Изменение карточки"}</strong>
      <p>{entry.actor||"Система"} · {new Date(entry.created_at).toLocaleString("ru-RU")} · {Number(entry.duration_ms)<1000?entry.duration_ms+" мс":(entry.duration_ms/1000).toFixed(1)+" с"}</p>
      <p>{entry.changed_fields.map((key:string)=>CARD_FIELD_LABELS[key]||"Поле карточки").join(", ")}</p>
      <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
        <button type="button" onClick={()=>setExpanded(expanded===entry.revision?"":entry.revision)}>До и после</button>
        <button type="button" disabled={disabled||busy} onClick={()=>void prepare(entry,"before")}>Вернуть состояние до правки</button>
        <button type="button" disabled={disabled||busy} onClick={()=>void prepare(entry,"after")}>Вернуть эту версию</button>
      </div>
      {expanded===entry.revision?<CardComparison beforeLabel="До" afterLabel="После" before={entry.before} after={entry.after} keys={entry.changed_fields}/>:null}
    </section>)}
    {cursor?<button type="button" disabled={busy} onClick={()=>void load(cursor)}>Загрузить ещё</button>:null}
    {legacy.map((entry,index)=><section key={"legacy-"+index} style={{border:"1px solid #e4e7ec",borderRadius:10,padding:12}}>
      <strong>{entry.reason||"Ранее сохранённое изменение"}</strong>
      <p>{entry.created_at?new Date(entry.created_at).toLocaleString("ru-RU"):"Дата не сохранена"} · {entry.actor||"Автор не сохранён"}</p>
      <p>{entry.message}</p>
    </section>)}
    {!busy&&!entries.length&&!legacy.length?<p>Сохранённых изменений пока нет.</p>:null}
    {busy?<p role="status">Загружаю…</p>:null}
  </div>;
}
