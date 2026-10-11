'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { auditAttribution, emailGoal, fireAuditEvent } from '../lib/attribution';

const money=(n:number)=>new Intl.NumberFormat('ru-RU').format(Math.max(0,Math.round(n)));
function token(){ return typeof window!=='undefined'?localStorage.getItem('boris_token')||'':''; }
function auth(): Record<string,string>{ const t=token(); const h:Record<string,string>={'Content-Type':'application/json'}; if(t)h.Authorization='Bearer '+t; return h; }
export default function AuditResultPage(){
  const [auditId,setAuditId]=useState('');
  const [data,setData]=useState<any>(null);
  const [marketData,setMarketData]=useState<any>(null);
  const [marketAuditId,setMarketAuditId]=useState('');
  const [marketState,setMarketState]=useState<'idle'|'loading'|'ready'|'unavailable'>('idle');
  const [contact,setContact]=useState<any>(null);
  const [recommendations,setRecommendations]=useState<any[]>([]);
  const [error,setError]=useState('');
  const [share,setShare]=useState('');
  const [shareToken,setShareToken]=useState('');
  const [revokeSecret,setRevokeSecret]=useState('');
  const [question,setQuestion]=useState('');
  const [auditAnswer,setAuditAnswer]=useState<any>(null);
  const [askBusy,setAskBusy]=useState(false);
  const [trialClicked,setTrialClicked]=useState(false);

  useEffect(()=>{
    if(!token()){
      localStorage.setItem('boris_post_login_return',window.location.pathname+window.location.search);
      window.location.replace('/login');
      return;
    }
    let cancelled=false;
    let marketTimer:number|undefined;
    const params=new URLSearchParams(window.location.search);
    const fromUrl=params.get('audit')||'';
    const fromMarket=params.get('market')||localStorage.getItem('boris_market_latest_id')||'';
    const id=fromUrl||localStorage.getItem('boris_audit_latest_id')||'';
    setAuditId(id);
    setMarketAuditId(fromMarket);
    if(id){
      try{
        const saved=JSON.parse(localStorage.getItem('boris_audit_share_'+id)||'{}');
        if(saved.url)setShare(String(saved.url));
        if(saved.token)setShareToken(String(saved.token));
        if(saved.revoke_secret)setRevokeSecret(String(saved.revoke_secret));
      }catch{}
    }
    if(!id){ setError('Аудит не найден'); return; }

    // Base audit is the first useful result. Do not block it on market/contact extras.
    fetch('/api/viral/audit/'+encodeURIComponent(id),{headers:auth(),cache:'no-store'})
      .then(async r=>{const d=await r.json();if(!r.ok)throw new Error(d?.detail||'Аудит не найден');return d;})
      .then(d=>{if(!cancelled)setData(d);})
      .catch(e=>{if(!cancelled)setError(e?.message||'Не удалось открыть аудит');});

    Promise.allSettled([
      fetch('/api/viral/sales-contact',{cache:'no-store'}).then(r=>r.json()),
      fetch('/api/viral/recommendations/'+encodeURIComponent(id),{headers:auth(),cache:'no-store'}).then(r=>r.ok?r.json():null),
    ]).then(results=>{
      if(cancelled)return;
      const c=results[0].status==='fulfilled'?results[0].value:null;
      const rec=results[1].status==='fulfilled'?results[1].value:null;
      if(c)setContact(c);
      if(rec)setRecommendations(rec?.recommendations||[]);
    });

    if(fromMarket){
      setMarketState('loading');
      const delays=[0,2000,3000,5000,10000];
      let attempt=0;
      const poll=async()=>{
        if(cancelled)return;
        try{
          const r=await fetch('/api/viral/market/'+encodeURIComponent(fromMarket),{headers:auth(),cache:'no-store'});
          const d=await r.json();
          if(cancelled)return;
          if(r.ok&&d?.status==='ok'&&d?.result){
            setMarketData(d.result);
            setMarketState('ready');
            return;
          }
          if(String(d?.stage||'').toUpperCase()==='ERROR'||d?.status==='error'){
            setMarketState('unavailable');
            return;
          }
        }catch{}
        attempt+=1;
        marketTimer=window.setTimeout(poll,delays[Math.min(attempt,delays.length-1)]);
      };
      poll();
    }

    return()=>{cancelled=true;if(marketTimer)window.clearTimeout(marketTimer);};
  },[]);

  useEffect(()=>{
    if(!data)return;
    const id=auditId||data?.audit_id||'';
    const key='boris_audit_completed_seen_'+id;
    try{
      if(sessionStorage.getItem(key))return;
      sessionStorage.setItem(key,'1');
    }catch{}
    fireAuditEvent('audit_completed_view','audit_result');
    fireAuditEvent('audit_result_view','audit_result');
    emailGoal('email_audit_completed');
  },[data,auditId]);

  useEffect(()=>{
    if(!data||!contact?.trial_enabled)return;
    const key='boris_trial_offer_seen_'+(auditId||data?.audit_id||'');
    try{
      if(sessionStorage.getItem(key))return;
      sessionStorage.setItem(key,'1');
      fireAuditEvent('trial_offer_viewed','audit_result');
    }catch{}
  },[data,contact?.trial_enabled,auditId]);

  const score=data?.score?.total||0;
  const est=data?.loss?.estimated||{};
  const confirmed=data?.loss?.confirmed||{};
  const basis=data?.loss?.calculation_basis||{};
  const crm=data?.crm||{};
  const reasons=data?.reasons||[];
  const connected=data?.score?.mode==='connected';
  const previousScore=data?.score?.previous_score;
  const comparison=data?.score?.comparison||{};
  const sources=data?.sources||[];
  const slowOutliers=Number(reasons.find((r:any)=>String(r?.kind||'')==='slow_outliers')?.value||0);
  const unansweredNow=Number(confirmed.currently_unanswered??confirmed.currently_or_never_unanswered??0);
  const answeredDialogs=Number(confirmed.answered_dialogs??0);
  const totalDialogs=Number(confirmed.dialogs_period??0);
  const marketScore=marketData?.market_score?.total??null;
  const demandLevel=marketData?.demand?.level||'Недостаточно данных';
  const directCompetitors=marketData?.competitors?.direct_sample??null;
  const competingOffers=marketData?.competitors?.competing_offers_sample??marketData?.competitors?.relevant_ads_sample??null;
  const competitorLabel=marketData?.competitors?.count_basis==='unique_sellers'?'Прямые конкуренты':(marketData?.competitors?.estimated_competitors!=null?'Оценка конкурентов':'Конкурирующие предложения');
  const competitorValue=marketData?.competitors?.count_basis==='unique_sellers'
    ? (directCompetitors??'—')
    : (marketData?.competitors?.estimated_competitors!=null?'≈ '+marketData.competitors.estimated_competitors:(competingOffers??'—'));
  const relevantListings=marketData?.competitors?.relevant_ads_sample??null;
  const recommendedAds=marketData?.recommended_ads||{};
  const marketSummary=marketData?.summary||'';
  const marketPositioning=marketData?.positioning||{};
  const marketSources:Array<Record<string,unknown>>=Array.isArray(marketData?.sources)?marketData.sources:[];
  const wordstatSource=marketSources.find((x)=>/яндекс|wordstat/i.test(String(x.source||'')));
  const avitoSource=marketSources.find((x)=>/avito|авито/i.test(String(x.source||'')));
  const avitoStatus=String(avitoSource?.status||'').toLowerCase();
  const avitoMarket=marketData?.market||{};
  const avitoPriceMedian=marketData?.prices?.median??avitoMarket?.price?.median??null;
  const avitoItems=Array.isArray(avitoMarket?.items)?avitoMarket.items:[];
  const verdict=useMemo(()=>score>=80?'Сильная база':score>=65?'Хороший уровень':score>=45?'Есть потенциал роста':'Нужны изменения',[score]);
  const handoffs=useMemo(()=>{
    if(recommendations.length)return recommendations.slice(0,4).map((r:any)=>({title:r.title,href:r.href,label:'Открыть',description:r.description}));
    return reasons.slice(0,3).map((r:any)=>{
      const kind=String(r?.kind||'');
      if(kind==='unanswered'||kind==='slow_response'||kind==='response')return {title:'Разобрать обращения и ускорить ответы',href:'/messages',label:'Открыть сообщения'};
      if(kind==='reactivation')return {title:'Проверить клиентов для возврата',href:'/reactivation',label:'Открыть реактивацию'};
      if(kind==='weak_ads')return {title:'Проверить слабые объявления',href:'/dashboard/campaign',label:'Открыть объявления'};
      if(kind==='kpi_gap')return {title:'Посмотреть выполнение KPI',href:'/dashboard/analytics',label:'Открыть аналитику'};
      return {title:'Посмотреть рекомендации BORIS',href:'/dashboard/home',label:'Открыть BORIS'};
    });
  },[reasons,recommendations]);

  async function mark(stage:string){
    try{await fetch('/api/viral/gateway/state',{method:'POST',headers:auth(),body:JSON.stringify({stage})});}catch{}
  }

  async function createShare(){
    const id=auditId||localStorage.getItem('boris_audit_latest_id')||'';
    if(!id)return;
    const r=await fetch('/api/viral/share',{method:'POST',headers:auth(),body:JSON.stringify({audit_id:id})});
    const d=await r.json();
    if(r.ok){
      const url=new URL(d.public_url,window.location.origin).toString();
      setShare(url);
      setShareToken(String(d.token||''));
      setRevokeSecret(String(d.revoke_secret||''));
      try{localStorage.setItem('boris_audit_share_'+id,JSON.stringify({url,token:d.token||'',revoke_secret:d.revoke_secret||''}));}catch{}
      try{await navigator.clipboard.writeText(url);}catch{}
    }
  }

  async function revokeShare(){
    if(!shareToken||!revokeSecret)return;
    const r=await fetch('/api/viral/share/'+encodeURIComponent(shareToken)+'/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revoke_secret:revokeSecret})});
    if(r.ok){
      try{localStorage.removeItem('boris_audit_share_'+(auditId||data?.audit_id||''));}catch{}
      setShare('');setShareToken('');setRevokeSecret('');
    }
  }

  async function askAudit(text?:string){
    const q=String(text||question||'').trim();
    const id=auditId||data?.audit_id||'';
    if(!q||!id||askBusy)return;
    setQuestion(q);setAskBusy(true);
    try{
      const r=await fetch('/api/viral/audit/'+encodeURIComponent(id)+'/ask',{method:'POST',headers:auth(),body:JSON.stringify({question:q})});
      const d=await r.json();
      if(!r.ok)throw new Error(d?.detail||'Не удалось ответить');
      setAuditAnswer(d);
    }catch(e:any){
      setAuditAnswer({answer:e?.message||'Не удалось ответить по аудиту'});
    }finally{setAskBusy(false);}
  }

  async function trialInterest(){
    setTrialClicked(true);
    await mark('TRIAL_INTEREST');
    fireAuditEvent('trial_cta_clicked','audit_result');
    fireAuditEvent('trial_cta_click','audit_result');
    fireAuditEvent('trial_interest','audit_result');
    emailGoal('email_trial_interest');
    const url=contact?.whatsapp_url||contact?.max_url||'https://wa.me/79819673787';
    if(url){
      let target=String(url);
      if(/wa\.me|whatsapp/i.test(target)){
        const message='Здравствуйте. Я прошёл аудит BORIS и хочу подключить бесплатный тест на '+String(contact?.trial_days||4)+' дня.';
        target += (target.includes('?')?'&':'?')+'text='+encodeURIComponent(message);
      }
      window.open(target,'_blank','noopener,noreferrer');
    }else{
      window.location.href='/audit/help?trial=1';
    }
  }

  function specialist(channel:'whatsapp'|'max'){
    mark('SPECIALIST_CONTACTED');
    fireAuditEvent('specialist_contact_clicked','audit_result');
    fireAuditEvent(channel==='whatsapp'?'whatsapp_clicked':'max_clicked','audit_result');
    let url=channel==='whatsapp'
      ? (contact?.whatsapp_url||'https://wa.me/79819673787')
      : (contact?.max_url||'https://max.ru/u/f9LHodD0cOLLNHY2Rea9pC_FXcSh-1LgXiDxZZTAfCr-XQM3rBeNpfMKPb0');
    if(url&&channel==='whatsapp'&&/wa\.me|whatsapp/i.test(url)){
      const message=String(contact?.message_template||'Здравствуйте. Я прошёл аудит BORIS. Оценка работы с клиентами: {score}/100. Хочу помощь с настройкой.').replace('{score}',String(score));
      url += (url.includes('?')?'&':'?')+'text='+encodeURIComponent(message);
    }
    if(url) window.open(url,'_blank','noopener,noreferrer');
    else window.location.href='/audit/help';
  }

  if(error)return <main className="audit-canonical min-h-screen grid place-items-center px-5"><div className="max-w-lg rounded-[30px] bg-white p-8 text-center"><h1 className="text-2xl font-black">{error}</h1><Link href="/audit" className="mt-6 inline-flex rounded-full bg-[#2563eb] px-6 py-3 font-black text-white">Вернуться в Audit Gateway</Link></div></main>;
  if(!data)return <main className="audit-canonical min-h-screen grid place-items-center text-[#667085]">BORIS собирает результат…</main>;

  return <main className="audit-canonical min-h-screen">
    <header className="audit-internal-header px-5 py-5"><div className="audit-wide mx-auto flex items-center justify-between">
      <Link href="/audit" className="text-xl font-black">BORIS <span className="text-[#2563eb]">AUDIT</span></Link>
      <div className="flex items-center gap-4"><Link href="/audit/referrals" className="text-sm font-bold text-[#2563eb]">Приглашения</Link><Link href="/audit/history" className="text-sm font-bold text-[#667085]">История</Link></div>
    </div></header>

    <section className="px-5 pb-20 pt-8">
      <div className="audit-wide mx-auto">
        {marketState==='loading'&&<div className="mb-6 rounded-[18px] border border-[#d8e4ff] bg-[#edf4ff] p-5"><div className="flex flex-wrap items-center justify-between gap-4"><div><div className="font-black text-[#15213b]">Основной разбор уже готов</div><div className="mt-1 text-sm text-[#53617a]">Спрос и рынок догружаются отдельно. Можно читать результат уже сейчас.</div></div><div className="rounded-full bg-white px-4 py-2 text-xs font-black text-[#2878ff]">Дополняем рынок…</div></div></div>}
        {marketState==='unavailable'&&<div className="mb-6 rounded-[18px] border border-amber-200 bg-amber-50 p-5"><div className="font-black text-amber-900">Дополнительные данные рынка сейчас недоступны</div><div className="mt-1 text-sm text-amber-800">Остальной аудит готов и остаётся доступен.</div></div>}
        {marketData&&<div className="mb-8">
          <div className="audit-reference-card p-6 sm:p-8">
            <div className="text-xs font-black uppercase tracking-[.1em] text-[#2878ff]">Главный вывод BORIS</div>
            <h1 className="mt-3 text-3xl font-black tracking-[-.035em] text-[#15213b] sm:text-4xl">{marketSummary||'Рынок проанализирован. Ниже — основные точки роста.'}</h1>
            <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-[14px] bg-[#f8faff] p-4"><div className="text-xs font-bold text-[#8490a6]">Оценка продаж</div><div className="mt-1 text-3xl font-black text-[#15213b]">{score}<span className="text-sm text-[#8490a6]">/100</span></div></div>
              <div className="rounded-[14px] bg-[#f8faff] p-4"><div className="text-xs font-bold text-[#8490a6]">Оценка рынка</div><div className="mt-1 text-3xl font-black text-[#15213b]">{marketScore??'—'}{marketScore!=null&&<span className="text-sm text-[#8490a6]">/100</span>}</div></div>
              <div className="rounded-[14px] bg-[#f8faff] p-4"><div className="text-xs font-bold text-[#8490a6]">Спрос · {marketData?.demand?.region_scope||'Россия'}</div><div className="mt-1 text-2xl font-black text-[#15213b]">{demandLevel}</div></div>
              <div className="rounded-[14px] bg-[#f8faff] p-4"><div className="text-xs font-bold text-[#8490a6]">{competitorLabel}</div><div className="mt-1 text-3xl font-black text-[#15213b]">{competitorValue}</div></div>
            </div>
          </div>

          <div className="audit-evidence-grid mt-4">
            <article className="audit-evidence-card">
              <div className="audit-evidence-top">
                <span className="audit-evidence-dot audit-evidence-dot-blue"/>
                <b>Спрос Яндекса</b>
                <em className={String(wordstatSource?.status||'').toLowerCase()==='ok'?'ok':'partial'}>
                  {String(wordstatSource?.status||'').toLowerCase()==='ok'?'Проверено':'Частично'}
                </em>
              </div>
              <strong>{marketData?.demand?.total_count!=null?money(Number(marketData.demand.total_count)):'—'}</strong>
              <p>Запросов по доступным данным спроса и смысловые кластеры, которые стоит проверять отдельно.</p>
            </article>

            <article className="audit-evidence-card">
              <div className="audit-evidence-top">
                <span className="audit-evidence-dot audit-evidence-dot-violet"/>
                <b>Публичная выдача Avito</b>
                <em className={avitoStatus==='ok'?'ok':'partial'}>
                  {avitoStatus==='ok'?'Проверено':'Временно частично'}
                </em>
              </div>
              <strong>{avitoStatus==='ok'?(competitorValue+' конкурентов / предложений'):'Без выдуманных цифр'}</strong>
              <p>{avitoStatus==='ok'
                ? 'BORIS смотрит релевантные объявления, продавцов, цены, заголовки и повторяющиеся паттерны в публичной выдаче.'
                : 'Если Avito не отдал выдачу или показал CAPTCHA, BORIS сохраняет остальные выводы и честно помечает этот источник.'}</p>
            </article>

            <article className="audit-evidence-card">
              <div className="audit-evidence-top">
                <span className="audit-evidence-dot audit-evidence-dot-green"/>
                <b>Реальные обращения</b>
                <em className={connected?'ok':'neutral'}>{connected?'Подключено':'После подключения'}</em>
              </div>
              <strong>{connected?((totalDialogs||0)+' диалогов'):'Не требуется для старта'}</strong>
              <p>{connected
                ? 'BORIS сверяет рынок с фактическими диалогами, ответами, временем реакции и доступной статистикой аккаунта.'
                : 'Первый рыночный аудит работает без Avito-пароля. Подключение нужно только для точного аудита ваших продаж.'}</p>
            </article>
          </div>

          {avitoStatus==='ok'&&<div className="audit-avito-proof mt-4">
            <div>
              <div className="text-xs font-black uppercase tracking-[.09em] text-[#755dfc]">Что BORIS увидел в Avito</div>
              <h2 className="mt-2 text-2xl font-black tracking-[-.025em] text-[#15213b]">Не просто «конкуренция высокая» — конкретные признаки выдачи</h2>
            </div>
            <div className="audit-avito-proof-grid">
              <div><span>Релевантные предложения</span><b>{relevantListings??'—'}</b></div>
              <div><span>{competitorLabel}</span><b>{competitorValue}</b></div>
              <div><span>Медианная цена</span><b>{avitoPriceMedian!=null?money(Number(avitoPriceMedian))+' ₽':'—'}</b></div>
              <div><span>Изучено карточек</span><b>{avitoItems.length||marketData?.market?.sample_direct||'—'}</b></div>
            </div>
          </div>}

          {recommendedAds&&<div className="mt-4 grid gap-4 lg:grid-cols-[1.1fr_.9fr]">
            <div className="audit-reference-card audit-score-emphasis p-6">
              <div className="text-xs font-black uppercase tracking-[.08em] text-[#2878ff]">{recommendedAds.competition_grounded===false?'ПРЕДВАРИТЕЛЬНО ПО СПРОСУ':'РЕКОМЕНДОВАННЫЙ СТАРТ'}</div>
              <div className="mt-2 text-4xl font-black tracking-[-.04em] text-[#15213b]">{recommendedAds.grounded===false?'Недостаточно данных':recommendedAds.low+'–'+recommendedAds.high+' объявлений'}</div>
              <p className="mt-3 text-sm leading-6 text-[#53617a]">{recommendedAds.grounded===false?'BORIS не будет придумывать количество объявлений без подтверждённых данных.':recommendedAds.competition_grounded===false?'Диапазон рассчитан по реальному спросу. После получения конкурентной выдачи Avito он может измениться.':'Чтобы закрыть основные поисковые сценарии клиентов, BORIS рекомендует тестировать несколько смысловых групп, а затем масштабировать лучшие гипотезы.'}</p>
              <details className="mt-4 rounded-[14px] border border-[#dfe6f2] bg-white px-4 py-3">
                <summary className="cursor-pointer text-sm font-black text-[#2878ff]">Почему столько?</summary>
                <div className="mt-3 grid gap-2 text-sm text-[#53617a]">
                  <div><b className="text-[#15213b]">{recommendedAds.logic?.semantic_clusters||recommendedAds.clusters||0}</b> групп клиентских запросов</div>
                  <div><b className="text-[#15213b]">{marketData?.competitors?.count_basis==='unique_sellers'?(directCompetitors??0):(competingOffers??0)}</b> {marketData?.competitors?.count_basis==='unique_sellers'?'продавцов':'конкурирующих объявлений'} в изученной выдаче</div>
                  <div><b className="text-[#15213b]">{relevantListings??0}</b> подходящих объявлений в изученной выдаче</div>
                  <div><b className="text-[#15213b]">{marketData?.demand?.total_count??'—'}</b> запросов по данным спроса, если источник доступен</div>
                </div>
              </details>
            </div>
            <div className="audit-reference-card p-6">
              <div className="text-xs font-black uppercase tracking-[.08em] text-[#7f56d9]">Как отстроиться</div>
              <div className="mt-4 space-y-3">
                {(marketPositioning?.ideas_to_test||[]).slice(0,3).map((x:string,i:number)=><div key={i} className="rounded-[14px] bg-[#f8faff] p-4 text-sm font-bold leading-6 text-[#53617a]">{x}</div>)}
              </div>
              <Link href={'/audit/market?market='+encodeURIComponent(marketAuditId)} className="mt-5 inline-flex text-sm font-black text-[#2878ff]">Посмотреть подробный аудит рынка →</Link>
            </div>
          </div>}
        </div>}

        <div className="audit-reference-card audit-score-emphasis grid gap-6 p-6 sm:p-8 md:grid-cols-[220px_1fr] md:items-center">
          <div className="mx-auto grid h-[176px] w-[176px] place-items-center rounded-full p-[12px]" style={{background:`conic-gradient(#2878ff 0 ${score}%, #e7edf7 ${score}% 100%)`}}>
            <div className="grid h-full w-full place-items-center rounded-full bg-white text-center shadow-[inset_0_0_0_1px_#edf1f7]">
              <div>
                <div className="text-5xl font-black text-[#15213b]">{score}</div>
                <div className="mt-1 text-sm font-bold text-[#8490a6]">из 100</div>
              </div>
            </div>
          </div>
          <div>
            <div className="inline-flex rounded-full bg-[#eaf1ff] px-3 py-1.5 text-xs font-black text-[#2878ff]">{connected?'По реальным обращениям':'Предварительная оценка'}</div>
            <p className="mt-4 text-xs font-black uppercase tracking-[.12em] text-[#8490a6]">ОЦЕНКА РАБОТЫ С КЛИЕНТАМИ</p>
            <h1 className="mt-2 text-3xl font-black sm:text-4xl">{verdict}</h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-[#53617a]">Оценка учитывает скорость ответа, обращения без ответа, переходы из просмотров в обращения, работу менеджеров, выполнение плана, повторную работу с клиентами и рекламу.</p>
            {connected&&<div className="mt-4 text-sm font-bold text-[#53617a]">Оценка рассчитана по реальным данным бизнеса</div>}
          </div>
        </div>

        {connected&&previousScore!=null&&<div className="audit-reference-card mx-auto mt-7 max-w-2xl p-5 text-center">
          <div className="text-sm font-bold text-[#53617a]">После подключения реальных данных</div>
          <div className="mt-2 text-2xl font-black text-[#15213b]">Предварительно {previousScore}/100 → По реальным данным {score}/100</div>
          <div className="mt-2 text-sm text-[#53617a]">Оценка пересчитана по фактическим обращениям и доступной статистике.</div>
        </div>}

        {connected&&(comparison.declared_avg_response_minutes!=null||comparison.declared_unanswered_leads!=null)&&<div className="mt-6 grid gap-4 md:grid-cols-2">
          {comparison.declared_avg_response_minutes!=null&&<div className="audit-reference-card p-5">
            <div className="text-xs font-black uppercase tracking-[.08em] text-[#8490a6]">Скорость ответа</div>
            <div className="mt-3 grid grid-cols-[1fr_auto_1fr] items-center gap-3">
              <div><div className="text-xs font-bold text-[#8490a6]">Вы указали</div><div className="mt-1 text-2xl font-black text-[#15213b]">{comparison.declared_avg_response_minutes} мин</div></div>
              <div className="text-xl font-black text-[#2878ff]">→</div>
              <div><div className="text-xs font-bold text-[#8490a6]">BORIS измерил</div><div className="mt-1 text-2xl font-black text-[#15213b]">{comparison.measured_avg_response_minutes} мин</div></div>
            </div>
          </div>}
          {comparison.declared_unanswered_leads!=null&&<div className="audit-reference-card p-5">
            <div className="text-xs font-black uppercase tracking-[.08em] text-[#8490a6]">Клиенты без ответа</div>
            <div className="mt-3 grid grid-cols-[1fr_auto_1fr] items-center gap-3">
              <div><div className="text-xs font-bold text-[#8490a6]">Вы указали</div><div className="mt-1 text-2xl font-black text-[#15213b]">{comparison.declared_unanswered_leads}</div></div>
              <div className="text-xl font-black text-[#2878ff]">→</div>
              <div><div className="text-xs font-bold text-[#8490a6]">BORIS измерил</div><div className="mt-1 text-2xl font-black text-[#15213b]">{comparison.measured_unanswered_leads}</div></div>
            </div>
          </div>}
        </div>}

        <div className="audit-reference-card audit-score-emphasis mt-10 p-7 text-center sm:p-10">
          <div className="text-sm font-bold text-[#53617a]">{est.money_available===false?'Денежный анализ':'Расчётный диапазон потенциальных потерь'}</div>
          {est.money_available===false
            ? <div className="mx-auto mt-4 max-w-3xl"><div className="text-3xl font-black tracking-[-.035em] text-[#15213b] sm:text-4xl">Денежный диапазон пока не рассчитан</div><p className="mt-3 text-sm leading-6 text-[#53617a]">{data?.loss?.disclaimer||'Нет подтверждённого среднего чека и конверсии в продажу.'}</p></div>
            : <><div className="mt-3 text-4xl font-black tracking-[-.045em] text-[#15213b] sm:text-6xl">{money(est.lost_revenue_low||0)}–{money(est.lost_revenue_high||0)} ₽</div><div className="mt-3 text-sm text-[#53617a]">в месяц · это оценка, а не подтверждённая потеря выручки</div><details className="mx-auto mt-5 max-w-xl rounded-[14px] border border-[#dfe6f2] bg-white px-4 py-3 text-left"><summary className="cursor-pointer text-sm font-black text-[#2878ff]">Откуда эта сумма?</summary><div className="mt-3 text-sm leading-6 text-[#53617a]"><div><b className="text-[#15213b]">{basis.problem_leads||0}</b> проблемных лидов</div><div>× <b className="text-[#15213b]">{basis.sales_conversion_percent||0}%</b> конверсия в продажу</div><div>× <b className="text-[#15213b]">{money(basis.average_check_rub||0)} ₽</b> средний чек</div><div className="mt-2">BORIS показывает диапазон ±30% вокруг базовой оценки, а не выдаёт его за подтверждённую выручку.</div></div></details></>}
        </div>

        {connected&&crm?.status&&<div className="audit-reference-card mt-6 p-6">
          <div><div className="text-xs font-black uppercase tracking-[.08em] text-[#7f56d9]">ДАННЫЕ О ПРОДАЖАХ</div><h2 className="mt-2 text-2xl font-black text-[#15213b]">{crm.money_ready?'Можно оценить денежный результат':'Пока недостаточно данных для денежной оценки'}</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-[#53617a]">{crm.money_ready?'Есть подтверждённые завершённые продажи и средний чек.':'Сделки есть, но пока недостаточно подтверждённых завершённых продаж и среднего чека.'}</p></div>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-[14px] bg-[#f8faff] p-4"><div className="text-2xl font-black">{crm.deals??0}</div><div className="text-xs font-bold text-[#53617a]">сделок за период</div></div>
            <div className="rounded-[14px] bg-[#f8faff] p-4"><div className="text-2xl font-black">{crm.won??0}</div><div className="text-xs font-bold text-[#53617a]">завершённых продаж</div></div>
            <div className="rounded-[14px] bg-[#f8faff] p-4"><div className="text-2xl font-black">{crm.avg_check_rub?money(crm.avg_check_rub)+' ₽':'—'}</div><div className="text-xs font-bold text-[#53617a]">средний чек</div></div>
            <div className="rounded-[14px] bg-[#f8faff] p-4"><div className="text-2xl font-black">{crm.deal_to_won_percent!=null?crm.deal_to_won_percent+'%':'—'}</div><div className="text-xs font-bold text-[#53617a]">доля завершённых продаж</div></div>
          </div>
        </div>}

        {connected&&<div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="audit-reference-card p-6"><div className="text-3xl font-black">{confirmed.currently_unanswered??0}</div><div className="mt-2 text-sm font-bold text-[#53617a]">Диалогов без ответа сейчас</div><div className="mt-3 text-xs text-[#8490a6]">Источник: переписки BORIS</div><Link href={'/audit/details?audit='+encodeURIComponent(data.audit_id||'')} className="mt-3 inline-block text-xs font-black text-[#2878ff]">Откуда это?</Link></div>
          <div className="audit-reference-card p-6"><div className="text-3xl font-black">{confirmed.median_response_minutes??0} мин</div><div className="mt-2 text-sm font-bold text-[#53617a]">Обычное время ответа</div><div className="mt-3 text-xs text-[#8490a6]">для большинства быстрых диалогов</div><Link href={'/audit/details?audit='+encodeURIComponent(data.audit_id||'')} className="mt-3 inline-block text-xs font-black text-[#2878ff]">Откуда это?</Link></div>
          <div className="audit-reference-card p-6"><div className="text-3xl font-black">{confirmed.view_to_contact_conversion_pct==null?'—':confirmed.view_to_contact_conversion_pct+'%'}</div><div className="mt-2 text-sm font-bold text-[#53617a]">Из просмотров в обращения</div><div className="mt-3 text-xs text-[#8490a6]">{confirmed.avito_views_latest??0} просмотров · {confirmed.avito_contacts_latest??0} обращений</div><Link href={'/audit/details?audit='+encodeURIComponent(data.audit_id||'')} className="mt-3 inline-block text-xs font-black text-[#2878ff]">Откуда это?</Link></div>
          <div className="audit-reference-card p-6"><div className="text-3xl font-black">{confirmed.p90_response_minutes??0} мин</div><div className="mt-2 text-sm font-bold text-[#53617a]">90% клиентов получают ответ быстрее</div><div className="mt-3 text-xs text-[#8490a6]">Максимум: {confirmed.max_response_minutes??0} мин</div><Link href={'/audit/details?audit='+encodeURIComponent(data.audit_id||'')} className="mt-3 inline-block text-xs font-black text-[#2878ff]">Откуда это?</Link></div>
        </div>}


        <section className="mt-8">
          <div className="mb-5"><div className="text-xs font-black uppercase tracking-[.1em] text-[#2878ff]">Три главные точки роста</div><h2 className="mt-2 text-3xl font-black tracking-[-.035em] text-[#15213b]">BORIS уже нашёл, с чего начать</h2></div>
          <div className="grid gap-4 lg:grid-cols-3">
            {handoffs.slice(0,3).map((h:any,i:number)=><Link key={h.title+i} href={h.href} onClick={()=>mark('MAIN_BORIS_ENTERED')} className={"rounded-[20px] border p-6 shadow-sm "+(i===0?'border-[#ffe0ba] bg-[#fff8ed]':'border-[#dfe6f2] bg-white')}>
              <div className={"text-sm font-black "+(i===0?'text-[#b35c00]':'text-[#2878ff]')}>0{i+1}</div>
              <div className="mt-3 text-xl font-black text-[#15213b]">{h.title}</div>
              {h.description&&<p className="mt-2 text-sm leading-6 text-[#53617a]">{h.description}</p>}
              <div className={"mt-5 text-sm font-black "+(i===0?'text-[#b35c00]':'text-[#2878ff]')}>{h.label||'Что делать'} →</div>
            </Link>)}
          </div>
        </section>

        {connected&&<section className="mt-8">
          <div className="mb-5"><div className="text-xs font-black uppercase tracking-[.1em] text-[#7f56d9]">Как проходит клиент</div><h2 className="mt-2 text-3xl font-black tracking-[-.035em] text-[#15213b]">Где именно возникает потеря</h2></div>
          <div className="grid gap-5 lg:grid-cols-2">
            <div className="audit-reference-card p-6 sm:p-7">
              <div className="text-sm font-black text-[#53617a]">Реклама</div>
              <div className="mt-5 grid grid-cols-[1fr_auto_1fr] items-center gap-3">
                <div className="rounded-[16px] bg-[#f8faff] p-5 text-center"><div className="text-3xl font-black text-[#15213b]">{confirmed.avito_views_latest??'—'}</div><div className="mt-1 text-xs font-bold text-[#53617a]">просмотров</div></div>
                <div className="text-2xl font-black text-[#c7cfdd]">→</div>
                <div className="rounded-[16px] bg-[#f4f6fa] p-5 text-center"><div className="text-3xl font-black text-[#15213b]">{confirmed.avito_contacts_latest??'—'}</div><div className="mt-1 text-xs font-bold text-[#53617a]">обращений</div></div>
              </div>
              <p className="mt-4 text-sm leading-6 text-[#8490a6]">{Number(confirmed.avito_views_latest||0)<100?'Данных пока мало, поэтому BORIS не делает жёсткий вывод по рекламе.':'Выборка уже достаточна для оценки перехода из просмотров в обращения.'}</p>
            </div>
            <div className="audit-reference-card p-6 sm:p-7">
              <div className="text-sm font-black text-[#53617a]">Обработка обращений</div>
              <div className="mt-5 grid grid-cols-[1fr_auto_1fr_auto_1fr] items-center gap-2">
                <div className="rounded-[14px] bg-[#f8faff] p-4 text-center"><div className="text-2xl font-black text-[#15213b]">{totalDialogs||'—'}</div><div className="mt-1 text-[11px] font-bold text-[#53617a]">диалогов</div></div>
                <div className="font-black text-[#c7cfdd]">→</div>
                <div className="rounded-[14px] bg-emerald-50 p-4 text-center"><div className="text-2xl font-black text-emerald-700">{answeredDialogs||'—'}</div><div className="mt-1 text-[11px] font-bold text-emerald-700">получили ответ</div></div>
                <div className="font-black text-[#c7cfdd]">→</div>
                <div className="rounded-[14px] bg-[#fff8ed] p-4 text-center"><div className="text-2xl font-black text-[#b35c00]">{unansweredNow||0}</div><div className="mt-1 text-[11px] font-bold text-[#8f5b2d]">требуют внимания</div></div>
              </div>
              <p className="mt-4 text-sm leading-6 text-[#53617a]">{unansweredNow>0?'Главная потеря сейчас происходит уже после появления обращения — часть клиентов остаётся без внимания.':'Сейчас критичных обращений без ответа не обнаружено.'}</p>
            </div>
          </div>
        </section>}

        {connected&&<section className="mt-8">
          <div className="rounded-[24px] border border-[#f2dcc4] bg-gradient-to-br from-[#fffaf3] to-white p-7 sm:p-8">
            <div className="text-xs font-black uppercase tracking-[.1em] text-[#b35c00]">Если ничего не менять</div>
            <h2 className="mt-3 text-3xl font-black tracking-[-.035em] text-[#15213b]">Проблема может повторяться</h2>
            <p className="mt-3 max-w-3xl text-sm leading-7 text-[#53617a]">Здесь только факты из текущего аудита — без выдуманного прогноза по деньгам.</p>
            <div className="mt-6 grid gap-4 md:grid-cols-3">
              <div className="rounded-[18px] border border-[#f3dfca] bg-white p-5"><div className="text-xs font-black uppercase tracking-[.08em] text-[#a7661f]">Сейчас</div><div className="mt-3 text-3xl font-black text-[#15213b]">{unansweredNow}</div><div className="mt-1 text-sm font-bold text-[#53617a]">обращений требуют внимания</div></div>
              <div className="rounded-[18px] border border-[#f3dfca] bg-white p-5"><div className="text-xs font-black uppercase tracking-[.08em] text-[#a7661f]">Редкие провалы</div><div className="mt-3 text-3xl font-black text-[#15213b]">{slowOutliers}</div><div className="mt-1 text-sm font-bold text-[#53617a]">ответов заняли больше 2 часов</div></div>
              <div className="rounded-[18px] border border-[#e7ebf2] bg-white p-5"><div className="text-xs font-black uppercase tracking-[.08em] text-[#8490a6]">Реклама</div><div className="mt-3 text-xl font-black text-[#15213b]">{Number(confirmed.avito_views_latest||0)<100?'Пока мало данных':'Данных достаточно'}</div><div className="mt-1 text-sm font-bold text-[#53617a]">для уверенной оценки объявлений</div></div>
            </div>
          </div>
        </section>}

        <section className="mt-8">
          <div className="mb-5"><div className="text-xs font-black uppercase tracking-[.1em] text-[#2878ff]">Ближайшие 24 часа</div><h2 className="mt-2 text-3xl font-black tracking-[-.035em] text-[#15213b]">Понятный план без лишней теории</h2></div>
          <div className="audit-reference-card p-6 sm:p-8">
            <div className="grid gap-0 lg:grid-cols-4">
              {[
                ['01',unansweredNow>0?'Разобрать обращения без ответа':'Проверить новые обращения',unansweredNow>0?'Начать с клиентов, которые уже требуют внимания.':'Убедиться, что новые обращения распределены между менеджерами.'],
                ['02',slowOutliers>0?'Разобрать долгие ответы':'Проверить скорость команды',slowOutliers>0?'Понять, почему отдельные клиенты ждали слишком долго.':'Сохранить текущую хорошую скорость ответа.'],
                ['03','Включить постоянный контроль','Не оставлять новые обращения без внимания.'],
                ['04','Вернуться к рекламе','Повторить оценку после накопления достаточной выборки.'],
              ].map(([n,t,d],i)=><div key={n} className={"relative p-5 "+(i<3?'lg:border-r lg:border-[#e7ebf2]':'')}>
                <div className="grid h-10 w-10 place-items-center rounded-full bg-[#edf4ff] text-sm font-black text-[#2878ff]">{n}</div>
                <div className="mt-4 font-black text-[#15213b]">{t}</div>
                <div className="mt-2 text-sm leading-6 text-[#53617a]">{d}</div>
              </div>)}
            </div>
          </div>
        </section>

        <section className="mt-8">
          <div className="mb-5"><div className="text-xs font-black uppercase tracking-[.1em] text-[#7f56d9]">Что BORIS может взять на себя</div><h2 className="mt-2 text-3xl font-black tracking-[-.035em] text-[#15213b]">Не просто показать проблему — помочь её контролировать</h2></div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ['Следить за обращениями','Показывать клиентов, которые остались без ответа.','💬'],
              ['Напоминать менеджеру','Выделять обращения, где ответ слишком долго не приходит.','⏱'],
              ['Возвращать старых клиентов','Находить диалоги, к которым стоит вернуться.','↩'],
              ['Проверять рекламу','Сравнивать просмотры, обращения и работу объявлений.','◎'],
            ].map(([t,d,icon])=><div key={t} className="audit-reference-card p-6"><div className="grid h-11 w-11 place-items-center rounded-[14px] bg-[#edf4ff] text-xl">{icon}</div><div className="mt-4 text-lg font-black text-[#15213b]">{t}</div><p className="mt-2 text-sm leading-6 text-[#53617a]">{d}</p></div>)}
          </div>
        </section>

        <section className="mt-8">
          <div className="audit-reference-card overflow-hidden p-0">
            <div className="grid lg:grid-cols-2">
              <div className="border-b border-[#e7ebf2] bg-[#fbfcfe] p-7 sm:p-8 lg:border-b-0 lg:border-r">
                <div className="text-xs font-black uppercase tracking-[.1em] text-[#8490a6]">Сейчас</div>
                <h2 className="mt-3 text-3xl font-black tracking-[-.035em] text-[#15213b]">Контроль зависит от внимания команды</h2>
                <div className="mt-6 grid gap-3">{['Обращение может остаться без ответа','Долгий ответ легко потерять в общей статистике','Рекламу приходится периодически проверять вручную'].map(x=><div key={x} className="flex items-start gap-3 rounded-[14px] bg-white p-4 text-sm font-bold leading-6 text-[#53617a]"><span className="mt-0.5 text-[#f59e0b]">•</span>{x}</div>)}</div>
              </div>
              <div className="bg-gradient-to-br from-[#f2f7ff] to-white p-7 sm:p-8">
                <div className="text-xs font-black uppercase tracking-[.1em] text-[#2878ff]">С BORIS</div>
                <h2 className="mt-3 text-3xl font-black tracking-[-.035em] text-[#15213b]">Проблемы видны сразу</h2>
                <div className="mt-6 grid gap-3">{['Обращения без ответа собраны в одном месте','Долгие ответы становятся заметны сразу','Реклама пересчитывается по новым данным'].map(x=><div key={x} className="flex items-start gap-3 rounded-[14px] border border-[#dbe7fb] bg-white p-4 text-sm font-bold leading-6 text-[#35577f]"><span className="mt-0.5 text-[#19b984]">✓</span>{x}</div>)}</div>
              </div>
            </div>
          </div>
        </section>

        <section className="mt-8">
          <div className="rounded-[22px] border border-[#e2e8f0] bg-white p-6 sm:p-7">
            <div className="flex flex-wrap items-start justify-between gap-5">
              <div><div className="text-xs font-black uppercase tracking-[.1em] text-[#8490a6]">На чём основан аудит</div><h2 className="mt-2 text-2xl font-black text-[#15213b]">Только на доступных данных бизнеса</h2></div>
              <div className="flex flex-wrap gap-2">
                {[
                  connected?'Реальные обращения':null,
                  connected?'Данные Avito':null,
                  crm?.status?'Данные продаж':null,
                  marketData?'Данные рынка':null,
                ].filter(Boolean).map((x:any)=><span key={x} className="rounded-[10px] border border-[#dfe6f2] bg-[#f8faff] px-3 py-2 text-sm font-bold text-[#53617a]">{x}</span>)}
              </div>
            </div>
          </div>
        </section>

        <div className="audit-reference-card mt-8 p-6 sm:p-7">
          <div className="text-sm font-black text-[#7f56d9]">СПРОСИТЬ BORIS ПРО МОЙ АУДИТ</div>
          <h2 className="mt-2 text-2xl font-black">Разберём результат без выдуманных цифр</h2>
          <p className="mt-2 text-sm leading-6 text-[#667085]">BORIS отвечает только по данным вашего аудита и не придумывает новые цифры.</p>
          <div className="mt-4 flex flex-wrap gap-2">
            {['Почему такая оценка?','Откуда взялась сумма?','Что исправить первым?','Какие клиенты требуют внимания?','Что даст полный BORIS?'].map(q=><button key={q} onClick={()=>askAudit(q)} disabled={askBusy} className="rounded-full border border-[#dce3ee] bg-[#f8fafc] px-4 py-2 text-sm font-bold disabled:opacity-50">{q}</button>)}
          </div>
          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <input value={question} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();askAudit();}}} placeholder="Например: почему оценка изменилась после подключения Avito?" className="h-13 flex-1 rounded-2xl border border-[#dce3ee] px-4 outline-none focus:border-[#2563eb]"/>
            <button onClick={()=>askAudit()} disabled={askBusy||!question.trim()} className="rounded-full bg-[#101828] px-6 py-3 font-black text-white disabled:opacity-50">{askBusy?'Разбираю…':'Спросить'}</button>
          </div>
          {auditAnswer?.answer&&<div className="mt-5 rounded-[24px] bg-[#f7f9fc] p-5">
            <div className="text-sm font-black text-[#2563eb]">BORIS</div>
            <div className="mt-2 whitespace-pre-line text-sm leading-7 text-[#344054]">{auditAnswer.answer}</div>
            {auditAnswer?.facts?.length>0&&<div className="mt-4 grid gap-2 sm:grid-cols-2">{auditAnswer.facts.map((f:any)=><div key={f.label} className="rounded-2xl bg-white p-3"><div className="text-lg font-black">{f.value}</div><div className="text-xs font-bold text-[#667085]">{f.label}</div></div>)}</div>}
            {auditAnswer?.action?.href&&<Link href={auditAnswer.action.href} onClick={()=>mark('MAIN_BORIS_ENTERED')} className="mt-4 inline-flex rounded-full bg-[#2563eb] px-5 py-3 text-sm font-black text-white">{auditAnswer.action.title||'Открыть'} →</Link>}
          </div>}
        </div>

        {contact?.trial_enabled&&<div className="audit-reference-card audit-score-emphasis mt-8 p-6 sm:p-8">
          <div className="grid gap-6 lg:grid-cols-[1.2fr_.8fr] lg:items-center">
            <div>
              <div className="text-xs font-black uppercase tracking-[.1em] text-[#2878ff]">Следующий шаг</div>
              <h2 className="mt-3 text-3xl font-black tracking-[-.035em] text-[#15213b] sm:text-4xl">{contact?.trial_title||('Попробовать BORIS '+(contact?.trial_days||4)+' дня бесплатно')}</h2>
              <p className="mt-3 max-w-3xl text-base leading-7 text-[#53617a]">{contact?.trial_description||'Без долгого внедрения. Начнём с найденных в аудите точек.'}</p>
              <div className="mt-5 grid gap-2 sm:grid-cols-2">
                {['Помощник по ответам клиентам','Контроль обращений','Возврат старых клиентов','Проверка рекламы'].map(x=><div key={x} className="rounded-[12px] bg-white px-4 py-3 text-sm font-bold text-[#53617a]">✓ {x}</div>)}
              </div>
              <div className="mt-6 flex flex-wrap gap-3">
                <button onClick={trialInterest} disabled={trialClicked} className="rounded-[10px] bg-[#2878ff] px-6 py-4 font-black text-white hover:bg-[#1767ed] disabled:cursor-default disabled:bg-[#19b984]">{trialClicked?'Интерес к тесту зафиксирован ✓':'Попробовать BORIS '+(contact?.trial_days||4)+' дня бесплатно'}</button>
                <Link href="/dashboard/home" onClick={()=>mark('MAIN_BORIS_ENTERED')} className="rounded-[10px] border border-[#dfe6f2] bg-white px-5 py-4 text-sm font-black text-[#53617a]">Открыть полный BORIS</Link>
              </div>
              {trialClicked&&<div className="mt-4 rounded-[14px] border border-emerald-200 bg-emerald-50 p-4 text-sm font-bold leading-6 text-emerald-800">Интерес к тесту отмечен. Для подключения напишите Кириллу в WhatsApp или MAX — тест не запускается автоматически без настройки.</div>}
            </div>
            <div className="rounded-[18px] border border-[#dfe6f2] bg-white p-6">
              <div className="text-xs font-black uppercase tracking-[.08em] text-[#7f56d9]">Есть вопросы по тесту?</div>
              <div className="mt-4 flex items-center gap-4">
                <img src="/boris-preview/kirill.webp" alt="Кирилл — создатель BORIS AI" className="h-16 w-16 shrink-0 rounded-full object-cover object-top shadow-sm" />
                <div><div className="text-2xl font-black text-[#15213b]">{contact?.contact_name||'Кирилл'}</div><div className="mt-1 text-sm font-bold text-[#53617a]">{contact?.contact_title||'Помогу запустить BORIS для вашего бизнеса'}</div></div>
              </div>
              <p className="mt-4 text-sm leading-6 text-[#53617a]">Помогу подключить тест, Avito и AI-менеджера и разобраться с первым запуском.</p>
              <div className="mt-5 flex flex-wrap gap-2">
                <button onClick={()=>specialist('whatsapp')} className="rounded-[10px] bg-[#2878ff] px-4 py-3 text-sm font-black text-white">WhatsApp</button>
                <button onClick={()=>specialist('max')} className="rounded-[10px] border border-[#dfe6f2] bg-white px-4 py-3 text-sm font-black text-[#15213b]">MAX</button>
              </div>
            </div>
          </div>
        </div>}

        <div className="mt-5 text-center">
          <Link href={connected ? '/audit/analyzing?account_id='+encodeURIComponent(data.account_id||'') : '/audit/connect'} onClick={()=>mark('SOURCE_CONNECTION_STARTED')} className="text-sm font-black text-[#2878ff] underline underline-offset-4">{connected?'Обновить данные аудита':'Подключить Avito для более точного аудита'}</Link>
        </div>

        <div className="mt-6 flex flex-col gap-3 rounded-[28px] border border-[#e1e7ef] bg-white p-6 sm:flex-row sm:items-center sm:justify-between">
          <div><div className="font-black">Показать результат партнёру</div><div className="mt-1 text-sm text-[#667085]">Публичная версия скрывает ваши внутренние данные.</div></div>
          <div className="flex flex-wrap gap-2">
            <button onClick={createShare} className="rounded-full border border-[#d8deea] px-5 py-3 font-black">{share?'Скопировать / обновить':'Создать безопасную ссылку'}</button>
            {share&&shareToken&&revokeSecret&&<button onClick={revokeShare} className="rounded-full border border-red-200 bg-red-50 px-5 py-3 font-black text-red-700">Отключить ссылку</button>}
          </div>
        </div>
        {share&&<div className="mt-3 break-all rounded-2xl bg-[#eef4ff] p-4 text-center text-sm font-bold text-[#2563eb]">{share}</div>}
        <div className="mt-6 text-center"><Link href={'/audit/details?audit='+encodeURIComponent(data.audit_id||'')} className="text-sm font-black text-[#2563eb] underline underline-offset-4">Показать детали аудита и источники</Link></div>
      </div>
    </section>
  </main>;
}
