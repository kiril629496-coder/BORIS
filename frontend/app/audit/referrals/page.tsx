'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

function auth():Record<string,string>{
  const t=typeof window!=='undefined'?localStorage.getItem('boris_token')||'':'';
  return t?{Authorization:'Bearer '+t}:{};
}

export default function ReferralPage(){
  const [data,setData]=useState<any>(null);
  const [error,setError]=useState('');
  const [copied,setCopied]=useState(false);

  async function load(){
    try{
      const r=await fetch('/api/viral/referral/stats',{headers:auth(),cache:'no-store'});
      const d=await r.json();
      if(!r.ok)throw new Error(d?.detail||'Не удалось загрузить приглашения');
      setData(d);
    }catch(e:any){setError(e?.message||'Не удалось загрузить приглашения');}
  }
  useEffect(()=>{load();},[]);

  const link=useMemo(()=>{
    if(!data?.link)return '';
    return typeof window!=='undefined'?new URL(String(data.link),window.location.origin).toString():String(data.link);
  },[data?.link]);

  async function copy(){
    if(!link)return;
    try{await navigator.clipboard.writeText(link);setCopied(true);window.setTimeout(()=>setCopied(false),1600);}catch{}
  }
  async function share(){
    if(!link)return;
    const text='Проверь свой бизнес через BORIS. Он покажет спрос, точки потерь и что можно улучшить.';
    if(navigator.share){
      try{await navigator.share({title:'BORIS — разбор бизнеса',text,url:link});return;}catch{}
    }
    await copy();
  }

  if(error)return <main className="min-h-screen bg-[#f4f7fb] px-5 py-16"><div className="mx-auto max-w-xl rounded-[30px] bg-white p-8 text-center"><h1 className="text-2xl font-black">{error}</h1><Link href="/audit" className="mt-6 inline-flex rounded-full bg-[#2563eb] px-6 py-3 font-black text-white">Вернуться в аудит</Link></div></main>;
  if(!data)return <main className="min-h-screen bg-[#f4f7fb] px-5 py-16"><div className="mx-auto max-w-5xl animate-pulse"><div className="h-10 w-80 rounded-xl bg-[#e5eaf2]"/><div className="mt-8 grid gap-4 sm:grid-cols-4">{[1,2,3,4].map(x=><div key={x} className="h-28 rounded-[26px] bg-white"/>)}</div></div></main>;

  return <main className="min-h-screen bg-[#f4f7fb] px-5 py-8 text-[#15213b] sm:py-14">
    <div className="mx-auto max-w-5xl">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div><div className="text-xs font-black uppercase tracking-[.14em] text-blue-600">BORIS · ПРИГЛАШЕНИЯ</div><h1 className="mt-2 text-4xl font-black tracking-[-.045em]">Ваш вирусный круг</h1></div>
        <Link href="/audit/result" className="rounded-full bg-white px-5 py-3 text-sm font-black shadow-sm">К результату аудита</Link>
      </header>

      <section className="mt-9 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ['Переходы',data.visits||0],
          ['Зарегистрировались',data.registrations||0],
          ['Подключили BORIS',data.activated||0],
          ['Получено','+'+String(data.reward_days||0)+' дней'],
        ].map(([label,value])=><div key={String(label)} className="rounded-[28px] bg-white p-6 shadow-[0_16px_55px_rgba(20,42,84,.07)]"><div className="text-4xl font-black tracking-[-.05em]">{value}</div><div className="mt-2 text-sm font-bold text-[#6f7c91]">{label}</div></div>)}
      </section>

      <section className="mt-6 overflow-hidden rounded-[36px] bg-[#10264f] p-7 text-white shadow-[0_24px_80px_rgba(12,35,80,.18)] sm:p-9">
        <div className="grid gap-8 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <div className="text-xs font-black uppercase tracking-[.14em] text-blue-300">Ваша ссылка</div>
            <h2 className="mt-3 text-3xl font-black tracking-[-.04em]">Пригласите предпринимателя проверить свой бизнес</h2>
            <p className="mt-4 max-w-2xl text-sm leading-6 text-white/65">За каждого нового пользователя, который зарегистрируется по вашей ссылке и реально подключит аккаунт, BORIS добавит вам 7 дней. За переход или пустую регистрацию награда не начисляется.</p>
            <div className="mt-6 break-all rounded-[18px] bg-white/[.08] px-4 py-3 text-sm font-bold text-white/75">{link}</div>
          </div>
          <div className="flex flex-wrap gap-2 lg:justify-end">
            <button onClick={copy} className="rounded-full bg-white px-5 py-3 text-sm font-black text-[#10264f]">{copied?'Скопировано ✓':'Копировать ссылку'}</button>
            <button onClick={share} className="rounded-full bg-blue-500 px-5 py-3 text-sm font-black text-white">Отправить другу</button>
          </div>
        </div>
      </section>

      <section className="mt-6 rounded-[34px] bg-white p-7 shadow-[0_16px_55px_rgba(20,42,84,.06)]">
        <div className="flex items-center justify-between gap-4"><div><h2 className="text-2xl font-black">Начисления</h2><p className="mt-1 text-sm text-[#748197]">Каждый квалифицированный пользователь даёт одну награду.</p></div><div className="rounded-full bg-[#edf4ff] px-4 py-2 text-xs font-black text-blue-700">{data?.reward_rule?.state==='active'?'Механика активна':'Проверяется'}</div></div>
        {(data.rewards||[]).length?<div className="mt-5 space-y-3">{data.rewards.slice().reverse().map((r:any)=><div key={r.id} className="flex flex-wrap items-center justify-between gap-3 rounded-[18px] bg-[#f7f9fc] px-5 py-4"><div><div className="font-black">+{r.reward_value||7} дней BORIS</div><div className="mt-1 text-xs text-[#7b879a]">{r.activated_at?new Date(r.activated_at).toLocaleString('ru-RU'):'Начисляется'}</div></div><span className="rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-black text-emerald-700">{r.status==='GRANTED'?'Начислено':r.status}</span></div>)}</div>:<div className="mt-6 rounded-[20px] bg-[#f7f9fc] p-6 text-sm font-bold text-[#69768b]">Пока начислений нет. Скопируйте ссылку выше и отправьте её знакомому предпринимателю.</div>}
      </section>
    </div>
  </main>;
}
