// Next.js Landing Page for БОРИС SaaS Platform — ПЕРЕСОБРАНО из актуальной демо-версии (42 карточки, новый hero)
// Save to: frontend/app/page.tsx
// ВАЖНО: требует зависимость 'motion' — выполнить 'npm install motion' в папке frontend перед сборкой
// ВАЖНО: компонент Mascot импортируется из '../components/Mascot' — положи mascot_nextjs.tsx туда как Mascot.tsx

'use client';

import React, { useEffect, useState, useRef } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { 
 ArrowRight, 
 Check, 
 HelpCircle, 
 Clock, 
 Sparkles, 
 ShieldAlert, 
 TrendingUp, 
 MessageSquare, 
 Users, 
 Briefcase, 
 FileText, 
 ChevronDown, 
 ChevronUp, 
 Lock, 
 Mail, 
 Building, 
 Image as ImageIcon, 
 CheckCircle2, 
 XCircle,
 HelpCircle as InfoIcon
} from 'lucide-react';
import { Mascot } from './components/Mascot';

import { registerUser, persistSession, rememberVerification } from './lib/register';
import TurnstileBox from './lib/turnstile';
import { resolvePostAuthRoute } from './lib/onboardingClient';
interface LandingPageProps {
 previewContent?: Record<string, any>; // Optional content passed from Admin Panel live-preview
}

export const LandingPage: React.FC<LandingPageProps> = ({ previewContent }) => {
 const [content, setContent] = useState<Record<string, any>>({});
 const [loading, setLoading] = useState(!previewContent);
 const [error, setError] = useState<string | null>(null);

 // FAQ open/close states
 const [openFaq, setOpenFaq] = useState<number | null>(null);

 // Active Category for 34 points
 const [activeCategory, setActiveCategory] = useState<number>(0);

 // Interactive Calculator State
 const [adCount, setAdCount] = useState<number>(30);
 const [hoursSaved, setHoursSaved] = useState<number>(0);

 // Interactive A/B Comparison State
 const [compareShowBoris, setCompareShowBoris] = useState<boolean>(true);

 // Chat Scenario State
 const [currentScenarioIndex, setCurrentScenarioIndex] = useState<number>(0);
 const [chatMessages, setChatMessages] = useState<any[]>([]);
 const [isTyping, setIsTyping] = useState<boolean>(false);
 const chatEndRef = useRef<HTMLDivElement>(null);
 const chatContainerRef = useRef<HTMLDivElement>(null);
 const chatScrollRef = useRef<HTMLDivElement>(null);
 const [chatStarted, setChatStarted] = useState<boolean>(false);

 // Registration Form State
 const [regEmail, setRegEmail] = useState('');
 const [regPassword, setRegPassword] = useState('');
 const [regCompany, setRegCompany] = useState('');
 const [regPlanned, setRegPlanned] = useState('1');
  const [regError, setRegError] = useState('');
 const [regSuccess, setRegSuccess] = useState(false);
 const [consentChecked, setConsentChecked] = useState(false);
 const [regToken, setRegToken] = useState('');
 const [regCaptchaKey, setRegCaptchaKey] = useState(0);

 // Fetch landing page content
 useEffect(() => {
 if (previewContent) {
 setContent(previewContent);
 setLoading(false);
 return;
 }

 fetch('/api/landing/content')
 .then(res => res.json())
 .then(data => {
 if (data.success) {
 setContent(data.content);
 } else {
 setError('Не удалось загрузить контент');
 }
 setLoading(false);
 })
 .catch(err => {
 console.error(err);
 setError('Ошибка при соединении с сервером');
 setLoading(false);
 });
 }, [previewContent]);

 // Easing counter animation for hours saved
 useEffect(() => {
 // 4 minutes per ad in manual handling
 const targetHours = Math.round((adCount * 4 * 10) / 60) / 10;
 
 let start = hoursSaved;
 const end = targetHours;
 if (start === end) return;
 
 const range = end - start;
 const duration = 300; // ms
 let startTime: number | null = null;

 const animate = (currentTime: number) => {
 if (!startTime) startTime = currentTime;
 const progress = Math.min((currentTime - startTime) / duration, 1);
 const currentVal = start + range * progress;
 setHoursSaved(Math.round(currentVal * 10) / 10);
 
 if (progress < 1) {
 requestAnimationFrame(animate);
 }
 };
 
 requestAnimationFrame(animate);
 }, [adCount]);

 // Chat trigger with IntersectionObserver
 useEffect(() => {
 if (!chatContainerRef.current || chatStarted) return;

 const observer = new IntersectionObserver((entries) => {
 if (entries[0].isIntersecting) {
 setChatStarted(true);
 startChatScenario(0);
 }
 }, { threshold: 0.2 });

 observer.observe(chatContainerRef.current);
 return () => observer.disconnect();
 }, [content, chatStarted]);

 // Run chat scenario
 const startChatScenario = async (scenarioIdx: number) => {
 const scenarios = content['chat.scenarios'] || [];
 if (scenarios.length === 0 || !scenarios[scenarioIdx]) return;

 const messages = scenarios[scenarioIdx].messages || [];
 setChatMessages([]);
 setIsTyping(false);

 for (let i = 0; i < messages.length; i++) {
 const msg = messages[i];
 if (msg.sender === 'client') {
 // Client posts instantly
 setChatMessages(prev => [...prev, msg]);
 await delay(800);
 } else {
 // Boris is typing...
 setIsTyping(true);
 // Type rate ~ 15-20ms per character plus some base wait
 const typingTime = Math.min(600 + msg.text.length * 15, 1800);
 await delay(typingTime);
 setIsTyping(false);
 setChatMessages(prev => [...prev, msg]);
 await delay(1000);
 }
 }
 };

 const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

 // Handle showing next mock chat scenario
 const handleNextScenario = () => {
 const scenarios = content['chat.scenarios'] || [];
 if (scenarios.length === 0) return;
 const nextIdx = (currentScenarioIndex + 1) % scenarios.length;
 setCurrentScenarioIndex(nextIdx);
 startChatScenario(nextIdx);
 };

 useEffect(() => {
 if (chatEndRef.current) {
 if (chatScrollRef.current) { chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight; }
 }
 }, [chatMessages, isTyping]);

 if (loading) {
 return (
 <div className="min-h-screen flex items-center justify-center bg-slate-50">
 <div className="flex flex-col items-center">
 <Mascot size={80} interactive={false} />
 <p className="mt-4 text-slate-500 font-medium animate-pulse">Загрузка космической платформы БОРИС...</p>
 </div>
 </div>
 );
 }

 // Define default values if content is empty or server failed
 const colorBg = content['color.bg'] || '#FFFFFF';
 const colorAccent = content['color.accent'] || '#2F6FED';
 const colorLight = content['color.light'] || '#F5FAFF';
 const colorText = content['color.text'] || '#14161A';
 const colorTextMuted = content['color.textMuted'] || '#5B6472';
 const colorSuccess = content['color.success'] || '#1E8E5A';
 const colorWarning = content['color.warning'] || '#E08A2C';

 // Helper styles mapping
 const inlineStyles = {
 '--color-bg': colorBg,
 '--color-accent': colorAccent,
 '--color-light': colorLight,
 '--color-text': colorText,
 '--color-text-muted': colorTextMuted,
 '--color-success': colorSuccess,
 '--color-warning': colorWarning,
 } as React.CSSProperties;

 // Group 42 pain points into categories
 const categories = [
 {
 name: 'Публикация и масштаб',
 icon: Sparkles,
 items: [
 { key: 'pain.1', pain: content['pain.1.title'], sol: content['pain.1.solution'] },
 { key: 'pain.2', pain: content['pain.2.title'], sol: content['pain.2.solution'] },
 { key: 'pain.3', pain: content['pain.3.title'], sol: content['pain.3.solution'] },
 { key: 'pain.4', pain: content['pain.4.title'], sol: content['pain.4.solution'] },
 { key: 'pain.5', pain: content['pain.5.title'], sol: content['pain.5.solution'] },
 { key: 'pain.35', pain: content['pain.35.title'], sol: content['pain.35.solution'] },
 { key: 'pain.36', pain: content['pain.36.title'], sol: content['pain.36.solution'] },
 ]
 },
 {
 name: 'ИИ-копирайтинг и визуал',
 icon: ImageIcon,
 items: [
 { key: 'pain.6', pain: content['pain.6.title'], sol: content['pain.6.solution'] },
 { key: 'pain.7', pain: content['pain.7.title'], sol: content['pain.7.solution'] },
 { key: 'pain.8', pain: content['pain.8.title'], sol: content['pain.8.solution'] },
 { key: 'pain.37', pain: content['pain.37.title'], sol: content['pain.37.solution'] },
 ]
 },
 {
 name: 'Защита от блокировок',
 icon: ShieldAlert,
 items: [
 { key: 'pain.9', pain: content['pain.9.title'], sol: content['pain.9.solution'] },
 { key: 'pain.10', pain: content['pain.10.title'], sol: content['pain.10.solution'] },
 ]
 },
 {
 name: 'Аналитика и результат',
 icon: TrendingUp,
 items: [
 { key: 'pain.11', pain: content['pain.11.title'], sol: content['pain.11.solution'] },
 { key: 'pain.12', pain: content['pain.12.title'], sol: content['pain.12.solution'] },
 { key: 'pain.13', pain: content['pain.13.title'], sol: content['pain.13.solution'] },
 { key: 'pain.14', pain: content['pain.14.title'], sol: content['pain.14.solution'] },
 { key: 'pain.15', pain: content['pain.15.title'], sol: content['pain.15.solution'] },
 { key: 'pain.16', pain: content['pain.16.title'], sol: content['pain.16.solution'] },
 { key: 'pain.40', pain: content['pain.40.title'], sol: content['pain.40.solution'] },
 { key: 'pain.41', pain: content['pain.41.title'], sol: content['pain.41.solution'] },
 ]
 },
 {
 name: 'ИИ-менеджер по продажам',
 icon: MessageSquare,
 items: [
 { key: 'pain.17', pain: content['pain.17.title'], sol: content['pain.17.solution'] },
 { key: 'pain.18', pain: content['pain.18.title'], sol: content['pain.18.solution'] },
 { key: 'pain.19', pain: content['pain.19.title'], sol: content['pain.19.solution'] },
 { key: 'pain.42', pain: content['pain.42.title'], sol: content['pain.42.solution'] },
 ]
 },
 {
 name: 'Агентства и CRM',
 icon: Users,
 items: [
 { key: 'pain.20', pain: content['pain.20.title'], sol: content['pain.20.solution'] },
 { key: 'pain.21', pain: content['pain.21.title'], sol: content['pain.21.solution'] },
 ]
 },
 {
 name: 'Дополнительные возможности',
 icon: Briefcase,
 items: [
 { key: 'pain.22', pain: content['pain.22.title'], sol: content['pain.22.solution'] },
 { key: 'pain.23', pain: content['pain.23.title'], sol: content['pain.23.solution'] },
 { key: 'pain.24', pain: content['pain.24.title'], sol: content['pain.24.solution'] },
 { key: 'pain.25', pain: content['pain.25.title'], sol: content['pain.25.solution'] },
 { key: 'pain.26', pain: content['pain.26.title'], sol: content['pain.26.solution'] },
 { key: 'pain.27', pain: content['pain.27.title'], sol: content['pain.27.solution'] },
 { key: 'pain.28', pain: content['pain.28.title'], sol: content['pain.28.solution'] },
 { key: 'pain.29', pain: content['pain.29.title'], sol: content['pain.29.solution'] },
 { key: 'pain.30', pain: content['pain.30.title'], sol: content['pain.30.solution'] },
 { key: 'pain.31', pain: content['pain.31.title'], sol: content['pain.31.solution'] },
 { key: 'pain.32', pain: content['pain.32.title'], sol: content['pain.32.solution'] },
 { key: 'pain.33', pain: content['pain.33.title'], sol: content['pain.33.solution'] },
 { key: 'pain.34', pain: content['pain.34.title'], sol: content['pain.34.solution'] },
 { key: 'pain.38', pain: content['pain.38.title'], sol: content['pain.38.solution'] },
 { key: 'pain.39', pain: content['pain.39.title'], sol: content['pain.39.solution'] },
 ]
 }
 ];

 const handleRegisterSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setRegError('');
    if (!regEmail || !regPassword) return;
    if (!regToken) { setRegError('Подтвердите, что вы не робот.'); return; }
    const r = await registerUser({
      email: regEmail,
      password: regPassword,
      accountName: regCompany,
      plannedAccounts: regPlanned,
      turnstileToken: regToken,
    });
    if (!r.ok) { setRegError(r.error); setRegToken(''); setRegCaptchaKey(k => k + 1); return; }
    const isPending = r.data && r.data.user && r.data.user.status === 'pending_verification';
    const saved = persistSession(r.data, true);
    if (!saved) {
      // ветка «адрес уже занят»: backend не выдаёт токен, на /verify вести некуда
      setRegError('Мы отправили письмо на указанный адрес. Если аккаунт уже существует — войдите.');
      setRegToken(''); setRegCaptchaKey(k => k + 1);
      return;
    }
    if (isPending) {
      rememberVerification(r.data);   // без verification_id экран /verify будет пустым
      window.location.href = '/verify';
      return;
    }
    setRegSuccess(true);
    const nextRoute = await resolvePostAuthRoute(r.data.user);
    setTimeout(() => {
      window.location.href = nextRoute;
    }, 1800);
  };

 return (
 <div 
 className="font-sans antialiased min-h-screen text-[#14161A] transition-colors duration-300 bg-white"
 style={inlineStyles}
 id="boris-landing-root"
 >
 {/* HEADER / NAVIGATION */}
 <header className="sticky top-0 z-50 bg-white/95 backdrop-blur-md border-b border-[#E5E7EB] h-[72px] flex items-center px-4 sm:px-6 md:px-10">
 <div className="max-w-7xl mx-auto w-full flex items-center justify-between">
 {/* Logo with Mascot */}
 <div className="flex items-center gap-[12px] cursor-pointer">
 <Mascot size={32} interactive={true} />
 <span className="font-extrabold text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] tracking-[-0.5px] text-[#14161A] font-display">
 БОРИС
 </span>
 </div>

 {/* Nav Anchors */}
 <nav className="hidden md:flex items-center gap-[32px] text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] font-medium text-[#5B6472]">
 <a href="#possibilities" className="hover:text-[#2F6FED] transition-colors">Возможности</a>
 <a href="#how-it-works" className="hover:text-[#2F6FED] transition-colors">Как это работает</a>
 <a href="#pricing" className="hover:text-[#2F6FED] transition-colors">Тариф</a>
 <a href="#faq" className="hover:text-[#2F6FED] transition-colors">FAQ</a>
 </nav>

 {/* CTA Header */}
 <div className="flex items-center gap-4">
 <a 
 href="#register" 
 className="bg-[#2F6FED] hover:bg-[#2058D0] text-white px-5 py-2.5 rounded-[8px] text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] font-semibold transition-colors shadow-sm"
 >
 Личный кабинет
 </a>
 </div>
 </div>
 </header>

 {/* HERO SECTION */}
 <section className="relative pt-12 pb-20 px-4 sm:px-6 md:px-10 overflow-hidden">
 <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-[40px] items-center min-h-[460px]">
 
 {/* Left Column Text */}
 <div className="space-y-6">
 {content['hero.badge'] && (
 <div 
 className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-[13px] sm:text-[14px] font-bold shadow-sm"
 style={{ backgroundColor: 'var(--color-light)', color: 'var(--color-accent)' }}
 >
 {content['hero.badge']}
 </div>
 )}
 
 <h1 className="text-[28px] sm:text-[36px] sm:text-[36px] font-extrabold text-[#14161A] tracking-tight leading-[1.1] font-display">
 {content['hero.title']}
 </h1>

 {/* Расшифровка БОРИС */}
 <div className="flex flex-wrap gap-x-3 gap-y-1 text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] font-bold">
 <span><span className="text-[#2F6FED]">Б</span>ыстрый</span>
 <span className="text-[#C9D2E3]">·</span>
 <span><span className="text-[#2F6FED]">О</span>нлайн</span>
 <span className="text-[#C9D2E3]">·</span>
 <span><span className="text-[#2F6FED]">Р</span>екламный</span>
 <span className="text-[#C9D2E3]">·</span>
 <span><span className="text-[#2F6FED]">И</span>И</span>
 <span className="text-[#C9D2E3]">·</span>
 <span><span className="text-[#2F6FED]">С</span>ервис</span>
 </div>

 <p className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] text-[#5B6472] leading-[1.5] max-w-2xl">
 {content['hero.subtitle']}
 </p>

 <div className="flex flex-wrap gap-4 pt-2">
 <a 
 href="#register" 
 className="inline-flex items-center justify-center gap-2 bg-[#2F6FED] hover:bg-[#2058D0] text-white py-[14px] px-[28px] rounded-[8px] font-semibold text-center transition-colors text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] shadow-sm"
 >
 {content['hero.cta']}
 <ArrowRight size={18} />
 </a>
 <div className="flex flex-col justify-center">
 <span className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] font-bold text-[#14161A]">4 дня бесплатно</span>
 <span className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] text-[#5B6472]">Без карты · настройка за 3 минуты</span>
 </div>
 </div>

 {/* Micro Badges / Microtext */}
 <div className="pt-6 flex flex-wrap gap-x-6 gap-y-2 text-[13px] sm:text-[14px] font-semibold text-[#5B6472]">
 {content['hero.microtext'] ? (
 <div className="flex items-center gap-1.5">
 <Check size={14} className="text-emerald-500" />
 <span>{content['hero.microtext']}</span>
 </div>
 ) : (
 <>
 <div className="flex items-center gap-1.5">
 <Check size={14} className="text-emerald-500" />
 Без привязки карты
 </div>
 <div className="flex items-center gap-1.5">
 <Check size={14} className="text-emerald-500" />
 Подключение за 3 минуты
 </div>
 <div className="flex items-center gap-1.5">
 <Check size={14} className="text-emerald-500" />
 Безопасно по API / Прокси
 </div>
 </>
 )}
 </div>
 </div>

 {/* Right Column - SIGNATURE LIVE MOCK-CHAT WITH BORIS */}
 <div className="w-full" ref={chatContainerRef}>
 <div className="bg-[#F5FAFF] border border-[#EAF4FF] rounded-[20px] shadow-[0_10px_25px_rgba(47,111,237,0.05)] h-[340px] flex flex-col p-5">
 
 {/* Chat Header */}
 <div className="border-b border-[#EAF4FF] pb-3 mb-3 flex items-center justify-between">
 <div className="flex items-center gap-3">
 <div className="relative">
 <Mascot size={28} interactive={false} />
 <span className="absolute bottom-0 right-0 w-2 h-2 bg-[#1E8E5A] border border-white rounded-full"></span>
 </div>
 <div>
 <h4 className="text-[#14161A] text-[13px] sm:text-[14px] font-bold flex items-center gap-1">
 Борис <span className="text-[13px] sm:text-[14px] bg-[#EAF4FF] text-[#2F6FED] px-1.5 py-0.5 rounded-full font-semibold">ИИ-Автопилот</span>
 </h4>
 <p className="text-[#5B6472] text-[13px] sm:text-[14px]">Квалифицирует лид 24/7</p>
 </div>
 </div>

 {/* Scenario Switcher */}
 <button
 onClick={handleNextScenario}
 className="px-2.5 py-1.5 rounded-lg bg-[#2F6FED] hover:bg-[#2058D0] text-white text-[13px] sm:text-[14px] font-bold flex items-center gap-1 transition-colors"
 >
 <Sparkles size={10} className="text-amber-300" />
 <span>Кейс: {content['chat.scenarios']?.[currentScenarioIndex]?.niche || 'Загрузка...'}</span>
 </button>
 </div>

 {/* Chat Bubble Stream */}
 <div ref={chatScrollRef} className="flex-1 overflow-y-auto pr-1 space-y-3 flex flex-col">
 {chatMessages.length === 0 && !isTyping && (
 <div className="h-full flex items-center justify-center text-center p-4">
 <p className="text-[#5B6472] text-[13px] sm:text-[14px] font-medium">Прокрутите страницу ниже, чтобы запустить диалог Бориса с клиентом</p>
 </div>
 )}
 {chatMessages.map((msg, index) => {
 const isClient = msg.sender === 'client';
 return (
 <div
 key={index}
 className={`flex ${isClient ? 'justify-end' : 'justify-start'}`}
 >
 <div
 className={`max-w-[80%] rounded-[16px] px-4 py-2.5 text-[13px] sm:text-[14px] leading-relaxed ${
 isClient
 ? 'bg-[#2F6FED] text-white rounded-br-[4px]'
 : 'bg-white text-[#14161A] border border-[#E5E7EB] rounded-bl-[4px]'
 }`}
 >
 {msg.text}
 </div>
 </div>
 );
 })}

 {/* Typing Indicator */}
 {isTyping && (
 <div className="flex justify-start">
 <div className="bg-white border border-[#E5E7EB] rounded-[16px] rounded-bl-[4px] px-4 py-2.5 flex items-center gap-1">
 <div className="w-[6px] h-[6px] bg-[#5B6472] rounded-full opacity-40 animate-bounce" />
 <div className="w-[6px] h-[6px] bg-[#5B6472] rounded-full opacity-40 animate-bounce [animation-delay:0.2s]" />
 <div className="w-[6px] h-[6px] bg-[#5B6472] rounded-full opacity-40 animate-bounce [animation-delay:0.4s]" />
 <span className="text-[13px] sm:text-[14px] text-[#5B6472] ml-2">Борис печатает...</span>
 </div>
 </div>
 )}
 <div ref={chatEndRef} />
 </div>
 </div>
 </div>

 </div>
 </section>

 {/* СЕКЦИЯ: два оффера под две ЦА */}
         {/* ===== REVIEWS BLOCK START ===== */}
        <section id="reviews" className="py-20 px-4 sm:px-6 md:px-10" style={{background:"#F6F7FB"}}>
          <div className="max-w-6xl mx-auto">
            <div style={{textAlign:"center", marginBottom:"12px"}}><span style={{display:"inline-block", background:"#EAF3FF", color:"#2F6FED", fontWeight:700, fontSize:"13px", padding:"7px 18px", borderRadius:"30px"}}>Реальные истории клиентов</span></div>
            <h2 style={{textAlign:"center", fontSize:"clamp(28px,4vw,42px)", fontWeight:800, color:"#14161A", margin:"0 0 12px", lineHeight:1.15}}>Что говорят наши клиенты о <span style={{color:"#2F6FED"}}>БОРИСе</span></h2>
            <p style={{textAlign:"center", fontSize:"17px", color:"#5B6472", maxWidth:"640px", margin:"0 auto 40px", lineHeight:1.5}}>Что изменилось в их работе и какими инструментами Бориса они пользуются каждый день.</p>
            <div className="rv-stats">
              <div className="rv-stat"><div className="rv-num">{"\u0434\u043e 3000"}</div><div className="rv-lab">объявлений за 40 минут</div></div>
              <div className="rv-stat"><div className="rv-num">{"\u00d73\u20134"}</div><div className="rv-lab">рост просмотров и контактов</div></div>
              <div className="rv-stat"><div className="rv-num">{"5+ \u0447\u0430\u0441\u043e\u0432"}</div><div className="rv-lab">свободного времени в день</div></div>
              <div className="rv-stat"><div className="rv-num">{"1 \u0434\u0435\u043d\u044c"}</div><div className="rv-lab">на запуск рекламы</div></div>
            </div>
            <div className="rv-marquee"><div className="rv-track">
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/materials.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#2F6FED"}}>АК</div></div><div className="rv-body"><div className="rv-name">Антон Кузин</div><div className="rv-role">стройматериалы, Москва</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abВёл 200 объявлений руками, уходило полдня. Этот сервис поднял 3000 за несколько часов. Освободилось много времени днём.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">ИИ-автопубликация по расписанию + автосоставление описаний</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/machinery.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#12B76A"}}>Т</div></div><div className="rv-body"><div className="rv-name">Татьяна</div><div className="rv-role">аренда спецтехники, Санкт-Петербург</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abПросмотров было 100 в день, стало стабильно 250-300 и растёт. Сервис освоила за 1 день.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">SEO-ядро и коммерческие ключи + аналитика спроса по городам</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/plumbing.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#F79009"}}>А</div></div><div className="rv-body"><div className="rv-name">Андрей</div><div className="rv-role">сантехнические услуги, Воронеж</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abОбъявления сливались в кашу, боялся дублей. Теперь каждое уникальное - и текст, и фото.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Автоуникализация каждого пикселя + автоуникализация текста</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/food.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#7A5AF8"}}>ИВ</div></div><div className="rv-body"><div className="rv-name">Игорь Володин</div><div className="rv-role">авитолог ресторана, Ставрополь</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abВсе позиции меню были на сайте, переносить руками - недели. Борис вытянул каталог за несколько минут.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Выгрузка с сайта + уникальные шаблоны + баннеры</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/equipment.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#E8620B"}}>А</div></div><div className="rv-body"><div className="rv-name">Андрей</div><div className="rv-role">оборудование для бизнеса, 2 филиала</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abВеду два аккаунта в разных городах - раньше путались. Теперь у каждого свои данные, ничего не пересекается.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Абсолютная изоляция данных + сводный дашборд директора</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/gates.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#0BA5A5"}}>А</div></div><div className="rv-body"><div className="rv-name">Антон</div><div className="rv-role">производство ворот и заборов, Пенза</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abЭкономлю минимум 5 часов в день. Эти часы теперь на само производство.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Автопубликация объявлений по расписанию</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/equipment.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#D63C8E"}}>КА</div></div><div className="rv-body"><div className="rv-name">Кристина Агапова</div><div className="rv-role">маркетолог производственной компании</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abАссортимент 1000+ позиций - руками это несколько дней или недель. Этот ИИ прогнал всё за 40 минут.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Выгрузка с сайта + уникальные описания под каждый товар</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/kitchen.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#3B82F6"}}>АК</div></div><div className="rv-body"><div className="rv-name">Александр Куприн</div><div className="rv-role">мастер по установке кухонь и шкафов</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abКонтакты выросли в 3-4 раза на первой неделе. Видно прямо в статистике.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Фильтр по контактам и конверсии + температура лидов</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/analytics.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#2F6FED"}}>В</div></div><div className="rv-body"><div className="rv-name">Виктор</div><div className="rv-role">частный авитолог</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abБоялся, что объявления не пройдут модерацию при массовой заливке. За несколько недель - ни одного отклонения.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Автопремодерация по правилам Avito + автоопределение категорий</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/construction.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#12B76A"}}>КА</div></div><div className="rv-body"><div className="rv-name">Константин Андреев</div><div className="rv-role">реклама на Авито, стройкомпания</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abЯ не программист, боялся не разобраться. Ответил на пару вопросов голосом - и Борис всё сделал сам под моим наблюдением.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Онбординг-интервью голосом + автоописания и публикация</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/towtruck.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#F79009"}}>Т</div></div><div className="rv-body"><div className="rv-name">Татьяна</div><div className="rv-role">аренда эвакуаторов, Сибирь</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abТексты сухие были, теперь живые - с эмодзи, с призывом. И в поиске стали находиться.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Тексты по формулам AIDA/PASCAL + SEO-ядро + анализ конкурентов</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/sports.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#7A5AF8"}}>В</div></div><div className="rv-body"><div className="rv-name">Василий</div><div className="rv-role">магазин спортинвентаря</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abУходил в отпуск и боялся всё бросить. Включил режим отпуска - Борис вёл продвижение сам.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Режим отпуска в Автопилоте</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/seafood.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#E8620B"}}>А</div></div><div className="rv-body"><div className="rv-name">Анатолий</div><div className="rv-role">продажа рыбы и морепродуктов</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abНе успевал отвечать клиентам в чатах Avito. Теперь ИИ-менеджер отвечает за 30 секунд по нашему прайсу.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Виртуальный ИИ-продавец + фильтр только покупатели</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/paving.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#0BA5A5"}}>А</div></div><div className="rv-body"><div className="rv-name">Арташес</div><div className="rv-role">тротуарная плитка, Нижний Новгород</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abЗапустил новое направление за 1 день: тексты, баннеры, фото, публикация - всё разом.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Продающие описания + баннеры 24/7 + выгрузка в Авито</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/tools.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#D63C8E"}}>С</div></div><div className="rv-body"><div className="rv-name">Сергей</div><div className="rv-role">магазин инструмента, тариф Максимальный</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abОформил витрину магазина каруселью - три баннера, как из студии. Профиль сразу стал солиднее.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Карусель баннеров витрины (Максимальный) + ИИ-генератор баннеров 24/7</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/materials.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#2F6FED"}}>АК</div></div><div className="rv-body"><div className="rv-name">Антон Кузин</div><div className="rv-role">стройматериалы, Москва</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abВёл 200 объявлений руками, уходило полдня. Этот сервис поднял 3000 за несколько часов. Освободилось много времени днём.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">ИИ-автопубликация по расписанию + автосоставление описаний</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/machinery.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#12B76A"}}>Т</div></div><div className="rv-body"><div className="rv-name">Татьяна</div><div className="rv-role">аренда спецтехники, Санкт-Петербург</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abПросмотров было 100 в день, стало стабильно 250-300 и растёт. Сервис освоила за 1 день.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">SEO-ядро и коммерческие ключи + аналитика спроса по городам</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/plumbing.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#F79009"}}>А</div></div><div className="rv-body"><div className="rv-name">Андрей</div><div className="rv-role">сантехнические услуги, Воронеж</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abОбъявления сливались в кашу, боялся дублей. Теперь каждое уникальное - и текст, и фото.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Автоуникализация каждого пикселя + автоуникализация текста</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/food.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#7A5AF8"}}>ИВ</div></div><div className="rv-body"><div className="rv-name">Игорь Володин</div><div className="rv-role">авитолог ресторана, Ставрополь</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abВсе позиции меню были на сайте, переносить руками - недели. Борис вытянул каталог за несколько минут.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Выгрузка с сайта + уникальные шаблоны + баннеры</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/equipment.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#E8620B"}}>А</div></div><div className="rv-body"><div className="rv-name">Андрей</div><div className="rv-role">оборудование для бизнеса, 2 филиала</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abВеду два аккаунта в разных городах - раньше путались. Теперь у каждого свои данные, ничего не пересекается.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Абсолютная изоляция данных + сводный дашборд директора</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/gates.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#0BA5A5"}}>А</div></div><div className="rv-body"><div className="rv-name">Антон</div><div className="rv-role">производство ворот и заборов, Пенза</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abЭкономлю минимум 5 часов в день. Эти часы теперь на само производство.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Автопубликация объявлений по расписанию</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/equipment.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#D63C8E"}}>КА</div></div><div className="rv-body"><div className="rv-name">Кристина Агапова</div><div className="rv-role">маркетолог производственной компании</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abАссортимент 1000+ позиций - руками это несколько дней или недель. Этот ИИ прогнал всё за 40 минут.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Выгрузка с сайта + уникальные описания под каждый товар</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/kitchen.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#3B82F6"}}>АК</div></div><div className="rv-body"><div className="rv-name">Александр Куприн</div><div className="rv-role">мастер по установке кухонь и шкафов</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abКонтакты выросли в 3-4 раза на первой неделе. Видно прямо в статистике.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Фильтр по контактам и конверсии + температура лидов</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/analytics.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#2F6FED"}}>В</div></div><div className="rv-body"><div className="rv-name">Виктор</div><div className="rv-role">частный авитолог</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abБоялся, что объявления не пройдут модерацию при массовой заливке. За несколько недель - ни одного отклонения.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Автопремодерация по правилам Avito + автоопределение категорий</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/construction.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#12B76A"}}>КА</div></div><div className="rv-body"><div className="rv-name">Константин Андреев</div><div className="rv-role">реклама на Авито, стройкомпания</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abЯ не программист, боялся не разобраться. Ответил на пару вопросов голосом - и Борис всё сделал сам под моим наблюдением.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Онбординг-интервью голосом + автоописания и публикация</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/towtruck.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#F79009"}}>Т</div></div><div className="rv-body"><div className="rv-name">Татьяна</div><div className="rv-role">аренда эвакуаторов, Сибирь</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abТексты сухие были, теперь живые - с эмодзи, с призывом. И в поиске стали находиться.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Тексты по формулам AIDA/PASCAL + SEO-ядро + анализ конкурентов</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/sports.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#7A5AF8"}}>В</div></div><div className="rv-body"><div className="rv-name">Василий</div><div className="rv-role">магазин спортинвентаря</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abУходил в отпуск и боялся всё бросить. Включил режим отпуска - Борис вёл продвижение сам.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Режим отпуска в Автопилоте</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/seafood.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#E8620B"}}>А</div></div><div className="rv-body"><div className="rv-name">Анатолий</div><div className="rv-role">продажа рыбы и морепродуктов</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abНе успевал отвечать клиентам в чатах Avito. Теперь ИИ-менеджер отвечает за 30 секунд по нашему прайсу.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Виртуальный ИИ-продавец + фильтр только покупатели</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/paving.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#0BA5A5"}}>А</div></div><div className="rv-body"><div className="rv-name">Арташес</div><div className="rv-role">тротуарная плитка, Нижний Новгород</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abЗапустил новое направление за 1 день: тексты, баннеры, фото, публикация - всё разом.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Продающие описания + баннеры 24/7 + выгрузка в Авито</div></div></div></div>
                <div className="rv-card"><div className="rv-img" style={{backgroundImage:"url(/reviews/tools.jpg)"}}><div className="rv-tag">Клиент Бориса</div><div className="rv-avatar" style={{background:"#D63C8E"}}>С</div></div><div className="rv-body"><div className="rv-name">Сергей</div><div className="rv-role">магазин инструмента, тариф Максимальный</div><div className="rv-stars">{"\u2605\u2605\u2605\u2605\u2605"}</div><p className="rv-text">{"\u00abОформил витрину магазина каруселью - три баннера, как из студии. Профиль сразу стал солиднее.\u00bb"}</p><div className="rv-uses-wrap"><div className="rv-uses-lab">Использует в Борисе</div><div className="rv-uses">Карусель баннеров витрины (Максимальный) + ИИ-генератор баннеров 24/7</div></div></div></div>
            </div></div>
            <div style={{textAlign:"center", marginTop:"40px"}}><a href="#pricing" className="rv-cta">Попробовать бесплатно 4 дня БОРИСа</a><div style={{color:"#8A93A6", fontSize:"13px", marginTop:"14px"}}>Без карты и обязательств. Запуск рекламы уже в первый день.</div></div>
          </div>
          <style dangerouslySetInnerHTML={{__html: `
            .rv-stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:16px; margin-bottom:44px; }
            .rv-stat { background:#fff; border:1px solid #E3E7F0; border-radius:16px; padding:24px 18px; text-align:center; transition:transform .25s cubic-bezier(.22,1,.36,1), box-shadow .25s ease, border-color .25s ease; }
            .rv-stat:hover { transform:translateY(-6px); box-shadow:0 16px 34px rgba(47,111,237,0.16); border-color:#2F6FED; }
            .rv-num { font-size:clamp(24px,3vw,32px); font-weight:800; color:#2F6FED; line-height:1; }
            .rv-lab { font-size:13.5px; color:#5B6472; margin-top:8px; }
            .rv-marquee { overflow:hidden; -webkit-mask-image:linear-gradient(90deg,transparent,#000 3%,#000 97%,transparent); mask-image:linear-gradient(90deg,transparent,#000 3%,#000 97%,transparent); }
            .rv-track { display:flex; gap:22px; width:max-content; padding:8px 0; animation:rv-scroll 90s linear infinite; }
            .rv-marquee:hover .rv-track { animation-play-state:paused; }
            @keyframes rv-scroll { from{transform:translateX(0)} to{transform:translateX(-50%)} }
            .rv-card { flex:0 0 380px; width:380px; background:#fff; border-radius:20px; overflow:hidden; box-shadow:0 10px 30px rgba(16,24,40,0.10); display:flex; flex-direction:column; transition:transform .28s cubic-bezier(.22,1,.36,1), box-shadow .28s ease; }
            .rv-card:hover { transform:translateY(-10px); box-shadow:0 26px 52px rgba(16,24,40,0.22); }
            .rv-img { height:190px; position:relative; background-size:cover; background-position:center; }
            .rv-img::after { content:""; position:absolute; inset:0; background:linear-gradient(to top,rgba(0,0,0,0.32),transparent 45%); }
            .rv-tag { position:absolute; top:12px; left:12px; z-index:2; background:rgba(255,255,255,0.94); color:#14161A; font-size:11px; font-weight:700; padding:5px 11px; border-radius:20px; }
            .rv-avatar { position:absolute; bottom:-22px; left:20px; z-index:3; width:50px; height:50px; border-radius:50%; border:3px solid #fff; color:#fff; font-weight:800; font-size:16px; display:flex; align-items:center; justify-content:center; box-shadow:0 4px 12px rgba(0,0,0,0.25); }
            .rv-body { padding:30px 22px 22px; display:flex; flex-direction:column; flex:1; }
            .rv-name { font-weight:700; color:#14161A; font-size:16px; }
            .rv-role { font-size:12.5px; color:#8A93A6; margin:2px 0 10px; }
            .rv-stars { color:#FDB022; font-size:15px; letter-spacing:2px; margin-bottom:12px; }
            .rv-text { font-size:15px; color:#3A414D; line-height:1.58; margin:0 0 16px; flex:1; }
            .rv-uses-wrap { border-top:1px solid #EEF1F6; padding-top:12px; }
            .rv-uses-lab { font-size:10.5px; font-weight:700; color:#9AA3B2; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px; }
            .rv-uses { font-size:12.5px; color:#2F6FED; line-height:1.42; font-weight:600; }
            .rv-cta { display:inline-block; background:linear-gradient(90deg,#2F6FED,#4C8DFF); color:#fff; font-weight:700; font-size:17px; padding:16px 40px; border-radius:12px; text-decoration:none; box-shadow:0 10px 26px rgba(47,111,237,0.35); transition:transform .2s ease, box-shadow .2s ease; }
            .rv-cta:hover { transform:translateY(-3px); box-shadow:0 18px 42px rgba(47,111,237,0.5); }
            @media(max-width:600px){ .rv-card{flex-basis:300px;width:300px;} .rv-img{height:165px;} }
          `}} />
        </section>
        {/* ===== REVIEWS BLOCK END ===== */}

        <section className="py-20 px-4 sm:px-6 md:px-10 bg-gradient-to-b from-white to-[#F5FAFF]">
 <div className="max-w-6xl mx-auto text-center mb-12">
 <h2 className="text-[28px] sm:text-[36px] font-extrabold text-[#14161A] font-display mb-3 leading-tight">
 Борис работает, пока вы живёте
 </h2>
 <p className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] text-[#5B6472] max-w-2xl mx-auto">
 Одному предпринимателю или агентству с десятками клиентов — Борис снимает рутину с каждого
 </p>
 </div>

 <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-6xl mx-auto">

 {/* Оффер 1 — соло-предприниматель */}
 <div className="lift-on-hover group relative bg-white rounded-[28px] p-8 sm:p-10 border border-[#EAF4FF] shadow-[0_8px_30px_rgba(47,111,237,0.06)] hover:shadow-[0_16px_50px_rgba(47,111,237,0.12)] transition-all duration-300 overflow-hidden">
 <div className="absolute -top-16 -right-16 w-56 h-56 bg-[#2F6FED]/5 rounded-full blur-3xl group-hover:bg-[#2F6FED]/10 transition-colors" />
 <div className="relative">
 <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-[#2F6FED]/10 text-[#2F6FED] text-[13px] sm:text-[14px] font-bold mb-6">
 🌙 Для соло-предпринимателя
 </div>
 <h3 className="text-[28px] sm:text-[36px] font-extrabold text-[#14161A] font-display leading-tight mb-4">
 Лёг — встал — на коне
 </h3>
 <p className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] text-[#5B6472] leading-relaxed mb-7">
 Вечером накидали задачи по объявлениям — и легли спать. Ночью Борис сам готовит баннеры, тексты и шаблоны. Утром вы просто одобряете и запускаете. Никакой рутины — только сильные действия.
 </p>
 <div className="space-y-3.5">
 {["Вечером ставите задачи в один клик","Ночью Борис молотит очередь — ничего не теряется","Утром готовый результат ждёт вашего «ок»"].map((t, i) => (
 <div key={i} className="flex items-start gap-3">
 <div className="w-6 h-6 rounded-full bg-[#2F6FED]/10 flex items-center justify-center shrink-0 mt-0.5">
 <Check size={14} className="text-[#2F6FED]" />
 </div>
 <span className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] text-[#14161A] font-medium">{t}</span>
 </div>
 ))}
 </div>
 </div>
 </div>

 {/* Оффер 2 — маркетолог / агентство */}
 <div className="lift-on-hover group relative bg-slate-900 rounded-[28px] p-8 sm:p-10 border border-slate-800 shadow-[0_8px_30px_rgba(0,0,0,0.15)] hover:shadow-[0_16px_50px_rgba(0,0,0,0.25)] transition-all duration-300 overflow-hidden">
 <div className="absolute -top-16 -right-16 w-56 h-56 bg-amber-400/10 rounded-full blur-3xl group-hover:bg-amber-400/20 transition-colors" />
 <div className="relative">
 <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-amber-400/15 text-amber-300 text-[13px] sm:text-[14px] font-bold mb-6">
 👑 Для маркетолога и агентства
 </div>
 <h3 className="text-[28px] sm:text-[36px] font-extrabold text-white font-display leading-tight mb-4">
 Все клиенты — в одном окне
 </h3>
 <p className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] text-slate-300 leading-relaxed mb-7">
 Десятки клиентов, у каждого свои задачи и дедлайны? Борис держит всё под контролем: данные каждого клиента, очередь задач и оповещения по каждой — вы ничего не упустите, даже когда клиентов сотня.
 </p>
 <div className="space-y-3.5">
 {["Все аккаунты клиентов на одном экране","Очередь задач по каждому клиенту отдельно","Оповещения по каждой задаче — ни одна не потеряется"].map((t, i) => (
 <div key={i} className="flex items-start gap-3">
 <div className="w-6 h-6 rounded-full bg-amber-400/15 flex items-center justify-center shrink-0 mt-0.5">
 <Check size={14} className="text-amber-400" />
 </div>
 <span className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] text-white font-medium">{t}</span>
 </div>
 ))}
 </div>
 </div>
 </div>

 </div>
 </section>





 {/* SOCIAL PROOF / FOR WHOM */}
 <section className="py-16 px-4 sm:px-6 md:px-10 bg-slate-50">
 <div className="max-w-7xl mx-auto space-y-12">
 <div className="text-center space-y-4">
 <h2 className="text-[28px] sm:text-[36px] font-extrabold tracking-tight text-slate-900">
 {content['social.title']}
 </h2>
 <p className="text-slate-500 max-w-2xl mx-auto text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] ">
 Борис полностью настраивается под специфику любой отрасли, беря на себя 95% монотонных рутинных задач.
 </p>
 </div>

 <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
 {(content['social.items'] || []).map((item: any, i: number) => (
 <div 
 key={i} 
 className="lift-on-hover bg-white p-6 rounded-2xl border border-slate-100 shadow-sm space-y-4"
 >
 <div 
 className="w-10 h-10 rounded-xl flex items-center justify-center font-bold text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px]"
 style={{ backgroundColor: 'var(--color-light)', color: 'var(--color-accent)' }}
 >
 {i + 1}
 </div>
 <h3 className="font-extrabold text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] text-slate-900">{item.title}</h3>
 <p className="text-[13px] sm:text-[14px] text-slate-500 leading-relaxed">{item.desc}</p>
 </div>
 ))}
 </div>
 </div>
 </section>

 {/* HOW IT WORKS */}
 <section id="how-it-works" className="py-20 px-4 sm:px-6 md:px-10 bg-white">
 <div className="max-w-7xl mx-auto space-y-16">
 <div className="text-center space-y-4">
 <h2 className="text-[28px] sm:text-[36px] font-extrabold tracking-tight text-[#14161A] font-display">
 {content['how.title']}
 </h2>
 <p className="text-[#5B6472] max-w-2xl mx-auto text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] ">
 Всего 4 простых этапа, чтобы переключить привлечение клиентов с ручного труда на автоматический интеллект.
 </p>
 </div>

 <div className="grid grid-cols-1 md:grid-cols-4 gap-8 relative">
 {(content['how.steps'] || []).map((step: any, idx: number) => (
 <div key={idx} className="relative space-y-4">
 {/* Connecting arrow/line for desktop */}
 {idx < 3 && (
 <div className="hidden md:block absolute top-6 left-full w-full h-[2px] bg-[#EAF4FF] -translate-x-6 z-0" />
 )}
 
 <div 
 className="w-12 h-12 rounded-[12px] flex items-center justify-center text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] font-black relative z-10 text-white shadow-md bg-[#2F6FED]"
 >
 {step.step}
 </div>
 <h3 className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] font-extrabold text-[#14161A] font-display">{step.title}</h3>
 <p className="text-[13px] sm:text-[14px] text-[#5B6472] leading-[1.5]">{step.desc}</p>
 </div>
 ))}
 </div>
 </div>
 </section>

 {/* INTERACTIVE CALCULATOR BLOCK */}
 <section className="py-16 px-4 sm:px-6 md:px-10 bg-[#F5FAFF] border-y border-[#EAF4FF]">
 <div className="max-w-4xl mx-auto bg-white rounded-[24px] p-6 sm:p-10 shadow-[0_10px_25px_rgba(47,111,237,0.05)] border border-[#EAF4FF] grid grid-cols-1 md:grid-cols-12 gap-8 items-center">
 
 <div className="md:col-span-7 space-y-6">
 <h3 className="text-[28px] sm:text-[36px] font-extrabold tracking-tight text-[#14161A] font-display">
 {content['calc.title']}
 </h3>
 <p className="text-[13px] sm:text-[14px] text-[#5B6472]">
 {content['calc.subtitle']}
 </p>

 <div className="space-y-4 pt-2">
 <div className="flex justify-between items-center text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] font-bold">
 <span className="text-[#14161A]">Сколько у вас active объявлений?</span>
 <span className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] text-[#2F6FED] bg-[#EAF4FF] px-3 py-1 rounded-lg border border-[#EAF4FF] font-mono">{adCount} шт.</span>
 </div>
 <input 
 type="range" 
 min="5" 
 max="3000" 
 value={adCount}
 onChange={(e) => setAdCount(Number(e.target.value))}
 className="w-full h-2 bg-slate-100 rounded-lg appearance-none cursor-pointer accent-[#2F6FED]"
 />
 <div className="flex justify-between text-[13px] sm:text-[14px] text-[#5B6472] font-semibold">
 <span>5 объявлений</span>
 <span>1500 шт.</span>
 <span>3000 объявлений</span>
 </div>
 </div>
 </div>

 <div className="md:col-span-5 bg-[#F5FAFF] rounded-[16px] p-6 border border-[#EAF4FF] flex flex-col items-center justify-center text-center space-y-4 shadow-sm">
 <Clock size={36} className="text-[#2F6FED]" />
 <div>
 <p className="text-[13px] sm:text-[14px] text-[#5B6472] uppercase tracking-[0.5px] font-bold">Вы тратите вручную:</p>
 <p className="text-[28px] sm:text-[36px] font-extrabold text-[#14161A] line-through decoration-rose-500 decoration-3 mt-1 font-display">
 ≈ {hoursSaved} ч. <span className="text-[13px] sm:text-[14px] font-normal text-[#5B6472]">/ нед.</span>
 </p>
 </div>
 
 <div className="w-full h-[1px] bg-[#EAF4FF]" />

 <div>
 <p className="text-[13px] sm:text-[14px] text-[#5B6472] uppercase tracking-[0.5px] font-bold">С автопилотом Борис:</p>
 <p className="text-[28px] sm:text-[36px] sm:text-[36px] font-black text-emerald-600 flex items-center justify-center gap-1.5 mt-1 font-display">
 0 часов <Sparkles size={20} className="text-amber-500" />
 </p>
 <p className="text-[13px] sm:text-[14px] text-[#5B6472] font-medium mt-1">ИИ делает всю рутину за вас</p>
 </div>
 </div>

 </div>
 </section>

 {/* PAIN / SOLUTIONS GRID (THE 34 POINTS) */}
 <section id="possibilities" className="py-20 px-4 sm:px-6 md:px-10 bg-white">
 <div className="max-w-7xl mx-auto space-y-12">
 
 <div className="text-center space-y-4">
 <h2 className="text-[28px] sm:text-[36px] font-extrabold tracking-tight text-[#14161A] font-display">
 Полный спектр ИИ-автоматизации Бориса
 </h2>
 <p className="text-[#5B6472] max-w-2xl mx-auto text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] ">
 Мы детально изучили проблемы бизнеса и упаковали их решения в 42 функциональных возможности системы.
 </p>
 </div>

 {/* Tab Navigation for 7 Categories */}
 <div className="flex flex-wrap justify-center gap-2 border-b border-[#EAF4FF] pb-4">
 {categories.map((cat, idx) => {
 const Icon = cat.icon;
 const isActive = activeCategory === idx;
 return (
 <button
 key={idx}
 onClick={() => setActiveCategory(idx)}
 className={`flex items-center gap-2 px-4 py-2.5 rounded-[8px] text-[13px] sm:text-[14px] font-bold transition-all cursor-pointer ${
 isActive 
 ? 'bg-[#2F6FED] text-white shadow-md' 
 : 'bg-[#F5FAFF] border border-[#EAF4FF] text-[#5B6472] hover:bg-[#EAF4FF] hover:text-[#14161A]'
 }`}
 >
 <Icon size={14} />
 <span>{cat.name}</span>
 </button>
 );
 })}
 </div>

 {/* Active Category Content Panel */}
 <div className="bg-[#F5FAFF] rounded-[20px] p-6 sm:p-10 border border-[#EAF4FF] shadow-[0_10px_25px_rgba(47,111,237,0.02)]">
 <h3 className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] font-black text-[#14161A] mb-6 flex items-center gap-2 font-display">
 <Sparkles size={18} className="text-[#2F6FED]" />
 Раздел: {categories[activeCategory].name}
 </h3>

 <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
 {categories[activeCategory].items.map((item, index) => {
 // Prevent crash if any point is undefined
 if (!item.pain) return null;
 return (
 <div 
 key={index} 
 className="lift-on-hover bg-white p-6 rounded-[12px] border border-[#EAF4FF] relative overflow-hidden shadow-sm flex flex-col justify-between hover:border-[#2F6FED]/30"
 >
 {/* label-new style */}
 <span className="absolute top-0 right-0 bg-[#2F6FED] text-white text-[13px] sm:text-[14px] py-[2px] px-2 rounded-bl-[8px] font-bold uppercase tracking-wider">
 Борис ИИ
 </span>

 <div className="space-y-4">
 <div className="flex items-start gap-2.5">
 <span className="p-1 rounded-lg bg-red-50 text-red-600 text-[13px] sm:text-[14px] mt-0.5">
 <XCircle size={14} />
 </span>
 <div className="space-y-1">
 <span className="text-[13px] sm:text-[14px] uppercase tracking-wider text-[#5B6472] font-bold">Проблема</span>
 <h4 className="font-extrabold text-[13px] sm:text-[14px] text-[#14161A] leading-snug">{item.pain}</h4>
 </div>
 </div>
 
 <div className="h-[1px] bg-[#EAF4FF] w-full" />
 
 <div className="flex items-start gap-2.5">
 <span className="p-1 rounded-lg bg-emerald-50 text-emerald-600 text-[13px] sm:text-[14px] mt-0.5">
 <CheckCircle2 size={14} />
 </span>
 <div className="space-y-1">
 <span className="text-[13px] sm:text-[14px] uppercase tracking-wider text-emerald-500 font-bold">Решение от Бориса</span>
 <p className="text-[13px] sm:text-[14px] text-[#5B6472] leading-[1.4]">{item.sol}</p>
 </div>
 </div>
 </div>
 </div>
 );
 })}
 </div>
 </div>

 </div>
 </section>

 {/* INTERACTIVE DO / AFTER COMPARISON SECTION */}
 <section className="py-16 px-4 sm:px-6 md:px-10 bg-[#F5FAFF] border-y border-[#EAF4FF]">
 <div className="max-w-4xl mx-auto space-y-10">
 
 <div className="text-center space-y-4">
 <h3 className="text-[28px] sm:text-[36px] font-extrabold text-[#14161A] font-display">
 {content['compare.title']}
 </h3>
 <p className="text-[#5B6472] max-w-xl mx-auto text-[13px] sm:text-[14px] ">
 {content['compare.subtitle']}
 </p>
 </div>

 <div className="bg-white rounded-[20px] p-5 sm:p-8 shadow-sm border border-[#EAF4FF] space-y-6">
 
 {/* Custom Interactive Toggle Selector */}
 <div className="flex justify-center">
 <div className="bg-[#F5FAFF] border border-[#EAF4FF] p-1.5 rounded-[12px] inline-flex gap-1.5">
 <button
 onClick={() => setCompareShowBoris(false)}
 className={`px-5 py-2.5 rounded-[8px] text-[13px] sm:text-[14px] font-bold transition-all cursor-pointer ${
 !compareShowBoris 
 ? 'bg-rose-500 text-white shadow-sm' 
 : 'text-[#5B6472] hover:text-[#14161A]'
 }`}
 >
 Обычный текст
 </button>
 <button
 onClick={() => setCompareShowBoris(true)}
 className={`px-5 py-2.5 rounded-[8px] text-[13px] sm:text-[14px] font-bold transition-all cursor-pointer flex items-center gap-1.5 ${
 compareShowBoris 
 ? 'bg-[#2F6FED] text-white shadow-sm' 
 : 'text-[#5B6472] hover:text-[#14161A]'
 }`}
 >
 <Sparkles size={14} />
 Текст от Бориса
 </button>
 </div>
 </div>

 {/* Structured Compare Block */}
 <div className="relative overflow-hidden rounded-2xl min-h-[220px]">
 <AnimatePresence mode="wait">
 {!compareShowBoris ? (
 <motion.div
 key="before"
 initial={{ opacity: 0, y: 10 }}
 animate={{ opacity: 1, y: 0 }}
 exit={{ opacity: 0, y: -10 }}
 transition={{ duration: 0.25 }}
 className="bg-rose-50/50 border border-rose-100 rounded-2xl p-6 h-full space-y-3"
 >
 <h4 className="font-extrabold text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] text-rose-700 flex items-center gap-2">
 <XCircle size={16} />
 {content['compare.beforeTitle']}
 </h4>
 <p className="text-[13px] sm:text-[14px] text-slate-600 leading-relaxed font-medium">
 {content['compare.beforeText']}
 </p>
 </motion.div>
 ) : (
 <motion.div
 key="after"
 initial={{ opacity: 0, y: 10 }}
 animate={{ opacity: 1, y: 0 }}
 exit={{ opacity: 0, y: -10 }}
 transition={{ duration: 0.25 }}
 className="bg-[#EAF4FF]/40 border border-[#2F6FED]/20 rounded-2xl p-6 h-full space-y-3"
 >
 <h4 className="font-extrabold text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] text-[#2F6FED] flex items-center gap-2">
 <Sparkles size={16} className="text-amber-500 animate-bounce" />
 {content['compare.afterTitle']}
 </h4>
 <p className="text-[13px] sm:text-[14px] text-slate-700 whitespace-pre-line leading-relaxed font-semibold">
 {content['compare.afterText']}
 </p>
 </motion.div>
 )}
 </AnimatePresence>
 </div>

 {/* Benefits Banner */}
 <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4 border-t border-[#EAF4FF] text-center">
 <div className="space-y-1">
 <p className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] font-black text-[#14161A] font-display">+250%</p>
 <p className="text-[13px] sm:text-[14px] font-bold text-[#5B6472]">Просмотров объявлений</p>
 </div>
 <div className="space-y-1">
 <p className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] font-black text-[#14161A] font-display">x3.2</p>
 <p className="text-[13px] sm:text-[14px] font-bold text-[#5B6472]">Конверсия в чаты и контакты</p>
 </div>
 <div className="space-y-1">
 <p className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] font-black text-[#14161A] font-display">0 минут</p>
 <p className="text-[13px] sm:text-[14px] font-bold text-[#5B6472]">Времени на написание текстов</p>
 </div>
 </div>

 </div>

 </div>
 </section>
 {/* СЕКЦИЯ: витрина баннеров Бориса */}
 <section className="py-16 px-4 sm:px-6 md:px-10 bg-white overflow-hidden border-y border-[#EAF4FF]">
 <div className="max-w-7xl mx-auto text-center mb-10">
 <h2 className="text-[28px] sm:text-[36px] font-extrabold text-[#14161A] font-display mb-3 leading-tight">
 Такие баннеры Борис делает за минуту
 </h2>
 <p className="text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] text-[#5B6472] max-w-2xl mx-auto">
 Просто опишите товар — Борис сам создаст продающий баннер под вашу нишу
 </p>
 </div>
 <div className="banner-marquee">
 <div className="banner-marquee-track">
 {[...Array(2)].flatMap((_, dup) =>
 [1,7,4,10,2,8,5,11,3,9,6].map((n) => (
 <div key={dup + "-" + n} className="banner-slide">
 <img src={"/banners-demo/" + n + ".jpg"} alt="Пример баннера Бориса" loading="lazy"
 className="rounded-2xl shadow-lg border border-slate-100" />
 </div>
 ))
 )}
 </div>
 </div>
 </section>
 <style jsx>{`
 .banner-marquee { width: 100%; overflow: hidden; -webkit-mask-image: linear-gradient(90deg, transparent, #000 8%, #000 92%, transparent); mask-image: linear-gradient(90deg, transparent, #000 8%, #000 92%, transparent); }
 .banner-marquee-track { display: flex; gap: 20px; width: max-content; animation: banner-scroll 60s linear infinite; }
 .banner-marquee:hover .banner-marquee-track { animation-play-state: paused; }
 .banner-slide { flex: 0 0 auto; width: 300px; }
 .banner-slide img { width: 100%; height: auto; display: block; }
 @keyframes banner-scroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }
 `}</style>

         {/* PRICING TABLE */}
 <section id="pricing" className="py-20 px-4 sm:px-6 md:px-10 bg-white">
 <div className="max-w-5xl mx-auto space-y-16">
 
 <div className="text-center space-y-4">
 <h2 className="text-[28px] sm:text-[36px] font-extrabold tracking-tight text-[#14161A] font-display">
 {content['pricing.title']}
 </h2>
 <p className="text-[#5B6472] max-w-2xl mx-auto text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] ">
 Три тарифа под любой масштаб — от соло-предпринимателя до агентства. Бесплатный тест, никаких скрытых платежей.
 </p>
 </div>

 <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-stretch max-w-6xl mx-auto">

 {/* Тариф 1 — Автопилот 2.0 */}
 <div className="bg-slate-900 rounded-[24px] p-6 sm:p-9 text-white shadow-xl border border-slate-800 flex flex-col space-y-6 relative overflow-hidden">
 <div className="absolute top-0 right-0 w-36 h-36 bg-blue-500/10 rounded-full blur-3xl" />
 <div className="space-y-3">
 <span className="px-3.5 py-1 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-400 text-[13px] sm:text-[14px] font-bold tracking-wider uppercase">Автопилот 2.0</span>
 <div className="flex items-baseline gap-2">
 <span className="text-[28px] sm:text-[36px] font-black font-display">7 000 ₽</span>
 <span className="text-slate-400 text-[13px] sm:text-[14px] font-semibold">за 30 дней</span>
 </div>
 <p className="text-[13px] sm:text-[14px] text-slate-400 leading-relaxed">Автоматизация ведения рекламы для соло-предпринимателя и малого бизнеса.</p>
 </div>
 <div className="w-full h-[1px] bg-slate-800" />
 <ul className="space-y-3 text-[13px] sm:text-[14px] text-slate-300 flex-1">
 {["Публикация до 1 000 объявлений","15 баннеров-инфографик в месяц","30 шаблонов объявлений","70 стоковых картинок","4 дня бесплатный тест","Докупка опций после лимита"].map((item, idx) => (
 <li key={idx} className="flex items-start gap-2.5">
 <Check size={14} className="text-emerald-400 mt-0.5 shrink-0" />
 <span>{item}</span>
 </li>
 ))}
 </ul>
 <a href="#register" className="w-full py-4 rounded-[8px] font-bold text-center block bg-[#2F6FED] hover:bg-[#2058D0] text-white transition-colors shadow-lg text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px]">
 Начать бесплатный период
 </a>
 </div>

 {/* Тариф 2 — Автопилот MAX */}
 <div className="bg-slate-900 rounded-[24px] p-6 sm:p-9 text-white shadow-xl border-2 border-blue-500/40 flex flex-col space-y-6 relative overflow-hidden">
 <div className="absolute top-0 right-0 w-36 h-36 bg-blue-500/20 rounded-full blur-3xl" />
 <div className="space-y-3">
 <span className="px-3.5 py-1 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-400 text-[13px] sm:text-[14px] font-bold tracking-wider uppercase">Автопилот MAX</span>
 <div className="flex items-baseline gap-2">
 <span className="text-[28px] sm:text-[36px] font-black font-display">14 000 ₽</span>
 <span className="text-slate-400 text-[13px] sm:text-[14px] font-semibold">за 30 дней</span>
 </div>
 <p className="text-[13px] sm:text-[14px] text-slate-400 leading-relaxed">Двойные объёмы для агентств и бизнеса с большим потоком объявлений.</p>
 </div>
 <div className="w-full h-[1px] bg-slate-800" />
 <ul className="space-y-3 text-[13px] sm:text-[14px] text-slate-300 flex-1">
 {["Публикация до 3 000 объявлений","40 баннеров-инфографик в месяц","60 шаблонов объявлений","140 стоковых картинок","Докупка опций после лимита"].map((item, idx) => (
 <li key={idx} className="flex items-start gap-2.5">
 <Check size={14} className="text-emerald-400 mt-0.5 shrink-0" />
 <span>{item}</span>
 </li>
 ))}
 </ul>
 <a href="#register" className="w-full py-4 rounded-[8px] font-bold text-center block bg-white hover:bg-slate-100 text-slate-900 transition-colors shadow-lg text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px]">
 Подключить
 </a>
 </div>

 {/* Тариф 3 — Автопилот Мультиаккаунт */}
 <div className="bg-gradient-to-b from-slate-900 to-[#1a1e2e] rounded-[24px] p-6 sm:p-9 text-white shadow-xl border-2 border-amber-400/40 flex flex-col space-y-6 relative overflow-hidden">
 <div className="absolute top-0 right-0 w-40 h-40 bg-amber-400/10 rounded-full blur-3xl" />
 <div className="flex justify-center -mt-1 mb-1">
 <div className="px-3 py-1 rounded-full bg-amber-400 text-slate-900 text-[13px] sm:text-[14px] font-bold tracking-wide uppercase shadow">Для маркетологов и агентств</div>
 </div>
 <div className="space-y-3">
 <span className="inline-block px-3.5 py-1 rounded-full bg-amber-400/10 border border-amber-400/20 text-amber-300 text-[13px] sm:text-[14px] font-bold tracking-wider uppercase">Автопилот Мультиаккаунт</span>
 <div className="flex items-baseline gap-2 pt-2">
 <span className="text-[22px] font-black font-display">Индивидуально</span>
 </div>
 <p className="text-[13px] sm:text-[14px] text-slate-400 leading-relaxed">Для тех, кто ведёт десятки клиентов. Все аккаунты под контролем из одного окна.</p>
 </div>
 <div className="w-full h-[1px] bg-slate-800" />
 <ul className="space-y-3 text-[13px] sm:text-[14px] text-slate-300 flex-1">
 {["👑 Панель Директора — все клиенты в одном окне","Очередь задач по каждому клиенту","Оповещения по каждой задаче — ничего не пропустите","Управление командой менеджеров","Приоритетная поддержка"].map((item, idx) => (
 <li key={idx} className="flex items-start gap-2.5">
 <Check size={14} className="text-amber-400 mt-0.5 shrink-0" />
 <span>{item}</span>
 </li>
 ))}
 </ul>
 <a href="mailto:eliseev-ko@mail.ru,ostapenko-kirill-86@yandex.ru?subject=Заявка на расчёт — Автопилот Мультиаккаунт&body=Здравствуйте! Хочу рассчитать тариф Мультиаккаунт.%0D%0A%0D%0AКоличество аккаунтов/клиентов: %0D%0AНиша: %0D%0AКонтакт для связи: " className="w-full py-4 rounded-[8px] font-bold text-center block bg-amber-400 hover:bg-amber-300 text-slate-900 transition-colors shadow-lg text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px]">
 Оставить заявку на расчёт
 </a>
 </div>
 </div>

 {/* Не входит в тарифы — общий блок */}
 <div className="max-w-5xl mx-auto mt-8 bg-[#F5FAFF] rounded-[24px] p-6 sm:p-8 border border-[#EAF4FF]">
 <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
 <div className="space-y-3">
 <h4 className="font-extrabold text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] text-[#14161A] flex items-center gap-2 font-display">
 <InfoIcon size={16} className="text-[#5B6472]" />
 Не входит в тарифы (оплачивается отдельно):
 </h4>
 <p className="text-[#5B6472] text-[13px] sm:text-[14px] leading-relaxed">Мы работаем честно и прозрачно. Эти ресурсы оплачиваются отдельно по мере необходимости, чтобы вы не переплачивали.</p>
 <ul className="space-y-2.5 text-[13px] sm:text-[14px] text-[#5B6472] pt-1">
 {["Баннеры для Расширенного/Максимального тарифа площадки","Виртуальный менеджер по продажам","Постинг в соцсети","Создание сайтов","Настройка другой рекламы"].map((item, idx) => (
 <li key={idx} className="flex items-start gap-2">
 <span className="w-1.5 h-1.5 rounded-full bg-[#5B6472] mt-1.5 shrink-0" />
 <span>{item}</span>
 </li>
 ))}
 </ul>
 </div>
 <div className="bg-white rounded-2xl p-5 border border-[#EAF4FF] text-center space-y-2 shadow-sm">
 <span className="text-[13px] sm:text-[14px] font-extrabold text-[#2F6FED] uppercase tracking-wider">Доп. лимиты</span>
 <p className="text-[13px] sm:text-[14px] text-[#5B6472] font-medium leading-normal">После исчерпания лимита любую опцию можно докупить отдельно — баннеры, шаблоны, стоки и объявления, не переходя на старший тариф.</p>
 </div>
 </div>
 </div>

 </div>
 </section>

 {/* TRIAL LIMITS SECTION */}
 <section className="py-16 px-4 sm:px-6 md:px-10 bg-[#F5FAFF] border-y border-[#EAF4FF]">
 <div className="max-w-4xl mx-auto bg-white rounded-[24px] p-6 sm:p-10 shadow-sm border border-[#EAF4FF]">
 <div className="text-center space-y-4 max-w-2xl mx-auto">
 <h3 className="text-[28px] sm:text-[36px] font-extrabold text-[#14161A] font-display">
 {content['trial.title']}
 </h3>
 <p className="text-[#5B6472] text-[13px] sm:text-[14px] ">
 {content['trial.subtitle']}
 </p>
 </div>

 {/* Trial Limit Numbers */}
 <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 mt-10 text-center">
 <div className="bg-[#F5FAFF] rounded-[16px] p-5 border border-[#EAF4FF] space-y-2">
 <span className="text-[28px] sm:text-[36px] sm:text-[36px] font-black text-[#2F6FED] font-display">{content['trial.bannerLimit']}</span>
 <p className="text-[13px] sm:text-[14px] font-bold text-[#5B6472] uppercase tracking-wider">ИИ-баннеров бесплатно</p>
 </div>
 <div className="bg-[#F5FAFF] rounded-[16px] p-5 border border-[#EAF4FF] space-y-2">
 <span className="text-[28px] sm:text-[36px] sm:text-[36px] font-black text-[#2F6FED] font-display">{content['trial.templateLimit']}</span>
 <p className="text-[13px] sm:text-[14px] font-bold text-[#5B6472] uppercase tracking-wider">Шаблонов постов</p>
 </div>
 <div className="bg-[#F5FAFF] rounded-[16px] p-5 border border-[#EAF4FF] space-y-2">
 <span className="text-[28px] sm:text-[36px] sm:text-[36px] font-black text-[#2F6FED] font-display">{content['trial.stockLimit']}</span>
 <p className="text-[13px] sm:text-[14px] font-bold text-[#5B6472] uppercase tracking-wider">Картинок из фотостоков</p>
 </div>
 </div>

 <div className="flex justify-center mt-8">
 <a 
 href="#register"
 className="px-8 py-3.5 rounded-[8px] font-bold text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] text-white shadow-md hover:shadow-lg bg-[#2F6FED] hover:bg-[#2058D0] transition-colors text-center"
 >
 {content['trial.cta']} ({content['trial.days']} дня бесплатно)
 </a>
 </div>
 </div>
 </section>

 {/* FAQ SECTION */}
 <section id="faq" className="py-20 px-4 sm:px-6 md:px-10 bg-white">
 <div className="max-w-4xl mx-auto space-y-12">
 
 <div className="text-center space-y-4">
 <h2 className="text-[28px] sm:text-[36px] font-extrabold tracking-tight text-[#14161A] font-display">
 {content['faq.title']}
 </h2>
 <p className="text-[#5B6472] max-w-2xl mx-auto text-[13px] sm:text-[14px] ">
 Отвечаем на популярные вопросы о безопасности, лимитах и особенностях работы с искусственным интеллектом Борис.
 </p>
 </div>

 <div className="space-y-4">
 {(content['faq.items'] || []).map((faq: any, idx: number) => {
 const isOpen = openFaq === idx;
 return (
 <div 
 key={idx}
 className="bg-white border border-[#EAF4FF] rounded-[12px] overflow-hidden transition-all hover:border-[#2F6FED]/30 shadow-sm"
 >
 <button
 onClick={() => setOpenFaq(isOpen ? null : idx)}
 className="w-full text-left px-6 py-5 flex items-center justify-between gap-4 font-extrabold text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] text-[#14161A] cursor-pointer font-display"
 >
 <span>{faq.q}</span>
 <span className="text-[#5B6472] shrink-0">
 {isOpen ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
 </span>
 </button>
 
 <AnimatePresence>
 {isOpen && (
 <motion.div
 initial={{ height: 0, opacity: 0 }}
 animate={{ height: 'auto', opacity: 1 }}
 exit={{ height: 0, opacity: 0 }}
 transition={{ duration: 0.2 }}
 >
 <div className="px-6 pb-6 pt-1 text-[13px] sm:text-[14px] text-[#5B6472] leading-relaxed border-t border-[#EAF4FF]/50">
 {faq.a}
 </div>
 </motion.div>
 )}
 </AnimatePresence>
 </div>
 );
 })}
 </div>

 </div>
 </section>

 {/* REGISTRATION FORM */}
 <section id="register" className="py-20 px-4 sm:px-6 md:px-10 bg-[#F5FAFF] border-y border-[#EAF4FF] relative overflow-hidden">
 <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-blue-600/5 rounded-full blur-3xl z-0" />
 
 <div className="max-w-md mx-auto relative z-10 space-y-8">
 <div className="text-center space-y-3">
 <div className="flex justify-center">
 <Mascot size={56} interactive={true} />
 </div>
 <h2 className="text-[28px] sm:text-[36px] font-extrabold tracking-tight text-[#14161A] font-display">Создайте личный кабинет</h2>
 <p className="text-[#5B6472] text-[13px] sm:text-[14px]">
 4 дня бесплатного триала со всеми функциями. Подключение за пару кликов.
 </p>
 </div>

 <div className="bg-white rounded-[24px] p-6 sm:p-8 border border-[#EAF4FF] shadow-[0_10px_25px_rgba(47,111,237,0.05)]">
 {regSuccess ? (
 <div className="text-center py-8 space-y-4">
 <div className="w-12 h-12 bg-emerald-500/10 border border-emerald-500/30 text-emerald-600 rounded-2xl flex items-center justify-center mx-auto">
 <Check size={24} />
 </div>
 <h3 className="font-extrabold text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] text-[#14161A] font-display">Регистрация успешна!</h3>
 <p className="text-[13px] sm:text-[14px] text-[#5B6472] leading-relaxed">
 Добро пожаловать в Борис! Мы создали ваш аккаунт и активировали 4-дневный триал. Переходим в личный кабинет...
 </p>
 </div>
 ) : (
 <form onSubmit={handleRegisterSubmit} className="space-y-4">
 <div className="space-y-1.5">
 <label className="text-[13px] sm:text-[14px] font-bold text-[#5B6472] uppercase tracking-wider">Электронная почта *</label>
 <div className="relative">
 <Mail size={16} className="absolute left-3 top-3.5 text-[#5B6472]" />
 <input 
 type="email" 
 required
 placeholder="name@company.ru"
 value={regEmail}
 onChange={(e) => setRegEmail(e.target.value)}
 className="w-full bg-[#F5FAFF] border border-[#EAF4FF] focus:border-[#2F6FED] rounded-[8px] py-3 pl-10 pr-4 text-[13px] sm:text-[14px] text-[#14161A] focus:outline-none transition-colors font-mono"
 />
 </div>
 </div>

 <div className="space-y-1.5">
 <label className="text-[13px] sm:text-[14px] font-bold text-[#5B6472] uppercase tracking-wider">Пароль *</label>
 <div className="relative">
 <Lock size={16} className="absolute left-3 top-3.5 text-[#5B6472]" />
 <input 
 type="password" 
 required
 placeholder="••••••••"
 value={regPassword}
 onChange={(e) => setRegPassword(e.target.value)}
 className="w-full bg-[#F5FAFF] border border-[#EAF4FF] focus:border-[#2F6FED] rounded-[8px] py-3 pl-10 pr-4 text-[13px] sm:text-[14px] text-[#14161A] focus:outline-none transition-colors"
 />
 </div>
 </div>

 <div className="space-y-1.5">
 <label className="text-[13px] sm:text-[14px] font-bold text-[#5B6472] uppercase tracking-wider">Название компании (опционально)</label>
 <div className="relative">
 <Building size={16} className="absolute left-3 top-3.5 text-[#5B6472]" />
 <input 
 type="text" 
 placeholder="ООО СтройПлит"
 value={regCompany}
 onChange={(e) => setRegCompany(e.target.value)}
 className="w-full bg-[#F5FAFF] border border-[#EAF4FF] focus:border-[#2F6FED] rounded-[8px] py-3 pl-10 pr-4 text-[13px] sm:text-[14px] text-[#14161A] focus:outline-none transition-colors"
 />
 </div>
 </div>

 <div className="space-y-1.5">
 <label className="text-[13px] sm:text-[14px] font-bold text-[#5B6472] uppercase tracking-wider">Сколько Avito-аккаунтов планируете вести?</label>
 <select
 value={regPlanned}
 onChange={(e) => setRegPlanned(e.target.value)}
 className="w-full bg-[#F5FAFF] border border-[#EAF4FF] focus:border-[#2F6FED] rounded-[8px] py-3 px-4 text-[13px] sm:text-[14px] text-[#14161A] focus:outline-none transition-colors"
 >
 <option value="1">1 — свой бизнес</option>
 <option value="2-4">2–4 аккаунта</option>
 <option value="5-14">5–14 аккаунтов</option>
 <option value="15+">15 и больше</option>
 </select>
 </div>

 <div className="flex items-start gap-2.5 pt-2">
 <input 
 id="consent-checkbox"
 type="checkbox" 
 required
 checked={consentChecked}
 onChange={(e) => setConsentChecked(e.target.checked)}
 className="mt-0.5 w-4 h-4 text-[#2F6FED] border-[#EAF4FF] rounded focus:ring-[#2F6FED]"
 />
 <label htmlFor="consent-checkbox" className="text-[13px] sm:text-[14px] text-[#5B6472] leading-normal select-none text-left">
 Я согласен с <a href="/oferta" target="_blank" className="text-[#2F6FED] underline hover:text-[#2058D0] font-bold">Публичной офертой</a> и <a href="/privacy" target="_blank" className="text-[#2F6FED] underline hover:text-[#2058D0] font-bold">Политикой конфиденциальности</a>
 </label>
 </div>

 {regError && (
   <div className="text-[13px] sm:text-[14px] text-red-600 bg-red-50 border border-red-200 rounded-[8px] py-3 px-4 text-center mb-2">{regError}</div>
 )}
 <TurnstileBox onToken={setRegToken} resetKey={regCaptchaKey} />
 <button
 type="submit"
 disabled={!consentChecked || !regToken}
 className={`w-full py-4 rounded-[8px] font-bold text-center block text-white transition-colors shadow-lg text-[13px] sm:text-[14px] ${
 consentChecked 
 ? 'bg-[#2F6FED] hover:bg-[#2058D0] cursor-pointer' 
 : 'bg-slate-300 border border-slate-200 cursor-not-allowed opacity-75'
 }`}
 >
 Зарегистрироваться и запустить Бориса
 </button>
 
 <p className="text-[13px] sm:text-[14px] text-[#5B6472] text-center leading-normal">
 Регистрируясь, вы соглашаетесь с условиями оферты и обработки персональных данных.
 </p>
 </form>
 )}
 </div>
 </div>
 </section>

 {/* FOOTER */}
 <footer className="bg-white text-[#5B6472] py-12 px-4 sm:px-6 md:px-10 border-t border-[#E5E7EB]">
 <div className="max-w-7xl mx-auto space-y-6 text-center md:text-left">
 <div className="flex flex-col md:flex-row justify-between items-center gap-6">
 <div className="flex items-center gap-2">
 <Mascot size={28} interactive={false} />
 <span className="font-extrabold text-[13px] sm:text-[14px] sm:text-[13px] sm:text-[14px] sm:text-[14px] sm:text-[18px] text-[#14161A] font-display">БОРИС</span>
 </div>

 <div className="flex flex-wrap justify-center gap-4 text-[13px] sm:text-[14px] font-semibold">
 <a href="/oferta" className="text-[#5B6472] hover:text-[#2F6FED] transition-colors">Условия использования</a>
 <span className="text-[#E5E7EB] hidden sm:inline">|</span>
 <a href="/privacy" className="text-[#5B6472] hover:text-[#2F6FED] transition-colors">Политика конфиденциальности</a>
 </div>

 <p className="text-[13px] sm:text-[14px] text-[#5B6472]">
 {content['footer.contacts']}
 </p>
 </div>

 <div className="h-[1px] bg-[#EAF4FF]" />

 <p className="text-[13px] sm:text-[14px] text-[#5B6472] leading-relaxed text-center">
 {content['footer.disclaimer']}
 </p>

 <p className="text-[13px] sm:text-[14px] text-[#5B6472] text-center pt-2">
 {content['footer.copyright']}
 </p>
 </div>
 </footer>
 </div>
 );
};

export default LandingPage;
