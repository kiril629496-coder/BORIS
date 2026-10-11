'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

function auth(json=false):Record<string,string>{const t=typeof window!=='undefined'?localStorage.getItem('boris_token')||'':'';const h:Record<string,string>={};if(t)h.Authorization='Bearer '+t;if(json)h['Content-Type']='application/json';return h;}

export default function AuditEmailSettingsPage(){
  const [optIn,setOptIn]=useState(false);
  const [ready,setReady]=useState(false);
  const [status,setStatus]=useState('');

  useEffect(()=>{
    const t=localStorage.getItem('boris_token');
    if(!t){localStorage.setItem('boris_post_login_return','/audit/email-settings');window.location.replace('/login');return;}
    fetch('/api/viral/gateway/email-settings',{headers:auth(),cache:'no-store'})
      .then(r=>r.json()).then(d=>{setOptIn(Boolean(d?.marketing_opt_in));setReady(true);})
      .catch(()=>setReady(true));
  },[]);

  async function save(next:boolean){
    setOptIn(next);setStatus('');
    const r=await fetch('/api/viral/gateway/email-settings',{method:'POST',headers:auth(true),body:JSON.stringify({marketing_opt_in:next})});
    const d=await r.json();
    setStatus(r.ok?'Сохранено':(d?.detail||'Ошибка'));
  }

  return <main className="audit-canonical min-h-screen">
    <header className="px-5 py-5"><div className="mx-auto flex max-w-3xl items-center justify-between"><Link href="/audit" className="text-xl font-black">BORIS <span className="text-[#2563eb]">AUDIT</span></Link><Link href="/audit" className="text-sm font-bold text-[#667085]">Мой аудит</Link></div></header>
    <section className="px-5 py-12"><div className="mx-auto max-w-3xl">
      <div className="audit-reference-card p-7 sm:p-9">
        <div className="text-sm font-black text-[#2563eb]">EMAIL SETTINGS</div>
        <h1 className="mt-3 text-3xl font-black tracking-[-.03em]">Письма по результатам аудита</h1>
        <p className="mt-4 leading-7 text-[#667085]">Сервисные письма — подтверждение почты, безопасность и готовый результат аудита — остаются частью работы аккаунта. Здесь можно отключить только маркетинговые рекомендации и напоминания.</p>
        {!ready?<div className="mt-6 text-[#667085]">Загрузка…</div>:<label className="mt-7 flex items-start justify-between gap-5 rounded-[24px] bg-[#f7f9fc] p-5">
          <div><div className="font-black">Получать рекомендации BORIS по аудиту</div><div className="mt-2 text-sm leading-6 text-[#667085]">Напоминание продолжить аудит, предложение подключить реальные данные и полезные рекомендации. Не чаще установленного nurture-лимита.</div></div>
          <input type="checkbox" checked={optIn} onChange={e=>save(e.target.checked)} className="mt-1 h-5 w-5"/>
        </label>}
        {status&&<div className="mt-4 text-sm font-bold text-[#667085]">{status}</div>}
      </div>
    </div></section>
  </main>;
}
