"use client";

import { useEffect, useMemo, useState } from "react";
import AppShell from "@/app/components/shell/AppShell";

type Platform={key:string;name:string;url:string;mode:string;priority:number;audience:string;registration:string;notes:string};
type Offer={key:string;title:string;setup_from:number;setup_to:number;monthly_from:number;monthly_to:number;short_pitch:string};
type Draft={id:string;platform:string;platform_name:string;topic:string;title:string;text:string;status:string;site_url:string;variant:number;publication_url?:string};
type BootstrapItem={platform:string;name:string;url:string;account_url?:string;checkpoint:string;status?:string;last_error?:string;action:string;human_action_required:boolean;owner_action_required:boolean;executor_role:string;one_time:boolean;reusable_across_clients:boolean;supported_client_formats?:string[];maturity_requirements?:Record<string,string|number|boolean>;starts_warmup_after_bootstrap?:boolean;after_completion:string;priority:number;urgent_for_active_projects?:boolean;relevant_projects?:number;potential_slots?:number;max_relevance?:number;requires_warmup_after_bootstrap?:boolean;relevant_sites?:string[]};
type BootstrapQueue={needed:number;needed_for_active_projects?:number;potential_slots_for_active_projects?:number;potential_slots_immediate_after_bootstrap?:number;potential_slots_after_warmup?:number;warming_after_bootstrap?:number;owner_action_required:boolean;human_action_required:boolean;reusable_across_clients:boolean;executor_role:string;by_checkpoint?:Record<string,number>;items:BootstrapItem[]};
type CoverageCell={known:number;allowed_zero_cost:number};
type SellableFormat={target:number;active_order_target?:number;strategic_reserve_target?:number;allowed_unique_sites:number;deficit:number;sellable:boolean;target_source?:string};
type QualitySite={domain:string;iks:number|null;tier:"high"|"medium"|"low"|"unknown";platform:string;name:string};
type InventorySummary={platform_records_total:number;forum_records_total:number;unique_forum_sites:number;unique_sites_seen_or_checked:number;discovery_unique_sites:number;terminal_discovery_sites:number;terminal_only_not_registry:number;rules_allowed:number;rules_review:number;rules_blocked:number;rules_reply_only:number;publication_ready:number;bootstrap_queue:number;warming_accounts:number;terminal_registration_blocked:number;coverage_by_niche:Record<string,CoverageCell>;sellable_product_formats:Record<string,SellableFormat>;sellable_product_complete:boolean;sellable_product_total_deficit:number;active_crowd_projects:number;current_required:number;current_relevant_sites:number;current_relevant_after_bootstrap:number;current_relevant_after_warmup:number;current_missing_after_bootstrap:number;current_missing_after_warmup:number;strict_relevant_platforms:string[];quality_measured_at?:string|null;quality_domains_total?:number;quality_high_sites?:number;quality_medium_sites?:number;quality_high_medium_sites?:number;quality_allowed_high_medium_sites?:number;quality_top_allowed?:QualitySite[]};
type WarmupItem={platform:string;name:string;url:string;status:string;checkpoint:string;updated_at?:string;warming_started_at?:string;earliest_age_ready_at?:string;age_elapsed_days?:number|null;age_remaining_days?:number;supported_client_formats?:string[];account_url?:string;requirements:Record<string,string|number|boolean>;owner_action_required:boolean;executor_role:string;reusable_across_clients:boolean;next_action:string};
type WarmupQueue={warming:number;owner_action_required:boolean;reusable_across_clients:boolean;items:WarmupItem[]};

const api=(url:string,opts?:RequestInit)=>{
  const token=typeof window!=="undefined"?localStorage.getItem("boris_token")||"":"";
  return fetch(url,{credentials:"include",cache:"no-store",...opts,headers:{"Content-Type":"application/json",...(token?{Authorization:`Bearer ${token}`}:{}),...(opts?.headers||{})}})
    .then(async r=>{const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||d.message||"Ошибка");return d});
};

const money=(n:number)=>new Intl.NumberFormat("ru-RU").format(n)+" ₽";
const statusLabel=(s:string)=>({needs_owner_approval:"Нужно согласование",approved:"Согласовано",rejected:"Отклонено",posted:"Опубликовано"} as Record<string,string>)[s]||s;
const errorMessage=(e:unknown)=>e instanceof Error?e.message:"Неизвестная ошибка";
const nicheLabel:Record<string,string>={
  marketing:"Маркетинг / SEO",it:"IT / разработка",business:"Бизнес",construction:"Строительство",
  services:"Услуги",goods:"Товары",furniture:"Мебель",auto:"Авто",logistics:"Логистика",
  marketplaces:"Маркетплейсы",realestate:"Недвижимость",horeca:"HoReCa",medicine:"Медицина",
  manufacturing:"Производство",accounting:"Бухгалтерия",unknown:"Другое"
};
const warmupReqLabel:Record<string,string>={
  level:"Уровень аккаунта",
  minimum_account_age_days:"Минимальный возраст",
  minimum_messages:"Сообщений",
  minimum_reactions:"Реакций",
  notes:"Правило прогрева",
};
const clientFormatLabel:Record<string,string>={goods:"товары",services:"услуги"};

export default function ServiceMarketplacePage(){
  const [platforms,setPlatforms]=useState<Platform[]>([]);
  const [offers,setOffers]=useState<Offer[]>([]);
  const [drafts,setDrafts]=useState<Draft[]>([]);
  const [bootstrap,setBootstrap]=useState<BootstrapQueue>({needed:0,owner_action_required:false,human_action_required:false,reusable_across_clients:true,executor_role:"platform_onboarding_worker",items:[]});
  const [warmup,setWarmup]=useState<WarmupQueue>({warming:0,owner_action_required:false,reusable_across_clients:true,items:[]});
  const [inventory,setInventory]=useState<InventorySummary>({platform_records_total:0,forum_records_total:0,unique_forum_sites:0,unique_sites_seen_or_checked:0,discovery_unique_sites:0,terminal_discovery_sites:0,terminal_only_not_registry:0,rules_allowed:0,rules_review:0,rules_blocked:0,rules_reply_only:0,publication_ready:0,bootstrap_queue:0,warming_accounts:0,terminal_registration_blocked:0,coverage_by_niche:{},sellable_product_formats:{},sellable_product_complete:false,sellable_product_total_deficit:0,active_crowd_projects:0,current_required:0,current_relevant_sites:0,current_relevant_after_bootstrap:0,current_relevant_after_warmup:0,current_missing_after_bootstrap:0,current_missing_after_warmup:0,strict_relevant_platforms:[],quality_domains_total:0,quality_high_sites:0,quality_medium_sites:0,quality_high_medium_sites:0,quality_allowed_high_medium_sites:0,quality_top_allowed:[]});
  const [busy,setBusy]=useState(false);
  const [verifyBusy,setVerifyBusy]=useState<string|null>(null);
  const [msg,setMsg]=useState("");
  const [filter,setFilter]=useState("needs_owner_approval");
  const [topic,setTopic]=useState("all");

  const load=async()=>{
    const critical=Promise.allSettled([
      api("/api/prospecting/service-marketplace/bootstrap-queue").then(b=>setBootstrap({...b,items:b.items||[]})),
      api("/api/prospecting/service-marketplace/warmup-queue").then(w=>setWarmup({...w,items:w.items||[]})),
      api("/api/prospecting/service-marketplace/inventory-summary").then(i=>setInventory(i)),
    ]);
    const secondary=Promise.all([
      api("/api/prospecting/service-marketplace/platforms"),
      api("/api/prospecting/service-marketplace/offers"),
      api("/api/prospecting/service-marketplace/drafts"),
    ]).then(([p,o,d])=>{
      setPlatforms(p.items||[]);setOffers(o.items||[]);setDrafts(d.items||[]);
    });
    await Promise.all([critical,secondary]);
  };
  useEffect(()=>{
    let alive=true;
    const refresh=()=>{ if(alive) void load().catch(e=>setMsg(errorMessage(e))); };
    const first=window.setTimeout(refresh,0);
    const timer=window.setInterval(refresh,15000);
    const onVisible=()=>{ if(document.visibilityState==="visible") refresh(); };
    document.addEventListener("visibilitychange",onVisible);
    return ()=>{
      alive=false;
      window.clearTimeout(first);
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange",onVisible);
    };
  },[]);

  const shown=useMemo(()=>drafts.filter(x=>(filter==="all"||x.status===filter)&&(topic==="all"||x.topic===topic)),[drafts,filter,topic]);
  const coverageRows=useMemo(()=>Object.entries(inventory.coverage_by_niche||{})
    .map(([key,value])=>({key,label:nicheLabel[key]||key,...value}))
    .sort((a,b)=>b.allowed_zero_cost-a.allowed_zero_cost||b.known-a.known||a.label.localeCompare(b.label,"ru")),[inventory.coverage_by_niche]);
  const activeBootstrapItems=useMemo(()=>bootstrap.items.filter(x=>x.urgent_for_active_projects),[bootstrap.items]);
  const reserveBootstrapItems=useMemo(()=>bootstrap.items.filter(x=>!x.urgent_for_active_projects),[bootstrap.items]);

  const decide=async(id:string,decision:"approved"|"rejected")=>{
    setBusy(true);setMsg("");
    try{
      await api(`/api/prospecting/service-marketplace/drafts/${id}/decision`,{method:"POST",body:JSON.stringify({decision})});
      setMsg(decision==="approved"?"Текст согласован. Публикация теперь разрешена для этого черновика.":"Текст отклонён и не будет опубликован.");
      await load();
    }catch(e:unknown){setMsg(errorMessage(e))}finally{setBusy(false)}
  };

  const verifyBootstrap=async(platform:string)=>{
    setVerifyBusy(platform);setMsg("");
    try{
      const d=await api(`/api/prospecting/service-marketplace/bootstrap-queue/${platform}/verify`,{method:"POST"});
      setMsg(d.message||"Проверка площадки завершена.");
      await load();
    }catch(e:unknown){setMsg(errorMessage(e))}finally{setVerifyBusy(null)}
  };

  const generate=async()=>{
    setBusy(true);setMsg("");
    try{
      const d=await api("/api/prospecting/service-marketplace/drafts/generate",{method:"POST",body:JSON.stringify({platform:"all",topic,variants:3})});
      setMsg(`Создано ${d.generated} вариантов без OpenAI. Все требуют вашего согласования.`);
      await load();
    }catch(e:unknown){setMsg(errorMessage(e))}finally{setBusy(false)}
  };

  return <AppShell active="acquisition" title="Размещение товаров и услуг" subtitle="Общая база площадок, прогрев аккаунтов и клиентские публикации"><main className="min-h-screen min-w-0 bg-slate-50 p-4 md:p-8">
    <div className="mx-auto max-w-7xl space-y-6">
      <section className="rounded-3xl bg-white p-6 shadow-sm border border-slate-200">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div>
            <div className="text-xs font-bold tracking-[.18em] text-blue-600">BORIS · CROWD SEO ДЛЯ ТОВАРОВ И УСЛУГ</div>
            <h1 className="mt-2 text-3xl font-bold text-slate-900">Общая база форумов для разных клиентских ниш</h1>
            <p className="mt-2 max-w-3xl text-slate-600">Клиент даёт только сайт. BORIS определяет нишу и тип предложения, выбирает релевантные разделы, готовит тексты и использует общий пул уже подготовленных и прогретых аккаунтов. До согласования проекта внешняя публикация запрещена.</p>
          </div>
          <a href="https://boris-ai.pro" target="_blank" rel="noreferrer" className="rounded-xl bg-blue-600 px-4 py-3 text-sm font-semibold text-white">Открыть boris-ai.pro</a>
        </div>
        {msg&&<div className="mt-4 rounded-xl bg-slate-100 px-4 py-3 text-sm text-slate-700">{msg}</div>}
      </section>

      <section className="rounded-3xl border border-blue-200 bg-blue-50 p-5">
        <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="text-xs font-bold tracking-[.15em] text-blue-700">БАЗА САЙТОВ CROWD SEO</div>
            <h2 className="mt-1 text-xl font-bold text-slate-950">Сколько площадок BORIS уже знает и что из них реально полезно</h2>
          </div>
          <div className="text-xs text-slate-600">Считаются уникальные форумные сайты, а не дубли записей.</div>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 2xl:grid-cols-8">
          <div className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="text-xs text-slate-500">Всего сайтов уже нашли / проверили</div>
            <div className="mt-1 text-3xl font-bold text-blue-800">{inventory.unique_sites_seen_or_checked}</div>
            <div className="mt-1 text-xs text-slate-500">{inventory.unique_forum_sites} в рабочем реестре · {inventory.terminal_only_not_registry} уже отбракованы вне реестра</div>
          </div>
          <div className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="text-xs text-slate-500">Высокий / средний ИКС</div>
            <div className="mt-1 text-3xl font-bold text-blue-800">{inventory.quality_high_medium_sites??0}</div>
            <div className="mt-1 text-xs text-slate-500">{inventory.quality_high_sites??0} высокий · {inventory.quality_medium_sites??0} средний</div>
          </div>
          <div className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="text-xs text-slate-500">Качественные и разрешённые</div>
            <div className="mt-1 text-3xl font-bold text-emerald-700">{inventory.quality_allowed_high_medium_sites??0}</div>
            <div className="mt-1 text-xs text-slate-500">high/medium ИКС + правила разрешают бесплатно</div>
          </div>
          <div className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="text-xs text-slate-500">Правила разрешают</div>
            <div className="mt-1 text-3xl font-bold text-emerald-700">{inventory.rules_allowed}</div>
            <div className="mt-1 text-xs text-slate-500">ещё {inventory.rules_review} требуют разбора</div>
          </div>
          <div className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="text-xs text-slate-500">Готовы прямо сейчас</div>
            <div className="mt-1 text-3xl font-bold text-emerald-700">{inventory.publication_ready}</div>
            <div className="mt-1 text-xs text-slate-500">аккаунт + правила + публикация готовы</div>
          </div>
          <div className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="text-xs text-slate-500">Разовая подготовка</div>
            <div className="mt-1 text-3xl font-bold text-violet-700">{inventory.bootstrap_queue}</div>
            <div className="mt-1 text-xs text-slate-500">{inventory.warming_accounts} уже прогреваются · {inventory.terminal_registration_blocked} имеют терминальный блок</div>
          </div>
          <div className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="text-xs text-slate-500">Для текущего проекта</div>
            <div className="mt-1 text-3xl font-bold text-violet-800">{Math.min(inventory.current_relevant_after_bootstrap,inventory.current_required)}<span className="text-base font-semibold text-slate-400"> / {inventory.current_required}</span></div>
            <div className="mt-1 text-xs text-slate-500">после быстрой подготовки · {Math.max(0,inventory.current_relevant_after_bootstrap-inventory.current_required)} ещё останется в резерве · ещё {Math.max(0,inventory.current_relevant_after_warmup-inventory.current_relevant_after_bootstrap)} добавится после прогрева</div>
          </div>
          <div className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="text-xs text-slate-500">Ещё найти</div>
            <div className="mt-1 text-3xl font-bold text-amber-700">{inventory.current_missing_after_bootstrap}</div>
            <div className="mt-1 text-xs text-slate-500">для быстрой ёмкости · после полного прогрева останется {inventory.current_missing_after_warmup}</div>
          </div>
        </div>
        <div className="mt-4 rounded-2xl bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-900">Топ разрешённых площадок по ИКС</div>
              <div className="text-xs text-slate-500">ИКС используется только как качество/приоритет. Правила и релевантность важнее ИКС.</div>
            </div>
            <div className="text-xs text-slate-500">{inventory.quality_measured_at?new Date(inventory.quality_measured_at).toLocaleString("ru-RU"):"ещё не измерено"}</div>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {(inventory.quality_top_allowed||[]).slice(0,12).map(site=><div key={site.domain} className="rounded-xl border border-slate-200 px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <div className="truncate text-sm font-semibold text-slate-900">{site.domain}</div>
                <div className={site.tier==="high"?"rounded-lg bg-blue-100 px-2 py-1 text-xs font-bold text-blue-800":"rounded-lg bg-slate-100 px-2 py-1 text-xs font-bold text-slate-700"}>ИКС {site.iks??0}</div>
              </div>
              <div className="mt-1 truncate text-xs text-slate-500">{site.name}</div>
            </div>)}
          </div>
        </div>
      </section>

      <section className="rounded-3xl border border-emerald-300 bg-emerald-50 p-5">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div>
            <div className="text-xs font-bold tracking-[.15em] text-emerald-700">ЁМКОСТЬ БАЗЫ CROWD SEO</div>
            <h2 className="mt-1 text-xl font-bold text-slate-950">Сколько разрешённых бесплатных площадок есть для товаров и услуг</h2>
            <p className="mt-1 text-sm text-slate-700">Объём каждого клиентского проекта берётся из его тарифа/заказа — фиксированного пакета нет. Здесь показана реальная база площадок; отдельно BORIS продолжает расширять стратегический пул до 100+100.</p>
          </div>
          <div className={(inventory.current_missing_after_bootstrap??0)===0?"rounded-2xl bg-emerald-600 px-4 py-3 text-sm font-bold text-white":"rounded-2xl bg-amber-100 px-4 py-3 text-sm font-bold text-amber-900"}>
            {(inventory.current_missing_after_bootstrap??0)===0?"Текущие активные заказы покрыты":"Дефицит по активным заказам: "+inventory.current_missing_after_bootstrap}
          </div>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {(["services","goods"] as const).map(format=>{
            const row=inventory.sellable_product_formats?.[format];
            const label=format==="services"?"Услуги":"Товары";
            const strategicTarget=row?.strategic_reserve_target??row?.target??100;
            const activeOrder=row?.active_order_target??0;
            const available=row?.allowed_unique_sites??0;
            const deficit=row?.deficit??Math.max(0,strategicTarget-available);
            return <div key={format} className="rounded-2xl bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <div className="font-semibold text-slate-900">{label}</div>
                <div className={row?.sellable?"rounded-lg bg-emerald-100 px-2 py-1 text-xs font-bold text-emerald-800":"rounded-lg bg-amber-100 px-2 py-1 text-xs font-bold text-amber-800"}>{row?.sellable?"РЕЗЕРВ СОБРАН":"НАБИРАЕМ РЕЗЕРВ"}</div>
              </div>
              <div className="mt-2 text-3xl font-bold text-slate-950">{available}<span className="text-base font-semibold text-slate-400"> / {strategicTarget}</span></div>
              <div className="mt-1 text-xs text-slate-600">разрешённых уникальных сайтов / стратегический минимум</div>
              <div className="mt-2 text-xs font-semibold text-slate-700">{deficit>0?"До общего резерва ещё: "+deficit:"Стратегический резерв собран"}</div>
              <div className="mt-1 text-xs text-slate-500">{activeOrder>0?"Активный заказ сейчас: "+activeOrder+" размещений":"Активного заказа этого формата сейчас нет — запас всё равно продолжаем собирать заранее"}</div>
            </div>
          })}
        </div>
      </section>

      <section className="rounded-3xl border border-emerald-200 bg-emerald-50 p-5">
        <div>
          <div className="text-xs font-bold tracking-[.15em] text-emerald-700">ПОКРЫТИЕ ПО НИШАМ</div>
          <h2 className="mt-1 text-xl font-bold text-slate-950">Где уже есть база для товаров и услуг клиентов</h2>
          <p className="mt-1 text-sm text-slate-700">Формат: разрешённые бесплатные площадки / все известные площадки этой ниши. Ноль не означает «не продаём» — это приоритет на поиск и подготовку до запуска клиента.</p>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-5">
          {coverageRows.map(row=><div key={row.key} className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="text-sm font-semibold text-slate-900">{row.label}</div>
            <div className="mt-2 text-2xl font-bold text-emerald-700">{row.allowed_zero_cost}<span className="text-base font-semibold text-slate-400"> / {row.known}</span></div>
            <div className="mt-1 text-xs text-slate-500">разрешено бесплатно / известно</div>
          </div>)}
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-4">
        <div className="rounded-2xl bg-white p-5 border border-slate-200"><div className="text-sm text-slate-500">Площадок</div><div className="mt-1 text-3xl font-bold">{platforms.length}</div></div>
        <div className="rounded-2xl bg-white p-5 border border-slate-200"><div className="text-sm text-slate-500">Услуг в прайсе</div><div className="mt-1 text-3xl font-bold">{offers.length}</div></div>
        <div className="rounded-2xl bg-white p-5 border border-slate-200"><div className="text-sm text-slate-500">Ждут согласования</div><div className="mt-1 text-3xl font-bold">{drafts.filter(x=>x.status==="needs_owner_approval").length}</div></div>
        <div className="rounded-2xl bg-white p-5 border border-slate-200"><div className="text-sm text-slate-500">Опубликовано</div><div className="mt-1 text-3xl font-bold">{drafts.filter(x=>x.status==="posted").length}</div></div>
      </section>

      <section className="rounded-3xl border border-violet-200 bg-violet-50 p-6">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div>
            <div className="text-xs font-bold tracking-[.15em] text-violet-700">СЛУЖЕБНАЯ ОЧЕРЕДЬ ПЛОЩАДОК</div>
            <h2 className="mt-1 text-xl font-bold text-violet-950">Одноразовая подготовка — не задача владельца</h2>
            <p className="mt-1 max-w-3xl text-sm text-violet-900">Эту очередь выполняет сотрудник подготовки площадок, а не владелец клиента. BORIS сам обнаруживает CAPTCHA, подтверждение почты или принятие правил и ставит один раз нужное действие специалисту; антибот-проверку он не обходит. После подтверждения BORIS сам перепроверяет аккаунт: если площадка требует возраст/уровень/репутацию — ставит его на прогрев, иначе переводит в READY. Готовый аккаунт используется для разных клиентских проектов.</p>
          </div>
          <div className="grid grid-cols-2 gap-2 text-center">
            <div className="rounded-2xl bg-white px-4 py-3 shadow-sm">
              <div className="text-xs text-slate-500">Нужны клиентам сейчас</div>
              <div className="text-3xl font-bold text-violet-800">{bootstrap.needed_for_active_projects??0}</div>
            </div>
            <div className="rounded-2xl bg-white px-4 py-3 shadow-sm">
              <div className="text-xs text-slate-500">Остальные в резерве</div>
              <div className="text-3xl font-bold text-slate-700">{Math.max(0,bootstrap.needed-(bootstrap.needed_for_active_projects??0))}</div>
            </div>
          </div>
        </div>
        {(bootstrap.needed_for_active_projects??0)>0&&<div className="mt-4 rounded-2xl border border-violet-200 bg-white p-4 text-sm text-violet-950">
          <b>Сначала делаем верхние {bootstrap.needed_for_active_projects} площадок.</b> Из них после обычной одноразовой подготовки могут быстро открыться до {bootstrap.potential_slots_immediate_after_bootstrap??0} релевантных размещений. Ещё {bootstrap.warming_after_bootstrap??0} площадка после регистрации перейдёт в прогрев; долгосрочно найденный пул вырастет до {bootstrap.potential_slots_after_warmup??bootstrap.potential_slots_for_active_projects??0}. Остальные площадки — резерв для других ниш.
        </div>}
        {activeBootstrapItems.length===0
          ?<div className="mt-4 rounded-2xl bg-white p-4 text-sm font-semibold text-emerald-700">Для активных клиентских проектов одноразовая подготовка сейчас не требуется.</div>
          :<div className="mt-4 grid gap-3 md:grid-cols-2" data-testid="active-bootstrap-grid">
            {activeBootstrapItems.map(x=><article key={x.platform} data-testid="active-bootstrap-card" className="rounded-2xl border border-violet-400 bg-white p-4 ring-1 ring-violet-200">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="font-semibold text-slate-900">{x.name}</div>
                    <span className="rounded-lg bg-emerald-100 px-2 py-1 text-[11px] font-bold text-emerald-800">НУЖНО ТЕКУЩИМ ПРОЕКТАМ</span>
                  </div>
                  <div className="mt-1 text-sm text-slate-700">{x.action}</div>
                  <div className="mt-2 text-xs font-semibold text-violet-800">{x.requires_warmup_after_bootstrap?"После регистрации: аккаунт пойдёт в прогрев, затем даст ":"После подготовки: до "}{x.potential_slots||1} релевантного размещения · ожидают проектов: {x.relevant_projects||1}</div>
                </div>
                <div className="shrink-0 rounded-lg bg-violet-100 px-2 py-1 text-xs font-bold text-violet-700">ОДИН РАЗ</div>
              </div>
              <div className="mt-3 rounded-xl bg-slate-50 p-3 text-xs text-slate-600">{x.after_completion} Готовый аккаунт переиспользуется для следующих клиентов — повторно проходить этот шаг не нужно.</div>
              {(x.supported_client_formats?.length||0)>0&&<div className="mt-2 text-xs font-semibold text-slate-700">Подходит для: {x.supported_client_formats?.map(v=>v==="goods"?"товаров":v==="services"?"услуг":v).join(" · ")}</div>}
              {x.starts_warmup_after_bootstrap&&x.maturity_requirements&&<div className="mt-2 rounded-xl bg-amber-50 p-3 text-xs text-amber-900">После регистрации начнётся прогрев. Минимальный возраст: {String(x.maturity_requirements.minimum_account_age_days||"—")} дней · сообщений: {String(x.maturity_requirements.minimum_messages||"—")} · реакций: {String(x.maturity_requirements.minimum_reactions||"—")}. BORIS не накручивает активность и сам перепроверяет готовность.</div>}
              <div className="mt-3 flex flex-wrap gap-2">
                <a href={x.account_url||x.url} target="_blank" rel="noreferrer" className="rounded-xl bg-violet-700 px-3 py-2 text-sm font-semibold text-white">Открыть площадку</a>
                <button disabled={verifyBusy!==null} onClick={()=>verifyBootstrap(x.platform)} className="rounded-xl border border-violet-200 bg-white px-3 py-2 text-sm font-semibold text-violet-800 disabled:opacity-50">{verifyBusy===x.platform?"Проверяем…":"Проверить после подготовки"}</button>
              </div>
            </article>)}
          </div>}
        {reserveBootstrapItems.length>0&&<details className="mt-4 rounded-2xl border border-slate-200 bg-white p-4" data-testid="reserve-bootstrap">
          <summary className="cursor-pointer text-sm font-semibold text-slate-700">Резервные площадки — сейчас не требуются ({reserveBootstrapItems.length})</summary>
          <p className="mt-2 text-xs text-slate-500">Они не входят в текущие 18 обязательных площадок. BORIS хранит их для других ниш и будущих клиентов.</p>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {reserveBootstrapItems.map(x=><div key={x.platform} className="rounded-xl bg-slate-50 p-3 text-sm text-slate-700">
              <div className="font-semibold">{x.name}</div>
              <div className="mt-1 text-xs text-slate-500">{x.action}</div>
              {(x.supported_client_formats?.length||0)>0&&<div className="mt-2 text-xs font-semibold text-slate-700">Подходит для: {x.supported_client_formats?.map(v=>clientFormatLabel[v]||v).join(" · ")}</div>}
              {x.starts_warmup_after_bootstrap&&x.maturity_requirements&&<div className="mt-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-900">После регистрации начнётся прогрев. Минимальный возраст: {String(x.maturity_requirements.minimum_account_age_days||"—")} дней · сообщений: {String(x.maturity_requirements.minimum_messages||"—")} · реакций: {String(x.maturity_requirements.minimum_reactions||"—")}. BORIS не накручивает активность и сам перепроверяет готовность.</div>}
            </div>)}
          </div>
        </details>}
      </section>

      <section className="rounded-3xl border border-amber-200 bg-amber-50 p-6">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div>
            <div className="text-xs font-bold tracking-[.15em] text-amber-700">ПРОГРЕВ АККАУНТОВ</div>
            <h2 className="mt-1 text-xl font-bold text-amber-950">Аккаунты, которые уже созданы, но ещё дозревают до права публикации</h2>
            <p className="mt-1 max-w-3xl text-sm text-amber-900">BORIS не считает успешный вход достаточным. Если площадке нужно 15 дней или больше — это нормальное ожидание: аккаунт остаётся в прогреве, BORIS сам перепроверяет право публикации и после выполнения условий переводит его в READY. Один прогретый аккаунт переиспользуется для подходящих клиентов по товарам и услугам.</p>
          </div>
          <div className="rounded-2xl bg-white px-5 py-3 text-center shadow-sm">
            <div className="text-xs text-slate-500">Сейчас прогреваются</div>
            <div className="text-3xl font-bold text-amber-700">{warmup.warming}</div>
          </div>
        </div>
        {warmup.items.length===0
          ?<div className="mt-4 rounded-2xl bg-white p-4 text-sm text-slate-700">Пока ни один аккаунт не перешёл из регистрации в стадию прогрева. Площадки с CAPTCHA/соглашением сначала находятся в служебной очереди выше.</div>
          :<div className="mt-4 grid gap-3 md:grid-cols-2">
            {warmup.items.map(x=><article key={x.platform} className="rounded-2xl border border-amber-200 bg-white p-4">
              <div className="font-semibold text-slate-900">{x.name}</div>
              <div className="mt-1 text-sm text-slate-700">{x.next_action}</div>
              <div className="mt-3 grid gap-2 sm:grid-cols-3 text-xs text-slate-700">
                <div className="rounded-lg bg-amber-50 p-3"><b>Прогрето</b><div className="mt-1 text-lg font-bold text-amber-800">{x.age_elapsed_days??"—"} дн.</div></div>
                <div className="rounded-lg bg-amber-50 p-3"><b>Осталось по возрасту</b><div className="mt-1 text-lg font-bold text-amber-800">{x.age_remaining_days??"—"} дн.</div></div>
                <div className="rounded-lg bg-amber-50 p-3"><b>Самая ранняя дата</b><div className="mt-1 font-semibold">{x.earliest_age_ready_at?new Date(x.earliest_age_ready_at).toLocaleDateString("ru-RU"):"—"}</div></div>
              </div>
              {(x.supported_client_formats?.length||0)>0&&<div className="mt-2 text-xs font-semibold text-slate-700">Можно использовать для клиентов: {x.supported_client_formats?.map(v=>clientFormatLabel[v]||v).join(" · ")}</div>}
              <div className="mt-3 grid grid-cols-1 gap-2 text-xs text-slate-600 sm:grid-cols-2">
                {Object.entries(x.requirements||{})
                  .filter(([k,v])=>typeof v!=="boolean"&&["level","minimum_account_age_days","minimum_messages","minimum_reactions","notes"].includes(k))
                  .map(([k,v])=><div key={k} className="rounded-lg bg-slate-50 p-2"><b>{warmupReqLabel[k]||k}</b>: {String(v)}{k==="minimum_account_age_days"?" дн.":""}</div>)}
              </div>
              <a href={x.account_url||x.url} target="_blank" rel="noreferrer" className="mt-3 inline-flex rounded-xl bg-amber-600 px-3 py-2 text-sm font-semibold text-white">Открыть площадку</a>
            </article>)}
          </div>}
      </section>

      <section className="rounded-3xl bg-white p-6 border border-slate-200">
        <h2 className="text-xl font-bold">Прайс услуг</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {offers.map(o=><div key={o.key} className="rounded-2xl border border-slate-200 p-4">
            <div className="font-semibold text-slate-900">{o.title}</div>
            <div className="mt-1 text-sm text-slate-600">{o.short_pitch}</div>
            <div className="mt-3 text-sm"><b>{money(o.setup_from)}–{money(o.setup_to)}</b> внедрение</div>
            <div className="text-sm text-slate-600">{money(o.monthly_from)}–{money(o.monthly_to)}/мес сопровождение</div>
          </div>)}
        </div>
      </section>

      <section className="rounded-3xl bg-white p-6 border border-slate-200">
        <h2 className="text-xl font-bold">Площадки</h2>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead><tr className="border-b"><th className="py-3">Приоритет</th><th>Площадка</th><th>Как работаем</th><th>Аудитория</th><th>Ограничения</th></tr></thead>
            <tbody>{platforms.map(p=><tr key={p.key} className="border-b border-slate-100 align-top">
              <td className="py-3 font-bold">{p.priority}</td>
              <td className="py-3"><a className="font-semibold text-blue-600" href={p.url} target="_blank" rel="noreferrer">{p.name}</a></td>
              <td className="py-3">{p.mode}</td><td className="py-3">{p.audience}</td><td className="py-3 text-slate-600">{p.notes}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </section>

      <section className="rounded-3xl bg-white p-6 border border-slate-200">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div><h2 className="text-xl font-bold">Тексты на ваше согласование</h2><p className="text-sm text-slate-500">Авито · Яндекс Директ · продажи · CRM, 1С и другие системы. Ссылка BORIS обязательна.</p></div>
          <div className="flex flex-wrap gap-2">
            <select value={topic} onChange={e=>setTopic(e.target.value)} className="rounded-xl border px-3 py-2">
              <option value="all">Все темы</option><option value="avito">Авито</option><option value="direct">Яндекс Директ</option><option value="sales">Продажи</option><option value="integrations">CRM / 1С / API</option>
            </select>
            <select value={filter} onChange={e=>setFilter(e.target.value)} className="rounded-xl border px-3 py-2">
              <option value="needs_owner_approval">Ждут согласования</option><option value="approved">Согласовано</option><option value="posted">Опубликовано</option><option value="rejected">Отклонено</option><option value="all">Все</option>
            </select>
            <button disabled={busy} onClick={generate} className="rounded-xl bg-slate-900 px-4 py-2 text-white disabled:opacity-50">Сгенерировать 3 варианта</button>
          </div>
        </div>

        <div className="mt-5 space-y-4">
          {shown.map(d=><article key={d.id} className="rounded-2xl border border-slate-200 p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div><div className="font-semibold">{d.platform_name} · {d.title}</div><div className="text-xs text-slate-500">ID {d.id} · вариант {d.variant} · {statusLabel(d.status)}</div></div>
              <div className="flex gap-2">
                {d.status==="needs_owner_approval"&&<>
                  <button disabled={busy} onClick={()=>decide(d.id,"approved")} className="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-semibold text-white">Согласовать</button>
                  <button disabled={busy} onClick={()=>decide(d.id,"rejected")} className="rounded-lg bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-700">Отклонить</button>
                </>}
                {d.publication_url&&<a href={d.publication_url} target="_blank" rel="noreferrer" className="rounded-lg bg-blue-50 px-3 py-2 text-sm font-semibold text-blue-700">Открыть пост</a>}
              </div>
            </div>
            <pre className="mt-4 max-w-full overflow-hidden whitespace-pre-wrap break-words rounded-xl bg-slate-50 p-4 text-sm leading-6 text-slate-700 [overflow-wrap:anywhere]">{d.text}</pre>
          </article>)}
          {!shown.length&&<div className="rounded-xl bg-slate-50 p-6 text-center text-slate-500">По выбранному фильтру ничего нет.</div>}
        </div>
      </section>
    </div>
  </main></AppShell>;
}
