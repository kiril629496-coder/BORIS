'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

function headers(): Record<string,string>{const t=typeof window!=='undefined'?localStorage.getItem('boris_token')||'':'';const h:Record<string,string>={};if(t)h.Authorization='Bearer '+t;return h;}

export default function AuditHistoryPage(){
  const [state,setState]=useState<any>(null);
  const [error,setError]=useState('');
  useEffect(()=>{
    const t=localStorage.getItem('boris_token');
    if(!t){window.location.replace('/audit');return;}
    fetch('/api/viral/gateway/state',{headers:headers()}).then(async r=>{const d=await r.json();if(!r.ok)throw new Error(d?.detail||'Ошибка');return d;}).then(d=>setState(d.state||{})).catch(e=>setError(e?.message||'Не удалось загрузить историю'));
  },[]);
  const history=[...(state?.history||[])].reverse();
  return <main className="audit-canonical min-h-screen">
    <header className="px-5 py-5"><div className="mx-auto flex max-w-4xl items-center justify-between"><Link href="/audit" className="text-xl font-black">BORIS <span className="text-[#2563eb]">AUDIT</span></Link><Link href="/audit" className="text-sm font-bold text-[#667085]">Мой аудит</Link></div></header>
    <section className="px-5 py-10"><div className="mx-auto max-w-4xl">
      <h1 className="text-4xl font-black tracking-[-.04em]">История результатов BORIS</h1>
      <p className="mt-3 text-[#667085]">Сравнивайте результаты только внутри одной версии методики. Результаты по реальным данным будут отмечаться отдельно от предварительных.</p>
      {error&&<div className="mt-6 rounded-2xl bg-red-50 p-4 font-bold text-red-700">{error}</div>}
      {!state&&!error&&<div className="mt-8 text-[#667085]">Загрузка…</div>}
      {state&&history.length===0&&<div className="mt-8 rounded-[28px] bg-white p-7"><div className="text-xl font-black">Истории пока нет</div><Link href="/audit/onboarding" className="mt-5 inline-flex rounded-full bg-[#2563eb] px-6 py-3 font-black text-white">Пройти первый аудит</Link></div>}
      <div className="mt-8 grid gap-4">
        {history.map((h:any,i:number)=><Link key={(h.audit_id||'')+i} href={'/audit/result?audit='+encodeURIComponent(h.audit_id||'')} className="flex items-center justify-between rounded-[26px] border border-[#e0e7ef] bg-white p-6">
          <div><div className="text-sm font-bold text-[#667085]">{h.mode==='connected'?'На основе данных BORIS':'Предварительная оценка'}</div><div className="mt-1 text-sm text-[#98a2b3]">{h.created_at?new Date(h.created_at).toLocaleString('ru-RU'):''}</div></div>
          <div className="text-4xl font-black">{h.score}<span className="text-lg text-[#98a2b3]">/100</span></div>
        </Link>)}
      </div>
    </div></section>
  </main>;
}
