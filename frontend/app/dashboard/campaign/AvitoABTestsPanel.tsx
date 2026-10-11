"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CampaignABControls from "./CampaignABControls";
import { useRouter } from "next/navigation";
import { Button, Card, EmptyState, Icon } from "@/app/ui";
import { color, space } from "@/app/ui/tokens";
import { useAccounts } from "@/app/lib/AccountContext";

type Row={
  item_id:number; campaign_id:number; avito_item_id?:string|null;
  title:string; description:string; banner_url?:string;
  views:number; contacts:number; conversion:number; avito_status:string;
  formula_id?:string; formula_label?:string; hypothesis_label?:string;
  experiment_state?:string; experiment_state_label?:string; action_hint?:string;
  updated_at?:string; enrolled?:boolean; auto_enrolled?:boolean; ab_finish_requested?:any;
};
function headers(){
  const t=typeof window==="undefined"?"":localStorage.getItem("boris_token")||"";
  return {Authorization:"Bearer "+t,"Content-Type":"application/json"};
}
function cleanClientText(value:any){
  return String(value||"")
    .replace(/<br\s*\/?>/gi,"\n")
    .replace(/<[^>]+>/g," ")
    .replace(/&nbsp;/gi," ")
    .replace(/&amp;/gi,"&")
    .replace(/&quot;/gi,"\"")
    .replace(/&#39;/g,"'")
    .replace(/[ \t]+/g," ")
    .replace(/\n\s+/g,"\n")
    .trim();
}
const stateStyle:Record<string,{bg:string;fg:string;border:string}>={
  leader:{bg:"#ECFDF3",fg:"#067647",border:"#ABEFC6"},
  weak:{bg:"#FFF1F0",fg:"#B42318",border:"#FECDCA"},
  testing:{bg:"#EEF4FF",fg:"#315BEA",border:"#C7D7FE"},
  collecting:{bg:"#FFF7E6",fg:"#9A6700",border:"#F3D58B"},
  finishing:{bg:"#FFF7E6",fg:"#9A6700",border:"#F3D58B"},
  paused:{bg:"#F2F4F7",fg:"#475467",border:"#D0D5DD"},
  finished:{bg:"#F2F4F7",fg:"#475467",border:"#D0D5DD"},
};
export default function AvitoABTestsPanel(){
  const router=useRouter();
  const {selectedAccountId}=useAccounts();
  const [rows,setRows]=useState<Row[]>([]);
  const [date,setDate]=useState("");
  const [windowDays,setWindowDays]=useState<7|30|60>(30);
  const [windows,setWindows]=useState<any>({});
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [expanded,setExpanded]=useState<number|null>(null);
  const [finishTarget,setFinishTarget]=useState<Row|null>(null);
  const [scaleTarget,setScaleTarget]=useState<Row|null>(null);
  const [scaleCount,setScaleCount]=useState(10);
  const [busy,setBusy]=useState("");
  const [actionsReady,setActionsReady]=useState(false);
  const currentAccount=useRef(selectedAccountId);currentAccount.current=selectedAccountId;
  useEffect(()=>{setRows([]);setWindows({});setActionsReady(false);setFinishTarget(null);setScaleTarget(null);setBusy("");setError("");setNotice("");},[selectedAccountId]);

  const load=useCallback(async()=>{
    if(!selectedAccountId)return;
    setLoading(true);setError("");
    try{
      const [testsResponse,windowsResponse]=await Promise.all([
        fetch("/api/campaign/ab-tests?account_id="+encodeURIComponent(selectedAccountId),{headers:headers(),cache:"no-store"}),
        fetch("/api/accounts/ab-windows?account_id="+encodeURIComponent(selectedAccountId),{headers:headers(),cache:"no-store"}),
      ]);
      const d=await testsResponse.json();
      const w=await windowsResponse.json().catch(()=>({}));
      if(!testsResponse.ok)throw new Error(d?.detail||"Не удалось загрузить результаты");
      if(currentAccount.current!==selectedAccountId)return;
      setRows(d.items||[]);setDate(d.stats_date||"");setActionsReady(d.ab_actions_contract===2);
      setWindows(windowsResponse.ok?(w?.windows||{}):{});
    }catch(e:unknown){if(currentAccount.current===selectedAccountId)setError(e instanceof Error?e.message:"Не удалось загрузить результаты");}
    finally{if(currentAccount.current===selectedAccountId)setLoading(false);}
  },[selectedAccountId]);
  useEffect(()=>{void load();const timer=setInterval(()=>{if(!document.hidden)void load();},15000);return()=>clearInterval(timer);},[load]);

  const windowMeta=windows?.[String(windowDays)]||null;
  const displayRows=useMemo(()=>rows.map((row)=>{
    if(!windowMeta)return row;
    const metric=windowMeta?.by_campaign_item?.[String(row.item_id)]
      || (row.avito_item_id?windowMeta?.by_avito_item?.[String(row.avito_item_id)]:null);
    if(!metric)return {...row,views:0,contacts:0,conversion:0};
    return {
      ...row,
      views:Number(metric.views||0),
      contacts:Number(metric.contacts||0),
      conversion:Number(metric.conversion||0),
    };
  }),[rows,windowMeta]);
  const stats=useMemo(()=>{
    const measured=displayRows.filter(x=>x.views>0);
    const views=measured.reduce((a,x)=>a+x.views,0);
    const contacts=measured.reduce((a,x)=>a+x.contacts,0);
    const conversion=views?Math.round(contacts/views*1000)/10:0;
    const leader=displayRows.find(x=>x.experiment_state==="leader")||null;
    return {measured,views,contacts,conversion,leader};
  },[displayRows]);

  async function action(row:Row,kind:"finish"|"scale",count=0){
    if(!selectedAccountId)return;
    setBusy(kind+"-"+row.item_id);setError("");setNotice("");
    try{
      const r=await fetch("/api/campaign/ab-tests/action",{
        method:"POST",headers:headers(),
        body:JSON.stringify({account_id:selectedAccountId,item_id:row.item_id,action:kind,count,confirmed:true,expected_updated_at:row.updated_at})
      });
      const d=await r.json();
      if(currentAccount.current!==selectedAccountId)return;
      if(!r.ok)throw new Error(d?.detail?.message||d?.detail||d?.message||"Действие не выполнено");
      if(d?.status==="blocked")throw new Error(d?.message||"BORIS не может безопасно выполнить это действие");
      if(kind==="finish"){
        setFinishTarget(null);
        setNotice(d?.message||"BORIS передал изменение в Avito.");
        await load();
      }else{
        setScaleTarget(null);
        setNotice(d?.message||"Новый проект масштабирования создан.");
        if(d?.campaign_id) setTimeout(()=>{if(currentAccount.current===selectedAccountId)router.push("/dashboard/campaign/"+d.campaign_id);},700);
      }
    }catch(e:any){if(currentAccount.current===selectedAccountId)setError(e?.message||"Не удалось выполнить действие");}
    finally{if(currentAccount.current===selectedAccountId)setBusy("");}
  }

  return <section className="boris-ab-panel" aria-label="Результаты A/B тестов">
    <div className="boris-ab-head">
      <div className="boris-ab-head-icon"><Icon name="target" size={22}/></div>
      <div className="boris-ab-head-copy">
        <span className="boris-ab-eyebrow">ЭКСПЕРИМЕНТЫ BORIS</span>
        <h2>Результаты A/B тестов</h2>
        <p>Каждое объявление, которое BORIS публикует в Avito, автоматически попадает сюда. Мы сравниваем рекламную гипотезу, просмотры, обращения и конверсию — и показываем, что делать дальше.</p>
      </div>
      <Button kind="ghost" onClick={()=>void load()} disabled={loading}>{loading?"Обновляем…":"Обновить"}</Button>
    </div>

    <div data-testid="ab-window-switcher" style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"center",flexWrap:"wrap",marginTop:space.md}}>
      <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
        {([7,30,60] as const).map(days=><button key={days} type="button" onClick={()=>setWindowDays(days)} style={{border:windowDays===days?"1px solid #315bea":"1px solid #d0d5dd",background:windowDays===days?"#eef4ff":"white",color:windowDays===days?"#1d4ed8":"#475467",borderRadius:999,padding:"7px 11px",fontSize:12,fontWeight:850,cursor:"pointer"}}>{days} дней</button>)}
      </div>
      {windowMeta?<div style={{fontSize:11.5,color:"#667085"}}>{windowMeta.period_from} — {windowMeta.period_to} · данных за <b>{Number(windowMeta.days_with_data||0)}</b> дней</div>:null}
    </div>

    <div className="boris-ab-kpis">
      {[
        {v:rows.length,l:"объявлений в тестах"},
        {v:stats.measured.length,l:"получили трафик"},
        {v:stats.views,l:"просмотров"},
        {v:stats.contacts,l:"обращений"},
        {v:stats.conversion+"%",l:"просмотр → обращение"},
      ].map(x=><div className="boris-ab-kpi" key={x.l}><strong>{x.v}</strong><span>{x.l}</span></div>)}
    </div>

    {stats.leader?<div className="boris-ab-winner">
      <span>Сейчас лучше работает</span>
      <strong>{stats.leader.hypothesis_label||stats.leader.title}</strong>
      <em>{stats.leader.conversion}% конверсия · {stats.leader.contacts} обращений · можно масштабировать</em>
    </div>:null}

    {date?<div className="boris-ab-date">Последняя статистика Avito: {date}</div>:null}
    {notice?<Card style={{marginTop:space.md,border:"1px solid #ABEFC6",background:"#ECFDF3",color:"#067647"}}>{notice}</Card>:null}
    {error?<Card style={{marginTop:space.md,color:color.red}}>{error}</Card>:null}
    {!rows.length&&!loading?<div style={{marginTop:space.md}}><EmptyState title="Пока нечего сравнивать" hint="После публикации объявлений BORIS автоматически добавит их в эксперименты и начнёт собирать фактическую статистику Avito."/></div>:null}

    <div className="boris-ab-grid">{displayRows.map((x,index)=>{
      const st=x.experiment_state||"collecting";
      const tone=stateStyle[st]||stateStyle.collecting;
      return <article className="boris-ab-card" key={x.item_id}>
        <div className="boris-ab-card-top">
          <span>Объявление {index+1}</span>
          <span style={{fontSize:11,fontWeight:900,padding:"5px 8px",borderRadius:999,background:tone.bg,color:tone.fg,border:"1px solid "+tone.border}}>{x.experiment_state_label||"Пока мало данных"}</span>
        </div>
        {x.banner_url?<div className="boris-ab-image"><img loading="lazy" decoding="async" src={x.banner_url} alt={x.title||"Баннер объявления"}/></div>:null}
        <h3>{x.title||"Без заголовка"}</h3>

        <div style={{padding:"10px 11px",borderRadius:12,background:"#FFF9EE",border:"1px solid #F3D7A1",marginTop:8}}>
          <div style={{fontSize:10,fontWeight:900,color:"#9A6700",textTransform:"uppercase",letterSpacing:.35}}>Что тестируем</div>
          <strong style={{display:"block",marginTop:4,fontSize:13,color:"#172033",lineHeight:1.4}}>{x.hypothesis_label||x.formula_label||"Рекламный подход"}</strong>
          {x.formula_label?<small style={{display:"block",marginTop:4,color:"#667085"}}>Рекламный подход: {x.formula_label}</small>:null}
        </div>
        <div style={{display:"grid",gap:5,marginTop:8,padding:"10px 11px",borderRadius:12,background:"#F8FAFC",border:"1px solid #E4E7EC"}}>
          <div style={{fontSize:10,fontWeight:900,color:"#475467",textTransform:"uppercase",letterSpacing:.35}}>Как BORIS принимает решение</div>
          <div style={{fontSize:11.5,color:"#475467",lineHeight:1.45}}>Сравнивает просмотры, обращения и конверсию с другими объявлениями этого аккаунта.</div>
          <small style={{color:"#667085",lineHeight:1.4}}>
            {st==="collecting"
              ?"Пока рано делать вывод: для сравнительного вывода BORIS ждёт достаточный трафик; кандидат на лидерство должен набрать не меньше 30 просмотров и 3 обращений."
              :st==="leader"
                ?"Данных уже достаточно для сравнительного лидерства. Перед масштабированием проверьте качество обращений."
                :st==="weak"
                  ?"Вариант заметно слабее по накопленным данным. Его можно завершить отдельно, не затрагивая остальные объявления."
                  :st==="testing"
                    ?"Данных хватает для сравнения, но явного лидера пока нет — тест продолжается."
                    :st==="finishing"
                      ?"Завершение уже передано; BORIS ждёт подтверждение Avito и не показывает ложный успех."
                      :st==="finished"
                        ?"Avito подтвердил завершение объявления."
                        :"BORIS продолжает наблюдение и обновит рекомендацию по мере накопления данных."}
          </small>
        </div>

        <p className={expanded===x.item_id?"is-open":""}>{cleanClientText(x.description)}</p>
        {cleanClientText(x.description).length>220?<button className="boris-ab-more" type="button" onClick={()=>setExpanded(expanded===x.item_id?null:x.item_id)}>{expanded===x.item_id?"Свернуть текст":"Показать весь текст"}</button>:null}

        <div className="boris-ab-metrics">
          <span><b>{x.views}</b><small>просмотров</small></span>
          <span><b>{x.contacts}</b><small>обращений</small></span>
          <span><b>{x.conversion}%</b><small>конверсия</small></span>
        </div>
        <div style={{fontSize:11.5,color:"#667085",lineHeight:1.45,marginTop:9}}>{x.action_hint}</div>

        <div style={{display:"flex",gap:7,flexWrap:"wrap",marginTop:12}}>
          <Button kind="ghost" onClick={()=>router.push("/dashboard/campaign/"+x.campaign_id+"?edit="+x.item_id+"&tab=check#item-"+x.item_id)}>Открыть карточку</Button>
          <Button onClick={()=>router.push("/dashboard/campaign/"+x.campaign_id+"?edit="+x.item_id+"&tab=ad#item-"+x.item_id)}>Редактировать текст</Button>
          <Button kind="ghost" onClick={()=>router.push("/dashboard/campaign/"+x.campaign_id+"?edit="+x.item_id+"&tab=photos#item-"+x.item_id)}>Фото и баннер</Button>
          <Button kind="ghost" onClick={()=>router.push("/dashboard/campaign/"+x.campaign_id+"?edit="+x.item_id+"&tab=history#item-"+x.item_id)}>История изменений</Button>
          {st==="leader"?<Button onClick={()=>{setScaleTarget(x);setScaleCount(10);}} disabled={!!busy}>Масштабировать</Button>:null}
          {st==="weak" && x.avito_item_id?<Button kind="ghost" onClick={()=>setFinishTarget(x)} disabled={!!busy}>В завершённые</Button>:null}
        </div>
        {actionsReady?<CampaignABControls key={String(selectedAccountId)+":"+x.item_id} row={x} accountId={selectedAccountId||""} onChanged={()=>void load()}/>:null}
      </article>;
    })}</div>

    {scaleTarget?<div role="dialog" aria-modal="true" style={{position:"fixed",inset:0,zIndex:1500,background:"rgba(15,23,42,.36)",display:"grid",placeItems:"center",padding:18}}>
      <Card style={{width:"min(560px,96vw)",borderRadius:18}}>
        <h3 style={{marginTop:0,color:"#172033"}}>Масштабировать успешный тест</h3>
        <p style={{color:"#667085",lineHeight:1.55}}>BORIS создаст новые уникальные объявления по логике победителя «{scaleTarget.hypothesis_label||scaleTarget.title}». Факты, цена и категория сохранятся, но тексты и визуалы не будут копиями один в один.</p>
        <label style={{display:"grid",gap:5,fontSize:12,fontWeight:800,color:"#344054"}}>Сколько новых объявлений создать
          <input type="number" min={1} max={50} value={scaleCount} onChange={e=>setScaleCount(Math.max(1,Math.min(50,Number(e.target.value)||1)))} style={{border:"1px solid #D0D5DD",borderRadius:10,padding:"10px 11px",font:"inherit"}}/>
        </label>
        <div style={{display:"flex",gap:8,justifyContent:"flex-end",marginTop:16,flexWrap:"wrap"}}>
          <Button kind="ghost" onClick={()=>setScaleTarget(null)} disabled={!!busy}>Отмена</Button>
          <Button onClick={()=>void action(scaleTarget,"scale",scaleCount)} disabled={!!busy}>{busy?"BORIS запускает…":"Создать "+scaleCount+" вариантов"}</Button>
        </div>
      </Card>
    </div>:null}

    {finishTarget?<div role="dialog" aria-modal="true" style={{position:"fixed",inset:0,zIndex:1500,background:"rgba(15,23,42,.36)",display:"grid",placeItems:"center",padding:18}}>
      <Card style={{width:"min(560px,96vw)",borderRadius:18}}>
        <h3 style={{marginTop:0,color:"#172033"}}>Перевести объявление в завершённые?</h3>
        <p style={{color:"#667085",lineHeight:1.55}}>BORIS завершит только это точное объявление через подключение Avito. Статус не будет показан как успешный, пока Avito не подтвердит изменение.</p>
        <div style={{padding:"10px 12px",borderRadius:11,background:"#FFF7E6",color:"#7A5A2A",fontSize:12}}><b>{finishTarget.title}</b><br/>{finishTarget.views} просмотров · {finishTarget.contacts} обращений · {finishTarget.conversion}%</div>
        <div style={{display:"flex",gap:8,justifyContent:"flex-end",marginTop:16,flexWrap:"wrap"}}>
          <Button kind="ghost" onClick={()=>setFinishTarget(null)} disabled={!!busy}>Отмена</Button>
          <Button onClick={()=>void action(finishTarget,"finish")} disabled={!!busy}>{busy?"Передаю в Avito…":"Да, завершить"}</Button>
        </div>
      </Card>
    </div>:null}
  </section>;
}
