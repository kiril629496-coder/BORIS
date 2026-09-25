"use client";

import { useEffect, useMemo, useState } from "react";
import AppShell from "@/app/components/shell/AppShell";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

type UnifiedStatus={
  free_only:boolean;paid_budget_rub:number;
  contacts:{site:string;whatsapp:string;max:string;phone:string};
  channels:{
    telegram:{active_sources:number;waiting:number;sent_today:number;replies_today:number;qualified_total:number};
    email:{active_campaigns:number;ready_contacts:number;sent_today:number;replies_today:number;qualified_total:number};
    free_placements:{enabled_free_platforms:number;blocked_nonfree_or_unverified:number;drafts:number;needs_owner_approval:number;approved:number;posted:number;ready_accounts:number;publications:number};
  };
  totals:{outbound_today:number;replies_today:number;qualified_total:number};
};
type Rule={platform:string;name:string;rule_status:string;checked_at?:string|null;publish_allowed:boolean;publish_reason:string};
type Platform={key:string;name:string;url:string;mode:string;priority:number;audience:string;notes:string;enabled_for_outreach?:boolean;free_policy?:any;seo_topics?:string[];seo_keywords?:string[];channel_type?:string;niche?:string;discovered_automatically?:boolean;registration_status?:string;registration_checkpoint?:string|null;publication_ready?:boolean};
type Registration={platform:string;name:string;url:string;mode:string;email?:string|null;status:string;checkpoint?:string|null;account_url?:string|null;last_error?:string|null;notes?:string|null};
type BootstrapItem={platform:string;name:string;url:string;account_url?:string|null;checkpoint:string;status?:string;last_error?:string|null;action:string;human_action_required:boolean;owner_action_required:boolean;client_action_required?:boolean;verification_module_required?:boolean;executor_role:string;one_time:boolean;requires_interactive_browser?:boolean;accepts_text_value?:boolean;auto_resume_after_verification?:boolean;reusable_across_clients:boolean;after_completion:string;priority:number};
type BootstrapQueue={needed:number;owner_action_required:boolean;client_action_required?:boolean;human_action_required:boolean;verification_module_required?:boolean;verification_module_tasks?:number;reusable_across_clients:boolean;executor_role:string;by_checkpoint?:Record<string,number>;items:BootstrapItem[]};
type WarmupItem={platform:string;name:string;url?:string|null;status?:string;checkpoint?:string|null;updated_at?:string|null;warming_started_at?:string|null;earliest_age_ready_at?:string|null;age_elapsed_days?:number|null;age_remaining_days?:number|null;account_url?:string|null;requirements?:Record<string,any>;supported_client_formats?:string[];owner_action_required:boolean;executor_role:string;reusable_across_clients:boolean;next_action?:string};
type WarmupQueue={warming:number;owner_action_required:boolean;reusable_across_clients:boolean;items:WarmupItem[]};
type Draft={id:string;platform:string;platform_name:string;topic:string;title:string;text:string;status:string;variant:number;publication_url?:string;target_url?:string;site_url?:string};
type Pricing={key:string;title:string;demand:string;result:string;mvp_days:number;full_days:number;mvp_total_internal_cost_estimate:number;mvp_sell_from:number;mvp_sell_to:number;full_sell_from:number;full_sell_to:number;monthly_cogs_from:number;monthly_cogs_to:number;stack:string[];client_documents?:string[];client_inputs?:string[];monthly_components?:string[]};
type Attempt={id:string;platform:string;action:string;status:string;draft_id?:string|null;url?:string|null;error_code?:string|null;error_detail?:string|null;rules_decision?:string|null;created_at:string};
type CrowdCapacityRow={project?:string;site?:string;niche?:string;required?:number;eligible?:number;autonomous?:number;ready?:number;bootstrap_relevant?:number;bootstrap_immediate_relevant?:number;bootstrap_surface_slots?:number;warming_relevant?:number;service_reachable?:number;service_reachable_after_bootstrap?:number;service_reachable_after_warmup?:number;autonomous_deficit?:number;ready_deficit?:number;discovery_deficit_after_bootstrap?:number;discovery_deficit_after_warmup?:number};
type Guardian={status:string;last_run?:string|null;remaining_audits?:number|null;failed?:any[];audited?:any[];free_platforms?:string[];auto_discovered_candidates?:number;discovery?:any;platform_bootstrap?:{needed?:number;owner_action_required?:boolean;human_action_required?:boolean;reusable_across_clients?:boolean;by_checkpoint?:Record<string,number>;items?:BootstrapItem[]};crowd_capacity?:{before?:CrowdCapacityRow[];after?:CrowdCapacityRow[];discovery_forced_by_deficit?:boolean;max_autonomous_deficit_before?:number;strategic_reserve_after?:{target_total?:number;allowed_unique_sites_total?:number;total_deficit?:number;target_per_format?:number;complete?:boolean;formats?:Record<string,{target?:number;allowed_unique_sites?:number;deficit?:number}>};discovery_schedule?:{due?:boolean;normal_due?:boolean;forced_due?:boolean;backoff_active?:boolean;backoff_until?:string|null;previous_new_or_refreshed?:number}};crowd_seo?:{projects?:number;refreshed?:number;drafts_synced?:number;publication_sync_changes?:number;publish_attempts?:number;published_verified?:number;link_checks?:number;replacement_required?:number;replacements_created?:number;errors?:any[]}};
type DiscoveryCandidate={key:string;name:string;url:string;domain:string;niche:string;query:string;score:number;forum_detected:boolean;commercial_context:boolean;register_url?:string|null;create_topic_hint:boolean;evidence?:string[];status:string;discovered_at:string;last_checked_at:string};
type DiscoveryPayload={status:{at?:string|null;queries?:number;new_or_refreshed?:number;total_candidates?:number;errors?:any[]};items:DiscoveryCandidate[]};
type CrowdPlacement={id:string;platform:string;platform_name:string;surface_id?:string|null;surface_name?:string|null;surface_language?:string|null;forum_url:string;target_url:string;keyword:string;link_mode:string;anchor:string;text_variant:number;text:string;status:string;publication_url?:string|null;final_publication_url?:string|null;checked_at?:string|null;http_status?:number|null;link_present?:boolean|null;indexable?:boolean|null;rel?:string|null;link_rel_type?:string|null;relevance?:number|null;iks?:number|null;iks_tier?:string|null;iks_measured_at?:string|null;error?:string|null};
type PlacementSummary={planned:number;active:number;history:number;published:number;verified:number;active_deficit:number};
type CrowdProject={id:string;site:string;domain:string;status:string;content_status?:string;content_approval_mode?:string;content_validation?:{status?:string;errors?:string[];checked_at?:string;owner_action_required?:boolean;mode?:string};internal_review_required?:boolean;active?:boolean;owner_action_required?:boolean;reused_existing?:boolean;publish_all_eligible?:boolean;niche:string;niche_label:string;offer_type?:"goods"|"services"|"mixed";offer_type_label?:string;target_count:number;bonus_count:number;target_pages:{url:string;label:string}[];keywords:string[];anchor_plan:any[];text_variants:{variant:number;text:string}[];placements:CrowdPlacement[];placement_summary?:PlacementSummary;created_at:string;updated_at:string;guarantee_days:number;delivery_rule?:string};
type LivePublicationRow={id:string;project:string;platform:string;surface?:string|null;topic:string;target_url:string;publication_url:string;status:string;checked_at?:string|null;http_status?:number|null;link_present?:boolean|null;indexable?:boolean|null;link_rel_type?:string|null;rel?:string|null};

const crowdPlacementIsHistory=(x:CrowdPlacement)=>["replacement_required","platform_replacement_required"].includes(x.status);
const crowdActivePlacements=(p:CrowdProject)=>p.placements.filter(x=>!crowdPlacementIsHistory(x));
const crowdIksValues=(p:CrowdProject)=>crowdActivePlacements(p).map(x=>x.iks).filter((x):x is number=>typeof x==="number");
const crowdIksAverage=(p:CrowdProject)=>{const v=crowdIksValues(p);return v.length?Math.round(v.reduce((a,b)=>a+b,0)/v.length):null};
const csvCell=(v:any)=>`"${String(v??"").replaceAll('"','""')}"`;
const downloadCrowdCsv=(p:CrowdProject)=>{
  const head=["№","Площадка","Раздел","Язык","ИКС","Качество ИКС","Целевая страница","Ключ","Тип ссылки","Анкор","Статус","Прямой URL поста","HTTP","Ссылка найдена","Индексируется","rel","dofollow/nofollow","Проверено","Активная/история","Ошибка"];
  const rows=p.placements.map((x,i)=>[
    i+1,x.platform_name,x.surface_name||"",x.surface_language||"ru",x.iks??"",x.iks_tier||"unknown",x.target_url,x.keyword,x.link_mode,x.anchor,
    crowdPlacementStatusRu(x.status),x.publication_url||"",x.http_status??"",x.link_present===true?"да":x.link_present===false?"нет":"",
    x.indexable===true?"да":x.indexable===false?"нет":"",x.rel||"",x.link_rel_type||"",x.checked_at||"",
    crowdPlacementIsHistory(x)?"история/замена":"активная",crowdErrorRu(x.error),
  ]);
  const csv="\uFEFF"+[head,...rows].map(r=>r.map(csvCell).join(";")).join("\r\n");
  const blob=new Blob([csv],{type:"text/csv;charset=utf-8"});
  const url=URL.createObjectURL(blob);
  const a=document.createElement("a");a.href=url;a.download=`crowd-seo-${p.domain}-report.csv`;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
};
const sleep=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms));
const api=async(url:string,opts?:RequestInit,retries=2):Promise<any>=>{
  const token=typeof window!=="undefined"?localStorage.getItem("boris_token")||"":"";
  const r=await fetch(url,{credentials:"include",cache:"no-store",...opts,headers:{"Content-Type":"application/json",...(token?{Authorization:`Bearer ${token}`}:{}),...(opts?.headers||{})}});
  if(r.status===429&&retries>0){
    await sleep(350+(url.length%7)*90);
    return api(url,opts,retries-1);
  }
  const d=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(d.detail||d.message||`Ошибка сервера, код ${r.status}`);
  return d;
};
const money=(n:number)=>new Intl.NumberFormat("ru-RU").format(n)+" ₽";
const ruleRu=(s:string)=>({allowed:"можно",blocked:"нельзя",reply_only:"только ответ",review:"нужна проверка",not_checked:"не проверено"} as Record<string,string>)[s]||s;
const statusRu=(s:string)=>({needs_owner_approval:"ждёт согласования",approved:"согласовано",rejected:"отклонено",posted:"опубликовано"} as Record<string,string>)[s]||s;
const topicRu=(s:string)=>({
  virtual_department:"BORIS — виртуальный отдел продаж и рекламы",
  development:"Разработка и автоматизация бизнеса",
  avito:"Автоматизация Avito",
  direct:"Автоматизация Яндекс Директ",
  sales:"Автоматизация продаж",
  integrations:"CRM и подключения к другим системам",
} as Record<string,string>)[s]||s;
const checkpointRu=(s?:string|null)=>({
  captcha_required:"нужно пройти CAPTCHA",
  interactive_captcha_required:"нужна интерактивная CAPTCHA / Turnstile",
  checkpoint_input_not_found:"поле кода на текущем шаге ещё не появилось",
  email_verification_required:"нужно подтвердить почту",
  sms_or_verification_code_required:"нужен код подтверждения",
  account_creation_unverified:"аккаунт не подтвердился после регистрации",
  registration_disabled:"регистрация отключена площадкой",
  free_only_policy:"площадка не подходит для бесплатного размещения",
  login_failed:"вход не выполнен",
  login_result_unclear:"вход не удалось подтвердить",
  registration_form_error:"ошибка формы регистрации",
  registration_form_not_found:"форма регистрации не найдена",
  registration_unavailable:"регистрация сейчас недоступна",
  registration_agreement_required:"нужно принять правила регистрации",
  terms_acceptance_required:"нужно один раз принять условия регистрации",
  age_and_terms_declaration_required:"нужно принять правила и подтвердить возраст 18+",
  registration_disabled_by_site:"регистрация отключена самой площадкой",
  registration_rejected_by_site:"площадка отклонила регистрацию",
  registration_route_unavailable:"путь регистрации на площадке недоступен",
  phone_sms_required:"нужно подтвердить телефон по SMS",
  business_email_required:"нужна корпоративная почта",
  real_identity_required:"нужны настоящие имя и фамилия",
  business_email_and_real_identity_required:"нужны корпоративная почта и настоящие имя/фамилия",
  external_registration_submit_blocked:"внешняя отправка регистрации заблокирована системой безопасности",
  post_submit_review:"нужно проверить результат регистрации",
  form_prepared:"форма готова к отправке",
  account_age_required:"идёт обязательный срок выдержки аккаунта",
  participation_level_required:"идёт прогрев до требуемого уровня участия",
  reputation_required:"идёт прогрев репутации аккаунта",
  post_registration_wait_required:"идёт обязательное ожидание после регистрации",
} as Record<string,string>)[s||""]||s||"—";
const actionRu=(s:string)=>({
  register:"регистрация",verify_registration:"проверка входа",prepare_post:"подготовка публикации",
  publish:"публикация",post_adapter_test:"проверка формы публикации",discover:"поиск площадок",
} as Record<string,string>)[s]||s;
const attemptStatusRu=(s:string)=>({
  success:"успех",blocked:"остановлено",failed:"ошибка",prepared:"подготовлено",
  submitted:"отправлено — проверяем ссылку",in_progress:"в работе",
} as Record<string,string>)[s]||s;
const crowdProjectStatusRu=(s:string)=>({
  analysis_ready:"сайт проанализирован",
  placement_plan_ready:"план размещений готов",
  expanding_publication_pool:"BORIS расширяет пул площадок",
  approved_for_placement:"автопроверка пройдена — BORIS выполняет размещения",
  content_rejected:"проект остановлен",
  duplicate_superseded:"исторический дубль — не участвует в работе",
} as Record<string,string>)[s]||"в работе";
const crowdPlacementStatusRu=(s:string)=>({
  waiting_platform_ready:"BORIS готовит площадку",
  external_checkpoint:"нужно внешнее подтверждение площадки",
  ready_for_content_approval:"BORIS проверяет материал",
  ready_to_publish:"в очереди на публикацию",
  published_unverified:"опубликовано — проверяем ссылку",
  publication_pending_verification:"отправлено — подтверждаем прямой URL",
  verified:"размещение проверено",
  replacement_required:"ссылка пропала — BORIS ставит замену",
  platform_replacement_required:"площадка недоступна — BORIS меняет её",
  adapter_required:"BORIS дорабатывает адаптер площадки",
} as Record<string,string>)[s]||"в работе";
const crowdErrorRu=(s?:string|null)=>({
  link_missing_or_not_indexable:"ссылка не найдена или страница закрыта от индексации",
  publication_url_verification_required:"прямой URL ещё подтверждается",
  post_form_not_found:"форма публикации пока не распознана",
  publisher_process_failed:"ошибка публикационного оператора — BORIS повторит после диагностики",
  platform_no_longer_eligible:"площадка больше не подходит — BORIS автоматически подбирает замену",
} as Record<string,string>)[s||""]||checkpointRu(s)||s||"";
const channelRu=(s?:string)=>({forum:"форум",telegram:"Telegram",social:"соцсеть",service_marketplace:"биржа услуг",community:"сообщество"} as Record<string,string>)[s||""]||s||"—";
const nicheRu=(s?:string)=>({
  business:"бизнес / предприниматели",construction:"строительство",marketing:"маркетинг / реклама",marketplaces:"маркетплейсы",
  furniture:"мебель",logistics:"логистика",auto:"автобизнес",realestate:"недвижимость",horeca:"HoReCa / опт",
  medicine:"медицина",manufacturing:"производство",it:"IT / разработка",goods:"товары",services:"услуги",
} as Record<string,string>)[s||""]||s||"—";
const errorRu=(s?:string|null)=>({
  owner_approval_required:"нужно ваше согласование текста",
  captcha_required:"нужно пройти CAPTCHA",
  account_creation_unverified:"аккаунт не подтвердился после регистрации",
  registration_disabled:"регистрация отключена площадкой",
  free_only_policy:"площадка не подходит для бесплатного размещения",
  login_failed:"вход не выполнен",
  login_result_unclear:"вход не удалось подтвердить",
  registration_form_error:"ошибка формы регистрации",
  registration_unavailable:"регистрация сейчас недоступна",
  business_email_required:"нужна корпоративная почта",
  real_identity_required:"нужны настоящие имя и фамилия",
  business_email_and_real_identity_required:"нужны корпоративная почта и настоящие имя/фамилия",
  publication_url_verification_required:"нужно подтвердить прямую ссылку опубликованной темы",
  rules_block:"правила не разрешают публикацию",
  rules_ambiguous:"правила площадки неоднозначны",
  rules_not_checked:"правила ещё не проверены",
} as Record<string,string>)[s||""]||s||"—";

export default function AcquisitionPage(){
  const [status,setStatus]=useState<UnifiedStatus|null>(null);
  const [rules,setRules]=useState<Rule[]>([]);
  const [platforms,setPlatforms]=useState<Platform[]>([]);
  const [drafts,setDrafts]=useState<Draft[]>([]);
  const [pricing,setPricing]=useState<Pricing[]>([]);
  const [pricingMeta,setPricingMeta]=useState<any>(null);
  const [attempts,setAttempts]=useState<Attempt[]>([]);
  const [registrations,setRegistrations]=useState<Registration[]>([]);
  const [bootstrapQueue,setBootstrapQueue]=useState<BootstrapQueue>({needed:0,owner_action_required:false,human_action_required:false,reusable_across_clients:true,executor_role:"platform_onboarding_worker",items:[]});
  const [warmupQueue,setWarmupQueue]=useState<WarmupQueue>({warming:0,owner_action_required:false,reusable_across_clients:true,items:[]});
  const [guardian,setGuardian]=useState<Guardian|null>(null);
  const [discovery,setDiscovery]=useState<DiscoveryPayload>({status:{},items:[]});
  const [crowdProjects,setCrowdProjects]=useState<CrowdProject[]>([]);
  const [clientSite,setClientSite]=useState("");
  const [clientTarget,setClientTarget]=useState("");
  const [clientBonus,setClientBonus]=useState("0");
  const [tab,setTab]=useState<"overview"|"clients"|"discovery"|"platforms"|"registrations"|"drafts"|"results"|"pricing">("overview");
  const [channelFilter,setChannelFilter]=useState("forum");
  const [nicheFilter,setNicheFilter]=useState("all");
  const [msg,setMsg]=useState("");
  const [busy,setBusy]=useState("");
  const [checkpointValues,setCheckpointValues]=useState<Record<string,string>>({});
  const capacityRows=guardian?.crowd_capacity?.after||[];
  const capacityWorst=capacityRows.reduce<CrowdCapacityRow>((worst,row)=>
    (row.autonomous_deficit??0)>(worst.autonomous_deficit??-1)?row:worst,
    capacityRows[0]||{}
  );
  const strategicReserve=guardian?.crowd_capacity?.strategic_reserve_after;

  const load=async()=>{
    // Не держим весь экран пустым из-за одного медленного API. Каждый блок
    // появляется сразу после своего ответа, а общий цикл только считает ошибки.
    const reqs=[
      api("/api/prospecting/unified/status").then(d=>setStatus(d)),
      api("/api/prospecting/unified/rules").then(d=>setRules(d.items||[])),
      api("/api/prospecting/service-marketplace/platforms").then(d=>setPlatforms(d.items||[])),
      api("/api/prospecting/service-marketplace/drafts").then(d=>setDrafts(d.items||[])),
      api("/api/prospecting/unified/pricing").then(d=>{setPricing(d.items||[]);setPricingMeta(d.summary||null)}),
      api("/api/prospecting/service-marketplace/attempts?limit=300").then(d=>setAttempts(d.items||[])),
      api("/api/prospecting/service-marketplace/registrations").then(d=>setRegistrations(d.items||[])),
      api("/api/prospecting/service-marketplace/bootstrap-queue").then(d=>setBootstrapQueue(d||{needed:0,owner_action_required:false,human_action_required:false,reusable_across_clients:true,executor_role:"platform_onboarding_worker",items:[]})),
      api("/api/prospecting/unified/guardian").then(d=>setGuardian(d||null)),
      api("/api/prospecting/unified/discovery").then(d=>setDiscovery(d||{status:{},items:[]})),
      api("/api/prospecting/service-marketplace/warmup-queue").then(d=>setWarmupQueue(d||{warming:0,owner_action_required:false,reusable_across_clients:true,items:[]})),
    ];
    const x=await Promise.allSettled(reqs);
    const failed=x.filter(v=>v.status==="rejected").length;
    if(failed)setMsg(`Часть данных временно не ответила (${failed}). BORIS повторил запросы и сохранил остальные блоки; обновит недостающие при следующем цикле.`);
  };
  useEffect(()=>{
    let alive=true;
    const refresh=()=>{ if(alive) void load().catch(e=>setMsg(e.message)); };
    refresh();
    const timer=window.setInterval(refresh,15000);
    const onVisible=()=>{ if(document.visibilityState==="visible") refresh(); };
    document.addEventListener("visibilitychange",onVisible);
    return ()=>{ alive=false; window.clearInterval(timer); document.removeEventListener("visibilitychange",onVisible); };
  },[]);
  const loadCrowd=async()=>{
    const d=await api("/api/prospecting/crowd-seo/projects");
    setCrowdProjects(d.items||[]);
  };
  useEffect(()=>{
    if(tab!=="clients"&&tab!=="results") return;
    let alive=true;
    const refresh=()=>{ if(alive) void loadCrowd().catch(e=>setMsg(e.message)); };
    refresh();
    const timer=window.setInterval(refresh,15000);
    return ()=>{ alive=false; window.clearInterval(timer); };
  },[tab]);

  const activeCrowdProjects=useMemo(()=>crowdProjects.filter(p=>p.active!==false),[crowdProjects]);
  const livePublicationRows=useMemo(()=>{
    const byUrl=new Map<string,LivePublicationRow>();
    for(const project of activeCrowdProjects){
      for(const placement of project.placements){
        if(!placement.publication_url) continue;
        byUrl.set(placement.publication_url,{
          id:placement.id,
          project:project.domain,
          platform:placement.platform_name,
          surface:placement.surface_name,
          topic:placement.keyword||placement.surface_name||"Crowd SEO",
          target_url:placement.target_url,
          publication_url:placement.publication_url,
          status:placement.status,
          checked_at:placement.checked_at,
          http_status:placement.http_status,
          link_present:placement.link_present,
          indexable:placement.indexable,
          link_rel_type:placement.link_rel_type,
          rel:placement.rel,
        });
      }
    }
    for(const draft of drafts){
      if(draft.status!=="posted"||!draft.publication_url||byUrl.has(draft.publication_url)) continue;
      byUrl.set(draft.publication_url,{
        id:draft.id,
        project:"BORIS",
        platform:draft.platform_name||draft.platform,
        topic:draft.title||topicRu(draft.topic),
        target_url:draft.target_url||draft.site_url||"https://boris-ai.pro/software-dev",
        publication_url:draft.publication_url,
        status:"published",
      });
    }
    return Array.from(byUrl.values()).sort((a,b)=>String(b.checked_at||"").localeCompare(String(a.checked_at||"")));
  },[activeCrowdProjects,drafts]);
  const humanCheckpointItems=useMemo(()=>(bootstrapQueue.items||[]).filter(x=>
    ["captcha_required","captcha_age_and_terms_required","email_verification_required","sms_or_verification_code_required","terms_acceptance_required","registration_agreement_required","age_and_terms_declaration_required","business_email_required","manual_verification"].includes(x.checkpoint)
  ),[bootstrapQueue]);
  const archivedCrowdCount=useMemo(()=>crowdProjects.filter(p=>p.active===false).length,[crowdProjects]);
  const freePlatforms=useMemo(()=>platforms.filter(x=>x.enabled_for_outreach),[platforms]);
  const readyPlatformKeys=useMemo(()=>new Set(platforms.filter(x=>x.publication_ready).map(x=>x.key)),[platforms]);
  const pending=useMemo(()=>drafts.filter(x=>x.status==="needs_owner_approval"&&readyPlatformKeys.has(x.platform)),[drafts,readyPlatformKeys]);
  const waitingDraftCount=useMemo(()=>drafts.filter(x=>x.status==="needs_owner_approval"&&!readyPlatformKeys.has(x.platform)).length,[drafts,readyPlatformKeys]);
  const visibleDrafts=useMemo(()=>drafts
    .filter(x=>x.status!=="rejected"&&(readyPlatformKeys.has(x.platform)||x.status==="posted"))
    .sort((a,b)=>{
      const p=(x:Draft)=>x.topic==="virtual_department"?0:x.topic==="development"?1:2;
      return p(a)-p(b) || a.status.localeCompare(b.status) || a.platform_name.localeCompare(b.platform_name);
    }),[drafts,readyPlatformKeys]);
  const filteredPlatforms=useMemo(()=>platforms.filter(x=>
    (channelFilter==="all"||x.channel_type===channelFilter) &&
    (nicheFilter==="all"||x.niche===nicheFilter)
  ),[platforms,channelFilter,nicheFilter]);
  const forumCount=useMemo(()=>platforms.filter(x=>x.channel_type==="forum").length,[platforms]);
  const ruleAllowedForums=useMemo(()=>platforms.filter(x=>x.channel_type==="forum"&&x.enabled_for_outreach).length,[platforms]);
  const readyForums=useMemo(()=>platforms.filter(x=>x.channel_type==="forum"&&x.publication_ready).length,[platforms]);
  const bootstrapItems=bootstrapQueue.items||[];

  const inspect=async(key:string)=>{
    setBusy("rule:"+key);setMsg("");
    try{
      const d=await api(`/api/prospecting/unified/rules/${key}/inspect`,{method:"POST"});
      setMsg(`${d.name}: правила проверены → ${ruleRu(d.decision)}.`);
      await load();
    }catch(e:any){setMsg(e.message)}finally{setBusy("")}
  };
  const decide=async(id:string,decision:"approved"|"rejected")=>{
    setBusy("draft:"+id);setMsg("");
    try{
      await api(`/api/prospecting/service-marketplace/drafts/${id}/decision`,{method:"POST",body:JSON.stringify({decision})});
      setMsg(decision==="approved"?"Текст согласован.":"Текст отклонён.");
      await load();
    }catch(e:any){setMsg(e.message)}finally{setBusy("")}
  };
  const generate=async()=>{
    setBusy("generate");setMsg("");
    try{
      const [a,b]=await Promise.all([
        api("/api/prospecting/service-marketplace/drafts/generate",{method:"POST",body:JSON.stringify({platform:"all",topic:"virtual_department",variants:2})}),
        api("/api/prospecting/service-marketplace/drafts/generate",{method:"POST",body:JSON.stringify({platform:"all",topic:"development",variants:2})}),
      ]);
      setMsg(`Обновлено ${(a.generated||0)+(b.generated||0)} материалов по двум главным офферам BORIS. Все ждут вашего согласования.`);
      await load();
    }catch(e:any){setMsg(e.message)}finally{setBusy("")}
  };
  // CROWD_BOOTSTRAP_VERIFY_NOW_V1
  // Специалист проходит одноразовый внешний checkpoint и сразу запускает
  // повторную проверку — не ждём следующего шестичасового guardian-cycle.
  const verifyBootstrap=async(platform:string)=>{
    setBusy("bootstrap:"+platform);setMsg("");
    try{
      const d=await api("/api/prospecting/service-marketplace/bootstrap-queue/"+platform+"/verify",{method:"POST"});
      setMsg(d.message||"Площадка перепроверена. BORIS обновил очередь подготовки.");
      await Promise.all([load(),loadCrowd()]);
    }catch(e:any){setMsg(e.message)}finally{setBusy("")}
  };
  const openBootstrapCheckpoint=(item:BootstrapItem)=>{
    const target=item.account_url||item.url;
    window.open(target,"_blank","noopener,noreferrer");
    if(item.auto_resume_after_verification!==false){
      setMsg(item.accepts_text_value
        ? "Площадка открыта. Если она выдала короткий код, введите его в карточке — BORIS сразу перепроверит и продолжит."
        : "Площадка открыта. Выполните одноразовый внешний шаг; BORIS сам перепроверит аккаунт и продолжит без повторной настройки."
      );
      // Мягкая повторная проверка относится ко всем внешним checkpoint'ам,
      // а не только к CAPTCHA: корпоративная почта, подтверждение личности,
      // реквизиты компании, SSO и обычная ручная регистрация тоже должны
      // автоматически возвращаться в READY/WARMING поток после завершения.
      window.setTimeout(()=>{ void verifyBootstrap(item.platform); },90000);
    }
  };
  const submitCheckpoint=async(platform:string)=>{
    const value=(checkpointValues[platform]||"").trim();
    if(!value){setMsg("Введите код или ответ для площадки.");return}
    setBusy("checkpoint:"+platform);setMsg("");
    try{
      const d=await api("/api/prospecting/service-marketplace/bootstrap-queue/"+platform+"/complete-checkpoint",{
        method:"POST",body:JSON.stringify({value})
      });
      setCheckpointValues(v=>({...v,[platform]:""}));
      setMsg(d.message||"Ответ отправлен. BORIS продолжает регистрацию.");
      await Promise.all([load(),loadCrowd()]);
    }catch(e:any){setMsg(e.message)}finally{setBusy("")}
  };

  const createCrowd=async()=>{
    const site=clientSite.trim();
    const target=Number.parseInt(clientTarget,10);
    const bonus=Number.parseInt(clientBonus||"0",10);
    if(!site){setMsg("Введите сайт клиента.");return}
    if(!Number.isFinite(target)||target<1){setMsg("Укажите количество размещений из тарифа/заказа клиента.");return}
    if(!Number.isFinite(bonus)||bonus<0){setMsg("Бонусные размещения не могут быть отрицательными.");return}
    setBusy("crowd:create");setMsg("");
    try{
      const p=await api("/api/prospecting/crowd-seo/projects",{method:"POST",body:JSON.stringify({site,target_count:target,bonus_count:bonus})});
      setClientSite("");setClientTarget("");setClientBonus("0");
      const total=(p.target_count||0)+(p.bonus_count||0);
      setMsg(p.reused_existing
        ? `Проект ${p.domain} уже существует — BORIS синхронизировал объём заказа: ${total} размещений и продолжил без дубля.`
        : `Проект ${p.domain} создан: BORIS сам определил формат «${p.offer_type_label||"товары / услуги"}», нишу, ключи, страницы и запустил ${total} размещений ровно по заказу клиента.`);
      await loadCrowd();
    }catch(e:any){setMsg(e.message)}finally{setBusy("")}
  };

  return <AppShell active="acquisition" title="Привлечение клиентов" subtitle="Telegram, Email, форумы, VK, правила, результаты и прайс"><main className="min-h-screen bg-slate-50 p-4 md:p-7">
    <div className="mx-auto max-w-[1500px] space-y-5">
      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="text-xs font-extrabold tracking-[.2em] text-emerald-600">BORIS · ПРИВЛЕЧЕНИЕ КЛИЕНТОВ</div>
            <h1 className="mt-2 text-3xl font-bold text-slate-950">Telegram + Email + форумы + VK в одном окне</h1>
            <p className="mt-2 max-w-4xl text-slate-600">Один контур: найти спрос → собрать контакт → подготовить текст → автоматически проверить правила и материал → отправить или опубликовать → сохранить ответ и прямую ссылку. Владельцу показывается результат, а не очередь ручных кнопок.</p>
          </div>
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-5 py-4">
            <div className="text-xs font-bold uppercase text-emerald-700">Режим продвижения</div>
            <div className="mt-1 text-2xl font-bold text-emerald-950">Только бесплатно · 0 ₽</div>
            <div className="mt-1 text-sm text-emerald-800">Платные рекламные разделы автоматически блокируются.</div>
          </div>
        </div>

        {status&&<div className="mt-5 grid gap-3 md:grid-cols-4">
          <a href={status.contacts.site} target="_blank" rel="noreferrer" className="rounded-2xl border border-slate-200 p-4 hover:bg-slate-50"><div className="text-xs text-slate-500">Сайт</div><div className="font-bold text-blue-700">boris-ai.pro</div></a>
          <a href={status.contacts.whatsapp} target="_blank" rel="noreferrer" className="rounded-2xl border border-slate-200 p-4 hover:bg-slate-50"><div className="text-xs text-slate-500">WhatsApp</div><div className="font-bold text-slate-900">{status.contacts.phone}</div></a>
          <a href={status.contacts.max} target="_blank" rel="noreferrer" className="rounded-2xl border border-slate-200 p-4 hover:bg-slate-50"><div className="text-xs text-slate-500">MAX</div><div className="font-bold text-slate-900">Открыть MAX</div></a>
          <div className="rounded-2xl border border-slate-200 p-4"><div className="text-xs text-slate-500">Консультация</div><div className="font-bold text-slate-900">+7 981 967-37-87</div></div>
        </div>}
      </section>

      <nav className="flex flex-wrap gap-2">
        {[
          ["overview","Сводка"],["clients",`SEO для клиентов (${crowdProjects.length})`],["discovery",`Автопоиск форумов (${discovery.items.length})`],["platforms","Площадки и правила"],["registrations",`Регистрации (${registrations.filter(x=>x.status==="ready").length}/${registrations.length})`],["drafts",`Материалы (${pending.length})`],["results",`Результаты (${attempts.length})`],["pricing",`Прайс (${pricing.length})`]
        ].map(([k,t])=><button key={k} onClick={()=>setTab(k as any)} className={`rounded-xl px-4 py-2 text-sm font-bold ${tab===k?"bg-slate-900 text-white":"border border-slate-200 bg-white text-slate-700"}`}>{t}</button>)}
        <a href="/dashboard/monitoring" className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-bold text-slate-700">Telegram-лиды ↗</a>
        <a href="/dashboard/prospecting/manage" className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-bold text-slate-700">Email-рассылка ↗</a>
      </nav>

      {msg&&<div className="rounded-2xl border border-blue-100 bg-blue-50 px-5 py-4 text-sm text-blue-900">{msg}</div>}

      {tab==="overview"&&status&&<>
        <section className="grid gap-4 md:grid-cols-3">
          <div className="rounded-3xl border border-slate-200 bg-white p-6">
            <div className="text-sm font-bold text-blue-600">TELEGRAM</div>
            <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
              <div><span className="text-slate-500">Источников</span><strong className="block text-2xl">{status.channels.telegram.active_sources}</strong></div>
              <div><span className="text-slate-500">Ждут</span><strong className="block text-2xl">{status.channels.telegram.waiting}</strong></div>
              <div><span className="text-slate-500">Сегодня отправлено</span><strong className="block text-2xl">{status.channels.telegram.sent_today}</strong></div>
              <div><span className="text-slate-500">Ответов сегодня</span><strong className="block text-2xl">{status.channels.telegram.replies_today}</strong></div>
            </div>
          </div>
          <div className="rounded-3xl border border-slate-200 bg-white p-6">
            <div className="text-sm font-bold text-orange-600">EMAIL</div>
            <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
              <div><span className="text-slate-500">Кампаний</span><strong className="block text-2xl">{status.channels.email.active_campaigns}</strong></div>
              <div><span className="text-slate-500">Контактов готово</span><strong className="block text-2xl">{status.channels.email.ready_contacts}</strong></div>
              <div><span className="text-slate-500">Сегодня отправлено</span><strong className="block text-2xl">{status.channels.email.sent_today}</strong></div>
              <div><span className="text-slate-500">Ответов сегодня</span><strong className="block text-2xl">{status.channels.email.replies_today}</strong></div>
            </div>
          </div>
          <div className="rounded-3xl border border-slate-200 bg-white p-6">
            <div className="text-sm font-bold text-emerald-600">БЕСПЛАТНЫЕ ПУБЛИКАЦИИ</div>
            <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
              <div><span className="text-slate-500">Разрешённых площадок</span><strong className="block text-2xl">{freePlatforms.length}</strong></div>
              <div><span className="text-slate-500">Ждут проверки</span><strong className="block text-2xl">{pending.length}</strong></div>
              <div><span className="text-slate-500">Проверено и разрешено</span><strong className="block text-2xl">{status.channels.free_placements.approved}</strong></div>
              <div><span className="text-slate-500">Опубликовано</span><strong className="block text-2xl">{status.channels.free_placements.publications}</strong></div>
            </div>
          </div>
        </section>


        {guardian&&<section className="rounded-3xl border border-slate-200 bg-white p-6">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <div className="text-sm font-bold text-violet-600">АВТОКОНТРОЛЬ ФОРУМОВ</div>
              <h2 className="mt-1 text-xl font-bold">BORIS сам перечитывает правила и чистит очередь</h2>
              <p className="mt-1 text-sm text-slate-500">Польза: если площадка стала платной или запретила контакты, BORIS сам убирает её из бесплатного продвижения и не ждёт вашей ручной проверки.</p>
            </div>
            <div className="grid min-w-[340px] grid-cols-3 gap-3 text-center text-sm">
              <div className="rounded-xl bg-slate-50 p-3"><div className="text-slate-500">Статус</div><b>{guardian.status==="ok"?"работает":guardian.status==="partial"?"частично":"ещё не запускался"}</b></div>
              <div className="rounded-xl bg-slate-50 p-3"><div className="text-slate-500">Осталось аудитов</div><b>{guardian.remaining_audits??"—"}</b></div>
              <div className="rounded-xl bg-slate-50 p-3"><div className="text-slate-500">Ошибок цикла</div><b>{guardian.failed?.length||0}</b></div>
            </div>
          </div>
          <div className="mt-3 text-xs text-slate-400">Последний цикл: {guardian.last_run?new Date(guardian.last_run).toLocaleString("ru-RU"):"ещё не завершён"}</div>
          <div className="mt-4 rounded-2xl border border-emerald-100 bg-emerald-50 p-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <div className="text-xs font-bold text-emerald-700">ОБЩИЙ БОЕВОЙ ПУЛ CROWD SEO</div>
                <div className="mt-1 text-sm font-semibold text-emerald-950">Цель — минимум {strategicReserve?.target_total??200} уникальных форумных сайтов с подтверждёнными правилами бесплатного размещения.</div>
                <div className="mt-1 text-xs text-emerald-800">Разделы одного форума не считаются отдельными площадками. Непроверенные кандидаты в это число не входят.</div>
              </div>
              <div className="grid min-w-[360px] grid-cols-3 gap-2 text-center text-xs">
                <div className="rounded-xl bg-white p-3"><span className="text-slate-500">Проверено</span><b className="block text-xl">{strategicReserve?.allowed_unique_sites_total??"—"}</b></div>
                <div className="rounded-xl bg-white p-3"><span className="text-slate-500">Цель</span><b className="block text-xl">{strategicReserve?.target_total??200}</b></div>
                <div className="rounded-xl bg-white p-3"><span className="text-slate-500">Ещё нужно</span><b className="block text-xl">{strategicReserve?.total_deficit??"—"}</b></div>
              </div>
            </div>
          </div>
          <div className="mt-4 rounded-2xl border border-violet-100 bg-violet-50 p-4">
            <div className="text-xs font-bold text-violet-700">SEO ДЛЯ КЛИЕНТОВ · АВТОПИЛОТ</div>
            <div className="mt-1 text-sm font-semibold text-violet-950">BORIS сам двигает автоматически проверенные проекты, проверяет ссылки и ставит замену, если размещение пропало.</div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-7">
              <div className="rounded-xl bg-white p-3"><span className="text-slate-500">Проектов</span><b className="block text-lg">{guardian.crowd_seo?.projects??"—"}</b></div>
              <div className="rounded-xl bg-white p-3"><span className="text-slate-500">Нужно площадок</span><b className="block text-lg">{capacityWorst.required??"—"}</b></div>
              <div className="rounded-xl bg-white p-3"><span className="text-slate-500">Уже READY</span><b className="block text-lg">{capacityWorst.ready??"—"}</b></div>
              <div className="rounded-xl bg-white p-3"><span className="text-slate-500">BORIS доведёт сам</span><b className="block text-lg">{capacityWorst.autonomous??"—"}</b></div>
              <div className="rounded-xl bg-white p-3"><span className="text-slate-500">Быстрая подготовка</span><b className="block text-lg">{capacityWorst.bootstrap_immediate_relevant??capacityWorst.bootstrap_relevant??"—"}</b></div>
              <div className="rounded-xl bg-white p-3"><span className="text-slate-500">После прогрева</span><b className="block text-lg">{capacityWorst.warming_relevant??0}</b></div>
              <div className="rounded-xl bg-white p-3"><span className="text-slate-500">Ещё найти долгосрочно</span><b className="block text-lg">{capacityWorst.discovery_deficit_after_warmup??capacityWorst.discovery_deficit_after_bootstrap??"—"}</b></div>
            </div>
            <div className="mt-3 text-xs text-violet-800">
              {(capacityWorst.autonomous_deficit??0)>0
                ?`Для этого проекта после обычной одноразовой подготовки доступно до ${capacityWorst.service_reachable_after_bootstrap??capacityWorst.service_reachable??0} релевантных площадок; ещё ${capacityWorst.warming_relevant??0} площадка станет рабочей после прогрева аккаунта. Быстрый остаток поиска — ${capacityWorst.discovery_deficit_after_bootstrap??0}, долгосрочный остаток после прогрева — ${capacityWorst.discovery_deficit_after_warmup??capacityWorst.discovery_deficit_after_bootstrap??0}.`
                :"Пула автономных площадок достаточно для планового объёма."}
            </div>
            {guardian.crowd_capacity?.discovery_schedule?.backoff_active&&<div className="mt-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-950">
              <b>Поиск не гоняется по кругу.</b> Последний расширенный поиск не дал новых подходящих площадок, поэтому BORIS временно не повторяет те же запросы. Следующая автоматическая попытка — {guardian.crowd_capacity.discovery_schedule.backoff_until?new Date(guardian.crowd_capacity.discovery_schedule.backoff_until).toLocaleString("ru-RU"):"по следующему безопасному окну"}.
            </div>}
            {bootstrapQueue.needed>0&&<div className="mt-2 text-xs text-violet-800">
              Во всей системе одноразовой подготовки ждут {bootstrapQueue.needed} площадок. Это не задача владельца клиентского проекта; готовые аккаунты переиспользуются между клиентами.
            </div>}
            {warmupQueue.warming>0&&<div className="mt-2 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-950">
              <b>Прогрев — нормальное ожидание, не ошибка.</b> Сейчас прогревается {warmupQueue.warming} аккаунт(ов). BORIS считает срок, перепроверяет готовность автоматически и после READY использует аккаунт для проектов по товарам и услугам. Искусственная накрутка сообщений/реакций не применяется.
            </div>}
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
              <div className="rounded-xl bg-white/70 p-3"><span className="text-slate-500">Попыток публикации</span><b className="block text-base">{guardian.crowd_seo?.publish_attempts??"—"}</b></div>
              <div className="rounded-xl bg-white/70 p-3"><span className="text-slate-500">Проверок ссылок</span><b className="block text-base">{guardian.crowd_seo?.link_checks??"—"}</b></div>
              <div className="rounded-xl bg-white/70 p-3"><span className="text-slate-500">Нужна замена</span><b className="block text-base">{guardian.crowd_seo?.replacement_required??"—"}</b></div>
              <div className="rounded-xl bg-white/70 p-3"><span className="text-slate-500">Ошибок</span><b className="block text-base">{guardian.crowd_seo?.errors?.length??0}</b></div>
            </div>
          </div>
        </section>}

        <section className="rounded-3xl border border-slate-200 bg-white p-6">
          <h2 className="text-xl font-bold">Как работает общий конвейер</h2>
          <div className="mt-4 grid gap-3 md:grid-cols-6">
            {["Найти спрос","Проверить контакт","Подготовить текст","Проверить правила","Согласовать","Отправить → CRM"].map((x,i)=><div key={x} className="rounded-2xl bg-slate-50 p-4"><div className="text-xs font-bold text-slate-400">0{i+1}</div><div className="mt-1 font-semibold">{x}</div></div>)}
          </div>
          <div className="mt-4 rounded-2xl bg-amber-50 p-4 text-sm text-amber-950"><b>Жёсткие стопы:</b> платное размещение, неоднозначные правила, запрет ссылок/контактов и несогласованный текст. CAPTCHA/SMS BORIS распознаёт сам и ставит как одноразовую служебную проверку; после её прохождения автоматически перепроверяет аккаунт и продолжает. Обход антибот-защиты не используется.</div>
        </section>
      </>}


      {tab==="discovery"&&<section className="rounded-3xl border border-slate-200 bg-white p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-xl font-bold">Автопоиск новых форумов</h2>
            <p className="mt-1 max-w-4xl text-sm text-slate-500">BORIS сам ищет форумы по нишам, отбрасывает игровые/нерелевантные сайты и сохраняет только кандидатов с коммерческим контекстом. После этого отдельный контур проверяет правила и бесплатность.</p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-center text-sm">
            <div className="rounded-xl bg-slate-50 p-3"><div className="text-slate-500">Запросов в цикле</div><b>{discovery.status.queries??0}</b></div>
            <div className="rounded-xl bg-slate-50 p-3"><div className="text-slate-500">Кандидатов</div><b>{discovery.items.length}</b></div>
            <div className="rounded-xl bg-slate-50 p-3"><div className="text-slate-500">Ошибок поиска</div><b>{discovery.status.errors?.length||0}</b></div>
          </div>
        </div>
        <div className="mt-3 rounded-xl bg-blue-50 p-4 text-sm text-blue-950"><b>Автоматически:</b> поиск новых форумов запускается сторожем примерно раз в сутки. Вам не нужно вручную искать сайты. Найденная площадка сначала проходит проверку правил и бесплатности и только потом может попасть в рабочую очередь.</div>
        <div className="mt-5 overflow-x-auto">
          <table className="min-w-[1200px] w-full text-left text-sm">
            <thead><tr className="border-b text-slate-500"><th className="py-3">Форум</th><th>Ниша</th><th>Как найден</th><th>Оценка</th><th>Регистрация</th><th>Создание темы</th><th>Признаки</th></tr></thead>
            <tbody>{discovery.items.map(x=><tr key={x.key} className="border-b border-slate-100 align-top">
              <td className="py-4"><a href={x.url} target="_blank" rel="noreferrer" className="font-bold text-blue-700">{x.name}</a><div className="mt-1 text-xs text-slate-400">{x.domain}</div></td>
              <td className="py-4">{x.niche}</td>
              <td className="py-4 max-w-sm text-xs text-slate-600">{x.query}</td>
              <td className="py-4 font-bold">{x.score}</td>
              <td className="py-4">{x.register_url?<a href={x.register_url} target="_blank" rel="noreferrer" className="font-bold text-blue-700">найдена ↗</a>:"ещё не найдена"}</td>
              <td className="py-4">{x.create_topic_hint?"есть признак":"пока не найдено"}</td>
              <td className="py-4 max-w-md text-xs text-slate-500">{(x.evidence||[]).join(", ")||"—"}</td>
            </tr>)}</tbody>
          </table>
          {!discovery.items.length&&<div className="py-8 text-center text-slate-500">В текущем цикле подходящие новые коммерческие форумы ещё не найдены. Старые проверенные площадки остаются в основном реестре.</div>}
        </div>
        <div className="mt-3 text-xs text-slate-400">Последний поиск: {discovery.status.at?new Date(discovery.status.at).toLocaleString("ru-RU"):"ещё не запускался"}</div>
      </section>}

      {tab==="platforms"&&<section className="rounded-3xl border border-slate-200 bg-white p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div><h2 className="text-xl font-bold">Площадки и правила</h2><p className="text-sm text-slate-500">В базе {platforms.length} площадок, из них {forumCount} форумов. Правила разрешают бесплатное размещение на {ruleAllowedForums}; прямо сейчас к публикации готовы {readyForums}. Перед отправкой правила перечитываются повторно.</p></div>
          <div className="flex flex-wrap gap-2">
            <select value={channelFilter} onChange={e=>setChannelFilter(e.target.value)} className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm">
              <option value="forum">Только форумы</option>
              <option value="all">Все каналы</option>
              <option value="telegram">Telegram</option>
              <option value="social">Соцсети</option>
              <option value="service_marketplace">Биржи услуг</option>
              <option value="community">Другие сообщества</option>
            </select>
            <select value={nicheFilter} onChange={e=>setNicheFilter(e.target.value)} className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm">
              <option value="all">Все ниши</option>
              <option value="business">Бизнес / предприниматели</option>
              <option value="construction">Строительство</option>
              <option value="marketing">Маркетинг / реклама</option>
              <option value="it">IT / разработка</option>
              <option value="marketplaces">Маркетплейсы</option>
              <option value="furniture">Мебель</option>
              <option value="logistics">Логистика</option>
              <option value="auto">Автобизнес</option>
              <option value="realestate">Недвижимость</option>
              <option value="horeca">HoReCa / опт</option>
              <option value="medicine">Медицина</option>
              <option value="manufacturing">Производство</option>
              <option value="services">Услуги</option>
            </select>
          </div>
        </div>
        <div className="mt-3 text-sm text-slate-500">Показано: <b>{filteredPlatforms.length}</b>. Главная цель: найти бесплатные места, где можно законно создать тему/обсуждение про BORIS или услуги разработки.</div>
        <div className="mt-5 overflow-x-auto">
          <table className="min-w-[1500px] w-full text-left text-sm">
            <thead><tr className="border-b text-slate-500"><th className="py-3">Площадка</th><th>Тип</th><th>Ниша</th><th>Аудитория</th><th>Правила</th><th>Бесплатно</th><th>Готово к публикации</th><th>Что продвигаем</th><th>Ключевые слова</th><th></th></tr></thead>
            <tbody>{filteredPlatforms.map(p=>{const r=rules.find(x=>x.platform===p.key);const ok=p.enabled_for_outreach;return <tr key={p.key} className="border-b border-slate-100 align-top">
              <td className="py-4"><a href={p.url} target="_blank" rel="noreferrer" className="font-bold text-blue-700">{p.name}</a><div className="mt-1 text-xs text-slate-400">{p.discovered_automatically?"найдено автоматически":"база BORIS"}</div></td>
              <td className="py-4">{channelRu(p.channel_type)}</td>
              <td className="py-4">{nicheRu(p.niche)}</td>
              <td className="py-4 pr-4 text-slate-600">{p.audience}</td>
              <td className="py-4"><span className={`rounded-full px-2 py-1 text-xs font-bold ${r?.rule_status==="allowed"?"bg-emerald-50 text-emerald-700":r?.rule_status==="blocked"?"bg-rose-50 text-rose-700":"bg-amber-50 text-amber-700"}`}>{ruleRu(r?.rule_status||"not_checked")}</span><div className="mt-1 text-xs text-slate-400">{r?.checked_at?new Date(r.checked_at).toLocaleString("ru-RU"):"—"}</div></td>
              <td className="py-4 font-bold">{ok?"Да":"Нет / пока нет"}<div className="mt-1 text-xs font-normal text-slate-400">{humanizeBusinessText(p.free_policy?.action||p.free_policy?.reason,"проверить правила площадки")}</div></td>
              <td className="py-4">{p.publication_ready?<span className="rounded-full bg-emerald-50 px-2 py-1 text-xs font-bold text-emerald-700">Да, можно публиковать</span>:<><span className="rounded-full bg-amber-50 px-2 py-1 text-xs font-bold text-amber-700">Пока нет</span><div className="mt-2 text-xs text-slate-500">{!ok?"сначала площадка должна пройти правила":p.registration_checkpoint?checkpointRu(p.registration_checkpoint):p.registration_status==="ready"?"аккаунт готов":"нужна регистрация"}</div></>}</td>
              <td className="py-4"><div className="max-w-sm space-y-1 text-xs text-slate-700">{(p.seo_topics||[]).map(x=><div key={x}>• {x}</div>)}</div></td>
              <td className="py-4"><div className="max-w-sm text-xs text-slate-500">{(p.seo_keywords||[]).join(", ")}</div></td>
              <td className="py-4"><button disabled={busy===("rule:"+p.key)} onClick={()=>inspect(p.key)} className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-bold hover:bg-slate-50 disabled:opacity-50">Перечитать правила</button></td>
            </tr>})}</tbody>
          </table>
        </div>
      </section>}


      {tab==="registrations"&&<section className="rounded-3xl border border-slate-200 bg-white p-6">
        <div>
          <h2 className="text-xl font-bold">Регистрации площадок</h2>
          <p className="text-sm text-slate-500">Здесь видно, где BORIS уже может работать сам, а где нужна одноразовая служебная подготовка площадки. Владелец клиентского проекта не работает оператором регистраций.</p>
        </div>
        {warmupQueue.items.length>0&&<div className="mt-5 rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
          <div className="font-bold text-emerald-950">Прогрев аккаунтов: {warmupQueue.warming}</div>
          <div className="mt-1 text-sm text-emerald-900">Ожидание 15 дней или дольше допустимо. Это общий актив BORIS, а не отдельный аккаунт одного клиента: после готовности он используется для подходящих проектов по товарам и услугам. BORIS перепроверяет право публикации автоматически.</div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {warmupQueue.items.map(item=><div key={item.platform} className="rounded-xl bg-white p-4">
              <div className="font-bold text-slate-900">{item.name||item.platform}</div>
              <div className="mt-1 text-sm text-emerald-800">{checkpointRu(item.checkpoint)}</div>
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-lg bg-slate-50 p-2"><span className="text-slate-500">Возраст аккаунта</span><b className="block">{item.age_elapsed_days??"—"} дн.</b></div>
                <div className="rounded-lg bg-slate-50 p-2"><span className="text-slate-500">Осталось по сроку</span><b className="block">{item.age_remaining_days??"—"} дн.</b></div>
              </div>
              {item.earliest_age_ready_at&&<div className="mt-2 text-xs text-slate-600">Не раньше: <b>{new Date(item.earliest_age_ready_at).toLocaleString("ru-RU")}</b>. Дополнительные требования площадки тоже должны быть выполнены.</div>}
              {!!item.supported_client_formats?.length&&<div className="mt-2 text-xs text-slate-600">Подходит для: <b>{item.supported_client_formats.map(x=>x==="goods"?"товаров":x==="services"?"услуг":x).join(" + ")}</b>.</div>}
              <div className="mt-2 text-xs text-slate-500">{item.next_action||"BORIS сам перепроверит готовность."}</div>
            </div>)}
          </div>
        </div>}
        {bootstrapItems.length>0&&<div className="mt-5 rounded-2xl border border-violet-200 bg-violet-50 p-5">
          <div className="font-bold text-violet-950">Служебная очередь верификации: {bootstrapQueue.verification_module_tasks??bootstrapItems.length}</div>
          <div className="mt-1 text-sm text-violet-900">BORIS сам заполняет регистрацию, сохраняет точку продолжения и после подтверждения доводит площадку до READY. Если сам сайт показывает интерактивную защитную проверку или требует принять условия, нужен только один внешний ручной шаг; после него повторно настраивать площадку не надо.</div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {bootstrapItems.map(item=><div key={item.platform} className="rounded-xl bg-white p-4">
              <div className="font-bold text-slate-900">{item.name||item.platform}</div>
              <div className="mt-1 text-sm text-violet-800">{item.action||checkpointRu(item.checkpoint)}</div>
              <div className="mt-1 text-xs text-slate-500">{item.after_completion||"После подготовки BORIS автоматически проверит аккаунт и продолжит работу."}</div>
              {item.last_error&&<div className="mt-1 text-xs text-slate-400">{humanizeBusinessText(item.last_error,"Нужен одноразовый внешний шаг подготовки площадки")}</div>}
              <div className="mt-3 flex flex-wrap gap-2">
                <button onClick={()=>openBootstrapCheckpoint(item)} className="inline-block rounded-lg border border-slate-200 px-3 py-2 text-xs font-bold text-blue-700">Открыть проверку ↗</button>
                <button
                  disabled={busy===("bootstrap:"+item.platform)}
                  onClick={()=>verifyBootstrap(item.platform)}
                  className="rounded-lg bg-violet-700 px-3 py-2 text-xs font-bold text-white disabled:opacity-50"
                >
                  {busy===("bootstrap:"+item.platform)?"Проверяю…":"Проверить после прохождения"}
                </button>
              </div>
              {item.accepts_text_value&&<div className="mt-3 flex gap-2">
                <input
                  value={checkpointValues[item.platform]||""}
                  onChange={e=>setCheckpointValues(v=>({...v,[item.platform]:e.target.value}))}
                  placeholder="SMS/OTP-код"
                  className="min-w-0 flex-1 rounded-lg border border-violet-200 px-3 py-2 text-sm outline-none focus:border-violet-500"
                />
                <button
                  disabled={busy===("checkpoint:"+item.platform)||!(checkpointValues[item.platform]||"").trim()}
                  onClick={()=>void submitCheckpoint(item.platform)}
                  className="rounded-lg bg-slate-900 px-3 py-2 text-xs font-bold text-white disabled:opacity-40"
                >
                  {busy===("checkpoint:"+item.platform)?"Отправляю…":"Отправить и продолжить"}
                </button>
              </div>}
              <div className="mt-2 text-[11px] text-slate-400">{item.accepts_text_value?"Короткий код BORIS подставит и сразу перепроверит сам.":"Здесь не нужен код в BORIS: выполните одноразовый шаг на площадке. После открытия карточка автоматически запускает мягкую повторную проверку."}</div>
            </div>)}
          </div>
        </div>}
        <div className="mt-5 overflow-x-auto">
          <table className="min-w-[1150px] w-full text-left text-sm">
            <thead><tr className="border-b text-slate-500"><th className="py-3">Площадка</th><th>Статус</th><th>Почта</th><th>Контрольная точка</th><th>Профиль</th><th>Последняя ошибка</th></tr></thead>
            <tbody>{registrations.map(r=><tr key={r.platform} className="border-b border-slate-100 align-top">
              <td className="py-4"><a href={r.url} target="_blank" rel="noreferrer" className="font-bold text-blue-700">{r.name}</a><div className="mt-1 text-xs text-slate-400">{humanizeBusinessText(r.mode,"режим площадки")}</div></td>
              <td className="py-4"><span className={`rounded-full px-2 py-1 text-xs font-bold ${r.status==="ready"?"bg-emerald-50 text-emerald-700":r.status==="blocked"?"bg-rose-50 text-rose-700":r.status==="verification_required"?"bg-amber-50 text-amber-700":"bg-slate-100 text-slate-700"}`}>{r.status==="ready"?"готово":r.status==="not_registered"?"не зарегистрирован":r.status==="in_progress"?"регистрация идёт":r.status==="verification_required"?"нужно подтверждение":r.status==="blocked"?"заблокировано":humanizeBusinessText(r.status,"состояние уточняется")}</span></td>
              <td className="py-4">{r.email||"—"}</td>
              <td className="py-4 text-slate-600">{checkpointRu(r.checkpoint)}</td>
              <td className="py-4">{r.account_url?<a href={r.account_url} target="_blank" rel="noreferrer" className="font-bold text-blue-700">Открыть ↗</a>:"—"}</td>
              <td className="py-4 max-w-md text-xs text-slate-500">{r.last_error?humanizeBusinessText(r.last_error,"Причина уточняется"):"—"}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </section>}

      {tab==="drafts"&&<section className="rounded-3xl border border-slate-200 bg-white p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="text-xl font-bold">Материалы для продвижения BORIS и услуг разработки</h2><p className="text-sm text-slate-500">Главные офферы: BORIS как виртуальный отдел продаж и рекламы под ключ и отдельные услуги разработки/автоматизации бизнеса. Здесь показываются только материалы для READY-площадок. Ещё {waitingDraftCount} черновиков сохранены и ждут готовности площадок — вашего участия по ним сейчас не требуется.</p></div>
          <button disabled={busy==="generate"} onClick={generate} className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">Обновить бесплатные варианты</button>
        </div>
        <div className="mt-5 space-y-4">
          {visibleDrafts.map(d=><article key={d.id} className="rounded-2xl border border-slate-200 p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div><div className="font-bold">{d.title}</div><div className="mt-1 text-sm text-slate-600">{d.platform_name} · {topicRu(d.topic)}</div><div className="text-xs text-slate-500">{statusRu(d.status)} · вариант {d.variant} · {d.id}</div>{d.status==="needs_owner_approval"&&!readyPlatformKeys.has(d.platform)&&<div className="mt-2 inline-block rounded-full bg-slate-100 px-2 py-1 text-xs font-bold text-slate-600">Согласование позже — площадка ещё не READY</div>}</div>
              <div className="flex gap-2">
                {d.status==="needs_owner_approval"&&readyPlatformKeys.has(d.platform)&&<>
                  <button disabled={busy===("draft:"+d.id)} onClick={()=>decide(d.id,"approved")} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-bold text-white">Согласовать</button>
                  <button disabled={busy===("draft:"+d.id)} onClick={()=>decide(d.id,"rejected")} className="rounded-lg bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700">Отклонить</button>
                </>}
                {d.publication_url&&<a href={d.publication_url} target="_blank" rel="noreferrer" className="rounded-lg bg-blue-50 px-3 py-2 text-xs font-bold text-blue-700">Пост ↗</a>}
              </div>
            </div>
            <pre className="mt-4 whitespace-pre-wrap rounded-xl bg-slate-50 p-4 text-sm leading-6 text-slate-700">{d.text}</pre>
          </article>)}
        </div>
      </section>}


      {tab==="results"&&<section className="rounded-3xl border border-slate-200 bg-white p-6">
        <div>
          <h2 className="text-xl font-bold">Результаты публикаций и ошибок</h2>
          <p className="text-sm text-slate-500">Каждая попытка заканчивается либо прямой ссылкой на пост, либо точной ошибкой и причиной остановки.</p>
        </div>

        <div className="mt-5 rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs font-extrabold tracking-[.14em] text-emerald-700">ЖИВЫЕ РАЗМЕЩЕНИЯ BORIS</div>
              <div className="mt-1 text-xl font-bold text-emerald-950">{livePublicationRows.length} реальных URL</div>
              <div className="mt-1 text-sm text-emerald-900">Только фактически опубликованные страницы. Черновики и неподтверждённые попытки сюда не попадают.</div>
            </div>
          </div>
          <div className="mt-4 overflow-x-auto">
            <table className="min-w-[1100px] w-full text-left text-xs">
              <thead><tr className="border-b border-emerald-200 text-emerald-800"><th className="py-2">Проект</th><th>Площадка</th><th>Ключ / тема</th><th>Ссылка BORIS</th><th>Проверка</th><th>Живой пост</th></tr></thead>
              <tbody>{livePublicationRows.map(row=><tr key={row.publication_url} className="border-b border-emerald-100 align-top">
                <td className="py-3 font-semibold">{row.project}</td>
                <td className="py-3"><div className="font-semibold">{row.platform}</div>{row.surface&&<div className="mt-1 text-[11px] text-emerald-700">{row.surface}</div>}</td>
                <td className="py-3"><div className="font-semibold">{row.topic||"—"}</div><div className="mt-1 text-[11px] text-emerald-700">{row.status==="published"?"опубликовано":crowdPlacementStatusRu(row.status)}</div></td>
                <td className="py-3 max-w-xs"><a href={row.target_url} target="_blank" rel="noreferrer" className="font-semibold text-blue-700">{row.target_url}</a></td>
                <td className="py-3">{row.checked_at?<><b>{row.http_status?"HTTP "+row.http_status:"проверено"}</b><div>{row.link_present===true?"ссылка найдена":row.link_present===false?"ссылка не найдена":"—"} · {row.indexable===true?"индексируется":row.indexable===false?"не индексируется":"—"}</div><div>{row.link_rel_type||row.rel||"rel —"}</div></>:<span className="text-amber-700">опубликовано · SEO-проверка синхронизируется</span>}</td>
                <td className="py-3"><a href={row.publication_url} target="_blank" rel="noreferrer" className="rounded-lg bg-emerald-700 px-3 py-2 font-bold text-white">Открыть пост ↗</a></td>
              </tr>)}</tbody>
            </table>
            {!livePublicationRows.length&&<div className="py-6 text-center text-sm text-emerald-900">Живых размещений пока нет.</div>}
          </div>
        </div>

        {humanCheckpointItems.length>0&&<div className="mt-5 rounded-2xl border border-amber-200 bg-amber-50 p-5">
          <div className="text-xs font-extrabold tracking-[.14em] text-amber-700">НУЖНО ПОДТВЕРДИТЬ ПЛОЩАДКУ</div>
          <div className="mt-1 text-lg font-bold text-amber-950">{humanCheckpointItems.length} одноразовых проверок</div>
          <div className="mt-1 text-sm text-amber-900">Защитная проверка, код, почта или условия не выбрасывают площадку из работы. Откройте одноразовый шаг; BORIS сам запустит повторную проверку, а кнопка «Проверить и продолжить» остаётся для немедленной проверки.</div>
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            {humanCheckpointItems.map(item=><div key={item.platform} className="rounded-xl border border-amber-200 bg-white p-4">
              <div className="font-bold text-slate-900">{item.name}</div>
              <div className="mt-1 text-sm text-amber-800">{checkpointRu(item.checkpoint)}</div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button onClick={()=>openBootstrapCheckpoint(item)} className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-900">Открыть площадку ↗</button>
                <button disabled={busy===("bootstrap:"+item.platform)} onClick={()=>void verifyBootstrap(item.platform)} className="rounded-lg bg-slate-900 px-3 py-2 text-xs font-bold text-white disabled:opacity-50">{busy===("bootstrap:"+item.platform)?"Проверяю…":"Проверить и продолжить"}</button>
              </div>
            </div>)}
          </div>
        </div>}

        <div className="mt-5 overflow-x-auto">
          <table className="min-w-[1100px] w-full text-left text-sm">
            <thead><tr className="border-b text-slate-500"><th className="py-3">Время</th><th>Площадка</th><th>Действие</th><th>Результат</th><th>Ссылка</th><th>Ошибка / причина</th><th>Правила</th></tr></thead>
            <tbody>{attempts.map(a=><tr key={a.id} className="border-b border-slate-100 align-top">
              <td className="py-4 whitespace-nowrap">{new Date(a.created_at).toLocaleString("ru-RU")}</td>
              <td className="py-4 font-semibold">{platforms.find(p=>p.key===a.platform)?.name||humanizeBusinessText(a.platform,"Площадка")}</td>
              <td className="py-4">{actionRu(a.action)}</td>
              <td className="py-4"><span className={`rounded-full px-2 py-1 text-xs font-bold ${a.status==="success"?"bg-emerald-50 text-emerald-700":a.status==="blocked"?"bg-amber-50 text-amber-700":"bg-rose-50 text-rose-700"}`}>{attemptStatusRu(a.status)}</span></td>
              <td className="py-4">{a.url?<a href={a.url} target="_blank" rel="noreferrer" className="font-bold text-blue-700">Открыть результат ↗</a>:"—"}</td>
              <td className="py-4 max-w-md"><div className="font-semibold text-slate-800">{errorRu(a.error_code)}</div><div className="text-xs text-slate-500">{a.error_detail?humanizeBusinessText(a.error_detail,"Подробность ошибки сохранена в журнале"):"—"}</div></td>
              <td className="py-4">{a.rules_decision?ruleRu(a.rules_decision):"—"}</td>
            </tr>)}</tbody>
          </table>
          {!attempts.length&&<div className="py-8 text-center text-slate-500">Попыток публикации пока нет. После первого запуска здесь будет URL либо причина ошибки.</div>}
        </div>
      </section>}

      {tab==="clients"&&<section className="rounded-3xl border border-slate-200 bg-white p-6">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <h2 className="text-xl font-bold">SEO-размещения для клиентов · товары и услуги · объём из заказа</h2>
            <p className="mt-1 max-w-4xl text-sm text-slate-500">Клиент присылает адрес сайта, а количество размещений BORIS берёт из оплаченного тарифа/заказа. Нишу, страницы, ключи, ссылки, тексты, разрешённые тематические форумы, прогрев аккаунтов, публикации и контроль прямых URL BORIS ведёт сам.</p>
          </div>
          <div className="rounded-2xl bg-emerald-50 px-5 py-4 text-sm text-emerald-950"><b>Объём:</b> по конкретному заказу клиента · гарантия контроля 30 дней · только бесплатные площадки</div>
        </div>
        <div className="mt-5 rounded-2xl border border-blue-100 bg-blue-50 p-5">
          <div className="text-sm font-bold text-blue-950">Что нужно для запуска</div>
          <div className="mt-1 text-2xl font-bold text-blue-950">Сайт + объём оплаченного заказа</div>
          <div className="mt-4 grid gap-2 md:grid-cols-[minmax(0,1fr)_180px_180px_auto]">
            <input value={clientSite} onChange={e=>setClientSite(e.target.value)} placeholder="https://site-client.ru" className="min-w-0 rounded-xl border border-blue-200 bg-white px-4 py-3 text-sm outline-none focus:border-blue-400"/>
            <input type="number" min={1} max={500} value={clientTarget} onChange={e=>setClientTarget(e.target.value)} placeholder="Размещений" className="min-w-0 rounded-xl border border-blue-200 bg-white px-4 py-3 text-sm outline-none focus:border-blue-400"/>
            <input type="number" min={0} max={200} value={clientBonus} onChange={e=>setClientBonus(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")void createCrowd()}} placeholder="Бонусных, если есть" className="min-w-0 rounded-xl border border-blue-200 bg-white px-4 py-3 text-sm outline-none focus:border-blue-400"/>
            <button disabled={busy==="crowd:create"} onClick={()=>void createCrowd()} className="rounded-xl bg-blue-700 px-5 py-3 text-sm font-bold text-white disabled:opacity-50">{busy==="crowd:create"?"Анализирую сайт…":"Создать SEO-проект"}</button>
          </div>
          <div className="mt-3 text-xs text-blue-800">Количество не придумывается системой: оно должно совпадать с заказом клиента. Ниша, ключи, страницы, анкоры, тексты и площадки формируются автоматически. OpenAI для текстов этого контура не используется.</div>
        </div>

        <div className="mt-6 space-y-5">
          {activeCrowdProjects.map(p=><article data-testid="crowd-active-project" key={p.id} className="rounded-2xl border border-slate-200 p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <a href={p.site} target="_blank" rel="noreferrer" className="text-lg font-bold text-blue-700">{p.domain}</a>
                <div className="mt-1 text-sm text-slate-600">{p.offer_type_label||"товары / услуги"} · {p.niche_label} · {p.publish_all_eligible?"внутренний пул без фиксированного пакета":((p.target_count||0)+(p.bonus_count||0))+" размещений по заказу"} · гарантия {p.guarantee_days} дней</div>
                {p.publish_all_eligible&&<div className="mt-1 text-xs font-semibold text-violet-700">Внутренний проект BORIS: используется весь подходящий пул для live-проверки и расширения общей базы.</div>}
                <div className="mt-1 text-xs text-slate-500">Состояние: {crowdProjectStatusRu(p.status)} · {p.content_status==="approved"?"автопроверка материалов пройдена":p.content_status==="internal_review_required"?"BORIS исправляет внутреннюю проверку сам":p.content_status==="rejected"?"проект остановлен":"BORIS проверяет материалы автоматически"}</div>
                <div className={"mt-3 inline-flex rounded-full px-3 py-1 text-xs font-bold "+(p.content_validation?.status==="PASS"?"bg-emerald-50 text-emerald-700":"bg-blue-50 text-blue-700")}>
                  {p.content_validation?.status==="PASS"?"✓ Автопроверка PASS · вам ничего согласовывать не нужно":"BORIS сам проверяет проект · владелец не оператор"}
                </div>
              </div>
              <div>
                <div className="flex justify-end"><button onClick={()=>downloadCrowdCsv(p)} className="mb-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50">Скачать отчёт CSV</button></div>
                <div className="grid grid-cols-4 gap-2 text-center text-xs">
                  <div className="rounded-xl bg-slate-50 px-3 py-2"><b className="block text-lg">{p.placement_summary?.planned??(p.target_count+p.bonus_count)}</b>план</div>
                  <div className="rounded-xl bg-blue-50 px-3 py-2"><b className="block text-lg text-blue-700">{p.placement_summary?.active??crowdActivePlacements(p).length}</b>активно</div>
                  <div className="rounded-xl bg-emerald-50 px-3 py-2"><b className="block text-lg text-emerald-700">{p.placement_summary?.published??crowdActivePlacements(p).filter(x=>!!x.publication_url).length}</b>URL</div>
                  <div className="rounded-xl bg-violet-50 px-3 py-2"><b className="block text-lg text-violet-700">{crowdIksAverage(p)??"—"}</b>ср. ИКС</div>
                </div>
                {(p.placement_summary?.history??0)>0&&<div className="mt-1 text-right text-[11px] text-slate-400">История замен: {p.placement_summary?.history}</div>}
              </div>
            </div>

            <div className="mt-4 grid gap-3 lg:grid-cols-3">
              <div className="rounded-xl bg-slate-50 p-4"><div className="text-xs font-bold text-slate-500">BORIS выбрал страницы</div><div className="mt-2 space-y-1 text-xs">{p.target_pages.map(x=><a key={x.url} href={x.url} target="_blank" rel="noreferrer" className="block truncate font-semibold text-blue-700">{x.label}</a>)}</div></div>
              <div className="rounded-xl bg-slate-50 p-4"><div className="text-xs font-bold text-slate-500">BORIS собрал ключи</div><div className="mt-2 text-xs leading-6 text-slate-700">{p.keywords.join(", ")||"—"}</div></div>
              <div className="rounded-xl bg-slate-50 p-4"><div className="text-xs font-bold text-slate-500">Профиль ссылок · активный план</div><div className="mt-2 text-xs leading-6 text-slate-700">безанкорные {crowdActivePlacements(p).filter(x=>x.link_mode==="безанкорная").length} · частичный анкор {crowdActivePlacements(p).filter(x=>x.link_mode==="частичный анкор").length} · брендовые {crowdActivePlacements(p).filter(x=>x.link_mode==="брендовая").length}</div></div>
            </div>

            <details className="mt-4 rounded-xl border border-slate-200 p-4">
              <summary className="cursor-pointer text-sm font-bold">3 автоматически подготовленных текста</summary>
              <div className="mt-3 grid gap-3 lg:grid-cols-3">{p.text_variants.map(t=><div key={t.variant} className="rounded-xl bg-slate-50 p-3 text-xs leading-5 text-slate-700"><b>Вариант {t.variant}</b><div className="mt-1">{t.text}</div></div>)}</div>
            </details>

            <div className="mt-4 text-[11px] text-slate-400">ИКС площадок обновляется автоматически раз в неделю; dofollow/nofollow и индексируемость проверяются после фактической публикации.</div>
            <div className="mt-2 overflow-x-auto">
              <table className="min-w-[1600px] w-full text-left text-xs">
                <thead><tr className="border-b text-slate-500"><th className="py-2">Площадка</th><th>ИКС</th><th>Страница</th><th>Ключ</th><th>Ссылка</th><th>Статус</th><th>Проверка SEO-ссылки</th><th>Прямой URL результата</th></tr></thead>
                <tbody>{crowdActivePlacements(p).map(x=><tr key={x.id} className="border-b border-slate-100 align-top">
                  <td className="py-3"><a href={x.forum_url} target="_blank" rel="noreferrer" className="font-bold text-blue-700">{x.platform_name}</a>{x.surface_name&&x.surface_name!==x.platform_name&&<div className="mt-1 text-[11px] text-slate-400">{x.surface_name}</div>}</td>
                  <td className="py-3"><b>{x.iks??"—"}</b><div className="mt-1 text-[11px] text-slate-400">{x.iks_tier==="high"?"высокий":x.iks_tier==="medium"?"средний":x.iks_tier==="low"?"низкий":"не определён"}</div></td>
                  <td className="py-3 max-w-xs"><a href={x.target_url} target="_blank" rel="noreferrer" className="text-blue-700">{x.target_url}</a></td>
                  <td className="py-3">{x.keyword}</td>
                  <td className="py-3"><b>{x.link_mode}</b><div className="mt-1 text-slate-400">{x.anchor}</div></td>
                  <td className="py-3"><div className="font-semibold text-slate-700">{crowdPlacementStatusRu(x.status)}</div>{x.error&&<div className="mt-1 max-w-xs text-[11px] leading-4 text-rose-600">{crowdErrorRu(x.error)}</div>}</td>
                  <td className="py-3"><div className="max-w-xs leading-5">{x.checked_at?<><b>{x.http_status?("HTTP "+x.http_status):"проверено"}</b><div>{x.link_present===true?"ссылка найдена":x.link_present===false?"ссылка не найдена":"—"} · {x.indexable===true?"индексируется":x.indexable===false?"не индексируется":"—"}</div><div>{x.link_rel_type||x.rel||"rel ещё не определён"}</div><div className="text-[10px] text-slate-400">{new Date(x.checked_at).toLocaleString("ru-RU")}</div></>:<span className="text-slate-400">после публикации BORIS проверит автоматически</span>}</div></td>
                  <td className="py-3">{x.publication_url?<a href={x.publication_url} target="_blank" rel="noreferrer" className="font-bold text-emerald-700">Открыть пост ↗</a>:"—"}</td>
                </tr>)}</tbody>
              </table>
            </div>
          </article>)}
          {archivedCrowdCount>0&&<div data-testid="crowd-archived-count" className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-500">Исторические дубли: {archivedCrowdCount}. Они автоматически отключены, не расходуют площадки и не публикуются параллельно.</div>}
          {!activeCrowdProjects.length&&<div className="rounded-2xl border border-dashed border-slate-300 p-8 text-center text-slate-500">Активных клиентских SEO-проектов пока нет. Вставьте сайт выше — всё остальное BORIS сделает сам.</div>}
        </div>
      </section>}

      {tab==="pricing"&&<section className="rounded-3xl border border-slate-200 bg-white p-6">
        <div><h2 className="text-xl font-bold">Прайс + MVP + себестоимость</h2><p className="text-sm text-slate-500">Управленческая себестоимость MVP рассчитана по внутренней ставке {pricingMeta?.internal_hourly_cost||1500} ₽/ч + минимальные прямые расходы. Это нужно для контроля маржи, а не как обещание клиенту.</p></div>
        <div className="mt-5 overflow-x-auto">
          <table className="min-w-[1400px] w-full text-left text-sm">
            <thead><tr className="border-b text-slate-500"><th className="py-3">Направление</th><th>Спрос</th><th>MVP</th><th>Себестоимость MVP</th><th>Продажа MVP</th><th>Полный проект</th><th>Себестоимость / мес</th><th>Что нужно от клиента</th><th>Что отдаём клиенту</th><th>Софты / расходы в месяц</th></tr></thead>
            <tbody>{pricing.map(x=><tr key={x.key} className="border-b border-slate-100 align-top">
              <td className="py-4 pr-4"><b>{x.title}</b><div className="mt-1 max-w-xs text-xs text-slate-500">{x.result}</div></td>
              <td className="py-4">{x.demand}</td>
              <td className="py-4 font-bold">{x.mvp_days} раб. дн.</td>
              <td className="py-4">{money(x.mvp_total_internal_cost_estimate)}</td>
              <td className="py-4 font-bold text-emerald-700">{money(x.mvp_sell_from)}–{money(x.mvp_sell_to)}</td>
              <td className="py-4 font-bold">{money(x.full_sell_from)}–{money(x.full_sell_to)}<div className="text-xs font-normal text-slate-400">{x.full_days} раб. дн.</div></td>
              <td className="py-4">{money(x.monthly_cogs_from)}–{money(x.monthly_cogs_to)}</td>
              <td className="py-4"><div className="max-w-sm text-xs text-slate-600">{(x.client_inputs||[]).join(" · ")}</div></td>
              <td className="py-4"><div className="max-w-sm text-xs text-slate-600">{(x.client_documents||[]).join(" · ")}</div></td>
              <td className="py-4"><div className="max-w-sm text-xs text-slate-600">{(x.monthly_components||x.stack||[]).join(" · ")}</div></td>
            </tr>)}</tbody>
          </table>
        </div>
      </section>}
    </div>
  </main></AppShell>;
}
