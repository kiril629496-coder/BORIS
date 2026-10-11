'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import TurnstileBox from '../lib/turnstile';
import { persistSession, registerUser, rememberVerification } from '../lib/register';
import { auditAttribution, captureAuditAttribution, fireAuditEvent } from './lib/attribution';

function authHeaders(): Record<string,string> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('boris_token') || '' : '';
  const h: Record<string,string> = {};
  if (token) h.Authorization = 'Bearer ' + token;
  return h;
}

export default function AuditGatewayPage() {
  const [ready,setReady] = useState(false);
  const [loggedIn,setLoggedIn] = useState(false);
  const [state,setState] = useState<any>(null);
  const [avitoAccountId,setAvitoAccountId] = useState('');
  const [avitoConnected,setAvitoConnected] = useState(false);
  const [name,setName] = useState('');
  const [company,setCompany] = useState('');
  const [email,setEmail] = useState('');
  const [password,setPassword] = useState('');
  const [niche,setNiche] = useState('');
  const [region,setRegion] = useState('');
  const [legal,setLegal] = useState(false);
  const [marketing,setMarketing] = useState(false);
  const [captcha,setCaptcha] = useState('');
  const [captchaKey,setCaptchaKey] = useState(0);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const [registrationOpen,setRegistrationOpen] = useState(false);

  useEffect(()=>{
    const visitAttribution=captureAuditAttribution();
    fireAuditEvent('viral_page_view','audit_gateway');
    fireAuditEvent('audit_view','audit_gateway');
    try{
      const ym=(window as any).ym;
      if(typeof ym==='function'&&String(visitAttribution.utm_source||'').toLowerCase()==='email') {
        ym(111297252,'reachGoal','email_audit_visit',{
          utm_campaign:visitAttribution.utm_campaign||undefined,
          utm_content:visitAttribution.utm_content||undefined,
          utm_term:visitAttribution.utm_term||undefined,
        });
      }
    }catch{}
    const token = localStorage.getItem('boris_token');
    if (!token) { setReady(true); return; }
    fetch('/api/viral/gateway/state',{headers:authHeaders()})
      .then(async r=>{
        if(r.status===401||r.status===403) throw new Error('unauthorized');
        const d=await r.json(); if(!r.ok) throw new Error(d?.detail||'state');
        return d;
      })
      .then(async d=>{
        setLoggedIn(true);setState(d.state||{});
        try{
          const ar=await fetch('/api/accounts/list',{headers:authHeaders(),cache:'no-store'});
          const ad=await ar.json();
          const list=ad?.accounts||[];
          const saved=localStorage.getItem('boris_currentAccount')||'';
          const account=(list.find((a:any)=>a.account_id===saved)||list[0])?.account_id||'';
          if(account){
            setAvitoAccountId(account);
            const cr=await fetch('/api/accounts/avito_connection?account_id='+encodeURIComponent(account)+'&verify=false',{headers:authHeaders(),cache:'no-store'});
            const cd=await cr.json();
            setAvitoConnected(cd?.connection==='connected'||cd?.connection==='tariff_error');
          }
        }catch{}
        setReady(true);
      })
      .catch(()=>{setLoggedIn(false);setReady(true);});
  },[]);

  async function register(e:React.FormEvent) {
    e.preventDefault();
    setError('');
    if(niche.trim().length<2 || region.trim().length<2) { setError('Укажите направление бизнеса и город / регион.'); return; }
    if(!legal) { setError('Примите пользовательское соглашение и политику обработки данных.'); return; }
    if(!captcha) { setError('Подтвердите, что вы не робот.'); return; }
    setBusy(true);
    const attribution=auditAttribution();
    fireAuditEvent('registration_started','audit_gateway');
    const r=await registerUser({
      email,password,accountName:company.trim()||niche.trim()||'Мой бизнес',plannedAccounts:'1',turnstileToken:captcha,
      attributionSessionId:String(attribution.session_id||''),
      utmSource:String(attribution.utm_source||''),
      utmMedium:String(attribution.utm_medium||''),
      utmCampaign:String(attribution.utm_campaign||''),
      utmContent:String(attribution.utm_content||''),
      utmTerm:String(attribution.utm_term||''),
      landingPath:String(attribution.landing_path||'/audit'),
      firstSeenAt:String(attribution.first_seen_at||''),
      qa:Boolean(attribution.qa)
    });
    if(!r.ok) {
      setError(r.error); setBusy(false); setCaptcha(''); setCaptchaKey(x=>x+1); return;
    }
    const saved=persistSession(r.data,true);
    if(saved){
      fireAuditEvent('viral_registration','audit_gateway');
      fireAuditEvent('registration_completed','audit_gateway');
      try{
        const ym=(window as any).ym;
        if(typeof ym==='function') {
          const params={
            source:'audit_gateway',
            utm_source:attribution.utm_source||undefined,
            utm_campaign:attribution.utm_campaign||undefined,
            utm_content:attribution.utm_content||undefined,
            utm_term:attribution.utm_term||undefined,
          };
          ym(111297252,'reachGoal','boris_audit_registration',params);
          if(String(attribution.utm_source||'').toLowerCase()==='email') {
            ym(111297252,'reachGoal','email_registration',params);
          }
        }
      }catch{}
    }
    if(!saved) {
      setError('Для этого email аккаунт уже может существовать. Войдите и продолжите аудит.');
      setBusy(false); setCaptcha(''); setCaptchaKey(x=>x+1); return;
    }
    localStorage.setItem('boris_audit_contact_name',name.trim());
    localStorage.setItem('boris_audit_niche',niche.trim());
    localStorage.setItem('boris_audit_region',region.trim());
    localStorage.setItem('boris_audit_marketing_opt_in',marketing?'1':'0');
    localStorage.setItem('boris_post_verify_return','/audit/onboarding');
    if(r.data?.user?.status==='pending_verification') {
      rememberVerification(r.data);
      window.location.href='/verify';
      return;
    }
    window.location.href='/audit/onboarding';
  }

  function revealRegistration() {
    setError('');
    if(niche.trim().length<2 || region.trim().length<2) {
      setError('Укажите, чем вы занимаетесь, и ваш город / регион.');
      return;
    }
    try {
      localStorage.setItem('boris_audit_niche',niche.trim());
      localStorage.setItem('boris_audit_region',region.trim());
    } catch {}
    fireAuditEvent('audit_start','audit_gateway');
    setRegistrationOpen(true);
    setTimeout(()=>document.getElementById('audit-registration-fields')?.scrollIntoView({behavior:'smooth',block:'center'}),80);
  }

  if(!ready) return <main className="audit-canonical min-h-screen grid place-items-center text-[#667085]">Готовим бесплатный аудит…</main>;

  if(loggedIn) {
    const completed=Boolean(state?.audit_completed);
    const hasAssumptions=Number(state?.declared_average_check_rub||0)>0 && state?.declared_sales_conversion_percent!=null;
    const canRunConnected=avitoConnected&&Boolean(avitoAccountId)&&hasAssumptions;
    return (
      <main className="audit-canonical min-h-screen">
        <header className="px-5 py-5"><div className="mx-auto flex max-w-6xl items-center justify-between">
          <Link href="/audit" className="text-xl font-black">BORIS <span className="text-[#2563eb]">AUDIT</span></Link>
          <Link href="/dashboard/home" className="rounded-full border border-[#dce3ee] bg-white px-4 py-2 text-sm font-bold">Открыть полный BORIS</Link>
        </div></header>
        <section className="px-5 pb-16 pt-10">
          <div className="mx-auto max-w-6xl">
            <div className="max-w-3xl">
              <div className="inline-flex rounded-full bg-[#eaf1ff] px-4 py-2 text-sm font-black text-[#2563eb]">Личный Audit Gateway</div>
              <h1 className="mt-5 text-4xl font-black tracking-[-.04em] sm:text-6xl">{completed?'Ваш аудит уже начат':'Начнём с вашего бизнеса, а не с настроек'}</h1>
              <p className="mt-5 text-lg leading-8 text-[#667085]">{completed?'Можно открыть последний результат, пересчитать данные или подключить Avito для более точного аудита.':'Сначала BORIS покажет, как сейчас работают продажи и где есть потенциал роста. Основной интерфейс откроете тогда, когда результат уже будет понятен.'}</p>
            </div>

            <div className="mt-10 grid gap-5 lg:grid-cols-3">
              <Link href="/audit/onboarding" className="audit-reference-card p-7">
                <div className="text-sm font-bold text-[#2878ff]">{completed?'ПОВТОРНЫЙ АУДИТ':'ШАГ 1'}</div>
                <div className="mt-3 text-2xl font-black">{completed?'Пересчитать результат':'Быстрый аудит'}</div>
                <p className="mt-3 text-sm leading-6 text-[#53617a]">Несколько цифр → предварительная оценка → денежный диапазон.</p>
                <div className="mt-8 font-black text-[#2878ff]">Начать →</div>
              </Link>
              <Link href={canRunConnected?('/audit/analyzing?account_id='+encodeURIComponent(avitoAccountId)):(avitoConnected?'/audit/onboarding':'/audit/connect')} className="audit-reference-card p-7">
                <div className="text-sm font-bold text-[#2563eb]">{avitoConnected?'РЕАЛЬНЫЕ ДАННЫЕ':'ТОЧНЕЕ'}</div>
                <div className="mt-3 text-2xl font-black">{canRunConnected?'Запустить точный аудит':avitoConnected?'Уточнить 6 цифр':'Подключить Avito'}</div>
                <p className="mt-3 text-sm leading-6 text-[#667085]">{canRunConnected?'Avito уже подключён — BORIS может сразу проверить реальные обращения и статистику.':avitoConnected?'Avito уже подключён. Осталось указать средний чек и конверсию, чтобы денежная часть не была выдумана.':'Просмотры, контакты, переписки, фактическое время ответа и клиенты без ответа.'}</p>
                <div className="mt-8 font-black text-[#2563eb]">{canRunConnected?'Анализировать →':avitoConnected?'Продолжить →':'Подключить →'}</div>
              </Link>
              <Link href={state?.latest_audit_id?'/audit/result?audit='+encodeURIComponent(state.latest_audit_id):'/audit/onboarding'} className="audit-reference-card p-7">
                <div className="text-sm font-bold text-[#087443]">РЕЗУЛЬТАТ</div>
                <div className="mt-3 text-2xl font-black">Мой результат</div>
                <p className="mt-3 text-sm leading-6 text-[#667085]">{state?.latest_score!=null?'Последний результат: '+state.latest_score+'/100':'После первого аудита здесь появится ваш результат.'}</p>
                <div className="mt-8 font-black text-[#087443]">Открыть →</div>
              </Link>
            </div>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="audit-canonical audit-cold min-h-screen">
      <header className="audit-cold-header px-5">
        <div className="audit-wide mx-auto flex items-center justify-between gap-4">
          <Link href="/" className="flex items-center gap-2 text-xl font-black">
            <span className="audit-logo-mark">B</span>
            <span>BORIS <span className="text-[#2878ff]">AUDIT</span></span>
          </Link>
          <nav className="hidden items-center gap-6 text-sm font-bold text-[#53617a] md:flex">
            <a href="#how-it-works" className="hover:text-[#2878ff]">Как это работает</a>
            <a href="#audit-preview" className="hover:text-[#2878ff]">Что получите</a>
          </nav>
          <Link
            href="/login"
            onClick={()=>{try{localStorage.setItem("boris_post_login_return","/audit");}catch{}}}
            className="rounded-[10px] border border-[#dfe6f2] bg-white px-4 py-2.5 text-sm font-black text-[#15213b]"
          >
            Войти
          </Link>
        </div>
      </header>

      <section className="audit-hero relative overflow-hidden px-5 pb-14 pt-10 sm:pb-20 sm:pt-16">
        <div className="audit-hero-glow audit-hero-glow-one"/>
        <div className="audit-hero-glow audit-hero-glow-two"/>
        <div className="audit-wide audit-hero-frame relative mx-auto grid gap-10 lg:grid-cols-[1.08fr_.92fr] lg:items-center">
          <div>
            <div className="audit-kicker">БЕСПЛАТНЫЙ АУДИТ НИШИ И ПРОДАЖ</div>
            <h1 className="audit-cold-title mt-5">
              Проверьте, где бизнес <span>теряет заявки до вложений в продвижение.</span>
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-8 text-[#53617a] sm:text-xl">
              BORIS проверит спрос в Яндексе, публичную выдачу Avito и — после подключения — реальные обращения.
              Покажет конкуренцию, точки потерь и конкретный план: что менять первым и что BORIS может взять на себя.
            </p>

            <div className="mt-7 flex flex-col gap-3 sm:flex-row">
              <a href="#audit-start" className="audit-primary-cta inline-flex items-center justify-center rounded-[12px] bg-[#2878ff] px-6 py-4 text-base font-black text-white">
                Проверить мой бизнес бесплатно <span aria-hidden="true">→</span>
              </a>
              <Link href="/audit/demo/planeta-mebeli" className="inline-flex items-center justify-center rounded-[12px] border border-[#dfe6f2] bg-white px-6 py-4 text-base font-black text-[#15213b]">
                Посмотреть готовый аудит
              </Link>
            </div>

            <div className="mt-6 flex flex-wrap gap-x-5 gap-y-2 text-sm font-bold text-[#53617a]">
              <span>✓ Без банковской карты</span>
              <span>✓ Без доступа к Avito на старте</span>
              <span>✓ Можно остановиться после аудита</span>
            </div>

            <div className="audit-no-sales mt-7">
              <span className="audit-no-sales-icon">✓</span>
              <div>
                <div className="font-black text-[#15213b]">Это не заявка менеджеру и не «бесплатная консультация».</div>
                <div className="mt-1 text-sm leading-6 text-[#53617a]">Вы сначала получаете собственный результат и только потом сами решаете, запускать ли бесплатный тест.</div>
              </div>
            </div>
          </div>

          <div className="audit-result-mock">
            <div className="audit-result-topbar">
              <div className="flex items-center gap-2">
                <span className="audit-live-dot"/>
                <span className="text-xs font-black uppercase tracking-[.08em] text-[#2878ff]">Пример отчёта</span>
              </div>
              <span className="text-xs font-bold text-[#8490a6]">BORIS AUDIT</span>
            </div>
            <div className="p-5 sm:p-7">
              <div className="flex items-start justify-between gap-5">
                <div>
                  <div className="text-sm font-bold text-[#8490a6]">Как работают продажи</div>
                  <div className="mt-2 flex items-end gap-2">
                    <span className="text-6xl font-black tracking-[-.06em] text-[#15213b]">64</span>
                    <span className="pb-2 text-lg font-bold text-[#8490a6]">/100</span>
                  </div>
                </div>
                <div className="audit-score-ring">64</div>
              </div>
              <div className="mt-5 h-2 overflow-hidden rounded-full bg-[#e8eef8]">
                <div className="h-full w-[64%] rounded-full bg-[#2878ff]"/>
              </div>

              <div className="mt-6 grid grid-cols-2 gap-3">
                <div className="audit-mini-metric">
                  <span>Спрос</span>
                  <strong>Высокий</strong>
                </div>
                <div className="audit-mini-metric">
                  <span>Конкурентов</span>
                  <strong>27</strong>
                </div>
                <div className="audit-mini-metric">
                  <span>Старт рекламы</span>
                  <strong>28–36</strong>
                  <small>объявлений</small>
                </div>
                <div className="audit-mini-metric">
                  <span>Потенциал рынка</span>
                  <strong>58 / 100</strong>
                </div>
              </div>

              <div className="audit-money-card mt-3">
                <div className="text-xs font-black uppercase tracking-[.08em]">Где может теряться выручка</div>
                <div className="mt-2 text-2xl font-black">97 000–186 000 ₽</div>
                <div className="mt-1 text-xs opacity-75">пример расчёта · ваш результат будет рассчитан отдельно</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="how-it-works" className="audit-how-section px-5 py-8 sm:py-10">
        <div className="audit-wide audit-section-shell mx-auto">
          <div className="mx-auto max-w-3xl text-center">
            <div className="audit-kicker">НИКАКОЙ МАГИИ</div>
            <h2 className="audit-section-title mt-4 font-black text-[#15213b]">Что произойдёт после нажатия</h2>
            <p className="mt-4 text-base leading-7 text-[#53617a]">Три понятных шага. Никаких продаж до того, как вы увидите результат.</p>
          </div>
          <div className="mt-9 grid gap-4 md:grid-cols-3">
            {[
              ['01','Укажете нишу и город','Например: кухни на заказ · Казань. Этого достаточно, чтобы начать с внешнего рынка.'],
              ['02','BORIS разберёт рынок и продажи','Покажет спрос, конкурентную среду, объём рекламного теста и слабые места обработки обращений.'],
              ['03','Получите план и сами решите','Можно закончить на аудите или сразу перейти к бесплатному тесту BORIS на вашем бизнесе.']
            ].map(([n,t,d])=>(
              <div key={n} className="audit-step-card">
                <div className="audit-step-number">{n}</div>
                <h3 className="mt-5 text-xl font-black text-[#15213b]">{t}</h3>
                <p className="mt-3 text-sm leading-6 text-[#53617a]">{d}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="audit-preview" className="audit-preview-section px-5 pb-16 pt-5 sm:pb-24">
        <div className="audit-wide audit-preview-shell mx-auto">
          <div className="audit-preview-head">
            <div>
              <div className="audit-kicker">ЧТО ВЫ ПОЛУЧИТЕ</div>
              <h2 className="audit-section-title mt-4 font-black text-[#15213b]">Не PDF «для галочки». Карта рынка и следующий шаг.</h2>
              <p className="mt-4 max-w-3xl text-base leading-7 text-[#5e6b82]">
                BORIS отделяет факты от предположений: спрос берёт из Яндекса, конкурентную картину — из публичной выдачи Avito,
                а после подключения сравнивает это с реальными обращениями и скоростью ответа.
              </p>
            </div>
            <div className="audit-preview-badge">
              <span>≈ 20 сек</span>
              <small>первый рыночный результат</small>
            </div>
          </div>

          <div className="audit-source-proof" aria-label="Источники аудита">
            <div><span className="audit-source-dot audit-source-dot-blue"/><div><b>Спрос Яндекса</b><small>что и как часто ищут</small></div></div>
            <div><span className="audit-source-dot audit-source-dot-violet"/><div><b>Публичная выдача Avito</b><small>предложения, продавцы, цены, подача</small></div></div>
            <div><span className="audit-source-dot audit-source-dot-green"/><div><b>Реальные обращения</b><small>после подключения вашего аккаунта</small></div></div>
          </div>

          <div className="audit-preview-grid">
            <div className="audit-deliverables">
              <div className="audit-deliverable-grid">
                {[
                  ['01','Спрос','Какие запросы реально есть в нише и насколько рынок живой.'],
                  ['02','Avito-конкуренция','Что находится в публичной выдаче: релевантные предложения, продавцы, цены и повторяющиеся формулировки.'],
                  ['03','Рекламный старт','Сколько разных гипотез и объявлений имеет смысл тестировать, а не клонировать одно и то же.'],
                  ['04','Потери в продажах','После подключения — скорость ответа, обращения без ответа и места, где клиент выпадает из воронки.'],
                  ['05','План действий','Что исправить первым и какие задачи BORIS сможет выполнять автоматически.']
                ].map(([n,t,d])=>(
                  <article key={n} className="audit-deliverable-card">
                    <span className="audit-deliverable-number">{n}</span>
                    <div>
                      <h3>{t}</h3>
                      <p>{d}</p>
                    </div>
                  </article>
                ))}
              </div>

              <div className="audit-honesty-note">
                <span>✓</span>
                <div>
                  <b>Если Avito или другой внешний источник временно недоступен — BORIS так и напишет.</b>
                  <p>Никаких выдуманных конкурентов, цен или «точной выручки» без подтверждённых данных.</p>
                </div>
              </div>

              <Link href="/audit/demo/planeta-mebeli" className="audit-demo-link">
                <span>
                  <b>Посмотреть живой пример результата</b>
                  <small>Аудит «Планеты мебели»</small>
                </span>
                <span aria-hidden="true">→</span>
              </Link>
            </div>

            <form id="audit-start" onSubmit={register} className="audit-start-card audit-start-card-v4 scroll-mt-24">
              <div className="audit-start-top">
                <div>
                  <div className="audit-kicker">ВАШ БИЗНЕС</div>
                  <h2 className="mt-3 text-3xl font-black text-[#15213b]">Начните с двух ответов</h2>
                  <p className="mt-2 text-sm leading-6 text-[#5e6b82]">Этого достаточно, чтобы запустить внешний рыночный аудит.</p>
                </div>
                <div className="audit-free-chip"><strong>0 ₽</strong><small>навсегда</small></div>
              </div>

              <div className="audit-result-promise">
                <span>На выходе</span>
                <b>Спрос → Avito → конкуренция → план действий</b>
              </div>

              <div className="mt-6 grid gap-4 sm:grid-cols-2">
                <label className="grid gap-2">
                  <span className="text-sm font-extrabold text-[#15213b]">Что вы продаёте?</span>
                  <input
                    value={niche}
                    onChange={e=>setNiche(e.target.value)}
                    placeholder="Например: кухни на заказ"
                    className="h-12 border border-[#dce3ee] px-4"
                  />
                </label>
                <label className="grid gap-2">
                  <span className="text-sm font-extrabold text-[#15213b]">Город / регион</span>
                  <input
                    value={region}
                    onChange={e=>setRegion(e.target.value)}
                    placeholder="Например: Казань"
                    className="h-12 border border-[#dce3ee] px-4"
                  />
                </label>
              </div>

              {!registrationOpen ? (
                <>
                  {error&&<div className="mt-4 rounded-[12px] bg-red-50 p-4 text-sm font-bold text-red-700">{error}</div>}
                  <button type="button" onClick={revealRegistration} className="audit-primary-cta mt-6 w-full rounded-[14px] bg-[#2878ff] px-6 py-4 text-base font-black text-white">
                    Показать мой рынок бесплатно →
                  </button>
                  <div className="audit-trust-grid mt-4 text-center text-xs font-bold text-[#667085]">
                    <div className="audit-trust-cell">Без карты</div>
                    <div className="audit-trust-cell">Без Avito-пароля</div>
                    <div className="audit-trust-cell">Без звонка</div>
                  </div>
                  <p className="mt-4 text-center text-xs leading-5 text-[#8a96a8]">Регистрация появится только после этих двух полей — чтобы сохранить результат.</p>
                </>
              ) : (
                <div id="audit-registration-fields" className="mt-6 border-t border-[#e8edf5] pt-6">
                  <div className="rounded-[16px] bg-[#f4f8ff] p-4">
                    <div className="font-black text-[#15213b]">Сохраним расчёт за вами</div>
                    <p className="mt-1 text-sm leading-6 text-[#53617a]">
                      Email нужен только для входа в результат. Оплата, карта и звонок не нужны.
                    </p>
                  </div>

                  <div className="mt-5 grid gap-4">
                    <label className="grid gap-2">
                      <span className="text-sm font-extrabold text-[#15213b]">Email для результата</span>
                      <input required type="email" value={email} onChange={e=>setEmail(e.target.value)} placeholder="name@company.ru" className="h-12 border border-[#dce3ee] px-4" />
                    </label>
                    <label className="grid gap-2">
                      <span className="text-sm font-extrabold text-[#15213b]">Придумайте пароль</span>
                      <input required minLength={6} type="password" value={password} onChange={e=>setPassword(e.target.value)} placeholder="Минимум 6 символов" className="h-12 border border-[#dce3ee] px-4" />
                    </label>
                  </div>

                  <div className="mt-5"><TurnstileBox onToken={setCaptcha} resetKey={captchaKey}/></div>
                  <label className="mt-4 flex gap-3 text-xs leading-5 text-[#667085]">
                    <input type="checkbox" checked={legal} onChange={e=>setLegal(e.target.checked)} className="mt-1"/>
                    <span>Принимаю <Link href="/terms" className="font-bold underline">условия</Link> и <Link href="/privacy" className="font-bold underline">политику обработки данных</Link>.</span>
                  </label>

                  {error&&<div className="mt-4 rounded-[12px] bg-red-50 p-4 text-sm font-bold text-red-700">{error}</div>}
                  <button disabled={busy} className="audit-primary-cta mt-5 w-full rounded-[14px] bg-[#2878ff] px-6 py-4 text-base font-black text-white disabled:opacity-60">
                    {busy?'Создаём ваш аудит…':'Получить мой аудит бесплатно →'}
                  </button>
                  <p className="mt-3 text-center text-xs leading-5 text-[#8490a6]">После подтверждения email вы сразу продолжите аудит. Никаких платежей.</p>
                </div>
              )}
            </form>
          </div>
        </div>
      </section>

      <section className="audit-final-cta px-5 py-14">
        <div className="audit-wide mx-auto flex flex-col items-start justify-between gap-7 md:flex-row md:items-center">
          <div className="max-w-2xl">
            <div className="text-sm font-black uppercase tracking-[.08em] text-[#8eb9ff]">СНАЧАЛА ПРОВЕРЬТЕ</div>
            <h2 className="mt-3 text-3xl font-black text-white sm:text-4xl">Не верьте обещаниям про продвижение. Посмотрите на цифры своего бизнеса.</h2>
          </div>
          <a href="#audit-start" className="shrink-0 rounded-[12px] bg-white px-6 py-4 font-black text-[#1f66d8]">
            Получить аудит за 0 ₽ →
          </a>
        </div>
      </section>

      <footer className="border-t border-[#e8edf5] bg-white px-5 py-7">
        <div className="audit-wide mx-auto flex flex-wrap items-center justify-between gap-4 text-sm text-[#667085]">
          <div>
            <div className="font-black text-[#15213b]">BORIS <span className="text-[#2878ff]">AUDIT</span></div>
            <div className="mt-1 text-xs">Бесплатный аудит рынка, рекламы и продаж</div>
          </div>
          <div className="flex flex-wrap gap-5">
            <Link href="/privacy" className="font-bold hover:text-[#2878ff]">Политика</Link>
            <Link href="/terms" className="font-bold hover:text-[#2878ff]">Условия</Link>
            <Link href="/" className="font-bold hover:text-[#2878ff]">О BORIS</Link>
          </div>
        </div>
      </footer>
    </main>
  );
}

