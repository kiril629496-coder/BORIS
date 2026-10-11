"use client";
import { Button, Icon } from "@/app/ui";
import BorisSelect from "./BorisSelect";
import CampaignTimeline from "./CampaignTimeline";


/**
 * Обзор ЕЦО — вход в центр объявлений.
 *
 * Человек выбирает ЗАДАЧУ, а не раздел. Панель «Требует внимания» считается
 * на сервере и ведёт сразу в нужную выборку. Карточки, которые пока не
 * работают, приглушены и подписаны «Скоро» — неработающее не должно
 * выглядеть рабочим.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Card, Skeleton, Alert } from "../../ui";
import { EcShell, EcEmpty } from "../../ui/EcShell";
import { EcActionCard } from "../../ui/ec";
import { color, font, space } from "../../ui/tokens";
import { apiFetch, apiGet } from "../../lib/api";
import { apiPost } from "../../lib/campaignApi";
import { readCampaignDraft, writeCampaignDraft, clearCampaignDraft } from "../../lib/campaignDraft";
import { useAccounts } from "../../lib/AccountContext";
import MassEditor from "../../listings/MassEditor";
import { deterministicCurrentCampaignId, resolveOwningCampaignId } from "./projection";

function readinessBlockCopy(reason: string): string {
  const key=String(reason||"").trim();
  const map:Record<string,string>={
    price_pass:"Нужно подтвердить цену",
    no_needs_data:"Нужно подтвердить недостающие данные",
    no_conflict:"Нужно выбрать правильные данные",
    text_pass:"Нужно проверить текст объявления",
    content_qa_pass:"Нужно исправить текст объявления",
    formula_assigned:"BORIS завершает рекламную гипотезу",
    hypothesis_assigned:"BORIS завершает рекламную гипотезу",
    image_pass:"Нужна подходящая фотография",
    image_unique:"Нужно заменить повторяющуюся фотографию",
    banner_pass:"Нужно подготовить баннер",
    category_pass:"Нужно выбрать категорию Avito",
    attributes_pass:"Нужно заполнить параметры Avito",
    feed_pass:"Нужно закончить проверку Avito",
    account_scope_pass:"Нужно проверить аккаунт кампании",
  };
  return map[key] || "Нужно завершить подготовку кампании";
}

function readinessBlockTarget(reason:string): string {
  return String(reason||"")==="feed_pass" ? "#avito-feed-control" : "#campaign-items";
}

function feedReconcileCopy(feed:any): string {
  const state=String(feed?.state||"");
  const current=Number(feed?.current_feed_count||0);
  const mapped=Number(feed?.mapped_current_feed_count||0);
  const unmapped=Number(feed?.unmapped_current_feed_count||Math.max(0,current-mapped));
  if (state==="READY") return "Все объявления Avito связаны с текущими проектами.";
  if (state==="PARTIAL_MAPPING") return "BORIS видит "+unmapped+" объявлений Avito, которые ещё не связаны с текущими проектами.";
  if (state==="CURRENT_FEED_WITHOUT_VISIBLE_CAMPAIGN") return "BORIS нашёл "+current+" текущих объявлений Avito, но не видит связанный проект. Дубликаты не создаются — нужна сверка.";
  if (state==="NO_CURRENT_FEED") return "BORIS пока не получил список текущих объявлений Avito.";
  return "";
}

function readinessActions(reasons:any[]): {label:string;target:string}[] {
  const out:{label:string;target:string}[]=[];
  const seen=new Set<string>();
  for (const reason of (Array.isArray(reasons)?reasons:[])) {
    const label=readinessBlockCopy(String(reason||""));
    if (seen.has(label)) continue;
    seen.add(label);
    out.push({label,target:readinessBlockTarget(String(reason||""))});
  }
  return out;
}

function formatPromptForEditor(text: string) {
  return String(text || "")
    .replace(/([.!?])\s+(?=[А-ЯA-ZЁ«])/g, "$1\n")
    .replace(/;\s+(?=[А-ЯA-ZЁ«])/g, ";\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}


type PromptExample = { icon: "search"|"check"|"briefcase"|"user"|"target"|"palette"|"layers"; title:string; note:string; prompt:string; goal?:string; direction?:string; categoryTemplateId?:string; categoryPath?:string; categoryParams?:Record<string,any> };

function cleanClientDirection(raw:string): string {
  return String(raw || "")
    .replace(/[\u200B-\u200D\u2060\uFEFF]/g, "")
    .replace(/^[\s•·—–-]+/, "")
    .replace(/\s+/g, " ")
    .trim();
}

function splitClientDirections(niche:string, description:string=""): string[] {
  const nicheText=String(niche || "");
  let curated = nicheText.split(/\n|;|•|,/).map(cleanClientDirection).filter(Boolean);
  // A comma-separated assortment often ends with «X и Y». Split only that
  // list-tail case; never split semantic phrases like «ручная и механизированная
  // штукатурка» when there is no actual enumeration.
  if (nicheText.includes(",")) curated=curated.flatMap((x)=>x.includes(" и ") ? x.split(/\s+и\s+/i).map(cleanClientDirection).filter(Boolean) : [x]);
  // CLIENT_LAUNCHER_CURATED_NICHE_FIRST_V1: company_description is free-form
  // prose and can contain requisites, slogans and whole paragraphs. It must
  // never outrank the curated niche or real listing/category directions.
  const source = String(description || "").split(/\n|;|•/).map(cleanClientDirection).filter(Boolean);
  const descriptive = source.filter(x=>x.length>3 && x.length<=80 && x.split(/\s+/).length<=10 && !/[.:]$/.test(x) && !/^(?:москва|санкт-петербург|спб|компания|наша компания|наши приоритеты|нашими приоритетами|система|реквизиты|сертификаты)/i.test(x));
  const rows = curated.length ? curated : descriptive;
  return rows.filter((x,i,a)=>a.findIndex(y=>y.toLocaleLowerCase("ru")===x.toLocaleLowerCase("ru"))===i).slice(0,5);
}

function inferCampaignKind(context:any): "services"|"products" {
  if (context?.launcher_kind === "products" || context?.launcher_kind === "services") return context.launcher_kind;
  const paths=(context?.feed_category_paths||[]).map((x:any)=>String(x||"").trim().toLocaleLowerCase("ru"));
  const hasServices=paths.some((p:string)=>p.startsWith("услуги >"));
  const hasProducts=paths.some((p:string)=>p && !p.startsWith("услуги >"));
  if (hasServices && !hasProducts) return "services";
  if (hasProducts && !hasServices) return "products";
  const niche=String(context?.company_niche||"").toLocaleLowerCase("ru");
  if (/(товар|материал|мебел|кухн|шкаф|гардероб|перегород|огражден|техник|оборудован|запчаст|продукт)/i.test(niche)) return "products";
  if (/(услуг|ремонт|отделк|аренд|брокер|ипотек|консультац|штукатур|монтаж|установк)/i.test(niche)) return "services";
  return "services";
}

function campaignThemes(context:any, kind:"services"|"products", requireVerifiedPrice:boolean) {
  const key = kind === "products" ? "launcher_directions_products" : "launcher_directions_services";
  const directions = Array.isArray(context?.[key]) ? context[key] : [];
  const active = Array.isArray(context?.active_listing_titles) ? context.active_listing_titles : [];
  const feed = Array.isArray(context?.feed_listing_titles) ? context.feed_listing_titles : [];
  const entries = Array.isArray(context?.[kind === "products" ? "launcher_entries_products" : "launcher_entries_services"])
    ? context[kind === "products" ? "launcher_entries_products" : "launcher_entries_services"] : [];
  const normalize = (value:any) => cleanClientDirection(String(value || "")).toLocaleLowerCase("ru");
  return directions.map((direction:any) => {
    const title = cleanClientDirection(String(direction || ""));
    const normalized = normalize(title);
    const observed = [...active, ...feed].filter((item:any) => normalize(item) === normalized).length;
    const hint = entries.find((item:any) => normalize(item?.title) === normalized) || {};
    const hasCategory = Boolean(String(hint?.template_id || "").trim() || String(hint?.category_path || "").trim());
    const source = active.some((item:any) => normalize(item) === normalized)
      ? "активные объявления"
      : feed.some((item:any) => normalize(item) === normalized)
        ? "текущие объявления Avito"
        : context?.sources?.profile ? "профиль клиента" : "не подтверждено";
    const price = (context?.resolved_prices && context.resolved_prices[title]) || {};
    const priceConflict = Boolean(price?.conflict || price?.status === "PRICE_CONFLICT");
    const priceReady = !requireVerifiedPrice || (price?.status === "READY" && price?.verified === true && !priceConflict);
    const readiness = !hasCategory
      ? "NEEDS_DATA"
      : priceConflict ? "PRICE_CONFLICT" : priceReady ? "READY" : "NEEDS_DATA";
    return {
      title,
      source,
      category: String(hint?.category_path || "не определена"),
      variants: observed,
      readiness,
      hasCategory,
      price,
      campaignId: Number(hint?.campaign_id ?? hint?.owning_campaign_id ?? hint?.campaignId ?? hint?.owningCampaignId ?? 0) || 0,
      itemId: Number(hint?.item_id ?? hint?.itemId ?? 0) || 0,
      feedIdentity: String(hint?.feed_identity ?? hint?.feedIdentity ?? ""),
    };
  }).filter((item:any) => item.title).slice(0, 8);
}

function listingDirectionHints(context:any, kind?:"services"|"products"): string[] {
  const typedReady=Array.isArray(context?.launcher_directions_services) && Array.isArray(context?.launcher_directions_products);
  const typedKey=kind === "products" ? "launcher_directions_products" : "launcher_directions_services";
  const serverDirections=typedReady && kind ? [...(context?.[typedKey]||[])] : [...(context?.launcher_directions||[])];
  if (typedReady && kind) {
    const cleaned=serverDirections.map((raw:any)=>cleanClientDirection(String(raw||""))).filter(Boolean).map((title:string)=>
      title.replace(/\s+(?:в|для)\s+(?:москве|санкт-петербурге|спб|сочи|уфе|пензе)$/i, "").trim()
    );
    return cleaned.filter((x:string,i:number,a:string[])=>a.findIndex(y=>y.toLocaleLowerCase("ru")===x.toLocaleLowerCase("ru"))===i).slice(0,5);
  }
  const titles=[...(context?.active_listing_titles||[]), ...(context?.feed_listing_titles||[])];
  const categoryHints=(context?.feed_category_paths||[]).map((raw:any)=>String(raw||"").split(/[>\/]/).map((x:string)=>x.trim()).filter(Boolean).pop()||"");
  const cleaned=[...serverDirections,...categoryHints,...titles].map((raw:any)=>cleanClientDirection(String(raw||""))).filter(Boolean).map((title:string)=>
    title.replace(/\s+(?:в|для)\s+(?:москве|санкт-петербурге|спб|сочи|уфе|пензе)$/i, "").trim()
  );
  return cleaned.filter((x:string,i:number,a:string[])=>a.findIndex(y=>y.toLocaleLowerCase("ru")===x.toLocaleLowerCase("ru"))===i).slice(0,5);
}

function clientPromptExamples(account:any, kind:"services"|"products", context:any={}): PromptExample[] {
  const niche=String(account?.company_niche || context?.company_niche || "").trim();
  const description=String(account?.company_description || context?.company_description || "");
  const profileDirs=splitClientDirections(niche, description);
  const typedReady=Array.isArray(context?.launcher_directions_services) && Array.isArray(context?.launcher_directions_products);
  const listingDirs=listingDirectionHints(context,kind);
  const activeSet=new Set((context?.active_listing_titles||[]).map((x:any)=>String(x||"").trim().toLocaleLowerCase("ru")));
  const feedSet=new Set((context?.feed_listing_titles||[]).map((x:any)=>String(x||"").trim().toLocaleLowerCase("ru")));
  // Typed backend directions are authoritative for the selected tab. Legacy
  // responses can still combine server hints with profile directions during a
  // rolling deploy, but a typed response never leaks product directions into
  // services or vice versa.
  const directionSeed=typedReady ? listingDirs : [...listingDirs,...profileDirs];
  const dirs=directionSeed.filter((x,i,a)=>a.findIndex(y=>y.toLocaleLowerCase("ru")===x.toLocaleLowerCase("ru"))===i).slice(0,5);
  const typedEntries=(kind==="products" ? context?.launcher_entries_products : context?.launcher_entries_services) || [];
  const hintMap=new Map<string,any>((Array.isArray(typedEntries)?typedEntries:[]).map((x:any)=>[cleanClientDirection(String(x?.title||"")).toLocaleLowerCase("ru"),x]));
  if (!dirs.length) {
    const subject=kind==="services" ? "услугу выбранного клиента" : "товар выбранного клиента";
    const fallbackTasks=[
      ["Тестовая карточка","Проверить структуру, категорию и обязательные параметры"],
      ["Новая кампания","Подготовить основу новой кампании без выдуманного ассортимента"],
      ["Получить обращения","Сделать вариант с акцентом на звонок или сообщение"],
      ["Улучшить текущую подачу","Подготовить безопасный вариант для сравнения с текущими объявлениями"],
      ["Проверить спрос","Сделать один вариант для проверки спроса без неподтверждённых обещаний"],
    ];
    return fallbackTasks.map(([title,focus],index)=>({
      icon:(["target","layers","briefcase","check","palette"] as PromptExample["icon"][])[index],
      title, note:"1 объявление · нужны данные клиента", goal:index%2?"Цель: получить обращение":"Цель: проверить гипотезу",
      prompt:`Сделай 1 боевое объявление про ${subject}. ${focus}. Используй только данные выбранного клиента, которые реально есть в BORIS. Если направления, цены, характеристики или город не подтверждены — сначала запроси их и не подменяй чужими примерами. Внизу описания добавь отдельный короткий конкретный сценарий именно этой карточки только из подтверждённых фактов; для массовой кампании меняй смысловой ракурс, а не отдельные слова. Подготовь ровно 3 релевантные фотографии и один первый мобильный баннер: максимум 6 слов, максимум 2 строки, без мелкого текста, телефонов и россыпи плашек.`
    }));
  }
  const advantages=String(account?.company_advantages || context?.company_advantages || "").trim();
  const seeds=dirs.map((direction)=>({direction,title:direction,focus:""}));
  // Sparse accounts may have only a broad confirmed niche and no imported feed yet.
  // Still show five useful client-specific launch examples without inventing concrete
  // SKUs/services. Extra cards keep the same confirmed direction and vary only the
  // marketing task; BORIS must discover real positions from client data later.
  if (seeds.length<5) {
    const base=dirs[0];
    const fillers=[
      {title:`Тест спроса: ${base}`,focus:"Сделай тестовый вариант с акцентом на наиболее понятный подтверждённый сценарий спроса. Не придумывай ассортимент."},
      {title:`Новая кампания: ${base}`,focus:"Сделай вариант для запуска новой кампании. Используй только реальные позиции, если они есть в данных клиента."},
      {title:`Продажи: ${base}`,focus:"Сделай вариант с акцентом на обращение или звонок, используя только подтверждённые преимущества клиента."},
      {title:`Улучшить объявления: ${base}`,focus:"Сделай тестовую карточку как основу для улучшения текущих объявлений; не добавляй неподтверждённые товары или характеристики."},
    ];
    for (const f of fillers) { if (seeds.length>=5) break; seeds.push({direction:base,...f}); }
  }
  // Generic confirmed geo extraction: never whitelist client cities in UI code.
  // A city may be reused only when the client text explicitly labels it as "город"/"г.".
  const explicitCity=(description.match(/(?:^|[\n.;])\s*(?:город|г\.)\s+([А-ЯЁ][А-Яа-яЁё-]+(?:\s+[А-ЯЁ][А-Яа-яЁё-]+){0,2})(?=\s*(?:[,.;\n]|$))/m)?.[1] || "").trim();
  const splitDirectionGeo=(raw:string)=>{
    const value=String(raw||"").trim();
    const parts=value.split(/\s+/);
    const suffix=parts.length>1 ? parts[parts.length-1] : "";
    const suffixLooksCity=/^[А-ЯЁ][А-Яа-яЁё-]{2,}$/.test(suffix) && description.toLocaleLowerCase("ru").includes(suffix.toLocaleLowerCase("ru"));
    if (suffixLooksCity) return {direction:parts.slice(0,-1).join(" ").trim(),city:suffix};
    return {direction:value,city:explicitCity};
  };
  const icons:PromptExample["icon"][]=["target","layers","briefcase","check","palette"];
  return seeds.map((seed,index)=>{
    const rawDirection=seed.direction; const geo=splitDirectionGeo(rawDirection); const direction=geo.direction; const city=geo.city;
    const categoryHint=hintMap.get(cleanClientDirection(rawDirection).toLocaleLowerCase("ru")) || {};
    const categoryLeaf=String(categoryHint?.category_path||"").split(">").map((x:string)=>x.trim()).filter(Boolean).pop() || "";
    return ({
    icon:icons[index%icons.length],
    direction,
    categoryTemplateId:String(categoryHint?.template_id||""),
    categoryPath:String(categoryHint?.category_path||""),
    categoryParams:(categoryHint?.params && typeof categoryHint.params==="object") ? categoryHint.params : {},
    title:seed.title.length>42 ? seed.title.slice(0,40)+"…" : seed.title,
    note:["1 объявление", activeSet.has(rawDirection.toLocaleLowerCase("ru"))?"из активных объявлений":feedSet.has(rawDirection.toLocaleLowerCase("ru"))?"из текущих объявлений Avito":"из данных компании",categoryLeaf?`Avito: ${categoryLeaf}`:"Avito: уточним подкатегорию",city].filter(Boolean).join(" · "),
    goal:index%2 ? "Цель: получить обращение" : "Цель: получить звонок или сообщение",
    prompt:[
      `Сделай 1 боевое объявление по ${kind==="services"?"услуге":"товару"} «${direction}»${city?` в городе ${city}`:""}.`,
      seed.focus || "",
      "Используй только подтверждённые данные выбранного клиента, его преимущества, ассортимент и реальные характеристики.",
      advantages ? `Преимущества клиента уже сохранены в BORIS — используй только релевантные для «${direction}», без выдуманных обещаний.` : "Не придумывай цены, гарантии, сроки и характеристики.",
      "Сделай естественный продающий заголовок и описание, проверь категорию и обязательные параметры Avito.",
      "УНИКАЛИЗАЦИЯ ВНИЗУ ОПИСАНИЯ: добавь отдельный короткий конкретный пример задачи/сценария именно для этой карточки. Используй только подтверждённые город, товар/услугу и факты клиента; не выдумывай размеры, цены, сроки, кейсы или характеристики. Для массовой кампании меняй смысловой ракурс и формулировку этого нижнего примера между карточками, а не только отдельные слова.",
      "Подготовь ровно 3 релевантные фотографии для галереи и один первый продающий баннер.",
      "БАННЕР ДЛЯ ТЕЛЕФОНА: один крупный заголовок максимум 6 слов и максимум 2 строки; без мелкого текста, списков, телефонов, адресов, мелких преимуществ, кнопок и россыпи плашек. Главный объект не перекрывать текстом.",
      "Перед готовностью обязательно проверь соответствие картинки объявлению и читаемость баннера в размере мобильной карточки."
    ].filter(Boolean).join(" ")
    });
  });
}

function formatDuration(seconds:any){
  const total=Math.max(0,Number(seconds||0));
  if(total<60)return Math.round(total)+" сек";
  const minutes=Math.floor(total/60);
  const secs=Math.floor(total%60);
  if(minutes<60)return minutes+" мин"+(secs?" "+secs+" сек":"");
  const hours=Math.floor(minutes/60);
  const mins=minutes%60;
  return hours+" ч"+(mins?" "+mins+" мин":"");
}

function clientTaskSeconds(task:any,nowMs:number){
  if(task?.finished_at)return Number(task?.duration_total_seconds||0);
  const stamp=Date.parse(String(task?.created_at||""));
  return Number.isFinite(stamp)?Math.max(0,Math.floor((nowMs-stamp)/1000)):Number(task?.duration_total_seconds||0);
}

type CampaignMenuItem={label:string;note?:string;onClick:()=>void};
function CampaignMenu({label,items,testId}:{label:string;items:CampaignMenuItem[];testId:string}){
  return <details data-testid={testId} className="boris-campaign-menu" style={{position:"relative"}}>
    <summary style={{listStyle:"none",cursor:"pointer",display:"inline-flex",alignItems:"center",gap:8,minHeight:40,padding:"9px 13px",border:"1px solid #D7E2F5",borderRadius:999,background:"#fff",color:"#22304A",fontSize:12.5,fontWeight:900,boxShadow:"0 5px 18px rgba(39,73,125,.06)",userSelect:"none"}}>
      <span>{label}</span><span aria-hidden="true" style={{fontSize:14,color:"#4C6FAE"}}>⌄</span>
    </summary>
    <div className="boris-campaign-menu-panel" style={{position:"absolute",right:0,top:"calc(100% + 7px)",zIndex:80,width:"min(310px,88vw)",padding:7,border:"1px solid #DDE6F4",borderRadius:16,background:"#fff",boxShadow:"0 20px 56px rgba(17,35,66,.16)"}}>
      {items.map((item)=><button key={item.label} type="button" onClick={(e)=>{item.onClick();(e.currentTarget.closest("details") as HTMLDetailsElement|null)?.removeAttribute("open");}} style={{width:"100%",display:"grid",gap:2,textAlign:"left",border:0,borderRadius:11,background:"#fff",padding:"10px 11px",cursor:"pointer",color:"#172033",font:"inherit"}}>
        <strong style={{fontSize:12.5}}>{item.label}</strong>
        {item.note?<small style={{fontSize:10.5,lineHeight:1.35,color:"#667085"}}>{item.note}</small>:null}
      </button>)}
    </div>
  </details>;
}

export default function EcOverview() {
  const router = useRouter();
  const { selectedAccountId, selectedAccount, setSelectedAccount } = useAccounts();

  const [account, setAccount] = useState("");
  const [media, setMedia] = useState<any>({});
  const [cat, setCat] = useState<any>({});
  const [trend, setTrend] = useState<any>({ history: [] });
  const [campaigns, setCampaigns] = useState<any[]>([]);
  const [selectedCampaignId, setSelectedCampaignId] = useState<number>(0);
  const [historyAvailable,setHistoryAvailable]=useState(false);
  useEffect(()=>{const tab=new URLSearchParams(window.location.search).get("tab");if(tab==="tasks"||tab==="history")setWorkspaceTab(tab);},[]);
  const [workspaceTab, setWorkspaceTab] = useState<"projects"|"ads"|"history"|"tasks"|"automation"|"settings">("projects");
  const [showLaunchWorkspace,setShowLaunchWorkspace]=useState(false);
  const [showClientHelp, setShowClientHelp] = useState(false);
  // One account launch must never block another. Every initial Campaign launch
  // has its own foreground lock; after guardian/prepare the durable server job is
  // fully independent from the selected UI account.
  const [creatingByAccount, setCreatingByAccount] = useState<Record<string,boolean>>({});
  const createInFlight = useRef<Record<string,boolean>>({});
  const creating = Boolean(selectedAccountId && creatingByAccount[selectedAccountId]);
  const pendingCampaign = useRef<Record<string, number>>({});
  const [campaignBrief, setCampaignBrief] = useState("");
  const [sourceUrls, setSourceUrls] = useState("");
  const [sourceSections, setSourceSections] = useState("");
  const [bindProductData, setBindProductData] = useState(true);
  const [campaignPriceSource, setCampaignPriceSource] = useState<"company_or_site"|"company_price_list"|"website"|"range">("company_or_site");
  const [campaignPriceMin, setCampaignPriceMin] = useState("");
  const [campaignPriceMax, setCampaignPriceMax] = useState("");
  const [inventoryPolicy, setInventoryPolicy] = useState<any>({
    enabled:false,auto_apply:false,target_active_ads:0,price_source:"company_price_list",price_min:0,price_max:0,randomize_price:false,
    allow_create:true,allow_edit_existing:true,allow_reactivate:true,allow_update_price:true,allow_update_text:true,allow_update_media:true,allow_update_category:true,daily_mode:"maintain",
  });
  const [inventoryPlan, setInventoryPlan] = useState<any>(null);
  const [inventoryJob, setInventoryJob] = useState<any>(null);
  const [inventorySaving, setInventorySaving] = useState(false);
  const [inventoryPlanning, setInventoryPlanning] = useState(false);
  const [inventoryRunning, setInventoryRunning] = useState(false);
  const [sourceImportStatus, setSourceImportStatus] = useState("");
  const [sourceImportProgress, setSourceImportProgress] = useState<{done:number;total:number;running:boolean}>({done:0,total:0,running:false});
  const [sourceCatalogSummary, setSourceCatalogSummary] = useState<any>(null);
  const briefAccountRef = useRef("");
  const [briefStorageWarning, setBriefStorageWarning] = useState(false);
  useEffect(() => {
    const aid = selectedAccountId || "";
    briefAccountRef.current = aid;
    const saved = aid ? readCampaignDraft<string>("brief:" + aid) : undefined;
    const sourceDraft = aid ? readCampaignDraft<any>("source-contract:" + aid) : undefined;
    setCampaignBrief(typeof saved === "string" ? saved : "");
    setSourceUrls(String(sourceDraft?.sourceUrls || ""));
    setSourceSections(String(sourceDraft?.sourceSections || ""));
    setBindProductData(sourceDraft?.bindProductData !== false);
    setCampaignPriceSource(["company_or_site","company_price_list","website","range"].includes(String(sourceDraft?.campaignPriceSource || ""))
      ? sourceDraft.campaignPriceSource : "company_or_site");
    setCampaignPriceMin(String(sourceDraft?.campaignPriceMin || ""));
    setCampaignPriceMax(String(sourceDraft?.campaignPriceMax || ""));
    setInventoryPlan(null);
    setInventoryJob(null);
    setShowLaunchWorkspace(false);
    setSourceImportStatus("");
    setSourceImportProgress({done:0,total:0,running:false});
    setSourceCatalogSummary(null);
    setBriefStorageWarning(false);
  }, [selectedAccountId]);
  const updateCampaignBrief = (value: string) => {
    setCampaignBrief(value);
    const aid = selectedAccountId || "";
    if (aid && aid === briefAccountRef.current) {
      setBriefStorageWarning(!writeCampaignDraft("brief:" + aid, value));
    }
  };
  const sourceContractDraftValue = (patch:any={}) => ({
    sourceUrls,sourceSections,bindProductData,campaignPriceSource,campaignPriceMin,campaignPriceMax,...patch,
  });
  const persistSourceContractDraft = (next?: any) => {
    const aid = selectedAccountId || "";
    if (!aid || aid !== briefAccountRef.current) return;
    const value = next || sourceContractDraftValue();
    if (!writeCampaignDraft("source-contract:" + aid, value)) setBriefStorageWarning(true);
  };
  const [selectedPromptExample, setSelectedPromptExample] = useState<PromptExample|null>(null);
  const [promptKind, setPromptKind] = useState<"services" | "products">("services");
  const [campaignContext, setCampaignContext] = useState<any>({});
  const [projectContext, setProjectContext] = useState<any>({facts:[]});
  const [campaignTimeline, setCampaignTimeline] = useState<any[]>([]);
  const [campaignTasks, setCampaignTasks] = useState<any[]>([]);
  const [globalActiveCampaigns, setGlobalActiveCampaigns] = useState<any[]>([]);
  useEffect(() => {
    let alive = true;
    const refreshGlobalActive = async () => {
      try {
        const result = await apiGet("/api/accounts/active-campaigns");
        if (alive && result?.status === "ok") setGlobalActiveCampaigns(Array.isArray(result.items) ? result.items : []);
      } catch {
        // Keep the last known cross-account summary during a transient refresh failure.
      }
    };
    void refreshGlobalActive();
    const timer = window.setInterval(() => { void refreshGlobalActive(); }, 5000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);
  const [taskCursor,setTaskCursor]=useState<string|null>(null),[taskPageBusy,setTaskPageBusy]=useState(false);
  const selectedAccountRef=useRef(selectedAccountId);selectedAccountRef.current=selectedAccountId;
  async function moreTasks(){
    if(!taskCursor||!selectedAccountId)return;
    const aid=selectedAccountId;setTaskPageBusy(true);
    try{
      const r=await apiGet('/api/campaign/tasks?account_id='+encodeURIComponent(aid)+'&limit=60&cursor='+encodeURIComponent(taskCursor));
      if(selectedAccountRef.current!==aid)return;
      if(r.status==='ok'){setCampaignTasks(old=>[...old,...(r.items||[])].filter((x,i,a)=>a.findIndex(y=>String(y.id)===String(x.id))===i));setTaskCursor(r.next_cursor||null);}
    }finally{if(selectedAccountRef.current===aid)setTaskPageBusy(false);}
  }

  const [taskFilter, setTaskFilter] = useState<"all"|"active"|"attention"|"done"|"failed">("all");
  const [clockNow, setClockNow] = useState(()=>Date.now());
  const [campaignLaunchStatusByAccount, setCampaignLaunchStatusByAccount] = useState<Record<string,string>>({});
  const [campaignLaunchStartedAtByAccount, setCampaignLaunchStartedAtByAccount] = useState<Record<string,number>>({});
  const campaignLaunchStatus = selectedAccountId ? String(campaignLaunchStatusByAccount[selectedAccountId]||"") : "";
  const campaignLaunchStartedAt = selectedAccountId ? Number(campaignLaunchStartedAtByAccount[selectedAccountId]||0) || null : null;
  const [feedReconcile, setFeedReconcile] = useState<any>({});
  const [campaignReadiness, setCampaignReadiness] = useState<any>({});
  const [aiMarketer, setAiMarketer] = useState<any>({});
  const [strategy, setStrategy] = useState<any>({ target_active_ads: 0, directions: [], cities: [], use_price_list: true, use_site_services: true, unique_texts: true, unique_images: true, require_verified_price: true, require_preflight: true });
  useEffect(()=>{
    if(!selectedAccountId || typeof window==="undefined") return;
    const fromAudit=new URLSearchParams(window.location.search).get("from")==="audit";
    if(!fromAudit) return;
    let prefill:any=null;
    try{prefill=JSON.parse(localStorage.getItem("boris_campaign_audit_prefill")||"null");}catch{}
    if(!prefill || typeof prefill!=="object") return;
    const prompt=String(prefill.prompt||"").trim();
    if(prompt){
      setSelectedPromptExample(null);
      updateCampaignBrief(formatPromptForEditor(prompt));
    }
    const query=String(prefill.query||"").trim();
    const region=String(prefill.region||"").trim();
    const low=Number(prefill?.recommended_ads?.low||0);
    const high=Number(prefill?.recommended_ads?.high||0);
    const grounded=prefill?.recommended_ads?.grounded!==false;
    const target=grounded&&low>0&&high>=low?Math.round((low+high)/2):0;
    setStrategy((current:any)=>({
      ...current,
      target_active_ads:target||current.target_active_ads||0,
      directions:query?[query]:(current.directions||[]),
      cities:region?[region]:(current.cities||[]),
    }));
    setWorkspaceTab("projects");
    setShowLaunchWorkspace(true);
    try{localStorage.removeItem("boris_campaign_audit_prefill");}catch{}
    window.setTimeout(()=>document.getElementById("campaign-create")?.scrollIntoView({behavior:"smooth",block:"start"}),120);
  },[selectedAccountId]);
  const [strategySaving, setStrategySaving] = useState(false);
  const [strategySaved, setStrategySaved] = useState(false);
  const [strategyLoaded, setStrategyLoaded] = useState(false);
  const promptExamples = clientPromptExamples(selectedAccount, promptKind, campaignContext);
  const themes = campaignThemes(campaignContext, promptKind, strategy.require_verified_price !== false);

  const updateStrategy = (key: string, value: any) => {
    setStrategy((current: any) => ({ ...current, [key]: value }));
    setStrategySaved(false);
  };

  const saveStrategy = async () => {
    if (!account) return;
    setStrategySaving(true);
    setErr("");
    try {
      const response = await apiFetch(`/api/campaign/strategy?account_id=${encodeURIComponent(account)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(strategy),
      });
      const body = await response.json();
      if (!response.ok || body?.status === "error") throw new Error(body?.message || "Не удалось сохранить стратегию");
      setStrategy(body.strategy || strategy);
      setStrategySaved(true);
    } catch (error: any) {
      setErr(error?.message || "Не удалось сохранить стратегию");
    } finally {
      setStrategySaving(false);
    }
  };

  const refreshInventoryPlan = async (aid?: string) => {
    const target=String(aid || account || selectedAccountId || "").trim();
    if (!target) return;
    setInventoryPlanning(true);
    try {
      const result=await apiGet(`/api/campaign/inventory-policy/plan?account_id=${encodeURIComponent(target)}`);
      if (result?.status==="ok") setInventoryPlan(result);
    } catch (error:any) {
      setErr(error?.message || "Не удалось рассчитать ежедневный план");
    } finally {
      setInventoryPlanning(false);
    }
  };


  const refreshInventoryStatus = async (aid?: string) => {
    const target=String(aid || account || selectedAccountId || "").trim();
    if(!target) return null;
    try {
      const result=await apiGet("/api/campaign/inventory-policy/status?account_id="+encodeURIComponent(target));
      if(result?.status==="ok") {
        setInventoryJob(result.job || null);
        return result.job || null;
      }
    } catch {}
    return null;
  };

  const runInventoryNow = async () => {
    const target=String(account || selectedAccountId || "").trim();
    if(!target) return;
    setInventoryRunning(true); setErr("");
    try {
      const saveResponse=await apiFetch("/api/campaign/inventory-policy?account_id="+encodeURIComponent(target),{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(inventoryPolicy)});
      const saveBody=await saveResponse.json();
      if(!saveResponse.ok || saveBody?.status==="error") throw new Error(saveBody?.message || "Не удалось сохранить режим");
      setInventoryPolicy(saveBody.policy || inventoryPolicy);
      const result=await apiPost("/api/campaign/inventory-policy/run?account_id="+encodeURIComponent(target),{confirmed:true});
      if(result?.status!=="ok") throw new Error(result?.message || "Не удалось запустить поддержание профиля");
      setInventoryJob(result.job || null);
      await refreshInventoryPlan(target);
    } catch(error:any) {
      setErr(error?.message || "Не удалось запустить поддержание профиля");
    } finally {
      setInventoryRunning(false);
    }
  };

  const saveInventoryPolicy = async () => {
    if (!account) return;
    setInventorySaving(true); setErr("");
    try {
      const response=await apiFetch(`/api/campaign/inventory-policy?account_id=${encodeURIComponent(account)}`,{
        method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(inventoryPolicy),
      });
      const body=await response.json();
      if(!response.ok || body?.status==="error") throw new Error(body?.message || "Не удалось сохранить режим поддержания");
      setInventoryPolicy(body.policy || inventoryPolicy);
      await refreshInventoryPlan(account);
    } catch(error:any) {
      setErr(error?.message || "Не удалось сохранить режим поддержания");
    } finally {
      setInventorySaving(false);
    }
  };
  const [loading, setLoading] = useState(true);
  const accountLoadSequence = useRef(0);
  const campaignScopeSequence = useRef(0);
  const selectedCampaignRef = useRef(0);
  const singleCampaignScopeRetryRef = useRef("");
  const activeAccountRef = useRef(selectedAccountId);
  activeAccountRef.current = selectedAccountId;
  const campaignAccountRef = useRef(account);
  campaignAccountRef.current = account;
  const [err, setErr] = useState("");
  const responseCampaignId = (body: any): number => Number(body?.campaign_id ?? body?.campaign?.id ?? body?.data?.campaign_id ?? 0) || 0;
  const refreshCampaignScope = (campaignId: number, campaignAccount: string) => {
    const requestedCampaignId = Number(campaignId);
    const requestedAccount = String(campaignAccount || selectedAccountId || account || "").trim();
    selectedCampaignRef.current = requestedCampaignId;
    const scopeRequest = ++campaignScopeSequence.current;
    if (!requestedAccount || !Number.isSafeInteger(requestedCampaignId) || requestedCampaignId <= 0) {
      setCampaignReadiness({});
      setAiMarketer({});
      return;
    }
    // SAME_SCOPE_REFRESH_NO_BLANK_V1: repeated hydration of the same
    // selected campaign (notably the bounded one-campaign retry) must not erase
    // already-valid readiness/AI state while the replacement request is in
    // flight. Account/selection changes still clear state in their own effects.
    // This prevents a successful 200 projection from flashing back to an empty
    // shell when a later same-scope response is superseded by the sequence fence.
    // CAMPAIGN_SCOPE_RESPONSE_FENCE_V2/V3: account changes reset the
    // selected campaign, while repeated reads of the same account+campaign are
    // equivalent. Fence by current identity, not by request ordering.
    // CURRENT_SCOPE_CAMPAIGN_FENCE_V4: Campaign IDs are globally unique and
    // account changes synchronously reset selectedCampaignRef to 0. Therefore the
    // selected Campaign identity is the durable response fence. A second React
    // account ref can lag during hydration and must not discard a valid 200.
    // Tenant/account authorization remains enforced by the backend request itself.
    const isCurrentScope = () => selectedCampaignRef.current === requestedCampaignId;
    const rejectScopeMismatch = (returnedId: number) => {
      if (!isCurrentScope() || returnedId <= 0 || returnedId === requestedCampaignId) return false;
      setCampaignReadiness({ status: "error", code: "CAMPAIGN_SCOPE_MISMATCH", campaign_id: returnedId, requested_campaign_id: requestedCampaignId });
      setAiMarketer({ status: "error", code: "CAMPAIGN_SCOPE_MISMATCH", campaign_id: returnedId, requested_campaign_id: requestedCampaignId });
      setErr(`Сбой области Campaign #${requestedCampaignId}: backend вернул другую кампанию`);
      return true;
    };
    // Readiness and AI Marketer have independent latency. A slow AI projection
    // must never hide an already-valid readiness response from the client.
    void apiGet(`/api/campaign/${requestedCampaignId}/readiness?account_id=${encodeURIComponent(requestedAccount)}`).then((readiness) => {
      if (!isCurrentScope()) return;
      const readinessId = responseCampaignId(readiness);
      if (readiness?.status === "ok" && rejectScopeMismatch(readinessId)) return;
      setCampaignReadiness(readiness || {});
    });
    void apiGet(`/api/campaign/${requestedCampaignId}/ai-marketer?account_id=${encodeURIComponent(requestedAccount)}`).then((marketer) => {
      if (!isCurrentScope()) return;
      const marketerId = responseCampaignId(marketer);
      if (marketer?.status === "ok" && rejectScopeMismatch(marketerId)) return;
      setAiMarketer(marketer || {});
    });
  };
  // Campaign selection must load its scoped data deterministically.
  const handleCampaignChange = (nextId: number) => {
    const requested = Number.isSafeInteger(nextId) && nextId > 0 ? nextId : 0;
    selectedCampaignRef.current = requested;
    setCampaignReadiness({});
    setAiMarketer({});
    // Single refresh authority: selectedCampaignId effect below.
    // Calling refresh here as well creates duplicate request sequences and can
    // transiently clear the mobile UI after a valid response.
    setSelectedCampaignId(requested);
  };

  useEffect(() => {
    accountLoadSequence.current += 1;
    campaignScopeSequence.current += 1;
    selectedCampaignRef.current = 0;
    singleCampaignScopeRetryRef.current = "";
    setCampaigns([]); setSelectedCampaignId(0); setMedia({}); setCat({}); setTrend({ history: [] }); setCampaignContext({}); setProjectContext({facts:[]}); setCampaignTimeline([]); setHistoryAvailable(false); setCampaignTasks([]); setTaskCursor(null); setFeedReconcile({}); setCampaignReadiness({}); setAiMarketer({}); setSelectedPromptExample(null); setErr(""); setAccount(selectedAccountId || ""); setStrategySaved(false); setStrategyLoaded(false);
    if (!selectedAccountId) {
      setStrategy({ target_active_ads: 0, directions: [], cities: [], use_price_list: true, use_site_services: true, unique_texts: true, unique_images: true, require_verified_price: true, require_preflight: true });
      setInventoryPolicy({enabled:false,auto_apply:false,target_active_ads:0,price_source:"company_price_list",price_min:0,price_max:0,randomize_price:false,allow_create:true,allow_edit_existing:true,allow_reactivate:true,allow_update_price:true,allow_update_text:true,allow_update_media:true,allow_update_category:true,daily_mode:"maintain"});
      return;
    }
    void apiGet(`/api/campaign/strategy?account_id=${encodeURIComponent(selectedAccountId)}`).then((result: any) => {
      if (result?.status === "ok") {
        const persisted=Boolean(result.strategy && Object.keys(result.strategy).length);
        if (result.strategy) setStrategy((current: any) => ({ ...current, ...result.strategy }));
        setStrategySaved(persisted);
        setStrategyLoaded(true);
      }
    }).catch(()=>setStrategyLoaded(true));
    void apiGet(`/api/campaign/inventory-policy?account_id=${encodeURIComponent(selectedAccountId)}`).then((result:any)=>{
      if(result?.status==="ok" && result.policy) setInventoryPolicy((current:any)=>({...current,...result.policy}));
    });
    void apiGet(`/api/campaign/project-context?account_id=${encodeURIComponent(selectedAccountId)}`).then((result:any)=>{
      if(result?.status==="ok") setProjectContext(result);
    }).catch(()=>setProjectContext({facts:[]}));
    void apiGet(`/api/campaign/timeline?account_id=${encodeURIComponent(selectedAccountId)}&limit=60`).then((result:any)=>{
      if(selectedAccountRef.current!==selectedAccountId)return;
      if(result?.status==="ok") {setCampaignTimeline(Array.isArray(result.items)?result.items:[]);setHistoryAvailable(result.history_contract===2);}
    }).catch(()=>setCampaignTimeline([]));
    void apiGet(`/api/campaign/tasks?account_id=${encodeURIComponent(selectedAccountId)}&limit=60`).then((result:any)=>{
      if(selectedAccountRef.current!==selectedAccountId)return;
      if(result?.status==="ok") {setCampaignTasks(Array.isArray(result.items)?result.items:[]);setTaskCursor(result.next_cursor||null);}
    }).catch(()=>setCampaignTasks([]));
    void refreshInventoryStatus(selectedAccountId);
  }, [selectedAccountId]);

  useEffect(() => {
    const active=Boolean(inventoryJob && ["queued","running","validating"].includes(String(inventoryJob.status||"")));
    if(!active || !selectedAccountId) return;
    const timer=window.setInterval(()=>{ void refreshInventoryStatus(selectedAccountId); void refreshInventoryPlan(selectedAccountId); },2500);
    return ()=>window.clearInterval(timer);
  }, [inventoryJob?.status, inventoryJob?.done, inventoryJob?.total, selectedAccountId]);

  useEffect(() => {
    const clock=window.setInterval(()=>setClockNow(Date.now()),1000);
    return ()=>window.clearInterval(clock);
  }, []);

  useEffect(() => {
    if(!selectedAccountId) return;
    const active=campaignTasks.some((task:any)=>["queued","running","validating","awaiting_confirmation"].includes(String(task?.status||"")));
    if(!active) return;
    const timer=window.setInterval(()=>{
      void apiGet(`/api/campaign/tasks?account_id=${encodeURIComponent(selectedAccountId)}&limit=60`).then((result:any)=>{
        if(selectedAccountRef.current!==selectedAccountId)return;
        if(result?.status==="ok") setCampaignTasks(old=>{const fresh=Array.isArray(result.items)?result.items:[];return [...fresh,...old.filter(x=>!fresh.some((y:any)=>String(y.id)===String(x.id)))];});
      }).catch(()=>{});
    },3000);
    return ()=>window.clearInterval(timer);
  }, [selectedAccountId, campaignTasks]);

  const load = useCallback(async (silent = false) => {
    const request = ++accountLoadSequence.current;
    if (!account) { if (!silent) setLoading(false); return; }
    if (!silent) setLoading(true);
    setErr("");
    const [m, c, cc, tr, ctx] = await Promise.all([
      apiGet("/api/media/stats?account_id=" + account),
      apiGet("/api/catalog/summary?account_id=" + account),
      apiGet("/api/campaign/list?account_id=" + encodeURIComponent(account)),
      apiGet("/api/catalog/overview-trend?account_id=" + encodeURIComponent(account)),
      apiGet("/api/campaign/context?account_id=" + encodeURIComponent(account)),
    ]);
    if (request !== accountLoadSequence.current || account !== activeAccountRef.current) return;
    if (m.status === "ok") setMedia(m);
    if (c.status === "ok") setCat(c.summary || {});
    const canonicalCampaigns = cc.status === "ok" && Array.isArray(cc.campaigns) ? cc.campaigns : [];
    const reconciled: number[] = cc.status === "ok" && cc.feed_reconcile && Array.isArray(cc.feed_reconcile.visible_campaign_ids)
      ? Array.from(new Set<number>(cc.feed_reconcile.visible_campaign_ids.map((id:any) => Number(id)).filter((id:number) => Number.isSafeInteger(id) && id > 0)))
      : [];
    const missingReconciledIds = reconciled.filter((id:number) => !canonicalCampaigns.some((campaign:any) => Number(campaign?.id) === id));
    const hydratedReconciled = missingReconciledIds.length
      ? (await Promise.all(missingReconciledIds.slice(0, 8).map((id:number) => apiGet(`/api/campaign/${id}?account_id=${encodeURIComponent(account)}`))))
          .map((detail:any) => detail?.status === "ok" ? detail?.campaign : null)
          .filter((campaign:any) => campaign && campaign.status !== "superseded" && Number(campaign.id) > 0)
      : [];
    const visibleCampaigns = [...canonicalCampaigns, ...hydratedReconciled]
      .filter((campaign:any, index:number, rows:any[]) => Number(campaign?.id) > 0 && rows.findIndex((item:any) => Number(item?.id) === Number(campaign?.id)) === index);
    if (cc.status === "ok") { setCampaigns(visibleCampaigns); setFeedReconcile(cc.feed_reconcile || {}); }
    if (tr.status === "ok") setTrend(tr);
    if (ctx.status === "ok") { setCampaignContext(ctx); setPromptKind(inferCampaignKind(ctx)); }
    const currentSelected = selectedCampaignRef.current;
    const feedProjection = cc.feed_reconcile || {};
    const owningCampaignId = Number(feedProjection.owning_campaign_id ?? ctx?.owning_campaign_id ?? 0) || 0;
    const resolvedCampaignId = resolveOwningCampaignId(visibleCampaigns, { campaign_id: owningCampaignId }, currentSelected)
      || deterministicCurrentCampaignId(visibleCampaigns);
    if (resolvedCampaignId > 0 && resolvedCampaignId !== currentSelected) {
      selectedCampaignRef.current = resolvedCampaignId;
      setSelectedCampaignId(resolvedCampaignId);
    }
    const failures = [m, c, cc, tr, ctx].filter((item: any) => item?.status === "error");
    if (failures.length) setErr(failures[0]?.message || "Часть данных центра объявлений недоступна");
    if (!silent) setLoading(false);
  }, [account]);
  useEffect(() => { void load(); return () => { accountLoadSequence.current += 1; }; }, [load]);

  // CAMPAIGN_OVERVIEW_PROGRESS_POLL_V1: while any durable campaign job is
  // active, refresh only server truth in the background. The page stays stable,
  // and a reload/browser close never loses progress because the source is DB.
  useEffect(() => {
    const active = campaigns.some((campaign:any) => ["queued","running","validating"].includes(String(campaign?.active_job?.status || "")));
    if (!active || !account) return;
    const timer = window.setInterval(() => { void load(true); }, 2500);
    return () => window.clearInterval(timer);
  }, [campaigns, account, load]);

  useEffect(() => {
    if (selectedCampaignId > 0) {
      refreshCampaignScope(selectedCampaignId, String(selectedAccountId || account || ""));
    } else {
      setCampaignReadiness({});
      setAiMarketer({});
    }
  }, [account, selectedAccountId, selectedCampaignId]);

  // SINGLE_CAMPAIGN_SCOPE_RETRY_V1: a one-campaign account has no alternate
  // selector value the user can switch to in order to retrigger hydration.
  // Account/list state may settle in a different React commit than the initial
  // selection effect, so perform one bounded scope refresh after both account
  // and the sole selectable campaign are stable. This is generic and never
  // bypasses account/tenant checks.
  useEffect(() => {
    if (!account || !selectedAccountId || account !== selectedAccountId || campaigns.length !== 1) return;
    const onlyId = Number(campaigns[0]?.id || 0);
    if (!Number.isSafeInteger(onlyId) || onlyId <= 0) return;
    // SINGLE_CAMPAIGN_REF_SYNC_V2: React state and the imperative scope
    // ref can settle in different commits after an account reset. Always repair
    // the ref first, even when selectedCampaignId already equals the sole ID;
    // otherwise valid 200 readiness responses can be discarded as stale.
    if (selectedCampaignRef.current !== onlyId) {
      selectedCampaignRef.current = onlyId;
    }
    if (selectedCampaignId !== onlyId) {
      setSelectedCampaignId(onlyId);
      return;
    }
    const retryKey = account + ":" + onlyId;
    if (singleCampaignScopeRetryRef.current === retryKey) return;
    singleCampaignScopeRetryRef.current = retryKey;
    const timer = window.setTimeout(() => {
      if (campaignAccountRef.current === account && selectedCampaignRef.current === onlyId) {
        refreshCampaignScope(onlyId, account);
      }
    }, 350);
    return () => window.clearTimeout(timer);
  }, [account, selectedAccountId, campaigns, selectedCampaignId]);

  const applySourceCatalogSummary = (parsed:any, scopeAccount:string) => {
    if (selectedAccountRef.current !== scopeAccount) return;
    const products=Array.isArray(parsed?.products)?parsed.products:[];
    const withPrice=products.filter((p:any)=>Number(p?.price||0)>0 || /\d/.test(String(p?.price_raw||""))).length;
    const withPhotos=products.filter((p:any)=>(Array.isArray(p?.images)&&p.images.length>0)||Boolean(p?.image)).length;
    const withCharacteristics=products.filter((p:any)=>p?.characteristics && typeof p.characteristics==="object" && Object.keys(p.characteristics).length>0).length;
    setSourceCatalogSummary({total:products.length,withPrice,withPhotos,withCharacteristics});
  };

  const ensureSourceCatalog = async (urls: string[], launchAccount: string) => {
    if (!urls.length || !launchAccount) return;
    const updateImportView=(statusText:string,progress?:{done:number;total:number;running:boolean})=>{
      if(selectedAccountRef.current!==launchAccount)return;
      if(progress)setSourceImportProgress(progress);
      setSourceImportStatus(statusText);
    };
    const norm=(v:string)=>String(v||"").trim().replace(/\/$/,"");
    const parsed=await apiGet("/api/parser/parsed_products?account_id="+encodeURIComponent(launchAccount)).catch(()=>null);
    const stored=(Array.isArray(parsed?.source_urls)?parsed.source_urls:(parsed?.source_url?[parsed.source_url]:[])).map((x:any)=>norm(String(x)));
    const reusable=Array.isArray(parsed?.products) && parsed.products.length>0 && urls.every((u)=>stored.some((s:string)=>norm(u).startsWith(s)||s.startsWith(norm(u))));
    if(reusable){
      applySourceCatalogSummary(parsed,launchAccount);
      updateImportView("Каталог сайта уже загружен: "+parsed.products.length+" товаров",{done:parsed.products.length,total:parsed.products.length,running:false});
      return;
    }
    const scope="source-import:"+launchAccount;
    const saved=readCampaignDraft<any>(scope);
    let taskId=Number(saved?.taskId||0);
    if(!taskId){
      const task=await apiPost("/api/tasks/create",{account_id:launchAccount,task_type:"parse_site",payload:{urls,account_id:launchAccount,limit:500}});
      taskId=Number(task?.task_id||0);
      if(!taskId) throw new Error("Не удалось запустить импорт сайта");
      writeCampaignDraft(scope,{taskId,urls});
      updateImportView("BORIS изучает сайт",{done:0,total:0,running:true});
    }
    for(let attempt=0;attempt<120;attempt++){
      const status=await apiGet("/api/tasks/status/"+taskId);
      const result=status?.result||{};
      const done=Number(result.done||0); const total=Number(result.total||0);
      updateImportView(total ? "Импорт сайта: "+done+" из "+total : "BORIS изучает сайт",{done,total,running:status?.status!=="done" && status?.status!=="error"});
      if(status?.status==="error"){
        updateImportView("",{done,total,running:false});
        throw new Error(status?.error_message||"Ошибка импорта сайта");
      }
      if(status?.status==="done"){
        clearCampaignDraft(scope);
        const fresh=await apiGet("/api/parser/parsed_products?account_id="+encodeURIComponent(launchAccount)).catch(()=>null);
        if(fresh) applySourceCatalogSummary(fresh,launchAccount);
        const finalTotal=total||Number(fresh?.products?.length||0);
        updateImportView("Импорт сайта завершён",{done:finalTotal,total:finalTotal,running:false});
        return;
      }
      await new Promise((resolve)=>setTimeout(resolve,3000));
    }
    throw new Error("Импорт сайта ещё выполняется. После обновления BORIS продолжит ту же задачу.");
  };

  const createFeedFactory = async () => {
    const launchAccount=String(account||"").trim();
    if (!launchAccount || launchAccount !== selectedAccountId) { setErr("Сначала выберите аккаунт"); return; }
    if (createInFlight.current[launchAccount]) return;
    if (!campaignBrief.trim()) { setErr("Опишите задачу для рекламной кампании обычными словами"); return; }
    createInFlight.current[launchAccount]=true;
    setCreatingByAccount(prev=>({...prev,[launchAccount]:true}));
    setErr("");
    const launchStartedAt=Date.now();
    setCampaignLaunchStartedAtByAccount(prev=>({...prev,[launchAccount]:launchStartedAt}));
    setCampaignLaunchStatusByAccount(prev=>({...prev,[launchAccount]:"Задача принята. BORIS проверяет материалы и данные клиента…"}));
    const setLaunchStatus=(message:string)=>{
      setCampaignLaunchStatusByAccount(prev=>({...prev,[launchAccount]:message}));
    };
    const pendingScope = "pending-create:" + launchAccount;
    const sourceUrlList=sourceUrls.split(/\n|,/).map((x:string)=>x.trim()).filter(Boolean);
    try {
      persistSourceContractDraft();
      await ensureSourceCatalog(sourceUrlList,launchAccount);
      setLaunchStatus("Данные проверены. BORIS создаёт проект и разбирает задачу…");
      // Resume the created campaign if the second request failed, including
      // after reload. Pending identities are account-scoped, so another client
      // may launch in parallel without reusing this Campaign.
      const saved = readCampaignDraft<{ campaignId: number }>(pendingScope);
      let cid = pendingCampaign.current[launchAccount] || Number(saved?.campaignId);
      if (!Number.isSafeInteger(cid) || cid <= 0) {
        const r = await apiPost("/api/campaign/create", { name: "Рекламная кампания · " + new Date().toLocaleDateString("ru-RU"), account_id: launchAccount, scenario_type: "feed_factory", source_type: "client" });
        cid = Number(r?.campaign?.id);
        if (!Number.isSafeInteger(cid) || cid <= 0) throw new Error("Кампания не создана");
        pendingCampaign.current[launchAccount] = cid;
        if (!writeCampaignDraft(pendingScope, { campaignId: cid }) && selectedAccountRef.current===launchAccount) setBriefStorageWarning(true);
      }
      // The overview promises one safe test card first. Mass quantity is parsed
      // from the owner's brief (e.g. «30 объявлений»); do not silently multiply
      // an otherwise single-card request into three variants.
      const selectedPromptStillExact=Boolean(selectedPromptExample && campaignBrief.trim()===formatPromptForEditor(selectedPromptExample.prompt).trim());
      const categoryHints=selectedPromptStillExact && selectedPromptExample?.direction && selectedPromptExample?.categoryTemplateId
        ? {[selectedPromptExample.direction]:{template_id:selectedPromptExample.categoryTemplateId,path:selectedPromptExample.categoryPath||""}} : {};
      const observedParams=selectedPromptStillExact && selectedPromptExample?.categoryParams && Object.keys(selectedPromptExample.categoryParams).length
        ? selectedPromptExample.categoryParams : {};
      const sourceSectionList=sourceSections.split(/\n/).map((x:string)=>x.trim()).filter(Boolean);
      const briefResult=await apiPost(`/api/campaign/${cid}/workflow/brief`, {
        brief: campaignBrief.trim(),
        variants: 1,
        category_hints: categoryHints,
        defaults: Object.keys(observedParams).length ? {params:observedParams} : {},
        source_urls: sourceUrlList,
        source_sections: sourceSectionList,
        bind_product_data: bindProductData,
        use_parsed_products: true,
        price_source: campaignPriceSource,
        price_min: Math.max(0,Number(campaignPriceMin||0)),
        price_max: Math.max(0,Number(campaignPriceMax||0)),
        trust_owner_source_prices: true,
      });
      setLaunchStatus((briefResult?.questions||[]).length
        ? "План создан. BORIS запускает задачу и сам остановится только на обязательном уточнении…"
        : "План создан. BORIS запускает фоновую подготовку объявлений…");
      const guardianResult=await apiPost(`/api/campaign/${cid}/guardian/prepare`,{account_id:launchAccount});
      const launchedJob=guardianResult?.job;
      if (!launchedJob?.id) throw new Error("Фоновая задача BORIS не запущена");
      setLaunchStatus(launchedJob?.status==="queued"
        ? `Задача №${launchedJob.id} запущена и стоит в очереди. Можно переключаться на следующий аккаунт.`
        : `Задача №${launchedJob.id} запущена. Можно переключаться на следующий аккаунт.`);
      if (readCampaignDraft("brief:" + launchAccount) === campaignBrief) clearCampaignDraft("brief:" + launchAccount);
      clearCampaignDraft("source-contract:" + launchAccount);
      delete pendingCampaign.current[launchAccount];
      if (readCampaignDraft<{ campaignId: number }>(pendingScope)?.campaignId === cid) clearCampaignDraft(pendingScope);
      if (selectedAccountRef.current === launchAccount) router.push("/dashboard/campaign/" + cid);
    } catch (e:any) {
      setCampaignLaunchStatusByAccount(prev=>({...prev,[launchAccount]:""}));
      setCampaignLaunchStartedAtByAccount(prev=>{
        const next={...prev}; delete next[launchAccount]; return next;
      });
      if (selectedAccountRef.current === launchAccount) {
        setErr(e?.message || "Не удалось создать кампанию");
      }
    } finally {
      delete createInFlight.current[launchAccount];
      setCreatingByAccount(prev=>({...prev,[launchAccount]:false}));
    }
  };

  const attention = [
    { n: cat.no_price || 0, t: "товаров без цены", src: "rule",
      go: "/dashboard/campaign/catalog" },
    { n: cat.no_photo || 0, t: "товаров без фотографий", src: "rule",
      go: "/dashboard/campaign/catalog" },
    { n: media.missing || 0, t: "файлов недоступны в хранилище", src: "integration",
      go: "/dashboard/campaign/media" },
    { n: cat.drafts || 0, t: "товаров в черновиках", src: "stats",
      go: "/dashboard/campaign/catalog" },
  ].filter((a) => a.n > 0);

  const mediaTotal = Number(media?.total || 0);
  const mediaAi = Number((media?.by_source || {}).ai || 0);
  const catalogTotal = Number(cat?.total || 0);
  const feedBackedTotal = Number(feedReconcile?.current_feed_count ?? feedReconcile?.mapped_current_feed_count ?? campaignContext?.feed_item_count ?? 0);
  const showCatalogWarning = catalogTotal <= 0 && mediaTotal <= 0 && feedBackedTotal <= 0;

  const primaryCards = [
    { icon: <Icon name="ads" size={22} />, tone: "blue", title: "Создать новые объявления",
      text: "Опишите задачу обычными словами. BORIS соберёт структуру, тексты, материалы и подготовит объявления к проверке.",
      tags: ["С нуля", "Минимум действий"], cta: "Начать", go: "#campaign-create" },
    { icon: <Icon name="pen" size={22} />, tone: "violet", title: "Изменить действующие объявления",
      text: "Массово измените цену, описание, фотографии и характеристики уже опубликованных объявлений выбранного аккаунта.",
      tags: ["Массовая правка", "Avito"], cta: "Изменить", go: "/listings?mode=mass-edit" },
    { icon: <Icon name="target" size={22} />, tone: "orange", title: "Улучшить результат",
      text: "Сравните заголовки, тексты и креативы по реальным просмотрам, контактам и конверсии. BORIS запоминает победителей.",
      tags: ["АБ тесты", "Конверсия"], cta: "Улучшить", go: "/dashboard/campaign/ab-tests" },
    { icon: <Icon name="chart" size={22} />, tone: "green", title: "Посмотреть результат",
      text: "Покажем, какие объявления, товары и города дают обращения и где результат проседает.",
      tags: ["Аналитика", "30 дней"], cta: "Открыть аналитику", go: "/dashboard/analytics" },
  ];

  const toolCards = [
    { icon: <Icon name="layers" size={20} />, tone: "green", title: "Ассортимент", text: "Товары, цены и характеристики.", go: "/dashboard/campaign/catalog" },
    { icon: <Icon name="image" size={20} />, tone: "blue", title: "Фотографии", text: "Исходники, рендеры и папки.", go: "/dashboard/campaign/media" },
    { icon: <Icon name="globe" size={20} />, tone: "cyan", title: "Импорт с сайта", text: "Перенос ассортимента с сайта.", go: "/dashboard/import-site" },
    { icon: <Icon name="zap" size={20} />, tone: "purple", title: "AI-фотостудия", text: "Новые ракурсы и изображения.", go: "/dashboard/campaign/photo" },
    { icon: <Icon name="palette" size={20} />, tone: "pink", title: "Баннеры", text: "Первый продающий кадр для галереи.", go: "/dashboard/campaign/banners" },
    { icon: <Icon name="pen" size={20} />, tone: "cyan", title: "Тексты", text: "Описания по фактам бизнеса.", go: "/dashboard/campaign/texts" },
    { icon: <Icon name="send" size={20} />, tone: "indigo", title: "Проекты и публикация", text: "Проверка готовых проектов перед запуском.", go: "#campaign-projects" },
    { icon: <Icon name="plug" size={20} />, tone: "purple", title: "Подключения", text: "Avito и передача обращений в продажи.", go: "/dashboard/crm/integrations" },
  ];

  const history = Array.isArray(trend?.history) ? trend.history : [];
  const currentMetrics = trend?.current || {
    media_total: mediaTotal,
    media_ai: mediaAi,
    catalog_total: catalogTotal,
    ready: Number(cat.ready || 0),
    no_price: Number(cat.no_price || 0),
    no_photo: Number(cat.no_photo || 0),
    drafts: Number(cat.drafts || 0),
    missing: Number(media.missing || 0),
  };
  const metricCards = [
    { key:"media_total", label:"Мои материалы", note:"Черновики и заготовки", icon:<Icon name="image" size={21}/>, accent:"#7C3AED", soft:"#F6F1FF", goodUp:true, go:"/dashboard/campaign/media" },
    { key:"media_ai", label:"ИИ-изображения", note:"Снято и готово к использованию", icon:<Icon name="zap" size={21}/>, accent:"#16A34A", soft:"#EFFCF4", goodUp:true, go:"/dashboard/campaign/media?source=ai" },
    { key:"catalog_total", label:"Товаров в каталоге", note:"Добавлено и готово к публикации", icon:<Icon name="layers" size={21}/>, accent:"#2563EB", soft:"#F1F6FF", goodUp:true, go:"/dashboard/campaign/catalog" },
    { key:"ready", label:"Готовы к объявлениям", note:"Прошли проверку и можно публиковать", icon:<Icon name="send" size={21}/>, accent:"#F59E0B", soft:"#FFF8EB", goodUp:true, go:"/dashboard/campaign/catalog" },
    { key:"no_price", label:"Без цены", note:"Требуют указания цены", icon:<Icon name="coins" size={21}/>, accent:"#F43F5E", soft:"#FFF1F4", goodUp:false, go:"/dashboard/campaign/catalog" },
    { key:"no_photo", label:"Без фото", note:"Нужно добавить изображения", icon:<Icon name="image" size={21}/>, accent:"#06B6D4", soft:"#EEFCFF", goodUp:false, go:"/dashboard/campaign/catalog" },
    { key:"drafts", label:"Черновики", note:"Сохранены, но не опубликованы", icon:<Icon name="file" size={21}/>, accent:"#8B5CF6", soft:"#F7F3FF", goodUp:false, go:"/dashboard/campaign/catalog" },
    { key:"missing", label:"Проблемы файлов", note:"Ошибки и недоступные файлы", icon:<Icon name="alert" size={21}/>, accent:"#2563EB", soft:"#F1F6FF", goodUp:false, go:"/dashboard/campaign/media" },
  ];

  const metricDelta = (key:string, current:number, goodUp:boolean) => {
    if (history.length < 2) return { delta:0, pct:0, arrow:"•", sentiment:"neutral", text:"Накапливаем историю" };
    const prev = Number(history[history.length - 2]?.[key] || 0);
    const delta = current - prev;
    const pct = prev === 0 ? (delta === 0 ? 0 : 100) : Math.round(Math.abs(delta) / Math.abs(prev) * 100);
    const improved = delta === 0 ? null : (goodUp ? delta > 0 : delta < 0);
    return {
      delta, pct,
      arrow: delta > 0 ? "▲" : delta < 0 ? "▼" : "•",
      sentiment: improved === null ? "neutral" : improved ? "good" : "bad",
      text: delta === 0 ? "Без изменений" : `${pct}%  ${delta > 0 ? "+" : ""}${delta}`,
    };
  };

  const selectedCampaign = campaigns.find((campaign:any)=>Number(campaign?.id||0)===Number(selectedCampaignId||0)) || null;
  const campaignReady = Boolean(campaignReadiness?.status==="ok" && campaignReadiness?.ready_to_publish);
  const preparedCount = Number(campaignReadiness?.qa_pass ?? campaignReadiness?.generated ?? 0);
  const generatedCount = Number(campaignReadiness?.generated || 0);
  const headline = selectedCampaignId > 0
    ? (campaignReady
      ? `Создано ${generatedCount} · готовы ${generatedCount} из ${generatedCount}`
      : (generatedCount > 0 ? `Создано ${generatedCount} · полностью готово ${preparedCount} из ${generatedCount}` : "BORIS готовит проект"))
    : "Создайте проект объявлений";
  const startBoris = () => {
    setWorkspaceTab("projects");
    setShowLaunchWorkspace(true);
    window.setTimeout(()=>document.getElementById("campaign-create")?.scrollIntoView({behavior:"smooth",block:"start"}),30);
  };
  const openCampaignDetailTab = (tab:string) => {
    if (selectedCampaignId > 0) router.push("/dashboard/campaign/" + selectedCampaignId + "?tab=" + encodeURIComponent(tab));
    else startBoris();
  };
  const openMassEditor = () => {
    setWorkspaceTab("ads");
    window.setTimeout(()=>{
      const node=document.getElementById("mass-editor") as HTMLDetailsElement|null;
      if(node){node.open=true;node.scrollIntoView({behavior:"smooth",block:"start"});}
    },30);
  };
  const downloadHistoryCsv = async () => {
    const aid=selectedAccountId||account;
    if(!aid)return;
    try{
      const response=await apiFetch("/api/campaign/timeline/export?account_id="+encodeURIComponent(aid));
      if(!response.ok)throw new Error("Не удалось подготовить историю");
      const blob=await response.blob();
      const url=URL.createObjectURL(blob);
      const link=document.createElement("a");
      link.href=url;link.download="boris-history-"+aid+".csv";document.body.appendChild(link);link.click();link.remove();
      window.setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(error:any){setErr(String(error?.message||"Не удалось скачать историю"));}
  };
  const experimentName = (formula:string) => ({
    AIDA:"Интерес → выгода → действие",
    PAS:"Проблема → решение",
    FAB:"Характеристики → выгоды",
    BAB:"До → после",
    "4P":"Польза → доказательство → действие",
    QUEST:"Вопрос клиента → решение",
    OBJECTION:"Работа с возражением",
    EXPERT:"Экспертный подход",
    RESULT:"Фокус на результате",
    COMPARISON:"Сравнение вариантов",
    PROCESS:"Как проходит работа",
  } as Record<string,string>)[String(formula||"").toUpperCase()] || "Новый рекламный подход";
  const experimentCards = [
    ...(aiMarketer?.formula_winners || []).map((formula:string)=>({formula,state:"winner"})),
    ...(aiMarketer?.formula_running || []).map((formula:string)=>({formula,state:"running"})),
    ...(aiMarketer?.formula_inconclusive || []).map((formula:string)=>({formula,state:"inconclusive"})),
    ...(aiMarketer?.formula_losers || []).map((formula:string)=>({formula,state:"loser"})),
  ];
  const experimentState = (state:string) => ({
    winner:"Есть лидер",
    running:"Тестируется",
    inconclusive:"Пока мало данных",
    loser:"Проигрывает",
  } as Record<string,string>)[state] || "Тестируется";
  const creativeFamilyName = (family:string) => ({
    glass_light:"Светлое стекло",
    glass_dark:"Тёмное стекло",
    warm_accent:"Тёплый акцент",
    charcoal_center:"Графитовый центр",
  } as Record<string,string>)[String(family||"")] || String(family||"");
  const creativeFamilyPlan=aiMarketer?.creative_family_memory?.plan||null;
  const creativeFamilyWeights=creativeFamilyPlan?.weights_pct||{};
  const inventoryStageLabel = (stage:string) => ({
    queued:"В очереди",
    snapshot:"Проверяем профиль",
    snapshot_done:"Профиль проверен",
    reactivate:"Обновляем текущие объявления",
    existing_inventory_done:"Текущие объявления обработаны",
    create_new:"Создаём недостающие объявления",
    creation_done:"Недостающие объявления подготовлены",
    legacy_browser:"Обновляем старые объявления",
    verify:"Финальная проверка",
    completed:"Готово",
  } as Record<string,string>)[String(stage||"")] || "BORIS работает";
  const moreItems:CampaignMenuItem[]=[
    {label:"История запусков",note:"Что BORIS уже сделал по этому аккаунту",onClick:()=>setWorkspaceTab("history")},
    {label:"Массовые операции",note:"Изменить сразу несколько объявлений с предпросмотром",onClick:openMassEditor},
    {label:"Автоматизация",note:"Поддерживать заданное число активных объявлений",onClick:()=>setWorkspaceTab("automation")},
    {label:"Экспорт CSV",note:"Скачать историю действий",onClick:()=>void downloadHistoryCsv()},
    {label:"Дополнительные настройки",note:"Стратегия, источники и правила подготовки",onClick:()=>setWorkspaceTab("settings")},
    {label:"Как это работает",note:"Короткая справка без технических терминов",onClick:()=>setShowClientHelp(v=>!v)},
  ];

  return (
    <EcShell tab="overview" title="Объявления"
      subtitle="Создавайте, тестируйте и масштабируйте объявления с помощью BORIS.">
      {err !== "" && <Alert kind="danger">{err}</Alert>}
      {loading ? (
        <Card><Skeleton lines={5} /></Card>
      ) : !account ? (
        <EcEmpty title="Выберите аккаунт"
          hint="Центр объявлений работает в разрезе одного Avito-аккаунта." />
      ) : (
        <>
          <section data-testid="campaign-client-hero" style={{marginBottom:18,padding:"22px 24px",borderRadius:24,background:"linear-gradient(135deg,#EEF5FF 0%,#FFFFFF 58%,#F2F7FF 100%)",border:"1px solid #D7E4F7",boxShadow:"0 18px 44px rgba(40,79,139,.09)"}}>
            <div style={{display:"flex",justifyContent:"space-between",gap:18,alignItems:"center",flexWrap:"wrap"}}>
              <div style={{minWidth:0,flex:"1 1 520px"}}>
                <div style={{fontSize:11.5,fontWeight:950,letterSpacing:.7,color:"#315FA8",textTransform:"uppercase"}}>BORIS · ОБЪЯВЛЕНИЯ ПОД КЛЮЧ</div>
                <h2 style={{margin:"7px 0 0",fontSize:30,lineHeight:1.12,color:"#172033"}}>Объявления</h2>
                <div style={{marginTop:8,fontSize:14,color:"#5F6F86",lineHeight:1.5}}>Создавайте, тестируйте и масштабируйте объявления с помощью BORIS.</div>
                <div style={{display:"inline-flex",alignItems:"center",gap:7,marginTop:11,padding:"6px 9px",borderRadius:999,background:campaignReady?"#ECFDF3":"#F5F8FC",border:"1px solid "+(campaignReady?"#C7E8D2":"#E0E7F1"),fontSize:11.5,fontWeight:850,color:campaignReady?"#18794E":"#51647F"}}>
                  <span style={{width:7,height:7,borderRadius:99,background:campaignReady?"#22A06B":"#4C79C3"}}/>{headline}
                </div>
              </div>
              <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
                {campaigns.length ? <details data-testid="campaign-project-picker" style={{position:"relative",minWidth:260,maxWidth:360}}>
                  <summary aria-label="Текущий проект" style={{listStyle:"none",cursor:"pointer",display:"grid",gridTemplateColumns:"1fr auto",gap:10,alignItems:"center",border:"1px solid #D7E2F5",background:"#fff",borderRadius:16,padding:"9px 12px",boxShadow:"0 7px 22px rgba(39,73,125,.07)"}}>
                    <span style={{minWidth:0}}>
                      <small style={{display:"block",fontSize:10,fontWeight:900,color:"#4C6FAE",textTransform:"uppercase",letterSpacing:.45}}>Текущий проект</small>
                      <strong style={{display:"block",marginTop:2,fontSize:13,color:"#344054",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>
                        {String(campaigns.find((x:any)=>Number(x.id)===Number(selectedCampaignId))?.name||"Выберите проект").replace(/\s*#\d+\s*/g," ").trim()}
                      </strong>
                    </span>
                    <span style={{width:30,height:30,borderRadius:10,display:"grid",placeItems:"center",background:"#EEF4FF",color:"#315FA8",fontWeight:900}}>⌄</span>
                  </summary>
                  <div style={{position:"absolute",right:0,top:"calc(100% + 7px)",zIndex:50,width:"min(420px,92vw)",maxHeight:360,overflowY:"auto",background:"#fff",border:"1px solid #DDE6F4",borderRadius:16,padding:7,boxShadow:"0 18px 50px rgba(15,23,42,.16)"}}>
                    {campaigns.map((campaign:any)=>{
                      const active=Number(campaign.id)===Number(selectedCampaignId);
                      const totalAds=Number(campaign?.counts?.all||0);
                      const readyAds=Number(campaign?.counts?.ready||0);
                      return <button key={campaign.id} type="button" onClick={(e)=>{handleCampaignChange(Number(campaign.id));(e.currentTarget.closest("details") as HTMLDetailsElement | null)?.removeAttribute("open");}} style={{width:"100%",border:active?"1px solid #7AA5E7":"1px solid transparent",background:active?"#EEF5FF":"#fff",borderRadius:11,padding:"10px 11px",display:"grid",gridTemplateColumns:"1fr auto",gap:8,textAlign:"left",cursor:"pointer",marginBottom:4}}>
                        <span style={{minWidth:0}}><strong style={{display:"block",fontSize:13,color:"#172033",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{String(campaign?.name||"Проект объявлений").replace(/\s*#\d+\s*/g," ").trim()}</strong><small style={{display:"block",marginTop:3,color:"#667085"}}>{totalAds?totalAds+" объявлений · "+readyAds+" готовы":"Новый проект"}</small></span>
                        <span style={{fontSize:11,fontWeight:900,color:active?"#315FA8":"#98A2B3",alignSelf:"center"}}>{active?"Выбран":"Открыть"}</span>
                      </button>;
                    })}
                  </div>
                </details> : null}
                <Button type="button" onClick={startBoris}>Создать объявления</Button>
                <CampaignMenu label="Ещё" items={moreItems} testId="campaign-more-menu"/>
              </div>
            </div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(6,minmax(90px,1fr))",gap:8,marginTop:18,overflowX:"auto",paddingBottom:2}}>
              {[
                ["Бизнес",true],
                ["Стратегия",Boolean(selectedCampaignId)],
                ["Объявления",generatedCount>0],
                ["Фото",Boolean(campaignReadiness?.gates?.image_pass)],
                ["Проверка",Boolean(campaignReadiness?.gates?.feed_pass)],
                ["Avito",campaignReady],
              ].map(([label,done],index)=><div key={String(label)} style={{minWidth:90,padding:"9px 10px",borderRadius:999,background:done?"#ECFDF3":index===0||selectedCampaignId?"#EEF4FF":"#F7F9FC",border:`1px solid ${done?"#B7E7C9":index===0||selectedCampaignId?"#D5E3F8":"#E7ECF3"}`,fontSize:12,fontWeight:850,color:done?"#18794E":index===0||selectedCampaignId?"#315FA8":"#667085"}}>{String(label)} {done?"✓":""}</div>)}
            </div>
          </section>

          <nav aria-label="Разделы объявлений" data-testid="campaign-client-tabs" style={{display:"flex",gap:7,overflowX:"auto",padding:"4px 2px 16px",marginBottom:4}}>
            <button type="button" onClick={startBoris} style={{whiteSpace:"nowrap",border:workspaceTab==="projects"&&showLaunchWorkspace?"1px solid #7AA5E7":"1px solid #E4E7EC",background:workspaceTab==="projects"&&showLaunchWorkspace?"#EEF5FF":"#fff",color:workspaceTab==="projects"&&showLaunchWorkspace?"#315FA8":"#344054",borderRadius:999,padding:"9px 14px",fontSize:13,fontWeight:900,cursor:"pointer"}}>Создать объявления</button>
            <button type="button" onClick={()=>selectedCampaignId?router.push("/dashboard/campaign/"+selectedCampaignId):setWorkspaceTab("ads")} style={{whiteSpace:"nowrap",border:workspaceTab==="ads"?"1px solid #7AA5E7":"1px solid #E4E7EC",background:workspaceTab==="ads"?"#EEF5FF":"#fff",color:workspaceTab==="ads"?"#315FA8":"#344054",borderRadius:999,padding:"9px 14px",fontSize:13,fontWeight:900,cursor:"pointer"}}>{generatedCount>0?`Мои объявления (${generatedCount})`:"Мои объявления"}</button>
            <button type="button" onClick={()=>router.push("/dashboard/campaign/ab-tests")} style={{whiteSpace:"nowrap",border:"1px solid #E4E7EC",background:"#fff",color:"#344054",borderRadius:999,padding:"9px 14px",fontSize:13,fontWeight:900,cursor:"pointer"}}>A/B тесты</button>
            <button type="button" onClick={()=>openCampaignDetailTab("photos")} style={{whiteSpace:"nowrap",border:"1px solid #E4E7EC",background:"#fff",color:"#344054",borderRadius:999,padding:"9px 14px",fontSize:13,fontWeight:900,cursor:"pointer"}}>Материалы</button>
            <button type="button" onClick={()=>setWorkspaceTab("tasks")} style={{whiteSpace:"nowrap",border:workspaceTab==="tasks"?"1px solid #7AA5E7":"1px solid #E4E7EC",background:workspaceTab==="tasks"?"#EEF5FF":"#fff",color:workspaceTab==="tasks"?"#315FA8":"#344054",borderRadius:999,padding:"9px 14px",fontSize:13,fontWeight:900,cursor:"pointer"}}>Текущие задачи</button>
          </nav>
          {showClientHelp ? <Card data-testid="campaign-client-help" style={{marginBottom:space.lg,border:"1px solid #F2D39B",background:"#FFFBF3"}}>
            <div style={{...font.h3,color:color.heading}}>Как работает «Объявления под ключ»</div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(210px,1fr))",gap:10,marginTop:12}}>
              {[
                ["1. Расскажите задачу","Напишите обычными словами, что продвигать, сколько объявлений нужно и какие товары/услуги важнее."],
                ["2. Дайте источники","Можно вставить сайт, нужные разделы, использовать прайс, данные «О компании» и текущие объявления Avito."],
                ["3. BORIS подготовит","Система соберёт отдельные карточки, тексты, цены, категории, характеристики, фото и баннеры."],
                ["4. Проверьте результат","Откройте карточки, при необходимости измените текст, цену, фото или параметры."],
                ["5. Размещение","BORIS проверит готовность Avito и только после подтверждения передаст объявления наружу."],
                ["6. Можно закрыть страницу","Долгие задачи и прогресс хранятся на сервере. После обновления работа продолжится с того же места."],
                ["7. Правки","Откройте карточку объявления: текст, цена, фотографии, баннер и параметры Avito редактируются отдельно. Для нескольких карточек используйте массовую настройку с предпросмотром."],
                ["8. A/B-тесты","После публикации BORIS автоматически сравнивает гипотезы по фактическим просмотрам и обращениям. Если данных мало, система не объявляет лидера раньше времени."],
                ["9. Масштабирование","Сильный рекламный подход можно масштабировать: BORIS создаст новые уникальные варианты, сохранив подтверждённые факты, цену, товар и категорию."],
                ["10. Ежедневное поддержание","В «Автоматизации» можно задать целевое число активных объявлений. BORIS проверяет профиль, безопасно возвращает подходящие завершённые и создаёт недостающие."],
              ].map(([title,text])=><div key={title} style={{padding:"12px 13px",borderRadius:13,background:"#fff",border:"1px solid #F1E3C6"}}><strong style={{color:color.heading}}>{title}</strong><div style={{...font.small,marginTop:5,color:color.muted,lineHeight:1.5}}>{text}</div></div>)}
            </div>
          </Card> : null}

          {workspaceTab==="projects" ? <div data-testid="campaign-simplified-overview" style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(260px,1fr))",gap:14,marginBottom:space.lg}}>
            <Card style={{border:"1px solid #DCE7F7",borderRadius:20,boxShadow:"0 10px 30px rgba(37,67,112,.05)"}}>
              <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"center"}}>
                <div>
                  <div style={{...font.h3,color:color.heading}}>Быстрый запуск</div>
                  <div style={{...font.small,marginTop:4,color:color.muted}}>Только основные действия для запуска и проверки.</div>
                </div>
                <span style={{width:34,height:34,borderRadius:12,display:"grid",placeItems:"center",background:"#EEF4FF",color:"#315FA8",fontWeight:950}}>↗</span>
              </div>
              <div style={{display:"grid",gap:7,marginTop:14}}>
                {[
                  ["Подготовить объявления","Тексты, цены и структура",startBoris],
                  ["Сгенерировать фото","Фото выбранного проекта",()=>openCampaignDetailTab("photos")],
                  ["Запустить A/B","Сравнить реальные гипотезы",()=>router.push("/dashboard/campaign/ab-tests")],
                  ["Проверить перед публикацией","Готовность карточек и Avito",()=>openCampaignDetailTab("check")],
                ].map(([title,note,action]:any)=><button key={title} type="button" onClick={action} style={{display:"grid",gridTemplateColumns:"minmax(0,1fr) auto",gap:8,alignItems:"center",width:"100%",border:"1px solid #E7ECF4",borderRadius:13,background:"#fff",padding:"10px 11px",textAlign:"left",cursor:"pointer"}}>
                  <span><strong style={{display:"block",fontSize:12.5,color:"#172033"}}>{title}</strong><small style={{display:"block",marginTop:2,fontSize:10.5,color:"#667085"}}>{note}</small></span>
                  <b style={{fontSize:16,color:"#4C6FAE"}}>›</b>
                </button>)}
              </div>
            </Card>

            <Card style={{border:"1px solid #E4E9F1",borderRadius:20,boxShadow:"0 10px 30px rgba(37,67,112,.04)"}}>
              <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"center"}}>
                <div>
                  <div style={{...font.h3,color:color.heading}}>Что BORIS сделал</div>
                  <div style={{...font.small,marginTop:4,color:color.muted}}>Только значимые реальные действия.</div>
                </div>
                <button type="button" onClick={()=>setWorkspaceTab("history")} style={{border:0,background:"transparent",color:"#315FA8",fontSize:11.5,fontWeight:900,cursor:"pointer"}}>Вся история →</button>
              </div>
              <div style={{display:"grid",gap:8,marginTop:14}}>
                {campaignTimeline.length ? campaignTimeline.slice(0,4).map((item:any,index:number)=><div key={String(item?.id||item?.when||index)} style={{display:"grid",gridTemplateColumns:"12px minmax(0,1fr)",gap:9,alignItems:"start",padding:"9px 8px",borderRadius:11,background:"#FAFBFD"}}>
                  <span style={{width:8,height:8,borderRadius:99,marginTop:5,background:String(item?.action||"").toLowerCase().includes("ошиб")?"#D92D20":"#22A06B"}}/>
                  <span style={{minWidth:0}}><strong style={{display:"block",fontSize:12,color:"#22304A",lineHeight:1.35}}>{String(item?.action||"Действие BORIS")}</strong><small style={{display:"block",marginTop:3,fontSize:10,color:"#98A2B3"}}>{item?.when?new Date(item.when).toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}):""}{item?.actor?" · "+String(item.actor):""}</small></span>
                </div>) : <div style={{padding:"16px 12px",borderRadius:13,background:"#F8FAFC",fontSize:11.5,color:"#667085"}}>История появится после первых действий BORIS.</div>}
              </div>
            </Card>

            <Card style={{border:"1px solid #E4E9F1",borderRadius:20,boxShadow:"0 10px 30px rgba(37,67,112,.04)"}}>
              <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"center"}}>
                <div>
                  <div style={{...font.h3,color:color.heading}}>Активные A/B</div>
                  <div style={{...font.small,marginTop:4,color:color.muted}}>Гипотезы без преждевременных победителей.</div>
                </div>
                <button type="button" onClick={()=>router.push("/dashboard/campaign/ab-tests")} style={{border:0,background:"transparent",color:"#315FA8",fontSize:11.5,fontWeight:900,cursor:"pointer"}}>Все A/B →</button>
              </div>
              <div style={{display:"grid",gap:9,marginTop:14}}>
                {experimentCards.length ? experimentCards.slice(0,2).map((card:any,index:number)=><div key={card.state+card.formula+index} style={{padding:"11px 12px",borderRadius:13,border:"1px solid #E6EBF2",background:"#fff"}}>
                  <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"start"}}>
                    <strong style={{fontSize:12.5,color:"#172033",lineHeight:1.35}}>{experimentName(card.formula)}</strong>
                    <span style={{whiteSpace:"nowrap",fontSize:9.5,fontWeight:900,padding:"4px 7px",borderRadius:999,background:card.state==="winner"?"#ECFDF3":card.state==="running"?"#EEF4FF":"#F5F7FA",color:card.state==="winner"?"#18794E":card.state==="running"?"#315FA8":"#667085"}}>{experimentState(card.state)}</span>
                  </div>
                  <button type="button" onClick={()=>router.push("/dashboard/campaign/ab-tests")} style={{marginTop:8,border:0,background:"transparent",padding:0,color:"#315FA8",fontSize:11,fontWeight:900,cursor:"pointer"}}>Открыть →</button>
                </div>) : <div style={{padding:"16px 12px",borderRadius:13,background:"#F8FAFC",fontSize:11.5,color:"#667085"}}>Пока мало данных. После публикации BORIS начнёт сравнение автоматически.</div>}
              </div>
            </Card>
          </div> : null}

          {workspaceTab==="projects" && showLaunchWorkspace ? <div style={{display:"flex",justifyContent:"flex-end",margin:"-4px 0 12px"}}>
            <button type="button" onClick={()=>setShowLaunchWorkspace(false)} style={{border:"1px solid #D7E2F5",background:"#fff",color:"#315FA8",borderRadius:999,padding:"8px 12px",fontSize:11.5,fontWeight:900,cursor:"pointer"}}>Свернуть подготовку</button>
          </div> : null}

          {workspaceTab==="settings" && Array.isArray(projectContext?.facts) && projectContext.facts.length ? <Card data-testid="campaign-project-context" style={{marginBottom:space.lg,border:"1px solid #E8E0D1"}}>
            <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",flexWrap:"wrap"}}>
              <div>
                <div style={{...font.h3,color:color.heading}}>BORIS знает о проекте</div>
                <div style={{...font.small,marginTop:4,color:color.muted}}>Только подтверждённые и актуальные факты из прошлых обсуждений. Цены всё равно берутся из текущего прайса, сайта или подтверждённых данных компании.</div>
              </div>
              <Button type="button" kind="ghost" onClick={()=>setWorkspaceTab("settings")}>Изменить</Button>
            </div>
            <div style={{display:"grid",gap:7,marginTop:12}}>
              {projectContext.facts.slice(0,8).map((fact:any,index:number)=><div key={String(fact?.text||index)} style={{display:"flex",gap:9,alignItems:"flex-start",padding:"9px 10px",borderRadius:11,background:"#FAFBFC",border:"1px solid #ECEEF2"}}>
                <span style={{width:7,height:7,borderRadius:99,background:"#F59E0B",marginTop:6,flex:"0 0 auto"}}/>
                <div style={{minWidth:0,flex:1}}><div style={{fontSize:13,color:color.heading,lineHeight:1.45}}>{String(fact?.text||"")}</div><div style={{fontSize:11,color:color.muted,marginTop:2}}>Из прошлых обсуждений</div></div>
              </div>)}
            </div>
          </Card> : null}

          {workspaceTab==="projects" && showLaunchWorkspace && campaignTimeline.length ? <Card data-testid="campaign-owner-timeline-preview" style={{marginBottom:space.lg,border:"1px solid #E4E7EC"}}>
            <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",flexWrap:"wrap"}}>
              <div>
                <div style={{...font.h3,color:color.heading}}>Что BORIS сделал</div>
                <div style={{...font.small,marginTop:4,color:color.muted}}>Только реальные действия по объявлениям и Avito.</div>
              </div>
              <Button type="button" kind="ghost" onClick={()=>setWorkspaceTab("history")}>Вся история</Button>
            </div>
            <div style={{display:"grid",gap:8,marginTop:12}}>
              {campaignTimeline.slice(0,3).map((item:any,index:number)=><div key={String(item?.id||item?.when||index)} style={{display:"grid",gridTemplateColumns:"18px minmax(0,1fr)",gap:10,alignItems:"start",padding:"9px 10px",borderRadius:11,background:"#FAFBFC",border:"1px solid #ECEEF2"}}>
                <span style={{width:9,height:9,borderRadius:99,background:String(item?.action||"").toLowerCase().includes("ошиб")?"#D92D20":String(item?.action||"").toLowerCase().includes("провер")?"#F59E0B":"#22A06B",marginTop:5}}/>
                <div style={{minWidth:0}}>
                  <div style={{fontSize:13,fontWeight:800,color:"#172033",lineHeight:1.4}}>{String(item?.action||"Действие BORIS")}</div>
                  <div style={{fontSize:10.5,color:"#98A2B3",marginTop:4}}>{item?.when ? new Date(item.when).toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}) : ""} · {String(item?.actor||"BORIS")}{Number(item?.duration_ms||0)>0 ? " · "+(Number(item.duration_ms)/1000).toLocaleString("ru-RU",{maximumFractionDigits:1})+" сек" : ""}</div>
                </div>
              </div>)}
            </div>
          </Card> : null}

          {workspaceTab==="history" ? <Card data-testid="campaign-owner-timeline" style={{marginBottom:space.lg,border:"1px solid #E4E7EC"}}>
            <CampaignTimeline key={selectedAccountId} accountId={selectedAccountId||""} available={historyAvailable} legacy={campaignTimeline}/>
            <div data-testid="campaign-history-task-timing" style={{marginTop:16,paddingTop:14,borderTop:"1px solid #E7EAF0"}}>
              <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"center",flexWrap:"wrap"}}>
                <div>
                  <div style={{fontSize:12.5,fontWeight:900,color:"#344054"}}>Время выполнения задач</div>
                  <div style={{fontSize:11,color:"#667085",marginTop:3}}>Реальные серверные задачи: когда начались, чем закончились и сколько заняли.</div>
                </div>
                <Button type="button" kind="ghost" onClick={()=>setWorkspaceTab("tasks")}>Вся история задач</Button>
              </div>
              {campaignTasks.length ? <div style={{display:"grid",gap:7,marginTop:10}}>
                {campaignTasks.slice(0,6).map((task:any)=>{
                  const st=String(task?.status||"");
                  const attention=st.startsWith("blocked") || ["waiting_external","pending_external_verification","needs_action"].includes(st);
                  const active=["queued","running","validating","awaiting_confirmation"].includes(st);
                  const seconds=task.finished_at ? Number(task.duration_total_seconds||0) : clientTaskSeconds(task,clockNow);
                  return <div key={"history-task-"+String(task.id)} style={{display:"grid",gridTemplateColumns:"minmax(0,1fr) auto",gap:10,alignItems:"center",padding:"9px 10px",borderRadius:11,background:"#FAFBFC",border:"1px solid #ECEEF2"}}>
                    <div style={{minWidth:0}}>
                      <div style={{fontSize:12,fontWeight:850,color:"#344054"}}>{String(task.title||"Задача BORIS")}</div>
                      <div style={{fontSize:10.5,color:"#667085",marginTop:3}}>{String(task.status_text||"BORIS работает")}{task.stage_text?" · "+String(task.stage_text):""}</div>
                    </div>
                    <div style={{textAlign:"right",whiteSpace:"nowrap"}}>
                      <div style={{fontSize:11.5,fontWeight:900,color:attention?"#B54708":st==="failed"?"#B42318":active?"#9A6700":"#067647"}}>{formatDuration(seconds)}</div>
                      <div style={{fontSize:9.5,color:"#98A2B3",marginTop:2}}>{task.finished_at?"итого":attention?"до остановки":active?"идёт сейчас":"в очереди"}</div>
                    </div>
                  </div>;
                })}
              </div> : <div style={{marginTop:10,fontSize:11,color:"#98A2B3"}}>Задач по этому аккаунту пока нет.</div>}
            </div>
          </Card> : null}

          {workspaceTab==="tasks" ? <Card data-testid="campaign-task-history" style={{marginBottom:space.lg,border:"1px solid #E4E7EC"}}>
            <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",flexWrap:"wrap"}}>
              <div>
                <div style={{...font.h3,color:color.heading}}>История задач</div>
                <div style={{...font.small,marginTop:4,color:color.muted}}>Старт, ожидание, активная работа, завершение и фактическая длительность каждой фоновой задачи BORIS.</div>
              </div>
              <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                {([["all","Все"],["active","В работе"],["attention","Нужно действие"],["done","Завершённые"],["failed","С ошибкой"]] as const).map(([key,label])=><button key={key} type="button" onClick={()=>setTaskFilter(key)} style={{border:taskFilter===key?"1px solid #F59E0B":"1px solid #E4E7EC",background:taskFilter===key?"#FFF7E6":"#fff",color:taskFilter===key?"#9A5A00":"#475467",borderRadius:999,padding:"6px 9px",fontSize:11,fontWeight:850,cursor:"pointer"}}>{label}</button>)}
              </div>
            </div>
            <div style={{display:"grid",gap:9,marginTop:14}}>
              {campaignTasks.filter((task:any)=>{
                const st=String(task?.status||"");
                if(taskFilter==="active")return ["queued","running","validating","awaiting_confirmation"].includes(st);
                if(taskFilter==="attention")return st.startsWith("blocked") || ["waiting_external","pending_external_verification","needs_action"].includes(st);
                if(taskFilter==="done")return ["completed","partial","cancelled"].includes(st);
                if(taskFilter==="failed")return st==="failed";
                return true;
              }).map((task:any)=><details key={String(task.id)} style={{border:"1px solid #E7EAF0",borderRadius:14,background:"#fff",overflow:"hidden"}}>
                <summary style={{listStyle:"none",cursor:"pointer",padding:"12px 13px",display:"grid",gridTemplateColumns:"minmax(0,1fr) auto",gap:12,alignItems:"center"}}>
                  <span style={{minWidth:0}}>
                    <span style={{display:"flex",gap:7,alignItems:"center",flexWrap:"wrap"}}>
                      <strong style={{fontSize:13,color:"#172033"}}>{String(task.title||"Задача BORIS")}</strong>
                      <em style={{fontStyle:"normal",fontSize:10,fontWeight:900,padding:"4px 7px",borderRadius:999,background:task.status==="failed"?"#FFF1F0":["queued","running","validating","awaiting_confirmation"].includes(task.status)?"#FFF7E6":"#ECFDF3",color:task.status==="failed"?"#B42318":["queued","running","validating","awaiting_confirmation"].includes(task.status)?"#9A6700":"#067647"}}>{String(task.status_text||"BORIS работает")}</em>
                    </span>
                    <small style={{display:"block",marginTop:5,color:"#667085"}}>{task.stage_text?String(task.stage_text)+" · ":""}{task.finished_at?"Завершено за "+formatDuration(task.duration_total_seconds):["running","validating"].includes(task.status)?"BORIS работает · "+formatDuration(clientTaskSeconds(task,clockNow)):"Ожидание · "+formatDuration(clientTaskSeconds(task,clockNow))}</small>
                  </span>
                  <span style={{fontSize:12,fontWeight:900,color:"#344054"}}>{Number(task.progress_percent||0)}%</span>
                </summary>
                <div style={{padding:"0 13px 13px"}}>
                  <div style={{height:8,borderRadius:999,background:"#EEF1F5",overflow:"hidden"}}><div style={{height:"100%",width:Math.max(2,Math.min(100,Number(task.progress_percent||0)))+"%",borderRadius:999,background:task.status==="failed"?"#D92D20":"#F59E0B"}}/></div>
                  <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:8,marginTop:10}}>
                    <div style={{padding:"8px 9px",borderRadius:9,background:"#F8FAFC",fontSize:11,color:"#667085"}}>Создана<br/><b style={{color:"#344054"}}>{task.created_at?new Date(task.created_at).toLocaleString("ru-RU"):"—"}</b></div>
                    <div style={{padding:"8px 9px",borderRadius:9,background:"#F8FAFC",fontSize:11,color:"#667085"}}>Старт<br/><b style={{color:"#344054"}}>{task.started_at?new Date(task.started_at).toLocaleString("ru-RU"):"Ещё не началась"}</b></div>
                    <div style={{padding:"8px 9px",borderRadius:9,background:"#F8FAFC",fontSize:11,color:"#667085"}}>Завершение<br/><b style={{color:"#344054"}}>{task.finished_at?new Date(task.finished_at).toLocaleString("ru-RU"):"В процессе"}</b></div>
                    <div style={{padding:"8px 9px",borderRadius:9,background:"#F8FAFC",fontSize:11,color:"#667085"}}>Итого<br/><b style={{color:"#344054"}}>{formatDuration(task.finished_at?task.duration_total_seconds:clientTaskSeconds(task,clockNow))}</b></div>
                    <div style={{padding:"8px 9px",borderRadius:9,background:"#F8FAFC",fontSize:11,color:"#667085"}}>Работа BORIS<br/><b style={{color:"#344054"}}>{Number(task.timing_contract||0)<1||task.duration_active_seconds==null?"Не сохранялось":formatDuration(task.duration_active_seconds)}</b></div>
                    <div style={{padding:"8px 9px",borderRadius:9,background:"#F8FAFC",fontSize:11,color:"#667085"}}>Ожидание<br/><b style={{color:"#344054"}}>{Number(task.timing_contract||0)<1||task.duration_waiting_seconds==null?"Не сохранялось":formatDuration(task.duration_waiting_seconds)}</b></div>
                    <div style={{padding:"8px 9px",borderRadius:9,background:"#F8FAFC",fontSize:11,color:"#667085"}}>Повторы после ошибок<br/><b style={{color:"#344054"}}>{Number(task.retries||0)}</b></div>
                  </div>
                  {task.problem?<div style={{marginTop:9,padding:"9px 10px",borderRadius:9,background:"#FFF1F0",color:"#B42318",fontSize:11.5}}>{String(task.problem)}</div>:null}
                </div>
              </details>)}
              {taskCursor?<Button kind="ghost" disabled={taskPageBusy} onClick={()=>void moreTasks()}>Показать ещё задачи</Button>:null}
              {!campaignTasks.length?<div style={{padding:"16px",borderRadius:12,background:"#F8FAFC",color:"#667085",fontSize:12}}>История фоновых задач по этому аккаунту пока пуста.</div>:null}
            </div>
          </Card> : null}

          {workspaceTab==="projects" && showLaunchWorkspace ? <Card id="campaign-experiments" style={{marginBottom:space.lg}}>
            <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",flexWrap:"wrap"}}>
              <div>
                <div style={{...font.h3,color:color.heading}}>Эксперименты и рекламные гипотезы</div>
                <div style={{...font.small,marginTop:4}}>BORIS показывает идеи человеческим языком и не объявляет победителя, пока данных недостаточно.</div>
              </div>
              <Button type="button" kind="ghost" onClick={()=>router.push("/dashboard/campaign/ab-tests")}>Открыть результаты A/B</Button>
            </div>
            {experimentCards.length ? <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(240px,1fr))",gap:12,marginTop:14}}>
              {experimentCards.slice(0,3).map((card:any,index:number)=><article key={card.state+card.formula+index} style={{padding:16,border:"1px solid #E7EAF0",borderRadius:16,background:"#fff",boxShadow:"0 10px 24px rgba(15,23,42,.04)"}}>
                <div style={{display:"flex",justifyContent:"space-between",gap:10}}>
                  <strong style={{color:color.heading}}>{experimentName(card.formula)}</strong>
                  <span style={{fontSize:11,fontWeight:900,padding:"5px 8px",borderRadius:999,background:card.state==="winner"?"#ECFDF3":card.state==="running"?"#FFF7E6":"#F5F7FA",color:card.state==="winner"?"#18794E":card.state==="running"?"#9A6700":"#667085"}}>{experimentState(card.state)}</span>
                </div>
                <div style={{...font.small,marginTop:9,color:color.muted}}>{card.state==="winner"?"BORIS будет чаще использовать этот подход, сохраняя часть трафика для новых тестов.":card.state==="inconclusive"?"Пока рано делать вывод — BORIS продолжает собирать статистику.":"Подход сравнивается по просмотрам, обращениям и конверсии."}</div>
              </article>)}
            </div> : <div style={{marginTop:14,padding:"14px 16px",borderRadius:14,background:"#F8FAFC",...font.small,color:color.muted}}>Пока мало данных для сравнения. После публикации BORIS соберёт результаты и покажет гипотезы здесь.</div>}
          </Card> : null}
          {workspaceTab==="ads" ? <Card style={{marginBottom:space.lg}}>
            <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",flexWrap:"wrap"}}>
              <div>
                <div style={{...font.h3,color:color.heading}}>Мои объявления</div>
                <div style={{...font.small,marginTop:4}}>Откройте проект, чтобы посмотреть карточки, тексты, цены, фотографии и быстро внести изменения.</div>
              </div>
              <Button type="button" disabled={!selectedCampaignId} onClick={()=>selectedCampaignId&&router.push("/dashboard/campaign/"+selectedCampaignId)}>Открыть карточки</Button>
            </div>
          </Card> : null}
          <details id="mass-editor" style={{display:workspaceTab==="ads"?"block":"none",marginBottom:space.lg,border:`1px solid ${color.line}`,borderRadius:18,background:color.surface,overflow:"hidden",boxShadow:"0 10px 30px rgba(15,23,42,.05)"}}>
            <summary style={{listStyle:"none",cursor:"pointer",padding:"18px 20px",display:"flex",alignItems:"center",gap:14,flexWrap:"wrap"}}>
              <span style={{width:42,height:42,borderRadius:13,display:"grid",placeItems:"center",background:"#EEF4FF",color:"#2563EB",fontWeight:900}}>✎</span>
              <span style={{flex:"1 1 420px",minWidth:0}}>
                <strong style={{...font.h3,color:color.heading,display:"block"}}>Массовая правка объявлений</strong>
                <small style={{...font.small,display:"block",marginTop:3}}>Цена, описание, фотографии и характеристики всех активных объявлений выбранного Avito-аккаунта</small>
              </span>
              <span style={{border:`1px solid ${color.line}`,borderRadius:11,padding:"8px 12px",fontWeight:800,fontSize:13,color:color.heading,background:"#fff"}}>Открыть</span>
            </summary>
            <div style={{padding:"0 16px 16px"}}><MassEditor account={account} /></div>
          </details>
          <Card id="campaign-themes" className="boris-campaign-overview-summary" style={{ display:workspaceTab==="settings"?"block":"none", marginBottom: space.lg }}>
            <div style={{display:"flex",justifyContent:"space-between",gap:16,alignItems:"flex-start",flexWrap:"wrap"}}>
              <div>
                <div style={{...font.h3,color:color.heading}}>Темы и направления клиента</div>
                <div style={{...font.small,marginTop:4}}>BORIS показывает только извлечённые из текущего аккаунта темы: профиль, активные объявления и данные Avito. Неподтверждённые товары и услуги не добавляются.</div>
              </div>
              <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
                <span style={{...font.small,padding:"7px 10px",borderRadius:8,background:"#EEF4FF",color:"#2457A6"}}>{promptKind === "products" ? "ТОВАРЫ" : "УСЛУГИ"}</span>
                <span style={{...font.small,padding:"7px 10px",borderRadius:8,background:"#F5F7FA"}}>источников: {Object.values(campaignContext?.sources || {}).filter(Boolean).length}</span>
                <div style={{...font.small,display:"inline-grid",gap:2,padding:"7px 10px",borderRadius:10,background:"#FFF8EB",color:"#8A5A00",border:"1px solid #E8D6A5",maxWidth:300}}>
                  <span style={{fontSize:10,fontWeight:900,textTransform:"uppercase",letterSpacing:.35}}>Текущий проект</span>
                  <strong style={{fontSize:12,color:"#344054",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>
                    {selectedCampaign ? String(selectedCampaign?.name||"Проект объявлений").replace(/\s*#\d+\s*/g," ").trim() : "Не выбран"}
                  </strong>
                </div>
              </div>
            </div>
            {themes.length ? <div style={{display:"grid",gap:8,marginTop:14}}>
              {themes.map((theme:any)=><div key={theme.title} style={{display:"grid",gridTemplateColumns:"minmax(180px,1.3fr) minmax(120px,1fr) minmax(100px,.7fr) minmax(150px,1fr) auto",gap:10,alignItems:"center",padding:"10px 12px",border:`1px solid ${color.line}`,borderRadius:10,background:"#fff"}}>
                <div><strong style={{color:color.heading}}>{theme.title}</strong><div style={{...font.small,marginTop:2}}>Источник: {theme.source}</div></div>
                <div style={{...font.small}}><span style={{color:color.muted}}>Категория:</span> {theme.category}</div>
                <div style={{...font.small}}><span style={{color:color.muted}}>Вариантов:</span> {theme.variants || "—"}</div>
                <div style={{...font.small}}><span style={{color:color.muted}}>Цена:</span> {theme.price?.price != null ? `${theme.price.price}${theme.price.price_to != null ? `–${theme.price.price_to}` : ""} ${theme.price.currency || ""}` : "не подтверждена"}<div style={{marginTop:2,fontSize:11,color:theme.price?.conflict ? "#B42318" : color.muted}}>{theme.price?.conflict ? "Конфликт источников" : (theme.price?.source ? `${theme.price.source} · ${theme.price.verified ? "проверена" : "не проверена"}` : "источник не найден")}</div></div>
                <span style={{fontSize:11,fontWeight:900,letterSpacing:.3,padding:"5px 8px",borderRadius:7,background:theme.readiness === "READY" ? "#ECFDF3" : "#FFF7E6",color:theme.readiness === "READY" ? "#18794E" : "#9A6700"}}>{theme.readiness==="READY"?"Готово":theme.readiness==="PRICE_CONFLICT"?"Проверить цену":"Нужно уточнить"}</span>
              </div>)}
            </div> : <div style={{marginTop:14,padding:"12px 14px",border:"1px dashed #CBD5E1",borderRadius:10,...font.small,color:color.muted}}>Темы ещё не извлечены. Добавьте направление в профиле, прайс, сайт или текущие объявления Avito — после обновления BORIS покажет конкретные темы здесь.</div>}
            <div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:12,...font.small}}>
              <span style={{padding:"6px 9px",borderRadius:7,background:"#F5F7FA"}}>Прайс: {strategy.use_price_list ? "разрешён" : "выключен"}</span>
              <span style={{padding:"6px 9px",borderRadius:7,background:"#F5F7FA"}}>Сайт: {strategy.use_site_services ? "разрешён" : "выключен"}</span>
              <span style={{padding:"6px 9px",borderRadius:7,background:"#F5F7FA"}}>Цена: {strategy.require_verified_price ? "только подтверждённая" : "без обязательной проверки"}</span>
            </div>
          </Card>
          <Card id="campaign-readiness" className="boris-campaign-overview-summary" style={{ display:workspaceTab==="projects"&&showLaunchWorkspace?"block":"none", marginBottom: space.lg }}>
            <div style={{...font.h3,color:color.heading}}>Готовность объявлений</div>
            <div style={{...font.small,marginTop:4}}>BORIS показывает фактическое состояние проверки простым языком и не помечает кампанию готовой, пока обязательные условия не выполнены.</div>
            {campaignReadiness?.status === "ok" ? (
              <>
                <div style={{...font.bodyStrong,color:campaignReadiness.ready_to_publish?"#067647":"#B54708",marginTop:12}}>
                  {campaignReadiness.ready_to_publish
                    ? `${Number(campaignReadiness.generated||0)} из ${Number(campaignReadiness.generated||0)} объявлений готовы`
                    : "Нужны действия перед размещением"}
                </div>
                <div style={{...font.small,marginTop:5}}>
                  {campaignReadiness.ready_to_publish
                    ? "Все обязательные проверки пройдены. Можно открыть проект и проверить финальный пакет перед размещением."
                    : `Подготовлено карточек: ${Number(campaignReadiness.generated||0)}. BORIS не даст ложный статус «готово», пока проблемы ниже не устранены.`}
                </div>
                {!campaignReadiness.ready_to_publish && readinessActions(campaignReadiness.block_reasons||[]).length ? (
                  <div style={{display:"grid",gap:7,marginTop:10}}>
                    {readinessActions(campaignReadiness.block_reasons||[]).map((action)=><button key={action.label} type="button" onClick={()=>selectedCampaignId>0&&router.push("/dashboard/campaign/"+selectedCampaignId+action.target)} style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",textAlign:"left",border:"1px solid #FED7AA",background:"#FFF8EB",borderRadius:9,padding:"9px 11px",color:"#7A2E0E",cursor:"pointer"}}><span>{action.label}</span><b>Открыть →</b></button>)}
                  </div>
                ) : null}
              </>
            ) : <div style={{...font.small,marginTop:10}}>{feedReconcileCopy(feedReconcile) || "BORIS определяет текущую кампанию и её готовность."}</div>}
            {aiMarketer?.status === "ok" && <div style={{...font.small,marginTop:10}}><b>Следующий шаг BORIS:</b> {({
              FORMULA:"Сравнить рекламные подходы",
              TITLE:"Проверить новый заголовок",
              OPENING:"Проверить новый первый абзац",
              FIRST_IMAGE:"Проверить главное фото",
              BANNER:"Проверить новый баннер",
              CTA:"Проверить новый призыв к действию",
              PRICE_PRESENTATION:"Проверить подачу цены",
            } as Record<string,string>)[String(aiMarketer?.recommendation?.hypothesis_type||"").toUpperCase()] || "Продолжить сбор данных"}</div>}
            {aiMarketer?.status==="ok" && Object.keys(creativeFamilyWeights).length>0 ? (
              <div data-testid="campaign-creative-family-memory" style={{marginTop:10,padding:"10px 11px",border:"1px solid #D8E3F2",borderRadius:10,background:"#FBFDFF"}}>
                <div style={{...font.small,color:color.heading}}><b>Креативы V7:</b> план по накопленной статистике</div>
                <div style={{display:"flex",gap:6,flexWrap:"wrap",marginTop:7}}>
                  {Object.entries(creativeFamilyWeights).map(([family,weight])=><span key={family} style={{fontSize:11.5,fontWeight:800,padding:"5px 8px",borderRadius:999,background:"#EEF4FF",color:"#344054"}}>{creativeFamilyName(family)} · {Number(weight||0)}%</span>)}
                </div>
                <div style={{fontSize:11.5,color:"#667085",marginTop:7,lineHeight:1.45}}>
                  {creativeFamilyPlan?.reason||"BORIS собирает evidence по creative family."} Пока это рекомендация AI Marketer: генератор не меняет доли автоматически без отдельной production-приёмки.
                </div>
              </div>
            ) : null}
            {feedReconcileCopy(feedReconcile) && <div style={{...font.small,marginTop:8,padding:"8px 10px",borderRadius:8,background:"#F8FAFC"}}><b>Avito:</b> {feedReconcileCopy(feedReconcile)}</div>}
          </Card>
          <Card id="campaign-strategy" data-feed-reconcile-state={feedReconcile?.state || ""} className="boris-campaign-overview-summary" style={{ display:workspaceTab==="settings"?"block":"none", marginBottom: space.lg }}>
            <div style={{display:"flex",justifyContent:"space-between",gap:16,alignItems:"flex-start",flexWrap:"wrap"}}>
              <div>
                <div style={{...font.h3,color:color.heading}}>Стратегия создания объявлений</div>
                <div style={{...font.small,marginTop:4}}>Настройки сохраняются отдельно для выбранного аккаунта. BORIS использует их для подготовки дефицита, но не публикует объявления автоматически.</div>
              </div>
              <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
                <label style={{...font.small}}>Цель: активных объявлений <input aria-label="Target active ads" type="number" min={0} max={100000} value={Number(strategy.target_active_ads || 0)} onChange={(e)=>updateStrategy("target_active_ads",Math.max(0,Number(e.target.value || 0)))} style={{width:100,marginLeft:6,border:`1px solid ${color.line}`,borderRadius:8,padding:"7px 8px"}} /></label>
                <span style={{...font.small,padding:"7px 10px",borderRadius:8,background:"#F5F7FA"}}>Сейчас активно: {Number.isFinite(Number(cat?.active)) ? Number(cat.active) : "—"}</span>
                <span style={{...font.small,padding:"7px 10px",borderRadius:8,background:"#FFF8EB",color:"#8A5A00"}}>Не хватает: {Number.isFinite(Number(cat?.active)) ? Math.max(0,Number(strategy.target_active_ads || 0)-Number(cat.active || 0)) : "—"}</span>
              </div>
            </div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:12,marginTop:14}}>
              <label style={{...font.small,color:color.heading}}>Направления
                <textarea rows={3} value={Array.isArray(strategy.directions) ? strategy.directions.join("\n") : ""} onChange={(e)=>updateStrategy("directions",e.target.value.split(/\n|,/).map((x:string)=>x.trim()).filter(Boolean))} placeholder="Например: окна, остекление балконов" style={{display:"block",width:"100%",marginTop:5,border:`1px solid ${color.line}`,borderRadius:10,padding:9,font:"inherit"}} />
              </label>
              <label style={{...font.small,color:color.heading}}>Города
                <textarea rows={3} value={Array.isArray(strategy.cities) ? strategy.cities.join("\n") : ""} onChange={(e)=>updateStrategy("cities",e.target.value.split(/\n|,/).map((x:string)=>x.trim()).filter(Boolean))} placeholder="Например: Пенза, Москва" style={{display:"block",width:"100%",marginTop:5,border:`1px solid ${strategyLoaded && !strategySaved ? "#F59E0B" : color.line}`,borderRadius:10,padding:9,font:"inherit"}} />
                <span role="status" aria-live="polite" style={{display:"block",marginTop:6,fontSize:12,lineHeight:1.35,color:!strategyLoaded?color.muted:strategySaved?"#18794E":"#B54708",fontWeight:strategyLoaded?700:500}}>
                  {!strategyLoaded
                    ? "Проверяю сохранённые города…"
                    : strategySaved
                      ? `✓ Сохранено на сервере${Array.isArray(strategy.cities) && strategy.cities.length ? ": " + strategy.cities.join(" · ") : ""}`
                      : "● Есть несохранённые изменения — нажмите «Сохранить стратегию»"}
                </span>
              </label>
              <div style={{display:"grid",gap:7,alignContent:"start",paddingTop:2}}>
                {[["use_price_list","Использовать прайс"],["use_site_services","Использовать услуги с сайта"],["unique_texts","Уникализировать тексты"],["unique_images","Уникализировать изображения"],["require_verified_price","Требовать подтверждённую цену"],["require_preflight","Проверять перед публикацией"]].map(([key,label])=><label key={key} style={{...font.small,color:color.heading,display:"flex",gap:8,alignItems:"center"}}><input type="checkbox" checked={Boolean(strategy[key])} onChange={(e)=>updateStrategy(key,e.target.checked)} />{label}</label>)}
              </div>
            </div>
            <div style={{display:"flex",justifyContent:"flex-end",alignItems:"center",gap:10,marginTop:12,flexWrap:"wrap"}}>
              {strategyLoaded ? <span role="status" style={{...font.small,color:strategySaved?"#18794E":"#B54708",fontWeight:700}}>
                {strategySaved ? "✓ Стратегия сохранена для аккаунта" : "● Изменения пока не сохранены"}
              </span> : null}
              <Button type="button" kind="ghost" onClick={saveStrategy} disabled={strategySaving || !strategyLoaded || strategySaved}>{strategySaving ? "Сохраняю…" : strategySaved ? "✓ Сохранено" : "Сохранить изменения"}</Button>
            </div>
          </Card>
          <Card id="campaign-maintain-active" className="boris-campaign-overview-summary" style={{ display:workspaceTab==="automation"?"block":"none", marginBottom: space.lg }}>
            <div style={{display:"flex",justifyContent:"space-between",gap:14,alignItems:"flex-start",flexWrap:"wrap"}}>
              <div style={{flex:"1 1 420px"}}>
                <div style={{...font.h3,color:color.heading}}>Поддерживать активные объявления каждый день</div>
                <div style={{...font.small,marginTop:4}}>Задайте, сколько объявлений должно быть активно в профиле. BORIS сверяет весь аккаунт, включая объявления, созданные не через BORIS, и строит план: обновить действующие → вернуть подходящие завершённые → создать недостающие.</div>
              </div>
              <div style={{display:"grid",gap:7}}>
                <label style={{display:"inline-flex",gap:8,alignItems:"center",padding:"8px 10px",borderRadius:10,background:inventoryPolicy.enabled?"#ECFDF3":"#F5F7FA",...font.small,color:color.heading}}>
                  <input type="checkbox" checked={Boolean(inventoryPolicy.enabled)} onChange={(e)=>setInventoryPolicy((p:any)=>({...p,enabled:e.target.checked}))}/>
                  Режим включён
                </label>
                <label style={{display:"inline-flex",gap:8,alignItems:"center",padding:"8px 10px",borderRadius:10,background:inventoryPolicy.auto_apply?"#FFF7E6":"#F5F7FA",...font.small,color:color.heading}}>
                  <input type="checkbox" checked={Boolean(inventoryPolicy.auto_apply)} onChange={(e)=>setInventoryPolicy((p:any)=>({...p,auto_apply:e.target.checked}))}/>
                  Автоматически применять план каждый день
                </label>
              </div>
            </div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(210px,1fr))",gap:12,marginTop:14}}>
              <label style={{display:"grid",gap:5,...font.small,color:color.heading}}>Сколько объявлений должно быть активно
                <input type="number" min={0} max={100000} value={Number(inventoryPolicy.target_active_ads||0)} onChange={(e)=>setInventoryPolicy((p:any)=>({...p,target_active_ads:Math.max(0,Number(e.target.value||0))}))} style={{border:`1px solid ${color.line}`,borderRadius:9,padding:"9px 10px"}}/>
              </label>
              <label style={{display:"grid",gap:5,...font.small,color:color.heading}}>Источник актуальной цены
                <BorisSelect value={String(inventoryPolicy.price_source||"company_price_list")} onChange={value=>setInventoryPolicy((p:any)=>({...p,price_source:value}))} placeholder="Источник актуальной цены" ariaLabel="Источник актуальной цены" options={[
                  {value:"company_price_list",label:"Прайс во вкладке «О компании»"},
                  {value:"website",label:"Каталог/цены с сайта"},
                  {value:"range",label:"Заданный диапазон цен"},
                ]}/>
              </label>
              {inventoryPolicy.price_source==="range" ? <>
                <label style={{display:"grid",gap:5,...font.small,color:color.heading}}>Цена от<input type="number" min={1} value={Number(inventoryPolicy.price_min||0)||""} onChange={(e)=>setInventoryPolicy((p:any)=>({...p,price_min:Math.max(0,Number(e.target.value||0))}))} style={{border:`1px solid ${color.line}`,borderRadius:9,padding:"9px 10px"}}/></label>
                <label style={{display:"grid",gap:5,...font.small,color:color.heading}}>Цена до<input type="number" min={1} value={Number(inventoryPolicy.price_max||0)||""} onChange={(e)=>setInventoryPolicy((p:any)=>({...p,price_max:Math.max(0,Number(e.target.value||0))}))} style={{border:`1px solid ${color.line}`,borderRadius:9,padding:"9px 10px"}}/></label>
                <label style={{display:"flex",gap:8,alignItems:"center",alignSelf:"end",padding:"9px 10px",border:`1px solid ${color.line}`,borderRadius:9,...font.small,color:color.heading}}><input type="checkbox" checked={Boolean(inventoryPolicy.randomize_price)} onChange={(e)=>setInventoryPolicy((p:any)=>({...p,randomize_price:e.target.checked}))}/>Выбирать цену внутри диапазона детерминированно для разных объявлений</label>
              </> : null}
            </div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(230px,1fr))",gap:7,marginTop:12}}>
              {[
                ["allow_create","Создавать новые при дефиците"],
                ["allow_edit_existing","Редактировать существующие, даже не созданные BORIS"],
                ["allow_reactivate","Возвращать подходящие завершённые/неактивные"],
                ["allow_update_price","Обновлять цены"],
                ["allow_update_text","Обновлять описания"],
                ["allow_update_media","Обновлять фотографии"],
                ["allow_update_category","Обновлять категорию и характеристики"],
              ].map(([key,label])=><label key={key} style={{display:"flex",gap:8,alignItems:"center",...font.small,color:color.heading}}><input type="checkbox" checked={Boolean(inventoryPolicy[key])} onChange={(e)=>setInventoryPolicy((p:any)=>({...p,[key]:e.target.checked}))}/>{label}</label>)}
            </div>
            {inventoryPlan ? <div data-testid="campaign-inventory-plan" style={{marginTop:14,padding:"12px 14px",border:"1px solid #D6E4FF",background:"#F8FBFF",borderRadius:12}}>
              <div style={{...font.bodyStrong,color:color.heading}}>План на текущий момент</div>
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(135px,1fr))",gap:8,marginTop:8,...font.small}}>
                <span>Активно: <b>{Number(inventoryPlan.inventory?.active||0)}</b></span>
                <span>Цель: <b>{Number(inventoryPlan.target?.active_ads||0)}</b></span>
                <span>Дефицит: <b>{Number(inventoryPlan.target?.deficit||0)}</b></span>
                <span>Можно вернуть: <b>{Number(inventoryPlan.plan?.reactivate||0)}</b></span>
                <span>Создать новых: <b>{Number(inventoryPlan.plan?.create_new||0)}</b></span>
                <span>Проверить active: <b>{Number(inventoryPlan.plan?.edit_active||0)}</b></span>
                {inventoryPolicy.price_source==="range" ? <span>Цена вне диапазона: <b>{Number(inventoryPlan.plan?.price_updates_by_range||0)}</b></span> : null}
              </div>
              <div style={{...font.small,marginTop:8,color:color.muted}}>Расчёт использует фактический инвентарь Avito. Этот предпросмотр ничего не меняет наружу.</div>
            </div> : null}
            {inventoryJob ? <div data-testid="campaign-inventory-progress" style={{marginTop:14,padding:"12px 14px",border:"1px solid #F7D58B",background:"#FFFBF2",borderRadius:12}}>
              <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",flexWrap:"wrap"}}>
                <div><div style={{...font.bodyStrong,color:color.heading}}>Работа BORIS по поддержанию профиля</div><div style={{...font.small,marginTop:3,color:color.muted}}>{inventoryJob.message || inventoryJob.state_text || "BORIS продолжает работу"}</div></div>
                <b style={{color:"#8A5A00"}}>{Math.max(0,Math.min(100,Number(inventoryJob.total||0)>0?Math.round(Number(inventoryJob.done||0)/Math.max(1,Number(inventoryJob.total||1))*100):(["queued","running","validating"].includes(String(inventoryJob.status||""))?8:(inventoryJob.status==="completed"?100:0))))}%</b>
              </div>
              <div role="progressbar" aria-label="Прогресс поддержания активных объявлений" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.max(0,Math.min(100,Number(inventoryJob.total||0)>0?Math.round(Number(inventoryJob.done||0)/Math.max(1,Number(inventoryJob.total||1))*100):0))} style={{height:9,borderRadius:999,background:"#F3E8CF",overflow:"hidden",marginTop:8}}>
                <div style={{height:"100%",width:`${Math.max(3,Math.min(100,Number(inventoryJob.total||0)>0?Math.round(Number(inventoryJob.done||0)/Math.max(1,Number(inventoryJob.total||1))*100):(["queued","running","validating"].includes(String(inventoryJob.status||""))?8:(inventoryJob.status==="completed"?100:0))))}%`,background:"#F59E0B",borderRadius:999,transition:"width .25s ease"}}/>
              </div>
              <div style={{...font.small,marginTop:7,color:color.muted}}>Этап: <b>{inventoryStageLabel(String(inventoryJob.stage||inventoryJob.status||""))}</b>{Number(inventoryJob.total||0)>0 ? ` · ${Number(inventoryJob.done||0)}/${Number(inventoryJob.total||0)}` : ""}. Можно закрыть или обновить страницу — прогресс хранится на сервере.</div>
              {Number(inventoryJob?.result?.legacy_browser?.total||0)>0 ? <div style={{marginTop:8,padding:"8px 10px",borderRadius:10,background:"#fff",border:"1px solid #F3E0B8",...font.small,color:color.heading}}>
                Старые объявления: <b>{Number(inventoryJob.result.legacy_browser.processed||0)} / {Number(inventoryJob.result.legacy_browser.total||0)}</b>
                {Number(inventoryJob.result.legacy_browser.verified||0)>0 ? <span style={{marginLeft:8,color:"#18794E"}}>· подтверждено {Number(inventoryJob.result.legacy_browser.verified||0)}</span> : null}
              </div> : null}
              {Array.isArray(inventoryJob?.result?.blockers) && inventoryJob.result.blockers.length ? <div style={{display:"grid",gap:6,marginTop:8}}>
                {inventoryJob.result.blockers.slice(0,5).map((blocker:any,index:number)=><div key={String(blocker?.message||index)} style={{padding:"8px 10px",borderRadius:10,background:"#FFF5F3",border:"1px solid #FFD6CC",...font.small,color:"#8A2D1E"}}>{String(blocker?.message||"Нужно проверить часть объявлений")}</div>)}
              </div> : null}
            </div> : null}
            <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"center",flexWrap:"wrap",marginTop:13}}>
              <div style={{...font.small,color:color.muted}}>BORIS меняет только объявления выбранного аккаунта и после каждого внешнего изменения повторно проверяет результат.</div>
              <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
                <Button type="button" kind="ghost" onClick={()=>refreshInventoryPlan()} disabled={inventoryPlanning}>{inventoryPlanning?"Считаю…":"Рассчитать план"}</Button>
                <Button type="button" kind="ghost" onClick={saveInventoryPolicy} disabled={inventorySaving || inventoryRunning}>{inventorySaving?"Сохраняю…":"Сохранить режим"}</Button>
                <Button type="button" onClick={runInventoryNow} disabled={inventoryRunning || ["queued","running","validating"].includes(String(inventoryJob?.status||""))}>{inventoryRunning?"Запускаю…":(["queued","running","validating"].includes(String(inventoryJob?.status||""))?"BORIS уже работает":"Запустить сейчас")}</Button>
              </div>
            </div>
          </Card>
          <Card id="campaign-create" className="boris-campaign-overview-summary" style={{ display:workspaceTab==="projects"&&showLaunchWorkspace?"block":"none", marginBottom: space.lg }}>
            <div>
              <div style={{...font.h3, color:color.heading}}>Запустить рекламную кампанию с нуля</div>
              <div style={{...font.small,marginTop:4}}>Напишите задачу так, как поставили бы её маркетологу. BORIS сам выделит товары/услуги, города, цену «от», разные рекламные посылы, проверит категории Avito и дальше спросит только обязательные характеристики.</div>
              <div className="boris-campaign-prompt-examples">
                <div className="boris-campaign-prompt-examples-head">
                  <div>
                    <div className="boris-campaign-prompt-examples-title">Боевые примеры для выбранного клиента</div>
                    <div className="boris-campaign-prompt-examples-subtitle">Карточки собраны из данных компании и реально увиденных объявлений выбранного аккаунта. Нажмите — BORIS подготовит одну тестовую карточку перед массовым запуском.</div>
                  </div>
                  <div className="boris-campaign-prompt-tabs" role="tablist" aria-label="Тип рекламной кампании">
                    <button type="button" className={promptKind === "services" ? "active" : ""} onClick={()=>setPromptKind("services")}><Icon name="briefcase" size={17} /> Услуги</button>
                    <button type="button" className={promptKind === "products" ? "active" : ""} onClick={()=>setPromptKind("products")}><Icon name="layers" size={17} /> Товары</button>
                  </div>
                </div>
                <div className="boris-campaign-prompt-grid">
                  {promptExamples.map((example, index)=><button type="button" key={example.title} className="boris-campaign-prompt-card" onClick={()=>{setSelectedPromptExample(example);updateCampaignBrief(formatPromptForEditor(example.prompt));window.setTimeout(()=>document.getElementById("boris-campaign-brief")?.scrollIntoView({behavior:"smooth",block:"center"}),20);}}>
                    <span className="boris-campaign-prompt-icon"><Icon name={example.icon} size={20} /></span>
                    <span className="boris-campaign-prompt-copy"><strong>{example.title}</strong><small>{example.note}</small>{"goal" in example && <em>{example.goal}</em>}</span>
                    <span className="boris-campaign-prompt-number">0{index+1}</span>
                    <span className="boris-campaign-prompt-arrow"><Icon name="arrow" size={18} /></span>
                  </button>)}
                </div>
                <div className="boris-campaign-prompt-rules" aria-label="Правила генерации объявлений">
                  <div><Icon name="star" size={18}/><span><b>Уникализация</b><small>Внизу каждого описания — свой конкретный пример задачи; между карточками меняется смысловой ракурс, а не только слова</small></span></div>
                  <div><Icon name="check" size={16}/><span><b>Орфография</b><small>Проверка текста до сохранения карточек</small></span></div>
                  <div><Icon name="zap" size={16}/><span><b>До 3 CTA</b><small>Короткие призывы без навязчивых повторов</small></span></div>
                  <div><Icon name="target" size={16}/><span><b>Фото + 1 главный баннер</b><small>Количество фото — из задания; баннер крупный и читаемый с телефона</small></span></div>
                  <div><Icon name="info" size={16}/><span><b>Нецелевая аудитория</b><small>Только реальные ограничения из данных клиента</small></span></div>
                </div>
              </div>
              <p role="status" style={{fontSize:13,color:briefStorageWarning?"#B42318":"#667085"}}>{briefStorageWarning ? "Браузер не смог сохранить копию. Не закрывайте страницу до создания кампании." : "Текст сохраняется на этом устройстве. После обновления страницы можно продолжить."}</p>
              <textarea id="boris-campaign-brief" value={campaignBrief} onChange={(e)=>{setSelectedPromptExample(null);updateCampaignBrief(e.target.value);}} rows={5} placeholder={'Например: Сделай 30 разных объявлений по ремонту фар в Санкт-Петербурге. Цена от 3 500 ₽. Сделай разные посылы: срочный ремонт, сложные случаи, замена стекла, восстановление корпуса. Проверь категорию и обязательные параметры Avito.'} style={{width:"100%",marginTop:12,border:`1px solid ${color.line}`,borderRadius:12,padding:"12px 13px",font:"inherit",resize:"vertical",minHeight:112,background:"#fff"}} />
              <div data-testid="campaign-source-contract" style={{marginTop:12,padding:"14px",border:`1px solid ${color.line}`,borderRadius:14,background:"#FAFBFC"}}>
                <div style={{...font.bodyStrong,color:color.heading}}>Сайт, каталог и источники данных</div>
                <div style={{...font.small,marginTop:4,color:color.muted}}>Можно дать сайт целиком или конкретные разделы. BORIS использует существующий импорт сайта и сохраняет связку каждого товара: его фотографии → его характеристики → его цена.</div>
                <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(260px,1fr))",gap:10,marginTop:10}}>
                  <label style={{display:"grid",gap:5,...font.small,color:color.heading}}>Сайт или ссылки на разделы
                    <textarea rows={3} value={sourceUrls} onChange={(e)=>{const v=e.target.value;setSourceUrls(v);persistSourceContractDraft(sourceContractDraftValue({sourceUrls:v}));}} placeholder={'https://site.ru/catalog\nhttps://site.ru/services'} style={{width:"100%",border:`1px solid ${color.line}`,borderRadius:10,padding:9,font:"inherit",resize:"vertical"}} />
                  </label>
                  <label style={{display:"grid",gap:5,...font.small,color:color.heading}}>Нужные разделы словами или ссылками
                    <textarea rows={3} value={sourceSections} onChange={(e)=>{const v=e.target.value;setSourceSections(v);persistSourceContractDraft(sourceContractDraftValue({sourceSections:v}));}} placeholder={'Металлочерепица\nФасад\nhttps://site.ru/catalog/fence'} style={{width:"100%",border:`1px solid ${color.line}`,borderRadius:10,padding:9,font:"inherit",resize:"vertical"}} />
                  </label>
                </div>
                <label style={{display:"flex",gap:8,alignItems:"flex-start",marginTop:10,...font.small,color:color.heading}}>
                  <input type="checkbox" checked={bindProductData} onChange={(e)=>{const v=e.target.checked;setBindProductData(v);persistSourceContractDraft(sourceContractDraftValue({bindProductData:v}));}} />
                  <span><b>Жёстко связывать товар с его данными</b><br/><span style={{color:color.muted}}>Фото, характеристики и цена одного товара не смешиваются с другим товаром.</span></span>
                </label>
                <div style={{display:"flex",gap:10,flexWrap:"wrap",alignItems:"end",marginTop:10}}>
                  <label style={{display:"grid",gap:5,...font.small,color:color.heading}}>Источник цены
                    <BorisSelect value={campaignPriceSource} onChange={value=>{const v=value as typeof campaignPriceSource;setCampaignPriceSource(v);persistSourceContractDraft(sourceContractDraftValue({campaignPriceSource:v}));}} placeholder="Источник цены" ariaLabel="Источник цены" options={[
                      {value:"company_or_site",label:"Прайс компании → затем сайт"},
                      {value:"company_price_list",label:"Только прайс компании"},
                      {value:"website",label:"Цена конкретного товара с сайта"},
                      {value:"range",label:"Диапазон, заданный вручную"},
                    ]}/>
                  </label>
                  {campaignPriceSource==="range" ? <>
                    <label style={{display:"grid",gap:5,...font.small,color:color.heading}}>Цена от<input type="number" min={1} value={campaignPriceMin} onChange={(e)=>{const v=e.target.value;setCampaignPriceMin(v);persistSourceContractDraft(sourceContractDraftValue({campaignPriceMin:v}));}} style={{width:130,border:`1px solid ${color.line}`,borderRadius:9,padding:"8px 9px"}} /></label>
                    <label style={{display:"grid",gap:5,...font.small,color:color.heading}}>Цена до<input type="number" min={1} value={campaignPriceMax} onChange={(e)=>{const v=e.target.value;setCampaignPriceMax(v);persistSourceContractDraft(sourceContractDraftValue({campaignPriceMax:v}));}} style={{width:130,border:`1px solid ${color.line}`,borderRadius:9,padding:"8px 9px"}} /></label>
                  </> : null}
                </div>
                {(sourceImportStatus || sourceImportProgress.running || sourceCatalogSummary) ? <div role="status" data-testid="campaign-source-import-progress" style={{marginTop:10,padding:"10px 12px",borderRadius:11,background:"#F5F8FF",border:"1px solid #DCE6FF"}}>
                  <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"center",...font.small,color:"#315bea"}}>
                    <b>{sourceImportProgress.running ? "BORIS изучает сайт" : (sourceImportStatus || "Каталог готов")}</b>
                    {sourceImportProgress.total>0 ? <span>{sourceImportProgress.done} / {sourceImportProgress.total}</span> : null}
                  </div>
                  {sourceImportProgress.running || sourceImportProgress.total>0 ? <div style={{height:8,borderRadius:999,background:"#E6ECFA",overflow:"hidden",marginTop:8}}>
                    <div style={{height:"100%",width:`${sourceImportProgress.total>0?Math.max(4,Math.min(100,Math.round(sourceImportProgress.done/Math.max(1,sourceImportProgress.total)*100))):8}%`,background:"#F59E0B",borderRadius:999,transition:"width .25s ease"}} />
                  </div> : null}
                  {sourceImportProgress.running ? <div style={{...font.small,marginTop:7,color:color.muted}}>Можно закрыть или обновить страницу — BORIS продолжит ту же задачу.</div> : null}
                  {sourceCatalogSummary ? <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(120px,1fr))",gap:7,marginTop:9}}>
                    <span style={{padding:"7px 8px",borderRadius:9,background:"#fff",...font.small}}>Товаров: <b>{Number(sourceCatalogSummary.total||0)}</b></span>
                    <span style={{padding:"7px 8px",borderRadius:9,background:"#fff",...font.small}}>С ценой: <b>{Number(sourceCatalogSummary.withPrice||0)}</b></span>
                    <span style={{padding:"7px 8px",borderRadius:9,background:"#fff",...font.small}}>С фото: <b>{Number(sourceCatalogSummary.withPhotos||0)}</b></span>
                    <span style={{padding:"7px 8px",borderRadius:9,background:"#fff",...font.small}}>С характеристиками: <b>{Number(sourceCatalogSummary.withCharacteristics||0)}</b></span>
                  </div> : null}
                </div> : null}
              </div>
              <div style={{marginTop:10,padding:"10px 12px",border:"1px solid #fde3a7",borderRadius:11,background:"#fffaf0",fontSize:12.5,lineHeight:1.45,color:"#7a4b00"}}><b>Важно перед запуском в Avito:</b> для категории «Услуги» нужен активный тариф <b>«Расширенный»</b>, для категории «Товары» — активный тариф <b>«Максимальный»</b>. BORIS проверит подключение и не будет выдавать неподтверждённый тариф за активный.</div>
              {(creating || campaignLaunchStatus) ? <div role="status" aria-live="polite" data-testid="campaign-launch-live-status" style={{marginTop:10,padding:"12px 14px",border:"1px solid #D6E4FF",borderRadius:12,background:"#F8FBFF"}}>
                <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",flexWrap:"wrap",...font.small,color:color.heading}}>
                  <span><b>{campaignLaunchStatus || "Задача принята. BORIS запускает подготовку…"}</b></span>
                  <span style={{color:color.muted}}>Прошло: {formatDuration(Math.max(0,Math.floor((clockNow-(campaignLaunchStartedAt||clockNow))/1000)))}</span>
                </div>
                <div style={{height:8,borderRadius:999,background:"#E8EEF7",overflow:"hidden",marginTop:8}}><div style={{height:"100%",width:creating?"38%":"100%",background:"#F59E0B",borderRadius:999,transition:"width .25s ease"}}/></div>
                <div style={{...font.small,marginTop:7,color:color.muted}}>После запуска работа хранится на сервере. Можно перейти в другой аккаунт, запустить следующую кампанию, закрыть или обновить страницу — текущая задача не остановится.</div>
              </div> : null}
              <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",flexWrap:"wrap",marginTop:10}}>
                <div style={font.small}>После запуска откроется пошаговый мастер. Ничего не публикуется без проверки и согласования.</div>
                <Button type="button" onClick={createFeedFactory} disabled={creating || !campaignBrief.trim()}>{creating ? "Разбираю задачу…" : "Начать создание кампании"}</Button>
              </div>
            </div>
            {!campaigns.length && Array.isArray(feedReconcile?.visible_campaign_ids) && feedReconcile.visible_campaign_ids.length ? <div data-testid="campaign-reconcile-fallback" style={{marginTop:space.md,padding:"12px 14px",border:"1px solid #F5C26B",borderRadius:10,background:"#FFF8E8",...font.small,color:"#7A4B00"}}>BORIS нашёл связанные проекты: {feedReconcile.visible_campaign_ids.join(", ")}. Детали проекта временно недоступны; BORIS не считает этот аккаунт пустым и не создаёт дубликаты.</div> : null}
            {globalActiveCampaigns.length ? <section data-testid="campaign-global-active" style={{marginTop:space.md,padding:"13px 14px",border:"1px solid #c7d7fe",borderRadius:14,background:"#f7f9ff"}}>
              <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"center",flexWrap:"wrap"}}>
                <div>
                  <div style={{...font.h3,color:color.heading}}>Активные кампании по всем аккаунтам</div>
                  <div style={{...font.small,marginTop:3,color:color.muted}}>Можно запускать следующего клиента — эти задачи продолжаются на сервере независимо друг от друга.</div>
                </div>
                <span style={{fontSize:12,fontWeight:900,padding:"5px 9px",borderRadius:999,background:"#eef4ff",color:"#315bea"}}>Сейчас: {globalActiveCampaigns.length}</span>
              </div>
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(250px,1fr))",gap:8,marginTop:10}}>
                {globalActiveCampaigns.slice(0,8).map((task:any)=>{
                  const stageLabels:Record<string,string>={queued:"В очереди",preflight:"Предварительная проверка",materialize:"Создание карточек",texts:"Тексты",media:"Фотографии",media_shards:"Фотографии",media_retry:"Фотографии",banners:"Баннеры",internal_validation:"Проверка",official_avito_validation:"Проверка Avito",ready_for_review:"Готово",sending:"Передача в Avito",awaiting_avito_result:"Avito проверяет"};
                  const stage=String(task?.stage||"queued");
                  const done=Math.max(0,Number(task?.done||0));
                  const total=Math.max(0,Number(task?.total||0));
                  const itemProgress=Boolean(task?.item_progress)&&total>0;
                  const activeSeconds=task?.duration_active_seconds==null?null:Math.max(0,Number(task.duration_active_seconds||0));
                  const waitingSeconds=task?.duration_waiting_seconds==null?null:Math.max(0,Number(task.duration_waiting_seconds||0));
                  const needsAction=Boolean(task?.requires_owner_action);
                  const healthState=String(task?.health_state||"OK");
                  const healthReasons=Array.isArray(task?.health_reasons)?task.health_reasons:[];
                  const healthCritical=healthState==="CRITICAL";
                  const healthWarning=healthState==="WARNING";
                  const healthLabel=needsAction?"Нужно действие":healthCritical?"Требует внимания":healthWarning?"Есть задержка":"В работе";
                  return <button key={String(task.job_id)} type="button" onClick={()=>{
                    const aid=String(task?.account_id||"");
                    const cid=Number(task?.campaign_id||0);
                    if(aid)setSelectedAccount(aid);
                    if(cid>0)router.push("/dashboard/campaign/"+cid+"?account_id="+encodeURIComponent(aid));
                  }} style={{textAlign:"left",border:"1px solid #dbeafe",borderRadius:11,background:"#fff",padding:"10px 11px",cursor:"pointer",font:"inherit",color:color.heading}}>
                    <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"start"}}>
                      <div style={{minWidth:0}}>
                        <div style={{fontSize:11,color:color.muted,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{String(task?.account_name||task?.account_id||"")}</div>
                        <div style={{fontSize:12.5,fontWeight:900,marginTop:2,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{String(task?.campaign_name||("Кампания #"+task?.campaign_id))}</div>
                      </div>
                      <span style={{fontSize:10,fontWeight:900,padding:"4px 6px",borderRadius:999,background:needsAction?"#fff7ed":healthCritical?"#fff1f0":healthWarning?"#fff7ed":"#ecfdf3",color:needsAction?"#b54708":healthCritical?"#b42318":healthWarning?"#b54708":"#067647",whiteSpace:"nowrap"}}>{healthLabel}</span>
                    </div>
                    <div style={{display:"flex",justifyContent:"space-between",gap:8,marginTop:7,fontSize:11.5}}>
                      <span>{stageLabels[stage]||"BORIS работает"}</span>
                      <b>{itemProgress?done+"/"+total:"прогресс сохранён"}</b>
                    </div>
                    {(activeSeconds!=null||waitingSeconds!=null)?<div style={{fontSize:10.5,color:color.muted,marginTop:5}}>Работа: <b>{activeSeconds==null?"—":formatDuration(activeSeconds)}</b> · ожидание: <b>{waitingSeconds==null?"—":formatDuration(waitingSeconds)}</b></div>:null}
                    {(healthWarning||healthCritical)&&healthReasons.length?<div style={{fontSize:10.5,color:healthCritical?"#b42318":"#b54708",marginTop:4,lineHeight:1.35}}>{String(healthReasons[0])}</div>:null}
                  </button>;
                })}
              </div>
              {globalActiveCampaigns.length>8?<div style={{...font.small,marginTop:8,color:color.muted}}>Ещё активных кампаний: {globalActiveCampaigns.length-8}</div>:null}
            </section> : null}
            {campaigns.length ? <section id="campaign-projects" style={{ marginTop:space.md, borderTop:`1px solid ${color.line}`, paddingTop:space.md }}>
              <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"end",flexWrap:"wrap",marginBottom:space.sm}}>
                <div><div style={{...font.h3,color:color.heading}}>Проекты объявлений</div><div style={{...font.small,marginTop:4}}>Готовые и текущие проекты выбранного аккаунта. Прогресс сохраняется на сервере.</div></div>
                <span style={{fontSize:12,fontWeight:850,color:"#667085"}}>{campaigns.length} {campaigns.length===1?"проект":"проектов"}</span>
              </div>
              <div className="boris-campaign-list-grid" style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(280px,1fr))",gap:space.sm}}>{campaigns.slice(0,8).map((c:any)=>{
                const ff=c.settings?.feed_factory||{}; const n=c.counts||{};
                const publicationStep=String(ff.step||"");
                const publicationActive=["publication_processing","publication_scheduled","publication_pending_review","publication_completed","publication_partial","publication_failed"].includes(publicationStep);
                const reviewReady=Boolean(ff.ready_for_owner_approval) && !publicationActive;
                const prepared=Number(ff.internal_validation?.ready || (reviewReady ? n.all : n.ready) || 0);
                const photos=Number(ff.commercial_package?.total_media_links || ff.preview_integrity?.media_nonempty || 0);
                const xmlPassed=(ff.official_xml_check?.status === "pass" || ff.official_xmlcheck?.status === "ok");
                const action=(n.needs_data||0)+(n.publish_failed||0);
                const activeJob=c.active_job||null;
                const globalJob=globalActiveCampaigns.find((task:any)=>Number(task?.campaign_id||0)===Number(c.id)&&String(task?.account_id||"")===String(account||""))||null;
                const jobState=globalJob||activeJob;
                const jobActive=Boolean(jobState && ["queued","running","validating"].includes(String(jobState.status||"")));
                const useItemProgress=Boolean(globalJob?.item_progress)&&Number(globalJob?.total||0)>0;
                const jobDone=Math.max(0,Number(useItemProgress?globalJob?.done:activeJob?.done||0));
                const jobTotal=Math.max(0,Number(useItemProgress?globalJob?.total:activeJob?.total||0));
                const jobPercent=jobTotal>0 ? Math.max(0,Math.min(100,Math.round((jobDone/Math.max(1,jobTotal))*100))) : (jobActive ? 8 : 0);
                const jobStage=String(globalJob?.stage||activeJob?.stage||"");
                const jobStageText:Record<string,string>={queued:"В очереди",competitor:"Анализ конкурентов",structure:"Структура",materialize:"Создание карточек",texts:"Тексты",media:"Фотографии",banners:"Баннеры",validation:"Проверка",safe_feed:"Готовность Avito",sending:"Отправка в Avito"};
                const step:Record<string,string>={brief:"Нужно ТЗ",questions:"Нужны уточнения",structure:"Проверьте план",texts:"Готовим тексты",media:"Нужны фото",category:"Категории",settings:"Цена и география",validation:"Нужна проверка",preview:"Ждёт согласования",safe_feed:"Автозагрузка готова",publication_processing:"Avito обрабатывает публикацию",publication_scheduled:"Avito заберёт объявления по расписанию",publication_pending_review:"Ждём итоговый отчёт Avito",publication_completed:"Опубликовано полностью",publication_partial:"Опубликовано частично — есть ошибки",publication_failed:"Avito не опубликовал"};
                const statusLabel = jobActive
                  ? (jobStageText[jobStage] || activeJob?.state_text || "BORIS работает")
                  : reviewReady
                  ? "Ждёт согласования"
                  : (step[ff.step||""] || (prepared ? `Готово ${prepared}` : "Проект кампании"));
                const updatedLabel = c.updated_at
                  ? "Изменено " + new Date(c.updated_at).toLocaleDateString("ru-RU")
                  : "Есть сохранённый проект";
                return <article
                  className="boris-campaign-list-card boris-ad-card"
                  data-testid="ad-campaign-card"
                  data-state={reviewReady ? "review" : publicationActive ? "publication" : action ? "attention" : "working"}
                  key={c.id}
                >
                  <div className="boris-ad-card-head">
                    <span className="boris-ad-card-icon" aria-hidden="true">
                      <Icon name={reviewReady ? "check" : publicationActive ? "upload" : "list"} size={20} />
                    </span>
                    <div className="boris-ad-card-copy">
                      <strong>{c.name}</strong>
                      <span>{selectedAccount?.name || account}</span>
                    </div>
                    <span className="boris-ad-card-status">{statusLabel}</span>
                  </div>

                  <div className="boris-ad-card-metrics">
                    <div><span>Объявления</span><strong>{n.all||0}</strong></div>
                    <div><span>Готовы</span><strong>{prepared}</strong></div>
                    <div><span>Фото</span><strong>{photos}</strong></div>
                  </div>

                  <div className="boris-ad-card-result">
                    {reviewReady ? (
                      <>
                        <span>Проверка Avito</span>
                        <strong data-tone={xmlPassed ? "ok" : "warning"}>{xmlPassed ? "Пройдена" : "Требует внимания"}</strong>
                      </>
                    ) : publicationActive && ff.owner_activation ? (
                      <>
                        <span>Avito</span>
                        <strong>
                          Опубликовано {Number(ff.owner_activation.published||0)}
                          {Number(ff.owner_activation.errors||0)>0 ? ` · ошибок ${Number(ff.owner_activation.errors||0)}` : ""}
                        </strong>
                      </>
                    ) : (
                      <>
                        <span>Текущее состояние</span>
                        <strong>{action ? `Требуют действий: ${action}` : "BORIS продолжает работу"}</strong>
                      </>
                    )}
                  </div>

                  {jobActive ? <div data-testid="campaign-card-durable-progress" style={{marginTop:10}}>
                    <div style={{display:"flex",justifyContent:"space-between",gap:10,fontSize:12,color:"#667085"}}>
                      <span>{jobStageText[jobStage] || activeJob?.state_text || "BORIS работает"}{jobTotal>0 ? ` · ${jobDone}/${jobTotal}` : ""}</span>
                      <b style={{color:"#101828"}}>{jobPercent}%</b>
                    </div>
                    <div role="progressbar" aria-label="Прогресс проекта" aria-valuemin={0} aria-valuemax={100} aria-valuenow={jobPercent} style={{height:8,borderRadius:999,background:"#E8EEF7",overflow:"hidden",marginTop:6}}>
                      <div style={{height:"100%",width:`${Math.max(3,jobPercent)}%`,background:"#F59E0B",borderRadius:999,transition:"width .25s ease"}}/>
                    </div>
                    <small style={{display:"block",marginTop:6,color:"#667085"}}>Можно закрыть или обновить страницу — BORIS продолжит эту же задачу.</small>
                  </div> : null}

                  <div className="boris-ad-card-footer">
                    <small>{updatedLabel}</small>
                    <Button
                      kind={reviewReady ? "primary" : "ghost"}
                      data-testid="open-ad-campaign"
                      onClick={()=>reviewReady ? window.location.assign("/dashboard/campaign/"+c.id) : router.push("/dashboard/campaign/"+c.id)}
                    >
                      {reviewReady ? "Посмотреть и согласовать" : "Открыть проект"}
                    </Button>
                  </div>
                </article>;
              })}</div>
            </section> : <div id="campaign-projects" style={{marginTop:space.md,borderTop:`1px solid ${color.line}`,paddingTop:space.sm}}>
              <div style={{...font.body,color:color.heading}}><b>У выбранного аккаунта пока нет кампаний</b></div>
              <div style={{...font.small,marginTop:4}}>Сейчас выбран: {selectedAccount?.name || account}. Кампании других аккаунтов здесь намеренно не показываются — переключить аккаунт можно в верхней панели.</div>
            </div>}
          </Card>
          <div className={`boris-campaign-workspace-grid${attention.length ? " has-attention" : ""}`} data-boris-g32b-page="campaign" style={{display:workspaceTab==="projects"&&showLaunchWorkspace?undefined:"none"}}>
            <section className="boris-campaign-actions-pane">
              <div className="boris-campaign-section-heading">Что вы хотите сделать?</div>
              <div className="boris-campaign-section-subtitle">Четыре основных сценария. Остальные инструменты спрятаны ниже и не мешают работе.</div>
              <div className="boris-campaign-actions-grid boris-campaign-primary-actions">
                {primaryCards.map((c) => (
                  <EcActionCard key={c.title} {...c} variant="work" onClick={() => {
                    if (c.go.startsWith("#")) document.querySelector(c.go)?.scrollIntoView({ behavior: "smooth", block: "start" });
                    else router.push(c.go);
                  }} />
                ))}
              </div>
              <details className="boris-campaign-tools" style={{marginTop:14}}>
                <summary><span><Icon name="layers" size={18}/> Все инструменты объявлений</span><small>Материалы, импорт, фотостудия, баннеры, тексты, публикация и подключения</small></summary>
                <div className="boris-campaign-tools-grid">
                  {toolCards.map((c) => (
                    <button key={c.title} type="button" className={`boris-campaign-tool-card tone-${c.tone}`} onClick={() => {
                      if (c.go.startsWith("#")) document.querySelector(c.go)?.scrollIntoView({ behavior: "smooth", block: "start" });
                      else router.push(c.go);
                    }}>
                      <span className="boris-campaign-tool-icon">{c.icon}</span>
                      <span><strong>{c.title}</strong><small>{c.text}</small></span>
                      <b>›</b>
                    </button>
                  ))}
                </div>
              </details>
            </section>
            {attention.length > 0 && (
              <aside className="boris-campaign-attention-panel" aria-label="Требует внимания">
                <div className="boris-campaign-attention-head">
                  <div className="boris-campaign-attention-icon"><Icon name="alert" size={18} /></div>
                  <div>
                    <span>КОНТРОЛЬ КАЧЕСТВА</span>
                    <h3>Требует внимания</h3>
                    <p>Что нужно поправить, чтобы объявления были готовы к работе.</p>
                  </div>
                </div>
                <div className="boris-campaign-attention-list">
                  {attention.map((a, index) => (
                    <button key={`${a.t}-${index}`} type="button" className="boris-campaign-attention-item" onClick={() => router.push(a.go)}>
                      <div className="boris-campaign-attention-count">{a.n}</div>
                      <div className="boris-campaign-attention-copy">
                        <strong>{a.t}</strong>
                        <small>{a.src === "rule" ? "BORIS нашёл по правилам качества" : "Найдено по текущей статистике"}</small>
                      </div>
                      <span className="boris-campaign-attention-arrow">›</span>
                    </button>
                  ))}
                </div>
                <div className="boris-campaign-attention-foot">
                  <Icon name="check" size={14} />
                  <span>После исправления пункт исчезнет отсюда автоматически.</span>
                </div>
              </aside>
            )}
          </div>

          <section style={{ display:workspaceTab==="projects"&&showLaunchWorkspace?"block":"none", marginTop: 26 }}>
            <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", gap:12, flexWrap:"wrap", marginBottom:14 }}>
              <div style={{ ...font.h2, color:color.heading }}>Сводка по объявлениям</div>
              <div style={{ border:`1px solid ${color.line}`, borderRadius:12, padding:"9px 13px", background:color.surface, ...font.small, color:color.heading, fontWeight:700 }}>
                За 30 дней
              </div>
            </div>
            <div className="boris-campaign-summary-grid">
              {metricCards.map((m) => {
                const value = Number(currentMetrics?.[m.key] || 0);
                const d = metricDelta(m.key, value, m.goodUp);
                const trendColor = d.sentiment === "good" ? "#16A34A" : d.sentiment === "bad" ? "#E11D48" : "#94A3B8";
                return (
                  <button key={m.key} type="button" className="boris-campaign-summary-card" onClick={() => router.push(m.go)}
                    style={{ "--summary-accent":m.accent, "--summary-soft":m.soft } as React.CSSProperties}>
                    <span className="boris-campaign-summary-icon">{m.icon}</span>
                    <span className="boris-campaign-summary-copy">
                      <strong>{m.label} <em>{value.toLocaleString("ru-RU")}</em></strong>
                      <small>{m.note}</small>
                      <span className="boris-campaign-summary-trend" style={{color:trendColor}}>{d.arrow} {d.text}</span>
                    </span>
                    <span className="boris-campaign-summary-arrow">›</span>
                  </button>
                );
              })}
            </div>
          </section>
          {(catalogTotal > 0 || feedBackedTotal > 0) && (
            <Card id="campaign-source-separation" style={{ display:workspaceTab==="settings"?"block":"none", marginBottom: space.lg, border: `1px solid ${color.line}` }}>
              <div style={{ ...font.h3, color: color.heading }}>Источники ассортимента</div>
              <div style={{ ...font.small, marginTop: 5 }}>BORIS отдельно считает товары из каталога и объявления, уже связанные с Avito, чтобы не смешивать разные источники.</div>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
                <span style={{ ...font.small, padding: "8px 10px", borderRadius: 8, background: "#EEF4FF", color: "#2457A6" }}>Товаров в каталоге: {catalogTotal}</span>
                <span style={{ ...font.small, padding: "8px 10px", borderRadius: 8, background: feedBackedTotal > 0 ? "#ECFDF3" : "#F5F7FA", color: feedBackedTotal > 0 ? "#18794E" : color.muted }}>Связано с Avito: {feedBackedTotal}</span>
              </div>
            </Card>
          )}
          {showCatalogWarning && (
            <Alert kind="warning">
              Каталог не заведён: добавьте товары или загрузите материалы, чтобы BORIS подготовил объявления к размещению.
              <div style={{ marginTop: 8 }}>
                <Button type="button" onClick={() => router.push("/dashboard/campaign/catalog")}>Открыть каталог</Button>
              </div>
            </Alert>
          )}

        </>
      )}
    </EcShell>
  );
}

// BORIS_AUTOCONVERGENCE_PROBE_20260903_V1: harmless source-generation probe for deploy self-heal regression.
