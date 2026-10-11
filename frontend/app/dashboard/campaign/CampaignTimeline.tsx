"use client";
import {useEffect,useRef,useState} from "react";
import BorisSelect from "./BorisSelect";
import {Button} from "@/app/ui";

export function historyDay(when:string,now=new Date()){
  const d=new Date(when),today=new Date(now.getFullYear(),now.getMonth(),now.getDate());
  const day=new Date(d.getFullYear(),d.getMonth(),d.getDate());
  if(day.getTime()===today.getTime())return "Сегодня";
  const yesterday=new Date(today);yesterday.setDate(today.getDate()-1);
  if(day.getTime()===yesterday.getTime())return "Вчера";
  return d.toLocaleDateString("ru-RU",{day:"numeric",month:"long",...(d.getFullYear()!==today.getFullYear()?{year:"numeric" as const}:{})});
}
const categories=[["all","Все"],["boris","BORIS"],["staff","Сотрудники"],["changes","Изменения"],["publications","Публикации"],["ab","A/B"],["errors","Ошибки"]].map(([value,label])=>({value,label}));
const periods=[["all","За всё время"],["today","Сегодня"],["7","7 дней"],["30","30 дней"],["custom","Произвольный период"]].map(([value,label])=>({value,label}));
export default function CampaignTimeline({accountId,available,legacy=[]}:{accountId:string;available:boolean;legacy:any[]}){
  const [q,setQ]=useState(""),[search,setSearch]=useState(""),[category,setCategory]=useState("all"),[period,setPeriod]=useState("all");
  const [start,setStart]=useState(""),[end,setEnd]=useState("");
  const [rows,setRows]=useState<any[]>([]),[cursor,setCursor]=useState<string|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState("");
  const epoch=useRef(0);
  const headers=()=>({Authorization:"Bearer "+(localStorage.getItem("boris_token")||"")});
  const query=()=>new URLSearchParams({account_id:accountId,q:search,category,period,start,end,tz:Intl.DateTimeFormat().resolvedOptions().timeZone||"Europe/Moscow"});
  useEffect(()=>{const t=setTimeout(()=>setSearch(q),300);return()=>clearTimeout(t);},[q]);
  async function load(next:string|null,version:number){
    setBusy(true);setError("");
    try{
      const params=query();if(next)params.set("cursor",next);
      const r=await fetch("/api/campaign/timeline/search?"+params,{headers:headers(),cache:"no-store"});
      if(!r.ok)throw new Error("Не удалось загрузить историю");
      const d=await r.json();if(version!==epoch.current)return;
      setRows(old=>next?[...old,...d.items]:d.items);setCursor(d.next_cursor||null);
    }catch(e){if(version===epoch.current)setError(e instanceof Error?e.message:"Не удалось загрузить историю");}
    finally{if(version===epoch.current)setBusy(false);}
  }
  useEffect(()=>{
    const version=++epoch.current;setRows([]);setCursor(null);setError("");
    if(available&&accountId&&(period!=="custom"||(start&&end)))void load(null,version);
    return()=>{epoch.current++;};
  },[accountId,available,search,category,period,start,end]);
  async function download(){
    const version=epoch.current;setBusy(true);setError("");
    try{
      const r=await fetch("/api/campaign/timeline/export?"+query(),{headers:headers(),cache:"no-store"});
      if(!r.ok)throw new Error("Не удалось скачать историю");
      const blob=await r.blob();if(version!==epoch.current)return;
      const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="История объявлений.csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(e){if(version===epoch.current)setError(e instanceof Error?e.message:"Не удалось скачать историю");}
    finally{if(version===epoch.current)setBusy(false);}
  }
  const items=available?rows:legacy;
  return <section aria-label="История действий">
    <h3>История действий</h3>
    {available?<div style={{display:"grid",gap:10,gridTemplateColumns:"repeat(auto-fit,minmax(min(100%,200px),1fr))"}}>
      <input aria-label="Поиск по объявлению, проекту, сотруднику или действию" placeholder="Поиск в истории" value={q} onChange={e=>setQ(e.target.value)} style={{minWidth:0,padding:10,border:"1px solid #D0D5DD",borderRadius:10,font:"inherit"}}/>
      <BorisSelect value={category} options={categories} placeholder="Тип события" onChange={setCategory}/>
      <BorisSelect value={period} options={periods} placeholder="Период" onChange={setPeriod}/>
      {period==="custom"?<><label>С <input type="date" value={start} onChange={e=>setStart(e.target.value)} style={{maxWidth:"100%"}}/></label><label>По <input type="date" value={end} onChange={e=>setEnd(e.target.value)} style={{maxWidth:"100%"}}/></label></>:null}
      <Button kind="ghost" onClick={()=>void download()} disabled={busy||(period==="custom"&&(!start||!end))}>Скачать историю · CSV</Button>
    </div>:null}
    {error?<p role="alert">{error}</p>:null}
    {items.map((item,index)=>{
      const group=item.when?historyDay(item.when):"Дата не сохранена";
      const previous=index&&items[index-1].when?historyDay(items[index-1].when):"";
      return <div key={item.id}>
        {group!==previous?<h4>{group}</h4>:null}
        <details style={{border:"1px solid #E7EAF0",borderRadius:12,padding:12,marginBottom:8,overflowWrap:"anywhere"}}>
          <summary style={{cursor:"pointer"}}><strong>{item.action||"Действие по объявлениям"}</strong><br/>
            <small>{item.object_name} · {item.actor||"Сотрудник"}{item.when?" · "+new Date(item.when).toLocaleTimeString("ru-RU"):""}</small>
          </summary>
          {item.duration_ms!=null?<p>{"выполнялось "+(Number(item.duration_ms)/1000).toLocaleString("ru-RU",{maximumFractionDigits:1})+" сек"}</p>:null}
          {item.project?<p>Проект: {item.project}</p>:null}
          {item.before?<p>Было: {item.before}</p>:null}{item.after?<p>Стало: {item.after}</p>:null}
          {!item.before&&!item.after?<p>Для этого исторического события детальное сравнение не сохранялось.</p>:null}
          {item.reason?<p>Причина: {item.reason}</p>:null}
        </details>
      </div>;
    })}
    {!items.length&&!busy?<p>Событий по выбранным условиям пока нет.</p>:null}
    {busy?<p role="status">Загружаем историю…</p>:null}
    {cursor?<Button kind="ghost" disabled={busy} onClick={()=>void load(cursor,epoch.current)}>Показать ещё</Button>:null}
  </section>;
}
