"use client";
import { Button, Icon } from "@/app/ui";


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
import { apiGet } from "../../lib/api";
import { apiPost } from "../../lib/campaignApi";
import { readCampaignDraft, writeCampaignDraft, clearCampaignDraft } from "../../lib/campaignDraft";
import { useAccounts } from "../../lib/AccountContext";
import MassEditor from "../../listings/MassEditor";

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
  const explicitCity=(description.match(/(?:^|[\n.;])\s*(Москва|Санкт-Петербург|СПб|Сочи|Уфа|Пенза|Ижевск|Стерлитамак)(?=\s*(?:[,.;\n]|$))/m)?.[1] || "").trim();
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
    note:["1 объявление", activeSet.has(rawDirection.toLocaleLowerCase("ru"))?"из активных объявлений":feedSet.has(rawDirection.toLocaleLowerCase("ru"))?"из текущего фида":"из данных компании",categoryLeaf?`Avito: ${categoryLeaf}`:"Avito: уточним подкатегорию",city].filter(Boolean).join(" · "),
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

export default function EcOverview() {
  const router = useRouter();
  const { selectedAccountId, selectedAccount } = useAccounts();

  const [account, setAccount] = useState("");
  const [media, setMedia] = useState<any>({});
  const [cat, setCat] = useState<any>({});
  const [trend, setTrend] = useState<any>({ history: [] });
  const [campaigns, setCampaigns] = useState<any[]>([]);
  const [creating, setCreating] = useState(false);
  const createInFlight = useRef(false);
  const pendingCampaign = useRef<Record<string, number>>({});
  const [campaignBrief, setCampaignBrief] = useState("");
  const briefAccountRef = useRef("");
  const [briefStorageWarning, setBriefStorageWarning] = useState(false);
  useEffect(() => {
    const aid = selectedAccountId || "";
    briefAccountRef.current = aid;
    const saved = aid ? readCampaignDraft<string>("brief:" + aid) : undefined;
    setCampaignBrief(typeof saved === "string" ? saved : "");
    setBriefStorageWarning(false);
  }, [selectedAccountId]);
  const updateCampaignBrief = (value: string) => {
    setCampaignBrief(value);
    const aid = selectedAccountId || "";
    if (aid && aid === briefAccountRef.current) {
      setBriefStorageWarning(!writeCampaignDraft("brief:" + aid, value));
    }
  };
  const [selectedPromptExample, setSelectedPromptExample] = useState<PromptExample|null>(null);
  const [promptKind, setPromptKind] = useState<"services" | "products">("services");
  const [campaignContext, setCampaignContext] = useState<any>({});
  const promptExamples = clientPromptExamples(selectedAccount, promptKind, campaignContext);
  const [loading, setLoading] = useState(true);
  const loadSequence = useRef(0);
  const activeAccountRef = useRef(selectedAccountId);
  activeAccountRef.current = selectedAccountId;
  const [err, setErr] = useState("");

  useEffect(() => { setCampaigns([]); setMedia({}); setCat({}); setTrend({ history: [] }); setCampaignContext({}); setSelectedPromptExample(null); setErr(""); setAccount(selectedAccountId || ""); }, [selectedAccountId]);
  const load = useCallback(async () => {
    const request = ++loadSequence.current;
    if (!account) { setLoading(false); return; }
    setLoading(true); setErr("");
    const [m, c, cc, tr, ctx] = await Promise.all([
      apiGet("/api/media/stats?account_id=" + account),
      apiGet("/api/catalog/summary?account_id=" + account),
      apiGet("/api/campaign/list?account_id=" + encodeURIComponent(account)),
      apiGet("/api/catalog/overview-trend?account_id=" + encodeURIComponent(account)),
      apiGet("/api/campaign/context?account_id=" + encodeURIComponent(account)),
    ]);
    if (request !== loadSequence.current || account !== activeAccountRef.current) return;
    if (m.status === "ok") setMedia(m);
    if (c.status === "ok") setCat(c.summary || {});
    if (cc.status === "ok") setCampaigns(cc.campaigns || []);
    if (tr.status === "ok") setTrend(tr);
    if (ctx.status === "ok") { setCampaignContext(ctx); setPromptKind(inferCampaignKind(ctx)); }
    const failures = [m, c, cc, tr, ctx].filter((item: any) => item?.status === "error");
    if (failures.length) {
      setErr(failures[0]?.message || "Часть данных центра объявлений недоступна");
    }
    setLoading(false);
  }, [account]);
  useEffect(() => { void load(); return () => { loadSequence.current += 1; }; }, [load]);

  const createFeedFactory = async () => {
    if (createInFlight.current) return;
    if (!account || account !== selectedAccountId) { setErr("Сначала выберите аккаунт"); return; }
    if (!campaignBrief.trim()) { setErr("Опишите задачу для рекламной кампании обычными словами"); return; }
    createInFlight.current = true;
    setCreating(true); setErr("");
    const pendingScope = "pending-create:" + account;
    try {
      // Resume the created campaign if the second request failed, including
      // after reload. An edited brief still belongs to this unfinished draft.
      const saved = readCampaignDraft<{ campaignId: number }>(pendingScope);
      let cid = pendingCampaign.current[account] || Number(saved?.campaignId);
      if (!Number.isSafeInteger(cid) || cid <= 0) {
        const r = await apiPost("/api/campaign/create", { name: "Рекламная кампания · " + new Date().toLocaleDateString("ru-RU"), account_id: account, scenario_type: "feed_factory", source_type: "client" });
        cid = Number(r?.campaign?.id);
        if (!Number.isSafeInteger(cid) || cid <= 0) throw new Error("Кампания не создана");
        pendingCampaign.current[account] = cid;
        if (!writeCampaignDraft(pendingScope, { campaignId: cid })) setBriefStorageWarning(true);
      }
      // The overview promises one safe test card first. Mass quantity is parsed
      // from the owner's brief (e.g. «30 объявлений»); do not silently multiply
      // an otherwise single-card request into three variants.
      const selectedPromptStillExact=Boolean(selectedPromptExample && campaignBrief.trim()===formatPromptForEditor(selectedPromptExample.prompt).trim());
      const categoryHints=selectedPromptStillExact && selectedPromptExample?.direction && selectedPromptExample?.categoryTemplateId
        ? {[selectedPromptExample.direction]:{template_id:selectedPromptExample.categoryTemplateId,path:selectedPromptExample.categoryPath||""}} : {};
      const observedParams=selectedPromptStillExact && selectedPromptExample?.categoryParams && Object.keys(selectedPromptExample.categoryParams).length
        ? selectedPromptExample.categoryParams : {};
      await apiPost(`/api/campaign/${cid}/workflow/brief`, { brief: campaignBrief.trim(), variants: 1, category_hints: categoryHints, defaults: Object.keys(observedParams).length ? {params:observedParams} : {} });
      if (readCampaignDraft("brief:" + account) === campaignBrief) clearCampaignDraft("brief:" + account);
      delete pendingCampaign.current[account];
      if (readCampaignDraft<{ campaignId: number }>(pendingScope)?.campaignId === cid) clearCampaignDraft(pendingScope);
      if (briefAccountRef.current === account) router.push("/dashboard/campaign/" + cid);
    } catch (e:any) {
      if (briefAccountRef.current === account) setErr(e?.message || "Не удалось создать кампанию");
    }
    finally { createInFlight.current = false; setCreating(false); }
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
  const showCatalogWarning = catalogTotal <= 0 && mediaTotal <= 0;

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

  return (
    <EcShell tab="overview" title="Центр объявлений"
      subtitle="Ассортимент, фотографии, тексты и публикация — в одном окне">
      {err !== "" && <Alert kind="danger">{err}</Alert>}
      {loading ? (
        <Card><Skeleton lines={5} /></Card>
      ) : !account ? (
        <EcEmpty title="Выберите аккаунт"
          hint="Центр объявлений работает в разрезе одного Avito-аккаунта." />
      ) : (
        <>
          <details id="mass-editor" style={{marginBottom:space.lg,border:`1px solid ${color.line}`,borderRadius:18,background:color.surface,overflow:"hidden",boxShadow:"0 10px 30px rgba(15,23,42,.05)"}}>
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
          <Card id="campaign-create" className="boris-campaign-overview-summary" style={{ marginBottom: space.lg }}>
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
              <div style={{marginTop:10,padding:"10px 12px",border:"1px solid #fde3a7",borderRadius:11,background:"#fffaf0",fontSize:12.5,lineHeight:1.45,color:"#7a4b00"}}><b>Важно перед запуском в Avito:</b> для категории «Услуги» нужен активный тариф <b>«Расширенный»</b>, для категории «Товары» — активный тариф <b>«Максимальный»</b>. BORIS проверит подключение и не будет выдавать неподтверждённый тариф за активный.</div>
              <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",flexWrap:"wrap",marginTop:10}}>
                <div style={font.small}>После запуска откроется пошаговый мастер. Ничего не публикуется без проверки и согласования.</div>
                <Button type="button" onClick={createFeedFactory} disabled={creating || !campaignBrief.trim()}>{creating ? "Разбираю задачу…" : "Начать создание кампании"}</Button>
              </div>
            </div>
            {campaigns.length ? <details id="campaign-projects" style={{ marginTop:space.md, borderTop:`1px solid ${color.line}`, paddingTop:space.sm }}>
              <summary style={{cursor:"pointer",...font.bodyStrong,color:color.heading}}>Показать проекты автозагрузки ({campaigns.length})</summary>
              <div style={{...font.h3, marginTop:10, marginBottom:4}}>Проекты автозагрузки</div>
              <div style={{...font.small,marginBottom:space.sm}}>Это сохранённые рабочие проекты BORIS: в них хранятся объявления, фото, тексты и статус согласования. Выбран аккаунт: <b>{selectedAccount?.name || account}</b></div>
              <div className="boris-campaign-list-grid" style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(280px,1fr))",gap:space.sm}}>{campaigns.slice(0,8).map((c:any)=>{
                const ff=c.settings?.feed_factory||{}; const n=c.counts||{};
                const publicationStep=String(ff.step||"");
                const publicationActive=["publication_processing","publication_scheduled","publication_pending_review","publication_completed","publication_partial","publication_failed"].includes(publicationStep);
                const reviewReady=Boolean(ff.ready_for_owner_approval) && !publicationActive;
                const prepared=Number(ff.internal_validation?.ready || (reviewReady ? n.all : n.ready) || 0);
                const photos=Number(ff.commercial_package?.total_media_links || ff.preview_integrity?.media_nonempty || 0);
                const xmlPassed=(ff.official_xml_check?.status === "pass" || ff.official_xmlcheck?.status === "ok");
                const action=(n.needs_data||0)+(n.publish_failed||0);
                const step:Record<string,string>={brief:"Нужно ТЗ",questions:"Нужны уточнения",structure:"Проверьте план",texts:"Готовим тексты",media:"Нужны фото",category:"Категории",settings:"Цена и география",validation:"Нужна проверка",preview:"Ждёт согласования",safe_feed:"Автозагрузка готова",publication_processing:"Avito обрабатывает публикацию",publication_scheduled:"Avito заберёт фид по расписанию",publication_pending_review:"Ждём итоговый отчёт Avito",publication_completed:"Опубликовано полностью",publication_partial:"Опубликовано частично — есть ошибки",publication_failed:"Avito не опубликовал"};
                const statusLabel = reviewReady
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
            </details> : <div id="campaign-projects" style={{marginTop:space.md,borderTop:`1px solid ${color.line}`,paddingTop:space.sm}}>
              <div style={{...font.body,color:color.heading}}><b>У выбранного аккаунта пока нет кампаний</b></div>
              <div style={{...font.small,marginTop:4}}>Сейчас выбран: {selectedAccount?.name || account}. Кампании других аккаунтов здесь намеренно не показываются — переключить аккаунт можно в верхней панели.</div>
            </div>}
          </Card>
          <div className={`boris-campaign-workspace-grid${attention.length ? " has-attention" : ""}`} data-boris-g32b-page="campaign">
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

          <section style={{ marginTop: 26 }}>
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
          {showCatalogWarning && (
            <Alert kind="warning">
              Каталог не заведён: добавьте товары или загрузите материалы, чтобы BORIS подготовил объявления и фид.
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
