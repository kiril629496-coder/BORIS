"use client";

import React from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAccounts } from "../lib/AccountContext";
import { MENU } from "../ui/menu";

type Action = { label: string; href: string; why?: string };
type PersonaKey = "business_man" | "business_woman" | "robot" | "friendly" | "spark";
type Persona = { key: PersonaKey; label: string; short: string; emoji: string; accent: string; accent2: string };

const PERSONAS: Persona[] = [
  { key: "business_man", label: "Деловой мужчина", short: "BORIS", emoji: "👨🏻‍💼", accent: "#2563eb", accent2: "#4f46e5" },
  { key: "business_woman", label: "Деловая женщина", short: "BORIS", emoji: "👩🏻‍💼", accent: "#7c3aed", accent2: "#db2777" },
  { key: "robot", label: "Борис-робот", short: "BORIS", emoji: "🤖", accent: "#0ea5e9", accent2: "#2563eb" },
  { key: "friendly", label: "Добрый помощник", short: "BORIS", emoji: "🙂", accent: "#059669", accent2: "#0ea5e9" },
  { key: "spark", label: "Энергичный", short: "BORIS", emoji: "😎", accent: "#f59e0b", accent2: "#ef4444" },
];

type DailyReport = {
  status?: string;
  message?: string;
  account_name?: string;
  headline?: string;
  period_label?: string;
  text?: string;
  totals?: { new_dialogs?: number; new_msgs?: number; unread?: number; unanswered?: number; max_wait_h?: number };
  accounts?: Array<{ account_id?: string; account_name?: string; new_dialogs?: number; new_msgs?: number; unread?: number; unanswered?: number }>;
  generated_at?: string;
  metric_notes?: { new_dialogs?: string; new_msgs?: string; unread?: string; unanswered?: string };
};

function href(key: string): string {
  for (const group of MENU) for (const item of group.items) if (item.key === key) return item.href;
  return "/dashboard/command-center";
}

const ROUTES: Array<[RegExp, string, Action[]]> = [
  [/^\/dashboard\/analytics/, "Могу помочь быстро понять цифры на этом экране.", [
    { label: "Показать аналитику", href: href("stats") },
    { label: "Открыть объявления", href: href("listings") },
    { label: "Подготовить отчёт", href: "/dashboard/client-success" },
  ]],
  [/^\/messages/, "Могу помочь с обращениями клиентов.", [
    { label: "Открыть сообщения", href: href("messages") },
    { label: "Открыть CRM", href: "/dashboard/crm" },
    { label: "Спросить BORIS", href: href("command") },
  ]],
  [/^\/dashboard\/crm/, "Могу помочь найти сделки, которым нужен следующий шаг.", [
    { label: "Открыть CRM", href: "/dashboard/crm" },
    { label: "Открыть задачи", href: "/dashboard?tab=tasks" },
    { label: "Спросить BORIS", href: "/command" },
  ]],
  [/^\/dashboard\/campaign/, "Могу помочь с проверкой кампании.", [
    { label: "Открыть кампании", href: "/dashboard/campaign" },
    { label: "Открыть объявления", href: "/listings" },
    { label: "Спросить BORIS", href: "/command" },
  ]],
  [/^\/dashboard\/billing/, "Могу подсказать по тарифу и оплате.", [
    { label: "Тарифы и финансы", href: "/dashboard/billing" },
    { label: "Спросить BORIS", href: "/command" },
  ]],
  [/^\/dashboard\/(?:home|command-center)/, "Я рядом. Могу быстро сориентировать по кабинету.", [
    { label: "Что требует внимания", href: "/dashboard/home" },
    { label: "Как идут продажи", href: "/dashboard/analytics" },
    { label: "Подготовить отчёт", href: "/dashboard/client-success" },
    { label: "Спросить BORIS", href: "/command" },
  ]],
];

function routeContext(path: string): { intro: string; actions: Action[] } {
  for (const [rx, intro, actions] of ROUTES) if (rx.test(path)) return { intro, actions };
  return { intro: "Я рядом. Подсказать, куда лучше зайти?", actions: [
    { label: "На главную", href: "/dashboard/home" },
    { label: "Что требует внимания", href: "/dashboard/home" },
    { label: "Спросить BORIS", href: "/command" },
  ] };
}

function stripHtml(value: string): string {
  return (value || "").replace(/<[^>]+>/g, "").replace(/\n{3,}/g, "\n\n").trim();
}

export default function ContextualBoris() {
  const pathname = usePathname() || "";
  const router = useRouter();
  const { accounts, selectedAccountId, selectedAccount, mode } = useAccounts();
  const [open, setOpen] = React.useState(false);
  const [payment, setPayment] = React.useState<any>(null);
  const [proactive, setProactive] = React.useState(false);
  const [eyeOffset, setEyeOffset] = React.useState({ x: 0, y: 0 });
  const [report, setReport] = React.useState<DailyReport | null>(null);
  const [reportScope, setReportScope] = React.useState<"current" | "all">("current");
  const [reportLoading, setReportLoading] = React.useState(false);
  const [question, setQuestion] = React.useState("");
  const [personaKey, setPersonaKey] = React.useState<PersonaKey>("business_man");
  const [showPersonaPicker, setShowPersonaPicker] = React.useState(false);
  const [supportOpen, setSupportOpen] = React.useState(false);
  const [supportText, setSupportText] = React.useState("");
  const [supportBusy, setSupportBusy] = React.useState(false);
  const [supportResult, setSupportResult] = React.useState("");
  const [supportTickets, setSupportTickets] = React.useState<any[]>([]);
  const [supportHistoryOpen, setSupportHistoryOpen] = React.useState(false);
  const [auditContext, setAuditContext] = React.useState<any>(null);
  const accountId = selectedAccountId || "";
  const hasAccounts = accounts.length > 0;
  const ctx = routeContext(pathname);
  const persona = PERSONAS.find(p => p.key === personaKey) || PERSONAS[0];
  const routePose = React.useMemo(() => {
    const poses = ["👋", "📊", "💬", "🎯", "✨", "🧭", "💡", "🚀"];
    let hash = 0;
    for (let i = 0; i < pathname.length; i += 1) hash = ((hash << 5) - hash + pathname.charCodeAt(i)) | 0;
    return poses[Math.abs(hash) % poses.length];
  }, [pathname]);

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = localStorage.getItem("boris_assistant_persona") as PersonaKey | null;
    if (saved && PERSONAS.some(p => p.key === saved)) setPersonaKey(saved);
  }, []);

  const choosePersona = React.useCallback((key: PersonaKey) => {
    setPersonaKey(key);
    setShowPersonaPicker(false);
    try { localStorage.setItem("boris_assistant_persona", key); } catch {}
  }, []);

  React.useEffect(() => {
    setOpen(false);
    setPayment(null);
    setReport(null);
    if (!accountId) return;
    const token = localStorage.getItem("boris_token") || "";
    fetch(`/api/payments/status?account_id=${encodeURIComponent(accountId)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {}, cache: "no-store",
    }).then(r => r.json()).then(d => setPayment(d?.payment || null)).catch(() => {});
    fetch("/api/viral/gateway/context", {
      headers: token ? { Authorization: `Bearer ${token}` } : {}, cache: "no-store",
    }).then(r => r.ok ? r.json() : null).then(d => setAuditContext(d?.context || null)).catch(() => setAuditContext(null));
  }, [accountId, pathname]);

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const move = (event: MouseEvent) => {
      const cx = window.innerWidth - 48;
      const cy = window.innerHeight - 48;
      const dx = event.clientX - cx;
      const dy = event.clientY - cy;
      const length = Math.hypot(dx, dy) || 1;
      const strength = Math.min(3.2, length / 120);
      setEyeOffset({ x: (dx / length) * strength, y: (dy / length) * strength });
    };
    window.addEventListener("mousemove", move, { passive: true });
    return () => window.removeEventListener("mousemove", move);
  }, []);

  // Proactive helper: new self-registered users get guided setup quickly; existing users after inactivity.
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    let timer: number | undefined;
    const arm = () => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        if (open || document.querySelector('[role="dialog"], textarea:focus, input:focus, select:focus')) return;
        const dismissed = localStorage.getItem("boris_proactive_dismissed") === "1";
        if (dismissed && hasAccounts) return;
        const snoozeUntil = Number(sessionStorage.getItem("boris_proactive_snooze_until") || 0);
        if (Date.now() < snoozeUntil) return;
        setProactive(true);
      }, hasAccounts ? 22000 : 4500);
    };
    const activity = () => { if (!proactive) arm(); };
    const events: Array<keyof WindowEventMap> = ["mousemove", "keydown", "scroll", "touchstart", "pointerdown"];
    events.forEach(name => window.addEventListener(name, activity, { passive: true }));
    arm();
    return () => {
      if (timer) window.clearTimeout(timer);
      events.forEach(name => window.removeEventListener(name, activity));
    };
  }, [hasAccounts, pathname, open, proactive]);

  const loadTodayReport = React.useCallback(async (scope: "current" | "all") => {
    if (!hasAccounts || (scope === "current" && !accountId)) return;
    setOpen(true); setProactive(false); setReportLoading(true); setReport(null); setReportScope(scope);
    try {
      const token = localStorage.getItem("boris_token") || "";
      const accountParam = scope === "current" ? `&account_id=${encodeURIComponent(accountId)}` : "";
      const url = `/api/inbox/daily_report/preview?period_days=1&report_type=messages${accountParam}`;
      let data: any = null;
      let lastError = "";
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          const ctrl = new AbortController();
          const timeout = window.setTimeout(() => ctrl.abort(), 12000);
          const res = await fetch(url, {
            method: "GET",
            credentials: "include",
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            cache: "no-store",
            signal: ctrl.signal,
          });
          window.clearTimeout(timeout);
          const raw = await res.text();
          try { data = raw ? JSON.parse(raw) : null; } catch { data = null; }
          if (res.ok && data) break;
          lastError = String(data?.detail || data?.message || `HTTP ${res.status}`);
        } catch (e) {
          lastError = e instanceof Error ? e.message : "network error";
        }
      }
      if (!data || data?.status !== "ok") {
        throw new Error(lastError || data?.message || "report unavailable");
      }
      setReport(data);
    } catch (e) {
      const reason = e instanceof Error ? e.message : "неизвестная ошибка";
      setReport({ status: "error", message: `Не удалось получить сегодняшнюю сводку: ${reason}` });
    } finally {
      setReportLoading(false);
    }
  }, [accountId, hasAccounts]);

  const loadSupportTickets = React.useCallback(async () => {
    try {
      const token = localStorage.getItem("boris_token") || "";
      const qs = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
      const res = await fetch(`/api/support/my${qs}`, { headers: token ? { Authorization: `Bearer ${token}` } : {}, cache: "no-store" });
      const data = await res.json().catch(() => null);
      setSupportTickets(Array.isArray(data?.["обращения"]) ? data["обращения"] : []);
    } catch { setSupportTickets([]); }
  }, [accountId]);

  const sendSupport = React.useCallback(async () => {
    const text = supportText.trim();
    if (!text || supportBusy) return;
    setSupportBusy(true); setSupportResult("");
    try {
      const res = await fetch("/api/support/message", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, account_id: accountId, name: selectedAccount?.name || "Клиент BORIS" }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok || data?.status !== "ok") throw new Error(data?.message || data?.detail || "Не удалось отправить обращение");
      setSupportResult(data?.["сообщение"] || `Обращение №${data?.["номер"] || ""} принято.`);
      setSupportText("");
      await loadSupportTickets();
    } catch (e) { setSupportResult(e instanceof Error ? e.message : "Не удалось отправить обращение"); }
    finally { setSupportBusy(false); }
  }, [supportText, supportBusy, accountId, selectedAccount?.name, loadSupportTickets]);

  const handleQuestion = React.useCallback(() => {
    const q = question.trim().toLowerCase();
    if (!q) return;
    if (q.includes("отчет") || q.includes("отчёт") || q.includes("сводк")) {
      loadTodayReport(q.includes("все") || q.includes("всем") || q.includes("всех") ? "all" : "current");
      return;
    }
    const routes: Array<[string[], string]> = [
      [["сообщ", "диалог", "чат"], "/messages"],
      [["сдел", "crm", "срм"], "/dashboard/crm"],
      [["аналит", "статист", "результ"], "/dashboard/analytics"],
      [["объяв", "кампан", "авито"], "/dashboard/campaign"],
      [["тариф", "оплат", "счет", "счёт"], "/dashboard/billing"],
      [["маркет"], "/dashboard/marketing"],
    ];
    for (const [words, route] of routes) {
      if (words.some(word => q.includes(word))) { router.push(route); setOpen(false); return; }
    }
    router.push("/command"); setOpen(false);
  }, [question, loadTodayReport, router]);

  const days = payment?.has_payment ? Number(payment.days_left) : null;
  const renewal = mode === "single" && days !== null && days <= 10;
  const renewalText = days === null ? "" : days < 0 ? "Требуется продление BORIS" : days === 0 ? "Сегодня последний день текущего периода BORIS" : `До продления BORIS ${days} дн.`;
  const shownAccount = !hasAccounts ? "Новый кабинет · настройка BORIS" : mode === "all" ? `Все аккаунты (${accounts.length})` : (selectedAccount?.name || "Текущий аккаунт");
  const guidedStep = !hasAccounts ? 1 : !payment?.has_payment ? 2 : 3;

  return <>
    {(open || proactive) && <div role="dialog" aria-label="Помощник BORIS" className="boris-contextual-panel" style={{position:"fixed",right:20,bottom:92,width:400,maxWidth:"calc(100vw - 28px)",maxHeight:"min(720px,calc(100vh - 118px))",overflow:"auto",background:"linear-gradient(180deg,rgba(255,255,255,.98),rgba(248,251,255,.97))",backdropFilter:"blur(18px)",WebkitBackdropFilter:"blur(18px)",border:`1px solid ${persona.accent}33`,borderRadius:22,boxShadow:`0 28px 80px rgba(15,23,42,.22), 0 10px 30px ${persona.accent}1f`,padding:0,zIndex:1050}}>
      <div style={{position:"relative",overflow:"hidden",padding:"17px 17px 14px",background:`radial-gradient(circle at 85% 0,${persona.accent}22,transparent 38%),linear-gradient(135deg,#ffffff 0%,${persona.accent}0d 100%)`,borderBottom:"1px solid rgba(148,163,184,.18)"}}>
        <div style={{position:"absolute",width:120,height:120,borderRadius:"50%",right:-45,top:-60,background:`radial-gradient(circle,${persona.accent2}22,transparent 68%)`,pointerEvents:"none"}} />
        <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",position:"relative"}}>
          <div style={{display:"flex",gap:11,alignItems:"center",minWidth:0}}>
            <button onClick={()=>setShowPersonaPicker(v=>!v)} title="Выбрать образ помощника" style={{width:52,height:52,flex:"0 0 52px",border:`1px solid ${persona.accent}33`,borderRadius:16,background:`linear-gradient(145deg,#fff,${persona.accent}15)`,boxShadow:`0 8px 22px ${persona.accent}20`,fontSize:30,cursor:"pointer",display:"grid",placeItems:"center"}}>{persona.emoji}</button>
            <div style={{minWidth:0}}><div style={{display:"flex",alignItems:"center",gap:7}}><b style={{fontSize:17,letterSpacing:"-.01em"}}>{persona.short}</b><span style={{display:"inline-flex",alignItems:"center",gap:4,fontSize:10,fontWeight:800,color:"#047857",background:"#ecfdf5",border:"1px solid #a7f3d0",borderRadius:999,padding:"3px 7px"}}><span style={{width:6,height:6,borderRadius:"50%",background:"#10b981",boxShadow:"0 0 0 3px #d1fae5"}}/>онлайн</span></div><div style={{fontSize:12,color:"var(--boris-text-3,#667085)",marginTop:3,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{shownAccount}</div></div>
          </div>
          <button aria-label="Закрыть" onClick={()=>{if(proactive && !open){try{localStorage.setItem("boris_proactive_dismissed","1")}catch{}}setOpen(false);setProactive(false);setShowPersonaPicker(false)}} style={{width:34,height:34,border:"1px solid rgba(148,163,184,.22)",borderRadius:10,background:"rgba(255,255,255,.72)",fontSize:20,cursor:"pointer",display:"grid",placeItems:"center",color:"#475569"}}>×</button>
        </div>
        {showPersonaPicker && <div style={{display:"grid",gridTemplateColumns:"repeat(5,1fr)",gap:7,marginTop:13,padding:9,border:"1px solid rgba(148,163,184,.22)",borderRadius:14,background:"rgba(255,255,255,.78)",boxShadow:"0 8px 24px rgba(15,23,42,.08)"}}>{PERSONAS.map(p=><button key={p.key} onClick={()=>choosePersona(p.key)} title={p.label} style={{minWidth:0,padding:"7px 3px",border:p.key===persona.key?`2px solid ${p.accent}`:"1px solid #e2e8f0",borderRadius:11,background:p.key===persona.key?`${p.accent}0f`:"#fff",cursor:"pointer"}}><div style={{fontSize:25,lineHeight:1}}>{p.emoji}</div><div style={{fontSize:9,fontWeight:750,color:"#475569",marginTop:4,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{p.label.replace("Деловой ","")}</div></button>)}</div>}
      </div>
      <div style={{padding:"14px 16px 16px"}}>
      {proactive && !open ? <div style={{fontSize:16,lineHeight:1.48,marginBottom:13,fontWeight:720,color:"#172033"}}>{auditContext?.score ? `Я уже видел ваш аудит: BORIS Score ${auditContext.score}/100. Могу помочь разобрать найденные проблемы и показать, где это исправить.` : !hasAccounts ? "Добро пожаловать! Я проведу вас по настройке BORIS шаг за шагом — начнём с подключения Avito." : "Я рядом ✨ Хотите, быстро покажу, что сегодня происходит, или подскажу, куда лучше зайти?"}</div> : <div style={{fontSize:14,lineHeight:1.45,marginBottom:11,color:"#344054"}}>{auditContext?.score ? `Ваш последний аудит уже в контексте: Score ${auditContext.score}/100 · ${auditContext.connected ? "на реальных данных BORIS" : "предварительный"}.` : !hasAccounts ? "Помогу настроить кабинет с нуля. Выполняйте шаги по порядку — я буду подсказывать следующий." : ctx.intro}</div>}

      {auditContext?.score && <div style={{marginBottom:12,padding:12,border:"1px solid #dbe7ff",borderRadius:15,background:"linear-gradient(135deg,#eef4ff,#fff)"}}>
        <div style={{fontSize:11,fontWeight:850,color:"#2563eb",letterSpacing:".06em"}}>ВАШ АУДИТ УЖЕ ЗДЕСЬ</div>
        <div style={{marginTop:5,fontSize:17,fontWeight:850,color:"#172033"}}>BORIS Score {auditContext.score}/100</div>
        <div style={{marginTop:4,fontSize:12.5,lineHeight:1.45,color:"#667085"}}>{(auditContext.main_problems||[]).length ? "Главные точки: "+auditContext.main_problems.join(" · ") : "Контекст аудита сохранён и не потеряется при переходах по BORIS."}</div>
        <div style={{display:"flex",flexWrap:"wrap",gap:7,marginTop:9}}>
          <button onClick={()=>router.push("/audit")} style={{minHeight:40,border:0,borderRadius:999,padding:"0 14px",background:"#2563eb",color:"#fff",fontWeight:800,cursor:"pointer"}}>Вернуться к моему аудиту</button>
          <button onClick={()=>router.push("/audit/help")} style={{minHeight:40,border:"1px solid #d8deea",borderRadius:999,padding:"0 14px",background:"#fff",color:"#172033",fontWeight:800,cursor:"pointer"}}>Специалист · WhatsApp / MAX</button>
        </div>
      </div>}

      <div style={{marginBottom:12,padding:12,border:`1px solid ${persona.accent}26`,borderRadius:15,background:`linear-gradient(135deg,${persona.accent}0d,#fff)`}}>
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:8,marginBottom:9}}><b style={{fontSize:14}}>Настроим BORIS вместе</b><span style={{fontSize:10.5,fontWeight:800,color:persona.accent}}>шаг {guidedStep} из 5</span></div>
        <div style={{display:"grid",gap:6}}>
          {[
            [1,"Подключить аккаунт Avito","/dashboard/connect-avito"],
            [2,"Выбрать тариф и активировать работу","/dashboard/billing"],
            [3,"Задать бизнес-цель и KPI","/dashboard/command-center"],
            [4,"Создать и проверить объявления","/dashboard/campaign"],
            [5,"Настроить сообщения и ИИ-менеджера","/messages"],
          ].map(([n,label,route])=>{const num=Number(n);const done=num<guidedStep;const current=num===guidedStep;return <button key={String(n)} onClick={()=>router.push(String(route))} style={{display:"grid",gridTemplateColumns:"28px 1fr auto",alignItems:"center",gap:8,minHeight:42,padding:"6px 8px",border:`1px solid ${current?persona.accent+"55":"#e5eaf2"}`,borderRadius:10,background:current?`${persona.accent}0c`:"#fff",cursor:"pointer",textAlign:"left",color:"#24324a"}}><span style={{width:26,height:26,display:"grid",placeItems:"center",borderRadius:8,background:done?"#ecfdf5":current?persona.accent:"#f3f6fa",color:done?"#059669":current?"#fff":"#718096",fontSize:11,fontWeight:900}}>{done?"✓":n}</span><span style={{fontSize:12.5,fontWeight:700}}>{label}</span><span style={{fontSize:15,color:persona.accent}}>→</span></button>})}
        </div>
        {!hasAccounts && <div style={{fontSize:11.5,lineHeight:1.4,color:"#667085",marginTop:9}}>Начните с подключения Avito. После каждого шага BORIS автоматически подскажет следующий — не нужно разбираться в кабинете самостоятельно.</div>}
      </div>

      {renewal && <div style={{marginTop:10,marginBottom:10,padding:11,borderRadius:10,background:"var(--boris-bg,#f6f8fb)"}}>
        <b>{renewalText}</b>
        {payment?.paid_until && <div style={{fontSize:13,marginTop:4}}>Действует до: {payment.paid_until}</div>}
        <button onClick={()=>router.push("/dashboard/billing")} style={{marginTop:8,minHeight:40,border:0,borderRadius:9,padding:"0 14px",background:"var(--boris-blue,#2563eb)",color:"#fff",fontWeight:600,cursor:"pointer"}}>Посмотреть тариф</button>
      </div>}

      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:7,marginBottom:9}}>
        <button onClick={()=>loadTodayReport("current")} disabled={!accountId||reportLoading} style={{minHeight:48,textAlign:"left",border:`1px solid ${persona.accent}33`,borderRadius:13,background:`linear-gradient(135deg,${persona.accent}12,#fff)`,padding:"8px 11px",cursor:"pointer",fontWeight:760,color:persona.accent,boxShadow:`0 5px 14px ${persona.accent}10`}}>Сегодня · этот аккаунт</button>
        <button onClick={()=>loadTodayReport("all")} disabled={reportLoading} style={{minHeight:48,textAlign:"left",border:`1px solid ${persona.accent2}33`,borderRadius:13,background:`linear-gradient(135deg,${persona.accent2}12,#fff)`,padding:"8px 11px",cursor:"pointer",fontWeight:760,color:persona.accent2,boxShadow:`0 5px 14px ${persona.accent2}10`}}>Сегодня · все аккаунты</button>
      </div>

      {reportLoading && <div style={{padding:12,borderRadius:10,background:"var(--boris-bg,#f8fafc)",fontSize:13}}>Собираю сегодняшнюю сводку…</div>}
      {report?.status === "error" && <div style={{padding:12,borderRadius:10,background:"#fff7ed",fontSize:13}}>{report.message || "Не удалось подготовить отчёт"}</div>}
      {report?.status === "ok" && <div style={{padding:13,borderRadius:15,background:"linear-gradient(180deg,#fff,#f8fbff)",border:`1px solid ${persona.accent}22`,boxShadow:"0 8px 22px rgba(15,23,42,.06)",marginBottom:11}}>
        <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center"}}><b style={{fontSize:14}}>Сегодня · {reportScope === "all" ? "все аккаунты" : (report.account_name || "аккаунт")}</b><span style={{fontSize:11,color:"#667085"}}>живые данные</span></div>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6,marginTop:9}}>
          {[
            ["Новых диалогов",report.totals?.new_dialogs,report.metric_notes?.new_dialogs],
            ["Новых сообщений",report.totals?.new_msgs,report.metric_notes?.new_msgs],
            ["Непрочитано",report.totals?.unread,report.metric_notes?.unread],
            ["Без ответа",report.totals?.unanswered,report.metric_notes?.unanswered],
          ].map(([label,value,note])=><div key={String(label)} title={String(note||"")} style={{padding:8,borderRadius:9,background:"#fff",border:"1px solid #eaecf0"}}><div style={{fontSize:11,color:"#667085"}}>{label}</div><b style={{fontSize:18}}>{String(value??0)}</b></div>)}
        </div>
        <details style={{marginTop:9}}><summary style={{cursor:"pointer",fontSize:12,fontWeight:700}}>Показать подробности</summary><div style={{whiteSpace:"pre-wrap",fontSize:12,lineHeight:1.45,marginTop:8,maxHeight:230,overflow:"auto"}}>{stripHtml(report.text || "Нет данных")}</div></details>
        <div style={{fontSize:10.5,color:"#667085",marginTop:8}}>Данные обновлены {report.generated_at ? new Date(report.generated_at).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"}) : "сейчас"}. Системные события Avito не считаются новыми диалогами.</div>
        <div style={{display:"flex",gap:7,marginTop:9,flexWrap:"wrap"}}><button onClick={()=>router.push("/messages")} style={{minHeight:38,border:0,borderRadius:9,padding:"0 11px",background:"var(--boris-blue,#2563eb)",color:"#fff",fontWeight:700,cursor:"pointer"}}>Открыть сообщения</button><button onClick={()=>router.push("/dashboard/client-success")} style={{minHeight:38,border:"1px solid #d0d5dd",borderRadius:9,padding:"0 11px",background:"#fff",fontWeight:700,cursor:"pointer"}}>Полный отчёт</button></div>
      </div>}

      <div style={{display:"grid",gap:7}}>{ctx.actions.slice(0,4).map(a=><button key={a.label} onClick={()=>router.push(a.href)} style={{minHeight:43,textAlign:"left",border:"1px solid #e2e8f0",borderRadius:11,background:"rgba(255,255,255,.82)",padding:"0 12px",cursor:"pointer",fontWeight:680,color:"#1e293b",boxShadow:"0 2px 7px rgba(15,23,42,.025)"}}>{a.label}</button>)}</div>

      <div style={{marginTop:10,border:"1px solid #e2e8f0",borderRadius:13,background:"rgba(255,255,255,.84)",padding:10}}>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:7}}>
          <button onClick={()=>{setSupportOpen(v=>!v);setSupportResult("")}} style={{minHeight:42,border:`1px solid ${persona.accent}33`,borderRadius:10,background:`${persona.accent}0c`,color:persona.accent,fontWeight:700,cursor:"pointer"}}>Задать вопрос поддержке</button>
          <button onClick={async()=>{const next=!supportHistoryOpen;setSupportHistoryOpen(next);if(next)await loadSupportTickets()}} style={{minHeight:42,border:"1px solid #d0d5dd",borderRadius:10,background:"#fff",color:"#344054",fontWeight:700,cursor:"pointer"}}>Мои обращения</button>
        </div>
        {supportOpen && <div style={{marginTop:9}}>
          <textarea value={supportText} onChange={e=>setSupportText(e.target.value)} placeholder="Опишите вопрос или проблему. Аккаунт будет приложен автоматически." style={{width:"100%",minHeight:86,resize:"vertical",boxSizing:"border-box",border:"1px solid #d0d5dd",borderRadius:10,padding:10,fontSize:13,lineHeight:1.45}} />
          <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center",marginTop:7}}><span style={{fontSize:11,color:"#667085"}}>Поддержка получит аккаунт: {selectedAccount?.name || accountId || "текущий"}</span><button onClick={sendSupport} disabled={supportBusy||!supportText.trim()} style={{minHeight:38,border:0,borderRadius:9,padding:"0 12px",background:"var(--boris-blue,#2563eb)",color:"#fff",fontWeight:700,cursor:"pointer"}}>{supportBusy?"Отправляю…":"Отправить"}</button></div>
          {supportResult && <div style={{marginTop:8,padding:9,borderRadius:9,background:supportResult.includes("принято")?"#ecfdf5":"#fff7ed",fontSize:12,lineHeight:1.4}}>{supportResult}</div>}
        </div>}
        {supportHistoryOpen && <div style={{marginTop:9,display:"grid",gap:6,maxHeight:180,overflow:"auto"}}>{supportTickets.length?supportTickets.slice(0,6).map((t:any)=><div key={t["номер"]} style={{padding:8,border:"1px solid #eaecf0",borderRadius:9,background:"#fff",fontSize:12}}><div style={{display:"flex",justifyContent:"space-between",gap:8}}><b>№{t["номер"]}</b><span style={{color:t["статус"]==="решено"?"#047857":"#b45309"}}>{t["статус"]}</span></div><div style={{color:"#475569",marginTop:3,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{t["текст"]}</div>{t["ответы"]?.length?<div style={{marginTop:4,color:"#2563eb"}}>Есть ответ поддержки</div>:null}</div>):<div style={{fontSize:12,color:"#667085",padding:6}}>Обращений пока нет.</div>}</div>}
      </div>

      <div style={{display:"flex",gap:7,marginTop:10}}><input value={question} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")handleQuestion()}} placeholder="Например: покажи отчёт по всем аккаунтам" style={{minWidth:0,flex:1,minHeight:42,border:"1px solid var(--boris-border,#d0d5dd)",borderRadius:10,padding:"0 11px",fontSize:13}}/><button onClick={handleQuestion} disabled={!question.trim()} style={{minHeight:42,border:0,borderRadius:10,padding:"0 13px",background:"var(--boris-blue,#2563eb)",color:"#fff",fontWeight:700,cursor:"pointer"}}>→</button></div>

      {proactive && !open && <button onClick={()=>{setProactive(false);sessionStorage.setItem("boris_proactive_snooze_until",String(Date.now()+10*60*1000))}} style={{marginTop:10,border:0,background:"transparent",cursor:"pointer",color:"var(--boris-text-3,#667085)"}}>Не сейчас</button>}
      </div>
    </div>}
    <button aria-label="Открыть помощника BORIS" className="boris-contextual-launcher" onClick={()=>{setProactive(false);setOpen(v=>!v)}} style={{position:"fixed",right:16,bottom:20,width:62,height:62,padding:0,border:"2px solid rgba(255,255,255,.92)",borderRadius:20,background:`radial-gradient(circle at 30% 20%,#fff 0%,${persona.accent}18 35%,${persona.accent}35 100%)`,boxShadow:`0 18px 42px ${persona.accent}45,0 5px 14px rgba(15,23,42,.16)`,cursor:"pointer",zIndex:1049,display:"grid",placeItems:"center",overflow:"visible"}}>
      <span aria-hidden="true" style={{position:"absolute",inset:-7,borderRadius:25,border:`1px solid ${persona.accent}3a`,boxShadow:`0 0 0 6px ${persona.accent}0b`}} />
      <span aria-hidden="true" style={{fontSize:persona.key==="robot"?33:34,lineHeight:1,filter:"drop-shadow(0 4px 7px rgba(15,23,42,.12))"}}>{persona.emoji}</span>
      <span aria-hidden="true" style={{position:"absolute",left:-8,top:-9,width:25,height:25,display:"grid",placeItems:"center",border:"2px solid #fff",borderRadius:10,background:"#fff",fontSize:14,boxShadow:"0 5px 14px rgba(15,23,42,.15)",transform:"rotate(-7deg)"}}>{routePose}</span>
      <span style={{position:"absolute",right:-4,bottom:-4,minWidth:20,height:20,padding:"0 4px",display:"grid",placeItems:"center",border:"2px solid #fff",borderRadius:7,background:`linear-gradient(135deg,${persona.accent},${persona.accent2})`,color:"#fff",fontSize:9,fontWeight:900,boxShadow:"0 4px 10px rgba(15,23,42,.18)"}}>AI</span>
    </button>
  </>;
}
