"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/app/components/shell/AppShell";
import { useAccounts } from "@/app/lib/AccountContext";
import { Alert, Button, Card, EmptyState, Icon, Kpi, KpiRow, Page, type IconName } from "@/app/ui";
import { color, font, space } from "@/app/ui/tokens";

function authHeaders() {
  const token = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return { Authorization: `Bearer ${token}` };
}
async function api(path: string, init?: RequestInit) {
  const res = await fetch(path, { ...init, headers: { ...authHeaders(), ...(init?.headers || {}) }, cache: "no-store" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.status === "error") throw new Error(data?.message || data?.detail || `HTTP ${res.status}`);
  return data;
}
function rub(v: unknown) { return typeof v === "number" && Number.isFinite(v) ? `${new Intl.NumberFormat("ru-RU").format(v)} ₽` : "—"; }
function date(v: unknown) { if (!v) return "—"; const d = new Date(String(v)); return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleDateString("ru-RU"); }

type ProductGroup = { key: string; title: string; subtitle: string; icon: IconName; tone: string; items: Array<[string, any]> };
function groupFor(code: string) {
  if (code.startsWith("tariff_")) return "tariffs";
  if (code.startsWith("ban")) return "banners";
  if (code.startsWith("msg")) return "mop";
  if (code.startsWith("rop")) return "rop";
  if (code.startsWith("post")) return "social";
  if (code.startsWith("sub")) return "autopilot";
  if (code.startsWith("prof")) return "profile";
  if (code.startsWith("phone")) return "phone";
  return "other";
}
const GROUP_META: Record<string, Omit<ProductGroup,"items">> = {
  tariffs: { key:"tariffs", title:"Основные тарифы BORIS", subtitle:"Реальная стоимость и лимиты текущих тарифов", icon:"award", tone:"green" },
  autopilot: { key:"autopilot", title:"Автопилот BORIS", subtitle:"Тарифы для автоматизации рекламы и контроля целей", icon:"zap", tone:"blue" },
  mop: { key:"mop", title:"ИИ-менеджер МОП", subtitle:"Пакеты сообщений и работа с обращениями клиентов", icon:"messages", tone:"cyan" },
  rop: { key:"rop", title:"ИИ-руководитель РОП", subtitle:"Контроль продаж, разбор звонков и дополнительные минуты", icon:"chart", tone:"violet" },
  banners: { key:"banners", title:"Баннеры", subtitle:"Пакеты рекламных изображений для объявлений", icon:"image", tone:"pink" },
  social: { key:"social", title:"Социальные сети", subtitle:"Публикации во ВКонтакте и Telegram", icon:"send", tone:"teal" },
  profile: { key:"profile", title:"Профиль Avito", subtitle:"Оформление и расширение возможностей профиля", icon:"user", tone:"orange" },
  phone: { key:"phone", title:"BORIS Phone", subtitle:"Телефония BORIS, веб-телефон, CRM, записи и ИИ-анализ", icon:"phone", tone:"blue" },
  other: { key:"other", title:"Другие услуги", subtitle:"Дополнительные активные услуги BORIS", icon:"star", tone:"blue" },
};

export default function BillingPage() {
  const { selectedAccountId, selectedAccount, loading: accountsLoading } = useAccounts();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [payingPack, setPayingPack] = useState("");
  const [buyingPack, setBuyingPack] = useState("");
  const [phoneRequested, setPhoneRequested] = useState(false);
  const [phoneCommercial, setPhoneCommercial] = useState<any>(null);
  const [phonePriceDraft, setPhonePriceDraft] = useState("");
  const [phoneCommercialBusy, setPhoneCommercialBusy] = useState(false);
  const [phoneCommercialMessage, setPhoneCommercialMessage] = useState("");
  const [invoicePack, setInvoicePack] = useState("");
  const [invoiceBusy, setInvoiceBusy] = useState(false);
  const [invoiceResult, setInvoiceResult] = useState<any>(null);
  const [payerName, setPayerName] = useState("");
  const [payerInn, setPayerInn] = useState("");
  const [openGroup, setOpenGroup] = useState("tariffs");

  const load = useCallback(async () => {
    if (!selectedAccountId) { setData(null); return; }
    setLoading(true); setError("");
    const q = encodeURIComponent(selectedAccountId);
    try {
      const [billing, payment, prices, history, limits, wallet, phoneSettings] = await Promise.all([
        api(`/api/billing/status?account_id=${q}`), api(`/api/payments/status?account_id=${q}`),
        api(`/api/payments/prices?account_id=${q}`), api(`/api/payments/history?account_id=${q}`),
        api(`/api/calltracking/all_limits?account_id=${q}`), api(`/api/wallet/balance?account_id=${q}`),
        api("/api/payments/phone-commercial-settings").catch(() => null),
      ]);
      setData({ billing: billing.billing || null, payment: payment.payment || null, prices: prices["цены"] || {}, segment: prices["сегмент"] || null, history: Array.isArray(history.history) ? history.history : [], limits, wallet });
      setPhoneCommercial(phoneSettings);
      setPhonePriceDraft(phoneSettings?.price_rub ? String(phoneSettings.price_rub) : "");
    } catch (e: any) {
      setData(null); setError(e?.message ? `Не удалось загрузить финансовые данные: ${e.message}` : "Не удалось загрузить финансовые данные");
    } finally { setLoading(false); }
  }, [selectedAccountId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (typeof window === "undefined") return;
    setPhoneRequested(new URLSearchParams(window.location.search).get("product") === "phone");
  }, []);

  async function openPayment(pack: string) {
    if (!selectedAccountId || !pack || payingPack) return;
    setPayingPack(pack); setError("");
    try {
      const result = await api(`/api/payments/robokassa/link?account_id=${encodeURIComponent(selectedAccountId)}&pack=${encodeURIComponent(pack)}`);
      if (!result?.url) throw new Error("Ссылка на оплату не получена");
      window.location.href = result.url;
    } catch (e: any) { setError(e?.message || "Не удалось открыть оплату"); }
    finally { setPayingPack(""); }
  }

  async function purchaseFromBalance(pack: string) {
    if (!selectedAccountId || !pack || buyingPack) return;
    setBuyingPack(pack); setError("");
    try {
      const seed = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
      const result = await api("/api/wallet/purchase", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_id: selectedAccountId,
          pack,
          purchase_id: `web:${seed}`,
        }),
      });
      if (result?.status !== "ok") throw new Error(result?.message || "Покупка не выполнена");
      await load();
    } catch (e: any) {
      setError(e?.message || "Не удалось оплатить услугу с баланса");
    } finally {
      setBuyingPack("");
    }
  }

  async function savePhoneCommercial(enabled: boolean) {
    if (phoneCommercialBusy) return;
    setPhoneCommercialBusy(true); setPhoneCommercialMessage(""); setError("");
    try {
      const price = Number(phonePriceDraft.replace(/[^0-9]/g, ""));
      if (enabled && (!Number.isInteger(price) || price <= 0)) {
        setPhoneCommercialMessage("Укажите положительную цену BORIS Phone в рублях.");
        return;
      }
      const result = await api("/api/payments/phone-commercial-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled,
          price_rub: enabled ? price : null,
          confirm: true,
        }),
      });
      setPhoneCommercial(result);
      setPhonePriceDraft(result?.price_rub ? String(result.price_rub) : "");
      setPhoneCommercialMessage(enabled
        ? `Цена сохранена: ${rub(Number(result?.price_rub))} за 30 дней. Каталог обновлён без перезапуска.`
        : "Продажа BORIS Phone отключена. Существующие оплаченные доступы не отзываются.");
      await load();
    } catch (e: any) {
      setPhoneCommercialMessage(e?.message || "Не удалось сохранить коммерческую цену Phone.");
    } finally {
      setPhoneCommercialBusy(false);
    }
  }

  async function createInvoice() {
    if (!selectedAccountId || !invoicePack || !payerName.trim() || invoiceBusy) return;
    setInvoiceBusy(true); setInvoiceResult(null); setError("");
    try {
      const result = await api("/api/wallet/invoice/create", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_id: selectedAccountId, pack: invoicePack, payer_name: payerName.trim(), payer_inn: payerInn.trim() }),
      });
      setInvoiceResult(result?.invoice || null);
    } catch (e: any) { setError(e?.message || "Не удалось выставить счёт"); }
    finally { setInvoiceBusy(false); }
  }

  async function downloadInvoice(number: string) {
    if (!number) return;
    setError("");
    try {
      const res = await fetch(`/api/wallet/invoice/pdf?number=${encodeURIComponent(number)}`, { headers: authHeaders(), cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const type = res.headers.get("content-type") || "";
      if (!type.includes("application/pdf")) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.message || "PDF счёта не получен");
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url; a.download = `Счёт ${number}.pdf`; a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) { setError(e?.message || "Не удалось скачать счёт"); }
  }

  const b = data?.billing;
  const usage = b?.usage && typeof b.usage === "object" ? Object.entries(b.usage) : [];
  const modules = useMemo(() => {
    if (!data?.limits) return [] as any[];
    const out: any[] = [];
    const mop = data.limits.mop;
    if (mop) out.push({ name:"МОП · ИИ-менеджер", active:Boolean(mop.active), used:mop.used, purchased:mop.purchased, left:mop.left, price:data.prices?.msg1700?.sum, icon:"messages" as IconName, tone:"cyan" });
    const rop = data.limits.rop;
    if (rop) { const mins = rop.minutes || {}; out.push({ name:"РОП · ИИ-руководитель", active:Boolean(data.limits.unlimited || mins.active || (mins.left ?? 0) > 0), used:mins.used, purchased:mins.purchased, left:mins.left, price:data.prices?.rop1500?.sum, icon:"chart" as IconName, tone:"violet" }); }
    const social = data.limits.social;
    if (social) out.push({ name:"Социальные сети", active:Number(social.active || 0) > 0, used:social.posts_today, purchased:social.per_day, left:social.left_today, price:null, icon:"send" as IconName, tone:"teal" });
    return out;
  }, [data]);

  const productGroups = useMemo<ProductGroup[]>(() => {
    const buckets = new Map<string, Array<[string,any]>>();
    Object.entries(data?.prices || {}).forEach(([code,item]) => { const key=groupFor(code); const arr=buckets.get(key)||[]; arr.push([code,item]); buckets.set(key,arr); });
    return ["tariffs","phone","autopilot","mop","rop","banners","social","profile","other"].filter(k=>buckets.has(k)).map(k=>({ ...GROUP_META[k], items:buckets.get(k)! }));
  }, [data]);

  useEffect(() => { if (productGroups.length && !productGroups.some(g=>g.key===openGroup)) setOpenGroup(productGroups[0].key); }, [productGroups, openGroup]);
  useEffect(() => { if (phoneRequested && productGroups.some(g=>g.key==="phone")) setOpenGroup("phone"); }, [phoneRequested, productGroups]);
  const activeProducts = productGroups.find(g=>g.key===openGroup) || productGroups[0];
  const planKnown = Boolean(b?.tier && b.tier !== "none");
  const hasPlan = Boolean(planKnown && b?.active !== false);
  const expiredPlan = Boolean(planKnown && b?.blocked_reason === "subscription_expired");
  const isTrial = b?.tier === "trial" && hasPlan;
  const activeModules = modules.filter((m)=>m.active);
  const catalogCount = productGroups.reduce((sum,g)=>sum+g.items.length,0);
  const primaryUsage = usage.find(([,raw]: [string,any])=>Number(raw?.limit)>0) as [string,any] | undefined;
  const primaryUsagePercent = primaryUsage ? Math.min(100, Math.round((Number(primaryUsage[1]?.used || 0) / Number(primaryUsage[1]?.limit || 1)) * 100)) : 0;
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior:"smooth", block:"start" });

  return (
    <AppShell active="billing" title="Тарифы и финансы" subtitle="Тариф, модули, реальные способы оплаты и история выбранного аккаунта">
      <Page className="boris-billing-reference-page">
        {error ? <Alert kind="danger">{error}</Alert> : null}
        {phoneRequested && data && !data?.prices?.phone_monthly ? <Alert kind="warning">BORIS Phone подготовлен к оплате, но коммерческая цена ещё не задана в production-прайсе. BORIS не показывает фиктивную цену и не активирует Phone бесплатно.</Alert> : null}
        {!accountsLoading && !selectedAccountId ? <EmptyState title="Не выбран аккаунт" hint="Выберите аккаунт в верхней панели." /> : null}
        {selectedAccountId ? <>
          <div className="boris-billing-intro">
            <div><h1>Тарифы и финансы</h1><p>Управляйте тарифом, оплачивайте услуги и следите за лимитами выбранного аккаунта.</p></div>
            <Button kind="ghost" onClick={()=>void load()} disabled={loading}><Icon name="refresh" size={16}/>{loading?" Обновляем…":" Обновить"}</Button>
          </div>

          <div className="boris-billing-summary-grid">
            <Card className="boris-billing-summary-card tone-green"><div className="boris-billing-summary-icon"><Icon name="award" size={23}/></div><div><small>Текущий тариф</small><strong>{loading?"Загрузка…":planKnown?b?.tier_name:"Не подключён"}</strong>{hasPlan?<span className="is-good">Активен</span>:expiredPlan?<span>Срок оплаты закончился</span>:<span>Можно выбрать ниже</span>}</div></Card>
            <Card className="boris-billing-summary-card tone-green"><div className="boris-billing-summary-icon"><Icon name="finance" size={23}/></div><div><small>Баланс</small><strong>{data?.wallet?rub(Number(data.wallet.balance||0)):"—"}</strong><button onClick={()=>scrollTo("billing-documents")}>Счета и документы →</button></div></Card>
            {(data?.payment?.paid_until||b?.paid_until||(hasPlan&&b?.period_end))?<Card className="boris-billing-summary-card tone-blue"><div className="boris-billing-summary-icon"><Icon name="calendar" size={23}/></div><div><small>{expiredPlan?"Оплата закончилась":data?.payment?.paid_until?"Оплачено до":"Текущий период до"}</small><strong>{date(data?.payment?.paid_until||b?.paid_until||b?.period_end)}</strong><button onClick={()=>scrollTo("billing-overview")}>Подробнее →</button></div></Card>:null}
            <Card className="boris-billing-summary-card tone-violet"><div className="boris-billing-summary-icon"><Icon name="layers" size={23}/></div><div><small>Активных модулей</small><strong>{activeModules.length}{catalogCount?` из ${catalogCount}`:""}</strong><button onClick={()=>scrollTo("billing-services")}>Все услуги →</button></div></Card>
          </div>

          <nav className="boris-billing-section-tabs" aria-label="Разделы финансов">
            <button onClick={()=>scrollTo("billing-overview")}>Обзор</button><button onClick={()=>scrollTo("billing-services")}>Услуги и оплата</button><button onClick={()=>scrollTo("billing-limits")}>Лимиты и использование</button><button onClick={()=>scrollTo("billing-history")}>История платежей</button><button onClick={()=>scrollTo("billing-documents")}>Счета и документы</button>
          </nav>

          <section id="billing-overview" className="boris-billing-overview-grid">
            <Card className="boris-billing-tariff-detail">
              <div className="boris-billing-card-heading"><h2>Ваш тариф</h2>{hasPlan?<span>Активен</span>:expiredPlan?<span>Нужно продлить</span>:null}</div>
              <h3>{planKnown?b?.tier_name:"Основной тариф пока не подключён"}</h3><p>{hasPlan?"Текущий пакет и его фактические лимиты для выбранного аккаунта.":expiredPlan?"Оплаченный период закончился. BORIS остановил платные действия до продления тарифа.":"Выберите подходящий тариф или отдельный модуль в каталоге услуг ниже."}</p>
              {planKnown?<div className="boris-billing-tariff-price"><b>{b?.tier==="trial"?"Бесплатно":rub(Number(data?.prices?.[b?.tier]?.sum ?? b?.price_rub ?? 0))}</b><span>{expiredPlan?"для продления на 30 дней":b?.tier==="trial"?"4 дня после подтверждения аккаунта":"за текущий 30-дневный тариф"}</span></div>:null}
              <div className="boris-billing-detail-list">{planKnown&&b?.period_start?<div><span>Период действия</span><b>{date(b.period_start)} — {date(b?.paid_until||b.period_end)}</b></div>:null}{data?.payment?.has_payment?<div><span>Сумма текущей оплаты</span><b>{rub(Number(data.payment.amount_rub||0))}</b></div>:null}{hasPlan&&b?.days_left!=null?<div><span>До конца периода</span><b>{b.days_left} дн.</b></div>:null}<div><span>{isTrial?"Статус доступа":"Статус оплаты"}</span><b>{expiredPlan?"Срок оплаты закончился":isTrial?"Пробный период активен":data?.payment?.has_payment?(data.payment.overdue?"Требует оплаты":"Оплачено"):hasPlan?"Тариф активен":"Платёж по периоду не найден"}</b></div></div>
              {primaryUsage?<div className="boris-billing-primary-usage"><div><span>{primaryUsage[1]?.name||primaryUsage[0]}</span><b>{primaryUsage[1]?.used??"—"} из {primaryUsage[1]?.limit??"—"}</b></div><div className="boris-billing-progress"><i style={{width:`${primaryUsagePercent}%`}}/></div></div>:null}
              <div className="boris-billing-tariff-actions"><Button kind="ghost" onClick={()=>scrollTo("billing-services")}>Выбрать услуги</Button><Button onClick={()=>scrollTo("billing-services")}>Управление тарифом</Button></div>
            </Card>
            <Card className="boris-billing-included-card"><div className="boris-billing-card-heading"><h2>Что подключено</h2></div><div className="boris-billing-included-list">{activeModules.map((m)=><div key={m.name}><span><Icon name="check" size={14}/></span><b>{m.name}</b></div>)}{!loading&&!activeModules.length?<p>Активные модули пока не найдены. Их можно подключить в каталоге услуг.</p>:null}</div></Card>
            <Card className="boris-billing-quick-card"><div className="boris-billing-card-heading"><h2>Быстрые действия</h2></div><button className="tone-blue" onClick={()=>scrollTo("billing-services")}><span><Icon name="plus" size={18}/></span><div><b>Подключить услугу</b><small>Выберите нужный модуль</small></div><Icon name="arrow" size={16}/></button><button className="tone-orange" onClick={()=>scrollTo("billing-documents")}><span><Icon name="file" size={18}/></span><div><b>Выставить счёт</b><small>Для ИП и организаций</small></div><Icon name="arrow" size={16}/></button><button className="tone-violet" onClick={()=>scrollTo("billing-history")}><span><Icon name="clock" size={18}/></span><div><b>История платежей</b><small>Все реальные операции</small></div><Icon name="arrow" size={16}/></button></Card>
          </section>

          <div className="boris-billing-reference-grid">
          <Card className="boris-billing-plan-card">
            <div className="boris-billing-plan-head">
              <div><span>{selectedAccount?.name || selectedAccountId}</span><h2>{loading ? "Загрузка…" : b?.tier_name || "Основной тариф пока не подключён"}</h2><p>{b?.period_start ? `Период: ${date(b.period_start)} — ${date(b.period_end)}` : "Подключите подходящий тариф или модуль в каталоге ниже."}</p></div>
              <Button kind="ghost" onClick={() => void load()} disabled={loading}>{loading ? "Обновляем…" : "Обновить"}</Button>
            </div>
            <KpiRow>
              <Kpi value={b?.tier_name || "—"} label="Текущий тариф" />
              <Kpi value={b?.days_left != null ? `${b.days_left} дн.` : "—"} label="До обновления" />
              <Kpi value={isTrial ? "Активен" : data?.payment?.has_payment ? (data.payment.overdue ? "Просрочено" : "Оплачено") : "Нет данных"} label={isTrial?"Пробный период":"Статус оплаты"} />
              <Kpi value={String(modules.filter((m)=>m.active).length)} label="Активных модулей" />
              <Kpi value={data?.wallet ? rub(Number(data.wallet.balance || 0)) : "—"} label="Баланс" />
            </KpiRow>
          </Card>

          <Card className="boris-billing-modules-card">
            <div className="boris-section-title"><div><span>Подключено сейчас</span><h2>Модули BORIS</h2></div></div>
            <div className="boris-billing-module-grid">
              {modules.map((m) => <div className={`boris-billing-module-card tone-${m.tone}`} key={m.name}>
                <div className="boris-billing-module-icon"><Icon name={m.icon} size={21}/></div>
                <div><b>{m.name}</b><small>{m.active ? "Подключён" : "Не активен"}</small></div>
                <strong>{rub(m.price)}</strong>
                <div className="boris-billing-usage"><span>{m.used ?? "—"} / {m.purchased ?? "—"}</span><small>Осталось: {m.left ?? "—"}</small></div>
              </div>)}
              {!loading && !modules.length ? <EmptyState title="Данные модулей не предоставлены" hint="BORIS не подставляет выдуманные подключения." /> : null}
            </div>
          </Card>

          <Card id="billing-documents" className="boris-billing-payment-card">
            <div className="boris-section-title"><div><span>Текущий период</span><h2>Оплата</h2></div><div className="boris-billing-payment-icon"><Icon name="card" size={23}/></div></div>
            {data?.payment?.has_payment ? <div className="boris-billing-payment-list">
              <div><span>Оплачено</span><b>{rub(Number(data.payment.amount_rub || 0))}</b></div><div><span>Дата оплаты</span><b>{date(data.payment.paid_at)}</b></div><div><span>Действует до</span><b>{date(data.payment.paid_until)}</b></div><div><span>Осталось</span><b>{data.payment.days_left} дн.</b></div>
            </div> : <EmptyState title="Платежей по текущему периоду пока нет" hint="Оплатить услугу можно в каталоге ниже или выставить счёт через существующий платёжный контур BORIS." />}
            <div className="boris-billing-invoice-box">
              <div><b>Счёт для безналичной оплаты</b><small>Для ИП и организаций. Выберите услугу и укажите плательщика — BORIS сформирует счёт через существующий рабочий контур.</small></div>
              <select value={invoicePack} onChange={(e)=>{setInvoicePack(e.target.value);setInvoiceResult(null);}} aria-label="Услуга для счёта">
                <option value="">Выберите услугу</option>
                {Object.entries(data?.prices || {}).map(([code,item]: [string,any])=><option key={code} value={code}>{item?.title || "Услуга BORIS"} — {rub(Number(item?.sum))}</option>)}
              </select>
              <div className="boris-billing-invoice-fields">
                <input value={payerName} onChange={(e)=>setPayerName(e.target.value)} placeholder="Название ИП или организации" aria-label="Плательщик" />
                <input value={payerInn} onChange={(e)=>setPayerInn(e.target.value.replace(/[^0-9]/g,""))} placeholder="ИНН (необязательно)" inputMode="numeric" aria-label="ИНН плательщика" />
              </div>
              <Button kind="ghost" onClick={()=>void createInvoice()} disabled={!invoicePack || !payerName.trim() || invoiceBusy}>{invoiceBusy?"Выставляем…":"Выставить счёт"}</Button>
              {invoiceResult ? <div className="boris-billing-invoice-result"><b>Счёт {invoiceResult.number}</b><span>{invoiceResult.title} · {rub(Number(invoiceResult.amount))}</span><small>Назначение: {invoiceResult.purpose}</small><Button kind="ghost" onClick={()=>void downloadInvoice(invoiceResult.number)}>Скачать PDF</Button></div> : null}
            </div>
          </Card>

          <Card id="billing-limits" className="boris-billing-limits-card">
            <div className="boris-section-title"><div><span>Использование</span><h2>Лимиты и остатки</h2><p>Фактический расход по выбранному аккаунту — всё видно прямо здесь.</p></div></div>
            <div>{usage.length ? <KpiRow>{usage.map(([key, raw]: [string, any]) => <Kpi key={key} value={raw?.limit > 0 ? `${raw.used ?? 0} / ${raw.limit}${raw.extra ? ` + ${raw.extra}` : ""}` : raw?.used ?? "—"} label={raw?.name || key} />)}</KpiRow> : <EmptyState title="Лимиты не предоставлены" hint="Backend не вернул usage для выбранного аккаунта." />}</div>
          </Card>

          {phoneCommercial ? <Card className="boris-billing-payment-card">
            <div className="boris-section-title">
              <div>
                <span>Только владелец платформы</span>
                <h2>Коммерческая цена BORIS Phone</h2>
                <p>Один источник цены для каталога, Робокассы и оплаты с баланса. Без заданной цены Phone не продаётся.</p>
              </div>
              <div className="boris-billing-payment-icon"><Icon name="phone" size={23}/></div>
            </div>
            <div className="boris-billing-invoice-box">
              <div>
                <b>{phoneCommercial.enabled ? `Продажа включена · ${rub(Number(phoneCommercial.price_rub))} / 30 дней` : "Продажа сейчас выключена"}</b>
                <small>Минуты связи оплачиваются отдельно. Изменение цены не отзывает уже оплаченные доступы.</small>
              </div>
              <div className="boris-billing-invoice-fields">
                <input
                  value={phonePriceDraft}
                  onChange={(e)=>setPhonePriceDraft(e.target.value.replace(/[^0-9]/g,""))}
                  placeholder="Цена за 30 дней, ₽"
                  inputMode="numeric"
                  aria-label="Цена BORIS Phone за 30 дней"
                />
              </div>
              <div className="boris-billing-tariff-actions">
                <Button onClick={()=>void savePhoneCommercial(true)} disabled={phoneCommercialBusy || !phonePriceDraft}>
                  {phoneCommercialBusy ? "Сохраняем…" : "Сохранить и включить"}
                </Button>
                <Button kind="ghost" onClick={()=>void savePhoneCommercial(false)} disabled={phoneCommercialBusy || !phoneCommercial.enabled}>
                  Отключить продажу
                </Button>
              </div>
              {phoneCommercialMessage ? <small>{phoneCommercialMessage}</small> : null}
            </div>
          </Card> : null}

          <Card id="billing-services" className="boris-billing-services-card">
            <div className="boris-section-title"><div><span>Каталог услуг</span><h2>Что можно подключить</h2><p>Все позиции и цены загружаются из текущего рабочего прайса. Оплатить можно через Robokassa или с баланса выбранного клиента.</p></div></div>
            <div className="boris-billing-group-tabs">{productGroups.map(g=><button key={g.key} className={openGroup===g.key?"active":""} onClick={()=>setOpenGroup(g.key)}><span className={`tone-${g.tone}`}><Icon name={g.icon} size={18}/></span>{g.title}<b>{g.items.length}</b></button>)}</div>
            {activeProducts ? <div className="boris-billing-group-head"><div className={`boris-billing-group-icon tone-${activeProducts.tone}`}><Icon name={activeProducts.icon} size={24}/></div><div><h3>{activeProducts.title}</h3><p>{activeProducts.subtitle}</p></div></div> : null}
            <div className="boris-billing-payment-catalog">
              {(activeProducts?.items || []).map(([code,item]) => <div className={`boris-billing-payment-product tone-${activeProducts?.tone || "blue"}`} key={code}>
                <div className="boris-payment-product-main">
                  <div className="boris-payment-product-head"><span className="boris-payment-mini-icon"><Icon name={activeProducts?.icon || "star"} size={18}/></span><b>{item?.title || "Услуга BORIS"}</b></div>
                  {item?.desc ? <p>{item.desc}</p> : null}
                  <small>{item?.days ? `${item.days} дней` : item?.addon ? "До конца текущего периода" : "Разовая услуга"}</small>
                </div>
                <strong>{rub(Number(item?.sum))}</strong>
                <Button onClick={()=>void openPayment(code)} disabled={!!payingPack||!!buyingPack}>{payingPack===code?"Открываю оплату…":"Оплатить картой"}</Button>
                <Button kind="ghost" onClick={()=>void purchaseFromBalance(code)} disabled={!!payingPack||!!buyingPack||Number(data?.wallet?.balance||0)<Number(item?.sum||0)}>{buyingPack===code?"Списываю…":Number(data?.wallet?.balance||0)>=Number(item?.sum||0)?"С баланса":"Не хватает на балансе"}</Button>
              </div>)}
            </div>
          </Card>

          <Card id="billing-history" className="boris-billing-history-card">
            <div className="boris-section-title"><div><span>История</span><h2>Оплаты</h2></div></div>
            <div className="boris-billing-list">{(data?.history || []).slice().reverse().slice(0,20).map((h:any,i:number)=><div key={`${h.at || "row"}-${i}`}><span><b>{h.title || h.pack || "Оплата"}</b><small>{date(h.at)}</small></span><b>{rub(Number(h.amount_rub || 0))}</b></div>)}
              {!loading && !(data?.history || []).length ? <EmptyState title="История пока пуста" hint="После оплаты здесь появятся операции по выбранному аккаунту." /> : null}
            </div>
          </Card>
        </div></> : null}
      </Page>
    </AppShell>
  );
}
