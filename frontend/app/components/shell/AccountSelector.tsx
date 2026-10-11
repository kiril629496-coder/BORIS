"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

import {
  apiGet,
  getAccount,
  getRole,
  setAccount,
} from "../../lib/api";

import css from "./shell.module.css";

type Props = {
  account: string;
  onAccountChange: (id: string) => void;
};

function getId(a: any): string {
  return String(a?.account_id ?? a?.id ?? "");
}

function getName(a: any): string {
  return String(a?.name ?? a?.account_name ?? a?.title ?? getId(a));
}

function isInternal(a: any): boolean {
  const id=getId(a).trim().toLowerCase();
  const name=getName(a).trim().toLowerCase();
  return /^(?:qa|test|demo|fixture|sandbox|mock)(?:[_-]|\s|$)/i.test(id)
    || /^(?:qa|test|demo|fixture|sandbox|mock)(?:[_-]|\s|$)/i.test(name);
}

function accountRole(a:any): string {
  const role=String(a?.role||"").toLowerCase();
  return role==="owner"?"Руководитель":role==="employee"?"Сотрудник":role==="manager"?"Менеджер":role==="viewer"?"Просмотр":"";
}

export default function AccountSelector({ account, onAccountChange }: Props) {
  const [accounts, setAccounts] = useState<any[]>([]);
  const [open,setOpen]=useState(false);
  const [query,setQuery]=useState("");
  const [mode,setMode]=useState<"work"|"all"|"qa">("work");
  const [activeIndex,setActiveIndex]=useState(0);
  const rootRef=useRef<HTMLDivElement|null>(null);

  useEffect(() => {
    let alive = true;
    apiGet("/api/accounts/list").then((d:any)=>{
      if(!alive)return;
      const list=Array.isArray(d)?d:Array.isArray(d?.accounts)?d.accounts:[];
      const seen=new Set<string>();
      const clean=list.filter((a:any)=>{
        const id=getId(a);
        if(!id||seen.has(id))return false;
        seen.add(id); return true;
      });
      setAccounts(clean);
      if (!account && clean.length > 0) {
        const stored=getAccount();
        const storedExists=Boolean(stored)&&clean.some((a:any)=>getId(a)===String(stored));
        const firstWork=clean.find((a:any)=>!isInternal(a));
        const selected=storedExists?String(stored):getId(firstWork||clean[0]);
        if(selected){setAccount(selected);onAccountChange(selected);}
      }
    }).catch(()=>{});
    return()=>{alive=false;};
  }, [account,onAccountChange]);

  useEffect(()=>{
    if(!open)return;
    const onDoc=(event:MouseEvent)=>{
      if(rootRef.current&&!rootRef.current.contains(event.target as Node))setOpen(false);
    };
    const onKey=(event:globalThis.KeyboardEvent)=>{
      if(event.key==="Escape")setOpen(false);
    };
    document.addEventListener("mousedown",onDoc);
    document.addEventListener("keydown",onKey);
    return()=>{document.removeEventListener("mousedown",onDoc);document.removeEventListener("keydown",onKey);};
  },[open]);

  const current=accounts.find(a=>getId(a)===account)||null;
  const role=String(getRole()||"").toLowerCase();
  const maySeeQa=role==="owner"||role==="manager";
  const filtered=useMemo(()=>{
    const q=query.trim().toLowerCase();
    const rows=accounts.filter((a:any)=>{
      const internal=isInternal(a);
      if(mode==="work"&&internal)return false;
      if(mode==="qa"&&!internal)return false;
      if(!maySeeQa&&internal)return false;
      if(!q)return true;
      return (getName(a)+" "+getId(a)+" "+String(a?.company_niche||"")).toLowerCase().includes(q);
    });
    rows.sort((a:any,b:any)=>{
      const aa=getId(a)===account?0:1;
      const bb=getId(b)===account?0:1;
      if(aa!==bb)return aa-bb;
      const ai=isInternal(a)?1:0, bi=isInternal(b)?1:0;
      if(ai!==bi)return ai-bi;
      return getName(a).localeCompare(getName(b),"ru");
    });
    return rows;
  },[accounts,mode,query,maySeeQa,account]);

  useEffect(()=>{setActiveIndex(0);},[query,mode,open,account]);

  if(accounts.length<=1)return null;

  const choose=(id:string)=>{
    setAccount(id);
    onAccountChange(id);
    setOpen(false);
    setQuery("");
    setActiveIndex(0);
  };

  const onPickerKeyDown=(event:ReactKeyboardEvent)=>{
    if(!open)return;
    if(event.key==="ArrowDown"){
      event.preventDefault();
      setActiveIndex((i)=>Math.min(Math.max(0,filtered.length-1),i+1));
    } else if(event.key==="ArrowUp"){
      event.preventDefault();
      setActiveIndex((i)=>Math.max(0,i-1));
    } else if(event.key==="Home"){
      event.preventDefault();setActiveIndex(0);
    } else if(event.key==="End"){
      event.preventDefault();setActiveIndex(Math.max(0,filtered.length-1));
    } else if(event.key==="Enter" && filtered[activeIndex]){
      event.preventDefault();choose(getId(filtered[activeIndex]));
    }
  };

  return (
    <div className={css.accountPicker} ref={rootRef}>
      <span className={css.accountLabel}>Аккаунт</span>
      <button type="button" className={css.accountPickerButton} aria-haspopup="listbox" aria-expanded={open}
              onClick={()=>setOpen(v=>!v)}>
        <span className={css.accountPickerMain}>
          <span className={css.accountPickerName}>{current?getName(current):"Выберите аккаунт"}</span>
          <span className={css.accountPickerMeta}>{current?(accountRole(current)||String(current?.company_niche||"Рабочий аккаунт")):""}</span>
        </span>
        <span className={css.accountPickerChevron} data-open={open?"1":"0"}>⌄</span>
      </button>
      {open ? <div className={css.accountPickerPopover}>
        <div className={css.accountPickerSearchWrap}>
          <span aria-hidden="true">⌕</span>
          <input autoFocus value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={onPickerKeyDown}
                 placeholder="Найти аккаунт…" aria-label="Поиск аккаунта" className={css.accountPickerSearch}/>
        </div>
        {maySeeQa ? <div className={css.accountPickerFilters}>
          {([
            ["work","Рабочие"],["all","Все"],["qa","QA"]
          ] as const).map(([key,label])=><button key={key} type="button" data-active={mode===key?"1":"0"} onClick={()=>setMode(key)}>{label}</button>)}
        </div> : null}
        <div className={css.accountPickerList} role="listbox" aria-label="Доступные аккаунты">
          {!filtered.length ? <div className={css.accountPickerEmpty}>Ничего не найдено</div> : filtered.map((a:any,index:number)=>{
            const id=getId(a); const active=id===account; const internal=isInternal(a); const focused=index===activeIndex;
            const connected=a?.avito_connected===true||a?.has_avito===true||a?.avito_status==="connected";
            return <button key={id} type="button" role="option" aria-selected={active}
                           className={css.accountPickerItem} data-active={active?"1":"0"} data-focused={focused?"1":"0"}
                           onMouseEnter={()=>setActiveIndex(index)} onFocus={()=>setActiveIndex(index)} onClick={()=>choose(id)}>
              <span className={css.accountPickerAvatar}>{getName(a).trim().slice(0,1).toUpperCase()||"Б"}</span>
              <span className={css.accountPickerItemText}>
                <strong>{getName(a)}</strong>
                <small>{[accountRole(a),String(a?.company_niche||"").trim()].filter(Boolean).join(" · ")||"Рабочий аккаунт"}</small>
              </span>
              <span className={css.accountPickerBadges}>
                {internal?<em data-tone="qa">QA</em>:<em data-tone="active">Активен</em>}
                {connected?<em data-tone="avito">Avito</em>:null}
              </span>
              {active?<span className={css.accountPickerCheck}>✓</span>:null}
            </button>;
          })}
        </div>
      </div> : null}
    </div>
  );
}
