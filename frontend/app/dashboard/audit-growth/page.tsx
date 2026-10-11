'use client';

import { useEffect, useMemo, useState } from 'react';
import AppShell from '@/app/components/shell/AppShell';

function auth(json=false):Record<string,string>{const t=typeof window!=='undefined'?localStorage.getItem('boris_token')||'':'';const h:Record<string,string>={};if(t)h.Authorization='Bearer '+t;if(json)h['Content-Type']='application/json';return h;}

export default function AuditGrowthPage(){
  const [funnel,setFunnel]=useState<any>({});
  const [ocean,setOcean]=useState<any>({summary:{},by_source:{tg:{},vk:{}},posts:[],rows:[]});
  const [leads,setLeads]=useState<any[]>([]);
  const [contact,setContact]=useState<any>({whatsapp_url:'',max_url:'',phone:'',message_template:'',contact_name:'Кирилл О.',contact_title:'Разработчик BORIS',trial_enabled:true,trial_days:4,trial_title:'',trial_description:''});
  const [saved,setSaved]=useState('');
  const [error,setError]=useState('');
  const [leadFilter,setLeadFilter]=useState('all');
  const [leadQuery,setLeadQuery]=useState('');
  const [costs,setCosts]=useState<any>({summary:{},by_kind:{},alerts:[],recent:[]});
  const [audits,setAudits]=useState<any[]>([]);
  const [auditPeriod,setAuditPeriod]=useState('30d');
  const [auditNiche,setAuditNiche]=useState('');
  const [auditCity,setAuditCity]=useState('');
  const [auditReferral,setAuditReferral]=useState('all');
  const [auditSource,setAuditSource]=useState('all');
  const [auditMode,setAuditMode]=useState('all');
  const [auditReadiness,setAuditReadiness]=useState('ALL');

  async function load(){
    setError('');
    try{
      const auditParams=new URLSearchParams({period:auditPeriod});
      if(auditNiche.trim())auditParams.set('niche',auditNiche.trim());
      if(auditCity.trim())auditParams.set('city',auditCity.trim());
      if(auditReferral!=='all')auditParams.set('referral',auditReferral);
      if(auditSource!=='all')auditParams.set('source',auditSource);
      if(auditMode!=='all')auditParams.set('mode',auditMode);
      if(auditReadiness!=='ALL')auditParams.set('readiness',auditReadiness);
      const [f,o,l,c,costData,auditData]=await Promise.all([
        fetch('/api/viral/owner/funnel',{headers:auth(),cache:'no-store'}).then(async r=>{const d=await r.json();if(!r.ok)throw new Error(d?.detail||'Нет доступа');return d;}),
        fetch('/api/viral/owner/ocean-funnel',{headers:auth(),cache:'no-store'}).then(async r=>{const d=await r.json();if(r.status===404)return {status:'pending',summary:{},by_source:{tg:{},vk:{}},posts:[],rows:[]};if(!r.ok)throw new Error(d?.detail||'Не удалось загрузить Ocean funnel');return d;}),
        fetch('/api/viral/owner/leads',{headers:auth(),cache:'no-store'}).then(async r=>{const d=await r.json();if(!r.ok)throw new Error(d?.detail||'Нет доступа');return d;}),
        fetch('/api/viral/sales-contact',{cache:'no-store'}).then(r=>r.json()),
        fetch('/api/viral/owner/costs',{headers:auth(),cache:'no-store'}).then(async r=>{const d=await r.json();if(!r.ok)throw new Error(d?.detail||'Не удалось загрузить экономику аудита');return d;}),
        fetch('/api/viral/owner/audits?'+auditParams.toString(),{headers:auth(),cache:'no-store'}).then(async r=>{const d=await r.json();if(r.status===404)return {status:'pending',audits:[]};if(!r.ok)throw new Error(d?.detail||'Не удалось загрузить реестр аудитов');return d;}),
      ]);
      setFunnel(f.funnel||{});setOcean(o||{summary:{},by_source:{tg:{},vk:{}},posts:[],rows:[]});setLeads(l.leads||[]);setContact(c||{});setCosts(costData||{summary:{},by_kind:{},alerts:[],recent:[]});setAudits(auditData.audits||[]);
    }catch(e:any){setError(e?.message||'Не удалось загрузить Audit Growth');}
  }
  useEffect(()=>{load();},[]);

  const metrics=[
    ['Посетили /audit',funnel.audit_page_views||0],
    ['Регистрации',funnel.registrations||0],
    ['Вошли в Gateway',funnel.verified_or_entered_gateway||0],
    ['Завершили аудит',funnel.audit_completed||0],
    ['Market audit',funnel.market_audit_completed||0],
    ['Connected audit',funnel.connected_audit_completed||0],
    ['Trial CTA',funnel.trial_cta_clicked||0],
    ['Trial started',funnel.trial_started||0],
    ['Открыли BORIS',funnel.main_boris_entered||0],
    ['Специалист',funnel.specialist_contacted||0],
    ['WhatsApp',funnel.whatsapp_clicks||0],
    ['MAX',funnel.max_clicks||0],
    ['Открыли public audit',funnel.audit_opened||funnel.share_opens||0],
    ['Дочитали public audit',funnel.audit_completed_view||0],
    ['Поделились',funnel.share_completed||0],
    ['Referral visits',funnel.referral_visit||0],
    ['Referral registrations',funnel.registration_completed||0],
    ['Qualified referrals',funnel.referral_qualified||0],
    ['Referral trials',funnel.referral_trial_started||0],
    ['Referral rewards',funnel.referral_rewards_granted||0],
    ['Выдано дней',funnel.referral_reward_days||0],
  ];

  const rates=useMemo(()=>{
    const pct=(a:number,b:number)=>b>0?Math.round(a/b*1000)/10:null;
    return [
      ['Visitor → Registration',pct(funnel.registrations||0,funnel.audit_page_views||0)],
      ['Gateway → Audit',pct(funnel.audit_completed||0,funnel.verified_or_entered_gateway||0)],
      ['Audit → Market',pct(funnel.market_audit_completed||0,funnel.audit_completed||0)],
      ['Audit → Connected',pct(funnel.connected_audit_completed||0,funnel.audit_completed||0)],
      ['Audit → Trial CTA',pct(funnel.trial_cta_clicked||0,funnel.audit_completed||0)],
      ['Trial CTA → Start',pct(funnel.trial_started||0,funnel.trial_cta_clicked||0)],
      ['Audit → Main BORIS',pct(funnel.main_boris_entered||0,funnel.audit_completed||0)],
      ['Audit → Specialist',pct(funnel.specialist_contacted||0,funnel.audit_completed||0)],
      ['Public audit → CTA',pct(funnel.cta_test_clicked||0,funnel.audit_opened||0)],
      ['Public audit → Share',pct(funnel.share_completed||0,funnel.audit_opened||0)],
      ['Referral → Registration',pct(funnel.registration_completed||0,funnel.referral_visit||0)],
      ['Registration → Qualified',pct(funnel.referral_qualified||0,funnel.registration_completed||0)],
      ['Qualified → Trial',pct(funnel.referral_trial_started||0,funnel.referral_qualified||0)],
    ];
  },[funnel]);

  const visibleLeads=useMemo(()=>{
    const q=leadQuery.trim().toLowerCase();
    return leads.filter((l:any)=>{
      const stage=String(l.stage||'').toUpperCase();
      const mode=String(l.mode||'').toLowerCase();
      const filterOk =
        leadFilter==='all' ||
        (leadFilter==='manual'&&mode!=='connected') ||
        (leadFilter==='connected'&&mode==='connected') ||
        (leadFilter==='market'&&Boolean(l.market_score!=null)) ||
        (leadFilter==='trial'&&['TRIAL_INTEREST','TRIAL_STARTED','PAID'].includes(stage)) ||
        (leadFilter==='boris'&&['MAIN_BORIS_ENTERED','SPECIALIST_CONTACTED','PAID'].includes(stage)) ||
        (leadFilter==='specialist'&&['SPECIALIST_CONTACTED','PAID'].includes(stage)) ||
        (leadFilter==='paid'&&stage==='PAID');
      if(!filterOk)return false;
      if(!q)return true;
      return String(l.company||'').toLowerCase().includes(q)||String(l.email||'').toLowerCase().includes(q);
    });
  },[leads,leadFilter,leadQuery]);

  async function saveContact(){
    setSaved('');
    const r=await fetch('/api/viral/owner/sales-contact',{method:'POST',headers:auth(true),body:JSON.stringify({
      whatsapp_url:contact.whatsapp_url||'',max_url:contact.max_url||'',phone:contact.phone||'',message_template:contact.message_template||'',
      contact_name:contact.contact_name||'Кирилл О.',contact_title:contact.contact_title||'Разработчик BORIS',
      trial_enabled:Boolean(contact.trial_enabled),trial_days:Number(contact.trial_days||4),
      trial_title:contact.trial_title||'',trial_description:contact.trial_description||''
    })});
    const d=await r.json();
    if(r.ok){setContact(d.contact||contact);setSaved('Сохранено');}else setSaved(d?.detail||'Ошибка');
  }

  return <AppShell active="audit-growth" title="Audit Growth" subtitle="Воронка регистрации, аудитов, подключения данных и перехода в основной BORIS">
    <div className="mx-auto max-w-7xl">
      <div className="flex flex-wrap items-end justify-between gap-4"><div><div className="text-sm font-black text-[#2878ff]">OWNER · AUDIT GROWTH</div><h1 className="mt-2 text-4xl font-black tracking-[-.035em] text-[#15213b]">Воронка BORIS Audit Gateway</h1><p className="mt-2 text-[#53617a]">Регистрация → аудит → реальные данные → BORIS → специалист.</p></div><button onClick={load} className="rounded-[10px] border border-[#dfe6f2] bg-white px-5 py-3 font-black text-[#2878ff]">Обновить</button></div>
      {error&&<div className="mt-5 rounded-[14px] bg-red-50 p-4 font-bold text-red-700">{error}</div>}

      <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">{metrics.map(([k,v]:any)=><div key={k} className="rounded-[18px] border border-[#dfe6f2] bg-white p-5 shadow-sm"><div className="text-3xl font-black text-[#15213b]">{v}</div><div className="mt-2 text-sm font-bold text-[#53617a]">{k}</div></div>)}</div>
      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">{rates.map(([k,v]:any)=><div key={k} className="rounded-[18px] border border-[#dfe6f2] bg-[#f8faff] p-5"><div className="text-2xl font-black text-[#2878ff]">{v==null?'—':v+'%'}</div><div className="mt-2 text-xs font-bold text-[#53617a]">{k}</div></div>)}</div>


      <div className="mt-8 rounded-[24px] border border-[#d9e6ff] bg-[linear-gradient(145deg,#f8fbff,#f7f4ff)] p-6 shadow-sm">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-sm font-black text-[#2878ff]">OCEAN → AUDIT</div>
            <h2 className="mt-2 text-2xl font-black text-[#15213b]">Реальная воронка TG / VK</h2>
            <p className="mt-2 text-sm text-[#53617a]">QA, localhost и bot-трафик исключены. Проценты скрыты до 30 реальных переходов.</p>
          </div>
          <div className="rounded-[12px] bg-white px-4 py-3 text-sm font-black text-[#15213b]">{ocean.summary?.visits||0} переходов</div>
        </div>

        <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
          {[
            ['Переходы',ocean.summary?.visits||0],
            ['Начали Audit',ocean.summary?.audits||0],
            ['Регистрации',ocean.summary?.registrations||0],
            ['Получили результат',ocean.summary?.results||0],
            ['Trial CTA',ocean.summary?.trial_cta||0],
            ['Connected',ocean.summary?.connected||0],
          ].map(([label,value]:any)=><div key={label} className="rounded-[18px] border border-[#e1e8f5] bg-white p-5"><div className="text-3xl font-black text-[#15213b]">{value}</div><div className="mt-2 text-xs font-bold text-[#53617a]">{label}</div></div>)}
        </div>

        {(ocean.alerts||[]).length>0&&<div className="mt-5 grid gap-2">{(ocean.alerts||[]).map((a:any,i:number)=><div key={(a.code||'alert')+'|'+(a.post_id||'')+'|'+i} className={"rounded-[14px] border px-4 py-3 text-sm font-bold "+(a.severity==='critical'?'border-red-200 bg-red-50 text-red-800':'border-amber-200 bg-amber-50 text-amber-900')}><div>{a.message||a.code}</div><div className="mt-1 text-xs opacity-70">{a.post_id?'post '+a.post_id:''}{a.platform?' · '+String(a.platform).toUpperCase():''}{a.age_hours!=null?' · '+a.age_hours+' ч':''}</div></div>)}</div>}

        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          {[
            ['Telegram',ocean.by_source?.tg||{}],
            ['VK',ocean.by_source?.vk||{}],
          ].map(([label,x]:any)=><div key={label} className="rounded-[20px] border border-[#dfe6f2] bg-white p-5">
            <div className="flex items-center justify-between gap-4"><div className="text-lg font-black text-[#15213b]">{label}</div><div className="text-sm font-black text-[#2878ff]">{x.visits||0} переходов</div></div>
            <div className="mt-4 grid grid-cols-5 gap-2 text-center">
              {[['Audit',x.audits],['Рег.',x.registrations],['Результат',x.results],['Trial',x.trial_cta],['Connected',x.connected]].map(([k,v]:any)=><div key={k} className="rounded-[12px] bg-[#f8faff] p-3"><div className="text-xl font-black">{v||0}</div><div className="mt-1 text-[10px] font-bold text-[#8490a6]">{k}</div></div>)}
            </div>
            <div className="mt-4 grid gap-2 sm:grid-cols-5">
              {[
                ['Click → Audit',x.rates?.visit_to_audit],
                ['Audit → Reg',x.rates?.audit_to_registration],
                ['Reg → Result',x.rates?.registration_to_result],
                ['Result → Trial',x.rates?.result_to_trial],
                ['Trial → Conn.',x.rates?.trial_to_connected],
              ].map(([k,v]:any)=><div key={k} className="text-xs"><b className="block text-[#2878ff]">{v==null?'—':v+'%'}</b><span className="text-[#8490a6]">{k}</span></div>)}
            </div>
          </div>)}
        </div>

        <div className="mt-6 overflow-x-auto">
          <table className="w-full min-w-[940px] text-left text-sm">
            <thead><tr className="border-b border-[#e4eaf4] text-[#8490a6]"><th className="py-3">Источник</th><th>Post / utm_content</th><th>Переходы</th><th>Audit</th><th>Регистрации</th><th>Результат</th><th>Trial</th><th>Connected</th></tr></thead>
            <tbody>{(ocean.posts||[]).slice(0,30).map((p:any,i:number)=><tr key={(p.source||'')+'|'+(p.post||'')+'|'+i} className="border-b border-[#eef2f7]"><td className="py-3 font-black text-[#15213b]">{p.source||'Ocean'}</td><td className="max-w-[320px] truncate" title={p.post||''}>{p.post||'—'}</td><td>{p.visits||0}</td><td>{p.audits||0}</td><td>{p.registrations||0}</td><td>{p.results||0}</td><td>{p.trial_cta||0}</td><td>{p.connected||0}</td></tr>)}</tbody>
          </table>
        </div>
      </div>

      <div className="mt-8 rounded-[18px] border border-[#dfe6f2] bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div><div className="text-sm font-black text-[#7f56d9]">ЭКОНОМИКА АУДИТА</div><h2 className="mt-2 text-2xl font-black text-[#15213b]">Скорость, себестоимость и стабильность</h2><p className="mt-2 text-sm text-[#53617a]">Только фактические run-записи. Fixed cost сервера сюда не смешивается.</p></div>
          <div className="rounded-[10px] bg-[#f8faff] px-4 py-3 text-sm font-black text-[#15213b]">{costs.summary?.total_runs||0} запусков</div>
        </div>

        {(costs.alerts||[]).length>0&&<div className="mt-5 grid gap-2">{costs.alerts.map((a:any)=><div key={a.code} className="rounded-[12px] border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-bold text-amber-900">{a.text}</div>)}</div>}

        <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
          <div className="rounded-[16px] bg-[#f8faff] p-5"><div className="text-3xl font-black text-[#15213b]">{Number(costs.summary?.avg_variable_cost_rub||0).toFixed(2)} ₽</div><div className="mt-2 text-xs font-bold text-[#53617a]">средняя переменная стоимость</div></div>
          <div className="rounded-[16px] bg-[#f8faff] p-5"><div className="text-3xl font-black text-[#15213b]">{costs.summary?.market_cache_hit_rate||0}%</div><div className="mt-2 text-xs font-bold text-[#53617a]">Market cache hit</div></div>
          <div className="rounded-[16px] bg-[#f8faff] p-5"><div className="text-3xl font-black text-[#15213b]">{costs.summary?.browser_success_rate||0}%</div><div className="mt-2 text-xs font-bold text-[#53617a]">Avito success</div></div>
          <div className="rounded-[16px] bg-[#f8faff] p-5"><div className="text-3xl font-black text-[#15213b]">{costs.summary?.wordstat_success_rate||0}%</div><div className="mt-2 text-xs font-bold text-[#53617a]">Wordstat success</div></div>
          <div className="rounded-[16px] bg-[#f8faff] p-5"><div className="text-3xl font-black text-[#15213b]">{Math.round(Number(costs.summary?.market_cold_p95_ms||0)/100)/10} c</div><div className="mt-2 text-xs font-bold text-[#53617a]">cold Market p95</div></div>
          <div className="rounded-[16px] bg-[#f8faff] p-5"><div className={"text-xl font-black "+(costs.wordstat?.status==='READY'?'text-emerald-700':'text-amber-800')}>{costs.wordstat?.status||'UNKNOWN'}</div><div className="mt-2 text-xs font-bold text-[#53617a]">Wordstat API</div>{costs.wordstat?.cost_rub!=null&&<div className="mt-1 text-[11px] text-[#8490a6]">{Number(costs.wordstat.cost_rub).toFixed(2)} ₽ / запрос</div>}</div>
          <div className="rounded-[16px] bg-[#f8faff] p-5"><div className={"text-xl font-black "+(costs.market_route?.state==='READY'?'text-emerald-700':costs.market_route?.state==='AVITO_BLOCKED'?'text-amber-800':'text-[#8490a6]')}>{costs.market_route?.state||'UNKNOWN'}</div><div className="mt-2 text-xs font-bold text-[#53617a]">Avito market route</div>{costs.market_route?.detail&&<div className="mt-1 text-[11px] text-[#8490a6]">{costs.market_route.detail}</div>}</div>
          <div className="rounded-[16px] bg-[#f8faff] p-5"><div className={"text-xl font-black "+(costs.market_browser?.status==='ONLINE'?'text-emerald-700':costs.market_browser?.status==='UNCONFIGURED'?'text-[#8490a6]':'text-amber-800')}>{costs.market_browser?.status||'UNCONFIGURED'}</div><div className="mt-2 text-xs font-bold text-[#53617a]">Audit Market Browser</div>{costs.market_browser?.device_name&&<div className="mt-1 text-[11px] text-[#8490a6]">{costs.market_browser.device_name}</div>}</div>
        </div>

        <div className="mt-6 grid gap-4 lg:grid-cols-3">
          {[
            ['manual','Manual Audit'],
            ['connected','Connected Avito'],
            ['market','Market Audit'],
          ].map(([key,label])=>{
            const x=costs.by_kind?.[key]||{};
            return <div key={key} className="rounded-[16px] border border-[#dfe6f2] bg-white p-5">
              <div className="font-black text-[#15213b]">{label}</div>
              <div className="mt-4 grid grid-cols-2 gap-3">
                <div><div className="text-xl font-black text-[#2878ff]">{Math.round(Number(x.p50_ms||0))/1000} c</div><div className="text-xs text-[#8490a6]">p50</div></div>
                <div><div className="text-xl font-black text-[#2878ff]">{Math.round(Number(x.p95_ms||0))/1000} c</div><div className="text-xs text-[#8490a6]">p95</div></div>
                <div><div className="text-xl font-black text-[#15213b]">{Number(x.avg_cost_rub||0).toFixed(2)} ₽</div><div className="text-xs text-[#8490a6]">средняя стоимость</div></div>
                <div><div className="text-xl font-black text-[#15213b]">{x.runs||0}</div><div className="text-xs text-[#8490a6]">успешных запусков</div></div>
              </div>
            </div>
          })}
        </div>
      </div>

      <div className="mt-8 rounded-[18px] border border-[#dfe6f2] bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div><div className="text-sm font-black text-[#2878ff]">OWNER · AUDIT REGISTER</div><h2 className="mt-2 text-2xl font-black text-[#15213b]">Реестр аудитов</h2><p className="mt-1 text-sm text-[#53617a]">Последние запуски с готовностью, временем, источником и ошибками.</p></div>
          <div className="text-sm font-black text-[#53617a]">{audits.length} записей</div>
        </div>
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
          <select value={auditPeriod} onChange={e=>setAuditPeriod(e.target.value)} className="h-11 rounded-[10px] border border-[#dfe6f2] bg-white px-3 font-bold text-[#15213b]"><option value="today">Сегодня</option><option value="7d">7 дней</option><option value="30d">30 дней</option><option value="90d">90 дней</option><option value="all">Весь период</option></select>
          <input value={auditNiche} onChange={e=>setAuditNiche(e.target.value)} placeholder="Ниша" className="h-11 rounded-[10px] border border-[#dfe6f2] px-3"/>
          <input value={auditCity} onChange={e=>setAuditCity(e.target.value)} placeholder="Город" className="h-11 rounded-[10px] border border-[#dfe6f2] px-3"/>
          <select value={auditReferral} onChange={e=>setAuditReferral(e.target.value)} className="h-11 rounded-[10px] border border-[#dfe6f2] bg-white px-3 font-bold text-[#15213b]"><option value="all">Referral: все</option><option value="referral">Referral</option><option value="non_referral">Без referral</option></select>
          <select value={auditSource} onChange={e=>setAuditSource(e.target.value)} className="h-11 rounded-[10px] border border-[#dfe6f2] bg-white px-3 font-bold text-[#15213b]"><option value="all">Источник: все</option><option value="Ocean TG">Ocean TG</option><option value="Ocean VK">Ocean VK</option><option value="Direct">Direct</option><option value="Referral">Referral</option><option value="Email">Email</option><option value="Other">Other</option></select>
          <select value={auditMode} onChange={e=>setAuditMode(e.target.value)} className="h-11 rounded-[10px] border border-[#dfe6f2] bg-white px-3 font-bold text-[#15213b]"><option value="all">Все типы</option><option value="manual">Manual</option><option value="market">Market</option><option value="connected">Connected</option></select>
          <select value={auditReadiness} onChange={e=>setAuditReadiness(e.target.value)} className="h-11 rounded-[10px] border border-[#dfe6f2] bg-white px-3 font-bold text-[#15213b]"><option value="ALL">Все статусы</option><option value="READY">READY</option><option value="PARTIAL_READY">PARTIAL_READY</option><option value="FAILED">FAILED</option><option value="COLLECTING">COLLECTING</option></select>
          <button onClick={load} className="h-11 rounded-[10px] bg-[#2878ff] px-4 font-black text-white">Применить</button>
        </div>
        <div className="mt-5 overflow-x-auto"><table className="w-full min-w-[1880px] text-left text-sm"><thead><tr className="border-b border-[#edf1f7] text-[#8490a6]"><th className="py-3">Дата</th><th>Пользователь</th><th>Компания</th><th>Ниша</th><th>Город</th><th>Тип</th><th>Источник</th><th>Campaign</th><th>Post / content</th><th>Status</th><th>Readiness</th><th>Время</th><th>Referral</th><th>Trial</th><th>Connected</th><th>Ошибка</th></tr></thead><tbody>{audits.map((a:any)=><tr key={a.run_id||a.audit_id} className="border-b border-[#f0f2f6]"><td className="py-4 whitespace-nowrap">{a.date?new Date(a.date).toLocaleString('ru-RU'):'—'}</td><td>{a.user||'—'}</td><td className="font-black text-[#15213b]">{a.company||a.account_id||'—'}</td><td>{a.niche||'—'}</td><td>{a.city||'—'}</td><td className="font-bold">{a.type||'—'}</td><td>{a.source||'Direct'}</td><td>{a.campaign||'—'}</td><td className="max-w-[260px] truncate" title={a.post||''}>{a.post||'—'}</td><td>{a.status||'—'}</td><td><span className={"rounded-full px-2 py-1 text-xs font-black "+(a.readiness==='READY'?'bg-emerald-50 text-emerald-700':a.readiness==='FAILED'?'bg-red-50 text-red-700':a.readiness==='PARTIAL_READY'?'bg-amber-50 text-amber-800':'bg-[#f2f5fa] text-[#53617a]')}>{a.readiness||'—'}</span></td><td>{a.duration_ms!=null?(Math.round(Number(a.duration_ms)/100)/10)+' c':'—'}</td><td>{a.referral?(a.referral_code||'Да'):'—'}</td><td>{a.trial_interest?'Да':'—'}</td><td>{a.connected?'Да':'—'}</td><td className="max-w-[280px] truncate text-red-700" title={a.error||''}>{a.error||'—'}</td></tr>)}</tbody></table></div>

        <div className="my-8 border-t border-[#edf1f7]"/>
        <div className="flex flex-wrap items-center justify-between gap-4"><div><h2 className="text-2xl font-black text-[#15213b]">Audit leads</h2><div className="mt-1 text-sm font-bold text-[#53617a]">{visibleLeads.length} из {leads.length}</div></div><input value={leadQuery} onChange={e=>setLeadQuery(e.target.value)} placeholder="Компания или email" className="h-11 min-w-[240px] rounded-[10px] border border-[#dfe6f2] px-4 outline-none focus:border-[#2878ff]"/></div>
        <div className="mt-4 flex flex-wrap gap-2">{[['all','Все'],['manual','Manual'],['connected','Connected'],['market','Market'],['trial','Trial'],['boris','Main BORIS'],['specialist','Specialist'],['paid','Paid']].map(([key,label])=><button key={key} onClick={()=>setLeadFilter(key)} className={"rounded-[10px] border px-4 py-2 text-sm font-black "+(leadFilter===key?'border-[#2878ff] bg-[#edf4ff] text-[#2878ff]':'border-[#dfe6f2] bg-white text-[#53617a]')}>{label}</button>)}</div>
        <div className="mt-5 overflow-x-auto"><table className="w-full min-w-[1180px] text-left text-sm"><thead><tr className="border-b border-[#edf1f7] text-[#8490a6]"><th className="py-3">Компания</th><th>Email</th><th>Источник</th><th>Sales</th><th>Market</th><th>Спрос</th><th>Рынок</th><th>Объявления</th><th>Тест</th><th>Этап</th><th>Последнее действие</th></tr></thead><tbody>{visibleLeads.map((l:any)=><tr key={l.account_id} className="border-b border-[#f0f2f6]"><td className="py-4 font-black text-[#15213b]">{l.company}</td><td>{l.email||'—'}</td><td>{l.first_touch?.referral_code?('ref:'+l.first_touch.referral_code):(l.first_touch?.utm_source||'direct')}</td><td className="font-black">{l.score??'—'}</td><td className="font-black">{l.market_score??'—'}</td><td>{l.demand_level||'—'}</td><td><div className="font-bold">{l.competitors!=null?l.competitors:(l.estimated_competitors!=null?'≈ '+l.estimated_competitors:(l.competing_offers??'—'))}</div>{l.market_category_slug&&<div className="mt-1 text-xs text-[#8490a6]">{String(l.market_category_slug).replaceAll('_',' ')}</div>}</td><td>{l.recommended_ads?.grounded===false?'—':(l.recommended_ads?.low!=null?l.recommended_ads.low+'–'+l.recommended_ads.high:'—')}</td><td>{l.trial_interest?'Да':'—'}</td><td>{l.stage}</td><td>{l.updated_at?new Date(l.updated_at).toLocaleString('ru-RU'):'—'}</td></tr>)}</tbody></table></div>
      </div>

      <div className="mt-8 rounded-[18px] border border-[#dfe6f2] bg-white p-6 shadow-sm">
        <h2 className="text-2xl font-black text-[#15213b]">Настройки связи со специалистом</h2>
        <p className="mt-2 text-sm text-[#53617a]">Эти контакты Audit Gateway показывает после результата. Менять код для обновления ссылок не нужно.</p>
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <label className="grid gap-2"><span className="font-bold">Имя специалиста</span><input value={contact.contact_name||''} onChange={e=>setContact((v:any)=>({...v,contact_name:e.target.value}))} className="h-13 rounded-[10px] border border-[#dfe6f2] px-4"/></label>
          <label className="grid gap-2"><span className="font-bold">Подпись / роль</span><input value={contact.contact_title||''} onChange={e=>setContact((v:any)=>({...v,contact_title:e.target.value}))} className="h-13 rounded-[10px] border border-[#dfe6f2] px-4"/></label>
          <label className="grid gap-2"><span className="font-bold">WhatsApp URL</span><input value={contact.whatsapp_url||''} onChange={e=>setContact((v:any)=>({...v,whatsapp_url:e.target.value}))} className="h-13 rounded-[10px] border border-[#dfe6f2] px-4"/></label>
          <label className="grid gap-2"><span className="font-bold">MAX URL</span><input value={contact.max_url||''} onChange={e=>setContact((v:any)=>({...v,max_url:e.target.value}))} className="h-13 rounded-[10px] border border-[#dfe6f2] px-4"/></label>
          <label className="grid gap-2"><span className="font-bold">Телефон</span><input value={contact.phone||''} onChange={e=>setContact((v:any)=>({...v,phone:e.target.value}))} className="h-13 rounded-[10px] border border-[#dfe6f2] px-4"/></label>
          <label className="grid gap-2"><span className="font-bold">Шаблон сообщения</span><input value={contact.message_template||''} onChange={e=>setContact((v:any)=>({...v,message_template:e.target.value}))} className="h-13 rounded-[10px] border border-[#dfe6f2] px-4"/></label>
          <label className="flex items-center justify-between gap-4 rounded-[14px] bg-[#f8faff] p-4"><span><b>Бесплатный тест</b><span className="mt-1 block text-xs text-[#53617a]">Показывать оффер после аудита</span></span><input type="checkbox" checked={Boolean(contact.trial_enabled)} onChange={e=>setContact((v:any)=>({...v,trial_enabled:e.target.checked}))}/></label>
          <label className="grid gap-2"><span className="font-bold">Дней теста</span><input type="number" min="1" max="30" value={contact.trial_days||4} onChange={e=>setContact((v:any)=>({...v,trial_days:Number(e.target.value)}))} className="h-13 rounded-[10px] border border-[#dfe6f2] px-4"/></label>
          <label className="grid gap-2"><span className="font-bold">Заголовок теста</span><input value={contact.trial_title||''} onChange={e=>setContact((v:any)=>({...v,trial_title:e.target.value}))} placeholder="Попробуйте BORIS бесплатно 4 дня" className="h-13 rounded-[10px] border border-[#dfe6f2] px-4"/></label>
          <label className="grid gap-2"><span className="font-bold">Описание теста</span><input value={contact.trial_description||''} onChange={e=>setContact((v:any)=>({...v,trial_description:e.target.value}))} placeholder="Начнём с найденных в аудите точек" className="h-13 rounded-[10px] border border-[#dfe6f2] px-4"/></label>
        </div>
        <div className="mt-5 flex items-center gap-4"><button onClick={saveContact} className="rounded-[10px] bg-[#2878ff] px-6 py-3 font-black text-white">Сохранить</button>{saved&&<span className="text-sm font-bold text-[#53617a]">{saved}</span>}</div>
      </div>
    </div>
  </AppShell>;
}
