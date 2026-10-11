
'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';

const money = (n: number) => new Intl.NumberFormat('ru-RU').format(Math.max(0, Math.round(n || 0)));

function fire(event: string, extra: Record<string, unknown> = {}) {
  fetch('/api/viral/events', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({event, source: 'public_audit', ...extra}),
  }).catch(() => {});
}

export default function PublicReportClient({token}:{token:string}) {
  const [data,setData]=useState<any>(null);
  const [error,setError]=useState('');
  const [pollStep,setPollStep]=useState(0);
  const [copied,setCopied]=useState('');
  const scrollMarks=useRef(new Set<number>());

  async function load(){
    try{
      const r=await fetch('/api/viral/public/'+encodeURIComponent(token),{cache:'no-store'});
      const d=await r.json();
      if(!r.ok)throw new Error(d?.detail||'Отчёт недоступен');
      setData(d);
      setError('');
      return d;
    }catch(e:any){
      setError(e?.message||'Отчёт недоступен');
      return null;
    }
  }

  useEffect(()=>{
    load().then((d)=>{
      if(d){
        fire('viral_share_opened',{referral_code:d?.referral?.code||undefined});
        fire('audit_opened',{referral_code:d?.referral?.code||undefined});
      }
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[token]);

  useEffect(()=>{
    const state=String(data?.data_status?.state||'');
    if(!['PARTIAL_READY','REFRESHING'].includes(state))return;
    const delays=[2000,3000,5000,10000];
    const ms=delays[Math.min(pollStep,delays.length-1)];
    const id=window.setTimeout(async()=>{
      const next=await load();
      if(next&&['PARTIAL_READY','REFRESHING'].includes(String(next?.data_status?.state||''))){
        setPollStep(x=>x+1);
      }
    },ms);
    return()=>window.clearTimeout(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[data?.data_status?.state,pollStep]);

  useEffect(()=>{
    function onScroll(){
      const doc=document.documentElement;
      const max=Math.max(1,doc.scrollHeight-window.innerHeight);
      const pct=Math.min(100,Math.round((window.scrollY/max)*100));
      for(const mark of [25,50,75,100]){
        if(pct>=mark&&!scrollMarks.current.has(mark)){
          scrollMarks.current.add(mark);
          fire(mark===100?'audit_completed_view':'audit_'+mark+'_viewed',{referral_code:data?.referral?.code||undefined});
        }
      }
    }
    window.addEventListener('scroll',onScroll,{passive:true});
    onScroll();
    return()=>window.removeEventListener('scroll',onScroll);
  },[data?.referral?.code]);

  const score=Number(data?.growth_score?.total??data?.score?.total??0);
  const demand=data?.demand||{};
  const advertising=data?.advertising||{};
  const opportunities=Array.isArray(data?.top_opportunities)?data.top_opportunities:[];
  const journey=Array.isArray(data?.customer_journey)?data.customer_journey:[];
  const actions=Array.isArray(data?.boris_actions)?data.boris_actions:[];
  const plan=Array.isArray(data?.first_24h_plan)?data.first_24h_plan:[];
  const company=data?.company?.name||'Ваш бизнес';
  const contact=data?.contacts||{};
  const status=data?.data_status||{};
  const demandState=String((status?.sections||[]).find((x:any)=>x?.code==='demand')?.state||'');
  const demandPending=demandState==='loading'||['PARTIAL_READY','REFRESHING'].includes(String(status?.state||''));
  const checkHref=data?.cta?.href||data?.referral?.path||'/audit';
  const shareHref=typeof window!=='undefined'?window.location.origin+'/v/'+token:'/v/'+token;
  const referralHref=typeof window!=='undefined'&&data?.referral?.path
    ? window.location.origin+data.referral.path
    : (typeof window!=='undefined'?window.location.origin+'/audit':'/audit');
  const shareText='Я проверил бизнес через BORIS. Он показал, где могут теряться клиенты и что можно улучшить. Результат: '+shareHref+' Проверить свой бизнес: '+referralHref;
  const nicheText=([company,...(demand.top_requests||[]).map((x:any)=>String(x?.phrase||''))]).join(' ').toLowerCase();
  const nicheProfile=
    /кухн|мебел|шкаф|гардероб|фасад/.test(nicheText)
      ? {label:'Мебель и интерьер',description:'BORIS смотрит спрос, разные сценарии товара, рекламу и путь клиента до расчёта или замера.',left:'bg-amber-400/15',right:'bg-orange-500/15'}
      : /окн|остеклен|балкон|лоджи|двер/.test(nicheText)
        ? {label:'Окна и монтаж',description:'BORIS смотрит географию спроса, типовые запросы, рекламу, скорость ответа и переход к замеру.',left:'bg-cyan-400/20',right:'bg-sky-500/15'}
        : /маникюр|педикюр|эпиляц|салон|космет|бров|ресниц/.test(nicheText)
          ? {label:'Красота и запись',description:'BORIS смотрит спрос, запись, скорость реакции, повторные визиты и рекламные предложения.',left:'bg-fuchsia-400/15',right:'bg-rose-500/15'}
          : /оборудован|производств|металлоконструк|поставк|оптов|\bопт\b/.test(nicheText)
            ? {label:'B2B и производство',description:'BORIS смотрит отраслевой спрос, разные типы заказчиков, рекламу, скорость обработки заявки и следующий коммерческий шаг.',left:'bg-indigo-400/20',right:'bg-blue-500/15'}
            : /строител|ремонт квартир|ремонт домов|дом под ключ|кровл|фундамент|отделоч/.test(nicheText)
              ? {label:'Строительство и ремонт',description:'BORIS смотрит географию спроса, виды работ, рекламные предложения, скорость ответа и переход клиента к расчёту или выезду.',left:'bg-emerald-400/15',right:'bg-teal-500/15'}
              : {label:'Бизнес',description:'BORIS изучил доступные данные о спросе, рекламе и обработке клиентов. Ниже — только то, что полезно владельцу бизнеса.',left:'bg-blue-500/20',right:'bg-violet-500/20'};

  const mainConclusion=useMemo(()=>{
    const text=String(data?.summary?.text||'').trim();
    if(text)return text;
    return score>=70
      ? 'У бизнеса уже есть сильная база. Главная задача — увеличить охват и не терять обращения на следующих шагах.'
      : 'Есть заметный потенциал роста: BORIS нашёл точки, которые можно усилить в рекламе и обработке клиентов.';
  },[data?.summary?.text,score]);

  function trackCheck(){
    fire('check_my_business_clicked',{referral_code:data?.referral?.code||undefined});
    fire('viral_cta_clicked',{referral_code:data?.referral?.code||undefined});
  }

  async function copy(kind:'result'|'invite'){
    const text=kind==='result'?shareText:referralHref;
    try{
      await navigator.clipboard.writeText(text);
      setCopied(kind);
      fire('share_completed',{referral_code:data?.referral?.code||undefined});
      window.setTimeout(()=>setCopied(''),1600);
    }catch{
      setCopied('');
    }
  }

  async function nativeShare(){
    fire('share_clicked',{referral_code:data?.referral?.code||undefined});
    if(navigator.share){
      try{
        await navigator.share({title:'Разбор бизнеса от BORIS',text:shareText,url:shareHref});
        fire('share_completed',{referral_code:data?.referral?.code||undefined});
        return;
      }catch{}
    }
    await copy('result');
  }

  function shareTo(channel:'whatsapp'|'telegram'|'max'){
    fire('share_clicked',{referral_code:data?.referral?.code||undefined});
    if(channel==='whatsapp'){
      window.open('https://wa.me/?text='+encodeURIComponent(shareText),'_blank','noopener,noreferrer');
      fire('share_completed',{referral_code:data?.referral?.code||undefined});
      return;
    }
    if(channel==='telegram'){
      window.open('https://t.me/share/url?url='+encodeURIComponent(shareHref)+'&text='+encodeURIComponent('BORIS разобрал мой бизнес и показал точки роста. Проверить свой бизнес: '+referralHref),'_blank','noopener,noreferrer');
      fire('share_completed',{referral_code:data?.referral?.code||undefined});
      return;
    }
    window.open('https://max.ru/:share?text='+encodeURIComponent(shareText),'_blank','noopener,noreferrer');
    fire('share_completed',{referral_code:data?.referral?.code||undefined});
  }

  function shareCard(){
    fire('share_clicked',{referral_code:data?.referral?.code||undefined,format:'image'});
    window.open('/v/'+encodeURIComponent(token)+'/opengraph-image','_blank','noopener,noreferrer');
  }

  function trial(){
    fire('cta_test_clicked',{referral_code:data?.referral?.code||undefined});
    fire('trial_cta_clicked',{referral_code:data?.referral?.code||undefined});
    window.open(String(contact?.boris_url||'/audit'),'_blank','noopener,noreferrer');
  }

  function specialist(channel:'whatsapp'|'max'){
    fire(channel==='whatsapp'?'whatsapp_clicked':'max_clicked',{referral_code:data?.referral?.code||undefined});
    let url=channel==='whatsapp'?String(contact?.whatsapp_url||''):String(contact?.max_url||'');
    if(channel==='whatsapp'&&url){
      const msg='Здравствуйте. Я посмотрел разбор BORIS для '+company+' и хочу понять, как запустить это у себя.';
      url+=(url.includes('?')?'&':'?')+'text='+encodeURIComponent(msg);
    }
    if(url)window.open(url,'_blank','noopener,noreferrer');
  }

  if(error){
    return <main className="min-h-screen bg-[#071020] px-5 py-20 text-white">
      <div className="mx-auto max-w-xl rounded-[32px] border border-white/10 bg-white/[.06] p-8 text-center shadow-2xl">
        <div className="text-sm font-black uppercase tracking-[.18em] text-blue-300">BORIS</div>
        <h1 className="mt-4 text-3xl font-black">{error}</h1>
        <p className="mt-3 text-white/60">Ссылка могла истечь, быть отозвана или сеть временно недоступна.</p>
        <div className="mt-7 flex flex-wrap justify-center gap-2">
          <button onClick={()=>{setError('');setData(null);void load();}} className="inline-flex rounded-full bg-white px-6 py-3 font-black text-[#071020]">Повторить</button>
          <Link href="/audit" className="inline-flex rounded-full border border-white/15 bg-white/[.06] px-6 py-3 font-black text-white">Проверить свой бизнес</Link>
        </div>
      </div>
    </main>;
  }

  if(!data){
    return <main className="min-h-screen bg-[#071020] px-5 py-20 text-white">
      <div className="mx-auto max-w-3xl">
        <div className="h-4 w-28 animate-pulse rounded-full bg-white/10"/>
        <div className="mt-8 h-14 max-w-2xl animate-pulse rounded-2xl bg-white/10"/>
        <div className="mt-4 h-6 max-w-xl animate-pulse rounded-xl bg-white/10"/>
        <div className="mt-10 grid gap-4 sm:grid-cols-3">
          {[1,2,3].map(x=><div key={x} className="h-28 animate-pulse rounded-[28px] bg-white/10"/>)}
        </div>
      </div>
    </main>;
  }

  return <main className="min-h-screen overflow-hidden bg-[#f4f7fb] text-[#13203a]">
    <section className="relative overflow-hidden bg-[#071020] px-5 pb-24 pt-6 text-white sm:pb-32">
      <div className={"pointer-events-none absolute -left-32 -top-40 h-[520px] w-[520px] rounded-full blur-3xl "+nicheProfile.left}/>
      <div className={"pointer-events-none absolute -right-40 top-20 h-[500px] w-[500px] rounded-full blur-3xl "+nicheProfile.right}/>
      <div className="relative mx-auto max-w-[1680px]">
        <header className="flex items-center justify-between">
          <Link href="/audit" className="text-xl font-black tracking-[-.04em]">BORIS <span className="text-blue-400">AUDIT</span></Link>
          <button onClick={nativeShare} className="rounded-full border border-white/15 bg-white/[.06] px-4 py-2 text-sm font-black backdrop-blur">Поделиться</button>
        </header>

        {status?.state&&status.state!=='READY'&&<div className="mt-8 rounded-[22px] border border-white/10 bg-white/[.06] p-4 backdrop-blur">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-sm font-black">Разбор уже можно смотреть</div>
              <div className="mt-1 text-xs text-white/55">{status.state==='FAILED_PARTIAL'?'Один источник сейчас недоступен. Остальные выводы готовы.':'Дополнительные данные догружаются независимо — страница не заблокирована.'}</div>
            </div>
            <div className="text-lg font-black text-blue-300">{Number(status.progress_percent||80)}%</div>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-blue-400 transition-all duration-700" style={{width:String(Number(status.progress_percent||80))+'%'}}/></div>
          <div className="mt-3 flex flex-wrap gap-2">
            {(status.sections||[]).map((s:any)=><span key={s.code} className="rounded-full bg-white/[.07] px-3 py-1 text-[11px] font-bold text-white/70">{s.state==='ready'?'✓ ':s.state==='loading'?'○ ':'— '}{s.label}</span>)}
          </div>
        </div>}

        <div className="mt-16 grid gap-10 lg:grid-cols-[1.15fr_.85fr] lg:items-end">
          <div>
            <div className="inline-flex rounded-full border border-blue-300/20 bg-blue-400/10 px-4 py-2 text-xs font-black uppercase tracking-[.14em] text-blue-200">Персональный разбор · {nicheProfile.label}</div>
            <h1 className="mt-6 max-w-4xl text-4xl font-black leading-[1.02] tracking-[-.055em] sm:text-6xl">{data?.summary?.headline||('Мы нашли точки роста для «'+company+'»')}</h1>
            <p className="mt-6 max-w-3xl text-base leading-7 text-white/65 sm:text-lg">{nicheProfile.description}</p>
            <div className="mt-9 grid gap-3 sm:grid-cols-3">
              <div className="rounded-[26px] border border-white/10 bg-white/[.07] p-5 backdrop-blur"><div className="text-4xl font-black tracking-[-.05em]">{score}<span className="text-base text-white/40"> /100</span></div><div className="mt-2 text-sm font-bold text-white/55">Потенциал роста</div></div>
              <div className="rounded-[26px] border border-white/10 bg-white/[.07] p-5 backdrop-blur"><div className="text-4xl font-black tracking-[-.05em]">{demand.total_count!=null?money(Number(demand.total_count)):'—'}</div><div className="mt-2 text-sm font-bold text-white/55">целевых запросов в месяц</div></div>
              <div className="rounded-[26px] border border-white/10 bg-white/[.07] p-5 backdrop-blur"><div className="text-4xl font-black tracking-[-.05em]">{advertising.grounded?(String(advertising.recommended_low)+'–'+String(advertising.recommended_high)):'—'}</div><div className="mt-2 text-sm font-bold text-white/55">рекламных предложений для старта</div></div>
            </div>
          </div>
          <div className="rounded-[34px] border border-white/10 bg-gradient-to-br from-white/[.13] to-white/[.04] p-7 shadow-2xl backdrop-blur sm:p-8">
            <div className="text-xs font-black uppercase tracking-[.16em] text-blue-300">Главный вывод</div>
            <p className="mt-4 text-2xl font-black leading-tight tracking-[-.03em]">{mainConclusion}</p>
            <a href="#growth" className="mt-7 inline-flex rounded-full bg-white px-5 py-3 text-sm font-black text-[#071020]">Показать, что исправить ↓</a>
          </div>
        </div>
      </div>
    </section>

    <section id="growth" className="px-5 py-20 sm:py-28">
      <div className="mx-auto max-w-[1680px]">
        <div className="max-w-3xl">
          <div className="text-xs font-black uppercase tracking-[.14em] text-blue-600">Главное</div>
          <h2 className="mt-3 text-4xl font-black tracking-[-.045em] sm:text-5xl">3 точки роста, которые стоит проверить первыми</h2>
        </div>
        <div className="mt-10 grid gap-5 lg:grid-cols-3">
          {opportunities.slice(0,3).map((x:any,i:number)=><article key={x.code||i} className={"relative overflow-hidden rounded-[34px] p-7 shadow-[0_18px_60px_rgba(20,42,84,.09)] "+(i===0?'bg-[#10264f] text-white':i===1?'bg-white':'bg-[#e9efff]')}>
            <div className={"text-6xl font-black tracking-[-.06em] "+(i===0?'text-white/12':'text-[#15213b]/10')}>0{i+1}</div>
            <h3 className="mt-8 text-2xl font-black tracking-[-.035em]">{x.title}</h3>
            <p className={"mt-4 text-sm leading-6 "+(i===0?'text-white/65':'text-[#5e6b82]')}>{x.description}</p>
            {i===2&&<div className="mt-6 inline-flex rounded-full bg-white px-4 py-2 text-xs font-black text-[#203c7b]">BORIS может делать это автоматически</div>}
          </article>)}
        </div>
      </div>
    </section>

    <section className="px-5 pb-20 sm:pb-28">
      <div className="mx-auto grid max-w-[1680px] gap-5 lg:grid-cols-2">
        <div className="rounded-[36px] bg-white p-7 shadow-[0_18px_70px_rgba(20,42,84,.08)] sm:p-9">
          <div className="text-xs font-black uppercase tracking-[.14em] text-[#8b95a8]">Сейчас</div>
          <h2 className="mt-3 text-3xl font-black tracking-[-.04em]">Много ручного контроля</h2>
          <div className="mt-7 space-y-3">
            {['Реклама и новое обращение','Нужно быстро заметить клиента','Ответить и не забыть','Вернуться к клиенту позже','Проконтролировать результат'].map((x,i)=><div key={x} className="flex items-center gap-3 rounded-2xl bg-[#f4f6fa] px-4 py-3"><span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-white text-xs font-black text-[#7e899c]">{i+1}</span><span className="text-sm font-bold text-[#526077]">{x}</span></div>)}
          </div>
        </div>
        <div className="rounded-[36px] bg-[#10264f] p-7 text-white shadow-[0_22px_80px_rgba(12,35,80,.2)] sm:p-9">
          <div className="text-xs font-black uppercase tracking-[.14em] text-blue-300">С BORIS</div>
          <h2 className="mt-3 text-3xl font-black tracking-[-.04em]">Система держит следующий шаг</h2>
          <div className="mt-7 space-y-3">
            {['BORIS видит обращение','Отвечает 24/7','Фиксирует клиента и следующий шаг','Напоминает и возвращает обращения','Показывает владельцу результат'].map((x,i)=><div key={x} className="flex items-center gap-3 rounded-2xl bg-white/[.08] px-4 py-3"><span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-blue-400 text-xs font-black text-[#071020]">✓</span><span className="text-sm font-bold text-white/80">{x}</span></div>)}
          </div>
        </div>
      </div>
    </section>

    <section className="bg-white px-5 py-20 sm:py-28">
      <div className="mx-auto max-w-[1680px]">
        <div className="grid gap-12 lg:grid-cols-[.8fr_1.2fr] lg:items-start">
          <div>
            <div className="text-xs font-black uppercase tracking-[.14em] text-violet-600">Путь клиента</div>
            <h2 className="mt-3 text-4xl font-black tracking-[-.045em]">Где клиент может потеряться</h2>
            <p className="mt-4 text-sm leading-6 text-[#637086]">BORIS не придумывает цифры. Если этап пока не измеряется, мы так и пишем — после подключения система начнёт собирать показатель.</p>
          </div>
          <div className="relative">
            <div className="absolute bottom-8 left-[19px] top-8 w-px bg-[#dce4f2]"/>
            <div className="space-y-4">
              {journey.slice(0,5).map((x:any,i:number)=><div key={x.code||i} className="relative flex gap-4 rounded-[24px] bg-[#f7f9fc] p-5">
                <div className="relative z-10 grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[#15213b] text-xs font-black text-white">{i+1}</div>
                <div className="min-w-0 flex-1"><div className="font-black">{x.title}</div><div className="mt-1 text-sm text-[#728096]">{x.value!=null?money(Number(x.value))+' за измеряемый период':'Недостаточно данных для точного расчёта — BORIS начнёт измерять после подключения.'}</div></div>
              </div>)}
            </div>
          </div>
        </div>
      </div>
    </section>

    <section className="px-5 py-20 sm:py-28">
      <div className="mx-auto max-w-[1680px]">
        <div className="grid gap-5 lg:grid-cols-[1.05fr_.95fr]">
          <div className="rounded-[36px] bg-[#eaf1ff] p-7 sm:p-9">
            <div className="text-xs font-black uppercase tracking-[.14em] text-blue-700">Спрос</div>
            <h2 className="mt-3 text-4xl font-black tracking-[-.045em]">Люди уже ищут ваши услуги</h2>
            <div className="mt-6 text-6xl font-black tracking-[-.06em] text-[#15213b]">{demand.total_count!=null?'≈ '+money(Number(demand.total_count)):(demandPending?'Пока считаем':'Недостаточно данных')}</div>
            <div className="mt-2 text-sm font-bold text-[#637086]">{demand.total_count!=null?'запросов в месяц · по открытым данным Яндекса':(demandPending?'Остальной аудит уже доступен':'BORIS не будет подставлять случайную цифру')}</div>
            {demand.region_scope&&<div className="mt-5 inline-flex rounded-full bg-white px-4 py-2 text-xs font-black text-[#44516a]">{demand.region_scope}</div>}
            <div className="mt-7 flex flex-wrap gap-2">
              {(demand.top_requests||[]).slice(0,10).map((x:any,i:number)=><span key={i} className="rounded-full bg-white px-4 py-2 text-sm font-bold text-[#40506b] shadow-sm">{x.phrase}{x.count!=null?<span className="ml-2 text-[#9aa5b6]">{money(Number(x.count))}</span>:null}</span>)}
            </div>
          </div>
          <div className="rounded-[36px] bg-[#15213b] p-7 text-white sm:p-9">
            <div className="text-xs font-black uppercase tracking-[.14em] text-blue-300">Реклама</div>
            <h2 className="mt-3 text-4xl font-black tracking-[-.045em]">Чтобы охватить больше спроса</h2>
            {advertising.grounded?<><div className="mt-7 text-6xl font-black tracking-[-.06em]">{advertising.recommended_low}–{advertising.recommended_high}</div><div className="mt-2 text-sm font-bold text-white/55">активных рекламных предложений для старта</div></>:<div className="mt-7 rounded-[22px] bg-white/[.08] p-5 text-sm font-bold leading-6 text-white/70">Пока недостаточно данных для точного количества. BORIS не будет подставлять случайную цифру.</div>}
            <p className="mt-7 text-sm leading-6 text-white/65">Не просто копии одного объявления: разные товары, потребности клиентов, изображения, заголовки и предложения.</p>
          </div>
        </div>
      </div>
    </section>

    {data?.money?.available&&<section className="px-5 pb-20 sm:pb-28">
      <div className="mx-auto max-w-[1680px] rounded-[38px] bg-white p-7 shadow-[0_20px_80px_rgba(20,42,84,.08)] sm:p-10">
        <div className="text-xs font-black uppercase tracking-[.14em] text-rose-600">Где бизнес может терять деньги</div>
        <div className="mt-5 text-4xl font-black tracking-[-.045em] sm:text-5xl">{money(Number(data.money.lost_revenue_low||0))}–{money(Number(data.money.lost_revenue_high||0))} ₽</div>
        <p className="mt-4 max-w-3xl text-sm leading-6 text-[#647187]">{data.money.disclaimer}</p>
      </div>
    </section>}

    <section className="bg-[#071020] px-5 py-20 text-white sm:py-28">
      <div className="mx-auto max-w-[1680px]">
        <div className="max-w-3xl"><div className="text-xs font-black uppercase tracking-[.14em] text-blue-300">Что BORIS сделает сам</div><h2 className="mt-3 text-4xl font-black tracking-[-.045em] sm:text-5xl">Не ещё один отчёт. Следующий шаг — действие.</h2></div>
        <div className="mt-10 grid gap-4 md:grid-cols-2">
          {actions.map((x:any,i:number)=><div key={i} className="rounded-[30px] border border-white/10 bg-white/[.06] p-6"><h3 className="text-xl font-black">{x.title}</h3><div className="mt-5 flex flex-wrap gap-2">{(x.items||[]).map((item:string)=><span key={item} className="rounded-full bg-white/[.08] px-3 py-2 text-xs font-bold text-white/70">{item}</span>)}</div></div>)}
        </div>
      </div>
    </section>

    <section className="px-5 py-20 sm:py-28">
      <div className="mx-auto max-w-[1680px]">
        <div className="grid gap-10 lg:grid-cols-[.75fr_1.25fr]">
          <div><div className="text-xs font-black uppercase tracking-[.14em] text-blue-600">Первые сутки</div><h2 className="mt-3 text-4xl font-black tracking-[-.045em]">Что можно запустить уже за первые 24 часа</h2></div>
          <div className="grid gap-3 sm:grid-cols-2">
            {plan.map((x:string,i:number)=><div key={x} className="flex gap-4 rounded-[24px] bg-white p-5 shadow-[0_12px_45px_rgba(20,42,84,.06)]"><span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[#eaf1ff] text-xs font-black text-blue-700">{i+1}</span><span className="pt-1 text-sm font-black">{x}</span></div>)}
          </div>
        </div>
      </div>
    </section>

    <section className="px-5 pb-20 sm:pb-28">
      <div className="mx-auto max-w-[1680px] rounded-[40px] bg-gradient-to-br from-[#dce8ff] via-white to-[#efe7ff] p-7 sm:p-10">
        <div className="grid gap-8 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <div className="text-xs font-black uppercase tracking-[.14em] text-blue-700">Поделиться результатом</div>
            <h2 className="mt-3 max-w-3xl text-4xl font-black tracking-[-.045em]">Знаете предпринимателя, которому тоже пригодится такой разбор?</h2>
            {data?.referral?.reward_active&&<p className="mt-4 max-w-2xl text-sm leading-6 text-[#59677e]">Если приглашённый предприниматель зарегистрируется и реально подключит аккаунт, вам начислится <b>+7 дней BORIS</b>. За простой клик награда не начисляется.</p>}
          </div>
          <div className="flex flex-wrap gap-2 lg:max-w-sm lg:justify-end">
            <button onClick={()=>shareTo('whatsapp')} className="rounded-full bg-[#15213b] px-5 py-3 text-sm font-black text-white">WhatsApp</button>
            <button onClick={()=>shareTo('telegram')} className="rounded-full bg-white px-5 py-3 text-sm font-black text-[#15213b] shadow-sm">Telegram</button>
            <button onClick={()=>shareTo('max')} className="rounded-full bg-white px-5 py-3 text-sm font-black text-[#15213b] shadow-sm">MAX</button>
            <button onClick={shareCard} className="rounded-full bg-white px-5 py-3 text-sm font-black text-[#15213b] shadow-sm">Карточка результата</button>
            <button onClick={()=>copy('result')} className="rounded-full bg-white px-5 py-3 text-sm font-black text-[#15213b] shadow-sm">{copied==='result'?'Скопировано ✓':'Копировать'}</button>
          </div>
        </div>
      </div>
    </section>

    <section className="px-5 pb-32">
      <div className="mx-auto grid max-w-[1680px] gap-5 lg:grid-cols-[1.15fr_.85fr]">
        <div className="rounded-[38px] bg-[#15213b] p-8 text-white sm:p-10">
          <div className="text-xs font-black uppercase tracking-[.14em] text-blue-300">Следующий шаг</div>
          <h2 className="mt-3 text-4xl font-black tracking-[-.045em]">Хотите, BORIS попробует исправить это сам?</h2>
          <p className="mt-4 max-w-2xl text-sm leading-6 text-white/65">Поможем подключить BORIS к вашему бизнесу и покажем работу на реальных обращениях. Тест — {Number(contact?.trial_days||4)} дня.</p>
          <button onClick={trial} className="mt-7 rounded-full bg-white px-6 py-3.5 text-sm font-black text-[#071020]">Попробовать BORIS {Number(contact?.trial_days||4)} дня бесплатно</button>
        </div>
        <div className="rounded-[38px] bg-white p-8 shadow-[0_18px_70px_rgba(20,42,84,.08)]">
          <div className="flex items-center gap-4">
            <img src="/boris-preview/kirill.webp" alt="Кирилл — создатель BORIS AI" className="h-16 w-16 shrink-0 rounded-full object-cover object-top shadow-sm" />
            <div><div className="text-2xl font-black">{contact?.contact_name||'Кирилл'}</div><div className="mt-1 text-sm font-bold text-[#778399]">{contact?.contact_title||'Помогу запустить BORIS для вашего бизнеса'}</div></div>
          </div>
          <p className="mt-6 text-sm leading-6 text-[#59677e]">Можно написать напрямую. Посмотрим ваш бизнес и решим, какие задачи BORIS имеет смысл взять на себя в первую очередь.</p>
          <div className="mt-6 flex flex-wrap gap-2">
            {contact?.whatsapp_url&&<button onClick={()=>specialist('whatsapp')} className="rounded-full bg-[#15213b] px-5 py-3 text-sm font-black text-white">Написать в WhatsApp</button>}
            {contact?.max_url&&<button onClick={()=>specialist('max')} className="rounded-full bg-[#eaf1ff] px-5 py-3 text-sm font-black text-blue-700">Написать в MAX</button>}
          </div>
        </div>
      </div>
    </section>

    <div className="fixed bottom-4 left-4 right-4 z-50 sm:left-auto sm:right-6 sm:w-auto">
      <Link onClick={trackCheck} href={checkHref} className="flex items-center justify-center rounded-full bg-[#2563eb] px-6 py-4 text-sm font-black text-white shadow-[0_18px_50px_rgba(37,99,235,.35)]">Проверить свой бизнес</Link>
    </div>
  </main>;
}
