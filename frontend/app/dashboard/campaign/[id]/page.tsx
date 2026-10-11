"use client";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

/**
 * Ad Campaign Workspace — рабочее окно кампании.
 *
 * Здесь только то, что реально работает на бэкенде: список объявлений
 * кампании с серверными фильтрами и пагинацией, правка локального черновика
 * и перенос существующих объявлений аккаунта. Кнопок будущих этапов
 * (стратегия, генерация, визуалы, публикация) на экране НЕТ — они появятся,
 * когда появится их бэкенд.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useParams } from "next/navigation";
import { Page, PageHeader, Card, Button, EmptyState, Alert, Input } from "../../../ui";
import { color, font, space } from "../../../ui/tokens";
import { apiGet } from "../../../lib/api";
import AccountSelector from "../../../components/shell/AccountSelector";
import { apiPost as campaignPost, apiUpload } from "../../../lib/campaignApi";
import { readCampaignDraft, writeCampaignDraft, clearCampaignDraft } from "../../../lib/campaignDraft";

import {CampaignConflictDialog, cardDraft, draftContent, mergeCardDraft} from "../CampaignConflictDialog";

import CampaignCardHistory from "../CampaignCardHistory";
import useCampaignEditorJob, {editorDuration} from "../useCampaignEditorJob";

import SharedBorisSelect, {type BorisSelectProps} from "../BorisSelect";

const PAGE = 50;

const STATUS_RU: Record<string, string> = {
  all: "Все",
  imported: "Импортированы",
  analyzing: "Анализируются",
  needs_data: "Нужно исправить",
  draft: "BORIS работает",
  ready: "Готовы",
  awaiting_confirmation: "Ждут подтверждения",
  publishing: "Публикуются",
  published: "Опубликованы",
  publish_failed: "Ошибки",
  paused: "Остановлены",
};

type Item = {
  id: number; account_id: string; status: string; source: string; updated_at?: string;
  segment: string | null; hypothesis_id: string | null; geo: string | null; intent: string | null;
  external_item_id: string | null; payload: any;
  hero_media_id?: number | null;
  media?: Array<{media_id:number;role:string;position:number;url?:string;preview_url?:string}>;
};

function renderCampaignDescriptionPreview(raw: string) {
  const clean=String(raw||"").replace(/<[^>]+>/g," ");
  const headingRe=/^(Когда |Что предлагаем\.?$|Три класса|По стоимости\.?$|Что ещё|Как может выглядеть|Почему |Что входит\.?$|По бюджету\.?$|[А-ЯЁ][А-Яа-яЁё-]+(?:\s+[А-ЯЁ][А-Яа-яЁё-]+){0,2}\s+и\s+(?:область|край|республика)\.?$|Напишите нам в чат\.?$)/i;
  return clean.split(/\n+/).map((line,index)=>{
    const text=line.trim();
    if(!text) return null;
    const unique=text.startsWith("✦ ");
    const heading=headingRe.test(text);
    return <span key={index} className={unique?"boris-campaign-description-unique":heading?"boris-campaign-description-heading":""}>{unique?<span className="boris-campaign-description-icon">✦</span>:null}{unique?text.replace(/^✦\s*/,""):text}</span>;
  });
}

const AVITO_FIELD_LABELS:Record<string,string>={
  GoodsType:"Вид товара",GoodsSubType:"Тип товара",AdType:"Вид продажи",Condition:"Состояние",
  Availability:"Доступность",Color:"Основной цвет",ColorName:"Название цвета",Material:"Материал",
  KitchenType:"Тип кухни",DoorsSurface:"Поверхность фасада",DoorsMaterial:"Материал фасада",
  WorktopIncluded:"Столешница в комплекте",TabletopMaterial:"Материал столешницы",WorktopColor:"Цвет столешницы",
  KitchenShape:"Форма кухни",KitchenIslandIncluded:"Остров в комплекте",AppliancesIncluded:"Техника в комплекте",
  FurnitureAdditions:"Комплектация",Width:"Ширина",Height:"Высота",Depth:"Глубина",
  DepthByWorktop:"Глубина нижних шкафов",DepthByCorner:"Глубина кухни",PriceType:"Как указана цена",
  Delivery:"Доставка",ReturnPolicy:"Возврат",DeliverySubsidy:"Скидка на доставку",
  MultiItem:"Объединять похожие объявления",MultiName:"Название группы объявлений",
  NDS:"НДС",WholesaleType:"Оптовая продажа",WholesaleMinOrderType:"Минимальный оптовый заказ",
  WholesaleMinOrderCount:"Количество в минимальном заказе",WholesalePacking:"Фасовка",
  WholesalePackingCount:"Количество в упаковке",WholesaleMeasureUnit:"Единица измерения",
  WholesaleDiscountLadderType:"Оптовая скидка",DiscountLadderList:"Размер оптовой скидки",
  WeightForDelivery:"Вес для доставки",LengthForDelivery:"Длина для доставки",HeightForDelivery:"Высота для доставки",WidthForDelivery:"Ширина для доставки",
  TNVED:"Код ТН ВЭД",OKPD2:"Код ОКПД2",NoDocuments:"Документы не требуются",SquareSymbol:"Знак маркировки",CodeGTIN:"Код товара",
  Certificate:"Сертификат",Declaration:"Декларация",SGR:"Свидетельство регистрации",DateSGR:"Дата регистрации",UrlSGR:"Ссылка на свидетельство",
  Minselhoz:"Документ Минсельхоза",DateMinselhoz:"Дата документа",NumberRU:"Номер регистрационного удостоверения",DateRU:"Дата удостоверения",BarcodeAvailability:"Наличие штрихкода",
  TargetAudience:"Для кого предложение",Place:"Где оказываете услугу",PledgeAmount:"Размер залога",
};

const AVITO_ADVANCED_OPTIONAL_FIELDS=new Set([
  "WeightForDelivery","LengthForDelivery","HeightForDelivery","WidthForDelivery","DeliverySubsidy",
  "NDS","WholesaleType","WholesaleMinOrderType","WholesaleMinOrderCount","WholesalePacking",
  "WholesalePackingCount","WholesaleMeasureUnit","WholesaleDiscountLadderType","DiscountLadderList",
  "TNVED","OKPD2","NoDocuments","SquareSymbol","CodeGTIN","Certificate","Declaration","SGR","DateSGR",
  "UrlSGR","Minselhoz","DateMinselhoz","NumberRU","DateRU","BarcodeAvailability","MultiItem","MultiName"
]);

function avitoFieldLabel(field:any){
  const tag=String(field?.field||field?.tag||"").trim();
  const raw=String(field?.label||field?.name_ru||"").trim();
  if(raw && raw!==tag && /[А-Яа-яЁё]/.test(raw)) return raw;
  if(AVITO_FIELD_LABELS[tag]) return AVITO_FIELD_LABELS[tag];
  return "Характеристика товара";
}

function avitoDependencyText(raw:any, fields:any[]){
  let text=String(raw||"").trim();
  if(!text) return "";
  const labels=new Map<string,string>(Object.entries(AVITO_FIELD_LABELS));
  for(const f of fields||[]){
    const tag=String(f?.field||f?.tag||"").trim();
    if(tag) labels.set(tag,avitoFieldLabel(f));
  }
  for(const [tag,label] of labels){
    text=text.replace(new RegExp("\\b"+tag+"\\b","g"),"«"+label+"»");
  }
  return text.split("|").map((part)=>part.trim()).filter(Boolean).map((part)=>{
    return part
      .replace(/^Обязательно, если\s+/i,"Нужно заполнить, если ")
      .replace(/^Применимо, если\s+/i,"Показывается, если ")
      .replace(/^Не применимо, если\s+/i,"Не требуется, если ")
      .replace(/^Заполняется автоматически, если\s+/i,"BORIS заполнит автоматически, если ")
      .replace(/в поле\s+«([^»]+)»\s+указано значение\s+'([^']+)'/gi,"«$1» = «$2»")
      .replace(/в поле\s+«([^»]+)»\s+указаны значения:\s*/gi,"«$1» — одно из значений: ")
      .replace(/поле\s+«([^»]+)»\s+заполнено/gi,"«$1» заполнено");
  }).map(part=>part.replace(/[.。]+$/,"")+".").join(" ");
}

function avitoFieldNeedsInput(f:any){
  return !["AvitoId","AuctionPrice","AuctionPriceLastDate"].includes(String(f.field||"")) && (f.allowed_values||[]).length!==1;
}
function renderAvitoGroups(fields:any[], render:(f:any)=>React.ReactNode){
  const visible=fields.filter(avitoFieldNeedsInput);
  const advanced=visible.filter(f=>AVITO_ADVANCED_OPTIONAL_FIELDS.has(String(f.field)));
  const primary=visible.filter(f=>!AVITO_ADVANCED_OPTIONAL_FIELDS.has(String(f.field))&&(f.required||f.depends_on));
  const optional=visible.filter(f=>!AVITO_ADVANCED_OPTIONAL_FIELDS.has(String(f.field))&&!f.required&&!f.depends_on);
  const grid=(rows:any[])=><div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(min(210px,100%),1fr))",gap:8}}>{rows.map(render)}</div>;
  return <div style={{gridColumn:"1 / -1",minWidth:0,display:"grid",gap:12}}>
    {primary.length?<section><b>Основные параметры</b>{grid(primary)}</section>:null}
    {optional.length?<details><summary>Дополнительные параметры</summary>{grid(optional)}</details>:null}
    {advanced.length?<details><summary>Доставка, опт и документы</summary>{grid(advanced)}</details>:null}
  </div>;
}

function BorisSelect(props:BorisSelectProps){return <SharedBorisSelect {...props}/>;}


export default function CampaignWorkspace() {
  const params = useParams();
  const id = String((params as any)?.id || "");

  const [camp, setCamp] = useState<any>(null);
  const [accounts, setAccounts] = useState<string[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [revisionHistoryAvailable,setRevisionHistoryAvailable]=useState(false);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");
  const [account, setAccount] = useState("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [editorTab, setEditorTab] = useState<"ad"|"price"|"photos"|"avito"|"check"|"history">("ad");
  const openedFromQueryRef = useRef("");
  const [draft, setDraft] = useState<any>({});
  const [saving, setSaving] = useState(false);
  const draftScopeRef = useRef("");
  const draftValueRef = useRef(draft);
  const draftBaselineRef = useRef("");
  const [draftDirty, setDraftDirty] = useState(false);
  const [draftRecoveryStatus, setDraftRecoveryStatus] = useState("");
  useEffect(() => {
    draftValueRef.current = draft;
    if (open === null || !draftScopeRef.current || !Object.keys(draft).length) {
      setDraftDirty(false);
      return;
    }
    const dirty = draftContent(draft) !== draftContent(JSON.parse(draftBaselineRef.current || "{}"));
    setDraftDirty(dirty);
    const ok = writeCampaignDraft(draftScopeRef.current, draft);
    setDraftRecoveryStatus(
      !ok
        ? "Не удалось сохранить локальную копию. Не закрывайте страницу до сохранения в BORIS."
        : dirty
          ? "Есть несохранённые изменения. Нажмите «Сохранить», чтобы отправить их в BORIS."
          : "Изменений пока нет."
    );
  }, [draft, open]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!draftDirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [draftDirty]);
  const [importReport, setImportReport] = useState<any>(null);
  const [importing, setImporting] = useState(false);
  const [brief, setBrief] = useState("");
  const [targetQuantity, setTargetQuantity] = useState("");
  const [plan, setPlan] = useState<any>(null);
  const [validation, setValidation] = useState<any>(null);
  const [feedBusy, setFeedBusy] = useState("");
  const [borisRunMessage, setBorisRunMessage] = useState("");
  const [exportInfo, setExportInfo] = useState<any>(null);
  const [workflow, setWorkflow] = useState<any>(null);
  const [orderProgress, setOrderProgress] = useState<any>(null);
  const [questions, setQuestions] = useState<any[]>([]);
  const [answers, setAnswers] = useState<Record<string, any>>({});
  const [intake, setIntake] = useState<any>(null);
  const [intakeDraft, setIntakeDraft] = useState<Record<string, any>>({});
  const [itemParamDrafts, setItemParamDrafts] = useState<Record<number, Record<string, any>>>({});
  const [searchRecoByDirection, setSearchRecoByDirection] = useState<Record<string, any>>({});
const [searchCityByDirection, setSearchCityByDirection] = useState<Record<string,string>>({});
const [selectedRecoTitles, setSelectedRecoTitles] = useState<Record<string,string[]>>({});
  const [showIntakeModal, setShowIntakeModal] = useState(false);
  const [intakeStep, setIntakeStep] = useState(0);
  const [newDirection, setNewDirection] = useState("");
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false);
  const [categoryQuery, setCategoryQuery] = useState("");
  const [intakeSelectOpen, setIntakeSelectOpen] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [mediaPool, setMediaPool] = useState<any[]>([]);
  const [stockQuery, setStockQuery] = useState("");
  const [stockSource, setStockSource] = useState("pexels");
  const [stockCount, setStockCount] = useState(12);
  const [referenceLinks, setReferenceLinks] = useState("");
  const [referenceText, setReferenceText] = useState("");
  const [uniquenessParameters, setUniquenessParameters] = useState("");
  const [referenceMode, setReferenceMode] = useState("inspired");
  const [referenceFiles, setReferenceFiles] = useState<File[]>([]);
  const [referenceMediaIds, setReferenceMediaIds] = useState<number[]>([]);
  const [referenceSaved, setReferenceSaved] = useState("");
  const [referenceTextFileName, setReferenceTextFileName] = useState("");
  const [ownTextFlowReady, setOwnTextFlowReady] = useState(false);
  const [selectedItemIds, setSelectedItemIdsState] = useState<number[]>([]);
  const [fullTextItem, setFullTextItem] = useState<any>(null);
  const [fullTextDraft, setFullTextDraft] = useState("");

  const itemVersionsRef=useRef<Record<number,string>>({});
  const [cardConflict,setCardConflict]=useState<any>(null);
  const setSelectedItemIds=(ids:number[])=>{
    for(const itemId of ids) {
      if(!selectedItemIds.includes(itemId)) itemVersionsRef.current[itemId]=items.find(it=>it.id===itemId)?.updated_at||"";
    }
    setSelectedItemIdsState(ids);
  };
  async function reloadConflict(itemId:number) {
    const result=await apiGet("/api/campaign/"+id+"/item/"+itemId);
    if(result?.item) setCardConflict((old:any)=>old?{...old,latest:result.item}:old);
  }
  function rebaseConflict() {
    const latest=cardConflict?.latest;
    if(!latest)return;
    const itemId=Number(latest.id), fresh=cardDraft(latest);
    itemVersionsRef.current[itemId]=latest.updated_at||"";
    if(open===itemId){
      let baseline:any={};
      try{baseline=JSON.parse(draftBaselineRef.current||"{}");}catch{}
      const merged=mergeCardDraft(baseline,draftValueRef.current,fresh);
      draftBaselineRef.current=JSON.stringify(fresh);
      draftValueRef.current=merged;setDraft(merged);
    }
    if(Number(fullTextItem?.id)===itemId) setFullTextItem((old:any)=>({...old,updated_at:latest.updated_at}));
    setItems(previous=>previous.map(it=>it.id===itemId?latest:it));
    setCardConflict(null);setErr("");
    setDraftRecoveryStatus("Основа обновлена. Ваши правки сохранены в форме — проверьте их перед сохранением.");
  }
  async function apiPost(url:string,body:any):Promise<any> {
    const match=url.match(/\/api\/campaign\/\d+\/(?:item\/(\d+)$|workflow\/(mutate|autopilot-mutate|price-confirm|category\/\d+|bulk-transform|photo-stack|banners|auto-media|enhance|texts)$)/);
    const guarded=!!match && !(match[2]==="bulk-transform"&&(body.preview_only!==false||!body.confirmed));
    const itemIds:number[]=match?.[1]?[Number(match[1])]:match?.[2]?.startsWith("category/")?[Number(url.split("/").pop())]:(body?.item_ids||[]);
    let payload=body;
    if(guarded){
      const expected:Record<string,string>={};
      for(const itemId of itemIds){
        expected[String(itemId)]=itemVersionsRef.current[itemId] ??
          (Number(fullTextItem?.id)===itemId?fullTextItem.updated_at:undefined) ??
          items.find(it=>it.id===itemId)?.updated_at ?? "";
      }
      payload={...body,expected_updated_at:body.expected_updated_at??expected};
    }
    try{
      let result=await campaignPost(url,payload);
      if(result?.persistent && result?.job?.kind==="campaign_editor") result=await trackEditorJob(result.job);
      for(const [key,version] of Object.entries(result?.item_versions||{})){
        const itemId=Number(key), token=String(version);
        itemVersionsRef.current[itemId]=token;
        if(open===itemId){draftValueRef.current={...draftValueRef.current,__updated_at:token};setDraft((old:any)=>({...old,__updated_at:token}));}
        if(Number(fullTextItem?.id)===itemId)setFullTextItem((old:any)=>old?{...old,updated_at:token}:old);
      }
      return result;
    }catch(error:any){
      if(error?.code==="CARD_VERSION_CONFLICT"){
        const itemId=Number(error.raw?.item_id||itemIds[0]||open||0);
        const item=items.find(it=>it.id===itemId);
        const local=open===itemId?{...draftValueRef.current}:Number(fullTextItem?.id)===itemId?{...cardDraft(item),description:fullTextDraft}:{...cardDraft(item),...(body.patch||{})};
        setCardConflict({itemId,local,latest:null});
        if(itemId)await reloadConflict(itemId).catch(()=>null);
      }
      throw error;
    }
  }

  const [campaignPhotoFiles, setCampaignPhotoFiles] = useState<File[]>([]);
  const [randomizePhotoStack, setRandomizePhotoStack] = useState(true);
  const [photosPerAd, setPhotosPerAd] = useState(6);
  const [enhancePrompt, setEnhancePrompt] = useState("");
  const [itemTextPrompts, setItemTextPrompts] = useState<Record<number,string>>({});
  const [competitorAudit, setCompetitorAudit] = useState<any>(null);
  const [competitorAuditOpen, setCompetitorAuditOpen] = useState(false);
  const [photoAnglesStatus, setPhotoAnglesStatus] = useState<any>(null);
  const [competitorQuery, setCompetitorQuery] = useState("");
  const [competitorCity, setCompetitorCity] = useState("");
  const [competitorCount, setCompetitorCount] = useState(10);
  const [competitorApply, setCompetitorApply] = useState(true);
  const photoStackInputRef = useRef<HTMLInputElement | null>(null);
  const enhancePromptRef = useRef<HTMLTextAreaElement | null>(null);
  // One user intent = one paid-generation identity. Keep it across an ambiguous
  // network failure; clear only after apiPost actually received an HTTP response.
  const bannerGenerationKeyRef = useRef("");
  const bannerGenerationFingerprintRef = useRef("");
  const textGenerationKeyRef = useRef("");
  const regenerateTextKeyRef = useRef<Record<number, string>>({});
  const [bulkPrice, setBulkPrice] = useState("");
  const [bulkCity, setBulkCity] = useState("");
  const [bulkDirection, setBulkDirection] = useState("");
  const [bulkTitleTemplate, setBulkTitleTemplate] = useState("");
  const [bulkDescriptionTemplate, setBulkDescriptionTemplate] = useState("");
  const [bulkDescriptionMode, setBulkDescriptionMode] = useState<"append"|"prepend"|"replace">("append");
  const [bulkVariablesText, setBulkVariablesText] = useState("");
  const [bulkTemplateId, setBulkTemplateId] = useState("");
  const [bulkCategoryContract, setBulkCategoryContract] = useState<any>(null);
  const [bulkCategoryValues, setBulkCategoryValues] = useState<Record<string,string>>({});
  const [bulkTransformPreview, setBulkTransformPreview] = useState<any>(null);
  const bulkCategoryCandidates = useMemo(() => {
    const map=new Map<string,{template_id:string;path:string}>();
    for(const d of (intake?.directions||[])){
      for(const c of (d?.candidates||[])){
        const tid=String(c?.template_id||"").trim();
        if(tid && !map.has(tid)) map.set(tid,{template_id:tid,path:String(c?.path||c?.leaf_name||tid)});
      }
      const tid=String(d?.template_id||"").trim();
      if(tid && !map.has(tid)) map.set(tid,{template_id:tid,path:String(d?.category_path||tid)});
    }
    for(const it of items){
      const tid=String(it?.payload?.template_id||"").trim();
      if(tid && !map.has(tid)) map.set(tid,{template_id:tid,path:String(it?.payload?.category_path||it?.payload?.category||tid)});
    }
    return Array.from(map.values());
  },[intake,items]);
  const [imageErrors, setImageErrors] = useState<Record<number, boolean>>({});
  const [returnComment, setReturnComment] = useState("");
  const [activation, setActivation] = useState<any>(null);
  const [showActivationConfirm, setShowActivationConfirm] = useState(false);
  const [feedControl, setFeedControl] = useState<any>(null);
  const [avitoPreflight, setAvitoPreflight] = useState<any>(null);
  const [ownerCostSummary, setOwnerCostSummary] = useState<any>(null);
  const [guardianJobs, setGuardianJobs] = useState<any[]>([]);
  const guardianSignalRef = useRef("");
  const [progressClockNow, setProgressClockNow] = useState(()=>Date.now());
  useEffect(()=>{
    const timer=window.setInterval(()=>setProgressClockNow(Date.now()),1000);
    return()=>window.clearInterval(timer);
  },[]);
  const [feedDetailsOpen, setFeedDetailsOpen] = useState(false);
  const [lightbox, setLightbox] = useState<{images:string[]; index:number; title:string} | null>(null);
  const [lightboxLoaded, setLightboxLoaded] = useState(false);
  const [lightboxError, setLightboxError] = useState(false);
  const [lightboxVisibleSrc, setLightboxVisibleSrc] = useState("");
  const lightboxDialogRef = useRef<HTMLDivElement | null>(null);
  const lightboxTriggerRef = useRef<HTMLElement | null>(null);
  const lightboxLoadTokenRef = useRef(0);

  const STEP_RU: Record<string,string> = {
    brief: "Расскажите, что нужно", questions: "Нужно уточнить несколько данных",
    structure: "Проверьте план", texts: "Подготовка текстов", media: "Фотографии", media_shards: "Фотографии", cost_anomaly: "Контроль стоимости",
    category: "Категории и характеристики", settings: "Цена и география",
    validation: "Проверка", preview: "Согласование", safe_feed: "Автозагрузка готова",
  };

  const closeLightbox = useCallback(() => {
    lightboxLoadTokenRef.current += 1;
    setLightbox(null);
    setLightboxVisibleSrc("");
    setLightboxLoaded(false);
    setLightboxError(false);
    requestAnimationFrame(() => lightboxTriggerRef.current?.focus());
  }, []);

  const openLightbox = useCallback((images:any, index:number, title:string, trigger:HTMLElement) => {
    const clean = Array.isArray(images) ? images.filter((x:any): x is string => typeof x === "string" && Boolean(x)) : [];
    if (!clean.length) return;
    const safeIndex=Math.max(0, Math.min(index, clean.length - 1));
    lightboxTriggerRef.current = trigger;
    lightboxLoadTokenRef.current += 1;
    setLightboxError(false);
    // The clicked card/thumbnail image is already painted by the browser.
    // Keep that exact source visible immediately instead of flashing a black loader.
    setLightboxVisibleSrc(clean[safeIndex]);
    setLightboxLoaded(true);
    setLightbox({ images: clean, index: safeIndex, title: title || "Фото объявления" });
  }, []);

  const moveLightbox = useCallback((delta:number) => {
    setLightbox((current) => {
      if (!current || !current.images.length) return current;
      const nextIndex=(current.index + delta + current.images.length) % current.images.length;
      const nextSrc=current.images[nextIndex];
      const token=++lightboxLoadTokenRef.current;
      setLightboxError(false);
      setLightboxLoaded(false);
      if (typeof window !== "undefined") {
        const probe=new window.Image();
        probe.onload=()=>{
          if (lightboxLoadTokenRef.current!==token) return;
          setLightboxVisibleSrc(nextSrc);
          setLightboxLoaded(true);
        };
        probe.onerror=()=>{
          if (lightboxLoadTokenRef.current!==token) return;
          setLightboxError(true);
          setLightboxLoaded(true);
        };
        probe.src=nextSrc;
        if (probe.complete && probe.naturalWidth>0) {
          setLightboxVisibleSrc(nextSrc);
          setLightboxLoaded(true);
        }
      } else {
        setLightboxVisibleSrc(nextSrc);
        setLightboxLoaded(true);
      }
      return { ...current, index: nextIndex };
    });
  }, []);

  useEffect(() => {
    if (!showIntakeModal) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previousOverflow; };
  }, [showIntakeModal]);

  const lightboxOpen = Boolean(lightbox);
  useEffect(() => {
    if (!lightboxOpen) return;
    const previousOverflow = document.body.style.overflow;
    const previousPaddingRight = document.body.style.paddingRight;
    const scrollbarWidth = Math.max(0, window.innerWidth - document.documentElement.clientWidth);
    if (scrollbarWidth > 0) document.body.style.paddingRight = scrollbarWidth + "px";
    document.body.style.overflow = "hidden";
    requestAnimationFrame(() => {
      const close = lightboxDialogRef.current?.querySelector<HTMLElement>("[data-lightbox-close]");
      close?.focus();
    });
    const onKeyDown = (event:KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeLightbox(); return; }
      if (event.key === "ArrowLeft") { event.preventDefault(); moveLightbox(-1); return; }
      if (event.key === "ArrowRight") { event.preventDefault(); moveLightbox(1); return; }
      if (event.key !== "Tab") return;
      const focusable = Array.from(lightboxDialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled]), [href], [tabindex]:not([tabindex='-1'])") || []);
      if (!focusable.length) { event.preventDefault(); return; }
      const first = focusable[0], last = focusable[focusable.length - 1], active = document.activeElement;
      if (event.shiftKey && active === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      document.body.style.paddingRight = previousPaddingRight;
    };
  }, [lightboxOpen, closeLightbox, moveLightbox]);

  // поиск не дёргает сервер на каждую букву
  useEffect(() => {
    const t = setTimeout(() => { setQuery(q); setOffset(0); }, 400);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    let alive=true;
    if (!bulkTemplateId) {
      setBulkCategoryContract(null);
      setBulkCategoryValues({});
      return () => { alive=false; };
    }
    apiGet("/api/campaign/category-contract?template_id="+encodeURIComponent(bulkTemplateId))
      .then((data:any)=>{
        if(!alive) return;
        setBulkCategoryContract(data?.status==="ok"?data:null);
        setBulkCategoryValues({});
      })
      .catch(()=>{ if(alive){ setBulkCategoryContract(null); setBulkCategoryValues({}); } });
    return () => { alive=false; };
  }, [bulkTemplateId]);

  const loadCampaign = useCallback(async () => {
    const r = await apiGet("/api/campaign/" + id);
    setCamp(r?.campaign || null);
    setAccounts(r?.accounts || []);
  }, [id]);

  const loadItems = useCallback(async () => {
    const p = new URLSearchParams();
    if (status) p.set("status", status);
    if (account) p.set("account_id", account);
    if (query) p.set("search", query);
    p.set("limit", String(PAGE));
    p.set("offset", String(offset));
    const r = await apiGet("/api/campaign/" + id + "/items?" + p.toString());
    setItems(r?.items || []);
    setRevisionHistoryAvailable(r?.card_revision_contract===1);
    setTotal(r?.total || 0);
    setCounts(r?.counts || {});
  }, [id, status, account, query, offset]);

  useEffect(() => {
    const acc = account || camp?.default_account_id || accounts[0] || "";
    if (!acc || !id) {
      setOwnerCostSummary(null);
      return;
    }
    let alive = true;
    apiGet("/api/accounts/campaign-costs?account_id=" + encodeURIComponent(acc) + "&campaign_id=" + encodeURIComponent(id))
      .then((data:any) => {
        if (alive) setOwnerCostSummary(data?.status === "ok" ? data : null);
      })
      .catch(() => {
        if (alive) setOwnerCostSummary(null);
      });
    return () => { alive = false; };
  }, [id, account, camp?.default_account_id, accounts]);

  const loadIntake = useCallback(async (autoOpen:boolean = true) => {
    const r = await apiGet("/api/campaign/" + id + "/workflow/intake");
    if (r?.status === "ok") {
      setIntake(r);
      const next:Record<string,any> = {};
      for (const d of (r.directions || [])) next[d.direction] = { template_id:d.template_id || "", mode:d.mode || "shared", answers:d.answers || {} };
      setIntakeDraft(next);
      if ((r.directions || []).length && !r.all_complete) {
        const firstPending=Math.max(0,(r.directions || []).findIndex((d:any)=>!d.complete));
        setIntakeStep(firstPending >= 0 ? firstPending : 0);
        if (autoOpen) setShowIntakeModal(true);
      } else if (r.all_complete) {
        setShowIntakeModal(false);
      }
    }
    return r;
  }, [id]);

  const loadSearchRecommendations = useCallback(async (direction:string, refresh:boolean=false, city:string="") => {
    if (!direction) return null;
    if (!refresh && searchRecoByDirection[direction]) return searchRecoByDirection[direction];
    setSearchRecoByDirection((p:any)=>({...p,[direction]:{...(p[direction]||{}),loading:true}}));
    try {
      const r=await apiGet("/api/campaign/"+id+"/workflow/search_recommendations?direction="+encodeURIComponent(direction)+(city?"&city="+encodeURIComponent(city):"")+(refresh?"&refresh=true":""));
      setSearchRecoByDirection((p:any)=>({...p,[direction]:{...r,loading:false}})); return r;
    } catch(e:any) {
      setSearchRecoByDirection((p:any)=>({...p,[direction]:{loading:false,source_status:"unavailable",message:e?.message||"Не удалось проверить Avito",title_variants:[]}})); return null;
    }
  }, [id, searchRecoByDirection]);

  useEffect(() => {
    const direction=String(intake?.directions?.[intakeStep]?.direction || "");
    if (showIntakeModal && direction && !searchRecoByDirection[direction]) void loadSearchRecommendations(direction,false);
  }, [showIntakeModal, intakeStep, intake, searchRecoByDirection, loadSearchRecommendations]);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true); setErr("");
      try {
        await Promise.all([loadCampaign(), loadItems()]);
        if (alive) {
          const [w] = await Promise.all([
            apiGet("/api/campaign/" + id + "/workflow"),
            loadIntake(),
          ]);
          if (!alive) return;
          setWorkflow(w?.workflow || null);
          setOrderProgress(w?.order || w?.workflow?.order_contract || null);
          setQuestions(w?.questions || []);
          if (w?.workflow?.brief) setBrief(w.workflow.brief);
          const refs=w?.workflow?.references || {};
          setReferenceLinks(Array.isArray(refs.links)?refs.links.join("\n"):"");
          setReferenceText(String(refs.text || "")); setUniquenessParameters(String(refs.uniqueness_parameters || ""));
          setReferenceMode(String(refs.mode || "inspired"));
          setOwnTextFlowReady(String(refs.mode || "") === "preserve_text" && Boolean(String(refs.text || "").trim()));
          setReferenceMediaIds(Array.isArray(refs.media_ids)?refs.media_ids.map((x:any)=>Number(x)).filter(Boolean):[]);
          // The editor and intake need the campaign/workflow, not the slower
          // full validation and media preview. Keep those requests independent
          // of the first usable screen, with their existing loading states.
          setLoading(false);
          // Preview и validation нужны клиенту не только в финальном статусе:
          // как только карточки материализованы, Campaign сразу показывает
          // реальный визуальный результат в reference-workspace.
          const [vr, pr] = await Promise.all([
            apiGet("/api/campaign/" + id + "/validation").catch(()=>null),
            apiGet("/api/campaign/" + id + "/workflow/preview").catch(()=>null),
          ]);
          setValidation(vr || null);
          setPreview(pr || null);
          if (w?.workflow?.ready_for_owner_approval) {
            const fc = await apiGet("/api/campaign/" + id + "/feed_control").catch(()=>null);
            setFeedControl(fc?.status === "ok" ? fc : null);
          }
        }
      } catch (e: any) {
        if (alive) setErr(e?.message || "Не удалось загрузить кампанию");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [loadCampaign, loadItems, loadIntake]);

  const startEdit = (it: Item) => {
    setEditorTab("ad");
    setOpen(open === it.id ? null : it.id);
    draftScopeRef.current = `item:${it.account_id}:${id}:${it.id}`;
    const canonical = cardDraft(it);
    draftBaselineRef.current = JSON.stringify(canonical);
    const recovered = readCampaignDraft<any>(draftScopeRef.current);
    const next = recovered && typeof recovered === "object" && !Array.isArray(recovered) ? {...recovered,__updated_at:recovered.__updated_at||""} : canonical;
    itemVersionsRef.current[it.id]=next.__updated_at;
    draftValueRef.current=next;
    setDraft(next);
  };

  useEffect(() => {
    if(typeof window==="undefined") return;
    let cancelled=false;
    const query=new URLSearchParams(window.location.search);
    const requestedId=Number(query.get("edit")||0);
    if(!requestedId) return;
    const requestedTab=String(query.get("tab")||"ad");
    const allowedTabs=new Set(["ad","price","photos","avito","check","history"]);
    const key=id+":"+requestedId+":"+requestedTab;
    if(openedFromQueryRef.current===key) return;
    const openRequested=async()=>{
      let item=items.find((x:any)=>Number(x.id)===requestedId);
      if(!item){
        try{
          const exact=await apiGet("/api/campaign/"+id+"/item/"+requestedId);
          if(exact?.item){
            item=exact.item as Item;
            if(!cancelled) setItems((prev)=>[item as Item,...prev.filter((x)=>Number(x.id)!==requestedId)]);
          }
        }catch{}
      }
      if(cancelled || !item) return;
      openedFromQueryRef.current=key;
      startEdit(item);
      if(allowedTabs.has(requestedTab)) setEditorTab(requestedTab as any);
      window.setTimeout(()=>document.getElementById("item-"+requestedId)?.scrollIntoView({block:"nearest"}),50);
    };
    void openRequested();
    return ()=>{cancelled=true;};
  }, [items,id]);

  const cancelEdit = () => {
    const scope = draftScopeRef.current;
    if (scope) clearCampaignDraft(scope);
    draftScopeRef.current = "";
    draftBaselineRef.current = "";
    setDraft({});
    setDraftDirty(false);
    setDraftRecoveryStatus("Изменения отменены.");
    setOpen(null);
  };

  const save = async (it: Item) => {
    const savedScope = draftScopeRef.current;
    const savedDraft = draftContent(draft);
    setSaving(true); setErr("");
    try {
      let paramsObj:any = {};
      try { paramsObj = JSON.parse(draft.params || "{}"); } catch { throw new Error("Параметры категории должны быть валидным JSON"); }
      const patch: any = {};
      const currentTitle = String(it.payload?.title ?? "");
      const currentDescription = String(it.payload?.description ?? "");
      const currentCity = String(it.payload?.city ?? it.geo ?? "");
      const currentAddress = String(it.payload?.address ?? "");
      if (String(draft.title ?? "") !== currentTitle) patch.title = draft.title;
      if (String(draft.description ?? "") !== currentDescription) patch.description = draft.description;
      if (String(draft.city ?? "") !== currentCity) patch.city = draft.city;
      if (String(draft.address ?? "") !== currentAddress) patch.address = draft.address;
      if (Object.keys(patch).length) {
        await apiPost("/api/campaign/" + id + "/workflow/mutate", { item_ids: [it.id], patch });
      }
      const oldPrice = String(it.payload?.price ?? "").trim();
      const newPrice = String(draft.price ?? "").trim();
      if (newPrice !== oldPrice || (newPrice && !it.payload?.price_verified && !it.payload?.verified_price)) {
        if (!newPrice) {
          await apiPost("/api/campaign/" + id + "/workflow/price-confirm", { item_ids: [it.id], clear: true });
        } else {
          const numericPrice = Number(newPrice);
          if (!Number.isFinite(numericPrice) || numericPrice <= 0) throw new Error("Укажите цену больше нуля");
          await apiPost("/api/campaign/" + id + "/workflow/price-confirm", { item_ids: [it.id], price: numericPrice });
        }
      }
      const currentTemplateId = String(it.payload?.template_id ?? "");
      const currentParams = JSON.stringify(it.payload?.params || {});
      const nextParams = JSON.stringify(paramsObj || {});
      if (draft.template_id && (String(draft.template_id) !== currentTemplateId || nextParams !== currentParams)) {
        await apiPost("/api/campaign/" + id + "/workflow/category/" + it.id, { template_id: draft.template_id, params: paramsObj });
      }
      await loadItems(); await loadWorkflow();
      if (draftScopeRef.current === savedScope && draftContent(draftValueRef.current) === savedDraft) {
        clearCampaignDraft(savedScope);
        draftScopeRef.current = "";
        setOpen(null);
      }
    } catch (e: any) {
      setErr(e?.message || "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  };

  const runImport = async (dry: boolean) => {
    const acc = account || camp?.default_account_id || accounts[0] || "";
    if (!acc) { setErr("Не выбран аккаунт"); return; }
    setImporting(true); setErr("");
    try {
      const r = await apiPost("/api/campaign/" + id + "/import_existing",
                              { account_id: acc, dry_run: dry });
      setImportReport({ ...(r?.report || {}), _dry: dry });
      if (!dry) { await loadItems(); await loadCampaign(); }
    } catch (e: any) {
      setErr(e?.message || "Перенос не удался");
    } finally {
      setImporting(false);
    }
  };

  const loadWorkflow = useCallback(async () => {
    const r = await apiGet("/api/campaign/" + id + "/workflow");
    setWorkflow(r?.workflow || null);
    setOrderProgress(r?.order || r?.workflow?.order_contract || null);
    setQuestions(r?.questions || []);
    if (r?.workflow?.brief) setBrief(r.workflow.brief);
    const refs=r?.workflow?.references || {};
    setReferenceLinks(Array.isArray(refs.links)?refs.links.join("\n"):"");
    setReferenceText(String(refs.text || "")); setUniquenessParameters(String(refs.uniqueness_parameters || ""));
    setReferenceMode(String(refs.mode || "inspired"));
    setOwnTextFlowReady(String(refs.mode || "") === "preserve_text" && Boolean(String(refs.text || "").trim()));
    setReferenceMediaIds(Array.isArray(refs.media_ids)?refs.media_ids.map((x:any)=>Number(x)).filter(Boolean):[]);
    return r;
  }, [id]);

  const loadGuardianStatus = useCallback(async () => {
    try {
      const r=await apiGet("/api/campaign/"+id+"/guardian/status");
      const jobs=Array.isArray(r?.jobs)?r.jobs:[];
      setGuardianJobs(jobs);
      const latest=jobs.find((j:any)=>["queued","running","validating","awaiting_confirmation"].includes(String(j?.status||""))) || jobs[0];
      if (!latest) return r;
      const signal=`${latest.id}:${latest.status}:${latest.stage}:${latest.done}:${latest.total}:${latest.last_progress_at||""}:${latest.attempts||0}`;
      if (guardianSignalRef.current===signal) return r;
      guardianSignalRef.current=signal;
      const stageText:Record<string,string>={queued:"BORIS продолжает автоматически. Готовые изменения сохраняются по мере обработки.",preflight:"BORIS проверяет задачу…",materialize:"BORIS создаёт объявления…",texts:"BORIS готовит тексты…",media:"BORIS подбирает и проверяет фотографии…",media_retry:"BORIS заменяет неподходящие фотографии и продолжает автоматически…",media_source_wait:"Источник фотографий временно недоступен. BORIS ждёт резервный медиапул и продолжит сам.",banners:"BORIS создаёт и проверяет баннеры…",billing_pending:"BORIS сверяет тарифный учёт и продолжит без повторной генерации.",billing_limit:"Подготовка остановлена на тарифном лимите. Уже готовые объявления сохранены.",banner_confirmation_required:"Для следующих баннеров требуется подтверждение платной генерации.",banner_cost_cap:"Генерация баннеров остановлена защитой стоимости.",internal_validation:"BORIS проверяет объявления…",official_avito_validation:"BORIS проверяет формат и обязательные поля Avito…",ready_for_review:"Кампания готова и проверена Avito. Осталось проверить результат и подтвердить размещение.",enhance_texts:"BORIS усиливает тексты по вашему промту…",enhance_done:"Тексты усилены. BORIS перепроверяет объявления и готовность Avito.",approval:"BORIS фиксирует согласование…",revalidate:"BORIS повторно проверяет объявления перед отправкой…",sending:"BORIS передаёт объявления в Avito…",awaiting_avito_result:"Avito обрабатывает объявления. BORIS ждёт итоговый отчёт.",delivered:"Все объявления опубликованы.",publication_partial:"Avito опубликовал часть объявлений. BORIS показывает причины по остальным.",publication_failed:"Avito не опубликовал объявления. BORIS показывает причины из итогового отчёта.",publication_pending_review:"Avito ещё не вернул итоговый отчёт. BORIS не показывает ложный успех.",needs_avito_connection:"Подключите Avito к этому аккаунту — после подключения отправка продолжится.",needs_autoload_access:"В этом аккаунте Avito недоступна автозагрузка. Включите её/нужный тариф — после этого отправка продолжится автоматически.",needs_input:"Нужно уточнить только обязательные данные Avito."};
      setBorisRunMessage(latest.message || stageText[latest.stage] || (latest.status==="running"?"BORIS продолжает автоматически. Готовые изменения сохраняются по мере обработки.":""));
      // Long Campaign stages are chunked on the server. Refresh business truth
      // only when the durable job checkpoint changes; otherwise the page can look
      // frozen at 4/8 while dozens of cards are actually finishing in the worker.
      const latestAutoRunning=["queued","running","validating"].includes(String(latest.status||""))
        || (String(latest.status||"")==="awaiting_confirmation" && latest.requires_owner_action===false);
      if (latestAutoRunning) {
        const wf=await apiGet("/api/campaign/"+id+"/workflow").catch(()=>null);
        if(wf){setWorkflow(wf?.workflow||null);setOrderProgress(wf?.order||wf?.workflow?.order_contract||null);setQuestions(wf?.questions||[]);}
        if (["materialize","texts","media","media_retry","banners","internal_validation","enhance_texts"].includes(String(latest.stage||""))) {
          await loadItems();
        }
        if (latest.stage==="enhance_texts" && Number(latest.done||0)>0) {
          const pv=await apiGet("/api/campaign/"+id+"/workflow/preview").catch(()=>null);
          if(pv) setPreview(pv);
        }
      }
      if (latest.status==="failed") {
        setErr("");
        if (latest.stage==="publication_failed") setBorisRunMessage(latest.message || stageText.publication_failed);
        else {
          // Never show an endless "still working" state after a terminal failure.
          // Prepared cards remain durable; an explicit continuation can safely resume
          // the same campaign/job identity without making the owner guess.
          setBorisRunMessage(latest.message || "Этап подготовки остановился. Уже готовые объявления сохранены. Нажмите «Продолжить», чтобы BORIS продолжил с текущего этапа.");
        }
      }
      if (latest.status==="awaiting_confirmation" && latest.stage==="needs_input") {
        setErr("");
        await loadIntake(true);
      }
      if (latest.status==="awaiting_confirmation" || latest.status==="completed" || latest.kind==="campaign_deliver") {
        const [wf,val,pv,fc]=await Promise.all([
          apiGet("/api/campaign/"+id+"/workflow").catch(()=>null),
          apiGet("/api/campaign/"+id+"/validation").catch(()=>null),
          apiGet("/api/campaign/"+id+"/workflow/preview").catch(()=>null),
          apiGet("/api/campaign/"+id+"/feed_control").catch(()=>null),
        ]);
        if(wf){setWorkflow(wf?.workflow||null);setOrderProgress(wf?.order||wf?.workflow?.order_contract||null);setQuestions(wf?.questions||[]);}
        setValidation(val||null);setPreview(pv||null);if(fc?.status==="ok")setFeedControl(fc);
        if (latest.kind==="campaign_deliver" && fc?.account_id) {
          const act=await apiGet("/api/campaign/"+id+"/owner_activation?account_id="+encodeURIComponent(fc.account_id)).catch(()=>null);
          if(act?.status==="ok") setActivation(act);
        }
        await loadItems();
      }
      return r;
    } catch { return null; }
  },[id,loadIntake,loadItems]);

  const {editorJob,trackEditorJob}=useCampaignEditorJob(String(id),()=>{void loadItems();void loadWorkflow();});

  useEffect(()=>{
    let alive=true;
    void loadGuardianStatus();
    const timer=window.setInterval(()=>{if(alive)void loadGuardianStatus();},3000);
    return()=>{alive=false;window.clearInterval(timer);};
  },[loadGuardianStatus]);

  const makePlan = async () => {
    setFeedBusy("plan"); setErr(""); setExportInfo(null);
    try {
      const r = await apiPost("/api/campaign/" + id + "/workflow/brief", { brief, variants: workflow?.variants || 1, quantity: Math.max(0, Math.min(1000, Number(targetQuantity || 0) || 0)) });
      setWorkflow(r?.workflow || null); setQuestions(r?.questions || []); setPlan(r?.workflow || null);
      const intakeState=await loadIntake(true);
      if ((intakeState?.directions || []).length && !intakeState?.all_complete) setShowIntakeModal(true);
    } catch (e: any) { setErr(e?.message || "Не удалось разобрать ТЗ"); }
    finally { setFeedBusy(""); }
  };
  const saveAnswers = async () => {
    setFeedBusy("answers"); setErr("");
    try {
      const r = await apiPost("/api/campaign/" + id + "/workflow/answers", { answers });
      const remaining=r?.questions || [];
      setWorkflow(r?.workflow || null); setQuestions(remaining); setAnswers({}); await loadIntake();
      if (!remaining.length) setTimeout(()=>{ void continueBorisPreparation(); },0);
    } catch (e: any) { setErr(e?.message || "Не удалось сохранить уточнения"); }
    finally { setFeedBusy(""); }
  };
  const setIntakeValue = (direction:string, field:string, patch:any) => {
    setIntakeDraft((prev:any)=>{
      const d={...(prev[direction]||{})}; d.answers={...(d.answers||{}),[field]:{...((d.answers||{})[field]||{mode:"exact"}),...patch}};
      return {...prev,[direction]:d};
    });
  };
  const saveDirectionIntake = async (direction:string) => {
    const d=intakeDraft[direction] || {}; setFeedBusy("intake-"+direction); setErr("");
    try {
      const r=await apiPost("/api/campaign/"+id+"/workflow/intake",{direction,template_id:d.template_id,mode:d.mode||"shared",answers:d.answers||{},confirmed:true});
      setIntake(r); await loadWorkflow(); await loadItems();
      if (r?.all_complete) {
        setShowIntakeModal(false);
        setBorisRunMessage("Спасибо. BORIS получил обязательные данные и продолжает подготовку сам…");
        setTimeout(()=>{ void continueBorisPreparation(); },0);
      } else {
        const dirs=r?.directions || [];
        const next=Math.max(0,dirs.findIndex((x:any)=>!x.complete));
        setIntakeStep(next >= 0 ? next : 0);
        setShowIntakeModal(true);
      }
      await loadIntake(false);
    } catch(e:any){setErr(e?.message||"Не удалось сохранить параметры направления");} finally{setFeedBusy("");}
  };
  const saveAllDirectionIntake = async () => {
    const dirs=intake?.directions || [];
    if (!dirs.length) return;
    setFeedBusy("intake-all"); setErr("");
    try {
      let last:any=null;
      for (const dir of dirs) {
        const d=intakeDraft[dir.direction] || {template_id:dir.template_id,mode:"shared",answers:dir.answers||{}};
        last=await apiPost("/api/campaign/"+id+"/workflow/intake",{
          direction:dir.direction,
          template_id:d.template_id || dir.template_id,
          mode:"shared",
          answers:d.answers || {},
          confirmed:true,
        });
      }
      if (last?.status==="ok") setIntake(last);
      const refreshed=await loadIntake(false);
      await loadWorkflow(); await loadItems();
      if (refreshed?.all_complete) {
        setShowIntakeModal(false);
        setBorisRunMessage("Спасибо. BORIS получил недостающие данные и продолжает подготовку сам…");
        setTimeout(()=>{ void continueBorisPreparation(); },0);
      } else {
        const pending=refreshed?.directions || [];
        const next=Math.max(0,pending.findIndex((x:any)=>!x.complete));
        setIntakeStep(next>=0?next:0);
        setShowIntakeModal(true);
        setErr("Заполните отмеченные обязательные поля — остальное BORIS сделает сам.");
      }
    } catch(e:any){ setErr(e?.message || "Не удалось сохранить уточнения"); }
    finally { setFeedBusy(""); }
  };
  const chooseDirectionCategory = async (direction:string, template_id:string) => {
    const d={...(intakeDraft[direction]||{}),template_id,answers:{}}; setIntakeDraft((p:any)=>({...p,[direction]:d})); setFeedBusy("intake-category-"+direction); setErr("");
    try { const r=await apiPost("/api/campaign/"+id+"/workflow/intake",{direction,template_id,mode:d.mode||"shared",answers:{},confirmed:false}); setIntake(r); await loadIntake(); }
    catch(e:any){setErr(e?.message||"Не удалось загрузить параметры категории");} finally{setFeedBusy("");}
  };
  const addDirection = async () => {
    const value=newDirection.trim();
    if (!value) return;
    setFeedBusy("add-direction"); setErr("");
    try {
      const r=await apiPost("/api/campaign/"+id+"/workflow/direction",{direction:value});
      setWorkflow(r?.workflow || null);
      setNewDirection("");
      await loadIntake(false);
      const dirs=r?.intake?.directions || [];
      const idx=Math.max(0,dirs.findIndex((x:any)=>String(x.direction||"").toLowerCase()===value.toLowerCase()));
      setIntakeStep(idx); setShowIntakeModal(true);
    } catch(e:any){setErr(e?.message||"Не удалось добавить направление");} finally{setFeedBusy("");}
  };
  const savePerItemParams = async (it:Item, direction:any) => {
    const patch=itemParamDrafts[it.id] || {}; const paramsObj={...(it.payload?.params||{}),...patch}; setFeedBusy("item-params-"+it.id); setErr("");
    try {
      await apiPost("/api/campaign/"+id+"/workflow/category/"+it.id,{template_id:direction.template_id,params:paramsObj});
      await loadItems(); await loadIntake();
    } catch(e:any){setErr(e?.message||"Не удалось сохранить параметры карточки");} finally{setFeedBusy("");}
  };
  const continueBorisPreparation = async () => {
    const acc = account || camp?.default_account_id || accounts[0] || "";
    if (!acc) { setErr("Не выбран аккаунт"); return; }
    setFeedBusy("boris-run"); setErr("");
    try {
      const currentIntake = await loadIntake(true);
      const currentWorkflow = await apiGet("/api/campaign/"+id+"/workflow");
      if ((currentWorkflow?.questions || []).length) {
        setBorisRunMessage("BORISу нужен один обязательный ответ. После него работа продолжится автоматически.");
        return;
      }
      if ((currentIntake?.directions || []).length && !currentIntake?.all_complete) {
        setBorisRunMessage("BORIS продолжает автоматически. Готовые изменения сохраняются по мере обработки.");
        return;
      }
      const r=await apiPost("/api/campaign/"+id+"/guardian/prepare",{account_id:acc});
      const job=r?.job;
      setBorisRunMessage("BORIS продолжает автоматически. Готовые изменения сохраняются по мере обработки.");
      guardianSignalRef.current="";
      await loadGuardianStatus();
      if(job?.status==="failed") throw new Error("BORIS восстанавливает подготовку автоматически. Действий от вас не требуется.");
    } catch (e:any) {
      setErr(e?.message || "BORIS не смог запустить подготовку");
    } finally { setFeedBusy(""); }
  };

  const uploadReferenceFilesIfNeeded = async (acc:string) => {
    let mediaIds=[...referenceMediaIds];
    if (!referenceFiles.length) return mediaIds;
    const form=new FormData();
    form.append("account_id",acc);
    form.append("folder_name",`campaign_${id}_competitor_refs`);
    referenceFiles.slice(0,8).forEach((f)=>form.append("files",f));
    const up=await apiUpload("/api/media/upload",form);
    if (up?.status === "error") throw new Error(up?.message || "Не удалось загрузить скриншоты");
    mediaIds=Array.from(new Set([
      ...mediaIds,
      ...(up?.created||[]).map((x:any)=>Number(x.id)).filter(Boolean),
      ...(up?.duplicates||[]).map((x:any)=>Number(x.duplicate_of)).filter(Boolean),
    ]));
    setReferenceMediaIds(mediaIds);
    setReferenceFiles([]);
    return mediaIds;
  };

  const startBoris = async () => {
    const ownText = referenceMode === "preserve_text" ? referenceText.trim() : "";
    const sourceBrief = brief.trim() || ownText;
    if (!sourceBrief) return;
    const acc = account || camp?.default_account_id || accounts[0] || "";
    setFeedBusy("boris-run"); setErr(""); setExportInfo(null);
    setBorisRunMessage("BORIS разбирает задачу и строит план…");
    try {
      const hasReferenceInputs = Boolean(
        ownText || referenceLinks.trim() || uniquenessParameters.trim() || referenceFiles.length || referenceMediaIds.length
      );
      if (hasReferenceInputs) {
        if (!acc) throw new Error("Сначала выберите аккаунт");
        const mediaIds=await uploadReferenceFilesIfNeeded(acc);
        const links=referenceLinks.split(/\n+/).map(x=>x.trim()).filter(Boolean).slice(0,10);
        const saved=await apiPost("/api/campaign/"+id+"/workflow/references",{
          links,
          text: ownText || referenceText.trim(),
          mode: ownText ? "preserve_text" : referenceMode,
          media_ids:mediaIds,
          uniqueness_parameters:uniquenessParameters
        });
        if (saved?.status === "error") throw new Error(saved?.message || "Не удалось сохранить исходные материалы");
        if (ownText) {
          setReferenceSaved("Исходный текст сохранён как источник фактов для всех вариантов.");
          setOwnTextFlowReady(true);
        } else {
          setReferenceSaved(saved?.screenshots_analyzed
            ? "Примеры и скриншоты сохранены и будут учтены при подготовке объявлений."
            : "Исходные материалы сохранены и будут учтены при подготовке объявлений.");
        }
      }
      const r = await apiPost("/api/campaign/" + id + "/workflow/brief", { brief: sourceBrief, variants: workflow?.variants || 1, quantity: Math.max(0, Math.min(1000, Number(targetQuantity || 0) || 0)) });
      setWorkflow(r?.workflow || null); setQuestions(r?.questions || []); setPlan(r?.workflow || null);
      if ((r?.questions || []).length) {
        setBorisRunMessage("BORIS разобрал задачу. Ответьте только на недостающие вопросы ниже — после этого он продолжит подготовку.");
        return;
      }
      await continueBorisPreparation();
    } catch (e:any) {
      setErr(e?.message || "Не удалось запустить BORIS");
      setBorisRunMessage("");
    } finally {
      // BORIS_CAMPAIGN_START_BUSY_CLEAR_V1: React state inside this async closure
      // can still contain the pre-click value. Always release the local button
      // lock after this launch step; guardianRunning remains the server-truth lock.
      setFeedBusy("");
    }
  };

  const materialize = async () => {
    setErr("");
    let currentIntake=intake;
    if (!currentIntake) currentIntake=await loadIntake(false);
    if ((currentIntake?.directions || []).length && !currentIntake?.all_complete) {
      const next=Math.max(0,(currentIntake.directions || []).findIndex((x:any)=>!x.complete));
      setIntakeStep(next >= 0 ? next : 0);
      setShowIntakeModal(true);
      setErr("Перед созданием объявлений ответьте на вопросы по каждому направлению.");
      return;
    }
    setFeedBusy("matrix");
    try {
      await apiPost("/api/campaign/" + id + "/workflow/materialize", { account_id: account || camp?.default_account_id || accounts[0] || "" });
      await loadWorkflow(); await loadItems();
    } catch (e: any) { setErr(e?.message || "Не удалось создать структуру объявлений"); }
    finally { setFeedBusy(""); }
  };
  const generateTexts = async () => {
    const targetIds = selectedItemIds.length ? selectedItemIds : (preview?.items || []).map((x:any)=>Number(x.id)).filter(Boolean);
    if (!targetIds.length) { setErr("Сначала создайте структуру объявлений."); return; }
    setFeedBusy("texts"); setErr("");
    try {
      if (!textGenerationKeyRef.current) textGenerationKeyRef.current = globalThis.crypto?.randomUUID?.() || ("texts-"+Date.now()+"-"+Math.random().toString(36).slice(2));
      const r = await apiPost("/api/campaign/" + id + "/workflow/texts", { item_ids: targetIds, goal: "message", length: "medium", idempotency_key: textGenerationKeyRef.current });
      textGenerationKeyRef.current = "";
      if (["selection_required","idempotency_required","paid_confirmation_required","error"].includes(String(r?.status||""))) {
        setErr(r?.message || "Генерация текстов остановлена защитой BORIS");
        return;
      }
      if (r?.errors?.length) setErr("Часть текстов не создана: " + r.errors.map((x:any)=>x.id).join(", "));
      await loadWorkflow(); await loadItems();
    } catch (e: any) { setErr(e?.message || "Генерация текстов не удалась"); }
    finally { setFeedBusy(""); }
  };
  const regenerateOne = async (itemId:number) => {
    setFeedBusy("text-"+itemId); setErr("");
    try {
      if (!regenerateTextKeyRef.current[itemId]) {
        regenerateTextKeyRef.current[itemId]=globalThis.crypto?.randomUUID?.() || ("text-regenerate-"+itemId+"-"+Date.now());
      }
      const r=await apiPost("/api/campaign/"+id+"/workflow/texts",{item_ids:[itemId],goal:"message",length:"medium",idempotency_key:regenerateTextKeyRef.current[itemId]});
      // An HTTP response means the backend knows this intent. Fetch/network
      // ambiguity keeps the same key for the next click and cannot rebuy it.
      delete regenerateTextKeyRef.current[itemId];
      if(r?.errors?.length) throw new Error(r.errors[0]?.message||"Текст не создан");
      await loadItems(); await loadWorkflow();
    }
    catch(e:any){setErr(e?.message||"Не удалось обновить текст");} finally{setFeedBusy("");}
  };
  const generateBanners = async () => {
    setErr("");
    if (!selectedItemIds.length) {
      setErr("Выберите объявления, для которых нужно создать баннеры. Массовая платная генерация без выбора отключена.");
      return;
    }
    setFeedBusy("banners");
    try {
      const fingerprint=[...selectedItemIds].map(Number).filter(Boolean).sort((a,b)=>a-b).join(",");
      if (!bannerGenerationKeyRef.current || bannerGenerationFingerprintRef.current !== fingerprint) {
        bannerGenerationKeyRef.current = globalThis.crypto?.randomUUID?.() || `banner-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        bannerGenerationFingerprintRef.current = fingerprint;
      }
      const r = await apiPost("/api/campaign/" + id + "/workflow/banners", { item_ids: selectedItemIds, goal: "offer", force: false, idempotency_key: bannerGenerationKeyRef.current });
      // apiPost returns for every HTTP response (including 4xx/5xx). Only fetch-level
      // exceptions reach catch, where retaining the key prevents a duplicate charge.
      bannerGenerationKeyRef.current = "";
      bannerGenerationFingerprintRef.current = "";
      if (r?.status === "error") {
        setErr(r?.message || "Создание баннеров не удалось");
        return;
      }
      if (r?.code === "paid_batch_confirmation_required") {
        setErr(`Защита бюджета остановила запуск: ${r.paid_images || selectedItemIds.length} платных изображений. Выберите до ${r.normal_paid_cap || 10} объявлений за один запуск.`);
        return;
      }
      if (r?.errors?.length) setErr("Часть баннеров не создана: " + r.errors.map((x:any)=>x.id).join(", "));
      await loadWorkflow(); await loadItems();
    } catch (e: any) { setErr(e?.message || "Создание баннеров не удалось"); }
    finally { setFeedBusy(""); }
  };
  const loadMedia = async () => {
    setFeedBusy("media"); setErr("");
    try {
      const acc = account || camp?.default_account_id || accounts[0] || "";
      const r = await apiGet("/api/media/list?account_id=" + encodeURIComponent(acc) + "&limit=18&sort=new");
      setMediaPool(r?.items || []);
    } catch (e:any) { setErr(e?.message || "Не удалось открыть медиатеку"); }
    finally { setFeedBusy(""); }
  };
  const collectStock = async () => {
    const acc=account || camp?.default_account_id || accounts[0] || "";
    const fallback=(workflow?.directions || []).join(", ") || brief;
    const query=(stockQuery || fallback || "").trim();
    if (!acc || query.length < 3) { setErr("Укажите, какие фотографии нужно найти"); return; }
    setFeedBusy("stock"); setErr("");
    try {
      const r=await apiPost("/api/avito/search_stock_photos",{query,count:stockCount,account_id:acc,folder:`campaign_${id}_stock`,source:stockSource});
      if (r?.status === "error") throw new Error(r?.message || "Сток не вернул фотографии");
      await loadMedia();
    } catch(e:any){ setErr(e?.message || "Не удалось собрать фотографии со стоков"); }
    finally { setFeedBusy(""); }
  };
  const collectStockForCampaignItems = async () => {
    const targetIds = selectedItemIds.length ? selectedItemIds : (preview?.items || []).map((x:any)=>Number(x.id)).filter(Boolean);
    if (!targetIds.length) { setErr("Сначала создайте варианты объявлений, затем BORIS подберёт фотографии под каждое из них."); return; }
    setFeedBusy("auto-stock"); setErr("");
    try {
      const r=await apiPost("/api/campaign/"+id+"/workflow/auto-media",{item_ids:targetIds,count_per_item:1});
      if (r?.status === "error" || r?.status === "selection_required") throw new Error(r?.message || "Не удалось подобрать фотографии");
      if (r?.errors?.length) setErr("Часть фотографий не подошла: "+r.errors.map((x:any)=>x.id).join(", "));
      await Promise.all([loadWorkflow(),loadItems(),loadPreview()]);
    } catch(e:any) { setErr(e?.message || "Не удалось подобрать фотографии"); }
    finally { setFeedBusy(""); }
  };

  const generateBannersFromOwnTextFlow = async () => {
    const targetIds = selectedItemIds.length ? selectedItemIds : (preview?.items || []).map((x:any)=>Number(x.id)).filter(Boolean);
    if (!targetIds.length) { setErr("Сначала создайте варианты объявлений. После этого BORIS сможет сделать визуалы под каждую карточку."); return; }
    if (!selectedItemIds.length) setSelectedItemIds(targetIds);
    document.getElementById("campaign-visual-actions")?.scrollIntoView({behavior:"smooth",block:"start"});
    if (targetIds.length > 10) { setErr("Выбрано "+targetIds.length+" объявлений. Для защиты бюджета выберите до 10 карточек на один запуск AI-баннеров."); return; }
    setFeedBusy("banners"); setErr("");
    try {
      const fingerprint=[...targetIds].map(Number).filter(Boolean).sort((a,b)=>a-b).join(",");
      if (!bannerGenerationKeyRef.current || bannerGenerationFingerprintRef.current !== fingerprint) {
        bannerGenerationKeyRef.current=globalThis.crypto?.randomUUID?.() || ("banner-"+Date.now()+"-"+Math.random().toString(36).slice(2));
        bannerGenerationFingerprintRef.current=fingerprint;
      }
      const r=await apiPost("/api/campaign/"+id+"/workflow/banners",{item_ids:targetIds,goal:"offer",force:false,idempotency_key:bannerGenerationKeyRef.current,confirmed_paid_items:0});
      // Definite HTTP response: backend has a durable view of this intent. On a
      // fetch/network exception catch keeps both refs, so a retry cannot rebuy it.
      bannerGenerationKeyRef.current="";
      bannerGenerationFingerprintRef.current="";
      if (r?.code === "paid_batch_confirmation_required") throw new Error(r?.message || "Слишком большая платная генерация");
      if (r?.status === "error") throw new Error(r?.message || "Создание баннеров не удалось");
      if (r?.errors?.length) setErr("Часть баннеров не создана: "+r.errors.map((x:any)=>x.id).join(", "));
      await Promise.all([loadWorkflow(),loadItems(),loadPreview()]);
    } catch(e:any) { setErr(e?.message || "Создание баннеров не удалось"); }
    finally { setFeedBusy(""); }
  };

  const loadOwnTextFile = async (file:File) => {
    setErr("");
    const low=(file.name || "").toLowerCase();
    try {
      if (low.endsWith(".docx")) {
        const form=new FormData(); form.append("file",file);
        const r=await apiUpload("/api/campaign/"+id+"/workflow/text-file",form);
        const text=String(r?.text || r?.extracted_text || "").trim();
        if (!text) throw new Error("BORIS не нашёл текст в Word-файле");
        setReferenceText(text.slice(0,30000));
      } else {
        const text=(await file.text()).trim();
        if (!text) throw new Error("Файл пустой");
        setReferenceText(text.slice(0,30000));
      }
      setReferenceMode("preserve_text");
      setReferenceTextFileName(file.name);
      setReferenceSaved(`Загружен исходный текст: ${file.name}`);
      setOwnTextFlowReady(false);
    } catch(e:any) { setErr(e?.message || "Не удалось прочитать файл с текстом"); }
  };

  const saveReferences = async () => {
    const acc=account || camp?.default_account_id || accounts[0] || "";
    if (!acc) { setErr("Сначала выберите аккаунт"); return; }
    setFeedBusy("references"); setErr(""); setReferenceSaved("");
    try {
      const mediaIds=await uploadReferenceFilesIfNeeded(acc);
      const links=referenceLinks.split(/\n+/).map(x=>x.trim()).filter(Boolean).slice(0,10);
      const r=await apiPost("/api/campaign/"+id+"/workflow/references",{links,text:referenceText,mode:referenceMode,media_ids:mediaIds,uniqueness_parameters:uniquenessParameters});
      if (r?.status === "error") throw new Error(r?.message || "Не удалось сохранить примеры");
      setReferenceMediaIds(mediaIds); setReferenceFiles([]);
      setReferenceSaved(referenceMode==="preserve_text" && referenceText.trim()?"Исходный текст сохранён. BORIS будет размножать его за счёт смысловой уникализации без выдумывания новых фактов.":r?.screenshots_analyzed?"Примеры сохранены. Скриншоты разобраны BORIS и будут учтены в текстах.":"Примеры сохранены и будут учтены при создании текстов.");
      setOwnTextFlowReady(referenceMode==="preserve_text" && Boolean(referenceText.trim()));
      await loadWorkflow();
    } catch(e:any){ setErr(e?.message || "Не удалось сохранить примеры конкурентов"); }
    finally { setFeedBusy(""); }
  };
  const attachMedia = async (mediaId:number) => {
    if (!selectedItemIds.length) { setErr("Выберите хотя бы одно объявление"); return; }
    setFeedBusy("attach"); setErr("");
    try {
      await apiPost("/api/campaign/" + id + "/workflow/mutate", { item_ids: selectedItemIds, media_ids: [mediaId], hero_media_id: mediaId });
      await loadItems(); await loadWorkflow();
    } catch (e:any) { setErr(e?.message || "Не удалось привязать изображение"); }
    finally { setFeedBusy(""); }
  };
  const openFullItemView = (it:Item) => {
    const p=it.payload || {};
    const images=Array.from(new Set([p.banner_url,...(p.images||[])].filter(Boolean)));
    itemVersionsRef.current[it.id]=it.updated_at||"";
    setFullTextItem({
      id:it.id, updated_at:it.updated_at,
      title:p.title || "Объявление",
      description:p.description || "",
      city:p.city || it.geo || "",
      category:p.category || String(p.category_path||"").split(">").map((x:string)=>x.trim()).filter(Boolean).pop() || "",
      price:p.price,
      images,
      banner_url:p.banner_url || "",
    });
    setFullTextDraft(String(p.description||""));
  };

  const saveFullText = async () => {
    if (!fullTextItem?.id) return;
    setFeedBusy("full-text-save"); setErr("");
    try {
      await apiPost("/api/campaign/"+id+"/workflow/mutate",{item_ids:[Number(fullTextItem.id)],patch:{description:fullTextDraft}});
      setFullTextItem((old:any)=>old?({...old,description:fullTextDraft}):old);
      const acc=account || camp?.default_account_id || accounts[0] || "";
      if(acc) await apiPost("/api/campaign/"+id+"/guardian/prepare",{account_id:acc}).catch(()=>null);
      await Promise.all([loadItems(),loadPreview(),checkFeed(),loadGuardianStatus()]);
      setBorisRunMessage("Текст сохранён. BORIS перепроверяет объявление перед размещением.");
    } catch(e:any){ setErr(e?.message || "Не удалось сохранить текст"); }
    finally { setFeedBusy(""); }
  };

  const uploadCampaignPhotoStack = async (filesOverride?:File[]) => {
    const files=(filesOverride && filesOverride.length) ? filesOverride : campaignPhotoFiles;
    const acc=account || camp?.default_account_id || accounts[0] || "";
    const targetIds=selectedItemIds.length ? selectedItemIds : (preview?.items || []).map((x:any)=>Number(x.id)).filter(Boolean);
    if (!acc || !targetIds.length) { setErr("Выберите объявления для фотографий"); return; }
    if (!files.length) { photoStackInputRef.current?.click(); return; }
    setFeedBusy("photo-stack"); setErr("");
    try {
      const form=new FormData(); form.append("account_id",acc); form.append("folder_name",`campaign_${id}_manual_photos`);
      files.slice(0,30).forEach((f)=>form.append("files",f));
      const up=await apiUpload("/api/media/upload",form);
      const mediaIds=Array.from(new Set([...(up?.created||[]).map((x:any)=>Number(x.id)),...(up?.duplicates||[]).map((x:any)=>Number(x.duplicate_of))].filter(Boolean)));
      if (!mediaIds.length) throw new Error("Не удалось загрузить фотографии");
      await apiPost("/api/campaign/"+id+"/workflow/photo-stack",{item_ids:targetIds,media_ids:mediaIds,randomize:randomizePhotoStack,after_banner:true,photos_per_item:photosPerAd});
      setCampaignPhotoFiles([]);
      setBorisRunMessage(`Фотографии добавлены в ${targetIds.length} объявлений. BORIS перепроверяет кампанию.`);
      await apiPost("/api/campaign/"+id+"/guardian/prepare",{account_id:acc}).catch(()=>null);
      await Promise.all([loadItems(),loadPreview(),checkFeed(),loadGuardianStatus()]);
    } catch(e:any){ setErr(e?.message || "Не удалось добавить фотографии"); }
    finally { setFeedBusy(""); }
  };

  const loadPhotoAnglesStatus = useCallback(async () => {
    try { const r=await apiGet("/api/campaign/"+id+"/workflow/photo-angles"); setPhotoAnglesStatus(r); return r; }
    catch { return null; }
  },[id]);

  const generatePhotoAngles = async () => {
    setFeedBusy("photo-angles"); setErr("");
    try {
      const r=await apiPost("/api/campaign/"+id+"/workflow/photo-angles",{target_count:20,source_count:5});
      setBorisRunMessage(r?.already_ready ? `20 новых ракурсов уже готовы. Повторная платная генерация не запущена.` : (r?.message || "BORIS создаёт новые ракурсы из лучших фотографий."));
      await Promise.all([loadPhotoAnglesStatus(),loadWorkflow()]);
    } catch(e:any){ setErr(e?.message || "Не удалось запустить генерацию новых ракурсов"); }
    finally { setFeedBusy(""); }
  };

  const analyzeTop10Competitors = async (apply=false) => {
    const targetIds=selectedItemIds.length ? selectedItemIds : (preview?.items || []).map((x:any)=>Number(x.id)).filter(Boolean);
    if (!targetIds.length) { setErr("Нет объявлений для анализа"); return; }
    setFeedBusy(apply?"competitors-apply":"competitors"); setErr("");
    try {
      const r=await apiPost("/api/campaign/"+id+"/workflow/copywriter/competitors",{item_ids:targetIds,query:competitorQuery.trim(),city:competitorCity.trim(),count:competitorCount,apply});
      if (r?.status!=="ok") throw new Error(r?.message || "Не удалось выполнить анализ конкурентов");
      setCompetitorAudit(r); setCompetitorAuditOpen(true);
      if (apply) {
        setBorisRunMessage(`BORIS использует анализ ТОП-${r.count || 10} конкурентов и усиливает ${targetIds.length} объявлений. BORIS продолжает автоматически. Готовые изменения сохраняются по мере обработки.`);
        await loadGuardianStatus();
      }
    } catch(e:any){ setErr(e?.message || "Не удалось выполнить анализ ТОП-10 конкурентов"); }
    finally { setFeedBusy(""); }
  };

  const runQuickTextEdit = async (itemId:number, instruction:string) => {
    if (!instruction.trim()) return;
    const acc=account || camp?.default_account_id || accounts[0] || "";
    if (!acc) { setErr("Не удалось определить аккаунт"); return; }
    setFeedBusy("quick-text-"+itemId); setErr("");
    try {
      const r=await apiPost("/api/campaign/"+id+"/workflow/enhance",{item_ids:[itemId],prompt:instruction.trim()});
      if (r?.status !== "ok") throw new Error(r?.message || "Не удалось запустить изменение текста");
      setBorisRunMessage("BORIS изменяет текст. Можно закрыть страницу — прогресс сохранится.");
      await loadGuardianStatus();
    } catch(e:any) { setErr(e?.message || "Не удалось изменить текст"); }
    finally { setFeedBusy(""); }
  };

  const makePhotoMain = async (itemId:number, mediaId:number) => {
    if (!mediaId) return;
    setFeedBusy("photo-main-"+itemId); setErr("");
    try {
      await apiPost("/api/campaign/"+id+"/workflow/mutate",{item_ids:[itemId],hero_media_id:mediaId});
      await Promise.all([loadItems(),loadWorkflow()]);
      setBorisRunMessage("Главное фото изменено. BORIS перепроверит соответствие карточке.");
    } catch(e:any) { setErr(e?.message || "Не удалось изменить главное фото"); }
    finally { setFeedBusy(""); }
  };

  const removePhoto = async (itemId:number, mediaId:number) => {
    if (!mediaId) return;
    setFeedBusy("photo-remove-"+itemId+"-"+mediaId); setErr("");
    try {
      await apiPost("/api/campaign/"+id+"/workflow/mutate",{item_ids:[itemId],remove_media_ids:[mediaId]});
      await Promise.all([loadItems(),loadWorkflow()]);
      setBorisRunMessage("Фото удалено из карточки. BORIS перепроверит объявление.");
    } catch(e:any) { setErr(e?.message || "Не удалось удалить фото"); }
    finally { setFeedBusy(""); }
  };

  const reorderPhotos = async (itemId:number, media:any[], fromIndex:number, toIndex:number) => {
    if (!Array.isArray(media) || media.length < 2) return;
    if (fromIndex < 0 || toIndex < 0 || fromIndex >= media.length || toIndex >= media.length || fromIndex===toIndex) return;
    const ordered=[...media];
    const [moved]=ordered.splice(fromIndex,1);
    ordered.splice(toIndex,0,moved);
    const ids=ordered.map((x:any)=>Number(x?.media_id||0)).filter((x:number)=>x>0);
    if (ids.length!==ordered.length) { setErr("Не удалось определить порядок фотографий. Обновите карточку."); return; }
    setFeedBusy("photo-order-"+itemId); setErr("");
    try {
      await apiPost("/api/campaign/"+id+"/workflow/mutate",{item_ids:[itemId],media_order_ids:ids});
      await Promise.all([loadItems(),loadWorkflow()]);
      setBorisRunMessage(toIndex===0 ? "Главное фото изменено. BORIS перепроверит карточку." : "Порядок фотографий сохранён. BORIS перепроверит карточку.");
    } catch(e:any) { setErr(e?.message || "Не удалось изменить порядок фотографий"); }
    finally { setFeedBusy(""); }
  };

  const findPhotoForOne = async (itemId:number) => {
    setFeedBusy("photo-auto-"+itemId); setErr("");
    try {
      const r=await apiPost("/api/campaign/"+id+"/workflow/auto-media",{item_ids:[itemId],count_per_item:1});
      if (r?.status==="error" || r?.status==="selection_required") throw new Error(r?.message || "Не удалось подобрать фото");
      await Promise.all([loadItems(),loadWorkflow(),loadPreview()]);
      setBorisRunMessage("BORIS подобрал фото и запустил повторную проверку карточки.");
    } catch(e:any) { setErr(e?.message || "Не удалось подобрать фото"); }
    finally { setFeedBusy(""); }
  };

  const regenerateBannerOne = async (itemId:number) => {
    setFeedBusy("banner-one-"+itemId); setErr("");
    try {
      const key=globalThis.crypto?.randomUUID?.() || ("banner-one-"+itemId+"-"+Date.now());
      const r=await apiPost("/api/campaign/"+id+"/workflow/banners",{item_ids:[itemId],goal:"offer",force:true,idempotency_key:key,confirmed_paid_items:1});
      if (r?.status!=="ok") throw new Error(r?.message || "Не удалось сделать другой баннер");
      await Promise.all([loadItems(),loadWorkflow(),loadPreview()]);
      setBorisRunMessage("Новый баннер готов. Проверьте его в карточке.");
    } catch(e:any) { setErr(e?.message || "Не удалось сделать другой баннер"); }
    finally { setFeedBusy(""); }
  };

  const restoreTextVersion = async (it:Item, history:any) => {
    const snapshot=history?.snapshot || {};
    const patch:any={};
    if (typeof snapshot.title==="string") patch.title=snapshot.title;
    if (typeof snapshot.description==="string") patch.description=snapshot.description;
    if (!Object.keys(patch).length) return;
    setFeedBusy("history-"+it.id); setErr("");
    try {
      await apiPost("/api/campaign/"+id+"/workflow/mutate",{item_ids:[it.id],patch});
      await Promise.all([loadItems(),loadWorkflow()]);
      setBorisRunMessage("Предыдущий текст восстановлен. BORIS перепроверит карточку.");
    } catch(e:any) { setErr(e?.message || "Не удалось восстановить версию"); }
    finally { setFeedBusy(""); }
  };

  const enhanceCampaignTexts = async () => {
    const prompt=enhancePrompt.trim();
    const targetIds=selectedItemIds.length ? selectedItemIds : (preview?.items || []).map((x:any)=>Number(x.id)).filter(Boolean);
    if (!prompt) { setErr("Напишите, как усилить объявления"); enhancePromptRef.current?.focus(); return; }
    if (!targetIds.length) { setErr("Нет объявлений для редактирования"); return; }
    const acc=account || camp?.default_account_id || accounts[0] || "";
    setFeedBusy("enhance-texts"); setErr("");
    try {
      const r=await apiPost("/api/campaign/"+id+"/workflow/enhance",{item_ids:targetIds,prompt});
      if (r?.status !== "ok") throw new Error(r?.message || "Не удалось запустить усиление текстов");
      setBorisRunMessage(`BORIS продолжает автоматически. Готовые изменения сохраняются по мере обработки.`);
      await loadGuardianStatus();
    } catch(e:any){ setErr(e?.message || "Не удалось усилить тексты"); }
    finally { setFeedBusy(""); }
  };

  const checkFeed = async () => {
    setFeedBusy("check"); setErr("");
    try { setValidation(await apiGet("/api/campaign/" + id + "/validation")); }
    catch (e: any) { setErr(e?.message || "Проверка не удалась"); }
    finally { setFeedBusy(""); }
  };
  const loadPreview = async () => {
    setFeedBusy("preview"); setErr("");
    try { setPreview(await apiGet("/api/campaign/" + id + "/workflow/preview")); }
    catch (e:any) { setErr(e?.message || "Preview не удалось загрузить"); }
    finally { setFeedBusy(""); }
  };
  const approveReady = async () => {
    setFeedBusy("approve"); setErr("");
    try { await apiPost("/api/campaign/" + id + "/approval", { item_ids: selectedItemIds, approved: true }); await checkFeed(); await loadItems(); await loadWorkflow(); }
    catch (e: any) { setErr(e?.message || "Согласование не удалось"); }
    finally { setFeedBusy(""); }
  };
  const safeExport = async () => {
    setFeedBusy("export"); setErr(""); setExportInfo(null);
    try { setExportInfo(await apiPost("/api/campaign/" + id + "/safe_export", {})); }
    catch (e: any) { setErr(e?.message || "Пакет пока не готов: исправьте отмеченные объявления и согласуйте готовые"); }
    finally { setFeedBusy(""); }
  };
  const parseBulkVariables = () => {
    const out:Record<string,string[]>={};
    for(const raw of String(bulkVariablesText||"").split(/\n+/)){
      const line=raw.trim();
      if(!line) continue;
      const pos=line.search(/[=:]/);
      if(pos<=0) continue;
      const key=line.slice(0,pos).trim();
      const values=line.slice(pos+1).split(/[|,;]/).map(x=>x.trim()).filter(Boolean);
      if(key && values.length) out[key]=values;
    }
    return out;
  };
  const bulkTargetIds = () => selectedItemIds.length
    ? selectedItemIds
    : items.filter((it:any)=>!bulkDirection || it.segment===bulkDirection).map((it:any)=>Number(it.id)).filter(Boolean);
  const bulkTransformPayload = (previewOnly:boolean,confirmed:boolean) => {
    const categoryParams:Record<string,string>={};
    for(const [key,val] of Object.entries(bulkCategoryValues)){
      if(String(val||"").trim()) categoryParams[key]=String(val).trim();
    }
    const price=bulkPrice.trim()?Number(bulkPrice):null;
    if(price!==null && (!Number.isFinite(price)||price<=0)) throw new Error("Укажите корректную цену больше нуля");
    return {
      item_ids:bulkTargetIds(),
      title_template:bulkTitleTemplate.trim(),
      description_template:bulkDescriptionTemplate.trim(),
      description_mode:bulkDescriptionMode,
      variables:parseBulkVariables(),
      price,
      city:bulkCity.trim(),
      template_id:bulkTemplateId,
      category_params:categoryParams,
      preview_only:previewOnly,
      confirmed,
    };
  };
  const previewBulkTransform = async () => {
    setFeedBusy("bulk-preview"); setErr(""); setBulkTransformPreview(null);
    try {
      const payload=bulkTransformPayload(true,false);
      if(!payload.item_ids.length) throw new Error("Выберите объявления для массовой настройки");
      if(!payload.title_template && !payload.description_template && payload.price===null && !payload.city && !payload.template_id) throw new Error("Укажите, что нужно изменить");
      const r=await apiPost("/api/campaign/"+id+"/workflow/bulk-transform",payload);
      setBulkTransformPreview(r);
      if(r?.status==="blocked") setErr(r?.message||"Часть карточек требует данных");
    } catch(e:any){ setErr(e?.message||"Не удалось подготовить предпросмотр"); }
    finally { setFeedBusy(""); }
  };
  const applyBulkTransform = async () => {
    setFeedBusy("bulk-apply"); setErr("");
    try {
      if(!bulkTransformPreview || bulkTransformPreview.status!=="ok") throw new Error("Сначала подготовьте успешный предпросмотр");
      const payload=bulkTransformPayload(false,true);
      const r=await apiPost("/api/campaign/"+id+"/workflow/bulk-transform",payload);
      if(r?.status!=="ok") throw new Error(r?.message||"Массовая настройка не применена");
      setBulkTransformPreview(null);
      setBorisRunMessage(r?.message||"Массовая настройка применена. BORIS перепроверяет карточки.");
      await Promise.all([loadItems(),loadWorkflow(),loadPreview()]);
    } catch(e:any){ setErr(e?.message||"Не удалось применить массовую настройку"); }
    finally { setFeedBusy(""); }
  };
  const loadFeedControl = useCallback(async (refreshAvito=false) => {
    const acc = account || camp?.default_account_id || accounts[0] || "";
    if (!acc) return null;
    try {
      const pf = await apiGet("/api/accounts/campaign-preflight?account_id=" + encodeURIComponent(acc) + "&campaign_id=" + encodeURIComponent(id) + "&refresh_avito=" + (refreshAvito?"true":"false"));
      if (pf?.status === "ok") {
        setAvitoPreflight(pf);
        if (pf?.delivery?.status === "ok") {
          const guardedDelivery={...pf.delivery,can_send:Boolean(pf.can_publish),blocked_reason:pf.blocked_reason||pf.delivery?.blocked_reason||null};
          setFeedControl(guardedDelivery);
          return guardedDelivery;
        }
        return null;
      }
    } catch {
      // Backward-compatible during rolling deploy: the old Feed Control remains usable.
      setAvitoPreflight(null);
    }
    const r = await apiGet("/api/campaign/" + id + "/feed_control?account_id=" + encodeURIComponent(acc));
    if (r?.status === "ok") setFeedControl(r);
    return r;
  }, [id, account, camp?.default_account_id, accounts]);
  useEffect(()=>{
    if(feedControl?.account_id && !avitoPreflight) {
      void loadFeedControl(false);
    }
  },[feedControl?.account_id,avitoPreflight,loadFeedControl]);
  const validateAvitoFeed = async () => {
    const acc = feedControl?.account_id || account || camp?.default_account_id || accounts[0] || "";
    setFeedBusy("xml-check"); setErr("");
    try {
      const r=await apiPost("/api/campaign/"+id+"/feed_control/validate",{account_id:acc,confirmed:false});
      if(r?.status==="ok") {
        setFeedControl(r);
        setAvitoPreflight(null);
        await Promise.all([loadWorkflow(),loadFeedControl(false)]);
      }
      else setErr(r?.message || "Проверка Avito не пройдена");
    } catch(e:any){setErr(e?.message||"Проверка Avito не удалась");} finally{setFeedBusy("");}
  };
  const loadActivation = useCallback(async () => {
    const acc = account || camp?.default_account_id || accounts[0] || "";
    if (!acc) return null;
    const r = await apiGet("/api/campaign/" + id + "/owner_activation?account_id=" + encodeURIComponent(acc));
    setActivation(r); return r;
  }, [id, account, camp?.default_account_id, accounts]);
  const openActivation = async () => {
    setFeedBusy("activation-check"); setErr("");
    try {
      const [r,fc]=await Promise.all([loadActivation(),loadFeedControl()]);
      if(r?.can_activate && fc?.can_send) setShowActivationConfirm(true);
      else setErr(fc?.blocked_reason || r?.blocked_reason || "Объявления пока не готовы к отправке");
    }
    catch(e:any){setErr(e?.message||"Не удалось проверить готовность к размещению");} finally{setFeedBusy("");}
  };
  const approveAllAndOpenActivation = async () => {
    if (!items.length) return;
    const acc=account || camp?.default_account_id || accounts[0] || "";
    if (!acc) { setErr("Не выбран аккаунт Avito для размещения"); return; }
    setFeedBusy("approve-place"); setErr("");
    try {
      // This click is the owner's explicit placement confirmation. The durable server job
      // re-approves only valid cards, rechecks the exact XML SHA and then calls the existing uploader.
      const r=await apiPost("/api/campaign/"+id+"/guardian/deliver",{account_id:acc,confirmed:true});
      if(r?.status!=="ok") throw new Error(r?.message||"Не удалось запустить размещение");
      setShowActivationConfirm(false);
      guardianSignalRef.current="";
      setBorisRunMessage("BORIS продолжает автоматически. Готовые изменения сохраняются по мере обработки.");
      await loadGuardianStatus();
    } catch(e:any){ setErr(e?.message || "Не удалось запустить размещение"); }
    finally{ setFeedBusy(""); }
  };
  const confirmActivation = approveAllAndOpenActivation;

  const returnSelected = async () => {
    if (!selectedItemIds.length) { setErr("Выберите объявления, которые нужно вернуть на исправление"); return; }
    setFeedBusy("return"); setErr("");
    if (!returnComment.trim()) { setErr("Напишите, что нужно исправить в выбранных объявлениях"); return; }
    try { await apiPost("/api/campaign/" + id + "/approval", { item_ids: selectedItemIds, approved: false, comment: returnComment.trim() }); setReturnComment(""); await loadItems(); await loadWorkflow(); }
    catch (e:any) { setErr(e?.message || "Не удалось вернуть объявления на исправление"); }
    finally { setFeedBusy(""); }
  };

  if (loading) {
    return <Page><PageHeader title="Кампания" subtitle="Загружаю…" /></Page>;
  }
  if (!camp) {
    return (
      <Page>
        <PageHeader title="Кампания" />
        <Alert kind="danger">Кампания не найдена или у вас нет доступа к её аккаунтам.</Alert>
      </Page>
    );
  }

  const tabs = ["", "needs_data", "draft", "ready", "published", "publish_failed"];
  const activeGuardianStatuses = ["queued","running","validating","awaiting_confirmation"];
  const latestGuardian = guardianJobs.find((j:any)=>activeGuardianStatuses.includes(String(j?.status||""))) || guardianJobs[0] || null;
  const latestPreparation = guardianJobs.find((j:any)=>j?.kind==="campaign_prepare") || null;
  const preparationStatus = String(latestPreparation?.status || "");
  const preparationNeedsOwnerAction = Boolean(
    latestPreparation && (
      latestPreparation.requires_owner_action === true
      || (latestPreparation.requires_owner_action == null && preparationStatus === "awaiting_confirmation")
    )
  );
  const preparationLive = ["queued","running","validating"].includes(preparationStatus)
    || (preparationStatus === "awaiting_confirmation" && !preparationNeedsOwnerAction);
  const preparationCreatedMs = latestPreparation?.created_at ? Date.parse(String(latestPreparation.created_at)) : NaN;
  const serverPreparationSeconds = Math.max(0, Number(latestPreparation?.duration_total_seconds ?? latestPreparation?.elapsed_seconds ?? 0));
  const preparationSeconds = preparationLive && Number.isFinite(preparationCreatedMs)
    ? Math.max(serverPreparationSeconds, Math.floor((progressClockNow-preparationCreatedMs)/1000))
    : serverPreparationSeconds;
  const preparationTimeText = preparationSeconds<60 ? `${Math.max(0,preparationSeconds)} сек` : `${Math.floor(preparationSeconds/60)} мин ${Math.floor(preparationSeconds%60)} сек`;
  const preparationActiveSeconds = latestPreparation?.duration_active_seconds == null ? null : Math.max(0, Number(latestPreparation.duration_active_seconds || 0));
  const preparationWaitingSeconds = latestPreparation?.duration_waiting_seconds == null ? null : Math.max(0, Number(latestPreparation.duration_waiting_seconds || 0));
  const preparationProviderSeconds = latestPreparation?.duration_provider_seconds == null ? null : Math.max(0, Number(latestPreparation.duration_provider_seconds || 0));
  const preparationActiveText = preparationActiveSeconds == null ? "" : editorDuration(preparationActiveSeconds);
  const preparationWaitingText = preparationWaitingSeconds == null ? "" : editorDuration(preparationWaitingSeconds);
  const preparationProviderText = preparationProviderSeconds == null || preparationProviderSeconds < 1 ? "" : editorDuration(preparationProviderSeconds);
  const activeEnhance = guardianJobs.find((j:any)=>j?.kind==="campaign_enhance" && ["queued","running","validating"].includes(String(j?.status||""))) || null;
  const guardianNeedsAction = Boolean(
    latestGuardian && (
      latestGuardian.requires_owner_action === true
      || (latestGuardian.requires_owner_action == null && String(latestGuardian.status||"")==="awaiting_confirmation")
    )
  );
  const guardianRunning = Boolean(
    latestGuardian && (
      ["queued","running","validating"].includes(String(latestGuardian.status||""))
      || (String(latestGuardian.status||"")==="awaiting_confirmation" && !guardianNeedsAction)
    )
  );
  const guardianTotal = Math.max(0, Number(latestGuardian?.total || 0));
  const guardianDone = Math.max(0, Number(latestGuardian?.done || 0));
  const guardianStage = String(latestGuardian?.stage || workflow?.step || "queued");
  const guardianStagePercent:Record<string,number> = {
    queued:3, preflight:8, materialize:18, texts:38, media:58, media_shards:58, media_retry:58, media_source_wait:58, cost_anomaly:65, banners:72,
    billing_pending:72, billing_limit:72, banner_confirmation_required:72, banner_cost_cap:72,
    internal_validation:82, official_avito_validation:90, ready_for_review:100,
    approval:92, revalidate:94, sending:96, awaiting_avito_result:98,
    delivered:100, publication_partial:100, publication_failed:100,
    publication_pending_review:99, needs_input:12, needs_avito_connection:92,
    needs_autoload_access:92,
  };
  const orderRequested = Math.max(0, Number(orderProgress?.requested_total || workflow?.order_contract?.requested_total || 0));
  const orderCreated = Math.max(0, Number(orderProgress?.created_count || workflow?.order_contract?.created_count || 0));
  const orderQa = Math.max(0, Number(orderProgress?.qa_pass_count || workflow?.order_contract?.qa_pass_count || 0));
  const orderReady = Math.max(0, Number(orderProgress?.ready_count || workflow?.order_contract?.ready_count || 0));
  const orderBlocked = Math.max(0, Number(orderProgress?.blocked_count || workflow?.order_contract?.blocked_count || 0));
  const orderByDirection:any = orderProgress?.by_direction || workflow?.order_contract?.by_direction || {};
  const stageItemTotal = Math.max(orderRequested, items.length, Number(workflow?.items_planned || 0));
  const expectedMediaPerItem = Math.max(1, Math.min(8, Number(orderProgress?.media_per_item || workflow?.order_contract?.media_per_item || 3)));
  const stageItemDone = guardianStage==="materialize" ? items.length
    : guardianStage==="texts" ? items.filter((x:any)=>String(x?.payload?.title||"").trim() && String(x?.payload?.description||"").trim()).length
    : ["media","media_shards","media_retry","media_source_wait"].includes(guardianStage) ? items.filter((x:any)=>{
        const banner=String(x?.payload?.banner_url||"").trim();
        const images=Array.isArray(x?.payload?.images)?x.payload.images.filter((u:any)=>String(u||"").trim()):[];
        const gallery=images.filter((u:any)=>!banner || String(u)!==banner);
        return gallery.length>=expectedMediaPerItem;
      }).length
    : ["banners","billing_pending","billing_limit","banner_confirmation_required","banner_cost_cap"].includes(guardianStage) ? items.filter((x:any)=>Boolean(String(x?.payload?.banner_url||"").trim())).length
    : 0;
  const stageItemsVisible = stageItemTotal>0 && ["materialize","texts","media","media_shards","media_retry","media_source_wait","banners","billing_pending","billing_limit","banner_confirmation_required","banner_cost_cap"].includes(guardianStage);
  const stageRanges:Record<string,[number,number]> = {
    materialize:[10,25],texts:[25,45],media:[45,65],media_shards:[45,65],media_retry:[45,65],media_source_wait:[45,65],cost_anomaly:[45,72],
    banners:[65,82],billing_pending:[65,82],billing_limit:[65,82],banner_confirmation_required:[65,82],banner_cost_cap:[65,82],
  };
  const stageRange=stageRanges[guardianStage];
  const stageExactPercent = stageItemsVisible && stageRange
    ? Math.round(stageRange[0] + (Math.min(stageItemTotal,stageItemDone)/Math.max(1,stageItemTotal))*(stageRange[1]-stageRange[0]))
    : null;
  const guardianPercent = stageExactPercent!=null
    ? Math.max(0,Math.min(100,stageExactPercent))
    : guardianTotal > 0
      ? Math.max(0, Math.min(100, Math.round((guardianDone / Math.max(1, guardianTotal)) * 100)))
      : Math.max(0, Math.min(100, guardianStagePercent[guardianStage] ?? (guardianRunning ? 8 : 0)));
  const progressRateDone = stageItemsVisible ? stageItemDone : Math.max(orderReady,orderQa,guardianDone);
  const progressRateMinutes = preparationActiveSeconds != null && preparationActiveSeconds > 0 ? preparationActiveSeconds/60 : preparationSeconds > 0 ? preparationSeconds/60 : 0;
  const progressItemsPerMinute = progressRateDone > 0 && progressRateMinutes > 0 ? progressRateDone/progressRateMinutes : null;
  const etaSeconds = guardianRunning && guardianPercent >= 10 && guardianPercent < 100 && preparationSeconds > 0
    ? Math.max(0,Math.round(preparationSeconds*(100-guardianPercent)/guardianPercent))
    : null;
  const etaText = etaSeconds == null ? "" : editorDuration(etaSeconds);
  const guardianWaitMessage = latestGuardian?.waiting_kind === "system_wait"
    ? String(latestGuardian?.message || "Сервер временно занят. Прогресс сохранён — BORIS продолжит автоматически.")
    : "";
  const guardianLastProgressText = latestGuardian?.last_progress_at
    ? new Date(String(latestGuardian.last_progress_at)).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit",second:"2-digit"})
    : "";
  const guardianSent = Boolean(latestGuardian?.kind==="campaign_deliver" && ["delivered","scheduled_url"].includes(String(latestGuardian?.stage||"")) && latestGuardian?.status==="completed");
  const guardianScheduled = Boolean(latestGuardian?.kind==="campaign_deliver" && latestGuardian?.stage==="scheduled_url" && latestGuardian?.status==="completed");
  // OWNER_AVITO_NEXT_CHECK_UX_V1: show the autonomous watcher schedule so the
  // owner knows BORIS is still working and never needs to press publish again.
  const guardianWaitingAvito = Boolean(latestGuardian?.kind==="campaign_deliver" && latestGuardian?.stage==="awaiting_scheduled_url_result" && ["queued","running","validating"].includes(String(latestGuardian?.status||"")));
  const guardianNextCheckText = latestGuardian?.scheduled_at ? new Date(String(latestGuardian.scheduled_at)).toLocaleString("ru-RU", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}) : "";
  const guardianPartial = Boolean(latestGuardian?.kind==="campaign_deliver" && ["publication_partial","publication_failed","publication_pending_review"].includes(String(latestGuardian?.stage||"")));
  const publicationReport:any = activation?.activation?.report || latestGuardian?.result?.report || null;
  const publicationScope:any = publicationReport?.scope || null;
  const publicationProblems:any[] = Array.isArray(publicationScope?.item_errors)
    ? publicationScope.item_errors
    : (Array.isArray(publicationReport?.item_errors) ? publicationReport.item_errors : []);
  const publicationSuccessful = Number(publicationScope?.active ?? publicationReport?.successful ?? 0);
  const publicationExpected = Number(publicationScope?.expected_total ?? publicationReport?.total ?? counts.all ?? items.length ?? 0);
  const publicationErrorCount = Number(publicationScope?.errors ?? publicationReport?.error ?? publicationProblems.length ?? 0);
  const publicationProblemText = (x:any) => {
    const raw=String(x?.message || "Avito не опубликовал изменение").replace(/<[^>]*>/g, "").trim();
    const section=String(x?.section || x?.status || "").toLowerCase();
    if(section.includes("fee_hard_limit")) return "Закончился лимит размещений Avito для этой категории. BORIS не включает платное размещение без вашего разрешения.";
    if(raw.toLowerCase().includes("товар уже продаётся")) return "Avito считает объявление повтором уже размещённого предложения. BORIS не создаёт дубль автоматически.";
    if(raw.toLowerCase().includes("автоподстановка номера телефона")) return "Avito изменил номер телефона при обработке объявления. BORIS показывает это как проблему, а не как успешное обновление.";
    return raw || "Avito вернул ошибку по объявлению.";
  };
  const officialXmlPassed = workflow?.official_xml_check?.status === "pass" || workflow?.official_xmlcheck?.status === "ok";
  const ownerReviewReady = Boolean(preview?.items?.length && validation?.ok && intake?.all_complete && officialXmlPassed);
  const preparedCount = Number(validation?.ready || workflow?.internal_validation?.ready || 0);
  const heroCount = items.filter((x:any)=>(x.payload?.images || []).length > 0).length;
  const photoAngleMode = workflow?.creative_mode === "photo_angles" || workflow?.banner_mode === "disabled";
  const generatedPhotoReady = Number(photoAnglesStatus?.generated ?? workflow?.generated_photo_ready ?? 0);
  const generatedPhotoTarget = Number(photoAnglesStatus?.target ?? workflow?.generated_photo_target ?? 20);
  const activationState = String(activation?.activation?.status || "").toLowerCase();
  const publicationCompleted = activationState === "completed";
  const publicationPartial = activationState === "partial";
  const publicationInFlight = ["processing","submitted","scheduled","scheduled_waiting","scheduled_pending_review","pending_review"].includes(activationState) || Boolean(latestGuardian?.kind==="campaign_deliver" && ["queued","running","validating"].includes(String(latestGuardian?.status||"")) && ["sending","awaiting_avito_result","awaiting_scheduled_url_result"].includes(String(latestGuardian?.stage||"")));
  // FRONTEND_PUBLICATION_LOCK_ALIGNS_BACKEND_V2: once Avito owns the
  // submitted scope, every frontend mutation stays locked exactly like the backend.
  // Terminal success/partial are immutable too, but are not presented as processing.
  const publicationLocked = publicationInFlight || publicationPartial || publicationCompleted;
  // Completed/partial publication stays immutable externally until a fresh owner
  // action, but the local BORIS card editor must remain usable. Editor saves create
  // a pending local revision; nothing is sent to Avito automatically.
  const cardEditorLocked = publicationInFlight;
  const publishedCount = Number(activation?.activation?.published || (publicationCompleted ? (feedControl?.items || counts.all || items.length) : 0));

  // CAMPAIGN_BUSINESS_STATUS_V1: translate technical workflow into the three
  // questions that matter to a client/owner: what BORIS is doing, what business
  // result this produces, and whether a human must act now.
  const hasMandatoryQuestions = Boolean((questions || []).length);
  const intakeNeedsHuman = Boolean((intake?.directions || []).length && !intake?.all_complete);
  let businessNow = "Готов принять задачу";
  let businessResult = "Из одного описания BORIS соберёт структуру кампании, объявления и проверку перед Avito.";
  let businessOwnerAction = "Опишите, что продаём, город и важные условия. Остальное BORIS сделает сам.";
  if (hasMandatoryQuestions) {
    businessNow = "Ждёт один обязательный факт";
    businessResult = "После вашего ответа BORIS продолжит подготовку сам — заново запускать кампанию не потребуется.";
    businessOwnerAction = "Ответьте только на вопрос, который BORIS не может безопасно определить без вас.";
  } else if (intakeNeedsHuman) {
    businessNow = "Уточняет обязательные параметры Avito";
    businessResult = "BORIS не отправит карточки с выдуманной категорией или обязательными полями.";
    businessOwnerAction = "Подтвердите только неизвестные параметры. После этого работа продолжится автоматически.";
  } else if (publicationCompleted) {
    businessNow = "Контролирует опубликованные объявления";
    businessResult = `${publishedCount || counts.all || items.length} объявлений опубликованы. Дальше BORIS контролирует активность и результат, а не просит вас следить вручную.`;
    businessOwnerAction = "Ничего. Вмешательство нужно только если BORIS не сможет исправить внешний сбой сам.";
  } else if (publicationPartial) {
    businessNow = "Разбирает ошибки Avito";
    businessResult = "Успешно опубликованные объявления не дублируются; проблемные сохранены с конкретными причинами.";
    businessOwnerAction = "Смотрите только те ошибки, которые требуют решения владельца. Остальное BORIS должен обработать сам.";
  } else if (publicationInFlight || guardianWaitingAvito || guardianScheduled) {
    businessNow = "Передал кампанию Avito и ждёт подтверждение";
    businessResult = "Страницу можно закрыть: BORIS сам проверит итог публикации и не запустит дубль повторным нажатием.";
    businessOwnerAction = "Ничего. Не нужно повторно нажимать размещение или вручную обновлять страницу.";
  } else if (ownerReviewReady) {
    businessNow = "Подготовка закончена";
    businessResult = `${counts.all || items.length} объявлений готовы к размещению: тексты, фото и обязательные проверки уже выполнены.`;
    businessOwnerAction = "Один раз посмотреть итог и подтвердить размещение.";
  } else if (guardianRunning || items.length) {
    businessNow = "Готовит кампанию";
    businessResult = "BORIS сам делает структуру, тексты, визуалы и проверки; готовые результаты сохраняются по ходу работы.";
    businessOwnerAction = "Ничего. Можно закрыть страницу и вернуться позже.";
  } else {
    const step = String(workflow?.step || "brief");
    if (step === "structure") {
      businessNow = "Собрал структуру кампании";
      businessResult = "BORIS уже понял направления и географию и переходит к созданию отдельных карточек объявлений.";
      businessOwnerAction = "Ничего. BORIS продолжает подготовку сам; повторно вводить задачу не нужно.";
    } else if (step === "texts") {
      businessNow = "Пишет тексты объявлений";
      businessResult = "BORIS готовит отдельные тексты под каждую карточку, а не один шаблон на всю кампанию.";
      businessOwnerAction = "Ничего. Подключайтесь только если BORIS запросит неизвестный факт.";
    } else if (step === "media") {
      businessNow = "Готовит фотографии и визуалы";
      businessResult = "BORIS подбирает и проверяет визуалы для карточек, чтобы не отправлять в Avito пустые или несоответствующие объявления.";
      businessOwnerAction = "Ничего. Ждите готовый результат.";
    } else if (step === "category" || step === "settings") {
      businessNow = "Настраивает Avito";
      businessResult = "BORIS подбирает категории, характеристики, цену и географию и останавливается только на неизвестных обязательных фактах.";
      businessOwnerAction = "Ничего, если нет отдельного вопроса BORIS.";
    } else if (step === "validation") {
      businessNow = "Проверяет кампанию перед отправкой";
      businessResult = "BORIS ищет ошибки до Avito, чтобы не тратить публикации на заведомо некорректные карточки.";
      businessOwnerAction = "Ничего. Дождитесь результата проверки.";
    } else if (step === "preview") {
      businessNow = "Показывает итог перед размещением";
      businessResult = "Все карточки собраны в одном месте для быстрой финальной проверки без ручной сборки кампании.";
      businessOwnerAction = "Посмотрите итог и подтвердите размещение, если всё верно.";
    } else if (step === "safe_feed") {
      businessNow = "Кампания готова к Avito";
      businessResult = "BORIS закончил подготовку и проверку объявлений — повторно собирать их вручную не нужно.";
      businessOwnerAction = "Только подтвердите размещение, если подтверждение ещё требуется.";
    }
  }

  const submittedSnapshot = activation?.activation?.submitted_snapshot || null;
  const reviewPageUrl = String(workflow?.review_page?.url || workflow?.canonical_combat_review?.url || "").trim();

  return (
      <Page className="boris-campaign-detail-reference-page">
        {cardConflict?<CampaignConflictDialog conflict={cardConflict} onClose={()=>setCardConflict(null)} onReload={()=>reloadConflict(cardConflict.itemId)} onRebase={rebaseConflict}/>:null}
        <PageHeader title={camp.name}
          subtitle={publicationCompleted ? `${publishedCount || counts.all || 0} объявлений опубликованы в Avito` : publicationPartial ? `${publishedCount} опубликовано · ${publicationErrorCount} с ошибками Avito` : ownerReviewReady ? `${counts.all || 0} объявлений готовы к вашей проверке` : `${counts.all || 0} объявлений · подготовка и редактирование`}
          actions={publicationCompleted ? <span style={{alignSelf:"center",padding:"6px 10px",borderRadius:999,background:"#ecfdf3",border:"1px solid #abefc6",color:"#067647",fontSize:12,fontWeight:800}}>Опубликовано в Avito</span> : publicationPartial ? <span style={{alignSelf:"center",padding:"6px 10px",borderRadius:999,background:"#fff7ed",border:"1px solid #fed7aa",color:"#b54708",fontSize:12,fontWeight:800}}>Опубликовано частично</span> : ownerReviewReady ? <span style={{alignSelf:"center",padding:"6px 10px",borderRadius:999,background:"#ecfdf3",border:"1px solid #abefc6",color:"#067647",fontSize:12,fontWeight:800}}>Готово к размещению</span> : items.length ? <span style={{alignSelf:"center",padding:"6px 10px",borderRadius:999,background:"#eef4ff",border:"1px solid #d7e5ff",color:"#315bea",fontSize:12,fontWeight:800}}>BORIS готовит</span> : null} />
        {err ? <Alert kind="danger">{err}</Alert> : null}
        {editorJob ? <div role="status" style={{padding:12,borderRadius:12,background:"#FFF7E6",marginBottom:12}}><b>{editorJob.status==="completed"?"Завершено за ":editorJob.status==="running"?"BORIS работает · ":String(editorJob.state_text||"Задача сохранена")+" · "}{editorDuration(editorJob.duration_total_seconds)}</b><div>{editorJob.progress_done||0} из {editorJob.progress_total||0} · можно обновить страницу</div>{editorJob.duration_active_seconds!=null?<div>Работа BORIS: {editorDuration(editorJob.duration_active_seconds)} · ожидание: {editorDuration(editorJob.duration_waiting_seconds)}</div>:null}</div>:null}
        {guardianScheduled ? <Alert kind="success">Пакет объявлений проверен и подключён к Avito. Для этого аккаунта Avito забирает его автоматически по своему расписанию — повторно нажимать размещение не нужно.</Alert> : null}
        {guardianWaitingAvito ? <Alert kind="success">BORIS ждёт, когда Avito заберёт подготовленный пакет объявлений. {guardianNextCheckText ? <>Следующая автоматическая проверка: <b>{guardianNextCheckText}</b>. </> : null}Повторно размещать объявления не нужно — BORIS проверит результат сам.</Alert> : null}
        {guardianPartial ? <Alert kind="danger">Avito завершил обработку не полностью. BORIS сохранил итоговый отчёт и причины по проблемным объявлениям — статус не маскируется как успешная публикация.</Alert> : null}

        <Card data-testid="campaign-business-status" className="boris-campaign-business-status" style={{marginBottom:space.md,padding:16}}>
          <div style={{...font.h2,color:color.heading,marginBottom:10}}><b>Что это даёт вам и клиенту сейчас</b></div>
          <div className="boris-campaign-business-status-grid">
            <div className="boris-campaign-business-status-cell">
              <div className="boris-campaign-business-status-label">BORIS сейчас</div>
              <div className="boris-campaign-business-status-value">{businessNow}</div>
            </div>
            <div className="boris-campaign-business-status-cell">
              <div className="boris-campaign-business-status-label">Практический результат</div>
              <div className="boris-campaign-business-status-value">{businessResult}</div>
            </div>
            <div className="boris-campaign-business-status-cell" data-owner-action={businessOwnerAction.startsWith("Ничего") ? "none" : "needed"}>
              <div className="boris-campaign-business-status-label">От вас сейчас</div>
              <div className="boris-campaign-business-status-value">{businessOwnerAction}</div>
            </div>
          </div>
        </Card>

        {ownerCostSummary ? (
          <Card data-testid="campaign-private-costs" style={{marginBottom:space.md,padding:16,border:"1px solid #D8E3F2",background:"#FBFDFF"}}>
            <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"flex-start",flexWrap:"wrap"}}>
              <div>
                <div style={{...font.h2,color:color.heading}}><b>Себестоимость Campaign</b></div>
                <div style={{...font.small,color:color.muted,marginTop:3}}>Видно только владельцу платформы BORIS</div>
              </div>
              <span style={{fontSize:11,fontWeight:900,padding:"5px 8px",borderRadius:999,background:"#EEF4FF",color:"#315BEA"}}>PRIVATE</span>
            </div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(170px,1fr))",gap:8,marginTop:12}}>
              <div style={{padding:"10px 11px",borderRadius:11,background:"#fff",border:"1px solid #E4E7EC"}}>
                <div style={{fontSize:11,color:"#667085"}}>Атрибутировано провайдерам</div>
                <b style={{display:"block",marginTop:3,color:"#172033"}}>{Number(ownerCostSummary.attributed_provider_spend_rub||0).toFixed(2)} ₽</b>
                <small style={{color:"#667085"}}>{Number(ownerCostSummary.attributed_provider_calls||0)} вызовов</small>
              </div>
              <div style={{padding:"10px 11px",borderRadius:11,background:"#fff",border:"1px solid #E4E7EC"}}>
                <div style={{fontSize:11,color:"#667085"}}>Текущие баннеры</div>
                <b style={{display:"block",marginTop:3,color:"#172033"}}>{Number(ownerCostSummary.current_banner_artifact_cost_rub||0).toFixed(2)} ₽</b>
                <small style={{color:"#667085"}}>{Number(ownerCostSummary.costed_banner_items||0)} из {Number(ownerCostSummary.banner_items||0)} с известной стоимостью</small>
              </div>
              <div style={{padding:"10px 11px",borderRadius:11,background:"#fff",border:"1px solid #E4E7EC"}}>
                <div style={{fontSize:11,color:"#667085"}}>Средняя стоимость баннера</div>
                <b style={{display:"block",marginTop:3,color:"#172033"}}>{ownerCostSummary.cost_per_costed_banner_item_rub==null?"—":Number(ownerCostSummary.cost_per_costed_banner_item_rub).toFixed(2)+" ₽"}</b>
              </div>
              <div style={{padding:"10px 11px",borderRadius:11,background:"#fff",border:"1px solid #E4E7EC"}}>
                <div style={{fontSize:11,color:"#667085"}}>Покрытие стоимости</div>
                <b style={{display:"block",marginTop:3,color:Number(ownerCostSummary.banner_cost_coverage_pct||0)>=99?"#067647":"#B54708"}}>{Number(ownerCostSummary.banner_cost_coverage_pct||0).toFixed(1)}%</b>
              </div>
            </div>
            <div style={{...font.small,color:color.muted,marginTop:9,lineHeight:1.5}}>
              Provider spend считается только по вызовам с явной привязкой к этой кампании. Старые или неатрибутированные вызовы BORIS не приписывает по времени, поэтому сумма провайдеров является нижней границей фактических расходов.
            </div>
          </Card>
        ) : null}

        {guardianPartial && publicationReport ? (
          <Card style={{marginBottom:space.md,border:"1px solid #fecdca",background:"#fffafa"}}>
            <div style={{...font.h2,color:color.heading,marginBottom:6}}><b>Итог публикации Avito</b></div>
            <div style={{...font.body,color:color.heading,lineHeight:1.65,marginBottom:publicationProblems.length?10:0}}>
              Подтверждено: <b>{publicationSuccessful}/{publicationExpected || counts.all || items.length}</b>
              {publicationErrorCount ? <> · Проблем: <b>{publicationErrorCount}</b></> : null}
            </div>
            {publicationProblems.length ? (
              <div style={{display:"grid",gap:8}}>
                {publicationProblems.slice(0,8).map((p:any,idx:number)=>(
                  <div key={String(p?.ad_id||p?.avito_id||idx)} style={{padding:"9px 11px",borderRadius:10,background:"#fff",border:"1px solid #fee4e2"}}>
                    <div style={{fontSize:12,fontWeight:800,color:color.heading,marginBottom:3}}>
                      Объявление {p?.avito_id ? `№ ${p.avito_id}` : p?.ad_id ? String(p.ad_id) : `#${idx+1}`}
                    </div>
                    <div style={{...font.small,color:color.heading,lineHeight:1.5}}>{publicationProblemText(p)}</div>
                  </div>
                ))}
                {publicationProblems.length>8 ? <div style={{...font.small,color:color.muted}}>Ещё проблем: {publicationProblems.length-8}. Полный отчёт сохранён BORIS.</div> : null}
              </div>
            ) : <div style={{...font.small,color:color.muted}}>Avito ещё формирует детальные причины. BORIS продолжает хранить фактический статус без ложного успеха.</div>}
          </Card>
        ) : null}

        <Card className="boris-campaign-review-summary" style={{ marginBottom: space.md }}>
          {ownerReviewReady ? (
            <div id="owner-review-summary">
              <div style={{...font.h2,color:color.heading,marginBottom:6}}><b>{publicationCompleted ? `${publishedCount || counts.all || items.length} объявлений опубликовано в Avito` : publicationPartial ? `${publishedCount}/${counts.all || items.length} объявлений опубликовано` : `${counts.all || items.length} объявлений подготовлено`}</b></div>
              <div style={{...font.body,color:color.heading,lineHeight:1.7}}>
                <div>Тексты: <b>готовы</b> · Фото: <b>{heroCount}/{counts.all || items.length}</b>{photoAngleMode ? <> · Новые ИИ-ракурсы: <b>{generatedPhotoReady}/{generatedPhotoTarget}</b></> : <> · Баннеры: <b>{items.filter((x:any)=>Boolean(x.payload?.banner_url)).length}/{counts.all || items.length}</b></>}</div>
                <div>Проверка BORIS: <b>{preparedCount || counts.all || items.length}/{counts.all || items.length}</b> · Avito: <b style={{color:(publicationCompleted || officialXmlPassed)?"#067647":"#b54708"}}>{publicationCompleted?"опубликовано":publicationPartial?"опубликовано частично":publicationInFlight?"обрабатывает отправку":officialXmlPassed?"готово":"BORIS завершает проверку"}</b></div>
                {preparationTimeText ? <div>Время полной подготовки: <b>{preparationTimeText}</b></div> : null}
                <div>От вас: <b>{publicationCompleted?"ничего — BORIS сам контролирует активность и KPI":publicationPartial?"повторно всю кампанию не отправлять — BORIS хранит причины ошибок":publicationInFlight?"ничего — BORIS сам ждёт итог Avito":"посмотреть результат и подтвердить размещение"}</b></div>
              </div>
              <div style={{...font.small,marginTop:space.sm}}>{publicationCompleted?"Размещение подтверждено Avito. Повторная отправка заблокирована; BORIS автоматически следит, чтобы объявления оставались активными, и собирает результат после публикации.":publicationPartial?"Avito завершил обработку частично. BORIS не маскирует это как полный успех и не дублирует всю кампанию.":publicationInFlight?"Кампания уже передана Avito. Закрывать страницу безопасно — BORIS сам дождётся итогового отчёта.":"BORIS уже сделал основную работу. Просмотрите готовые карточки ниже и подтвердите размещение одной кнопкой в конце — менять или заполнять что-либо нужно только если Avito требует факт, который BORIS не может определить без вас."}</div>
              {reviewPageUrl ? <div style={{marginTop:space.sm}}><a href={reviewPageUrl} target="_blank" rel="noopener noreferrer" style={{display:"inline-flex",alignItems:"center",gap:7,padding:"9px 13px",borderRadius:10,background:"#eef4ff",border:"1px solid #c7d7fe",color:"#315bea",fontSize:13,fontWeight:850,textDecoration:"none"}}>Открыть результат без входа ↗</a></div> : null}
            </div>
          ) : <>
          {(items.length || latestPreparation || guardianRunning || guardianNeedsAction) ? (
            <div style={{padding:"14px 16px",borderRadius:14,background:guardianNeedsAction?"#fffaf0":"#f8fbff",border:"1px solid "+(guardianNeedsAction?"#f5d69a":"#dbeafe"),marginBottom:space.md}}>
              <div style={{...font.h2,color:color.heading,marginBottom:6}}><b>{guardianNeedsAction?"BORIS ждёт вашего действия":"BORIS готовит кампанию"}</b></div>
              <div style={{...font.body,color:color.heading,lineHeight:1.65}}>{borisRunMessage || (guardianNeedsAction?"Подготовка поставлена на безопасную паузу. Уже готовые данные сохранены.":"BORIS продолжает автоматически. Готовые изменения сохраняются по мере обработки.")}</div>
              <div data-testid="campaign-durable-progress" style={{marginTop:12}}>
                <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",flexWrap:"wrap",...font.small,color:color.heading}}>
                  <span>Этап: <b>{humanizeBusinessText(guardianStage,"подготовка")}</b>{stageItemsVisible ? ` · ${stageItemDone}/${stageItemTotal}` : guardianTotal>0 ? ` · ${guardianDone}/${guardianTotal}` : ""}</span>
                  <span style={{display:"inline-flex",gap:12,alignItems:"center",flexWrap:"wrap"}}>{latestPreparation?<span>Прошло: <b>{preparationTimeText}</b></span>:null}<b>{guardianPercent}%</b></span>
                </div>
                <div role="progressbar" aria-label="Прогресс подготовки кампании" aria-valuemin={0} aria-valuemax={100} aria-valuenow={guardianPercent} style={{height:10,borderRadius:999,background:"#E8EEF7",overflow:"hidden",marginTop:7}}>
                  <div style={{height:"100%",width:`${Math.max(3,guardianPercent)}%`,background:guardianNeedsAction?"#D97706":"#F59E0B",borderRadius:999,transition:"width .25s ease"}} />
                </div>
                <div style={{...font.small,marginTop:7,color:color.muted}}>
                  {guardianNeedsAction
                    ? "Задача не зависла: она остановлена на конкретном обязательном действии. Уже готовые объявления сохранены."
                    : guardianWaitMessage || "Прогресс хранится на сервере. Можно перейти в другой аккаунт, обновить или закрыть страницу — эта кампания продолжит работу на сервере."}
                  {guardianLastProgressText ? <> Последнее сохранение прогресса: <b>{guardianLastProgressText}</b>.</> : null}
                </div>
                {latestPreparation && (preparationActiveText || preparationWaitingText || etaText || progressItemsPerMinute!=null) ? (
                  <div data-testid="campaign-performance-timing" style={{marginTop:8,display:"flex",gap:12,flexWrap:"wrap",...font.small,color:color.heading}}>
                    {preparationActiveText ? <span>BORIS работал: <b>{preparationActiveText}</b></span> : null}
                    {preparationWaitingText ? <span>Ожидание: <b>{preparationWaitingText}</b></span> : null}
                    {preparationProviderText ? <span>Внешние сервисы: <b>{preparationProviderText}</b></span> : null}
                    {progressItemsPerMinute!=null && Number.isFinite(progressItemsPerMinute) ? <span>Скорость: <b>{progressItemsPerMinute.toFixed(1)} объявл./мин</b></span> : null}
                    {etaText ? <span>Осталось примерно: <b>{etaText}</b></span> : null}
                  </div>
                ) : null}
                {orderRequested > 0 ? (
                  <div data-testid="campaign-order-progress" style={{marginTop:10,padding:"10px 12px",borderRadius:10,background:"white",border:"1px solid #dbeafe"}}>
                    <div style={{fontWeight:900,color:color.heading}}>Заказ: {orderReady}/{orderRequested} готово</div>
                    <div style={{...font.small,marginTop:5,color:color.heading}}>
                      Создано <b>{orderCreated}/{orderRequested}</b> · QA <b>{orderQa}/{orderRequested}</b> · Готово <b>{orderReady}/{orderRequested}</b>{orderBlocked ? <> · Требует внимания <b>{orderBlocked}</b></> : null}
                    </div>
                    {Object.entries(orderByDirection).map(([name,raw]:any)=><div key={name} style={{...font.small,marginTop:4,color:color.muted}}>{name}: READY <b>{Number(raw?.ready||0)} из {Number(raw?.requested||0)} объявлений</b></div>)}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
          <div style={{ ...font.h2, color: color.heading, marginBottom: 4 }}><b>Что нужно продать или продвигать?</b></div>
          <div style={{ ...font.small, marginBottom: space.sm }}>Напишите всё одним сообщением обычным языком: товары или услуги, города, цены и важные условия. BORIS сам подготовит структуру, тексты и подходящие визуалы. Если Avito требует неизвестный факт — BORIS спросит только его.</div>
          <div style={{ ...font.small, marginBottom: space.sm }}>
            Сейчас: <b>{STEP_RU[workflow?.step || "brief"] || "Подготовка проекта"}</b>{workflow?.updated_at ? " · сохранено " + new Date(workflow.updated_at).toLocaleString("ru-RU") : ""}. Работа сохраняется автоматически.
          </div>
          <textarea value={brief} rows={4} placeholder={'Например:\nремонт фар BMW\nремонт фар Mercedes\nзамена стекол фар\nСанкт-Петербург'} onChange={(e) => setBrief(e.target.value)} style={{ width: "100%", borderRadius: 10, padding: 10, border: "1px solid " + color.line, font: "inherit", marginBottom: space.sm }} />
          <div data-testid="campaign-quantity-row" style={{display:"flex",flexWrap:"wrap",gap:10,alignItems:"end",marginBottom:space.sm,minWidth:0}}>
            <label style={{display:"grid",gap:5,flex:"1 1 220px",maxWidth:260,minWidth:0}}><span style={{fontSize:12,fontWeight:800,color:color.heading}}>Сколько объявлений нужно</span><Input value={targetQuantity} placeholder="Например, 20" onChange={(e:any)=>setTargetQuantity(String(e.target.value||"").replace(/\D/g,"").slice(0,4))}/></label>
            <div style={{...font.small,color:color.muted,paddingBottom:7,flex:"999 1 280px",minWidth:0}}>Если оставить пустым, BORIS определит количество из задачи. Если указать число — в кампании будет создано именно столько отдельных карточек, до 1000.</div>
          </div>
          <div style={{ display: "flex", gap: space.sm, alignItems: "center", flexWrap: "wrap", marginBottom: space.sm }}>
            <Button
              onClick={workflow?.matrix?.length ? continueBorisPreparation : startBoris}
              disabled={publicationLocked || !(brief.trim() || (referenceMode === "preserve_text" && referenceText.trim())) || !!feedBusy || guardianRunning}
              style={{ minWidth: 190 }}
            >
              {guardianRunning ? "BORIS продолжает автоматически. Готовые изменения сохраняются по мере обработки." : feedBusy === "boris-run" ? "BORIS запускает…" : workflow?.matrix?.length ? "Продолжить подготовку" : "Подготовить кампанию"}
            </Button>
            <span style={{ ...font.small, color: color.muted }}>
              BORIS сам разберёт задачу, подготовит структуру, тексты, визуалы и проверку. Вы подключаетесь только если нужны данные или согласование.
            </span>
          </div>
          {borisRunMessage ? (
            <div style={{ marginBottom: space.sm, padding: "10px 12px", borderRadius: 10, background: "#f8fbff", border: "1px solid #dbeafe", ...font.small, color: color.heading }}>
              <b>{feedBusy === "boris-run" ? "BORIS работает: " : "BORIS: "}</b>{borisRunMessage}
            </div>
          ) : null}

          <details style={{marginTop:12,border:"1px solid #e4e7ec",borderRadius:14,padding:"12px 14px",background:"#fff"}}>
            <summary style={{cursor:"pointer",...font.bodyStrong,color:color.heading}}>Использовать свой текст или примеры</summary>
            <div style={{...font.small,marginTop:5}}>Вставьте свой готовый текст — BORIS сделает из него нужное количество уникальных объявлений, сохраняя факты. Также можно добавить ссылки или скриншоты примеров.  BORIS возьмёт структуру, боли, выгоды и подачу, но не перенесёт чужие неподтверждённые факты.</div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(260px,1fr))",gap:10,marginTop:10}}>
              <label style={{display:"grid",gap:5}}><span style={{fontSize:12,fontWeight:700}}>Ссылки на понравившиеся объявления</span><textarea value={referenceLinks} onChange={(e)=>setReferenceLinks(e.target.value)} rows={4} placeholder={'По одной ссылке в строке\nhttps://www.avito.ru/...'} style={{width:"100%",border:"1px solid "+color.line,borderRadius:10,padding:10,font:"inherit",resize:"vertical"}} /></label>
              <label style={{display:"grid",gap:5}}><span style={{fontSize:12,fontWeight:700}}>Описание или текст объявления</span><textarea value={referenceText} onChange={(e)=>setReferenceText(e.target.value)} rows={4} placeholder="Вставьте текст, который нравится, или свой текст, который нужно аккуратно оформить" style={{width:"100%",border:"1px solid "+color.line,borderRadius:10,padding:10,font:"inherit",resize:"vertical"}} /></label>
              <label style={{display:"grid",gap:5,alignContent:"start"}}><span style={{fontSize:12,fontWeight:700}}>Или загрузить свой текст</span><input type="file" accept=".docx,.txt,.md,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown" onChange={(e)=>{const f=e.target.files?.[0];if(f)void loadOwnTextFile(f);e.currentTarget.value="";}} style={{minHeight:42,padding:"8px 0"}}/><span style={{fontSize:11,color:color.muted}}>Word DOCX, TXT или MD. BORIS прочитает исходник, сохранит факты и сделает нужное количество действительно разных вариантов.</span>{referenceTextFileName?<span style={{fontSize:11,color:"#067647",fontWeight:700}}>✓ {referenceTextFileName}</span>:null}</label>
            </div>
            <div style={{marginTop:10,padding:12,border:"1px solid #b2ddff",borderRadius:12,background:"#f5fbff"}}>
              <div style={{fontSize:12,fontWeight:800,color:color.heading}}>За счёт каких параметров можно уникализировать объявления?</div>
              <div style={{fontSize:11,color:color.muted,marginTop:4}}>Напишите своими словами допустимые параметры и диапазоны. BORIS сам распределит разные комбинации по объявлениям и добавит конкретный пример внизу каждого текста. Ничего за пределами указанных данных придумывать нельзя.</div>
              <textarea value={uniquenessParameters} onChange={(e)=>setUniquenessParameters(e.target.value)} rows={3} placeholder={'Например: площадь 50–200 м²; слой штукатурки 10–40 мм; квартира/дом/коммерция; высота до 4 м. Или: кухня 2,5–5 м; фасады МДФ/ЛДСП; цвета белый/серый/графит.'} style={{width:"100%",marginTop:8,border:"1px solid "+color.line,borderRadius:10,padding:10,font:"inherit",resize:"vertical"}} />
            </div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:10,marginTop:10,alignItems:"end"}}>
              <label style={{display:"grid",gap:5}}><span style={{fontSize:12,fontWeight:700}}>Что сделать с примерами</span><BorisSelect value={referenceMode} onChange={setReferenceMode} placeholder="Что сделать с примерами" options={[{value:"inspired",label:"Сделать похожее по подаче, но уникальное"},{value:"preserve_text",label:"Оставить мой текст почти как есть, улучшить структуру и иконки"}]}/></label>
              <label style={{display:"grid",gap:5}}><span style={{fontSize:12,fontWeight:700}}>Скриншоты объявлений</span><input type="file" multiple accept="image/*" onChange={(e)=>setReferenceFiles(Array.from(e.target.files || []).slice(0,8))} style={{minHeight:42,padding:"8px 0"}} /></label>
              <Button kind="ghost" onClick={saveReferences} disabled={publicationLocked || !!feedBusy || (!referenceLinks.trim() && !referenceText.trim() && !uniquenessParameters.trim() && !referenceFiles.length && !referenceMediaIds.length)}>{feedBusy==="references"?"Сохраняю и анализирую…":"Сохранить примеры"}</Button>
            </div>
            <div style={{...font.small,marginTop:8}}>{referenceFiles.length?`Новых скриншотов: ${referenceFiles.length}. `:""}{referenceMediaIds.length?`Сохранено скриншотов: ${referenceMediaIds.length}. `:""}Для Avito BORIS не вставляет HTML/Markdown-жирный: ключевые строки выделяются структурой, короткими заголовками, иконками и читаемыми блоками.</div>
            {referenceSaved?<div style={{...font.small,marginTop:6,color:"#067647"}}><b>{referenceSaved}</b></div>:null}
          </details>

          {ownTextFlowReady ? <section style={{marginTop:12,border:"1px solid #dbeafe",borderRadius:14,padding:14,background:"#f8fbff"}}>
            <div style={{...font.bodyStrong,color:color.heading}}>Дальше — визуалы для этих же объявлений</div>
            <div style={{...font.small,marginTop:4,color:color.muted}}>Исходный текст уже привязан к кампании. После создания вариантов выберите, откуда BORIS возьмёт изображения. Всё остаётся внутри выбранного аккаунта.</div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(210px,1fr))",gap:9,marginTop:10}}>
              <button type="button" onClick={generateBannersFromOwnTextFlow} disabled={publicationLocked} style={{textAlign:"left",border:"1px solid #c7d7fe",background:"white",borderRadius:12,padding:12,cursor:publicationLocked?"not-allowed":"pointer",opacity:publicationLocked ? .55 : 1}}><b>✨ Создать баннеры и картинки</b><div style={{fontSize:11,color:"#667085",marginTop:4}}>ИИ создаст визуалы под конкретные объявления.</div></button>
              <button type="button" onClick={collectStockForCampaignItems} disabled={publicationLocked} style={{textAlign:"left",border:"1px solid #c7d7fe",background:"white",borderRadius:12,padding:12,cursor:publicationLocked?"not-allowed":"pointer",opacity:publicationLocked ? .55 : 1}}><b>🖼 Собрать со стоков</b><div style={{fontSize:11,color:"#667085",marginTop:4}}>Подбор по теме с проверкой соответствия объявлению.</div></button>
              <button type="button" onClick={()=>photoStackInputRef.current?.click()} disabled={publicationLocked} style={{textAlign:"left",border:"1px solid #c7d7fe",background:"white",borderRadius:12,padding:12,cursor:publicationLocked?"not-allowed":"pointer",opacity:publicationLocked ? .55 : 1}}><b>📁 Загрузить свои фото</b><div style={{fontSize:11,color:"#667085",marginTop:4}}>Свои изображения распределятся по объявлениям.</div></button>
            </div>
          </section> : null}

          {questions.length ? (
            <div style={{ borderTop: "1px solid " + color.line, paddingTop: space.sm, marginTop: space.sm }}>
              <div style={{ ...font.body, color: color.heading, marginBottom: space.sm }}><b>Нужно уточнить только недостающие данные</b></div>
              <div style={{ display: "grid", gap: space.sm }}>
                {questions.filter((qq:any)=>avitoFieldNeedsInput({...qq,field:qq.key})).map((qq:any) => (
                  <label key={qq.key} style={{ display: "grid", gap: 4 }}>
                    <span style={font.small}>{avitoFieldLabel({field:qq.key,label:qq.label})}</span>
                    {qq.allowed_values?.length ? (qq.multiple ? (
                      <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>{qq.allowed_values.map((raw:any)=>{const option=String(raw);const current=Array.isArray(answers[qq.key])?answers[qq.key].map(String):[];const selected=current.includes(option);return <button type="button" key={option} onClick={()=>setAnswers({...answers,[qq.key]:selected?current.filter((x:string)=>x!==option):[...current,option]})} style={{border:selected?"1px solid #315bea":"1px solid "+color.line,background:selected?"#eef4ff":"white",borderRadius:9,padding:"8px 10px",fontSize:12,fontWeight:700}}>{selected?"✓ ":""}{option}</button>})}</div>
                    ) : (
                      <BorisSelect value={String(answers[qq.key]??"")} onChange={value=>setAnswers({...answers,[qq.key]:value})} placeholder="Выберите…" options={[{value:"",label:"Выберите…"},...qq.allowed_values.map((v:any)=>({value:String(v),label:String(v)}))]}/>
                    )) : (
                      <Input value={answers[qq.key] ?? ""} placeholder={qq.key === "cities" ? "Например: Санкт-Петербург, Пушкин" : "Введите значение"} onChange={(e:any)=>setAnswers({...answers,[qq.key]:e.target.value})} />
                    )}
                  </label>
                ))}
              </div>
              <Button onClick={saveAnswers} disabled={publicationLocked || !!feedBusy || !Object.keys(answers).length} style={{ marginTop: space.sm }}>{feedBusy === "answers" ? "Сохраняю…" : "Сохранить уточнения"}</Button>
            </div>
          ) : null}

          {workflow?.matrix?.length ? (
            <div className="boris-campaign-client-plan" style={{ marginTop: space.md }}>
              <div style={{ ...font.body, color: color.heading }}><b>Кампания готовится</b></div>
              <div style={{ ...font.small, marginTop: 4, color: color.muted }}>BORIS уже разложил задачу на объявления. Ниже только то, что важно для запуска.</div>
              <div className="boris-campaign-client-plan-grid">
                <div><span>Направления</span><b>{workflow.directions?.length || 0}</b><small>товаров или услуг</small></div>
                <div><span>Объявления</span><b>{workflow.items_planned || workflow.matrix.length}</b><small>будет подготовлено</small></div>
                <div><span>География</span><b>{(workflow.cities || []).length || "—"}</b><small>{(workflow.cities || []).join(", ") || "нужно указать город"}</small></div>
                <div><span>Следующий шаг</span><b>{questions.length ? "Ответить" : intake?.all_complete === false ? "Заполнить" : "Проверить"}</b><small>{questions.length ? "недостающие данные" : intake?.all_complete === false ? "параметры Avito" : "готовые объявления"}</small></div>
              </div>
            </div>
          ) : null}

          </>}

          {intake?.directions?.length && (!items.length || !intake.all_complete) ? (
            <div id="feed-direction-intake-status" style={{marginTop:space.md,borderTop:"1px solid "+color.line,paddingTop:space.md,display:"flex",justifyContent:"space-between",gap:space.sm,alignItems:"center",flexWrap:"wrap"}}>
              <div>
                <div style={{...font.body,color:color.heading}}><b>BORISу нужно уточнить несколько фактов</b></div>
                <div style={{...font.small,marginTop:3}}>{intake.all_complete?"Все направления заполнены — можно создавать объявления.":`Нужно заполнить ${intake.directions.filter((d:any)=>!d.complete).length} из ${intake.directions.length} направлений до создания объявлений.`}</div>
              </div>
              <div style={{display:"flex",gap:8,flexWrap:"wrap",alignItems:"center"}}>{!intake.all_complete?<Button onClick={()=>{const idx=Math.max(0,(intake.directions||[]).findIndex((d:any)=>!d.complete));setIntakeStep(idx>=0?idx:0);setShowIntakeModal(true);}}>Уточнить и продолжить</Button>:<span style={{...font.small,color:"#067647",fontWeight:700}}>✓ Параметры сохранены. BORIS может продолжать подготовку автоматически.</span>}</div>
            </div>
          ) : null}

          {false && workflow?.directions?.length && !ownerReviewReady && !items.some((x:any)=>(x.payload?.images || []).length) ? (
            <div style={{marginTop:space.md,borderTop:"1px solid "+color.line,paddingTop:space.md}}>
              <div style={{...font.body,color:color.heading}}><b>Фотографии со стоков</b></div>
              <div style={{...font.small,marginTop:3}}>BORIS подставит направление кампании как поисковый запрос. Можно уточнить запрос вручную — найденные фото сохранятся только в медиатеку выбранного аккаунта.</div>
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:8,alignItems:"end",marginTop:9}}>
                <Input value={stockQuery} placeholder={(workflow.directions||[]).join(", ") || "Что искать на стоках"} onChange={(e:any)=>setStockQuery(e.target.value)} />
                <BorisSelect value={stockSource} onChange={setStockSource} placeholder="Библиотека фотографий" options={["pexels","pixabay","unsplash"].map(value=>({value,label:value[0].toUpperCase()+value.slice(1)}))}/>
                <input type="number" min={3} max={40} value={stockCount} onChange={(e)=>setStockCount(Math.max(3,Math.min(40,Number(e.target.value)||12)))} style={{minHeight:42,border:"1px solid "+color.line,borderRadius:9,padding:"0 9px"}} />
                <Button kind="ghost" onClick={collectStock} disabled={publicationLocked || !!feedBusy}>{feedBusy==="stock"?"Ищу…":"Собрать фото"}</Button>
              </div>
            </div>
          ) : null}

          {false && mediaPool.length ? (
            <div style={{ marginTop: space.md }}>
              <div style={{ ...font.small, marginBottom: 6 }}>Выберите объявления ниже, затем нажмите изображение — оно станет главным и сразу запишется в карточку объявления.</div>
              <div style={{ display:"flex", gap:8, overflowX:"auto" }}>
                {mediaPool.map((m:any)=><button key={m.id} onClick={()=>attachMedia(m.id)} style={{ border:"1px solid " + color.line, borderRadius:10, padding:4, background:"white", cursor:"pointer" }} title={m.filename}><img loading="lazy" decoding="async" src={m.thumb_url || m.preview_url || m.original_url} alt={m.filename || "media"} style={{ width:90, height:70, objectFit:"cover", borderRadius:8 }} /></button>)}
              </div>
            </div>
          ) : null}

          {false && items.length && !ownerReviewReady ? (
            <div style={{ marginTop: space.md, borderTop: "1px solid " + color.line, paddingTop: space.md }}>
              <div style={{...font.body,color:color.heading,marginBottom:6}}><b>Цена и география</b></div>
              <div style={{...font.small,marginBottom:space.sm}}>Если объявления выбраны ниже — значение применится только к ним. Иначе можно применить ко всему направлению или ко всей кампании.</div>
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:space.sm}}>
                <Input value={bulkPrice} placeholder="Общая цена, ₽" onChange={(e:any)=>setBulkPrice(e.target.value)} />
                <Input value={bulkCity} placeholder="Город / география" onChange={(e:any)=>setBulkCity(e.target.value)} />
                <BorisSelect value={bulkDirection} onChange={setBulkDirection} placeholder="Вся кампания" options={[{value:"",label:"Вся кампания"},...Array.from(new Set(items.map((x:any)=>x.segment).filter(Boolean))).map((d:any)=>({value:String(d),label:String(d)}))]}/>
              </div>
              <Button kind="ghost" onClick={()=>void previewBulkTransform()} disabled={publicationLocked || !!feedBusy} style={{marginTop:space.sm}}>Посмотреть результат</Button>
            </div>
          ) : null}

          {false && validation ? (
            <div style={{ marginTop: space.md, borderTop: "1px solid " + color.line, paddingTop: space.md, display: "flex", gap: space.md, alignItems: "center", flexWrap: "wrap" }}>
              <div style={{...font.body,color:color.heading}}><b>Проверка объявлений: {validation.ready}/{validation.total} готовы</b></div>
              {(() => {
                const attention = (validation.items || []).filter((x:any) => !x.valid || x.warnings?.length).length;
                const errors = (validation.items || []).filter((x:any) => !x.valid).length;
                return <>
                  <span style={{...font.small,color:errors ? "#b42318" : "#067647"}}>Ошибок: {errors}</span>
                  <span style={font.small}>Требуют внимания: {attention}</span>
                </>;
              })()}
            </div>
          ) : null}
          {preview?.items?.length ? <div id="owner-review-grid" className="boris-campaign-owner-review boris-campaign-reference-workspace" style={{ marginTop: space.md, scrollMarginTop: 16 }}>
            <section className="boris-campaign-reference-hero" aria-label="Варианты кампании" style={{display:"none"}} aria-hidden="true">
              <div className="boris-campaign-reference-hero-head">
                <div>
                  <div className="boris-campaign-reference-kicker">РЕКЛАМНАЯ КАМПАНИЯ</div>
                  <h2>Готовые варианты объявлений</h2>
                  <p>BORIS подготовил разные рекламные посылы. Нажмите на карточку, чтобы перейти к соответствующему объявлению ниже.</p>
                </div>
                <Button kind="ghost" onClick={generateBanners} disabled={publicationLocked || !!feedBusy}>{feedBusy==="banners"?"BORIS создаёт баннеры…":"Сгенерировать ещё"}</Button>
              </div>
              <div className="boris-campaign-reference-scenarios">
                {preview.items.slice(0,5).map((p:any,index:number)=>{
                  const src=items.find((x:any)=>x.id===p.id);
                  const goals=["Получить консультацию","Начать работу","Взять номер телефона","Начать сотрудничество","Получить обращение"];
                  return <button type="button" key={p.id} className="boris-campaign-reference-scenario" onClick={()=>{setSelectedItemIds([p.id]);document.getElementById(`campaign-ref-item-${p.id}`)?.scrollIntoView({behavior:"smooth",block:"center"});}}>
                    <span className="boris-campaign-reference-scenario-icon">{String(index+1).padStart(2,"0")}</span>
                    <span className="boris-campaign-reference-scenario-copy"><strong>{String(p.title||src?.segment||"Объявление").replace(/[{}]/g,"").split("|")[0]}</strong><small>{src?.segment || p.category || "Рекламный посыл"}</small><em>Цель: {goals[index] || "получить обращение"}</em></span>
                    {(p.banner || p.images?.[0]) ? <img loading="lazy" decoding="async" src={p.banner || p.images[0]} alt="" aria-hidden="true" /> : null}
                    <span className="boris-campaign-reference-scenario-arrow">›</span>
                  </button>;
                })}
              </div>
            </section>

            <div className="boris-campaign-reference-toolbar" style={{display:"none"}} aria-hidden="true">
              <div>
                <h3>{preview.items.length} объявлений{items[0]?.segment ? ` · ${items[0].segment}` : ""}</h3>
                <div className="boris-campaign-reference-filter-pills">
                  <button type="button" className={!status?"active":""} onClick={()=>setStatus("")}>Все <b>{preview.items.length}</b></button>
                  <button type="button" className={status==="ready"?"active":""} onClick={()=>setStatus("ready")}>Готовы <b>{items.filter((x:any)=>x.status==="ready").length}</b></button>
                  <button type="button" className={status==="draft"?"active":""} onClick={()=>setStatus("draft")}>Черновики <b>{items.filter((x:any)=>x.status==="draft").length}</b></button>
                  <button type="button" className={status==="needs_data"?"active":""} onClick={()=>setStatus("needs_data")}>Нужно исправить <b>{items.filter((x:any)=>x.status==="needs_data").length}</b></button>
                </div>
              </div>
              <div className="boris-campaign-reference-toolbar-actions">
                <Button kind="ghost" onClick={()=>setSelectedItemIds(selectedItemIds.length===items.length?[]:items.map((x:any)=>x.id))}>{selectedItemIds.length===items.length?"Снять выбор":"Выбрать все"}</Button>
                <span>Выбрано: <b>{selectedItemIds.length}</b></span>
              </div>
            </div>

            <section id="campaign-visual-actions" className="boris-edit-tools">
              <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"center",flexWrap:"wrap"}}>
                <div><div style={{fontSize:15,fontWeight:850,color:"#101828"}}>Доработать объявления</div><div style={{fontSize:12,color:"#667085",marginTop:2}}>Выберите нужные карточки. Для платной генерации баннеров выбор обязателен; стоки и свои фото можно применить ко всей кампании.</div></div>
                <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}><Button kind="ghost" onClick={()=>setSelectedItemIds(selectedItemIds.length===preview.items.length?[]:preview.items.map((x:any)=>Number(x.id)))}>{selectedItemIds.length===preview.items.length?"Снять выбор":"Выбрать все"}</Button><span style={{fontSize:12,color:"#475467"}}>Выбрано: <b>{selectedItemIds.length}</b>{!selectedItemIds.length?<em style={{fontStyle:"normal",marginLeft:6,color:"#667085"}}>· стоки/свои фото можно применить ко всем {preview.items.length}</em>:null}</span></div>
              </div>
              <div className="boris-edit-tools-grid">
                <div className="boris-edit-tool-card">
                  <div className="boris-edit-tool-title">Усилить тексты промтом</div>
                  <textarea ref={enhancePromptRef} value={enhancePrompt} onChange={(e)=>setEnhancePrompt(e.target.value)} rows={4} placeholder="Например: сделай текст более убедительным, усили боли семьи с детьми, добавь понятный призыв написать в Avito. Не выдумывай факты." style={{width:"100%",resize:"vertical",border:"1px solid #d0d5dd",borderRadius:10,padding:"9px 10px",font:"inherit",fontSize:13}} />
                  {activeEnhance ? <div className="boris-enhance-progress" role="status"><div><b>BORIS усиливает тексты</b><span>{Number(activeEnhance.done||0)} из {Number(activeEnhance.total||preview?.items?.length||0)}</span></div><div className="boris-enhance-progress-track"><i style={{width:`${Math.max(4,Math.min(100,(Number(activeEnhance.done||0)/Math.max(1,Number(activeEnhance.total||preview?.items?.length||1)))*100))}%`}} /></div><small>{String(activeEnhance.status||"")==="queued"?"BORIS продолжает автоматически. Готовые изменения сохраняются по мере обработки.":"BORIS продолжает автоматически. Готовые изменения сохраняются по мере обработки."}</small></div> : <Button onClick={enhanceCampaignTexts} disabled={publicationLocked || !!feedBusy || !enhancePrompt.trim()}>{feedBusy==="enhance-texts"?"Запускаю усиление…":selectedItemIds.length?`Усилить выбранные (${selectedItemIds.length})`:"Усилить все объявления"}</Button>}
                </div>
                <div className="boris-top10-copy-card">
                  <div className="boris-top10-copy-head"><span>{competitorCount}</span><div><b>Аудит конкурентов Avito</b><small>Запрос + город + до 10 объявлений. BORIS читает выдачу и делает короткую стратегическую сводку.</small></div></div>
                  <div className="boris-top10-fields"><label><span>Что ищет клиент</span><input value={competitorQuery} onChange={(e)=>setCompetitorQuery(e.target.value)} placeholder="Запрос, например Toyota Camry с выкупом" /></label><label><span>Город</span><input value={competitorCity} onChange={(e)=>setCompetitorCity(e.target.value)} placeholder="Город, например Москва" /></label><label className="boris-top10-count"><span>Конкурентов</span><input type="number" min={1} max={10} value={competitorCount} onChange={(e)=>setCompetitorCount(Math.max(1,Math.min(10,Number(e.target.value)||10)))} /></label></div>
                  <label style={{display:"flex",gap:8,alignItems:"flex-start",fontSize:12,color:"#344054",lineHeight:1.4}}><input type="checkbox" checked={competitorApply} onChange={(e)=>setCompetitorApply(e.target.checked)} style={{marginTop:2}}/><span><b>Использовать выводы в своих объявлениях и отстроиться от конкурентов</b><br/>BORIS берёт сильные принципы рынка, но не копирует чужие тексты, цены, гарантии и условия. Стратегия: «голубой океан» на подтверждённых фактах клиента.</span></label>
                  <div className="boris-top10-copy-points"><span>✓ Заголовки и структура</span><span>✓ Сильные/слабые приёмы</span><span>✓ Отстройка и рекламные углы</span></div>
                  <Button onClick={()=>analyzeTop10Competitors(competitorApply)} disabled={publicationLocked || !!feedBusy}>{feedBusy==="competitors"||feedBusy==="competitors-apply"?"BORIS анализирует выдачу…":`Проанализировать до ${competitorCount} конкурентов`}</Button>
                  <div style={{fontSize:10.5,color:"#667085",lineHeight:1.4}}>Источник — реальная выдача Avito по указанному запросу и городу. Если Avito не отдаёт данные, BORIS показывает это честно и не выдумывает конкурентов.</div>
                </div>
                {photoAngleMode ? <div className="boris-photo-angles-card">
                  <div className="boris-photo-angles-head"><span>ИИ</span><div><b>Новые ракурсы из лучших фото</b><small>BORIS сохраняет тот же автомобиль и меняет только положение камеры.</small></div></div>
                  <div className="boris-photo-angles-progress"><div><span>Готово</span><b>{generatedPhotoReady}/{generatedPhotoTarget}</b></div><div><span>Исходников</span><b>{Number(photoAnglesStatus?.source_count ?? workflow?.generated_photo_source_count ?? 5)}</b></div><div><span>Авто-QA</span><b>{Number(photoAnglesStatus?.safe_auto ?? workflow?.generated_photo_safe_auto_count ?? 18)}</b></div></div>
                  <div style={{fontSize:11,color:"#667085",lineHeight:1.45}}>Марка, модель, кузов, цвет, диски, оптика, номер и видимые особенности должны сохраняться. Пограничные кадры не ставятся в объявления автоматически.</div>
                  <Button onClick={generatedPhotoReady>=generatedPhotoTarget?loadPhotoAnglesStatus:generatePhotoAngles} disabled={publicationLocked || !!feedBusy}>{feedBusy==="photo-angles"?"BORIS создаёт ракурсы…":generatedPhotoReady>=generatedPhotoTarget?"20 новых ракурсов готовы":"Создать 20 новых ракурсов"}</Button>
                </div> : <div style={{padding:12,border:"1px solid #eaecf0",borderRadius:12,display:"grid",gap:8}}>
                  <div style={{fontSize:13,fontWeight:800,color:"#101828"}}>Добавить стопку фотографий</div>
                  <input ref={photoStackInputRef} type="file" accept="image/*" multiple style={{display:"none"}} onChange={(e)=>{const files=Array.from(e.target.files||[]);setCampaignPhotoFiles(files);if(files.length)void uploadCampaignPhotoStack(files);e.currentTarget.value="";}} />
                  <Button kind="ghost" onClick={()=>photoStackInputRef.current?.click()} disabled={publicationLocked}>{campaignPhotoFiles.length?`Выбрано фото: ${campaignPhotoFiles.length}`:"Выбрать фотографии с компьютера"}</Button>
                  <div style={{display:"flex",gap:12,alignItems:"center",flexWrap:"wrap"}}><label style={{display:"flex",gap:7,alignItems:"center",fontSize:12,color:"#344054"}}><input type="checkbox" checked={randomizePhotoStack} onChange={(e)=>setRandomizePhotoStack(e.target.checked)} />Перемешивать фото по объявлениям</label><label style={{display:"flex",gap:6,alignItems:"center",fontSize:12,color:"#344054"}}>Фото на объявление <input type="number" min={1} max={9} value={photosPerAd} onChange={(e)=>setPhotosPerAd(Math.max(1,Math.min(9,Number(e.target.value)||6)))} style={{width:58,border:"1px solid #d0d5dd",borderRadius:8,padding:"6px"}} /></label></div>
                  <div style={{fontSize:11,color:"#667085"}}>Можно загрузить до 30 фото за раз. Главное фото остаётся первым; остальные изображения распределяются по карточкам в разном порядке.</div>
                  <Button onClick={()=>void uploadCampaignPhotoStack()} disabled={publicationLocked || !!feedBusy}>{feedBusy==="photo-stack"?"Добавляю фотографии…":selectedItemIds.length?`Добавить в выбранные (${selectedItemIds.length})`:"Добавить во все объявления"}</Button>
                </div>}
              </div>
            </section>

            {competitorAuditOpen && competitorAudit?.analysis ? <section className="boris-top10-result">
              <div className="boris-top10-result-head"><div><span>АНАЛИЗ РЫНКА</span><h3>ТОП-{competitorAudit.count || 10} конкурентов · {competitorAudit.query}</h3><p>{competitorAudit.city}</p></div><button type="button" onClick={()=>setCompetitorAuditOpen(false)} aria-label="Закрыть">×</button></div>
              {competitorAudit.analysis.summary ? <div className="boris-top10-summary">{competitorAudit.analysis.summary}</div> : null}
              <div className="boris-top10-columns">
                <div><b>Сильные фишки</b>{(competitorAudit.analysis.strong_patterns||[]).slice(0,8).map((x:string,i:number)=><p key={'s'+i}>✓ {x}</p>)}</div>
                <div><b>Слабости конкурентов</b>{(competitorAudit.analysis.weak_patterns||[]).slice(0,8).map((x:string,i:number)=><p key={'w'+i}>− {x}</p>)}</div>
                <div><b>Отстройка BORIS</b>{(competitorAudit.analysis.differentiation||[]).slice(0,8).map((x:string,i:number)=><p key={'d'+i}>→ {x}</p>)}</div>
              </div>
              {(competitorAudit.analysis.hooks||[]).length ? <div className="boris-top10-hooks"><b>Рекламные углы</b><div>{(competitorAudit.analysis.hooks||[]).slice(0,8).map((x:string,i:number)=><span key={'h'+i}>{x}</span>)}</div></div> : null}
              <div className="boris-top10-actions"><div><small>Найдено и разобрано</small><b>{competitorAudit.count || 0} реальных объявлений Avito</b></div><Button onClick={()=>analyzeTop10Competitors(true)} disabled={publicationLocked || !!feedBusy}>{feedBusy==="competitors-apply"?"BORIS усиливает тексты…":"Применить фишки и отстройку к текстам"}</Button></div>
            </section> : null}

            <div className="boris-campaign-reference-body">
              <div className="boris-campaign-reference-grid">
                {preview.items.map((p:any,index:number)=>{
                  const vr=validation?.items?.find((x:any)=>x.id===p.id);
                  const src=items.find((x:any)=>x.id===p.id);
                  const unit=src?.payload?.price_unit || "";
                  const selected=selectedItemIds.includes(p.id);
                  const visuals=Array.from(new Set([...(photoAngleMode?[]:[p.banner]),...(p.images||[])].filter(Boolean))) as string[];
                  return <article id={`campaign-ref-item-${p.id}`} className={`boris-campaign-reference-card${selected?" is-selected":""}`} key={p.id}>
                    <button type="button" className="boris-campaign-reference-image" onClick={(e)=>visuals.length&&openLightbox(visuals,0,p.title,e.currentTarget)} aria-label={`Увеличить фото: ${p.title || "объявление"}`}>
                      {visuals?.[0] && !imageErrors[p.id] ? <img loading="lazy" decoding="async" src={visuals[0]} alt={p.title || "Фото объявления"} onError={()=>setImageErrors((old)=>({...old,[p.id]:true}))} /> : <span className="boris-campaign-reference-image-empty">Фото недоступно</span>}
                      <span className="boris-campaign-reference-card-number">{index+1}</span>
                      <span className="boris-campaign-reference-zoom">⌕</span>
                    </button>
                    {visuals.length>1 ? <div className="boris-campaign-reference-thumbs">{visuals.slice(1,7).map((img:string,idx:number)=><button type="button" key={img+idx} onClick={(e)=>openLightbox(visuals,idx+1,p.title,e.currentTarget)}><img loading="lazy" decoding="async" src={img} alt="" /></button>)}</div> : null}
                    <div className="boris-campaign-reference-card-content">
                      <div className="boris-campaign-reference-card-head">
                        <label><input type="checkbox" checked={selected} onChange={(e)=>setSelectedItemIds(e.target.checked?Array.from(new Set([...selectedItemIds,p.id])):selectedItemIds.filter(x=>x!==p.id))} /><strong>{String(p.title||"Без заголовка").replace(/[{}]/g,"").split("|")[0]}</strong></label>
                        <span className={vr?.valid?"ok":"bad"}>{vr?.valid?(officialXmlPassed && intake?.all_complete ? "Готово" : "Проверено BORIS"):"Исправить"}</span>
                      </div>
                      <p className="boris-campaign-reference-segment">{src?.segment || "Направление"}</p>
                      <p className="boris-campaign-reference-meta">{p.city || "Гео не задано"} · {p.category || "Категория уточняется"}</p>
                      <div className="boris-campaign-reference-price">{Number(p.price||0)>0?`${p.price} ₽${unit?` · ${unit}`:""}`:"Цена не указана"}</div>
                      <div className="boris-campaign-reference-status">{src?.status==="ready"?"Согласовано":"Ждёт вашего решения"}</div>
                      <div className="boris-campaign-reference-description">{renderCampaignDescriptionPreview(String(p.description||""))}</div>
                      <div style={{display:"flex",gap:6,flexWrap:"wrap",marginTop:8}}>
                        <Button kind="ghost" onClick={()=>{setFullTextItem(p);setFullTextDraft(String(p.description||""));}}>Весь текст</Button>
                        <Button kind="ghost" onClick={()=>{setSelectedItemIds([p.id]);document.getElementById('campaign-edit-tools')?.scrollIntoView({behavior:'smooth',block:'center'});}} disabled={publicationLocked}>+ Фото</Button>
                        <Button kind="ghost" onClick={()=>{setSelectedItemIds([p.id]);document.getElementById('campaign-edit-tools')?.scrollIntoView({behavior:'smooth',block:'center'});setTimeout(()=>enhancePromptRef.current?.focus(),350);}} disabled={publicationLocked}>Усилить промтом</Button>
                      </div>
                    </div>
                    <div className="boris-campaign-reference-card-actions" style={{display:"none"}} aria-hidden="true">
                      <Button kind="ghost" onClick={()=>{if(src){startEdit(src);setSelectedItemIds([src.id]);setTimeout(()=>document.getElementById('item-'+src.id)?.scrollIntoView({behavior:'smooth',block:'center'}),0);}}} disabled={publicationLocked}>Изменить</Button>
                      <Button kind="ghost" onClick={async()=>{setFeedBusy('approve-one-'+p.id);setErr('');try{await apiPost('/api/campaign/'+id+'/approval',{item_ids:[p.id],approved:true});await loadItems();await loadWorkflow();await checkFeed();await loadPreview();}catch(e:any){setErr(e?.message||'Не удалось согласовать объявление');}finally{setFeedBusy('');}}} disabled={publicationLocked || !!feedBusy}>Согласовать</Button>
                    </div>
                  </article>;
                })}
              </div>

              {(()=>{
                const rail=Array.from(new Set(preview.items.map((x:any)=>x.banner).filter(Boolean))).slice(0,10) as string[];
                return <aside className="boris-campaign-reference-rail" style={{display:"none"}} aria-hidden="true">
                  <div className="boris-campaign-reference-rail-head"><strong>{rail.length} баннеров</strong><span>для 1 места</span></div>
                  <div className="boris-campaign-reference-rail-list">
                    {rail.map((img:string,idx:number)=><button type="button" key={img+idx} onClick={(e)=>openLightbox(rail,idx,"Баннеры кампании",e.currentTarget)}><span>{idx+1}</span><img loading="lazy" decoding="async" src={img} alt={`Баннер ${idx+1}`} /></button>)}
                  </div>
                </aside>;
              })()}
            </div>

            <div className="boris-campaign-reference-rules" aria-label="Что BORIS проверил в объявлениях">
              <div><span>✦</span><p><b>Уникализация</b><small>Внизу каждого описания — свой конкретный сценарий; в массовой кампании меняется смысловой ракурс, а не только слова</small></p></div>
              <div><span>✓</span><p><b>Текст и орфография</b><small>Заголовок, описание и факты проверяются до готовности карточки</small></p></div>
              <div><span>↗</span><p><b>Призыв к действию</b><small>Короткие понятные CTA без навязчивых повторов</small></p></div>
              <div><span>▣</span><p><b>Фото + главный баннер</b><small>Галерея проходит смысловую проверку, а первый баннер проверяется на читаемость с телефона</small></p></div>
              <div><span>◉</span><p><b>Только факты клиента</b><small>Цены, гарантии, сроки и характеристики не добавляются без подтверждения</small></p></div>
            </div>

            <div className="boris-campaign-reference-approval">
              <div style={{...font.small,color:color.muted}}>Перед размещением нужно проверить тексты, изображения, категорию и обязательные поля. Если Avito требует неизвестный факт, его необходимо указать до размещения.</div>
              <div>
                <Button onClick={approveAllAndOpenActivation} disabled={publicationLocked || !!feedBusy || !ownerReviewReady || guardianRunning || guardianSent}>{guardianScheduled?"Avito заберёт объявления по расписанию":guardianSent?"Публикация подтверждена":guardianRunning?"BORIS размещает объявления в Avito…":feedBusy==="approve-place"?"BORIS запускает отправку…":"Проверить и разместить в Avito"}</Button>
              </div>
            </div>
          </div> : null}

          {showIntakeModal && intake?.directions?.length ? (()=>{
            const dirs=intake.directions || [];
            const safeStep=Math.max(0,Math.min(intakeStep,dirs.length-1));
            const dir=dirs[safeStep];
            const d=intakeDraft[dir.direction] || {template_id:dir.template_id,mode:dir.mode||"shared",answers:dir.answers||{}};
            const missingNameSet=new Set((dir.missing||[]).map((x:any)=>String(x)));
            const requiredFields=(dir.fields||[]).filter((f:any)=>{
              if(!f.required || f.depends_on) return false;
              if(["AvitoId","AuctionPrice","AuctionPriceLastDate"].includes(String(f.field||""))) return false;
              const allowed=Array.isArray(f.allowed_values)?f.allowed_values:[];
              const answer=(d.answers||{})[f.field];
              const hasAnswer=Boolean(answer && (Array.isArray(answer.value)?answer.value.length:String(answer.value??"").trim()));
              const hasCurrent=String(dir.current_common?.[f.field]??"").trim()!=="";
              // A single official value is a contract constant. BORIS can keep it
              // automatically; there is nothing useful for the client to choose.
              if(allowed.length===1) return false;
              return true;
            });
            const dependentFields:any[]=(dir.fields||[]).filter((f:any)=>{
              if(!f.depends_on || !avitoFieldNeedsInput(f)) return false;
              if((dir.missing||[]).includes(f.field) || (d.answers||{})[f.field]?.value) return true;
              const dep=String(f.depends_on||"").toLowerCase();
              return Object.values(d.answers||{}).some((a:any)=>{const v=String(a?.value||"").trim().toLowerCase();return !!v && dep.includes(v);});
            });
            const optionalBase:any[]=(dir.fields||[]).filter((f:any)=>avitoFieldNeedsInput(f) && !f.required && !f.depends_on && !["AvitoId","AuctionPrice","AuctionPriceLastDate"].includes(String(f.field||"")));
            const optionalFields:any[]=optionalBase.filter((f:any)=>!AVITO_ADVANCED_OPTIONAL_FIELDS.has(String(f.field||"")));
            const advancedOptionalFields:any[]=optionalBase.filter((f:any)=>AVITO_ADVANCED_OPTIONAL_FIELDS.has(String(f.field||"")));
            const missingFields=(dir.missing||[]).map((name:string)=>(dir.fields||[]).find((f:any)=>String(f.field)===String(name))).filter((f:any)=>f && avitoFieldNeedsInput(f));
            const dirItems=items.filter((x:any)=>String(x.segment||x.payload?.direction||"")===String(dir.direction));
            const reco=searchRecoByDirection[dir.direction] || {};
            const categoryCandidates=(dir.candidates||[]).filter((c:any)=>!categoryQuery.trim() || String(c.path||"").toLowerCase().includes(categoryQuery.trim().toLowerCase()));
            const selectedCategory=(dir.candidates||[]).find((c:any)=>String(c.template_id)===String(d.template_id||""));
            const selectedCategoryScore=Math.max(0,Number(selectedCategory?.score||0));
            const categoryAlternatives=(dir.candidates||[])
              .filter((c:any)=>String(c.template_id)!==String(d.template_id||""))
              .slice(0,3);
            const categoryMatchLabel=selectedCategory
              ? (selectedCategoryScore>=20 ? "Сильное" : selectedCategoryScore>=8 ? "Умеренное" : "Требует проверки")
              : "";
            const categoryMatchTone=selectedCategoryScore>=20 ? "#067647" : selectedCategoryScore>=8 ? "#b54708" : "#b42318";

            const renderPrettySelect=(key:string,value:any,options:any[],placeholder:string,onPick:(value:string)=>void)=><BorisSelect value={String(value||"")} options={options.map(v=>({value:String(v),label:String(v)}))} placeholder={placeholder} onChange={onPick}/>;
            const renderField=(f:any)=>{
              const a=(d.answers||{})[f.field] || {mode:"exact",value:""};
              const selectKey=`${dir.direction}:${f.field}`;
              return <div key={f.field} className="boris-intake-field-card" style={{padding:12,border:"1px solid #e5e7eb",borderRadius:14,background:"#fff"}}>
                <div style={{fontSize:13,fontWeight:700,color:"#172033"}}>{avitoFieldLabel(f)}{f.required?" *":""}</div>
                {f.depends_on?<div style={{fontSize:11,color:"#667085",marginTop:2,lineHeight:1.35}}>{f.field==="PledgeAmount"?"Укажите размер залога в рублях. Например: 30 000":avitoDependencyText(f.depends_on,dir.fields||[])}</div>:null}
                {f.allowed_values?.length ? <div style={{marginTop:7}}>{f.multiple ? <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>{f.allowed_values.map((raw:any)=>{const option=String(raw);const selected=Array.isArray(a.value)&&a.value.map(String).includes(option);return <button type="button" key={option} onClick={()=>{const current=Array.isArray(a.value)?a.value.map(String):[];const next=selected?current.filter((x:string)=>x!==option):[...current,option];setIntakeValue(dir.direction,f.field,{mode:"exact",value:next});}} style={{border:selected?"1px solid #315bea":"1px solid #d0d5dd",background:selected?"#eef4ff":"white",color:selected?"#1d4ed8":"#344054",borderRadius:9,padding:"8px 10px",fontSize:12,fontWeight:700,cursor:"pointer"}}>{selected?"✓ ":""}{option}</button>})}</div> : renderPrettySelect(selectKey,a.value??"",f.allowed_values,"Выберите…",(value)=>setIntakeValue(dir.direction,f.field,{mode:"exact",value}))}</div> : <>
                  {f.range_allowed ? <div style={{display:"flex",gap:6,marginTop:7}}><button type="button" onClick={()=>setIntakeValue(dir.direction,f.field,{mode:"exact"})} style={{flex:1,border:a.mode!=="range"?"2px solid #111827":"1px solid #d0d5dd",borderRadius:8,padding:"7px 8px",background:"white",cursor:"pointer",fontSize:12}}>Точно</button><button type="button" onClick={()=>setIntakeValue(dir.direction,f.field,{mode:"range"})} style={{flex:1,border:a.mode==="range"?"2px solid #111827":"1px solid #d0d5dd",borderRadius:8,padding:"7px 8px",background:"white",cursor:"pointer",fontSize:12}}>Диапазон</button></div>:null}
                  {a.mode==="range" && f.range_allowed ? <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6,marginTop:6}}><Input value={a.min??""} placeholder="От" onChange={(e:any)=>setIntakeValue(dir.direction,f.field,{min:e.target.value})}/><Input value={a.max??""} placeholder="До" onChange={(e:any)=>setIntakeValue(dir.direction,f.field,{max:e.target.value})}/></div> : <div style={{marginTop:6}}><Input value={a.value??""} placeholder={f.field==="PledgeAmount"?"Например, 30 000 ₽":"Введите значение"} onChange={(e:any)=>setIntakeValue(dir.direction,f.field,{mode:"exact",value:e.target.value})}/></div>}
                </>}
                {!a.value && dir.current_common?.[f.field] ? <button type="button" onClick={()=>setIntakeValue(dir.direction,f.field,{mode:"exact",value:dir.current_common[f.field]})} style={{border:0,background:"transparent",padding:"6px 0 0",cursor:"pointer",textDecoration:"underline",fontSize:11,color:"#475467"}}>Сейчас в карточках: {dir.current_common[f.field]} — использовать</button>:null}
              </div>;
            };
            return typeof document!=="undefined" ? createPortal(<div className="boris-intake-overlay" onClick={(e)=>{if(e.target===e.currentTarget)setShowIntakeModal(false);}} style={{position:"fixed",inset:0,zIndex:99999,background:"rgba(15,23,42,.52)",display:"grid",placeItems:"center",padding:"clamp(10px,2vw,24px)",overflow:"hidden"}}>
              <div role="dialog" aria-modal="true" aria-labelledby="feed-intake-title" className="boris-intake-reference boris-intake-dialog" style={{width:"min(1040px,calc(100vw - 24px))",maxHeight:"min(780px,calc(100dvh - 24px))",background:"#f8fafc",border:"1px solid #e4e7ec",borderRadius:20,boxShadow:"0 28px 80px rgba(15,23,42,.26)",overflow:"hidden",display:"flex",flexDirection:"column"}}>
                <div className="boris-intake-header" style={{padding:"14px 18px",background:"white",borderBottom:"1px solid #eaecf0",display:"flex",alignItems:"center",justifyContent:"space-between",gap:12}}>
                  <div><div id="feed-intake-title" style={{fontSize:20,fontWeight:850,color:"#101828"}}>Нужно уточнить обязательные данные Avito</div><div style={{fontSize:12,color:"#667085",marginTop:3}}>Услуга уже добавлена. Здесь ничего добавлять повторно не нужно — подтвердите только сведения, без которых Avito не примет объявление.</div></div>
                  <button type="button" aria-label="Закрыть опрос" onClick={()=>setShowIntakeModal(false)} style={{width:44,height:44,borderRadius:11,border:"1px solid #e4e7ec",background:"white",fontSize:22,cursor:"pointer",flex:"0 0 auto"}}>×</button>
                </div>
                <div className="boris-intake-body" style={{padding:"16px 18px 20px",overflowY:"auto",overscrollBehavior:"contain",minHeight:0,flex:"1 1 auto"}}>
                  {dirs.length>1 ? <div id="feed-intake-directions" style={{paddingBottom:14,borderBottom:"1px solid #eef2f6"}}><div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center",flexWrap:"wrap"}}><div style={{fontSize:12,fontWeight:800,color:"#344054"}}>Направления кампании</div><div style={{fontSize:12,color:"#667085"}}>{safeStep+1} из {dirs.length}</div></div><div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:8,padding:"10px 0 2px"}}>{dirs.map((x:any,idx:number)=><button key={x.direction} type="button" onClick={()=>{setIntakeStep(idx);setCategoryPickerOpen(false);setCategoryQuery("");}} style={{minWidth:0,textAlign:"left",whiteSpace:"normal",lineHeight:1.35,border:idx===safeStep?"1px solid #315bea":"1px solid #e4e7ec",background:idx===safeStep?"#eef4ff":x.complete?"#ecfdf3":"white",color:idx===safeStep?"#1d4ed8":x.complete?"#067647":"#475467",borderRadius:11,padding:"10px 12px",fontSize:12,fontWeight:750,cursor:"pointer"}}>{x.complete?"✓ ":""}{x.direction}</button>)}</div></div> : null}
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:10,flexWrap:"wrap"}}><div><div style={{fontSize:18,fontWeight:800,color:"#101828"}}>{dir.direction}</div><div style={{fontSize:12,color:"#667085",marginTop:3}}>Шаг {safeStep+1} из {dirs.length} · {dir.complete?"параметры подтверждены":"нужно заполнить"}</div></div><span style={{fontSize:12,fontWeight:800,color:dir.complete?"#067647":"#b54708",background:dir.complete?"#ecfdf3":"#fff7ed",padding:"6px 9px",borderRadius:8}}>{dir.complete?"Готово":"Обязательный шаг"}</span></div>
                  <div style={{marginTop:14,padding:"10px 12px",borderRadius:10,background:"#eef4ff",fontSize:12,color:"#344054"}}>Показываем только то, что действительно нужно для публикации. Если подкатегория однозначна, BORIS выберет её сам. Если Avito допускает несколько похожих вариантов — выберите один из предложенных ниже.</div>
                  <div data-testid="campaign-category-picker" className="boris-intake-category-card" style={{marginTop:12,padding:14,border:selectedCategory?"1px solid #ccebd9":"1px solid #fed7aa",borderRadius:14,background:"white"}}>
                    <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"flex-start",flexWrap:"wrap"}}>
                      <div style={{minWidth:0,flex:"1 1 260px"}}>
                        <div style={{fontSize:14,fontWeight:850,color:"#101828"}}>Категория Avito</div>
                        {selectedCategory
                          ? <>
                              <div style={{fontSize:12,color:"#067647",marginTop:4,lineHeight:1.45,fontWeight:750}}>✓ {selectedCategory.path}</div>
                              <div data-testid="campaign-category-confidence" style={{fontSize:11.5,color:categoryMatchTone,marginTop:5,lineHeight:1.45}}>
                                Совпадение BORIS: <b>{categoryMatchLabel}</b>
                                {categoryAlternatives.length ? <> · альтернативы: {categoryAlternatives.map((c:any)=>String(c.leaf_name||c.path||"")).join(" · ")}</> : null}
                              </div>
                            </>
                          : <div style={{fontSize:12,color:"#b54708",marginTop:4,lineHeight:1.45,fontWeight:750}}>Нужно выбрать подходящую подкатегорию, чтобы BORIS загрузил обязательные параметры Avito.</div>}
                      </div>
                      {selectedCategory ? <Button kind="ghost" onClick={()=>{setCategoryPickerOpen((v)=>!v);setCategoryQuery("");}}>{categoryPickerOpen?"Скрыть варианты":"Изменить категорию"}</Button> : null}
                    </div>
                    {(categoryPickerOpen || !selectedCategory) ? <div className="boris-intake-category-picker" style={{marginTop:10}}>
                      {dir.candidates?.length>4 ? <Input value={categoryQuery} placeholder="Найти подкатегорию" onChange={(e:any)=>setCategoryQuery(e.target.value)} /> : null}
                      {categoryCandidates.length ? <div className="boris-intake-category-options" style={{display:"grid",gap:8,marginTop:dir.candidates?.length>4?8:0}}>
                        {categoryCandidates.map((candidate:any)=><button
                          type="button"
                          key={String(candidate.template_id)}
                          disabled={publicationLocked || !!feedBusy}
                          onClick={async()=>{await chooseDirectionCategory(dir.direction,String(candidate.template_id));setCategoryPickerOpen(false);setCategoryQuery("");}}
                          style={{width:"100%",minWidth:0,textAlign:"left",whiteSpace:"normal",overflowWrap:"anywhere",lineHeight:1.4,border:String(d.template_id||"")===String(candidate.template_id)?"2px solid #315bea":"1px solid #e4e7ec",borderRadius:11,background:String(d.template_id||"")===String(candidate.template_id)?"#eef4ff":"#fff",color:"#344054",padding:"10px 12px",fontSize:12,fontWeight:750,cursor:feedBusy?"default":"pointer"}}
                        >{candidate.path}</button>)}
                      </div> : <div style={{marginTop:8,padding:"10px 11px",borderRadius:10,background:"#fff7ed",color:"#9a3412",fontSize:12,lineHeight:1.45}}>BORIS не нашёл достаточно точную подкатегорию. Уточните название товара или услуги в задании и запустите разбор ещё раз.</div>}
                    </div> : null}
                  </div>
                  <div style={{marginTop:12,padding:14,border:"1px solid #dbeafe",borderRadius:14,background:"white"}}>
                    <div style={{fontSize:14,fontWeight:850,color:"#101828"}}>Какие названия чаще встречаются в выдаче Avito</div>
                    <div style={{fontSize:12,color:"#667085",marginTop:3}}>Укажите город. BORIS проверит текущую выдачу и предложит варианты. Отметьте нужные — они распределятся по объявлениям.</div>
                    <div data-testid="campaign-search-city-row" style={{display:"flex",flexWrap:"wrap",gap:8,marginTop:10,alignItems:"end",minWidth:0}}>
                      <label style={{display:"grid",gap:4,flex:"1 1 220px",maxWidth:320,minWidth:0}}><span style={{fontSize:11,fontWeight:750,color:"#475467"}}>Город</span><Input value={searchCityByDirection[dir.direction]||String(dirItems[0]?.geo||dirItems[0]?.payload?.city||"")} placeholder="Например, Москва" onChange={(e:any)=>setSearchCityByDirection((p:any)=>({...p,[dir.direction]:e.target.value}))}/></label>
                      <div style={{flex:"0 1 auto",minWidth:0}}><Button kind="ghost" onClick={()=>loadSearchRecommendations(dir.direction,true,searchCityByDirection[dir.direction]||String(dirItems[0]?.geo||dirItems[0]?.payload?.city||""))}>Обновить выдачу</Button></div>
                    </div>
                    <div style={{display:"flex",gap:7,flexWrap:"wrap",marginTop:10}}>{((reco.observed_titles?.length?reco.observed_titles:reco.title_variants)||[]).slice(0,10).map((t:string)=>{const checked=(selectedRecoTitles[dir.direction]||[]).includes(t);return <label key={t} style={{display:"flex",gap:6,alignItems:"center",padding:"7px 9px",border:checked?"1px solid #315bea":"1px solid #e4e7ec",borderRadius:9,background:checked?"#eef4ff":"#fff",fontSize:11,cursor:"pointer"}}><input type="checkbox" checked={checked} onChange={()=>setSelectedRecoTitles((p:any)=>{const cur=p[dir.direction]||[];return {...p,[dir.direction]:checked?cur.filter((x:string)=>x!==t):[...cur,t]};})}/><span>{t}</span></label>})}</div>
                    <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center",marginTop:10,flexWrap:"wrap"}}><span style={{fontSize:11,color:"#667085"}}>{reco.source_status==="live"?"Источник: текущая выдача Avito":reco.source_status?"Выдача временно недоступна — показаны безопасные варианты BORIS":"BORIS проверяет выдачу…"}</span><Button kind="ghost" disabled={publicationLocked || !(selectedRecoTitles[dir.direction]||[]).length} onClick={async()=>{setFeedBusy("apply-reco");try{await apiPost("/api/campaign/"+id+"/workflow/search_recommendations/apply",{direction:dir.direction,titles:selectedRecoTitles[dir.direction]||[]});await loadItems();await loadWorkflow();}finally{setFeedBusy("");}}}>Добавить выбранные названия</Button></div>
                  </div>
                  {true ? <>
                    <div style={{marginTop:16,fontSize:14,fontWeight:800,color:"#101828"}}>Что нужно уточнить</div>
                    <div style={{fontSize:12,color:"#667085",marginTop:3}}>Эти ответы относятся только к направлению «{dir.direction}».</div>
                    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:9,marginTop:10}}>{requiredFields.map(renderField)}</div>
                    {dependentFields.length ? <div style={{marginTop:14}}><div style={{fontSize:14,fontWeight:850,color:"#101828"}}>Ещё нужно заполнить</div><div style={{fontSize:12,color:"#667085",marginTop:3}}>Эти характеристики нужны с учётом выбранных вами значений.</div><div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:9,marginTop:10}}>{dependentFields.map(renderField)}</div></div>:null}
                    {optionalFields.length ? <details style={{marginTop:14}}><summary style={{cursor:"pointer",fontSize:13,fontWeight:800,color:"#667085"}}>Дополнительные параметры ({optionalFields.length})</summary><div style={{fontSize:12,color:"#667085",marginTop:5}}>Это обычные дополнительные характеристики. Их можно не заполнять, если они не относятся к вашему предложению.</div><div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:9,marginTop:10}}>{optionalFields.map(renderField)}</div></details>:null}
                    {advancedOptionalFields.length ? <details style={{marginTop:10,border:"1px solid #EEF1F5",borderRadius:12,background:"#FAFBFC",padding:"10px 12px"}}><summary style={{cursor:"pointer",fontSize:12.5,fontWeight:800,color:"#667085"}}>Доставка, опт и документы ({advancedOptionalFields.length})</summary><div style={{fontSize:11.5,color:"#667085",marginTop:6,lineHeight:1.5}}>Эти поля нужны только в отдельных случаях. BORIS не требует их для продолжения, если Avito не сделал их обязательными для выбранной категории.</div><div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:9,marginTop:10}}>{advancedOptionalFields.map(renderField)}</div></details>:null}
                  </> : <div style={{marginTop:16,padding:14,border:"1px solid #d0d5dd",borderRadius:12,background:"white"}}><div style={{fontSize:14,fontWeight:800,color:"#101828"}}>Индивидуально по каждой карточке</div><div style={{fontSize:12,color:"#667085",marginTop:5,lineHeight:1.5}}>Для каждой карточки доступен полный контракт выбранной подкатегории Avito — все {(dir.fields||[]).length} параметров. Общие значения между карточками не размножаются.</div>{dirItems.length?<div style={{display:"grid",gap:10,marginTop:12}}>{dirItems.map((it:any)=><details key={it.id} style={{border:"1px solid #eaecf0",borderRadius:10,padding:10}}><summary style={{cursor:"pointer",fontSize:12,fontWeight:800,color:"#344054"}}>{it.payload?.title || `Карточка #${it.id}`} · {it.geo || it.payload?.city || "без города"}</summary><div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(210px,1fr))",gap:8,marginTop:10}}>{renderAvitoGroups(dir.fields||[],(f:any)=>{const val=(itemParamDrafts[it.id]||{})[f.field] ?? it.payload?.params?.[f.field] ?? ""; return <label key={f.field} style={{display:"grid",gap:4,padding:8,border:"1px solid #eef2f6",borderRadius:8}}><span style={{fontSize:11,fontWeight:700,color:"#344054"}}>{avitoFieldLabel(f)}{f.required?" *":""}</span>{f.depends_on?<span style={{fontSize:10,color:"#667085",lineHeight:1.4}}>{avitoDependencyText(f.depends_on,dir.fields||[])}</span>:null}{f.allowed_values?.length?<BorisSelect value={String(val||"")} placeholder="Не задано" ariaLabel={avitoFieldLabel(f)} options={[{value:"",label:"Не задано"},...f.allowed_values.map((v:any)=>({value:String(v),label:String(v)}))]} onChange={(next)=>setItemParamDrafts((p:any)=>({...p,[it.id]:{...(p[it.id]||{}),[f.field]:next}}))}/>:<Input value={val} placeholder="Значение" onChange={(e:any)=>setItemParamDrafts((p:any)=>({...p,[it.id]:{...(p[it.id]||{}),[f.field]:e.target.value}}))}/>}</label>})}</div><Button kind="ghost" onClick={()=>savePerItemParams(it,dir)} disabled={publicationLocked || !!feedBusy} style={{marginTop:9}}>{feedBusy==="item-params-"+it.id?"Сохраняю…":"Сохранить все параметры карточки"}</Button></details>)}</div>:<div style={{fontSize:12,color:"#b54708",marginTop:10}}>Сначала сохраните индивидуальный режим. После создания карточек здесь появятся все параметры Avito для каждой карточки.</div>}</div>}
                  {missingFields.length ? <div style={{marginTop:12,padding:"11px 12px",borderRadius:12,background:"#fff7ed",color:"#9a3412",fontSize:12}}><b>Чтобы продолжить, заполните:</b> {missingFields.map((f:any)=>f.field==="PledgeAmount"?"размер залога":avitoFieldLabel(f)).join(", ")}.</div> : <div style={{marginTop:12,padding:"11px 12px",borderRadius:12,background:"#ecfdf3",color:"#067647",fontSize:12,fontWeight:750}}>Все обязательные данные заполнены — можно продолжать.</div>}
                </div>
                <div id="feed-intake-review" className="boris-intake-footer" style={{padding:"12px 20px",background:"white",borderTop:"1px solid #eaecf0",display:"flex",justifyContent:"space-between",gap:10,alignItems:"center",flexWrap:"wrap",scrollMarginTop:12}}>
                  <div style={{fontSize:12,color:"#667085"}}>Заполнено направлений: <b>{dirs.filter((x:any)=>x.complete).length}/{dirs.length}</b></div>
                  <div className="boris-intake-footer-actions" style={{display:"flex",gap:8,flexWrap:"wrap"}}><Button kind="ghost" onClick={()=>setIntakeStep(Math.max(0,safeStep-1))} disabled={safeStep===0}>Назад</Button>{safeStep<dirs.length-1?<Button kind="ghost" onClick={()=>setIntakeStep(Math.min(dirs.length-1,safeStep+1))}>Следующее направление</Button>:null}<Button onClick={saveAllDirectionIntake} disabled={publicationLocked || !!feedBusy}>{feedBusy==="intake-all"?"Проверяю ответы…":"Продолжить"}</Button></div>
                </div>
              </div>
            </div>, document.body) : null;
          })() : null}

          {fullTextItem && typeof document!=="undefined" ? createPortal(<div onClick={(e)=>{if(e.target===e.currentTarget)setFullTextItem(null);}} style={{position:"fixed",inset:0,zIndex:99999,background:"rgba(15,23,42,.58)",display:"grid",placeItems:"center",padding:16}}>
            <div role="dialog" aria-modal="true" style={{width:"min(760px,100%)",maxHeight:"calc(100vh - 32px)",overflow:"auto",background:"white",borderRadius:16,boxShadow:"0 24px 80px rgba(15,23,42,.28)",padding:18}}>
              <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"flex-start"}}><div><div style={{fontSize:18,fontWeight:850,color:"#101828"}}>{fullTextItem.title || "Объявление"}</div><div style={{fontSize:12,color:"#667085",marginTop:4}}>{fullTextItem.city || ""} {fullTextItem.category?`· ${fullTextItem.category}`:""}</div></div><button type="button" onClick={()=>setFullTextItem(null)} style={{border:"1px solid #e4e7ec",background:"white",borderRadius:10,width:38,height:38,fontSize:22,cursor:"pointer"}}>×</button></div>
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:8,marginTop:14}}>
                <div style={{padding:"10px 12px",border:"1px solid #E4E7EC",borderRadius:11,background:"#FAFBFC"}}><small style={{color:"#667085"}}>Цена</small><strong style={{display:"block",marginTop:3,color:"#172033",fontSize:16}}>{Number(fullTextItem.price||0)>0?Number(fullTextItem.price).toLocaleString("ru-RU")+" ₽":"Не указана"}</strong></div>
                <div style={{padding:"10px 12px",border:"1px solid #E4E7EC",borderRadius:11,background:"#FAFBFC"}}><small style={{color:"#667085"}}>Фотографии</small><strong style={{display:"block",marginTop:3,color:"#172033",fontSize:16}}>{Array.isArray(fullTextItem.images)?fullTextItem.images.length:0}</strong></div>
              </div>
              {Array.isArray(fullTextItem.images) && fullTextItem.images.length ? <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(110px,1fr))",gap:8,marginTop:12}}>{fullTextItem.images.slice(0,10).map((src:string,index:number)=><button type="button" key={src+index} onClick={(e)=>openLightbox(fullTextItem.images,index,fullTextItem.title||"Объявление",e.currentTarget)} style={{height:96,border:"1px solid #E4E7EC",borderRadius:10,padding:0,overflow:"hidden",background:"#F7F8FA",cursor:"zoom-in"}}><img loading="lazy" decoding="async" src={src} alt={"Фото "+(index+1)} style={{width:"100%",height:"100%",objectFit:"cover"}}/></button>)}</div> : null}
              <div style={{marginTop:16,fontSize:12,fontWeight:800,color:"#344054"}}>Полный текст объявления</div>
              <textarea value={fullTextDraft} onChange={(e)=>setFullTextDraft(e.target.value)} rows={16} readOnly={publicationLocked} style={{marginTop:8,width:"100%",resize:"vertical",border:"1px solid #d0d5dd",borderRadius:10,padding:"12px",font:"inherit",fontSize:14,lineHeight:1.6,color:"#344054",background:publicationLocked?"#F8FAFC":"#fff"}} />
              <div style={{display:"flex",justifyContent:"flex-end",gap:8,marginTop:10}}><Button kind="ghost" onClick={()=>setFullTextItem(null)}>Закрыть</Button><Button onClick={saveFullText} disabled={publicationLocked || !!feedBusy || fullTextDraft===String(fullTextItem.description||"")}>{feedBusy==="full-text-save"?"Сохраняю…":"Сохранить текст"}</Button></div>
            </div>
          </div>, document.body) : null}

          {lightbox && typeof document!=="undefined" ? createPortal(<div onClick={(e)=>{if(e.target===e.currentTarget) closeLightbox();}} style={{position:"fixed",inset:0,zIndex:100000,background:"rgba(2,6,23,.88)",backdropFilter:"blur(4px)",display:"grid",placeItems:"center",padding:"clamp(8px,2vw,20px)"}}>
            <div ref={lightboxDialogRef} role="dialog" aria-modal="true" aria-labelledby="campaign-lightbox-title" style={{width:"min(1180px,100%)",maxHeight:"calc(100vh - 16px)",display:"flex",flexDirection:"column",background:"#0b1220",border:"1px solid rgba(255,255,255,.16)",borderRadius:16,boxShadow:"0 24px 80px rgba(0,0,0,.45)",overflow:"hidden",color:"white"}}>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:12,padding:"10px 12px",borderBottom:"1px solid rgba(255,255,255,.12)"}}>
                <div id="campaign-lightbox-title" style={{fontSize:14,fontWeight:700,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{lightbox.title}</div>
                <div style={{display:"flex",alignItems:"center",gap:10,flex:"0 0 auto"}}>
                  <span aria-live="polite" style={{fontSize:13,fontWeight:700}}>{lightbox.index + 1} / {lightbox.images.length}</span>
                  <button type="button" data-lightbox-close onClick={closeLightbox} aria-label="Закрыть просмотр" title="Закрыть (Esc)" style={{width:44,height:44,borderRadius:10,border:"1px solid rgba(255,255,255,.22)",background:"rgba(255,255,255,.08)",color:"white",fontSize:24,cursor:"pointer",display:"grid",placeItems:"center"}}>×</button>
                </div>
              </div>
              <div style={{position:"relative",minHeight:240,flex:"1 1 auto",display:"grid",placeItems:"center",padding:"12px 58px",overflow:"hidden"}}>
                {!lightboxLoaded && !lightboxError ? <div role="status" style={{position:"absolute",right:16,top:16,zIndex:2,padding:"6px 9px",borderRadius:999,background:"rgba(2,6,23,.62)",fontSize:12,color:"rgba(255,255,255,.85)"}}>Загружаю…</div> : null}
                {lightboxError ? <div role="alert" style={{position:"absolute",left:"50%",bottom:18,transform:"translateX(-50%)",zIndex:3,padding:"8px 11px",borderRadius:9,background:"rgba(127,29,29,.88)",fontSize:12}}>Следующее фото не загрузилось — текущее оставлено на экране.</div> : null}
                <img src={lightboxVisibleSrc || lightbox.images[lightbox.index]} alt={`${lightbox.title}: фото ${lightbox.index + 1} из ${lightbox.images.length}`} onLoad={()=>setLightboxLoaded(true)} onError={()=>setLightboxError(true)} style={{display:"block",maxWidth:"100%",maxHeight:"calc(100vh - 210px)",width:"auto",height:"auto",objectFit:"contain",borderRadius:8}} />
                <button type="button" onClick={()=>moveLightbox(-1)} disabled={lightbox.images.length<2} aria-label="Предыдущее фото" title="Предыдущее фото (←)" style={{position:"absolute",left:8,top:"50%",transform:"translateY(-50%)",width:48,height:56,borderRadius:12,border:"1px solid rgba(255,255,255,.22)",background:"rgba(2,6,23,.72)",color:"white",fontSize:28,cursor:lightbox.images.length>1?"pointer":"default",opacity:lightbox.images.length>1?1:.4}}>‹</button>
                <button type="button" onClick={()=>moveLightbox(1)} disabled={lightbox.images.length<2} aria-label="Следующее фото" title="Следующее фото (→)" style={{position:"absolute",right:8,top:"50%",transform:"translateY(-50%)",width:48,height:56,borderRadius:12,border:"1px solid rgba(255,255,255,.22)",background:"rgba(2,6,23,.72)",color:"white",fontSize:28,cursor:lightbox.images.length>1?"pointer":"default",opacity:lightbox.images.length>1?1:.4}}>›</button>
              </div>
              <div style={{display:"flex",gap:8,overflowX:"auto",padding:"10px 12px 12px",borderTop:"1px solid rgba(255,255,255,.12)"}}>
                {lightbox.images.map((img:string,idx:number)=><button type="button" key={img+idx} onClick={()=>{if(idx===lightbox.index)return;const delta=idx-lightbox.index;moveLightbox(delta);}} aria-label={`Показать фото ${idx+1} из ${lightbox.images.length}`} aria-current={idx===lightbox.index?"true":undefined} style={{flex:"0 0 auto",padding:2,border:idx===lightbox.index?"2px solid white":"2px solid transparent",borderRadius:8,background:"transparent",cursor:"pointer"}}><img loading="lazy" decoding="async" src={img} alt="" aria-hidden="true" style={{width:72,height:54,objectFit:"cover",borderRadius:5,display:"block"}} /></button>)}
              </div>
            </div>
          </div>, document.body) : null}

          {exportInfo ? <Alert kind="success">Пакет подготовлен: {exportInfo.items} объявлений. Ничего не отправлено в Avito без вашего подтверждения.</Alert> : null}

          {ownerReviewReady && feedControl ? <section id="avito-feed-control" className="boris-feed-finish">
            <div className="boris-feed-finish-head">
              <div>
                <div className="boris-feed-finish-kicker">РАЗМЕЩЕНИЕ</div>
                <div style={{...font.h2,color:color.heading}}><b>Готово к размещению</b></div>
                <div style={{...font.small,marginTop:4}}>BORIS проверил объявления и подготовил пакет для Avito. Здесь остаётся только финальная проверка и ваше подтверждение.</div>
              </div>
              <span className={`boris-feed-state ${feedControl.validation_state==="READY"?"ok":feedControl.validation_state==="STALE"?"warn":"bad"}`}>{feedControl.validation_state==="READY"?"✓ Проверка Avito пройдена":feedControl.validation_state==="STALE"?"Нужна повторная проверка":"Пакет требует проверки"}</span>
            </div>
            {publicationCompleted ? <Alert kind="success">
              <b>Кампания опубликована в Avito: {publishedCount}/{feedControl.items} объявлений активны.</b> Повторная отправка заблокирована. BORIS продолжает контролировать активность и результат после публикации.
              {submittedSnapshot ? <span style={{display:"block",marginTop:6}}>Подтверждённый пакет: <b>{submittedSnapshot.items || feedControl.items}</b> объявлений · <b>{submittedSnapshot.photos || 0}</b> фото</span> : null}
            </Alert> : publicationPartial ? <Alert kind="danger">
              <b>Avito опубликовал кампанию частично: {publishedCount}/{feedControl.items}.</b> Ошибок: <b>{publicationErrorCount}</b>. Повторная отправка всей кампании заблокирована; BORIS сохраняет причины по проблемным объявлениям.
            </Alert> : publicationInFlight ? <Alert kind="success">
              <b>Avito обрабатывает отправленную кампанию — изменения временно заморожены.</b> BORIS сам ждёт итоговый отчёт и не меняет тексты, фото, баннеры или категорию во время обработки.
              {submittedSnapshot ? <span style={{display:"block",marginTop:6}}>Отправлено: <b>{submittedSnapshot.items || feedControl.items}</b> объявлений · <b>{submittedSnapshot.photos || 0}</b> фото</span> : null}
            </Alert> : null}
            <div className="boris-feed-metrics">
              <div><span className="boris-feed-metric-icon">A</span><p><small>Аккаунт</small><b>{feedControl.account_name}</b><em>{feedControl.avito_connected?"Avito подключён":"Нужно подключить Avito"}</em></p></div>
              <div><span className="boris-feed-metric-icon">≡</span><p><small>Объявления</small><b>{feedControl.items}</b><em>{feedControl.photos} фотографий</em></p></div>
              <div><span className="boris-feed-metric-icon">⌖</span><p><small>География</small><b>{feedControl.cities?.length || 0} {feedControl.cities?.length===1?"город":"городов"}</b><em>{feedControl.cities?.join(", ") || "—"}</em></p></div>
              <div><span className="boris-feed-metric-icon">✓</span><p><small>Согласовано</small><b>{feedControl.approved}/{feedControl.items}</b><em>{feedControl.approved===feedControl.items?"Все объявления готовы":"Есть объявления на проверке"}</em></p></div>
            </div>
            {avitoPreflight?<div data-testid="campaign-avito-preflight" style={{marginTop:12,border:"1px solid #D8E3F2",borderRadius:14,padding:14,background:"#FBFDFF",display:"grid",gap:10}}>
              <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"flex-start",flexWrap:"wrap"}}>
                <div>
                  <b style={{fontSize:14,color:"#172033"}}>{avitoPreflight.can_publish?"Можно размещать в Avito":avitoPreflight.state==="READY_AVITO_BLOCKED"?"Контент готов — блокирует доступ Avito":"Проверка перед размещением"}</b>
                  <div style={{fontSize:11.5,color:"#667085",marginTop:3}}>{avitoPreflight.primary_action}{avitoPreflight.blocked_reason?" · "+humanizeBusinessText(avitoPreflight.blocked_reason,""):""}</div>
                </div>
                <Button kind="ghost" onClick={async()=>{setFeedBusy("preflight");try{await loadFeedControl(true);}finally{setFeedBusy("");}}} disabled={publicationLocked||!!feedBusy}>{feedBusy==="preflight"?"Проверяю…":"Обновить preflight"}</Button>
              </div>
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(210px,1fr))",gap:7}}>
                {(avitoPreflight.checks||[]).map((check:any)=>{
                  const good=check.status==="PASS";
                  const warn=check.status==="WARN"||check.status==="CHECKING"||check.status==="UNKNOWN";
                  const fg=good?"#067647":warn?"#B54708":"#B42318";
                  const bg=good?"#ECFDF3":warn?"#FFFAEB":"#FEF3F2";
                  const mark=good?"✓":warn?"!":"×";
                  return <div key={check.code} style={{border:"1px solid "+(good?"#ABEFC6":warn?"#FEDF89":"#FECDCA"),borderRadius:10,padding:"9px 10px",background:bg}}>
                    <div style={{display:"flex",gap:7,alignItems:"center",fontSize:12,fontWeight:850,color:fg}}><span>{mark}</span><span>{check.label}</span></div>
                    <div style={{fontSize:11,color:"#667085",lineHeight:1.4,marginTop:3}}>{check.message}</div>
                  </div>;
                })}
              </div>
            </div>:null}
            <div className={`boris-autoload-card ${feedControl.autoload_ready?"ready":"blocked"}`}>
              <div className="boris-autoload-card-icon">{feedControl.autoload_ready?"✓":"!"}</div>
              <div className="boris-autoload-card-copy">
                <b>{publicationCompleted?"Автозагрузка Avito активна":feedControl.autoload_ready?"Автозагрузка Avito доступна":"Нужно включить автозагрузку в Avito"}</b>
                <span>{publicationCompleted?"Текущая кампания уже передана в Avito. BORIS продолжает автоматический контроль активности и результата.":feedControl.autoload_ready?"Аккаунт готов принимать пакет объявлений — BORIS сможет отправить его после вашего подтверждения.":feedControl.avito_connected?"Подключение к Avito работает, но сам Avito не даёт этому аккаунту доступ к автозагрузке. Объявления готовы — размещение станет доступно после включения автозагрузки в кабинете Avito.":"Сначала подключите аккаунт Avito."}</span>
                {!feedControl.autoload_ready && feedControl.autoload_http_status ? <small>Avito подтвердил, что автозагрузка для этого аккаунта пока недоступна.</small> : null}
              </div>
              <Button kind="ghost" onClick={validateAvitoFeed} disabled={publicationLocked || !!feedBusy}>{publicationCompleted?"Публикация подтверждена":publicationPartial?"Итог зафиксирован":publicationInFlight?"Проверка зафиксирована":feedBusy==="xml-check"?"Проверяю…":"Проверить ещё раз"}</Button>
            </div>
            <div className="boris-feed-history">
              <div><small>Последняя проверка</small><b>{feedControl.last_validation?new Date(feedControl.last_validation).toLocaleString("ru-RU"):"Ещё не выполнялась"}</b><span>{feedControl.errors || 0} ошибок · {feedControl.warnings || 0} предупреждений</span></div>
              <div><small>Последняя отправка</small><b>{feedControl.last_send?new Date(feedControl.last_send).toLocaleString("ru-RU"):"Отправок ещё не было"}</b><span>{feedControl.last_result || "BORIS отправит объявления после подтверждения"}</span></div>
            </div>
            <div className="boris-feed-final-action">
              <div><b>{publicationCompleted?"Опубликовано в Avito":publicationPartial?"Опубликовано частично":publicationInFlight?"Avito обрабатывает отправку":feedControl.can_send?"Всё готово к размещению":"Объявления готовы. Остался доступ Avito"}</b><span>{publicationCompleted?`Подтверждено ${publishedCount}/${feedControl.items}. Повторно размещать ничего не нужно — BORIS автоматически контролирует активность и KPI.`:publicationPartial?`Опубликовано ${publishedCount}/${feedControl.items}; ошибок ${publicationErrorCount}. BORIS сохраняет причины и не повторяет всю кампанию.`:publicationInFlight?"Повторно размещать или редактировать ничего не нужно — BORIS сам дождётся итогового отчёта.":feedControl.can_send?`Согласовано ${counts.ready || 0} из ${counts.all || items.length}. Можно размещать.`:feedControl.autoload_ready?humanizeBusinessText(feedControl.blocked_reason,"BORIS завершает проверку перед размещением."):"BORIS не будет имитировать отправку: Avito сейчас запрещает автозагрузку для этого аккаунта."}</span></div>
              <Button onClick={openActivation} disabled={publicationLocked || !!feedBusy || !feedControl.can_send || (counts.ready || 0) !== (counts.all || items.length)}>
                {publicationCompleted?`${publishedCount}/${feedControl.items} опубликовано`:publicationPartial?"Есть ошибки публикации":publicationInFlight?"Ждём итог Avito":feedBusy==="activation-check"?"Проверяю…":feedControl.can_send?"Разместить в Avito":"Размещение пока недоступно"}
              </Button>
            </div>
          </section> : null}

          {showActivationConfirm && activation ? <div role="dialog" aria-modal="true" aria-labelledby="activation-title" style={{position:"fixed",inset:0,zIndex:1000,background:"rgba(0,0,0,.42)",display:"grid",placeItems:"center",padding:20}}>
            <Card style={{width:"min(560px,100%)",maxHeight:"90vh",overflow:"auto"}}>
              <div id="activation-title" style={{...font.h2,color:color.heading}}><b>Всё готово. Разместить на Avito?</b></div>
              <div style={{...font.body,marginTop:8}}>BORIS подготовил объявления для размещения. Финальное подтверждение передаст их через существующее подключение Avito.</div>
              <div style={{...font.body,lineHeight:1.8,marginTop:space.sm}}>
                <div>Объявлений к размещению: <b>{activation.total}</b></div>
                <div>Городов: <b>{activation.cities?.length || 0}</b>{activation.cities?.length ? " · "+activation.cities.join(", ") : ""}</div>
                <div>Фотографий: <b>{activation.photos}</b></div>
                <div>Проверка Avito: <b>{officialXmlPassed ? "пройдена" : "требует внимания"}</b></div>
                <div>Аккаунт Avito: <b>подключён</b></div>
              </div>
              <div style={{...font.small,marginTop:space.sm}}>BORIS передаст подготовленные объявления через существующее подключение Avito. Статус «Размещено» появится только после подтверждённого состояния в Avito; принятую отправку показываем как обработку.</div>
              <div style={{display:"flex",gap:space.sm,flexWrap:"wrap",marginTop:space.md}}>
                <Button onClick={confirmActivation} disabled={publicationLocked || !!feedBusy}>{feedBusy==="activate"?"Передаю…":"Разместить объявления"}</Button>
                <Button kind="ghost" onClick={()=>setShowActivationConfirm(false)} disabled={!!feedBusy}>Отмена</Button>
              </div>
            </Card>
          </div> : null}
        </Card>

        <Card style={{ marginBottom: space.md, display: "none" }} aria-hidden="true">
          <div style={{ display: "flex", gap: space.sm, flexWrap: "wrap", alignItems: "center" }}>
            <span style={{...font.small, display:"none"}}>Служебный импорт существующих объявлений:</span>
            <Button kind="ghost" onClick={() => runImport(true)} disabled={importing}>
              {importing ? "Считаю…" : "Посмотреть, что перенесётся"}
            </Button>
            {importReport && importReport._dry && importReport.imported > 0 ? (
              <Button onClick={() => runImport(false)} disabled={importing}>
                Перенести {importReport.imported}
              </Button>
            ) : null}
          </div>
          {importReport ? (
            <div style={{ ...font.small, marginTop: space.sm }}>
              {importReport._dry ? "Будет перенесено: " : "Перенесено: "}
              {importReport.imported} · уже было: {importReport.skipped} ·
              найдено всего: {importReport.found}
            </div>
          ) : null}
        </Card>

        <div className="boris-campaign-filters" style={{ display: "flex", gap: space.sm, flexWrap: "wrap", alignItems:"center", marginBottom: space.md }}>
          {tabs.map((t) => (
            <Button key={t || "all"} kind={status === t ? "secondary" : "ghost"}
                    onClick={() => { setStatus(t); setOffset(0); }}>
              {STATUS_RU[t || "all"]}
              {t ? (counts[t] ? " " + counts[t] : "") : (counts.all ? " " + counts.all : "")}
            </Button>
          ))}
          {accounts.length > 1 ? (
            <AccountSelector account={account} onAccountChange={value=>{setAccount(value);setOffset(0);}}/>
          ) : null}
          <span style={{ marginLeft: "auto", minWidth: 220 }}>
            <Input value={q} placeholder="Поиск по тексту объявления"
                   onChange={(e: any) => setQ(e.target.value)} />
          </span>
        </div>

        {!items.length ? (
          <EmptyState title="В кампании пока нет объявлений"
            hint="Напишите задачу одним сообщением выше — BORIS сам подготовит объявления и покажет готовый результат." />
        ) : (
          <Card id="campaign-items" className="boris-campaign-items-card">
            {/* A transformed ancestor changes the fixed editor's containing block and causes a hover/layout loop. */}
            <style>{`#campaign-items.boris-campaign-items-card {
              transform: none !important;
              transition-property: box-shadow, border-color !important;
            }`}</style>
            <div style={{ display:"flex",gap:space.sm,flexWrap:"wrap",alignItems:"center",marginBottom:space.sm }}>
              {!ownerReviewReady ? <span style={font.small}>Показано {offset + 1}–{Math.min(offset + items.length, total)} из {total}</span> : <span style={{...font.bodyStrong,color:color.heading}}>Готовые объявления</span>}
              {!publicationLocked ? <Button kind="ghost" onClick={()=>setSelectedItemIds(selectedItemIds.length===items.length?[]:items.map((x:any)=>x.id))}>{selectedItemIds.length===items.length?"Снять выбор":"Выбрать все на странице"}</Button> : null}
              {!publicationLocked && selectedItemIds.length ? <span style={{...font.small,fontWeight:800,color:"#9A6700"}}>Выбрано: {selectedItemIds.length}</span> : null}
            </div>

            {!publicationLocked ? <details data-testid="campaign-bulk-editor" style={{marginBottom:14,border:"1px solid #F0D9A9",borderRadius:16,background:"#FFFCF6",overflow:"hidden"}}>
              <summary style={{listStyle:"none",cursor:"pointer",padding:"13px 15px",display:"flex",justifyContent:"space-between",gap:10,alignItems:"center",flexWrap:"wrap"}}>
                <span><b style={{color:"#172033"}}>Массовая настройка BORIS</b><small style={{display:"block",marginTop:3,color:"#667085"}}>Тексты с переменными, цена, город, категория, фото и баннеры — для выбранных карточек.</small></span>
                <span style={{fontSize:11,fontWeight:900,padding:"5px 8px",borderRadius:999,background:selectedItemIds.length?"#FFF0D5":"#F2F4F7",color:selectedItemIds.length?"#9A5A00":"#667085"}}>{selectedItemIds.length?selectedItemIds.length+" выбрано":"Выберите карточки"}</span>
              </summary>
              <div style={{padding:"0 15px 15px",display:"grid",gap:14}}>
                <div style={{padding:"11px 12px",borderRadius:12,background:"#fff",border:"1px solid #F1E3C6"}}>
                  <div style={{fontSize:12,fontWeight:900,color:"#7A4B00"}}>Переменные уникализации</div>
                  <div style={{fontSize:11.5,color:"#667085",marginTop:4,lineHeight:1.5}}>Встроенные: <b>{"{товар}"}</b>, <b>{"{услуга}"}</b>, <b>{"{город}"}</b>, <b>{"{цена}"}</b>, <b>{"{категория}"}</b>, <b>{"{номер}"}</b>, <b>{"{вариант}"}</b>. Характеристики товара вроде <b>{"{цвет}"}</b> и <b>{"{размер}"}</b> BORIS возьмёт только из конкретной карточки. Свои варианты задаются ниже.</div>
                  <textarea value={bulkVariablesText} onChange={e=>{setBulkVariablesText(e.target.value);setBulkTransformPreview(null);}} rows={3} placeholder={"Например:\nцвет=Белый, Бежевый, Графит\nматериал=ЛДСП, МДФ"} style={{marginTop:8,width:"100%",boxSizing:"border-box",border:"1px solid #D7C49B",borderRadius:10,padding:"9px 10px",font:"inherit",resize:"vertical"}}/>
                </div>

                <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(260px,1fr))",gap:10}}>
                  <label style={{display:"grid",gap:5,fontSize:12,fontWeight:800,color:"#344054"}}>Шаблон названия
                    <input value={bulkTitleTemplate} onChange={e=>{setBulkTitleTemplate(e.target.value.slice(0,120));setBulkTransformPreview(null);}} placeholder="{товар} {город} {цвет}" style={{border:"1px solid #D0D5DD",borderRadius:10,padding:"10px 11px",font:"inherit"}}/>
                    <small style={{fontWeight:400,color:"#667085"}}>Итоговый заголовок BORIS ограничит 50 символами.</small>
                  </label>
                  <label style={{display:"grid",gap:5,fontSize:12,fontWeight:800,color:"#344054"}}>Что добавить в описание
                    <textarea value={bulkDescriptionTemplate} onChange={e=>{setBulkDescriptionTemplate(e.target.value);setBulkTransformPreview(null);}} rows={4} placeholder={"Например: Цвет: {цвет}. Размер: {размер}. Вариант: {номер}."} style={{border:"1px solid #D0D5DD",borderRadius:10,padding:"10px 11px",font:"inherit",resize:"vertical"}}/>
                  </label>
                </div>

                <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(190px,1fr))",gap:10}}>
                  <label style={{display:"grid",gap:5,fontSize:12,fontWeight:800,color:"#344054"}}>Как изменить описание
                    <BorisSelect value={bulkDescriptionMode} onChange={value=>{setBulkDescriptionMode(value as any);setBulkTransformPreview(null);}} placeholder="Как изменить описание" options={[{value:"append",label:"Добавить в конец"},{value:"prepend",label:"Добавить в начало"},{value:"replace",label:"Заменить описание"}]}/>
                  </label>
                  <label style={{display:"grid",gap:5,fontSize:12,fontWeight:800,color:"#344054"}}>Общая цена, ₽
                    <input value={bulkPrice} inputMode="numeric" onChange={e=>{setBulkPrice(e.target.value);setBulkTransformPreview(null);}} placeholder="Не менять" style={{border:"1px solid #D0D5DD",borderRadius:10,padding:"10px 11px",font:"inherit"}}/>
                  </label>
                  <label style={{display:"grid",gap:5,fontSize:12,fontWeight:800,color:"#344054"}}>Город / география
                    <input value={bulkCity} onChange={e=>{setBulkCity(e.target.value);setBulkTransformPreview(null);}} placeholder="Не менять" style={{border:"1px solid #D0D5DD",borderRadius:10,padding:"10px 11px",font:"inherit"}}/>
                  </label>
                  <label style={{display:"grid",gap:5,fontSize:12,fontWeight:800,color:"#344054"}}>Если ничего не выбрано
                    <BorisSelect value={bulkDirection} onChange={value=>{setBulkDirection(value);setBulkTransformPreview(null);}} placeholder="Вся текущая страница" options={[{value:"",label:"Вся текущая страница"},...Array.from(new Set(items.map((x:any)=>x.segment).filter(Boolean))).map((d:any)=>({value:String(d),label:"Только: "+d}))]}/>
                  </label>
                </div>

                <details style={{border:"1px solid #E4E7EC",borderRadius:12,background:"#fff"}}>
                  <summary style={{cursor:"pointer",padding:"11px 12px",fontSize:12,fontWeight:850,color:"#344054"}}>Категория и характеристики Avito</summary>
                  <div style={{padding:"0 12px 12px",display:"grid",gap:10}}>
                    <div style={{display:"grid",gap:5,fontSize:12,fontWeight:800,color:"#344054"}}><span>Категория</span>
                      <BorisSelect
                        value={bulkTemplateId}
                        placeholder="Не менять категорию"
                        ariaLabel="Категория Avito для массового изменения"
                        options={[{value:"",label:"Не менять категорию"},...bulkCategoryCandidates.map(c=>({value:String(c.template_id),label:String(c.path)}))]}
                        onChange={(next)=>{setBulkTemplateId(next);setBulkTransformPreview(null);}}
                      />
                    </div>
                    {bulkTemplateId && bulkCategoryContract ? <>
                      <div style={{fontSize:11.5,color:"#667085"}}>{bulkCategoryContract.category_path}. Обязательные поля отмечены звёздочкой. Можно использовать переменные в свободных полях.</div>
                      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(210px,1fr))",gap:8,maxHeight:360,overflowY:"auto"}}>
                        {renderAvitoGroups(bulkCategoryContract.fields||[],(f:any)=>{
                          const value=String(bulkCategoryValues[f.field]||"");
                          return <div key={f.field} style={{display:"grid",gap:4,padding:9,border:"1px solid #EEF1F5",borderRadius:10,background:f.required?"#FFFBF3":"#fff"}}>
                            <span style={{fontSize:11.5,fontWeight:800,color:"#344054"}}>{avitoFieldLabel(f)}{f.required?" *":""}</span>
                            {f.allowed_values?.length ? <BorisSelect
                              value={value}
                              placeholder={f.required?"Выберите значение":"Не менять"}
                              ariaLabel={avitoFieldLabel(f)}
                              options={[{value:"",label:f.required?"Выберите значение":"Не менять"},...f.allowed_values.map((v:any)=>({value:String(v),label:String(v)}))]}
                              onChange={(next)=>{setBulkCategoryValues(old=>({...old,[f.field]:next}));setBulkTransformPreview(null);}}
                            /> : <input value={value} onChange={e=>{setBulkCategoryValues(old=>({...old,[f.field]:e.target.value}));setBulkTransformPreview(null);}} placeholder={f.required?"Обязательное значение":"Не менять / оставить пустым"} style={{border:"1px solid #D0D5DD",borderRadius:8,padding:"8px 9px"}}/>}
                            {f.depends_on?<small style={{color:"#667085",lineHeight:1.4}}>{avitoDependencyText(f.depends_on,bulkCategoryContract.fields||[])}</small>:null}
                          </div>;
                        })}
                      </div>
                    </> : null}
                  </div>
                </details>

                <div style={{padding:"11px 12px",borderRadius:12,background:"#F8FAFC",border:"1px solid #E4E7EC"}}>
                  <div style={{fontSize:12,fontWeight:850,color:"#344054"}}>Фото и баннеры для выбранных</div>
                  <div style={{fontSize:11.5,color:"#667085",marginTop:4}}>Эти действия используют тот же набор выбранных карточек. Новые визуалы проходят обычную проверку BORIS.</div>
                  <div style={{display:"flex",gap:7,flexWrap:"wrap",marginTop:9}}>
                    <Button kind="ghost" onClick={()=>photoStackInputRef.current?.click()} disabled={!bulkTargetIds().length||!!feedBusy}>Загрузить свои фото</Button>
                    <Button kind="ghost" onClick={()=>void collectStockForCampaignItems()} disabled={!bulkTargetIds().length||!!feedBusy}>{feedBusy==="auto-stock"?"BORIS подбирает…":"Подобрать фото"}</Button>
                    <Button kind="ghost" onClick={()=>void generateBannersFromOwnTextFlow()} disabled={!bulkTargetIds().length||!!feedBusy}>{feedBusy==="banners"?"BORIS создаёт…":"Сделать баннеры"}</Button>
                  </div>
                </div>

                {bulkTransformPreview ? <div style={{padding:"12px 13px",borderRadius:13,border:"1px solid "+(bulkTransformPreview.status==="ok"?"#ABEFC6":"#FECDCA"),background:bulkTransformPreview.status==="ok"?"#F6FEF9":"#FFF6F5"}}>
                  <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center",flexWrap:"wrap"}}>
                    <b style={{color:"#172033"}}>{bulkTransformPreview.status==="ok"?"Предпросмотр готов":"Нужно заполнить данные"}</b>
                    <span style={{fontSize:11,color:"#667085"}}>{(bulkTransformPreview.items||[]).length} карточек</span>
                  </div>
                  {(bulkTransformPreview.errors||[]).length ? <div style={{display:"grid",gap:5,marginTop:8}}>{(bulkTransformPreview.errors||[]).slice(0,8).map((e:any,idx:number)=><div key={idx} style={{fontSize:11.5,color:"#B42318"}}>Карточка {e.item_id}: {e.message}{e.fields?.length?" — "+e.fields.join(", "):""}</div>)}</div>:null}
                  {(bulkTransformPreview.items||[]).length ? <div style={{display:"grid",gap:7,marginTop:9,maxHeight:260,overflowY:"auto"}}>{(bulkTransformPreview.items||[]).slice(0,10).map((p:any)=><div key={p.item_id} style={{padding:"8px 9px",borderRadius:9,background:"#fff",border:"1px solid #E4E7EC",fontSize:11.5,color:"#475467"}}>
                    <div><b>Название:</b> {p.title_after||p.title_before}</div>
                    {p.city_after!==p.city_before?<div><b>Город:</b> {p.city_before||"—"} → {p.city_after}</div>:null}
                    {String(p.price_after)!==String(p.price_before)?<div><b>Цена:</b> {String(p.price_before||"—")} → {String(p.price_after||"—")}</div>:null}
                    {p.template_id_after!==p.template_id_before?<div><b>Категория:</b> {p.category_after||"—"}</div>:null}
                  </div>)}</div>:null}
                </div> : null}

                <div style={{display:"flex",justifyContent:"flex-end",gap:8,flexWrap:"wrap"}}>
                  <Button kind="ghost" onClick={()=>void previewBulkTransform()} disabled={!!feedBusy||!bulkTargetIds().length}>{feedBusy==="bulk-preview"?"Считаю…":"Посмотреть результат"}</Button>
                  <Button onClick={()=>void applyBulkTransform()} disabled={!!feedBusy||bulkTransformPreview?.status!=="ok"}>{feedBusy==="bulk-apply"?"Применяю…":"Применить к выбранным"}</Button>
                </div>
              </div>
            </details> : null}

            <div data-testid="campaign-ad-card-grid" style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(300px,1fr))",gap:14}}>
            {open!==null ? <div aria-hidden="true" style={{position:"fixed",inset:0,background:"rgba(15,23,42,.28)",backdropFilter:"blur(2px)",zIndex:1190}} /> : null}
            {items.map((it) => (
              <article key={it.id} id={'item-'+it.id} data-editor-open={open===it.id?"true":"false"} style={open===it.id ? {
                                        position:"fixed",right:0,top:0,width:"min(680px,100vw)",height:"100dvh",zIndex:1200,
                                        padding:20,scrollMarginTop:20,border:"1px solid "+color.line,borderRadius:0,background:"#fff",
                                        boxShadow:"-22px 0 70px rgba(15,23,42,.22)",overflowY:"auto",boxSizing:"border-box"
                                      } : {
                                        padding:14,scrollMarginTop:20,border:"1px solid "+color.line,borderRadius:16,background:"#fff",
                                        boxShadow:"0 10px 26px rgba(15,23,42,.05)",overflow:"hidden"
                                      }}>
                {(it.payload?.banner_url || it.payload?.images?.[0]) ? <button type="button" onClick={(e)=>openLightbox(Array.from(new Set([it.payload?.banner_url,...(it.payload?.images||[])].filter(Boolean))),0,it.payload?.title||"Объявление",e.currentTarget)} style={{display:"block",height:180,border:0,padding:0,margin:"-14px -14px 12px",width:"calc(100% + 28px)",background:"#F7F8FA",cursor:"zoom-in",overflow:"hidden"}}><img loading="lazy" decoding="async" src={it.payload?.banner_url || it.payload?.images?.[0]} alt={it.payload?.title||"Фото объявления"} style={{width:"100%",height:"100%",objectFit:"cover"}} /></button> : null}
                <div style={{ display: "flex", gap: space.sm, alignItems: "center",
                              flexWrap: "wrap" }}>
                  {!publicationLocked ? <input type="checkbox" checked={selectedItemIds.includes(it.id)} onChange={(e)=>setSelectedItemIds(e.target.checked ? Array.from(new Set([...selectedItemIds,it.id])) : selectedItemIds.filter(x=>x!==it.id))} aria-label={'Выбрать объявление ' + it.id} /> : null}
                  <div style={{ flex: 1, minWidth: 240 }}>
                    <div style={{ ...font.body, color: color.heading }}>
                      {it.payload?.title || "Без заголовка"}
                    </div>
                    <div style={{...font.small,marginTop:3}}>
                      {[it.geo || it.payload?.city, it.payload?.price ? String(it.payload.price)+" ₽" : "Цена не указана", String(it.payload?.category_path||it.payload?.category||"").split(">").map((x:string)=>x.trim()).filter(Boolean).pop()].filter(Boolean).join(" · ")}
                    </div>
                    <div style={{display:"flex",gap:6,alignItems:"center",flexWrap:"wrap",marginTop:6}}>
                      {(it.payload?.hypothesis || it.payload?.creative_angle) ? <span style={{fontSize:10.5,padding:"4px 7px",borderRadius:999,background:"#FFF7E6",color:"#9A6700",fontWeight:800}}>Гипотеза: {String(it.payload?.hypothesis || it.payload?.creative_angle).replace(/^[A-Z0-9]+:\s*/,"").slice(0,90)}</span> : null}
                      <span style={{fontSize:10.5,color:"#667085"}}>Фото: {(it.payload?.images || []).length}{it.payload?.banner_url ? " · баннер готов" : ""}</span>
                    </div>
                    {String(it.payload?.price_status || "").toUpperCase()==="NEEDS_DATA" ? (
                      <div style={{...font.small,color:"#b54708",marginTop:4}}><b>Нужна цена.</b> BORIS не нашёл подтверждённую цену для этого объявления. Откройте карточку и укажите её.</div>
                    ) : String(it.payload?.price_status || "").toUpperCase()==="PRICE_CONFLICT" || it.payload?.price_conflict ? (
                      <div style={{...font.small,color:"#b54708",marginTop:4}}><b>Найдены разные цены.</b> Выберите правильную цену в карточке и сохраните — BORIS запомнит подтверждённое значение.</div>
                    ) : null}
                  </div>
                  <span style={{fontSize:11,fontWeight:900,padding:"6px 9px",borderRadius:999,background:(publicationCompleted || it.status === "published" || it.status === "ready" || ownerReviewReady)?"#ECFDF3":it.status==="needs_data"?"#FFF7E6":"#EEF4FF",color:(publicationCompleted || it.status === "published" || it.status === "ready" || ownerReviewReady)?"#067647":it.status==="needs_data"?"#B54708":"#315BEA"}}>
                    {publicationCompleted || it.status === "published" ? "Опубликовано" : ownerReviewReady ? (it.status === "ready" ? "Готово" : "Подготовлено") : (STATUS_RU[it.status] || humanizeBusinessText(it.status,"BORIS работает"))}
                  </span>
                  <Button kind="ghost" onClick={()=>openFullItemView(it)}>Посмотреть</Button>
                  {(publicationCompleted || it.status==="published") ? <Button kind="ghost" onClick={()=>window.location.assign("/dashboard/campaign/ab-tests")}>A/B результаты</Button> : null}
                  {!cardEditorLocked ? <>
                    <Button kind="ghost" onClick={()=>regenerateOne(it.id)} disabled={publicationLocked || !!feedBusy}>{feedBusy==="text-"+it.id?"Обновляю…":"Другой текст"}</Button>
                    <Button kind="ghost" onClick={() => startEdit(it)} disabled={cardEditorLocked}>
                      {open === it.id ? "Свернуть" : "Изменить"}
                    </Button>
                  </> : null}
                </div>
                {!cardEditorLocked && open === it.id ? (
                  <div style={{ marginTop: space.sm, display: "grid", gap: space.sm }}>
                    <div style={{position:"sticky",top:-20,zIndex:2,background:"#fff",padding:"10px 0 8px",borderBottom:"1px solid "+color.line,display:"flex",gap:6,overflowX:"auto"}}>
                      {([
                        ["ad","Объявление"],["price","Цена"],["photos","Фото"],["avito","Avito"],["check","Проверка"],["history","История"]
                      ] as const).map(([key,label])=><button key={key} type="button" onClick={()=>setEditorTab(key)} style={{whiteSpace:"nowrap",border:editorTab===key?"1px solid #F59E0B":"1px solid #E4E7EC",background:editorTab===key?"#FFF7E6":"#fff",color:editorTab===key?"#9A5A00":"#475467",borderRadius:999,padding:"7px 10px",fontSize:12,fontWeight:850,cursor:"pointer"}}>{label}</button>)}
                    </div>
                    {(publicationCompleted || publicationPartial) ? <div style={{padding:"10px 12px",border:"1px solid #F3D58B",borderRadius:11,background:"#FFF9EE",fontSize:11.5,lineHeight:1.5,color:"#7A5A2A"}}>
                      <b>Редактируется версия в BORIS.</b> Опубликованное объявление в Avito не изменится автоматически. После сохранения BORIS перепроверит локальную версию, а отправка обновления потребует отдельного подтверждения.
                    </div> : null}
                    {editorTab==="ad" ? <>
                    <div style={{display:"grid",gap:4}}>
                      <Input value={draft.title} placeholder="Заголовок"
                             onChange={(e: any) => setDraft({ ...draft, title: e.target.value.slice(0,50) })} />
                      <div style={{fontSize:11,color:String(draft.title||"").length>45?"#B54708":"#667085",textAlign:"right"}}>{String(draft.title||"").length} / 50</div>
                    </div>
                    <textarea value={draft.description} rows={8}
                              onChange={(e: any) => setDraft({ ...draft, description: e.target.value })}
                              style={{ width: "100%", borderRadius: 10, padding: "12px",
                                       border: "1px solid " + color.line, font: "inherit",lineHeight:1.55,minHeight:180 }} />
                    <div style={{display:"grid",gap:8,padding:"11px 12px",border:"1px solid #F1D59D",borderRadius:12,background:"#FFF9EE"}}>
                      <div style={{fontSize:12,fontWeight:850,color:"#7A4B00"}}>BORIS предлагает улучшить текст</div>
                      <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                        {[
                          ["Сделать короче","Сократи текст без потери важных фактов, цены, условий и ключевых слов. Не придумывай новые обещания."],
                          ["Сделать продающее","Усиль продающую подачу: сильный первый заход, конкретная ценность, снятие возражений и естественный призыв написать. Используй только подтверждённые факты."],
                          ["Сделать проще","Перепиши текст проще и понятнее обычному покупателю. Сохрани все подтверждённые факты."],
                          ["Добавить выгоды","Усиль конкретные выгоды на основе уже подтверждённых данных бизнеса. Ничего не выдумывай."],
                          ["Добавить ключевые слова","Добавь релевантные поисковые фразы естественно и вынеси короткий блок ключевых слов вниз описания."],
                          ["Изменить первый абзац","Перепиши только начало объявления: быстрее объясни, что продаётся, где и почему стоит написать."],
                          ["Переписать полностью","Сделай новый вариант описания с другим смысловым ракурсом, сохранив все факты и ограничения."],
                        ].map(([label,instruction])=><button key={label} type="button" onClick={()=>void runQuickTextEdit(it.id,instruction)} disabled={publicationLocked || !!feedBusy} style={{border:"1px solid #E6D4AF",background:"#fff",borderRadius:999,padding:"6px 9px",fontSize:11,fontWeight:800,color:"#6B4B16",cursor:"pointer"}}>{label}</button>)}
                      </div>
                      <div style={{display:"flex",gap:8,alignItems:"stretch",flexWrap:"wrap"}}>
                        <input value={itemTextPrompts[it.id]||""} onChange={(e)=>setItemTextPrompts((old)=>({...old,[it.id]:e.target.value}))} placeholder="Что нужно изменить? Например: сделай упор на срочный выезд, без скидок" style={{flex:"1 1 320px",border:"1px solid #D7C49B",borderRadius:10,padding:"9px 10px",font:"inherit"}} />
                        <Button type="button" kind="ghost" disabled={publicationLocked || !!feedBusy || !String(itemTextPrompts[it.id]||"").trim()} onClick={()=>void runQuickTextEdit(it.id,String(itemTextPrompts[it.id]||""))}>{feedBusy==="quick-text-"+it.id?"BORIS работает…":"Изменить через BORIS"}</Button>
                      </div>
                      <div style={{fontSize:11,color:"#7A6A4F"}}>Долгая правка выполняется в фоне. Можно закрыть или обновить страницу — прогресс сохранится.</div>
                    </div>
                    <details style={{border:"1px solid #E4E7EC",borderRadius:11,padding:"9px 10px",background:"#FAFBFC"}}>
                      <summary style={{cursor:"pointer",fontSize:12,fontWeight:850,color:"#344054"}}>История версий ({Array.isArray(it.payload?.edit_history)?it.payload.edit_history.length:0})</summary>
                      {Array.isArray(it.payload?.edit_history) && it.payload.edit_history.length ? <div style={{display:"grid",gap:8,marginTop:9}}>
                        {[...it.payload.edit_history].reverse().slice(0,6).map((h:any,index:number)=>{
                          const snap=h?.snapshot||{};
                          const when=h?.at ? new Date(h.at).toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}) : "";
                          return <div key={String(h?.at||index)} style={{padding:"9px 10px",borderRadius:9,background:"#fff",border:"1px solid #ECEEF2"}}>
                            <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center",flexWrap:"wrap"}}>
                              <b style={{fontSize:11,color:"#344054"}}>{String(h?.action||"edit")==="boris_text_edit"?"Версия до правки BORIS":"Версия до ручного изменения"}</b>
                              <span style={{fontSize:10,color:"#98A2B3"}}>{when}</span>
                            </div>
                            {h?.note?<div style={{fontSize:11,color:"#667085",marginTop:4}}>{String(h.note)}</div>:null}
                            <div style={{marginTop:6,fontSize:11,color:"#475467"}}><b>Заголовок:</b> {String(snap.title||"—")}</div>
                            <div style={{marginTop:3,fontSize:11,color:"#667085",lineHeight:1.45,maxHeight:58,overflow:"hidden"}}>{String(snap.description||"").slice(0,220)}{String(snap.description||"").length>220?"…":""}</div>
                            <Button type="button" kind="ghost" onClick={()=>void restoreTextVersion(it,h)} disabled={cardEditorLocked||!!feedBusy} style={{marginTop:7}}>Вернуть этот текст</Button>
                          </div>;
                        })}
                      </div> : <div style={{fontSize:11,color:"#667085",marginTop:8}}>История появится после первой сохранённой правки.</div>}
                    </details>
                    </> : null}
                    {editorTab==="price" ? <div style={{display:"grid",gap:8}}>
                      <div style={{...font.bodyStrong,color:color.heading}}>Цена объявления</div>
                      <div style={font.small}>Используйте только актуальную подтверждённую цену. BORIS перепроверит текст после изменения.</div>
                    <Input value={String(draft.price ?? "")} placeholder="Цена"
                           onChange={(e: any) => setDraft({ ...draft, price: e.target.value })} />
                    </div> : null}
                    {editorTab==="avito" ? <>
                    <div style={{...font.bodyStrong,color:color.heading}}>Настройки Avito</div>
                    <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))", gap:space.sm }}>
                      <Input value={draft.city || ""} placeholder="Город" onChange={(e:any)=>setDraft({...draft,city:e.target.value})} />
                      <Input value={draft.address || ""} placeholder="Адрес / район" onChange={(e:any)=>setDraft({...draft,address:e.target.value})} />
                    </div>
                    <div style={{display:"grid",gap:4}}>
                      <span style={font.small}>Категория</span>
                      <BorisSelect
                        value={String(draft.template_id||"")}
                        placeholder="Выберите категорию"
                        ariaLabel="Категория Avito"
                        options={[{value:"",label:"Выберите категорию"},...(((intake?.directions||[]).find((x:any)=>String(x.direction)===String(it.segment))?.candidates)||[]).map((c:any)=>({value:String(c.template_id),label:String(c.path)}))]}
                        onChange={(next)=>{
                          const liveCandidates=((intake?.directions||[]).find((x:any)=>String(x.direction)===String(it.segment))?.candidates||[]);
                          const c=liveCandidates.find((x:any)=>String(x.template_id)===next);
                          const templateChanged=String(next)!==String(it.payload?.template_id||"");
                          setDraft({...draft,template_id:next,category:c?.path||draft.category,params:templateChanged?"{}":draft.params});
                        }}
                      />
                    </div>
                    <div style={font.small}>Характеристики категории: заполнено {Object.keys(it.payload?.params || {}).length}. Если обязательного значения не хватает, BORIS покажет понятный вопрос выше.</div>
                    </> : null}
                    {editorTab==="photos" ? <div style={{display:"grid",gap:12}}>
                      <div style={{...font.bodyStrong,color:color.heading}}>Фото и баннер</div>
                      <div style={font.small}>Главное фото показывается первым. После изменения фотографий BORIS автоматически снимает старую проверку и перепроверяет карточку.</div>
                      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(132px,1fr))",gap:9}}>
                        {(it.media||[]).length ? (it.media||[]).map((m:any,idx:number)=>{
                          const src=String(m.preview_url||m.url||"");
                          const isHero=Number(it.hero_media_id||0)===Number(m.media_id)||String(m.role||"")==="hero";
                          return <div key={String(m.media_id)}
                            draggable={!cardEditorLocked&&!feedBusy}
                            onDragStart={(e)=>{e.dataTransfer.effectAllowed="move";e.dataTransfer.setData("text/plain",String(idx));}}
                            onDragOver={(e)=>{if(!cardEditorLocked){e.preventDefault();e.dataTransfer.dropEffect="move";}}}
                            onDrop={(e)=>{e.preventDefault();const from=Number(e.dataTransfer.getData("text/plain"));if(Number.isInteger(from))void reorderPhotos(it.id,it.media||[],from,idx);}}
                            title={cardEditorLocked?"":"Перетащите, чтобы изменить порядок"}
                            style={{border:"1px solid "+(isHero?"#F59E0B":color.line),borderRadius:12,padding:7,background:isHero?"#FFF9EE":"#fff",cursor:cardEditorLocked?"default":"grab"}}>
                            <button type="button" onClick={(e)=>openLightbox((it.media||[]).map((x:any)=>String(x.preview_url||x.url||"")).filter(Boolean),idx,it.payload?.title||"Фото объявления",e.currentTarget)} style={{width:"100%",height:92,padding:0,border:0,borderRadius:9,overflow:"hidden",background:"#F6F7F9",cursor:"zoom-in"}}>
                              {src?<img loading="lazy" decoding="async" src={src} alt={isHero?"Главное фото":"Фото объявления"} style={{width:"100%",height:"100%",objectFit:"cover"}}/>:<span style={{fontSize:11,color:"#667085"}}>Фото недоступно</span>}
                            </button>
                            <div style={{display:"flex",justifyContent:"space-between",gap:5,alignItems:"center",marginTop:6}}>
                              <span style={{fontSize:10,fontWeight:850,color:isHero?"#9A5A00":"#667085"}}>{isHero?"Главное":"Фото "+(idx+1)}</span>
                              <span style={{fontSize:9.5,color:"#98A2B3"}}>{cardEditorLocked?"":"↕ порядок"}</span>
                            </div>
                            <div style={{display:"flex",gap:5,flexWrap:"wrap",marginTop:6}}>
                              {!isHero?<button type="button" onClick={()=>void reorderPhotos(it.id,it.media||[],idx,0)} disabled={cardEditorLocked||!!feedBusy} style={{border:"1px solid #E4E7EC",background:"#fff",borderRadius:8,padding:"5px 7px",fontSize:10,fontWeight:800,cursor:"pointer"}}>Сделать главным</button>:null}
                              {idx>0?<button type="button" onClick={()=>void reorderPhotos(it.id,it.media||[],idx,idx-1)} disabled={cardEditorLocked||!!feedBusy} style={{border:"1px solid #E4E7EC",background:"#fff",borderRadius:8,padding:"5px 7px",fontSize:10,fontWeight:800,cursor:"pointer"}}>Раньше</button>:null}
                              {idx<(it.media||[]).length-1?<button type="button" onClick={()=>void reorderPhotos(it.id,it.media||[],idx,idx+1)} disabled={cardEditorLocked||!!feedBusy} style={{border:"1px solid #E4E7EC",background:"#fff",borderRadius:8,padding:"5px 7px",fontSize:10,fontWeight:800,cursor:"pointer"}}>Позже</button>:null}
                              <button type="button" onClick={()=>void removePhoto(it.id,Number(m.media_id))} disabled={cardEditorLocked||!!feedBusy} style={{border:"1px solid #F1B4B4",background:"#fff",color:"#B42318",borderRadius:8,padding:"5px 7px",fontSize:10,fontWeight:800,cursor:"pointer"}}>Удалить</button>
                            </div>
                          </div>;
                        }) : (it.payload?.images||[]).slice(0,6).map((src:string,idx:number)=><div key={src+idx} style={{border:"1px solid "+color.line,borderRadius:12,padding:7}}><img loading="lazy" decoding="async" src={src} alt={idx===0?"Главное фото":"Фото объявления"} style={{width:"100%",height:92,objectFit:"cover",borderRadius:9}}/><div style={{fontSize:10,color:"#667085",marginTop:5}}>{idx===0?"Главное":"Фото "+(idx+1)}</div></div>)}
                      </div>
                      <div style={{display:"flex",gap:7,flexWrap:"wrap"}}>
                        <Button kind="ghost" onClick={()=>{setSelectedItemIds([it.id]);void loadMedia();}} disabled={cardEditorLocked}>Добавить / заменить фото</Button>
                        <Button kind="ghost" onClick={()=>void findPhotoForOne(it.id)} disabled={publicationLocked||!!feedBusy}>{feedBusy==="photo-auto-"+it.id?"BORIS подбирает…":"Подобрать через BORIS"}</Button>
                      </div>
                      {it.payload?.banner_url ? <div style={{display:"grid",gap:8,paddingTop:4,borderTop:"1px solid "+color.line}}>
                        <span style={{...font.bodyStrong,color:color.heading}}>Главный баннер</span>
                        <img loading="lazy" decoding="async" src={it.payload.banner_url} alt="Баннер объявления" style={{width:"100%",maxHeight:280,objectFit:"cover",borderRadius:12,border:"1px solid "+color.line}} />
                        <div style={{display:"flex",gap:7,flexWrap:"wrap"}}>
                          <Button kind="ghost" onClick={()=>void regenerateBannerOne(it.id)} disabled={publicationLocked||!!feedBusy}>{feedBusy==="banner-one-"+it.id?"BORIS создаёт…":"Сделать другой"}</Button>
                          <Button kind="ghost" onClick={()=>{setEditorTab("ad");setItemTextPrompts((old)=>({...old,[it.id]:"Измени текст объявления так, чтобы баннер можно было пересобрать с новым коротким заголовком. Факты не менять."}));}} disabled={publicationLocked}>Изменить текст</Button>
                          <Button kind="ghost" onClick={()=>{setSelectedItemIds([it.id]);void loadMedia();}} disabled={cardEditorLocked}>Заменить фото</Button>
                          <span style={{alignSelf:"center",fontSize:11,color:"#18794E",fontWeight:800}}>Оставить этот — ничего делать не нужно</span>
                        </div>
                      </div> : <div style={{padding:"10px 12px",border:"1px dashed #E7C47B",borderRadius:11,background:"#FFF9EE",fontSize:12,color:"#7A5A2A"}}>Баннер ещё не готов. BORIS создаст его из проверенного фото и заголовка карточки.</div>}
                    </div> : null}
                    {editorTab==="check" ? <div style={{display:"grid",gap:9,padding:"12px 13px",borderRadius:12,background:"#F8FAFC",border:"1px solid #E4E7EC"}}>
                      <div style={{...font.bodyStrong,color:color.heading}}>Проверка объявления</div>
                      <div style={font.small}>Статус: <b>{it.status==="ready"?"Готово":it.status==="needs_data"?"Нужно исправить":"BORIS работает"}</b></div>
                      <div style={font.small}>Цена: <b>{String(it.payload?.price_status||"").toUpperCase()==="VERIFIED"?"подтверждена":String(it.payload?.price_status||"").toUpperCase()==="PRICE_CONFLICT"?"нужно выбрать":"нужно проверить"}</b></div>
                      <div style={font.small}>Фото: <b>{(it.payload?.images||[]).length}</b>{it.payload?.banner_url?" · баннер готов":""}</div>
                      <div style={font.small}>Категория: <b>{it.payload?.category || "требует проверки"}</b></div>
                    </div> : null}
                    {editorTab==="history" ? <section aria-label="История изменений карточки"><CampaignCardHistory available={revisionHistoryAvailable} campaignId={id} item={it} onMutation={apiPost} onSaved={async()=>{await loadItems();await loadWorkflow();}} disabled={cardEditorLocked||!!feedBusy}/></section>:null}
                    <p role="status" style={{fontSize:13,color:"#667085"}}>{draftRecoveryStatus}</p>
                    <div style={{ display: "flex", gap: space.sm, flexWrap:"wrap" }}>
                      <Button onClick={() => save(it)} disabled={cardEditorLocked || saving}>
                        {saving ? "Сохраняю…" : "Сохранить"}
                      </Button>
                      <Button kind="ghost" onClick={cancelEdit}>Отмена</Button>
                    </div>
                    <div style={font.small}>
                      Правка локальная: наружу в Avito ничего не уходит.
                    </div>
                  </div>
                ) : null}
              </article>
            ))}</div>
            {total > PAGE ? (
              <div style={{ display: "flex", gap: space.sm, marginTop: space.md }}>
                <Button kind="ghost" disabled={offset <= 0}
                        onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Назад</Button>
                <Button kind="ghost" disabled={offset + PAGE >= total}
                        onClick={() => setOffset(offset + PAGE)}>Вперёд →</Button>
              </div>
            ) : null}
          </Card>
        )}
      </Page>
  );
}
