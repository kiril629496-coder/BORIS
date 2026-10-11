'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { auditAttribution, emailGoal, fireAuditEvent } from '../lib/attribution';

function token(){ return typeof window!=='undefined' ? localStorage.getItem('boris_token')||'' : ''; }
function auth(): Record<string,string>{ const t=token(); const h:Record<string,string>={'Content-Type':'application/json'}; if(t)h.Authorization='Bearer '+t; return h; }

export default function AuditOnboardingPage(){
  const [form,setForm]=useState({leads_per_month:100,average_check_rub:30000,conversion_percent:10,managers_count:2,avg_response_minutes:30,unanswered_leads:10});
  const [market,setMarket]=useState({
    query: typeof window!=='undefined' ? (localStorage.getItem('boris_audit_niche')||'') : '',
    region: typeof window!=='undefined' ? (localStorage.getItem('boris_audit_region')||'') : '',
    source_url:''
  });
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [hintBusy,setHintBusy]=useState(false);
  const [sourceHint,setSourceHint]=useState<any>(null);

  useEffect(()=>{
    if(!token()){ window.location.replace('/audit'); return; }
    const firstTouch=auditAttribution();
    fireAuditEvent('audit_start','audit_onboarding');
    fetch('/api/viral/gateway/state',{
      method:'POST',headers:auth(),
      body:JSON.stringify({
        stage:'AUDIT_STARTED',
        contact_name:localStorage.getItem('boris_audit_contact_name')||'',
        niche:localStorage.getItem('boris_audit_niche')||'',
        region:localStorage.getItem('boris_audit_region')||'',
        marketing_opt_in:localStorage.getItem('boris_audit_marketing_opt_in')==='1',
        session_id:firstTouch.session_id||null,
        utm_source:firstTouch.utm_source||null,
        utm_medium:firstTouch.utm_medium||null,
        utm_campaign:firstTouch.utm_campaign||null,
        utm_content:firstTouch.utm_content||null,
        utm_term:firstTouch.utm_term||null,
        landing_path:firstTouch.landing_path||'/audit',
        first_seen_at:firstTouch.first_seen_at||null,
        qa:firstTouch.qa,
        referral_code:firstTouch.referral_code||null
      })
    }).catch(()=>{});
    emailGoal('email_audit_started');
  },[]);

  const fields=[
    ['leads_per_month','Лидов в месяц','Сколько обращений приходит за месяц'],
    ['average_check_rub','Средний чек, ₽','Средняя сумма одной продажи'],
    ['conversion_percent','Конверсия лид → продажа, %','Сколько из 100 лидов покупают'],
    ['managers_count','Менеджеров','Кто обрабатывает входящие обращения'],
    ['avg_response_minutes','Среднее время ответа, мин','Через сколько минут отвечаете клиенту'],
    ['unanswered_leads','Остаются без ответа','Сколько лидов в месяц теряются без ответа'],
  ] as const;

  async function analyzeSource(){
    const sourceUrl=market.source_url.trim();
    if(!sourceUrl){setSourceHint(null);return;}
    setHintBusy(true); setError('');
    try{
      const r=await fetch('/api/viral/market/source-hint',{method:'POST',headers:auth(),body:JSON.stringify({source_url:sourceUrl})});
      const d=await r.json();
      if(!r.ok) throw new Error(d?.detail||'Не удалось определить направление');
      setSourceHint(d);
    }catch(e:any){setSourceHint(null);setError(e?.message||'Не удалось определить направление по ссылке');}
    finally{setHintBusy(false);}
  }

  async function startMarketOnly(){
    setBusy(true); setError('');
    try{
      const query=market.query.trim();
      const region=market.region.trim();
      if(query.length<2||region.length<2) throw new Error('Укажите направление бизнеса и регион');
      localStorage.setItem('boris_audit_niche',query);
      localStorage.setItem('boris_audit_region',region);
      fireAuditEvent('market_audit_started','audit_onboarding');
      const r=await fetch('/api/viral/market/start',{method:'POST',headers:auth(),body:JSON.stringify({query,region,source_url:market.source_url.trim()||null})});
      const d=await r.json();
      if(!r.ok) throw new Error(d?.detail||'Не удалось запустить анализ рынка');
      localStorage.setItem('boris_market_latest_id',d.audit_id);
      window.location.href='/audit/market-analyzing?market='+encodeURIComponent(d.audit_id)+'&market_only=1';
    }catch(e:any){ setError(e?.message||'Ошибка рыночного аудита'); }
    finally{ setBusy(false); }
  }

  async function submit(e:React.FormEvent){
    e.preventDefault(); setBusy(true); setError('');
    try{
      const query=market.query.trim();
      const region=market.region.trim();
      if(query.length<2||region.length<2) throw new Error('Укажите направление бизнеса и регион');
      localStorage.setItem('boris_audit_niche',query);
      localStorage.setItem('boris_audit_region',region);
      fireAuditEvent('market_audit_started','audit_onboarding');

      const [salesResponse,marketResponse]=await Promise.all([
        fetch('/api/viral/calculator',{method:'POST',headers:auth(),body:JSON.stringify({...form,niche:query,region})}),
        fetch('/api/viral/market/start',{method:'POST',headers:auth(),body:JSON.stringify({query,region,source_url:market.source_url.trim()||null})})
      ]);
      const sales=await salesResponse.json();
      const marketData=await marketResponse.json();
      if(!salesResponse.ok) throw new Error(sales?.detail||'Не удалось рассчитать аудит бизнеса');
      if(!marketResponse.ok) throw new Error(marketData?.detail||'Не удалось запустить анализ рынка');

      const c=await fetch('/api/viral/gateway/complete',{method:'POST',headers:auth(),body:JSON.stringify({audit_id:sales.audit_id})});
      const cd=await c.json();
      if(!c.ok) throw new Error(cd?.detail||'Не удалось сохранить аудит');
      localStorage.setItem('boris_audit_latest_id',sales.audit_id);
      localStorage.setItem('boris_audit_latest_result',JSON.stringify(sales));
      localStorage.setItem('boris_market_latest_id',marketData.audit_id);
      window.location.href='/audit/market-analyzing?core='+encodeURIComponent(sales.audit_id)+'&market='+encodeURIComponent(marketData.audit_id);
    }catch(e:any){ setError(e?.message||'Ошибка аудита'); }
    finally{ setBusy(false); }
  }

  return <main className="audit-canonical min-h-screen">
    <header className="audit-internal-header px-5 py-5"><div className="audit-wide mx-auto flex items-center justify-between">
      <Link href="/audit" className="text-xl font-black">BORIS <span className="text-[#2563eb]">AUDIT</span></Link>
      <span className="rounded-[10px] bg-[#f4f6fa] px-3 py-2 text-xs font-black text-[#53617a]">Шаг 1 / 2</span>
    </div></header>
    <section className="px-5 pb-16 pt-8">
      <div className="audit-wide mx-auto mb-7">
        <div className="grid grid-cols-3 overflow-hidden rounded-[18px] border border-[#dfe6f2] bg-white shadow-[0_3px_12px_rgba(16,24,40,.04)]">
          {[['1','О бизнесе'],['2','Анализ рынка'],['3','Ваш результат']].map(([n,t],i)=><div key={n} className={"flex items-center gap-3 px-4 py-4 "+(i===0?'bg-[#edf4ff]':'border-l border-[#edf1f7]')}>
            <div className={"grid h-8 w-8 shrink-0 place-items-center rounded-[10px] text-sm font-black "+(i===0?'bg-[#2878ff] text-white':'bg-[#f1f4f8] text-[#8490a6]')}>{n}</div>
            <div className={"text-sm font-black "+(i===0?'text-[#15213b]':'text-[#8490a6]')}>{t}</div>
          </div>)}
        </div>
      </div>
      <div className="audit-wide audit-flow-shell mx-auto grid gap-8 lg:grid-cols-[.78fr_1.22fr]">
        <div className="lg:sticky lg:top-24 lg:self-start">
          <div className="inline-flex rounded-[10px] border border-[#d9e6ff] bg-[#edf4ff] px-3 py-2 text-xs font-black uppercase tracking-[.08em] text-[#2878ff]">Аудит бизнеса</div>
          <h1 className="mt-4 text-4xl font-black tracking-[-.035em] text-[#15213b] sm:text-5xl">Сначала рынок. Потом — ваши продажи.</h1>
          <p className="mt-4 text-base leading-7 text-[#53617a]">BORIS отдельно проверит внешний спрос и конкурентов, а ваши показатели использует только для оценки работы с клиентами. Можно начать только с рынка и добавить цифры бизнеса позже.</p>

          <div className="audit-reference-card mt-6 p-5">
            <div className="text-xs font-black uppercase tracking-[.08em] text-[#8490a6]">Как пройдёт аудит</div>
            <div className="mt-4 grid gap-4">
              {[
                ['01','Рынок','Спрос, конкуренты, цены, объявления'],
                ['02','Ваш бизнес','Лиды, чек, конверсия, скорость ответа'],
                ['03','Результат','Итоговая оценка + точки роста + действия']
              ].map(([n,t,d])=><div key={n} className="flex gap-3"><div className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-[#edf4ff] text-xs font-black text-[#2878ff]">{n}</div><div><div className="font-black text-[#15213b]">{t}</div><div className="mt-1 text-sm leading-5 text-[#53617a]">{d}</div></div></div>)}
            </div>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-3">
            <div className="audit-reference-card p-4"><div className="text-xs font-bold text-[#8490a6]">На выходе</div><div className="mt-2 text-xl font-black text-[#15213b]">2 оценки</div><div className="text-xs text-[#53617a]">рынок + продажи</div></div>
            <div className="audit-reference-card p-4"><div className="text-xs font-bold text-[#8490a6]">Рекомендация</div><div className="mt-2 text-xl font-black text-[#15213b]">Что делать</div><div className="text-xs text-[#53617a]">по приоритету</div></div>
          </div>
        </div>
        <form onSubmit={submit} className="audit-reference-card p-6 sm:p-8">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-xs font-black uppercase tracking-[.08em] text-[#2878ff]">Шаг 1 · рынок</div>
              <h2 className="mt-1 text-2xl font-black text-[#15213b]">Что вы продаёте и где</h2>
            </div>
            <div className="rounded-[10px] bg-[#f4f6fa] px-3 py-2 text-xs font-black text-[#53617a]">1 / 2</div>
          </div>
          <div className="mt-5 grid gap-4">
            <label className="grid gap-2"><span className="font-extrabold">Что вы продаёте?</span><input required value={market.query} onChange={e=>setMarket(v=>({...v,query:e.target.value}))} placeholder="Например: кухни на заказ" className="h-14 rounded-2xl border border-[#dce3ee] bg-[#fbfcfe] px-4 font-bold outline-none focus:border-[#2563eb]"/><span className="text-xs text-[#98a2b3]">Товар, услуга или основное направление</span></label>
            <label className="grid gap-2"><span className="font-extrabold">Где вы работаете?</span><input required value={market.region} onChange={e=>setMarket(v=>({...v,region:e.target.value}))} placeholder="Например: Казань" className="h-14 rounded-2xl border border-[#dce3ee] bg-[#fbfcfe] px-4 font-bold outline-none focus:border-[#2563eb]"/><span className="text-xs text-[#98a2b3]">Город или регион для анализа рынка</span></label>
            <div className="grid gap-2">
              <label className="grid gap-2"><span className="font-extrabold">Ссылка на Avito или сайт <span className="font-normal text-[#98a2b3]">— необязательно</span></span><input value={market.source_url} onChange={e=>{setMarket(v=>({...v,source_url:e.target.value}));setSourceHint(null);}} placeholder="https://..." className="h-14 rounded-2xl border border-[#dce3ee] bg-[#fbfcfe] px-4 font-bold outline-none focus:border-[#2563eb]"/><span className="text-xs text-[#98a2b3]">Поможет BORIS точнее понять направление; без ссылки аудит тоже работает</span></label>
              <button type="button" onClick={analyzeSource} disabled={hintBusy||!market.source_url.trim()} className="justify-self-start rounded-[10px] border border-[#dfe6f2] bg-white px-4 py-3 text-sm font-black text-[#2878ff] disabled:opacity-50">{hintBusy?'Смотрю страницу…':'Понять направление по ссылке'}</button>
              {sourceHint&&<div className="rounded-[14px] border border-[#d9e6ff] bg-[#f8faff] p-4">
                <div className="text-xs font-black uppercase tracking-[.08em] text-[#2878ff]">BORIS нашёл подсказку</div>
                <div className="mt-2 text-lg font-black text-[#15213b]">{sourceHint.suggested_query||'Направление не определено уверенно'}</div>
                <div className="mt-2 text-xs leading-5 text-[#53617a]">Источник: {sourceHint.source_host||'публичная страница'}{sourceHint.category_slug?' · категория Avito: '+String(sourceHint.category_slug).replaceAll('_',' '):''}</div>
                {sourceHint.suggested_query&&<div className="mt-4 flex flex-wrap gap-2"><button type="button" onClick={()=>{setMarket(v=>({...v,query:String(sourceHint.suggested_query||'')}));setSourceHint((x:any)=>({...x,accepted:true}));}} className="rounded-[10px] bg-[#2878ff] px-4 py-3 text-sm font-black text-white">Да, использовать</button><button type="button" onClick={()=>setSourceHint(null)} className="rounded-[10px] border border-[#dfe6f2] bg-white px-4 py-3 text-sm font-black text-[#53617a]">Изменить вручную</button></div>}
              </div>}
            </div>
          </div>
          <div className="my-7 h-px bg-[#edf1f7]"/>
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-xs font-black uppercase tracking-[.08em] text-[#7f56d9]">Шаг 2 · ваш бизнес</div>
              <h2 className="mt-1 text-2xl font-black text-[#15213b]">Что известно о продажах</h2>
              <p className="mt-1 text-sm text-[#53617a]">Если точных цифр нет, можно начать только с рынка.</p>
            </div>
            <div className="rounded-[10px] bg-[#f6f1ff] px-3 py-2 text-xs font-black text-[#7f56d9]">2 / 2</div>
          </div>
          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            {fields.map(([k,label,hint])=><label key={k} className="grid gap-2">
              <span className="font-extrabold">{label}</span>
              <input type="number" min="0" step={k==='conversion_percent'?'0.1':'1'} value={(form as any)[k]} onChange={e=>setForm(v=>({...v,[k]:Number(e.target.value)}))} className="h-14 rounded-2xl border border-[#dce3ee] bg-[#fbfcfe] px-4 text-lg font-bold outline-none focus:border-[#2563eb]"/>
              <span className="text-xs text-[#98a2b3]">{hint}</span>
            </label>)}
          </div>
          {error&&<div className="mt-5 rounded-2xl bg-red-50 p-4 text-sm font-bold text-red-700">{error}</div>}
          <button disabled={busy} className="mt-7 w-full rounded-full bg-[#2563eb] px-6 py-4 font-black text-white disabled:opacity-60">{busy?'BORIS считает…':'Получить полный аудит BORIS'}</button>
          <button type="button" onClick={startMarketOnly} disabled={busy} className="mt-3 w-full rounded-[10px] border border-[#dfe6f2] bg-white px-6 py-4 font-black text-[#2878ff] disabled:opacity-60">Пока получить только аудит рынка</button>
          <p className="mt-3 text-center text-xs leading-5 text-[#8490a6]">Можно начать только со спроса, конкурентов и количества объявлений — свои показатели бизнеса добавить позже.</p>
        </form>
      </div>
    </section>
  </main>;
}
