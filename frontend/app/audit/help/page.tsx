'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

export default function AuditHelpPage(){
  const [contact,setContact]=useState<any>(null);
  useEffect(()=>{fetch('/api/viral/sales-contact').then(r=>r.json()).then(setContact).catch(()=>setContact(null));},[]);
  const configured=Boolean(contact?.configured);
  return <main className="audit-canonical min-h-screen">
    <header className="px-5 py-5"><div className="mx-auto flex max-w-4xl items-center justify-between">
      <Link href="/audit" className="text-xl font-black">BORIS <span className="text-[#2563eb]">AUDIT</span></Link>
      <Link href="/audit" className="text-sm font-bold text-[#667085]">Вернуться к аудиту</Link>
    </div></header>
    <section className="px-5 py-14"><div className="mx-auto max-w-4xl">
      <div className="audit-reference-card p-7 sm:p-10">
        <div className="inline-flex rounded-full bg-[#f2edff] px-4 py-2 text-sm font-black text-[#7f56d9]">Помощь специалиста BORIS</div>
        <h1 className="mt-5 text-4xl font-black tracking-[-.04em] sm:text-5xl">Настроим BORIS вместе</h1>
        <p className="mt-5 max-w-2xl text-lg leading-8 text-[#667085]">Специалист поможет подключить Avito, AI-менеджера, CRM, объявления и аналитику. Контекст вашего аудита уже сохранён в аккаунте — повторять всё заново не нужно.</p>
        <div className="mt-8 grid gap-3 sm:grid-cols-2">
          {['Подключение Avito','AI-менеджер и переписки','CRM и контроль лидов','Объявления и аналитика'].map(x=><div key={x} className="rounded-[24px] bg-[#f7f9fc] p-5 font-bold">{x}</div>)}
        </div>
        {configured ? <div className="mt-8 flex flex-wrap gap-3">
          {contact?.whatsapp_url&&<a href={contact.whatsapp_url} target="_blank" rel="noreferrer" className="rounded-full bg-[#101828] px-6 py-4 font-black text-white">Написать в WhatsApp</a>}
          {contact?.max_url&&<a href={contact.max_url} target="_blank" rel="noreferrer" className="rounded-full border border-[#d8deea] px-6 py-4 font-black">Написать в MAX</a>}
        </div> : <div className="mt-8 rounded-[24px] bg-[#fff8eb] p-5 text-sm font-bold text-[#9a5b00]">Контакты отдела настройки ещё не заданы в конфигурации BORIS. Этот экран уже готов и автоматически покажет WhatsApp/MAX после настройки.</div>}
        {contact?.phone&&<div className="mt-5 text-sm text-[#667085]">Телефон специалиста: <b className="text-[#101828]">{contact.phone}</b></div>}
      </div>
    </div></section>
  </main>;
}
