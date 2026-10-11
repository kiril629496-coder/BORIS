'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { emailGoal, fireAuditEvent } from '../lib/attribution';

function auth():Record<string,string>{
  const t=typeof window!=='undefined'?localStorage.getItem('boris_token')||'':'';
  return t?{Authorization:'Bearer '+t}:{};
}
const money=(n:any)=>n==null?'—':new Intl.NumberFormat('ru-RU').format(Number(n)||0)+' ₽';
export default function AuditMarketPage(){
  const [data,setData]=useState<any>(null);
  const [error,setError]=useState('');
  const [marketId,setMarketId]=useState('');
  const [coreId,setCoreId]=useState('');
  const [contact,setContact]=useState<any>(null);
  const [trialClicked,setTrialClicked]=useState(false);

  useEffect(()=>{
    const id=new URLSearchParams(window.location.search).get('market')||localStorage.getItem('boris_market_latest_id')||'';
    const core=localStorage.getItem('boris_audit_latest_id')||'';
    setMarketId(id); setCoreId(core);
    if(!localStorage.getItem('boris_token')){localStorage.setItem('boris_post_login_return',window.location.pathname+window.location.search);window.location.replace('/login');return;}
    if(!id){setError('Рыночный аудит не найден');return;}
    Promise.all([
      fetch('/api/viral/market/'+encodeURIComponent(id),{headers:auth(),cache:'no-store'}).then(async r=>{const d=await r.json();if(!r.ok||d?.status!=='ok')throw new Error(d?.message||d?.detail||'Результат ещё не готов');return d.result;}),
      fetch('/api/viral/sales-contact',{cache:'no-store'}).then(r=>r.ok?r.json():null).catch(()=>null),
    ]).then(([market,contactData])=>{
      setData(market);setContact(contactData);
      const eventKey='boris_market_completed_seen_'+id;
      try{
        if(!sessionStorage.getItem(eventKey)){
          sessionStorage.setItem(eventKey,'1');
          fireAuditEvent('market_audit_completed','audit_market');
        }
      }catch{ fireAuditEvent('market_audit_completed','audit_market'); }
    }).catch(e=>setError(e?.message||'Не удалось открыть рынок'));
  },[]);

  if(error)return <main className="audit-canonical min-h-screen grid place-items-center px-5"><div className="audit-reference-card max-w-lg p-8 text-center"><h1 className="text-2xl font-black">{error}</h1><Link href="/audit" className="mt-5 inline-flex rounded-[10px] bg-[#2878ff] px-5 py-3 font-black text-white">Мой аудит</Link></div></main>;
  if(!data)return <main className="audit-canonical min-h-screen grid place-items-center text-[#53617a]">Загрузка рынка…</main>;

  const score=data.market_score||{};
  const demand=data.demand||{};
  const comp=data.competitors||{};
  const competitorLabel=comp.count_basis==='unique_sellers'?'Прямые конкуренты':(comp.estimated_competitors!=null?'Оценка конкурентов':'Конкурирующие предложения');
  const competitorValue=comp.count_basis==='unique_sellers'
    ? (comp.direct_sample??'—')
    : (comp.estimated_competitors!=null?'≈ '+comp.estimated_competitors:(comp.competing_offers_sample??comp.relevant_ads_sample??'—'));
  const prices=data.prices||{};
  const ads=data.recommended_ads||{};
  const pos=data.positioning||{};

  async function mark(stage:string){
    try{await fetch('/api/viral/gateway/state',{method:'POST',headers:{...auth(),'Content-Type':'application/json'},body:JSON.stringify({stage})});}catch{}
  }
  async function trialInterest(){
    setTrialClicked(true);
    await mark('TRIAL_INTEREST');
    fireAuditEvent('trial_cta_clicked','audit_market');
    fireAuditEvent('trial_cta_click','audit_market');
    fireAuditEvent('trial_interest','audit_market');
    emailGoal('email_trial_interest');
  }
  function openCampaign(){
    const topQueries=(demand.top_requests||[]).slice(0,6).map((x:any)=>String(x.phrase||'').trim()).filter(Boolean);
    const ideas=(pos.ideas_to_test||[]).slice(0,4).map((x:any)=>String(x||'').trim()).filter(Boolean);
    const range=ads.grounded===false?'определи разумное количество после проверки данных':`${ads.low}–${ads.high} объявлений`;
    const prompt=[
      `Подготовь рекламную кампанию по направлению «${data.query}» в регионе «${data.region}».`,
      `Цель: ${range}. Раздели объявления по разным смысловым сценариям спроса, а не делай копии одного текста.`,
      topQueries.length?`Поисковые формулировки для кластеризации: ${topQueries.join('; ')}.`:'',
      ideas.length?`Гипотезы для отстройки, которые нужно проверить, а не выдавать за факты клиента: ${ideas.join('; ')}.`:'',
      data.category_slug?`Публичная выдача указывает категорию Avito: ${String(data.category_slug).replaceAll('_',' ')}. Перед публикацией обязательно проверь категорию штатным intake BORIS.`:'',
      'Не придумывай цены, гарантии, сроки, характеристики и преимущества, которых нет в данных выбранного клиента.',
      'Сначала создай и проверь кампанию; ничего не публикуй автоматически без штатного подтверждения.'
    ].filter(Boolean).join('\n\n');
    try{
      localStorage.setItem('boris_campaign_audit_prefill',JSON.stringify({
        query:data.query||'',region:data.region||'',category_slug:data.category_slug||'',
        recommended_ads:ads,top_queries:topQueries,positioning_ideas:ideas,prompt,created_at:new Date().toISOString()
      }));
    }catch{}
    window.location.href='/dashboard/campaign?from=audit';
  }

  function specialist(channel:'whatsapp'|'max'){
    mark('SPECIALIST_CONTACTED');
    fireAuditEvent(channel==='whatsapp'?'whatsapp_clicked':'max_clicked','audit_market');
    let url=channel==='whatsapp'?contact?.whatsapp_url:contact?.max_url;
    if(url&&channel==='whatsapp'&&/wa\.me|whatsapp/i.test(url)){
      const message=String(contact?.message_template||'Здравствуйте. Я прошёл рыночный аудит BORIS и хочу помощь с тестом.').replace('{score}',String(score.total||0));
      url += (url.includes('?')?'&':'?')+'text='+encodeURIComponent(message);
    }
    if(url)window.open(url,'_blank','noopener,noreferrer');
    else window.location.href='/audit/help?trial=1';
  }

  return <main className="audit-canonical min-h-screen">
    <header className="audit-internal-header px-5 py-5"><div className="audit-wide mx-auto flex items-center justify-between">
      <Link href="/audit" className="text-xl font-black">BORIS <span className="text-[#2878ff]">AUDIT</span></Link>
      {coreId?<Link href={'/audit/result?audit='+encodeURIComponent(coreId)+'&market='+encodeURIComponent(marketId)} className="text-sm font-black text-[#53617a]">← К общему результату</Link>:<Link href="/audit/onboarding" className="text-sm font-black text-[#53617a]">Добавить данные бизнеса →</Link>}
    </div></header>
    <section className="audit-internal-section px-5 pb-20 pt-8"><div className="audit-wide mx-auto">
      <div className="audit-reference-card audit-score-emphasis grid gap-6 p-6 sm:p-8 md:grid-cols-[200px_1fr] md:items-center">
        <div className="mx-auto grid h-[164px] w-[164px] place-items-center rounded-full p-[11px]" style={{background:`conic-gradient(#8c5cf5 0 ${score.total||0}%, #e7edf7 ${score.total||0}% 100%)`}}><div className="grid h-full w-full place-items-center rounded-full bg-white text-center"><div><div className="text-5xl font-black text-[#15213b]">{score.total||0}</div><div className="text-sm font-bold text-[#8490a6]">из 100</div></div></div></div>
        <div><div className="text-xs font-black uppercase tracking-[.1em] text-[#8c5cf5]">ОЦЕНКА РЫНКА BORIS</div><h1 className="mt-2 text-3xl font-black text-[#15213b] sm:text-4xl">{data.summary}</h1><div className="mt-3 flex flex-wrap gap-2 text-sm font-bold text-[#53617a]">{data.category_slug&&<span className="rounded-[8px] bg-[#f4f6fa] px-2 py-1">Категория Avito: {String(data.category_slug).replaceAll('_',' ')}</span>}</div></div>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="audit-reference-card p-5"><div className="text-xs font-bold text-[#8490a6]">Поисковый спрос · {demand.region_scope||'Россия'}</div><div className="mt-2 text-2xl font-black">{demand.level||'Недостаточно данных'}</div>{demand.total_count!=null&&<div className="mt-1 text-sm text-[#53617a]">≈ {new Intl.NumberFormat('ru-RU').format(demand.total_count)} запросов</div>}<div className="mt-2 text-xs leading-5 text-[#8490a6]">Региональная конкурентная среда и цены ниже рассчитаны отдельно по {data.region||'выбранному региону'}.</div></div>
        <div className="audit-reference-card p-5"><div className="text-xs font-bold text-[#8490a6]">{competitorLabel}</div><div className="mt-2 text-2xl font-black">{competitorValue}</div><div className="mt-1 text-sm text-[#53617a]">{comp.count_basis==='unique_sellers'?'уникальных продавцов в выборке':(comp.estimated_competitors!=null?'оценка по публичной выдаче':'релевантных объявлений в выборке')}</div></div>
        <div className="audit-reference-card p-5"><div className="text-xs font-bold text-[#8490a6]">{ads.competition_grounded===false?'Предварительный старт по спросу':'Рекомендуемый старт'}</div><div className="mt-2 text-2xl font-black">{ads.grounded===false?'Недостаточно данных':(ads.low||'—')+'–'+(ads.high||'—')}</div><div className="mt-1 text-sm text-[#53617a]">{ads.grounded===false?'нужен подтверждённый спрос или выдача':(ads.competition_grounded===false?'объявлений · без учёта конкурентов Avito':'объявлений')}</div></div>
        <div className="audit-reference-card p-5"><div className="text-xs font-bold text-[#8490a6]">Медиана цены</div><div className="mt-2 text-2xl font-black">{money(prices.median)}</div><div className="mt-1 text-sm text-[#53617a]">{prices.min!=null&&prices.max!=null?money(prices.min)+' – '+money(prices.max):'данных пока мало'}</div></div>
      </div>

      <div className="mt-8 grid gap-5 lg:grid-cols-2">
        <div className="audit-reference-card p-6"><h2 className="text-2xl font-black">Что ищут клиенты</h2><div className="mt-2 text-xs font-bold text-[#8490a6]">Источник: Яндекс Вордстат · география частотности: {demand.region_scope||'Россия'}. Регион «{data.region}» учитывается отдельно в анализе Avito.</div><div className="mt-4 grid gap-2">{(demand.top_requests||[]).slice(0,8).map((x:any)=><div key={x.phrase} className="flex justify-between gap-4 rounded-[12px] bg-[#f8faff] p-3"><span className="font-bold">{x.phrase}</span><span className="font-black text-[#2878ff]">{x.count==null?'—':new Intl.NumberFormat('ru-RU').format(x.count)}</span></div>)}{!(demand.top_requests||[]).length&&<div className="text-sm text-[#53617a]">Точная частотность временно недоступна — BORIS не подставляет выдуманные цифры.</div>}</div></div>
        <div className="audit-reference-card p-6"><h2 className="text-2xl font-black">Почему столько объявлений?</h2><div className="mt-4 grid gap-3 text-sm text-[#53617a]"><div><b className="text-[#15213b]">{ads.logic?.semantic_clusters||ads.clusters||0}</b> смысловых кластеров спроса</div><div><b className="text-[#15213b]">{comp.count_basis==='unique_sellers'?(comp.direct_sample||0):(comp.competing_offers_sample||0)}</b> {comp.count_basis==='unique_sellers'?'уникальных продавцов':'конкурирующих предложений'} в текущей выборке</div><div>Учитывается уровень спроса и насыщенность выдачи.</div></div><p className="mt-5 text-sm leading-6 text-[#53617a]">BORIS рекомендует стартовый диапазон, а не бесконечное размножение объявлений. После теста масштабируются только сильные гипотезы.</p></div>
      </div>

      <div className="audit-reference-card mt-8 p-6"><h2 className="text-2xl font-black">Как отстроиться от конкурентов</h2>
        {(pos.market_common||[]).length>0&&<div className="mt-4"><div className="text-sm font-black text-[#8490a6]">Часто повторяется на рынке</div><div className="mt-2 flex flex-wrap gap-2">{pos.market_common.map((x:string)=><span key={x} className="rounded-[10px] bg-[#f4f6fa] px-3 py-2 text-sm font-bold text-[#53617a]">{x}</span>)}</div></div>}
        <div className="mt-5"><div className="text-sm font-black text-[#2878ff]">Идеи, которые стоит проверить</div><div className="mt-3 grid gap-3 md:grid-cols-3">{(pos.ideas_to_test||[]).map((x:string)=><div key={x} className="rounded-[14px] border border-[#dfe6f2] bg-[#f8faff] p-4 text-sm font-bold leading-6">{x}</div>)}</div></div>
      </div>


      <div className="mt-8 grid gap-5 lg:grid-cols-2">
        <div className="audit-reference-card p-6">
          <h2 className="text-xl font-black">На чём основан анализ</h2>
          <div className="mt-4 grid gap-3">{(data.sources||[]).map((s:any,i:number)=><div key={i} className="rounded-[12px] bg-[#f8faff] p-4"><div className="font-black">{s.source}</div><div className="mt-1 text-xs font-bold text-[#53617a]">{s.status==='ready'?'Данные получены':'Сейчас недоступно'}{s.checked_at?' · '+new Date(s.checked_at).toLocaleString('ru-RU'):''}</div></div>)}</div>
          <div className="mt-4 text-xs leading-5 text-[#8490a6]">BORIS не смешивает недоступные источники с подтверждёнными цифрами.</div>
        </div>
        <div className="audit-reference-card p-6">
          <h2 className="text-xl font-black">Примеры из выдачи</h2>
          <div className="mt-4 grid gap-3">{(data.sample_ads||[]).slice(0,4).map((x:any,i:number)=><div key={(x.url||x.title||'')+i} className="rounded-[12px] border border-[#edf1f7] bg-white p-4"><div className="font-black text-[#15213b]">{x.title||'Предложение'}</div><div className="mt-1 text-sm text-[#53617a]">{x.price?money(x.price):'Цена не указана'}{x.seller_name?' · '+x.seller_name:''}</div></div>)}{!(data.sample_ads||[]).length&&<div className="text-sm leading-6 text-[#53617a]">Публичная выборка сейчас недостаточна. BORIS не будет подставлять случайные объявления.</div>}</div>
        </div>
      </div>

      {contact?.trial_enabled&&<div className="audit-reference-card audit-score-emphasis mt-8 p-6 sm:p-8">
        <div className="grid gap-6 lg:grid-cols-[1.2fr_.8fr] lg:items-center">
          <div>
            <div className="text-xs font-black uppercase tracking-[.1em] text-[#2878ff]">Следующий шаг</div>
            <h2 className="mt-3 text-3xl font-black tracking-[-.035em] text-[#15213b]">{contact?.trial_title||('Попробуйте BORIS бесплатно '+(contact?.trial_days||4)+' дня')}</h2>
            <p className="mt-3 max-w-2xl leading-7 text-[#53617a]">{contact?.trial_description||'Начнём с найденных в рыночном аудите точек — без долгого внедрения.'}</p>
            <button onClick={trialInterest} className="mt-5 rounded-[10px] bg-[#2878ff] px-6 py-4 font-black text-white">Попробовать BORIS бесплатно {contact?.trial_days||4} дня</button>
            {trialClicked&&<div className="mt-4 rounded-[14px] border border-emerald-200 bg-emerald-50 p-4 text-sm font-bold leading-6 text-emerald-800">Интерес к тесту отмечен. Тест не включается автоматически — для подключения напишите Кириллу.</div>}
          </div>
          <div className="rounded-[18px] border border-[#dfe6f2] bg-white p-6">
            <div className="text-xs font-black uppercase tracking-[.08em] text-[#7f56d9]">Есть вопросы по тесту?</div>
            <div className="mt-3 text-2xl font-black text-[#15213b]">{contact?.contact_name||'Кирилл О.'}</div>
            <div className="mt-1 text-sm font-bold text-[#53617a]">{contact?.contact_title||'Разработчик BORIS'}</div>
            <p className="mt-4 text-sm leading-6 text-[#53617a]">Помогу подключить тест, Avito и AI-менеджера и разобраться с первым запуском.</p>
            <div className="mt-5 flex flex-wrap gap-2"><button onClick={()=>specialist('whatsapp')} className="rounded-[10px] bg-[#2878ff] px-4 py-3 text-sm font-black text-white">WhatsApp</button><button onClick={()=>specialist('max')} className="rounded-[10px] border border-[#dfe6f2] bg-white px-4 py-3 text-sm font-black text-[#15213b]">MAX</button></div>
          </div>
        </div>
      </div>}

      <div className="mt-8 flex flex-wrap gap-3"><button onClick={openCampaign} className="rounded-[10px] bg-[#2878ff] px-6 py-4 font-black text-white">Создать эти объявления с BORIS</button>{coreId?<Link href={'/audit/result?audit='+encodeURIComponent(coreId)+'&market='+encodeURIComponent(marketId)} className="rounded-[10px] border border-[#dfe6f2] bg-white px-6 py-4 font-black text-[#15213b]">Вернуться к полному аудиту</Link>:<Link href="/audit/onboarding" className="rounded-[10px] border border-[#dfe6f2] bg-white px-6 py-4 font-black text-[#15213b]">Добавить показатели бизнеса</Link>}</div>
    </div></section>
  </main>;
}
