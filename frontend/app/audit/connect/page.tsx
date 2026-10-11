'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

function authHeaders(json=false):Record<string,string>{
  const t=typeof window!=='undefined'?localStorage.getItem('boris_token')||'':'';
  const h:Record<string,string>={};
  if(t)h.Authorization='Bearer '+t;
  if(json)h['Content-Type']='application/json';
  return h;
}

export default function AuditConnectPage(){
  const [accounts,setAccounts]=useState<any[]>([]);
  const [accountId,setAccountId]=useState('');
  const [connection,setConnection]=useState<'loading'|'connected'|'not_connected'|'error'|'tariff_error'>('loading');
  const [message,setMessage]=useState('Проверяю подключение…');
  const [clientId,setClientId]=useState('');
  const [clientSecret,setClientSecret]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');

  useEffect(()=>{
    const t=localStorage.getItem('boris_token');
    if(!t){localStorage.setItem('boris_post_login_return','/audit/connect');window.location.replace('/login');return;}
    fetch('/api/accounts/list',{headers:authHeaders(),cache:'no-store'})
      .then(async r=>{const d=await r.json();if(!r.ok)throw new Error(d?.detail||'Не удалось получить аккаунт');return d;})
      .then(d=>{
        const list=d?.accounts||[]; setAccounts(list);
        const saved=localStorage.getItem('boris_currentAccount')||'';
        const first=(list.find((a:any)=>a.account_id===saved)||list[0])?.account_id||'';
        setAccountId(first);
      }).catch(e=>setError(e?.message||'Ошибка аккаунта'));
  },[]);

  useEffect(()=>{
    if(!accountId)return;
    setConnection('loading');setMessage('Проверяю подключение…');
    fetch('/api/accounts/avito_connection?account_id='+encodeURIComponent(accountId)+'&verify=false',{headers:authHeaders(),cache:'no-store'})
      .then(r=>r.json()).then(d=>{setConnection(d?.connection||'not_connected');setMessage(d?.message||'Не подключено');})
      .catch(()=>{setConnection('error');setMessage('Не удалось проверить подключение');});
  },[accountId]);

  async function connect(){
    if(!accountId||!clientId.trim()||!clientSecret.trim()||busy)return;
    setBusy(true);setError('');
    try{
      const r=await fetch('/api/accounts/connect_avito',{method:'POST',headers:authHeaders(true),body:JSON.stringify({account_id:accountId,client_id:clientId.trim(),client_secret:clientSecret.trim()})});
      const d=await r.json();
      if(!r.ok||d?.connection==='error')throw new Error(d?.message||d?.detail||'Avito не принял ключи');
      setConnection(d?.connection==='tariff_error'?'tariff_error':'connected');
      setMessage(d?.message||'Avito подключён');
      localStorage.setItem('boris_audit_connected_account',accountId);
      await fetch('/api/viral/gateway/state',{method:'POST',headers:authHeaders(true),body:JSON.stringify({stage:'AVITO_CONNECTED'})}).catch(()=>null);
      window.location.href='/audit/analyzing?account_id='+encodeURIComponent(accountId);
    }catch(e:any){setError(e?.message||'Ошибка подключения');}
    finally{setBusy(false);}
  }

  function analyze(){
    if(!accountId)return;
    localStorage.setItem('boris_audit_connected_account',accountId);
    fetch('/api/viral/gateway/state',{method:'POST',headers:authHeaders(true),body:JSON.stringify({stage:'SOURCE_CONNECTION_STARTED'})}).catch(()=>{});
    window.location.href='/audit/analyzing?account_id='+encodeURIComponent(accountId);
  }

  const connected=connection==='connected'||connection==='tariff_error';

  return <main className="audit-canonical min-h-screen">
    <header className="px-5 py-5"><div className="mx-auto flex max-w-5xl items-center justify-between">
      <Link href="/audit" className="text-xl font-black">BORIS <span className="text-[#2563eb]">AUDIT</span></Link>
      <span className="text-sm font-bold text-[#667085]">Шаг 2 · реальные данные</span>
    </div></header>
    <section className="px-5 pb-16 pt-8"><div className="mx-auto max-w-5xl">
      <div className="max-w-3xl">
        <div className="inline-flex rounded-full bg-[#edf4ff] px-4 py-2 text-sm font-black text-[#2563eb]">ТОЧНЫЙ АУДИТ</div>
        <h1 className="mt-5 text-4xl font-black tracking-[-.04em] sm:text-5xl">Подключите Avito и получите точный аудит</h1>
        <p className="mt-5 text-lg leading-8 text-[#667085]">BORIS использует разрешённые данные аккаунта, чтобы заменить предположения реальными просмотрами, контактами и переписками.</p>
      </div>

      <div className="mt-9 grid gap-5 lg:grid-cols-[.85fr_1.15fr]">
        <div className="audit-reference-card p-6">
          <div className="font-black">Что будет проверено</div>
          <div className="mt-5 grid gap-3 text-sm font-semibold text-[#475467]">
            {['Просмотры и обращения','Сколько просмотров превращаются в обращения','Новые диалоги','Как быстро отвечают менеджеры','Клиенты без ответа','Распределение ответов по скорости'].map(x=><div key={x} className="rounded-2xl bg-[#f7f9fc] p-4">✓ {x}</div>)}
          </div>
        </div>

        <div className="audit-reference-card p-6 sm:p-8">
          {accounts.length>1&&<label className="grid gap-2"><span className="font-extrabold">Аккаунт бизнеса</span><select value={accountId} onChange={e=>setAccountId(e.target.value)} className="h-14 rounded-2xl border border-[#dce3ee] bg-white px-4 font-bold">{accounts.map(a=><option key={a.account_id} value={a.account_id}>{a.name||a.account_id}</option>)}</select></label>}
          <div className="mt-5 rounded-[22px] bg-[#f7f9fc] p-5">
            <div className="text-sm font-bold text-[#667085]">Статус Avito</div>
            <div className="mt-1 text-xl font-black">{connection==='loading'?'Проверяем…':connected?'Подключён':'Не подключён'}</div>
            <div className="mt-1 text-sm text-[#667085]">{message}</div>
          </div>

          {connected ? <button onClick={analyze} className="mt-6 w-full rounded-full bg-[#2563eb] px-6 py-4 font-black text-white">Запустить точный аудит</button> :
          <div className="mt-6 grid gap-4">
            <label className="grid gap-2"><span className="font-extrabold">Avito Client ID</span><input value={clientId} onChange={e=>setClientId(e.target.value)} autoComplete="off" className="h-14 rounded-2xl border border-[#dce3ee] px-4 outline-none focus:border-[#2563eb]"/></label>
            <label className="grid gap-2"><span className="font-extrabold">Avito Client Secret</span><input value={clientSecret} onChange={e=>setClientSecret(e.target.value)} type="password" autoComplete="new-password" className="h-14 rounded-2xl border border-[#dce3ee] px-4 outline-none focus:border-[#2563eb]"/></label>
            <p className="text-xs leading-5 text-[#98a2b3]">Ключи проверяются BORIS и сохраняются в существующем защищённом контуре аккаунта. Audit Gateway не выводит их обратно.</p>
            <button onClick={connect} disabled={busy||!clientId.trim()||!clientSecret.trim()||!accountId} className="rounded-full bg-[#2563eb] px-6 py-4 font-black text-white disabled:opacity-50">{busy?'Проверяем Avito…':'Подключить Avito'}</button>
          </div>}
          {error&&<div className="mt-4 rounded-2xl bg-red-50 p-4 text-sm font-bold text-red-700">{error}</div>}
        </div>
      </div>
    </div></section>
  </main>;
}
