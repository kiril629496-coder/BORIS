'use client';

import Link from 'next/link';

const breakdown=[
  ['Скорость ответа',17,20],
  ['Клиенты без ответа',1,20],
  ['Из просмотров в обращения',8,15],
  ['Работа с обращениями',6,15],
  ['Выполнение плана',2,10],
  ['Повторная работа с клиентами',5,10],
  ['Рекламная эффективность',7,10],
];

export default function PlanetaAuditDemo(){
  return <main className="audit-canonical min-h-screen overflow-hidden bg-[#f6f8fc]">
    <header style={{position:'relative'}} className="border-b border-[#edf1f7] bg-white/95 px-5 py-4">
      <div className="mx-auto flex max-w-6xl items-center justify-between">
        <Link href="/audit" className="text-xl font-black tracking-[-.03em]">BORIS <span className="text-[#2878ff]">AUDIT</span></Link>
        <Link href="/audit" className="rounded-[10px] border border-[#dfe6f2] bg-white px-4 py-2 text-sm font-black text-[#53617a]">Мой аудит</Link>
      </div>
    </header>

    <section className="relative px-5 pb-10 pt-10 sm:pt-14">
      <div className="pointer-events-none absolute -left-24 top-10 h-72 w-72 rounded-full bg-[#dce9ff] opacity-60 blur-3xl"/>
      <div className="pointer-events-none absolute -right-24 top-24 h-80 w-80 rounded-full bg-[#efe4ff] opacity-60 blur-3xl"/>
      <div className="relative mx-auto max-w-6xl">
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <span className="rounded-[10px] bg-[#edf4ff] px-3 py-2 text-xs font-black uppercase tracking-[.08em] text-[#2878ff]">Планета Мебели</span>
          <span className="rounded-[10px] border border-[#dfe6f2] bg-white px-3 py-2 text-xs font-bold text-[#53617a]">Последние 30 дней</span>
          <span className="rounded-[10px] border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-black text-emerald-700">По реальным обращениям</span>
        </div>

        <div className="grid gap-6 lg:grid-cols-[1.3fr_.7fr] lg:items-stretch">
          <div className="audit-reference-card relative overflow-hidden border-[#d8e5fb] bg-white p-7 sm:p-9">
            <div className="absolute inset-x-0 top-0 h-[4px] bg-gradient-to-r from-[#2878ff] via-[#8c5cf5] to-[#19b984]"/>
            <div className="max-w-3xl">
              <div className="text-xs font-black uppercase tracking-[.12em] text-[#8490a6]">Главный вывод</div>
              <h1 className="mt-4 text-4xl font-black leading-[1.04] tracking-[-.045em] text-[#15213b] sm:text-5xl">Вы отвечаете быстро.<br/><span className="text-[#2878ff]">Но теряете часть клиентов.</span></h1>
              <p className="mt-5 max-w-2xl text-lg leading-8 text-[#53617a]">Большинство обращений получает ответ за несколько минут, но часть клиентов остаётся без ответа. Это сейчас главная точка роста.</p>
            </div>

            <div className="mt-8 grid gap-3 sm:grid-cols-3">
              <div className="rounded-[16px] border border-[#e7edf6] bg-[#f8faff] p-5">
                <div className="text-xs font-bold text-[#8490a6]">Быстрые ответы</div>
                <div className="mt-2 text-3xl font-black text-[#15213b]">10 из 12</div>
                <div className="mt-1 text-sm text-[#53617a]">до 5 минут</div>
              </div>
              <div className="rounded-[16px] border border-[#ffe0ba] bg-[#fff8ed] p-5">
                <div className="text-xs font-bold text-[#a7661f]">Требуют внимания</div>
                <div className="mt-2 text-3xl font-black text-[#b35c00]">7</div>
                <div className="mt-1 text-sm text-[#8f5b2d]">обращений без ответа</div>
              </div>
              <div className="rounded-[16px] border border-[#e7edf6] bg-[#f8faff] p-5">
                <div className="text-xs font-bold text-[#8490a6]">Редкий провал</div>
                <div className="mt-2 text-3xl font-black text-[#15213b]">≈ 23.6 ч</div>
                <div className="mt-1 text-sm text-[#53617a]">самый долгий ответ</div>
              </div>
            </div>
          </div>

          <div className="audit-reference-card relative overflow-hidden p-7 sm:p-8">
            <div className="pointer-events-none absolute -right-16 -top-16 h-44 w-44 rounded-full bg-[#edf4ff]"/>
            <div className="relative">
              <div className="text-xs font-black uppercase tracking-[.1em] text-[#8490a6]">Оценка работы с клиентами</div>
              <div className="mt-7 flex justify-center">
                <div className="grid h-[196px] w-[196px] place-items-center rounded-full p-[13px]" style={{background:'conic-gradient(#2878ff 0 46%, #e7edf7 46% 100%)'}}>
                  <div className="grid h-full w-full place-items-center rounded-full bg-white text-center shadow-[0_8px_35px_rgba(40,120,255,.12)]">
                    <div><div className="text-6xl font-black tracking-[-.06em] text-[#15213b]">46</div><div className="mt-1 text-sm font-bold text-[#8490a6]">из 100</div></div>
                  </div>
                </div>
              </div>
              <div className="mt-6 text-center">
                <div className="text-2xl font-black text-[#15213b]">Есть потенциал</div>
                <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-[#53617a]">Скорость хорошая. Главный резерв — не оставлять обращения без ответа.</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section className="px-5 py-8">
      <div className="mx-auto max-w-6xl">
        <div className="mb-5"><div className="text-xs font-black uppercase tracking-[.1em] text-[#2878ff]">Три главные точки роста</div><h2 className="mt-2 text-3xl font-black tracking-[-.035em] text-[#15213b]">BORIS уже нашёл, с чего начать</h2></div>
        <div className="grid gap-4 lg:grid-cols-3">
          {[
            ['01','7 обращений без ответа','Это самый прямой риск потери клиентов сейчас.','bg-[#fff8ed] border-[#ffe0ba]','text-[#b35c00]'],
            ['02','Один клиент ждал почти сутки','Такие случаи нельзя терять среди хорошей общей скорости ответа.','bg-white border-[#dfe6f2]','text-[#2878ff]'],
            ['03','По рекламе пока мало данных','BORIS не делает вывод, пока выборка слишком маленькая.','bg-white border-[#dfe6f2]','text-[#7f56d9]'],
          ].map(([n,t,d,box,tone])=><div key={n} className={"rounded-[20px] border p-6 shadow-sm "+box}>
            <div className={"text-sm font-black "+tone}>{n}</div>
            <div className="mt-3 text-xl font-black text-[#15213b]">{t}</div>
            <p className="mt-2 text-sm leading-6 text-[#53617a]">{d}</p>
            <div className={"mt-5 text-sm font-black "+tone}>Что делать →</div>
          </div>)}
        </div>
      </div>
    </section>

    <section className="px-5 py-8">
      <div className="mx-auto max-w-6xl">
        <div className="mb-5"><div className="text-xs font-black uppercase tracking-[.1em] text-[#7f56d9]">Как проходит клиент</div><h2 className="mt-2 text-3xl font-black tracking-[-.035em] text-[#15213b]">Где именно возникает потеря</h2></div>
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="audit-reference-card p-6 sm:p-7">
            <div className="text-sm font-black text-[#53617a]">Реклама</div>
            <div className="mt-5 grid grid-cols-[1fr_auto_1fr] items-center gap-3">
              <div className="rounded-[16px] bg-[#f8faff] p-5 text-center"><div className="text-3xl font-black text-[#15213b]">16</div><div className="mt-1 text-xs font-bold text-[#53617a]">просмотров</div></div>
              <div className="text-2xl font-black text-[#c7cfdd]">→</div>
              <div className="rounded-[16px] bg-[#f4f6fa] p-5 text-center"><div className="text-3xl font-black text-[#15213b]">0</div><div className="mt-1 text-xs font-bold text-[#53617a]">обращений</div></div>
            </div>
            <p className="mt-4 text-sm leading-6 text-[#8490a6]">Это отдельная свежая выборка Avito. Она слишком маленькая для уверенного вывода.</p>
          </div>
          <div className="audit-reference-card p-6 sm:p-7">
            <div className="text-sm font-black text-[#53617a]">Обработка обращений</div>
            <div className="mt-5 grid grid-cols-[1fr_auto_1fr_auto_1fr] items-center gap-2">
              <div className="rounded-[14px] bg-[#f8faff] p-4 text-center"><div className="text-2xl font-black text-[#15213b]">14</div><div className="mt-1 text-[11px] font-bold text-[#53617a]">диалогов</div></div>
              <div className="font-black text-[#c7cfdd]">→</div>
              <div className="rounded-[14px] bg-emerald-50 p-4 text-center"><div className="text-2xl font-black text-emerald-700">12</div><div className="mt-1 text-[11px] font-bold text-emerald-700">получили ответ</div></div>
              <div className="font-black text-[#c7cfdd]">→</div>
              <div className="rounded-[14px] bg-[#fff8ed] p-4 text-center"><div className="text-2xl font-black text-[#b35c00]">7</div><div className="mt-1 text-[11px] font-bold text-[#8f5b2d]">требуют внимания</div></div>
            </div>
            <p className="mt-4 text-sm leading-6 text-[#53617a]">Главная потеря сейчас происходит уже после появления обращения — часть клиентов остаётся без внимания.</p>
          </div>
        </div>
      </div>
    </section>

    <section className="px-5 py-6">
      <div className="mx-auto max-w-6xl">
        <div className="mb-5 flex items-end justify-between gap-4">
          <div><div className="text-xs font-black uppercase tracking-[.1em] text-[#2878ff]">Где теряются клиенты</div><h2 className="mt-2 text-3xl font-black tracking-[-.035em] text-[#15213b]">Главная проблема — не скорость, а пропущенные обращения</h2></div>
        </div>

        <div className="grid gap-5 lg:grid-cols-[1.2fr_.8fr]">
          <div className="audit-reference-card p-6 sm:p-7">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div><div className="text-sm font-bold text-[#53617a]">Распределение ответов</div><div className="mt-1 text-2xl font-black text-[#15213b]">Большинство клиентов получают ответ быстро</div></div>
              <div className="rounded-[10px] bg-emerald-50 px-3 py-2 text-xs font-black text-emerald-700">90% быстрее 14.5 мин</div>
            </div>

            <div className="mt-7 space-y-5">
              {[
                ['До 5 минут',10,83,'#19b984'],
                ['15–30 минут',1,8,'#2878ff'],
                ['Больше 2 часов',1,8,'#f59e0b'],
              ].map(([label,count,width,color]:any)=><div key={label}>
                <div className="mb-2 flex items-center justify-between text-sm"><span className="font-bold text-[#53617a]">{label}</span><span className="font-black text-[#15213b]">{count}</span></div>
                <div className="h-3 overflow-hidden rounded-full bg-[#eef2f7]"><div className="h-full rounded-full" style={{width:width+'%',background:color}}/></div>
              </div>)}
            </div>

            <div className="mt-7 rounded-[16px] border border-amber-200 bg-amber-50 p-5">
              <div className="flex items-start gap-3">
                <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-amber-100 text-lg">!</div>
                <div><div className="font-black text-amber-950">Один клиент ждал почти сутки</div><div className="mt-1 text-sm leading-6 text-amber-900">BORIS рекомендует отдельно разобрать этот диалог и понять, почему обращение осталось без реакции.</div></div>
              </div>
            </div>
          </div>

          <div className="audit-reference-card p-6 sm:p-7">
            <div className="text-xs font-black uppercase tracking-[.1em] text-[#f59e0b]">Самая важная задача</div>
            <div className="mt-3 text-5xl font-black tracking-[-.05em] text-[#15213b]">7</div>
            <div className="mt-1 text-xl font-black text-[#15213b]">обращений требуют внимания</div>
            <p className="mt-4 text-sm leading-6 text-[#53617a]">Это клиенты, по которым сейчас есть риск потери. Их стоит проверить раньше остальных улучшений.</p>
            <div className="mt-6 rounded-[16px] bg-[#f8faff] p-5">
              <div className="text-xs font-bold text-[#8490a6]">Что сделать сегодня</div>
              <div className="mt-2 text-lg font-black text-[#15213b]">Разобрать обращения без ответа и назначить ответственных</div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section className="px-5 py-8">
      <div className="mx-auto max-w-6xl">
        <div className="grid gap-5 lg:grid-cols-[.85fr_1.15fr]">
          <div className="audit-reference-card p-6 sm:p-7">
            <div className="text-xs font-black uppercase tracking-[.1em] text-[#7f56d9]">Реклама на Avito</div>
            <h2 className="mt-3 text-2xl font-black text-[#15213b]">Пока рано делать вывод</h2>
            <p className="mt-3 text-sm leading-6 text-[#53617a]">Последних данных пока мало: 16 просмотров и 0 обращений. На такой выборке BORIS не ставит рекламе плохую оценку.</p>
            <div className="mt-6 grid grid-cols-2 gap-3">
              <div className="rounded-[14px] bg-[#f8faff] p-4"><div className="text-3xl font-black">16</div><div className="text-xs font-bold text-[#53617a]">просмотров</div></div>
              <div className="rounded-[14px] bg-[#f8faff] p-4"><div className="text-3xl font-black">0</div><div className="text-xs font-bold text-[#53617a]">обращений</div></div>
            </div>
            <div className="mt-5 rounded-[14px] bg-[#edf4ff] p-4 text-sm font-bold leading-6 text-[#245fae]">Нужно накопить больше данных и повторно проверить рекламу.</div>
          </div>

          <div className="audit-reference-card p-6 sm:p-7">
            <div className="text-xs font-black uppercase tracking-[.1em] text-[#2878ff]">Из чего сложилась оценка</div>
            <div className="mt-5 grid gap-4 sm:grid-cols-2">
              {breakdown.map(([label,score,max])=><div key={label as string} className="rounded-[16px] border border-[#e6ebf3] bg-[#fbfcfe] p-4">
                <div className="flex items-center justify-between gap-3"><div className="text-sm font-black text-[#15213b]">{label}</div><div className="text-sm font-black text-[#2878ff]">{score}/{max}</div></div>
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-[#edf1f7]"><div className="h-full rounded-full bg-gradient-to-r from-[#2878ff] to-[#8c5cf5]" style={{width:((score as number)/(max as number)*100)+'%'}}/></div>
              </div>)}
            </div>
          </div>
        </div>
      </div>
    </section>

    <section className="px-5 py-8">
      <div className="mx-auto max-w-6xl">
        <div className="audit-reference-card overflow-hidden p-0">
          <div className="grid lg:grid-cols-[.8fr_1.2fr]">
            <div className="bg-[#f8f5ff] p-7 sm:p-8">
              <div className="text-xs font-black uppercase tracking-[.1em] text-[#7f56d9]">Анализ рынка · Уфа</div>
              <h2 className="mt-3 text-3xl font-black tracking-[-.035em] text-[#15213b]">Спрос подтверждён</h2>
              <p className="mt-3 text-sm leading-7 text-[#53617a]">По запросу «кухни на заказ» в Уфе BORIS нашёл реальный поисковый спрос. Данные по конкурентной выдаче Avito пока недоступны, поэтому конкурентов и цены не угадываем.</p>
              <div className="mt-5 grid gap-3 sm:grid-cols-2">
                <div className="rounded-[16px] border border-[#e5dcff] bg-white p-4"><div className="text-xs font-bold text-[#8490a6]">Поисковый спрос</div><div className="mt-2 text-3xl font-black text-[#15213b]">552</div><div className="mt-1 text-xs text-[#53617a]">запроса в месяц</div></div>
                <div className="rounded-[16px] border border-[#e5dcff] bg-white p-4"><div className="text-xs font-bold text-[#8490a6]">Оценка рынка</div><div className="mt-2 text-3xl font-black text-[#15213b]">61/100</div><div className="mt-1 text-xs text-[#53617a]">предварительно</div></div>
              </div>
              <div className="mt-4 rounded-[16px] border border-[#e5dcff] bg-white p-4">
                <div className="text-xs font-bold text-[#8490a6]">Что ищут чаще всего</div>
                <div className="mt-3 grid gap-2 text-sm">
                  <div className="flex justify-between gap-4"><span className="font-bold text-[#53617a]">кухни на заказ Уфа</span><b className="text-[#15213b]">350</b></div>
                  <div className="flex justify-between gap-4"><span className="font-bold text-[#53617a]">кухни на заказ недорого</span><b className="text-[#15213b]">85</b></div>
                  <div className="flex justify-between gap-4"><span className="font-bold text-[#53617a]">недорогие кухни на заказ Уфа</span><b className="text-[#15213b]">80</b></div>
                </div>
              </div>
              <div className="mt-4 rounded-[16px] border border-[#d9e6ff] bg-[#edf4ff] p-4">
                <div className="text-xs font-black uppercase tracking-[.08em] text-[#2878ff]">Предварительно по спросу</div>
                <div className="mt-2 text-2xl font-black text-[#15213b]">21–26 объявлений</div>
                <p className="mt-2 text-xs leading-5 text-[#53617a]">Диапазон рассчитан по спросу. После получения конкурентной выдачи Avito он может измениться.</p>
              </div>
            </div>
            <div className="p-7 sm:p-8">
              <div className="text-xs font-black uppercase tracking-[.1em] text-[#2878ff]">План действий</div>
              <h2 className="mt-3 text-3xl font-black tracking-[-.035em] text-[#15213b]">Что сделать сначала</h2>
              <div className="mt-6 grid gap-3">
                {[
                  ['01','Разобрать 7 обращений без ответа','Это самый прямой риск потери клиентов сейчас.'],
                  ['02','Понять причину долгого ответа','Один клиент ждал почти сутки — такой случай не должен повторяться.'],
                  ['03','Повторно проверить рекламу','Вернуться к рекламе, когда накопится достаточно просмотров и обращений.'],
                ].map(([n,t,d])=><div key={n} className="flex gap-4 rounded-[16px] border border-[#e6ebf3] bg-[#fbfcfe] p-5">
                  <div className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[#edf4ff] text-sm font-black text-[#2878ff]">{n}</div>
                  <div><div className="font-black text-[#15213b]">{t}</div><div className="mt-1 text-sm leading-6 text-[#53617a]">{d}</div></div>
                </div>)}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>


    <section className="px-5 py-8">
      <div className="mx-auto max-w-6xl">
        <div className="rounded-[24px] border border-[#f2dcc4] bg-gradient-to-br from-[#fffaf3] to-white p-7 sm:p-8">
          <div className="text-xs font-black uppercase tracking-[.1em] text-[#b35c00]">Если ничего не менять</div>
          <h2 className="mt-3 text-3xl font-black tracking-[-.035em] text-[#15213b]">Проблема может повторяться снова и снова</h2>
          <p className="mt-3 max-w-3xl text-sm leading-7 text-[#53617a]">BORIS не пугает прогнозами по деньгам. Здесь только факты, которые уже есть в данных.</p>
          <div className="mt-6 grid gap-4 md:grid-cols-3">
            <div className="rounded-[18px] border border-[#f3dfca] bg-white p-5">
              <div className="text-xs font-black uppercase tracking-[.08em] text-[#a7661f]">Сейчас</div>
              <div className="mt-3 text-3xl font-black text-[#15213b]">7</div>
              <div className="mt-1 text-sm font-bold text-[#53617a]">обращений уже требуют внимания</div>
            </div>
            <div className="rounded-[18px] border border-[#f3dfca] bg-white p-5">
              <div className="text-xs font-black uppercase tracking-[.08em] text-[#a7661f]">Риск</div>
              <div className="mt-3 text-2xl font-black text-[#15213b]">Почти сутки</div>
              <div className="mt-1 text-sm font-bold text-[#53617a]">один клиент ждал первого ответа</div>
            </div>
            <div className="rounded-[18px] border border-[#e7ebf2] bg-white p-5">
              <div className="text-xs font-black uppercase tracking-[.08em] text-[#8490a6]">Пока неизвестно</div>
              <div className="mt-3 text-xl font-black text-[#15213b]">Насколько эффективна реклама</div>
              <div className="mt-1 text-sm font-bold text-[#53617a]">выборка ещё слишком маленькая</div>
            </div>
          </div>
          <div className="mt-6 rounded-[16px] bg-white/80 p-5 text-sm font-bold leading-7 text-[#705437]">Пока обращения без ответа не контролируются системно, такие случаи могут повторяться даже при хорошей общей скорости команды.</div>
        </div>
      </div>
    </section>

    <section className="px-5 py-8">
      <div className="mx-auto max-w-6xl">
        <div className="mb-5"><div className="text-xs font-black uppercase tracking-[.1em] text-[#2878ff]">Ближайшие 24 часа</div><h2 className="mt-2 text-3xl font-black tracking-[-.035em] text-[#15213b]">Понятный план без лишней теории</h2></div>
        <div className="audit-reference-card p-6 sm:p-8">
          <div className="grid gap-0 lg:grid-cols-4">
            {[
              ['01','Разобрать 7 обращений','Проверить, кому ещё можно ответить прямо сейчас.'],
              ['02','Разобрать долгий ответ','Понять, почему один клиент ждал почти сутки.'],
              ['03','Включить контроль','Не оставлять новые обращения без внимания.'],
              ['04','Вернуться к рекламе','Проверить её повторно после накопления данных.'],
            ].map(([n,t,d],i)=><div key={n} className={"relative p-5 "+(i<3?'lg:border-r lg:border-[#e7ebf2]':'')}>
              <div className="grid h-10 w-10 place-items-center rounded-full bg-[#edf4ff] text-sm font-black text-[#2878ff]">{n}</div>
              <div className="mt-4 font-black text-[#15213b]">{t}</div>
              <div className="mt-2 text-sm leading-6 text-[#53617a]">{d}</div>
            </div>)}
          </div>
        </div>
      </div>
    </section>

    <section className="px-5 py-8">
      <div className="mx-auto max-w-6xl">
        <div className="mb-5"><div className="text-xs font-black uppercase tracking-[.1em] text-[#7f56d9]">Что BORIS может взять на себя</div><h2 className="mt-2 text-3xl font-black tracking-[-.035em] text-[#15213b]">Не просто показать проблему — помочь её контролировать</h2></div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ['Следить за обращениями','Показывать клиентов, которые остались без ответа.','💬'],
            ['Напоминать менеджеру','Выделять обращения, где ответ слишком долго не приходит.','⏱'],
            ['Возвращать старых клиентов','Находить диалоги, к которым стоит вернуться.','↩'],
            ['Проверять рекламу','Сравнивать просмотры, обращения и работу объявлений.','◎'],
          ].map(([t,d,icon])=><div key={t} className="audit-reference-card p-6">
            <div className="grid h-11 w-11 place-items-center rounded-[14px] bg-[#edf4ff] text-xl">{icon}</div>
            <div className="mt-4 text-lg font-black text-[#15213b]">{t}</div>
            <p className="mt-2 text-sm leading-6 text-[#53617a]">{d}</p>
          </div>)}
        </div>
      </div>
    </section>

    <section className="px-5 py-8">
      <div className="mx-auto max-w-6xl">
        <div className="audit-reference-card overflow-hidden p-0">
          <div className="grid lg:grid-cols-2">
            <div className="border-b border-[#e7ebf2] bg-[#fbfcfe] p-7 sm:p-8 lg:border-b-0 lg:border-r">
              <div className="text-xs font-black uppercase tracking-[.1em] text-[#8490a6]">Сейчас</div>
              <h2 className="mt-3 text-3xl font-black tracking-[-.035em] text-[#15213b]">Контроль зависит от внимания команды</h2>
              <div className="mt-6 grid gap-3">
                {[
                  'Обращение может остаться без ответа',
                  'Долгий ответ легко потерять в общей статистике',
                  'Рекламу приходится проверять вручную',
                ].map(x=><div key={x} className="flex items-start gap-3 rounded-[14px] bg-white p-4 text-sm font-bold leading-6 text-[#53617a]"><span className="mt-0.5 text-[#f59e0b]">•</span>{x}</div>)}
              </div>
            </div>
            <div className="bg-gradient-to-br from-[#f2f7ff] to-white p-7 sm:p-8">
              <div className="text-xs font-black uppercase tracking-[.1em] text-[#2878ff]">С BORIS</div>
              <h2 className="mt-3 text-3xl font-black tracking-[-.035em] text-[#15213b]">Проблемы видны сразу</h2>
              <div className="mt-6 grid gap-3">
                {[
                  'Обращения без ответа собраны в одном месте',
                  'Долгие ответы сразу становятся заметны',
                  'Реклама пересчитывается по новым данным',
                ].map(x=><div key={x} className="flex items-start gap-3 rounded-[14px] border border-[#dbe7fb] bg-white p-4 text-sm font-bold leading-6 text-[#35577f]"><span className="mt-0.5 text-[#19b984]">✓</span>{x}</div>)}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section className="px-5 py-8">
      <div className="mx-auto max-w-6xl">
        <div className="rounded-[22px] border border-[#e2e8f0] bg-white p-6 sm:p-7">
          <div className="flex flex-wrap items-start justify-between gap-5">
            <div><div className="text-xs font-black uppercase tracking-[.1em] text-[#8490a6]">На чём основан аудит</div><h2 className="mt-2 text-2xl font-black text-[#15213b]">Только на доступных данных бизнеса</h2></div>
            <div className="flex flex-wrap gap-2">
              {['Реальные обращения','Данные Avito','Данные продаж — когда доступны','Рынок — когда доступны данные'].map(x=><span key={x} className="rounded-[10px] border border-[#dfe6f2] bg-[#f8faff] px-3 py-2 text-sm font-bold text-[#53617a]">{x}</span>)}
            </div>
          </div>
        </div>
      </div>
    </section>

    <section className="px-5 pb-20 pt-8">
      <div className="mx-auto max-w-6xl">
        <div className="relative overflow-hidden rounded-[24px] border border-[#cfe0ff] bg-white p-7 shadow-[0_18px_60px_rgba(40,120,255,.10)] sm:p-9">
          <div className="pointer-events-none absolute -right-10 -top-20 h-64 w-64 rounded-full bg-[#e8f1ff] blur-2xl"/>
          <div className="pointer-events-none absolute -bottom-24 left-1/3 h-56 w-56 rounded-full bg-[#efe7ff] blur-2xl"/>
          <div className="relative grid gap-7 lg:grid-cols-[1.15fr_.85fr] lg:items-center">
            <div>
              <div className="text-xs font-black uppercase tracking-[.12em] text-[#2878ff]">Следующий шаг</div>
              <h2 className="mt-3 text-4xl font-black tracking-[-.04em] text-[#15213b]">Попробуйте BORIS бесплатно 4 дня</h2>
              <p className="mt-4 max-w-2xl text-base leading-7 text-[#53617a]">Начнём с найденных точек: обращений без ответа, скорости реакции менеджеров и контроля рекламы.</p>
              <div className="mt-6 flex flex-wrap gap-2">
                {['Помощник по ответам клиентам','Контроль обращений','Аудит рекламы'].map(x=><span key={x} className="rounded-[10px] border border-[#dfe6f2] bg-white px-3 py-2 text-sm font-bold text-[#53617a]">{x}</span>)}
              </div>
            </div>
            <div className="rounded-[20px] border border-[#dfe6f2] bg-white/95 p-6 shadow-sm">
              <div className="text-xs font-black uppercase tracking-[.1em] text-[#7f56d9]">Помощь с подключением</div>
              <div className="mt-4 flex items-center gap-4">
                <img src="/boris-preview/kirill.webp" alt="Кирилл — создатель BORIS AI" className="h-16 w-16 shrink-0 rounded-full object-cover object-top shadow-sm" />
                <div><div className="text-2xl font-black text-[#15213b]">Кирилл</div><div className="mt-1 text-sm font-bold text-[#53617a]">Помогу запустить BORIS для вашего бизнеса</div></div>
              </div>
              <p className="mt-4 text-sm leading-6 text-[#53617a]">Помогу подключить тест и пройти первый запуск по найденным проблемам.</p>
              <div className="mt-5 flex flex-wrap gap-2">
                <a href="https://wa.me/79819673787?text=%D0%97%D0%B4%D1%80%D0%B0%D0%B2%D1%81%D1%82%D0%B2%D1%83%D0%B9%D1%82%D0%B5!%20%D0%AF%20%D0%BF%D1%80%D0%BE%D1%88%D1%91%D0%BB%20%D0%B0%D1%83%D0%B4%D0%B8%D1%82%20BORIS%20%D0%B8%20%D1%85%D0%BE%D1%87%D1%83%20%D0%BF%D0%BE%D0%B4%D0%BA%D0%BB%D1%8E%D1%87%D0%B8%D1%82%D1%8C%20%D0%B1%D0%B5%D1%81%D0%BF%D0%BB%D0%B0%D1%82%D0%BD%D1%8B%D0%B9%20%D1%82%D0%B5%D1%81%D1%82." target="_blank" rel="noopener noreferrer" className="rounded-[10px] bg-[#2878ff] px-4 py-3 text-sm font-black text-white">WhatsApp</a>
                <a href="https://max.ru/u/f9LHodD0cOLLNHY2Rea9pC_FXcSh-1LgXiDxZZTAfCr-XQM3rBeNpfMKPb0" target="_blank" rel="noopener noreferrer" className="rounded-[10px] border border-[#dfe6f2] bg-white px-4 py-3 text-sm font-black text-[#15213b]">MAX</a>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-8 grid gap-3 text-center sm:grid-cols-3">
          <a href="https://boris-ai.pro/go/boris" target="_blank" rel="noopener noreferrer" className="rounded-[12px] bg-[#2878ff] px-6 py-4 font-black text-white shadow-[0_10px_30px_rgba(40,120,255,.2)]">Запустить тест BORIS</a>
          <a href="https://boris-ai.pro/" target="_blank" rel="noopener noreferrer" className="rounded-[12px] border border-[#dfe6f2] bg-white px-6 py-4 font-black text-[#15213b]">Основной сайт BORIS</a>
          <a href="https://systems-ai-oko.ru/" target="_blank" rel="noopener noreferrer" className="rounded-[12px] border border-[#dfe6f2] bg-white px-6 py-4 font-black text-[#53617a]">О разработчике</a>
        </div>
      </div>
    </section>
  </main>;
}
