'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

function auth(json=false):Record<string,string>{
  const t=typeof window!=='undefined'?localStorage.getItem('boris_token')||'':'';
  const h:Record<string,string>={};
  if(t)h.Authorization='Bearer '+t;
  if(json)h['Content-Type']='application/json';
  return h;
}

type StepState='waiting'|'working'|'done'|'error';

export default function AuditAnalyzingPage(){
  const [steps,setSteps]=useState<Record<string,StepState>>({
    connection:'working',stats:'waiting',messages:'waiting',score:'waiting',save:'waiting'
  });
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [retryKey,setRetryKey]=useState(0);
  const [accountId,setAccountId]=useState('');

  useEffect(()=>{
    const t=localStorage.getItem('boris_token');
    if(!t){
      localStorage.setItem('boris_post_login_return','/audit/analyzing');
      window.location.replace('/login');
      return;
    }
    const q=new URLSearchParams(window.location.search);
    const account=q.get('account_id')||localStorage.getItem('boris_audit_connected_account')||localStorage.getItem('boris_currentAccount')||'';
    setAccountId(account);
    if(!account){
      setError('Не найден подключённый аккаунт');
      setSteps(s=>({...s,connection:'error'}));
      return;
    }

    let alive=true;
    (async()=>{
      setError('');setNotice('');
      setSteps({connection:'working',stats:'waiting',messages:'waiting',score:'waiting',save:'waiting'});
      try{
        const rr=await fetch('/api/viral/connected-readiness/'+encodeURIComponent(account),{headers:auth(),cache:'no-store'});
        const rd=await rr.json();
        if(!rr.ok)throw new Error(rd?.detail||'Не удалось проверить готовность данных');
        if(!alive)return;

        setSteps(s=>({
          ...s,
          connection:rd.connection_ready?'done':'error',
          stats:rd.stats_ready?'done':'waiting',
          messages:rd.messages_ready?'done':'waiting',
        }));

        if(!rd.connection_ready){
          setError('Avito ещё не подключён к этому аккаунту.');
          return;
        }
        if(!rd.assumptions_ready){
          setNotice('Для денежной части не хватает среднего чека или конверсии. Сначала завершите быстрый аудит.');
          return;
        }
        if(!rd.can_run){
          setNotice('Подключение готово, но статистика и переписки ещё загружаются. BORIS не будет показывать точную оценку, пока не получит реальные данные.');
          return;
        }

        await fetch('/api/viral/gateway/state',{
          method:'POST',headers:auth(true),body:JSON.stringify({stage:'AVITO_CONNECTED'})
        }).catch(()=>null);

        setSteps(s=>({...s,score:'working'}));
        const ar=await fetch('/api/viral/connected-audit',{
          method:'POST',
          headers:auth(true),
          body:JSON.stringify({account_id:account,period_days:30})
        });
        const ad=await ar.json();
        if(!ar.ok)throw new Error(ad?.detail||'Не удалось рассчитать точный аудит');
        if(!alive)return;
        setSteps(s=>({...s,score:'done',save:'working'}));

        const sr=await fetch('/api/viral/gateway/complete',{
          method:'POST',headers:auth(true),body:JSON.stringify({audit_id:ad.audit_id})
        });
        const sd=await sr.json();
        if(!sr.ok)throw new Error(sd?.detail||'Не удалось сохранить результат');
        if(!alive)return;

        localStorage.setItem('boris_audit_latest_id',ad.audit_id);
        setSteps(s=>({...s,save:'done'}));
        window.location.replace('/audit/result?audit='+encodeURIComponent(ad.audit_id));
      }catch(e:any){
        if(!alive)return;
        setError(e?.message||'Ошибка анализа');
        setSteps(s=>{
          const out={...s};
          for(const k of Object.keys(out)){if(out[k]==='working')out[k]='error';}
          return out;
        });
      }
    })();
    return ()=>{alive=false;};
  },[retryKey]);

  const rows=[
    ['connection','Подключение аккаунта','Проверяем, что Avito действительно подключён'],
    ['stats','Статистика Avito','Проверяем наличие реальных просмотров и контактов'],
    ['messages','Переписки BORIS','Проверяем, появились ли диалоги для анализа скорости ответа'],
    ['score','Расчёт оценки BORIS','Запускается только когда данных достаточно'],
    ['save','Сохраняем результат','Ваш аудит будет доступен в истории'],
  ] as const;

  const label=(s:StepState)=>s==='done'?'✓ Готово':s==='working'?'В работе':s==='error'?'Ошибка':'Ожидает';

  return <main className="audit-canonical min-h-screen">
    <header className="px-5 py-5"><div className="mx-auto flex max-w-3xl items-center justify-between">
      <Link href="/audit" className="text-xl font-black">BORIS <span className="text-[#2563eb]">AUDIT</span></Link>
      <span className="text-sm font-bold text-[#667085]">Точный аудит</span>
    </div></header>

    <section className="px-5 py-12"><div className="mx-auto max-w-3xl">
      <div className="text-center">
        <div className="inline-flex rounded-full bg-[#edf4ff] px-4 py-2 text-sm font-black text-[#2563eb]">АНАЛИЗ РЕАЛЬНЫХ ДАННЫХ</div>
        <h1 className="mt-5 text-4xl font-black tracking-[-.04em]">BORIS проверяет ваш бизнес</h1>
        <p className="mt-4 text-[#667085]">Никаких фейковых процентов: статус меняется только после фактической проверки источника или завершения расчёта.</p>
      </div>

      <div className="mt-9 grid gap-3">
        {rows.map(([k,title,sub])=>{
          const s=steps[k]||'waiting';
          return <div key={k} className="flex items-center justify-between gap-5 rounded-[24px] border border-[#e0e7ef] bg-white p-5">
            <div><div className="font-black">{title}</div><div className="mt-1 text-sm text-[#667085]">{sub}</div></div>
            <div className={"shrink-0 rounded-full px-3 py-2 text-xs font-black "+(s==='done'?'bg-emerald-50 text-emerald-700':s==='working'?'bg-blue-50 text-blue-700':s==='error'?'bg-red-50 text-red-700':'bg-slate-100 text-slate-500')}>{label(s)}</div>
          </div>;
        })}
      </div>

      {notice&&<div className="mt-6 rounded-[24px] bg-[#fff8eb] p-5 text-sm font-bold text-[#8a5a00]">
        {notice}
        <div className="mt-4 flex flex-wrap gap-3">
          <button onClick={()=>setRetryKey(v=>v+1)} className="rounded-full bg-[#101828] px-5 py-3 text-sm font-black text-white">Проверить снова</button>
          <Link href="/audit/onboarding" className="rounded-full border border-[#ead8af] bg-white px-5 py-3 text-sm font-black">Быстрый аудит</Link>
        </div>
      </div>}

      {error&&<div className="mt-6 rounded-[24px] bg-red-50 p-5 text-sm font-bold text-red-700">
        {error}
        <div className="mt-4 flex flex-wrap gap-3">
          <button onClick={()=>setRetryKey(v=>v+1)} className="rounded-full bg-red-700 px-5 py-3 text-sm font-black text-white">Повторить проверку</button>
          <Link href="/audit/connect" className="rounded-full border border-red-200 bg-white px-5 py-3 text-sm font-black text-red-700">Подключение Avito</Link>
        </div>
      </div>}

      {accountId&&<div className="mt-5 text-center text-xs text-[#98a2b3]">Аккаунт анализа: {accountId}</div>}
    </div></section>
  </main>;
}
