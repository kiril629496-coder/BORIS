"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, EmptyState, Icon } from "@/app/ui";
import { color, space } from "@/app/ui/tokens";
import { useAccounts } from "@/app/lib/AccountContext";

type Row={item_id:number;campaign_id:number;avito_item_id?:string;title:string;description:string;banner_url?:string;views:number;contacts:number;conversion:number;avito_status:string};
function headers(){const t=typeof window==="undefined"?"":localStorage.getItem("boris_token")||"";return {Authorization:`Bearer ${t}`};}

export default function AvitoABTestsPanel(){
 const {selectedAccountId}=useAccounts();
 const [rows,setRows]=useState<Row[]>([]); const [date,setDate]=useState(""); const [loading,setLoading]=useState(false); const [error,setError]=useState(""); const [expanded,setExpanded]=useState<number|null>(null);
 const load=useCallback(async()=>{if(!selectedAccountId)return;setLoading(true);setError("");try{const r=await fetch(`/api/campaign/ab-tests?account_id=${encodeURIComponent(selectedAccountId)}`,{headers:headers(),cache:"no-store"});const d=await r.json();if(!r.ok)throw new Error(d?.detail||"Не удалось загрузить АБ тесты");setRows(d.items||[]);setDate(d.stats_date||"");}catch(e:unknown){setError(e instanceof Error?e.message:"Не удалось загрузить АБ тесты");}finally{setLoading(false)}},[selectedAccountId]);
 useEffect(()=>{
  // Account change is an external data-source change; reload the server snapshot.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  void load();
 },[load]);
 const stats=useMemo(()=>{const measured=rows.filter(x=>x.views>0);const views=measured.reduce((a,x)=>a+x.views,0);const contacts=measured.reduce((a,x)=>a+x.contacts,0);const conversion=views?Math.round(contacts/views*1000)/10:0;const winner=[...measured].sort((a,b)=>b.conversion-a.conversion||b.contacts-a.contacts)[0];return {measured,views,contacts,conversion,winner};},[rows]);
 return <section className="boris-ab-panel" aria-label="АБ тесты рекламы Авито">
  <div className="boris-ab-head">
   <div className="boris-ab-head-icon"><Icon name="target" size={22}/></div>
   <div className="boris-ab-head-copy"><span className="boris-ab-eyebrow">ОПТИМИЗАЦИЯ РЕКЛАМЫ</span><h2>АБ тесты рекламы Авито</h2><p>BORIS сравнивает точные связки «заголовок + текст + креатив» и запоминает, какие варианты реально дают обращения.</p></div>
   <Button kind="ghost" onClick={()=>void load()} disabled={loading}>{loading?"Обновляем…":"Обновить данные"}</Button>
  </div>
  <div className="boris-ab-kpis">
   {[{v:rows.length,l:"вариантов в памяти"},{v:stats.measured.length,l:"получили трафик"},{v:stats.views,l:"просмотров"},{v:stats.contacts,l:"контактов"},{v:`${stats.conversion}%`,l:"просмотр → контакт"}].map(x=><div className="boris-ab-kpi" key={x.l}><strong>{x.v}</strong><span>{x.l}</span></div>)}
  </div>
  {stats.winner?<div className="boris-ab-winner"><span>Лидер сейчас</span><strong>{stats.winner.title}</strong><em>{stats.winner.conversion}% конверсия · {stats.winner.contacts} контактов</em></div>:null}
  {date?<div className="boris-ab-date">Статистика Avito: {date}</div>:null}
  {error?<Card style={{marginTop:space.md,color:color.red}}>{error}</Card>:null}
  {!rows.length&&!loading?<div style={{marginTop:space.md}}><EmptyState title="Пока нечего сравнивать" hint="После запуска вариантов BORIS сохранит точный заголовок, текст и креатив и свяжет их с фактической статистикой Avito."/></div>:null}
  <div className="boris-ab-grid">{rows.map((x,index)=><article className="boris-ab-card" key={x.item_id}>
   <div className="boris-ab-card-top"><span>Вариант {index+1}</span><Badge kind={x.views?"success":"neutral"}>{x.views?`${x.conversion}%`:"ждём данные"}</Badge></div>
   {x.banner_url?<div className="boris-ab-image"><img src={x.banner_url} alt=""/></div>:null}
   <h3>{x.title}</h3>
   <p className={expanded===x.item_id?"is-open":""}>{x.description}</p>
   {x.description?.length>220?<button className="boris-ab-more" type="button" onClick={()=>setExpanded(expanded===x.item_id?null:x.item_id)}>{expanded===x.item_id?"Свернуть текст":"Показать весь текст"}</button>:null}
   <div className="boris-ab-metrics"><span><b>{x.views}</b><small>просмотров</small></span><span><b>{x.contacts}</b><small>контактов</small></span><span><b>{x.conversion}%</b><small>конверсия</small></span></div>
   <div className="boris-ab-memory"><Icon name="memory" size={15}/><span>Связка сохранена в памяти BORIS</span></div>
  </article>)}</div>
 </section>;
}
