'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

function auth():Record<string,string>{const t=typeof window!=='undefined'?localStorage.getItem('boris_token')||'':'';const h:Record<string,string>={};if(t)h.Authorization='Bearer '+t;return h;}

export default function AuditDetailsPage(){
  const [data,setData]=useState<any>(null);
  const [error,setError]=useState('');

  useEffect(()=>{
    const t=localStorage.getItem('boris_token');
    const auditId=new URLSearchParams(window.location.search).get('audit')||localStorage.getItem('boris_audit_latest_id')||'';
    if(!t){localStorage.setItem('boris_post_login_return',window.location.pathname+window.location.search);window.location.replace('/login');return;}
    if(!auditId){setError('Аудит не найден');return;}
    fetch('/api/viral/audit/'+encodeURIComponent(auditId),{headers:auth(),cache:'no-store'})
      .then(async r=>{const d=await r.json();if(!r.ok)throw new Error(d?.detail||'Аудит не найден');return d;})
      .then(setData).catch(e=>setError(e?.message||'Не удалось загрузить детали'));
  },[]);

  if(error)return <main className="audit-canonical min-h-screen grid place-items-center px-5"><div className="rounded-[28px] bg-white p-8 text-center"><h1 className="text-2xl font-black">{error}</h1><Link href="/audit" className="mt-5 inline-flex rounded-full bg-[#2563eb] px-5 py-3 font-black text-white">Мой аудит</Link></div></main>;
  if(!data)return <main className="audit-canonical min-h-screen grid place-items-center text-[#667085]">Загрузка деталей…</main>;

  const breakdown=Object.values(data.score?.breakdown||{}) as any[];
  const c=data.loss?.confirmed||{};
  const sla=c.sla_distribution||{};
  const sources=data.sources||[];
  const gaps=data.score?.data_gaps||[];
  const sourceLabel=(value:string)=>{
    const v=String(value||'');
    if(v==='BORIS messages')return 'Переписки BORIS';
    if(v==='user_declared')return 'Указано вами';
    return v||'Источник';
  };
  const freshnessLabel=(value:string)=>{
    const v=String(value||'').toLowerCase();
    if(v==='fresh'||v==='live')return 'Свежие данные';
    if(v==='stale')return 'Данные устарели';
    if(v==='missing')return 'Данные отсутствуют';
    if(v==='declared_now')return 'Указано вами';
    if(v==='latest_daily_stats')return 'Последняя статистика Avito';
    return value||'—';
  };

  return <main className="audit-canonical min-h-screen">
    <header className="px-5 py-5"><div className="mx-auto flex max-w-5xl items-center justify-between"><Link href="/audit" className="text-xl font-black">BORIS <span className="text-[#2563eb]">AUDIT</span></Link><Link href={'/audit/result?audit='+encodeURIComponent(data.audit_id||'')} className="text-sm font-bold text-[#667085]">← К результату</Link></div></header>
    <section className="px-5 pb-20 pt-8"><div className="mx-auto max-w-5xl">
      <div className="max-w-3xl"><div className="inline-flex rounded-full bg-[#edf4ff] px-4 py-2 text-sm font-black text-[#2563eb]">ДЕТАЛИ АУДИТА</div><h1 className="mt-5 text-4xl font-black tracking-[-.04em]">Из чего сложилась оценка {data.score?.total}/100</h1><p className="mt-4 text-[#667085]">Здесь показано, какие показатели сильнее всего повлияли на итог и откуда взяты данные.</p></div>

      <div className="mt-8 grid gap-4 sm:grid-cols-2">
        {breakdown.map((x:any)=><div key={x.label} className="rounded-[26px] border border-[#e0e7ef] bg-white p-5"><div className="flex items-center justify-between gap-4"><div className="font-black">{x.label}</div><div className="text-2xl font-black">{x.score}<span className="text-sm text-[#98a2b3]">/{x.max}</span></div></div><div className="mt-3 h-2 overflow-hidden rounded-full bg-[#eef2f7]"><div className="h-full rounded-full bg-[#2563eb]" style={{width:Math.max(0,Math.min(100,(Number(x.score||0)/Math.max(1,Number(x.max||1)))*100))+'%'}}/></div></div>)}
      </div>

      {Object.keys(sla).length>0&&<div className="audit-reference-card mt-8 p-6">
        <h2 className="text-2xl font-black">Распределение первого ответа</h2>
        <div className="mt-5 grid gap-3 sm:grid-cols-3">{Object.entries(sla).map(([k,v]:any)=><div key={k} className="rounded-2xl bg-[#f7f9fc] p-4"><div className="text-2xl font-black">{v}</div><div className="mt-1 text-sm font-bold text-[#667085]">{k}</div></div>)}</div>
      </div>}

      <div className="mt-8 grid gap-5 lg:grid-cols-2">
        <div className="audit-reference-card p-6"><h2 className="text-xl font-black">Откуда взяты данные</h2><div className="mt-4 grid gap-3">{sources.map((s:any,i:number)=><div key={i} className="rounded-2xl bg-[#f7f9fc] p-4"><div className="font-black">{sourceLabel(s.source)}</div><div className="mt-1 text-xs font-bold text-[#53617a]">{freshnessLabel(s.freshness)}{s.collected_at?' · '+new Date(s.collected_at).toLocaleString('ru-RU'):''}</div></div>)}</div></div>
        <div className="audit-reference-card p-6"><h2 className="text-xl font-black">Чего пока не хватает</h2>{gaps.length?<div className="mt-4 grid gap-3">{gaps.map((g:string)=><div key={g} className="rounded-2xl bg-[#fff8eb] p-4 text-sm font-bold text-[#8a5a00]">{g}</div>)}</div>:<div className="mt-4 rounded-2xl bg-[#edf9f2] p-4 text-sm font-bold text-[#087443]">Данных достаточно для текущей оценки.</div>}</div>
      </div>

      {data.score?.mode==='connected'&&<div className="audit-reference-card mt-8 p-6"><h2 className="text-xl font-black">Факты из переписок и Avito</h2><div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{[
        ['Диалогов за период',c.dialogs_period],
        ['Отвечено',c.answered_dialogs],
        ['Без ответа сейчас',c.currently_unanswered],
        ['Не отвечены за период',c.never_answered_in_period],
        ['Среднее время ответа, мин',c.avg_response_minutes],
        ['Обычное время ответа, мин',c.median_response_minutes],
        ['90% ответов быстрее, мин',c.p90_response_minutes],
        ['Из просмотров в обращения',c.view_to_contact_conversion_pct==null?'—':c.view_to_contact_conversion_pct+'%'],
      ].map(([k,v]:any)=><div key={k} className="rounded-2xl bg-[#f8faff] p-4"><div className="text-2xl font-black">{v??0}</div><div className="mt-1 text-xs font-bold text-[#53617a]">{k}</div></div>)}</div></div>}
    </div></section>
  </main>;
}
