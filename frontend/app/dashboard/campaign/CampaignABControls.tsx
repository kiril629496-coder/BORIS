"use client";
import {useEffect,useRef,useState} from "react";
import {createPortal} from "react-dom";
import {Button,Card} from "@/app/ui";

export default function CampaignABControls({row,accountId,onChanged}:{row:any;accountId:string;onChanged:()=>void}){
 const [open,setOpen]=useState(false),[prompt,setPrompt]=useState(""),[error,setError]=useState(""),[notice,setNotice]=useState(""),[busy,setBusy]=useState(false);
 const [journal,setJournal]=useState<any>(null);
 const [version,setVersion]=useState(row.updated_at);
 const dialog=useRef<HTMLDivElement>(null),live=useRef(true);
 useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
 useEffect(()=>{if(open)dialog.current?.querySelector("textarea")?.focus();},[open]);
 const headers=()=>({Authorization:"Bearer "+(localStorage.getItem("boris_token")||""),"Content-Type":"application/json"});
 async function act(action:string){
  setBusy(true);setError("");setNotice("");
  try{
   const r=await fetch("/api/campaign/ab-tests/action",{method:"POST",headers:headers(),body:JSON.stringify({account_id:accountId,item_id:row.item_id,action,prompt,expected_updated_at:action==="edit"?version:row.updated_at})});
   const d=await r.json();if(!live.current)return;
   if(!r.ok||d.status==="blocked"||(action==="edit"&&!d.job))throw new Error(d.detail?.message||d.message||(typeof d.detail==="string"?d.detail:"Не удалось выполнить действие"));
   if(action==="edit"){setOpen(false);setPrompt("");}
   setNotice(d.message||"Изменения сохранены");onChanged();
  }catch(e){if(live.current)setError(e instanceof Error?e.message:"Не удалось выполнить действие");}
  finally{if(live.current)setBusy(false);}
 }
 async function history(cursor=0){
  setError("");
  try{
   const r=await fetch("/api/campaign/ab-tests/"+row.item_id+"/journal?account_id="+encodeURIComponent(accountId)+"&cursor="+cursor,{headers:headers(),cache:"no-store"});
   if(!r.ok)throw new Error("Не удалось загрузить журнал");
   const d=await r.json();if(!live.current)return;
   setJournal((old:any)=>({...d,items:cursor?[...(old?.items||[]),...d.items]:d.items,legacy:cursor?old?.legacy||[]:d.legacy||[],observations:cursor?old?.observations||[]:d.observations||[]}));
  }catch(e){if(live.current)setError(e instanceof Error?e.message:"Не удалось загрузить журнал");}
 }
 return <div style={{marginTop:12}}>
  <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
   <Button kind="ghost" disabled={busy} onClick={()=>{setVersion(row.updated_at);setOpen(true);}}>Изменить через BORIS</Button>
   <Button kind="ghost" disabled={busy} onClick={()=>void act(row.experiment_state==="paused"?"resume":"pause")}>{row.experiment_state==="paused"?"Возобновить тест":"Остановить тест"}</Button>
   <Button kind="ghost" onClick={()=>void history()}>Журнал теста</Button>
  </div>
  {error&&!open?<p role="alert">{error}</p>:null}
  {notice?<p role="status">{notice} <a href="/dashboard/campaign?tab=tasks">Задачи</a></p>:null}
  {journal?<section aria-label="Журнал теста" style={{overflowWrap:"anywhere"}}>
   <h4>Журнал теста</h4>
   <p>{journal.participation==="paused"?"Тест остановлен":"Участвует в тестировании"}{journal.hypothesis?" · "+journal.hypothesis:""}{journal.formula?" · "+journal.formula:""}</p>
   {(journal.observations||[]).map((m:any)=><p key={m.date}>{m.date.slice(0,10)}: просмотры {m.views}, обращения {m.contacts}{m.conversion!==null?", конверсия "+m.conversion+"%":""}</p>)}
   {[...journal.items,...journal.legacy].map((e:any,i:number)=><p key={e.id||i}>{e.when?new Date(e.when).toLocaleString("ru-RU"):"Дата не сохранена"} · {e.actor}<br/>{e.action}{e.hypothesis?" · "+e.hypothesis:""}{e.formula?" · "+e.formula:""}{e.reason?" · "+e.reason:""}</p>)}
   {!journal.items.length&&!journal.legacy.length?<p>Для прежних действий подробный журнал не сохранялся.</p>:null}
   {journal.next_cursor?<Button kind="ghost" onClick={()=>void history(journal.next_cursor)}>Показать ещё</Button>:null}
  </section>:null}
  {open?createPortal(<div ref={dialog} role="dialog" aria-modal="true" aria-labelledby={"ab-edit-title-"+row.item_id} onKeyDown={e=>{
    if(e.key==="Escape"&&!busy)setOpen(false);
    if(e.key==="Tab"){
      const nodes=dialog.current?.querySelectorAll<HTMLElement>("textarea,button:not(:disabled),a[href]");if(!nodes?.length)return;
      const first=nodes[0],last=nodes[nodes.length-1];
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
    }
   }} style={{position:"fixed",inset:0,zIndex:1600,background:"rgba(15,23,42,.36)",display:"grid",placeItems:"center",padding:16}}>
   <Card style={{width:"min(560px,100%)",maxHeight:"90dvh",overflowY:"auto"}}>
    <h3 id={"ab-edit-title-"+row.item_id}>Изменить через BORIS</h3>
    <label style={{display:"grid",gap:8}}>Что изменить?<textarea value={prompt} onChange={e=>setPrompt(e.target.value)} rows={5} style={{width:"100%",boxSizing:"border-box",font:"inherit"}}/></label>
    {error?<p role="alert">{error} Ваш текст остаётся в форме. <a href={"/dashboard/campaign/"+row.campaign_id+"?edit="+row.item_id+"&tab=history#item-"+row.item_id}>Посмотреть изменения</a></p>:null}
    <div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:12}}>
     <Button kind="ghost" disabled={busy} onClick={()=>setOpen(false)}>Закрыть</Button>
     <Button disabled={busy||!prompt.trim()} onClick={()=>void act("edit")}>Сохранить задачу</Button>
    </div>
   </Card>
  </div>,document.body):null}
 </div>;
}
