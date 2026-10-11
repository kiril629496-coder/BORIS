"use client";
import {useEffect,useRef,useState} from "react";
import {createPortal} from "react-dom";

export const CARD_FIELD_LABELS:Record<string,string> = {
  title:"Название",description:"Описание",price:"Цена",city:"Город",address:"Адрес",
  category:"Категория",category_path:"Раздел Avito",template_id:"Подкатегория Avito",
  params:"Характеристики",images:"Фотографии",banner:"Баннер",banner_url:"Баннер",
  hero_media_id:"Главное фото",media:"Порядок фотографий",phone:"Телефон",
  manager:"Контактное лицо",delivery:"Доставка",
};
export function cardDraft(item:any) {
  const p=item?.payload||{};
  return {title:p.title||"",description:p.description||"",price:p.price??"",
    city:p.city||item?.geo||"",address:p.address||"",category:p.category||"",
    template_id:p.template_id||"",params:JSON.stringify(p.params||{},null,2),
    images:(p.images||[]).join("\n"),__updated_at:item?.updated_at||""};
}
export function draftContent(draft:any) {
  const {__updated_at,...content}=draft||{};
  return JSON.stringify(content);
}
export function mergeCardDraft(baseline:any,local:any,latest:any) {
  const merged={...latest};
  for(const key of Object.keys(local||{})) {
    if(key!=="__updated_at" && JSON.stringify(local[key])!==JSON.stringify(baseline?.[key])) merged[key]=local[key];
  }
  return merged;
}
export function readableCardValue(key:string,value:any):string {
  if(value===null||value===undefined||value==="") return "Не указано";
  if(key==="template_id") return "Подкатегория выбрана";
  if(key==="params"){
    let params=value;
    if(typeof params==="string"){try{params=JSON.parse(params);}catch{return "Характеристики изменены";}}
    return Object.values(params||{}).map(x=>Array.isArray(x)?x.join(", "):String(x)).join(" · ")||"Не указано";
  }
  if(key==="media") return (value||[]).map((x:any,i:number)=>"Фото "+(i+1)+(x.role==="hero"?" — главное":"")).join("; ")||"Нет фотографий";
  if(key==="hero_media_id") return value?"Главное фото выбрано":"Не выбрано";
  if(key==="images") return (Array.isArray(value)?value:value.split("\n")).filter(Boolean).length+" фото";
  if(key==="banner"||key==="banner_url") return value?"Баннер есть":"Баннера нет";
  if(typeof value==="object") return Array.isArray(value)?value.map(String).join(", "):"Значение изменено";
  return String(value).replace(/<[^>]*>/g," ");
}
export function CardComparison({before,after,keys,beforeLabel="Сохранено",afterLabel="Ваш вариант"}:{before:any;after:any;keys?:string[];beforeLabel?:string;afterLabel?:string}) {
  const fields=keys||Object.keys(CARD_FIELD_LABELS).filter(k=>JSON.stringify(before?.[k])!==JSON.stringify(after?.[k]));
  return <div style={{display:"grid",gap:10,overflowWrap:"anywhere"}}>
    {fields.length?fields.map(key=><div key={key} style={{border:"1px solid #e4e7ec",borderRadius:10,padding:10}}>
      <strong>{CARD_FIELD_LABELS[key]||"Поле карточки"}</strong>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(min(220px,100%),1fr))",gap:10,marginTop:8}}>
        <div><small>{beforeLabel}</small><div style={{whiteSpace:"pre-wrap"}}>{readableCardValue(key,before?.[key])}</div></div>
        <div><small>{afterLabel}</small><div style={{whiteSpace:"pre-wrap"}}>{readableCardValue(key,after?.[key])}</div></div>
      </div>
    </div>):<p>Текстовые поля совпадают. Могли измениться фотографии или результат проверки.</p>}
  </div>;
}
export function CampaignConflictDialog({conflict,onClose,onReload,onRebase}:{conflict:any;onClose:()=>void;onReload:()=>Promise<void>;onRebase:()=>void}) {
  const [compare,setCompare]=useState(false);
  const [notice,setNotice]=useState("");
  const root=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const previous=document.activeElement as HTMLElement|null;
    root.current?.focus();
    return()=>previous?.focus();
  },[]);
  if(typeof document==="undefined")return null;
  const local=conflict.local||{};
  const latest=conflict.latest?cardDraft(conflict.latest):{};
  return createPortal(<div style={{position:"fixed",inset:0,zIndex:100010,background:"rgba(15,23,42,.6)",display:"grid",placeItems:"center",padding:12}}>
    <div ref={root} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="campaign-conflict-heading"
      onKeyDown={e=>{
        if(e.key==="Escape"){e.preventDefault();onClose();}
        if(e.key==="Tab"){
          const nodes=Array.from(root.current?.querySelectorAll<HTMLElement>('button:not(:disabled),[href],input,textarea,[tabindex="0"]')||[]);
          const first=nodes[0],last=nodes[nodes.length-1];
          if(e.shiftKey&&(document.activeElement===first||document.activeElement===root.current)){e.preventDefault();last?.focus();}
          else if(!e.shiftKey&&(document.activeElement===last||document.activeElement===root.current)){e.preventDefault();first?.focus();}
        }
      }} style={{background:"#fff",width:"100%",maxWidth:780,maxHeight:"90dvh",overflow:"auto",boxSizing:"border-box",borderRadius:16,padding:18}}>
      <h2 id="campaign-conflict-heading">Есть более новая версия</h2>
      <p>Это объявление уже изменил другой сотрудник.</p>
      <div style={{display:"flex",gap:8,flexWrap:"wrap",marginBottom:12}}>
        <button type="button" onClick={async()=>{await onReload();setCompare(true);}}>Посмотреть изменения</button>
        <button type="button" onClick={()=>setCompare(true)}>Сравнить версии</button>
        <button type="button" onClick={async()=>{try{await navigator.clipboard.writeText(Object.keys(CARD_FIELD_LABELS).filter(k=>local[k]!==undefined).map(k=>CARD_FIELD_LABELS[k]+": "+readableCardValue(k,local[k])).join("\n\n"));setNotice("Черновик скопирован");}catch{setNotice("Не удалось скопировать. Ваш черновик остаётся в форме.");}}}>Скопировать мои изменения</button>
        <button type="button" onClick={onRebase} disabled={!conflict.latest}>Обновить карточку</button>
        <button type="button" onClick={onClose}>Закрыть</button>
      </div>
      {notice?<p role="status">{notice}</p>:null}
      {compare&&conflict.latest?<CardComparison before={latest} after={local}/>:null}
      {!conflict.latest?<p>Свежая версия ещё не загружена. Черновик остаётся у вас.</p>:null}
      <p>Ваш черновик сохранён. Обновление карточки сохранит ваши правки в форме.</p>
    </div>
  </div>,document.body);
}
