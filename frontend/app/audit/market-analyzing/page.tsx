'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { fireAuditEvent } from '../lib/attribution';

function auth():Record<string,string>{
  const t=typeof window!=='undefined'?localStorage.getItem('boris_token')||'':'';
  return t?{Authorization:'Bearer '+t}:{};
}

const ordered=[
  ['QUERY_READY','Готовим проверку'],
  ['DEMAND_RUNNING','Изучаем спрос'],
  ['DEMAND_READY','Спрос изучен'],
  ['COMPETITORS_RUNNING','Смотрим рынок'],
  ['COMPETITORS_READY','Рынок изучен'],
  ['ANALYSIS_READY','Собираем точки роста'],
] as const;

export default function MarketAnalyzingPage(){
  const [stage,setStage]=useState('QUERY_READY');
  const [error,setError]=useState('');
  const [attempt,setAttempt]=useState(0);
  const [elapsed,setElapsed]=useState(0);

  useEffect(()=>{
    const token=localStorage.getItem('boris_token');
    if(!token){localStorage.setItem('boris_post_login_return',window.location.pathname+window.location.search);window.location.replace('/login');return;}
    const params=new URLSearchParams(window.location.search);
    const marketId=params.get('market')||localStorage.getItem('boris_market_latest_id')||'';
    const marketOnly=params.get('market_only')==='1';
    const coreId=marketOnly?'':(params.get('core')||localStorage.getItem('boris_audit_latest_id')||'');
    if(!marketId){setError('Не найден рыночный аудит');return;}

    let stopped=false;
    let timer:number|undefined;
    const check=async()=>{
      try{
        const r=await fetch('/api/viral/market/'+encodeURIComponent(marketId)+'/status',{headers:auth(),cache:'no-store'});
        const d=await r.json();
        if(!r.ok)throw new Error(d?.detail||'Не удалось проверить анализ рынка');
        const next=String(d?.job?.status||'QUERY_READY');
        if(stopped)return;
        setStage(next);
        if(next==='ANALYSIS_READY'){
          const eventKey='boris_market_completed_seen_'+marketId;
          try{
            if(!sessionStorage.getItem(eventKey)){
              sessionStorage.setItem(eventKey,'1');
              fireAuditEvent('market_audit_completed','audit_market');
            }
          }catch{ fireAuditEvent('market_audit_completed','audit_market'); }
          if(marketOnly) window.location.replace('/audit/market?market='+encodeURIComponent(marketId));
          else window.location.replace('/audit/result?audit='+encodeURIComponent(coreId)+'&market='+encodeURIComponent(marketId));
          return;
        }
        if(next==='ERROR'){
          setError(String(d?.job?.message||'Часть рыночных данных временно недоступна'));
          return;
        }
        timer=window.setTimeout(check,2500);
      }catch(e:any){
        if(!stopped)setError(e?.message||'Не удалось проверить анализ рынка');
      }
    };
    check();
    return()=>{stopped=true;if(timer)window.clearTimeout(timer);};
  },[attempt]);

  useEffect(()=>{
    setElapsed(0);
    const started=Date.now();
    const id=window.setInterval(()=>setElapsed(Math.floor((Date.now()-started)/1000)),1000);
    return()=>window.clearInterval(id);
  },[attempt]);

  const currentIndex=Math.max(0,ordered.findIndex(([key])=>key===stage));
  const progressByStage:Record<string,number>={
    QUERY_READY:10,DEMAND_RUNNING:25,DEMAND_READY:45,
    COMPETITORS_RUNNING:60,COMPETITORS_READY:82,ANALYSIS_READY:100,
  };
  const progress=progressByStage[stage]??10;
  const elapsedText=String(Math.floor(elapsed/60)).padStart(2,'0')+':'+String(elapsed%60).padStart(2,'0');

  return <main className="audit-canonical min-h-screen">
    <header className="audit-internal-header px-5 py-5"><div className="audit-wide mx-auto flex items-center justify-between">
      <Link href="/audit" className="text-xl font-black">BORIS <span className="text-[#2878ff]">AUDIT</span></Link>
      <span className="text-sm font-bold text-[#53617a]">Анализ рынка</span>
    </div></header>
    <section className="audit-internal-section px-5 py-12"><div className="audit-wide audit-process-shell mx-auto">
      <div className="text-center">
        <div className="inline-flex rounded-[10px] bg-[#edf4ff] px-4 py-2 text-sm font-black text-[#2878ff]">РЫНОК + СПРОС</div>
        <h1 className="mt-5 text-4xl font-black tracking-[-.035em] text-[#15213b]">BORIS изучает ваш рынок</h1>
        <p className="mx-auto mt-4 max-w-2xl leading-7 text-[#53617a]">Показываем только фактические этапы. Если один источник временно недоступен, BORIS продолжит аудит по остальным данным и не будет придумывать цифры.</p>
      </div>

      <div className="mt-8 rounded-[18px] border border-[#d9e6ff] bg-[#edf4ff] p-5">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-sm font-black text-[#15213b]">Прогресс анализа</div>
            <div className="mt-1 text-xs font-bold text-[#6d7d96]">Прошло {elapsedText}</div>
          </div>
          <div className="text-2xl font-black text-[#2878ff]">{progress}%</div>
        </div>
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-white"><div className="h-full rounded-full bg-[#2878ff] transition-[width] duration-500" style={{width:progress+'%'}}/></div>
        {elapsed>=90&&stage!=='ANALYSIS_READY'&&!error&&<p className="mt-3 text-xs font-bold leading-5 text-[#53617a]">Анализ идёт дольше обычного. Задача сохранена на сервере — BORIS продолжает собирать доступные данные.</p>}
      </div>

      <div className="mt-5 grid gap-3">
        {ordered.map(([key,label],i)=>{
          const done=stage==='ANALYSIS_READY'||i<currentIndex;
          const active=key===stage&&stage!=='ANALYSIS_READY';
          return <div key={key} className="audit-reference-card flex items-center justify-between gap-5 p-5">
            <div>
              <div className="font-black text-[#15213b]">{label}</div>
              <div className="mt-1 text-xs text-[#8490a6]">{key==='COMPETITORS_RUNNING'?'Сравниваем публичные предложения и конкурентную среду':key==='DEMAND_RUNNING'?'Проверяем, что и как часто ищут клиенты':key==='ANALYSIS_READY'?'Собираем выводы и приоритетные действия':'Следующий этап запустится автоматически'}</div>
            </div>
            <div className={"rounded-[10px] px-3 py-2 text-xs font-black "+(done?'bg-emerald-50 text-emerald-700':active?'bg-[#edf4ff] text-[#2878ff]':'bg-[#f4f6fa] text-[#8490a6]')}>{done?'✓ Готово':active?'В работе':'Ожидает'}</div>
          </div>
        })}
      </div>

      {error&&<div className="mt-6 rounded-[14px] border border-amber-200 bg-amber-50 p-5">
        <div className="font-black text-amber-800">{error}</div>
        <p className="mt-2 text-sm leading-6 text-amber-800/80">Это не означает, что весь аудит потерян. Можно повторить получение доступных данных.</p>
        <button onClick={()=>{setError('');setAttempt(v=>v+1);}} className="mt-4 rounded-[10px] bg-[#2878ff] px-5 py-3 text-sm font-black text-white">Проверить снова</button>
      </div>}
    </div></section>
  </main>;
}
