"use client";
import {useEffect,useMemo,useRef,useState} from "react";

export type BorisSelectOption={value:string;label:string;disabled?:boolean};
export type BorisSelectProps={value:string;options:BorisSelectOption[];placeholder:string;onChange:(value:string)=>void;ariaLabel?:string};
export default function BorisSelect({value,options,placeholder,onChange,ariaLabel}:BorisSelectProps){
  const [open,setOpen]=useState(false);
  const [query,setQuery]=useState("");
  const [active,setActive]=useState(0);
  const root=useRef<HTMLDivElement|null>(null);
  const filtered=useMemo(()=>{
    const q=query.trim().toLowerCase();
    return options.filter((o)=>!q || (o.label+" "+o.value).toLowerCase().includes(q));
  },[options,query]);
  useEffect(()=>{
    if(!open)return;
    const click=(e:MouseEvent)=>{if(root.current&&!root.current.contains(e.target as Node))setOpen(false);};
    const key=(e:KeyboardEvent)=>{if(e.key==="Escape")setOpen(false);};
    document.addEventListener("mousedown",click);document.addEventListener("keydown",key);
    return()=>{document.removeEventListener("mousedown",click);document.removeEventListener("keydown",key);};
  },[open]);
  useEffect(()=>{setActive(Math.max(0,filtered.findIndex(o=>!o.disabled)));},[query,open]);
  useEffect(()=>{if(open)root.current?.querySelectorAll('[role="option"]')[active]?.scrollIntoView({block:"nearest"});},[active,open]);
  const selected=options.find((o)=>o.value===value);
  return <div ref={root} onKeyDown={(e)=>{
    if(e.key==="Escape"){e.preventDefault();setOpen(false);root.current?.querySelector("button")?.focus();return;}
    if(!open){if(e.key==="ArrowDown"||e.key==="ArrowUp"){e.preventDefault();setOpen(true);}return;}
    if(e.key==="Tab"){setOpen(false);return;}
    if(e.key==="ArrowDown"||e.key==="ArrowUp"){
      e.preventDefault();const step=e.key==="ArrowDown"?1:-1;
      const enabled=filtered.map((o,i)=>!o.disabled?i:-1).filter(i=>i>=0);
      if(enabled.length){const at=enabled.indexOf(active);setActive(enabled[(at+step+enabled.length)%enabled.length]);}
    }else if(e.key==="Enter"&&filtered[active]&&!filtered[active].disabled){
      e.preventDefault();onChange(filtered[active].value);setOpen(false);setQuery("");root.current?.querySelector("button")?.focus();
    }
  }} style={{position:"relative",minWidth:0}}>
    <button type="button" aria-haspopup="listbox" aria-expanded={open} aria-label={ariaLabel||placeholder}
      onClick={()=>setOpen(v=>!v)}

      style={{width:"100%",minHeight:42,border:"1px solid #D0D5DD",borderRadius:11,background:"#fff",padding:"9px 11px",display:"grid",gridTemplateColumns:"minmax(0,1fr) 20px",alignItems:"center",gap:8,textAlign:"left",font:"inherit",color:selected?"#172033":"#667085",cursor:"pointer",boxShadow:open?"0 0 0 3px rgba(49,91,234,.10)":"0 1px 2px rgba(16,24,40,.03)"}}>
      <span style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"normal",overflowWrap:"anywhere"}}>{selected?.label||placeholder}</span>
      <span aria-hidden="true" style={{transform:open?"rotate(180deg)":"none",transition:"transform .15s"}}>⌄</span>
    </button>
    {open?<div role="listbox" style={{position:"absolute",zIndex:60,left:0,right:0,top:"calc(100% + 6px)",maxHeight:300,overflow:"auto",background:"#fff",border:"1px solid #E4E7EC",borderRadius:13,boxShadow:"0 16px 40px rgba(15,23,42,.16)",padding:6}}>
      {options.length>7?<input autoFocus value={query} onChange={e=>setQuery(e.target.value)} placeholder="Найти вариант…" aria-label="Поиск по вариантам" style={{width:"100%",boxSizing:"border-box",border:"1px solid #D0D5DD",borderRadius:9,padding:"8px 9px",marginBottom:6,font:"inherit"}}/>:null}
      {filtered.length?filtered.map((o,index)=><button key={o.value||"__empty"} type="button" role="option" aria-selected={o.value===value} disabled={o.disabled} onMouseEnter={()=>setActive(index)} onClick={()=>{if(o.disabled)return;onChange(o.value);setOpen(false);setQuery("");}} style={{width:"100%",border:0,borderRadius:9,padding:"9px 10px",background:o.value===value?"#EEF4FF":index===active?"#F8FAFC":"#fff",display:"grid",gridTemplateColumns:"minmax(0,1fr) 20px",gap:8,textAlign:"left",font:"inherit",color:o.disabled?"#98A2B3":"#344054",cursor:o.disabled?"not-allowed":"pointer"}}><span style={{whiteSpace:"normal",overflowWrap:"anywhere"}}>{o.label}</span><b style={{color:"#315BEA"}}>{o.value===value?"✓":""}</b></button>):<div style={{padding:"10px",fontSize:12,color:"#667085"}}>Ничего не найдено</div>}
    </div>:null}
  </div>;
}
