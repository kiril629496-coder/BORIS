"use client";
import { useEffect, useMemo, useState } from "react";
import styles from "./page.module.css";
import AppShell from "@/app/components/shell/AppShell";

type Mailbox={id:number;account_id:string;email_address:string;display_name?:string;status:string;last_checked_at?:string;last_error?:string;smtp_host?:string;smtp_port?:number;imap_host?:string;imap_port?:number};
type AbVariant={label:string;title:string;subject:string;body:string;banner_path:string;banner_url:string};
type AbStat={variant:string;total:number;queued:number;sent:number;replied:number;positive_replied?:number;opt_out?:number};
type CopyStat={version:string;total:number;queued:number;sent:number;replied:number;positive_replied?:number;opt_out?:number};
type CopyDecision={state:"collecting"|"observing"|"promising"|"needs_revision"|string;sent:number;positive_replies:number;opt_outs:number;sample_target?:number;sample_remaining?:number;positive_rate_pct?:number;opt_out_rate_pct?:number;previous_sent?:number;previous_positive_replies?:number;owner_action_required:boolean;recommendation:string;auto_action:string};
type AbDecision={state:"collecting"|"leader"|"close"|string;leader?:string|null;leader_rate?:number;runner_rate?:number;leader_opt_out_rate?:number;weak_variants?:string[];total_sent:number;positive_replies:number;opt_outs:number;min_sent_per_variant:number;recommendation:string;auto_action:string};
type SubjectTracking={subject:string;sent:number;delivered_estimated?:number;delivery_rate_pct?:number;hard_bounced_unique?:number;tracked_sent:number;opened_unique:number;likely_human_opened_unique?:number;technical_only_opened_unique?:number;open_hits:number;replied_unique:number;positive_replied_unique?:number;opt_out_unique?:number;open_rate_pct:number;likely_human_open_rate_pct?:number;reply_rate_pct:number;positive_reply_rate_pct?:number};
type TrackingStat={sent:number;delivered_estimated:number;delivery_rate_pct:number;hard_bounced_unique:number;positive_replied_unique:number;opt_out_unique:number;positive_reply_rate_pct:number;tracking_eligible_sent:number;legacy_sent_before_tracking:number;tracked_sent:number;tracking_coverage_pct:number;opened_unique:number;open_rate_pct:number;likely_human_opened_unique:number;likely_human_open_rate_pct:number;technical_only_opened_unique:number;open_hits:number;repeat_openers:number;proxy_hint_hits:number;replied_unique:number;reply_rate_pct:number;reply_rate_delivered_pct:number;opened_and_replied:number;open_to_reply_rate_pct:number;open_metric_note?:string;subjects?:SubjectTracking[]};
type Campaign={id:number;name:string;niche:string;status:string;daily_limit:number;ready_count:number;account_id?:string;mailbox_id?:number;subject_template?:string;body_template?:string;ab_variants?:Array<{label:string;subject:string;body:string;banner_path?:string}>;ab_stats?:AbStat[];copy_stats?:CopyStat[];tracking?:TrackingStat;current_copy_version?:string;current_copy_stats?:CopyStat;current_copy_decision?:CopyDecision;ab_decision?:AbDecision;health?:{state:string;owner_action_required:boolean;mailbox_last_checked_at?:string|null;mailbox_error?:string|null;delivery_unknown:number;queue_pending:number;queue_stuck:number;auth_backoff_max_attempts:number;dead_today:number;replies_today:number;reply_alert_pending:number;reply_alert_delivery_unknown:number;reply_alert_stuck:number;reply_alert_email_dead:number;tracking_untracked_after_policy?:number;deliverability?:{state:string;sample:number;hard_failures:number;hard_failure_rate_pct:number;owner_action_required:boolean;max_enqueue_override?:number|null;reason:string};self_heal:string};today?:{sent:number;queued_or_sent:number;effective_daily_cap:number;owner_used:number;remaining:number;ramp_day:number;gate:string;timezone:string;next_cap?:number|null;next_change_date?:string|null}|null};
type Reply={id:number;from_email:string;subject?:string;body_preview?:string;crm_lead_id?:number;received_at?:string;company?:string;campaign?:string;account_id?:string;reply_status?:string|null;crm_status?:string|null;notification_status?:string|null;notification_error?:string|null;notification_attempted_at?:string|null;notification_email_queue_id?:number|null};
type RadarLead={id:number;source_key:string;title:string;status:string;score:number;url?:string;company_name?:string;published_at?:string;discovered_at?:string;budget_min_rub?:number|null;budget_max_rub?:number|null;crm_deal_id?:number|null};
type RadarSource={key:string;family:string;enabled:boolean;last_success_at?:string|null;last_error?:string|null;consecutive_failures:number;blocked_external:boolean;recovery_strategy?:string|null;next_scan_at?:string|null};
type RadarHealth={status:string;total_leads:number;fresh_24h:number;stuck_outreach:number;outreach_retrying:number;outreach_blocked:number;quality_version_current?:string;quality_recheck_pending?:number;sources_recovering:number;sources_blocked_external:number;sources_overdue:number;self_heal_active:boolean;scheduler_attention_required:boolean;owner_action_required:boolean;source_coverage?:{target_groups:number;configured_groups:number;missing_group_keys:string[];coverage_ready:boolean};business_coverage_status?:string};
type RadarStats={total:number;by_status:Record<string,number>;sources:RadarSource[]};
type DevelopmentEvidence={kind:string;fact:string;quote?:string;source_url?:string;strength?:number;source_type?:string};
type DevelopmentBrief={company?:string;outreach_gate?:string;evidence?:DevelopmentEvidence[];evidence_count?:number;site_evidence_count?:number;pain_score?:number;pain_confidence?:string;pain_hypothesis?:string;offer_angle?:string;personal_first_message?:string;analysis_status?:string;analysis_reason?:string;pages_analyzed?:number};
type DevelopmentLead={id:number;rank:number;fit_score:number;contact_email?:string;outreach_status:string;company?:string;website?:string;brief_json?:DevelopmentBrief};
type DevelopmentCampaign={id:number;name:string;niche:string;status:string;daily_limit:number;min_quality_score:number;subject_template:string;body_template:string;mailbox_id:number;mailbox_email?:string;ready_count:number;queued_count:number;sent_total:number;sent_today:number;replied_total:number;opt_out_total:number;shortlist?:{total:number;rejected:number;with_email:number;evidence_ready?:number;analysis_required?:number;avg_pain_score?:number}};

const api=(url:string,opts?:RequestInit)=>{
 const token=typeof window!=="undefined" ? localStorage.getItem("boris_token")||"" : "";
 return fetch(url,{credentials:"include",cache:"no-store",...opts,headers:{"Content-Type":"application/json",...(token?{Authorization:`Bearer ${token}`}:{}) ,...(opts?.headers||{})}}).then(async r=>{const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||d.message||"Ошибка");return d});
};

const BORIS_SUBJECT_TEMPLATE="{company}, рассчитать песок и щебень с доставкой по {city}?||Сыпучие материалы с доставкой по {city} — быстрый расчёт||Песок, щебень и ПГС для вашего объекта — стоимость с доставкой";

const BORIS_BODY_TEMPLATE=`Добрый день, команда {company}!

Если у вас сейчас есть объект, где нужен песок, щебень, ПГС или грунт, можем быстро посчитать поставку именно под ваш адрес.

🚚 Подберём материал и машину под реальный объём, чтобы не переплачивать за лишние рейсы и не ждать повторную доставку.

💰 Сразу покажем стоимость материала вместе с доставкой — одной понятной цифрой, которую удобно сравнить с текущим поставщиком.

📍 Учтём адрес, подъезд к объекту и нужное время поставки, чтобы машина приехала тогда, когда материал действительно можно принять.

✅ Перед выездом ещё раз подтверждаем материал, объём, адрес и итоговую стоимость.

Вам не нужно заполнять форму или созваниваться на 20 минут.

Достаточно ответить на это письмо двумя строками: адрес объекта и примерный объём.

Мы подготовим расчёт и предложим наиболее удобный вариант поставки.

Если закупка только планируется — можно получить ориентир по цене заранее и спокойно сравнить варианты.

Можно ответить совсем коротко: «Нужен расчёт».

С уважением,
Отдел поставок.`;

const AB_DEFAULTS:AbVariant[]=[
 {label:"A",title:"Быстрый расчёт",subject:"{company}, рассчитать песок и щебень с доставкой по {city}?",banner_path:"/root/BORIS/frontend/public/reviews/materials.jpg",banner_url:"/reviews/materials.jpg",body:BORIS_BODY_TEMPLATE},
 {label:"B",title:"Цена без лишних согласований",subject:"Сыпучие материалы по {city} — стоимость с доставкой до объекта",banner_path:"/root/BORIS/frontend/public/reviews/construction.jpg",banner_url:"/reviews/construction.jpg",body:`Добрый день, команда {company}!

Когда нужно быстро понять реальную стоимость поставки, обычно приходится отдельно уточнять материал, машину, объём и доставку.

Мы предлагаем сделать проще.

📦 Вы присылаете материал, адрес и примерный объём.

🚛 Мы подбираем подходящий транспорт под условия объекта.

💰 В ответ вы получаете итоговый расчёт с доставкой — без скрытых доплат и длинной цепочки согласований.

Такой расчёт удобно сразу сравнить с текущим поставщиком и понять, есть ли смысл менять условия.

Если цена и логистика подходят — согласовываем удобное окно поставки.

Если нет — у вас всё равно остаётся готовый ориентир по рынку и понимание реальной стоимости доставки.

Чтобы начать, не нужен точный заказ.

Можно прислать только адрес и примерный объём — материал и формат поставки уточним сами.

Если актуально, ответьте: «Посчитайте стоимость».

С уважением,
Отдел поставок.`},
 {label:"C",title:"Без срыва поставки",subject:"Песок и щебень на объект без срыва сроков — {city}",banner_path:"/root/BORIS/frontend/public/reviews/machinery.jpg",banner_url:"/reviews/machinery.jpg",body:`Добрый день, команда {company}!

Если материал нужен к конкретному этапу работ, одна низкая цена не спасает, когда машина приезжает поздно, бригада ждёт, а техника простаивает.

Поэтому мы начинаем не с обещания «самая низкая цена», а с понятной поставки под ваш объект.

🧱 Подбираем песок, щебень, ПГС и грунт под задачу.

📍 До выезда подтверждаем адрес, подъезд, материал и объём.

🕒 Согласовываем окно доставки заранее, чтобы объект был готов принять машину.

🚚 Подбираем транспорт под объём и условия заезда.

✅ Перед отправкой ещё раз подтверждаем параметры заказа.

В результате вы заранее понимаете, что приедет, когда приедет и сколько это будет стоить.

Если сейчас есть объект в работе — пришлите адрес и то, что примерно требуется.

Мы подготовим расчёт и предложим вариант поставки без лишних согласований.

Если закупка на следующую неделю — тоже можно посчитать заранее.

С уважением,
Отдел поставок.`},
 {label:"D",title:"Под задачу объекта",subject:"Какой материал лучше под вашу задачу? Подберём и рассчитаем доставку",banner_path:"/root/BORIS/frontend/public/reviews/paving.jpg",banner_url:"/reviews/paving.jpg",body:`Добрый день, команда {company}!

Если вы знаете задачу, но ещё выбираете материал, можем помочь подобрать вариант без лишнего запаса и переплаты.

🏗 Для фундамента и бетона подберём подходящую фракцию и объём.

🛣 Для отсыпки, дорог и площадок предложим материал под нужную нагрузку.

🌿 Для благоустройства подберём песок, отсев или смесь под конкретную работу.

📦 Если есть несколько вариантов — покажем разницу по цене и назначению простыми словами.

🚚 После выбора сразу рассчитаем доставку до объекта.

Так вы получаете не просто прайс, а готовый вариант под свою задачу.

Это особенно удобно, когда закупка делается не каждый день и не хочется переплачивать из-за неверного выбора материала.

Ответьте, что сейчас строите, отсыпаете или благоустраиваете, и укажите адрес.

Мы предложим подходящий материал, объём и стоимость доставки.

Можно написать буквально одной фразой: «Нужно для отсыпки, адрес такой-то».

С уважением,
Отдел поставок.`},
 {label:"E",title:"Простой заказ в 3 шага",subject:"Материал → объём → адрес. Этого достаточно для расчёта доставки",banner_path:"/root/BORIS/frontend/public/reviews/towtruck.jpg",banner_url:"/reviews/towtruck.jpg",body:`Добрый день, команда {company}!

Чтобы получить расчёт по сыпучим материалам, не нужно заполнять длинную форму или долго созваниваться.

Достаточно трёх вещей.

1️⃣ Напишите, какой материал нужен.

2️⃣ Укажите примерный объём.

3️⃣ Пришлите адрес доставки в {city}.

После этого мы сами подберём транспорт, проверим логистику и рассчитаем итоговую стоимость.

💰 Вы сразу увидите понятную цену с доставкой.

🚚 Если есть несколько вариантов машины или материала — покажем, какой вариант рациональнее.

📍 Если точный объём пока неизвестен, можно начать только с адреса и задачи объекта.

Это не обязывает к заказу — вы просто получаете готовый расчёт, который можно спокойно сравнить.

Если условия подходят, согласовываем дату и время поставки.

Ответьте на это письмо тремя строками — материал, объём, адрес — и мы подготовим предложение.

С уважением,
Отдел поставок.`}
];

export default function ProspectingPage(){
 const [account,setAccount]=useState("");
 const [mailboxes,setMailboxes]=useState<Mailbox[]>([]);
 const [campaigns,setCampaigns]=useState<Campaign[]>([]);
 const [ownerCampaigns,setOwnerCampaigns]=useState<Campaign[]>([]);
 const [developmentCampaign,setDevelopmentCampaign]=useState<DevelopmentCampaign|null>(null);
 const [developmentLeads,setDevelopmentLeads]=useState<DevelopmentLead[]>([]);
 const [ownerMailboxes,setOwnerMailboxes]=useState<Mailbox[]>([]);
 const [ownerReplies,setOwnerReplies]=useState<Reply[]>([]);
 const [replies,setReplies]=useState<Reply[]>([]);
 const [radarHealth,setRadarHealth]=useState<RadarHealth|null>(null);
 const [radarStats,setRadarStats]=useState<RadarStats|null>(null);
 const [radarLeads,setRadarLeads]=useState<RadarLead[]>([]);
 const [radarLoadError,setRadarLoadError]=useState("");
 const [msg,setMsg]=useState("");
 const [msgKind,setMsgKind]=useState<"ok"|"bad"|"info">("info");
 const [busy,setBusy]=useState(false);
 const [copyCampaignId,setCopyCampaignId]=useState<number|null>(null);
 const [copyAbEnabled,setCopyAbEnabled]=useState(false);
 const [abCount,setAbCount]=useState(5);
 const [activeAb,setActiveAb]=useState(0);
 const [abVariants,setAbVariants]=useState<AbVariant[]>(AB_DEFAULTS);
 const [mail,setMail]=useState({email_address:"",display_name:"Отдел поставок",smtp_host:"smtp.mail.ru",smtp_port:465,imap_host:"imap.mail.ru",imap_port:993,username:"",password:""});
 const [camp,setCamp]=useState({name:"Сыпучие материалы — демонстрация",niche:"Доставка сыпучих материалов",regions:"Москва и Московская область",subject_template:BORIS_SUBJECT_TEMPLATE,body_template:BORIS_BODY_TEMPLATE,daily_limit:30,min_quality_score:55,mailbox_id:""});
 const currentMailboxes=useMemo(()=>mailboxes.filter(x=>x.account_id===account),[mailboxes,account]);
 const healthyMailbox=currentMailboxes.find(x=>!x.last_error && x.status==="active");
 const mailboxReady=Boolean(healthyMailbox);
 const allDraftMailboxes=useMemo(()=>{const seen=new Set<number>();return [...currentMailboxes,...ownerMailboxes].filter(x=>{if(seen.has(x.id))return false;seen.add(x.id);return !x.last_error&&x.status==="active"})},[currentMailboxes,ownerMailboxes]);
 const draftMailbox=allDraftMailboxes.find(x=>String(x.id)===String(camp.mailbox_id||""))||healthyMailbox;
 const totalReplyCount=account==="__owner_outreach__"?ownerReplies.length:replies.length+ownerReplies.length;

 const loadRadar=async()=>{try{const [h,s,d,q]=await Promise.all([api("/api/lead-radar/health"),api("/api/lead-radar/stats"),api("/api/lead-radar/leads?limit=12&status=draft_ready"),api("/api/lead-radar/leads?limit=12&status=qualified")]);const useful=[...(d.items||[]),...(q.items||[])].sort((a:RadarLead,b:RadarLead)=>(b.score||0)-(a.score||0)).slice(0,12);setRadarHealth(h);setRadarStats(s);setRadarLeads(useful);setRadarLoadError("")}catch(e:unknown){setRadarLoadError(e instanceof Error?e.message:"Lead Radar недоступен")}};
 const load=async(a=account)=>{if(a){try{const q=encodeURIComponent(a);const [m,c,r]=await Promise.all([api(`/api/prospecting/scale/mailboxes?account_id=${q}`),api(`/api/prospecting/scale/campaigns?account_id=${q}`),api(`/api/prospecting/scale/replies?account_id=${q}`)]);setMailboxes(m.items||[]);setCampaigns(c.items||[]);setReplies(r.items||[])}catch(e:any){setMsgKind("bad");setMsg(e.message)}}try{const oq=encodeURIComponent("__owner_outreach__");const [om,oc,or]=await Promise.all([api(`/api/prospecting/scale/mailboxes?account_id=${oq}`),api(`/api/prospecting/scale/campaigns?account_id=${oq}`),api(`/api/prospecting/scale/replies?account_id=${oq}`)]);setOwnerMailboxes(om.items||[]);setOwnerCampaigns(oc.items||[]);setOwnerReplies(or.items||[])}catch{setOwnerMailboxes([]);setOwnerCampaigns([]);setOwnerReplies([])}try{const [d,l]=await Promise.all([api('/api/prospecting/scale/owner-development'),api('/api/prospecting/scale/owner-development/leads?limit=12')]);setDevelopmentCampaign(d.campaign||null);setDevelopmentLeads(l.items||[])}catch{setDevelopmentCampaign(null);setDevelopmentLeads([])}};
 useEffect(()=>{const a=localStorage.getItem("boris_currentAccount")||"";setAccount(a);void load(a);void loadRadar();},[]);
 useEffect(()=>{const timer=window.setInterval(()=>{void load(account);void loadRadar()},60000);return()=>window.clearInterval(timer)},[account]);
 useEffect(()=>{if(!healthyMailbox)return;if(!camp.mailbox_id)setCamp(x=>({...x,mailbox_id:String(healthyMailbox.id)}));setMail(x=>x.email_address?x:{...x,email_address:healthyMailbox.email_address,username:healthyMailbox.email_address,display_name:healthyMailbox.display_name||x.display_name,smtp_host:healthyMailbox.smtp_host||x.smtp_host,smtp_port:healthyMailbox.smtp_port||x.smtp_port,imap_host:healthyMailbox.imap_host||x.imap_host,imap_port:healthyMailbox.imap_port||x.imap_port});},[healthyMailbox?.id]);
 const preset=(p:string)=>{if(p==="yandex")setMail(x=>({...x,smtp_host:"smtp.yandex.ru",smtp_port:465,imap_host:"imap.yandex.ru",imap_port:993}));if(p==="mail")setMail(x=>({...x,smtp_host:"smtp.mail.ru",smtp_port:465,imap_host:"imap.mail.ru",imap_port:993}));if(p==="gmail")setMail(x=>({...x,smtp_host:"smtp.gmail.com",smtp_port:465,imap_host:"imap.gmail.com",imap_port:993}))};
 const checkSaved=async()=>{if(!healthyMailbox)return;setBusy(true);setMsg("");try{const d=await api(`/api/prospecting/scale/mailboxes/${healthyMailbox.id}/test`,{method:"POST"});const ok=Boolean(d?.test?.smtp&&d?.test?.imap);setMsgKind(ok?"ok":"bad");setMsg(ok?"Сохранённое подключение работает: отправка и получение ответов доступны.":"Почтовый сервис не подтвердил сохранённый доступ. Введите новый пароль приложения.");await load()}catch(e:any){setMsgKind("bad");setMsg(e.message)}finally{setBusy(false)}};
 const connect=async()=>{if(!account)return setMsg("Сначала выберите аккаунт BORIS");setBusy(true);setMsg("");try{const d=await api("/api/prospecting/scale/mailboxes",{method:"POST",body:JSON.stringify({...mail,account_id:account,username:mail.username||mail.email_address,smtp_ssl:true,imap_ssl:true})});if(d.ok){setMsgKind("ok");setMsg("Почта подключена: BORIS может отправлять письма и получать ответы.")}else{const authFailed=d?.test?.smtp_error==="SMTPAuthenticationError"||Boolean(d?.test?.imap_error);setMsgKind("bad");setMsg(authFailed?"Почтовый сервис отклонил авторизацию. Проверьте пароль приложения и повторите проверку.":"Почта сохранена, но соединение пока не прошло проверку.")}setMail(x=>({...x,password:""}));await load()}catch(e:any){setMsgKind("bad");setMsg(e.message)}finally{setBusy(false)}};
 const makeDraft=async(id:number)=>{setBusy(true);setMsg("");try{const region=camp.regions.split(",").map(x=>x.trim()).filter(Boolean)[0]||"";const d=await api(`/api/prospecting/scale/mailboxes/${id}/test-draft`,{method:"POST",body:JSON.stringify({subject:camp.subject_template||BORIS_SUBJECT_TEMPLATE,body_template:camp.body_template||BORIS_BODY_TEMPLATE,company:"СтройПроект",niche:camp.niche||"Доставка сыпучих материалов",region:region||"Москва и Московская область"})});if(d.ok){setMsgKind("ok");setMsg(`Тестовый черновик создан с текущим текстом кампании. Тема: ${d.subject_preview||"Письмо"}. Ничего не отправлено.`)}else{setMsgKind("bad");setMsg(`Черновик не создан: ${d.reason||"ошибка почтового сервиса"}`)}}catch(e:any){setMsgKind("bad");setMsg(e.message)}finally{setBusy(false)}};
 const create=async()=>{setBusy(true);setMsg("");try{const ab_variants=abVariants.slice(0,abCount).map(v=>({label:v.label,subject:v.subject,body:v.body,banner_path:v.banner_path}));const d=await api("/api/prospecting/scale/campaigns",{method:"POST",body:JSON.stringify({...camp,ab_variants,regions:camp.regions.split(",").map(x=>x.trim()).filter(Boolean),daily_limit:Number(camp.daily_limit),min_quality_score:Number(camp.min_quality_score),per_domain_daily_limit:1,account_id:account,mailbox_id:camp.mailbox_id?Number(camp.mailbox_id):null})});setMsgKind("ok");setMsg(`Кампания #${d.campaign_id} сохранена как черновик с ${ab_variants.length} A/B-вариантами. Ничего не отправляется.`);await load()}catch(e:any){setMsgKind("bad");setMsg(e.message)}finally{setBusy(false)}};
 const action=async(id:number,type:"audience"|"active"|"paused")=>{setBusy(true);setMsg("");try{if(type==="audience")await api(`/api/prospecting/scale/campaigns/${id}/audience`,{method:"POST"});else await api(`/api/prospecting/scale/campaigns/${id}/status`,{method:"POST",body:JSON.stringify({status:type})});setMsgKind("ok");setMsg(type==="audience"?"Аудитория обновлена.":type==="active"?"Кампания запущена с установленными лимитами.":"Кампания поставлена на паузу.");await load()}catch(e:any){setMsgKind("bad");setMsg(e.message)}finally{setBusy(false)}};
 const saveCopy=async()=>{if(!copyCampaignId)return;setBusy(true);setMsg("");try{const ab_variants=copyAbEnabled?abVariants.slice(0,abCount).map(v=>({label:v.label,subject:v.subject,body:v.body,banner_path:v.banner_path})):[];await api(`/api/prospecting/scale/campaigns/${copyCampaignId}/copy`,{method:"POST",body:JSON.stringify({subject_template:camp.subject_template,body_template:camp.body_template,ab_variants})});setMsgKind("ok");setMsg(ab_variants.length?`Письмо и ${ab_variants.length} A/B-вариантов сохранены в кампанию #${copyCampaignId}. Новые отправки используют закреплённый вариант A–E.`:`Письмо сохранено в кампанию #${copyCampaignId}. A/B-тест для этой кампании выключен, поэтому отправляется основной текст.`);await load()}catch(e:any){setMsgKind("bad");setMsg(e.message)}finally{setBusy(false)}};
 const useCampaignCopy=(c:Campaign)=>{setCopyCampaignId(c.id);const baseSubject=c.subject_template||BORIS_SUBJECT_TEMPLATE;const baseBody=c.body_template||BORIS_BODY_TEMPLATE;setCamp(x=>({...x,name:c.name||x.name,niche:c.niche||x.niche,subject_template:baseSubject,body_template:baseBody,mailbox_id:c.mailbox_id?String(c.mailbox_id):x.mailbox_id}));if(c.ab_variants?.length){const mapped=AB_DEFAULTS.map((fallback,i)=>{const v=c.ab_variants?.[i];return v?{...fallback,label:v.label||fallback.label,subject:v.subject||fallback.subject,body:v.body||fallback.body,banner_path:v.banner_path||fallback.banner_path}:fallback});setAbVariants(mapped);setAbCount(Math.min(5,c.ab_variants.length));setCopyAbEnabled(true);setActiveAb(0)}else{setAbVariants(AB_DEFAULTS.map((fallback,i)=>i===0?{...fallback,subject:baseSubject,body:baseBody,banner_path:""}:fallback));setAbCount(1);setCopyAbEnabled(false);setActiveAb(0)}setMsgKind("info");setMsg(`Текст кампании «${c.name}» загружен в редактор. A/B-тест ${c.ab_variants?.length?"включён":"выключен"}.`);document.getElementById("boris-email-copy-editor")?.scrollIntoView({behavior:"smooth",block:"center"});};

 const campaignStatus=(s:string)=>({active:"работает",draft:"черновик",paused:"пауза",completed:"завершена"} as Record<string,string>)[s]||s;
 const shortDate=(s?:string|null)=>s?new Date(`${s}T00:00:00`).toLocaleDateString("ru-RU",{day:"numeric",month:"long"}):"";
 const replyStage=(r:Reply)=>r.reply_status==="opt_out"?"Отказ / отписка":r.crm_status==="целевое действие"?"Целевое действие":r.crm_status==="квалифицирован"?"Квалифицирован":r.crm_status==="отказ"?"Отказ":r.crm_status||"Новый ответ";
 const replyNext=(r:Reply)=>r.reply_status==="opt_out"?"BORIS исключил адрес из следующих отправок":r.crm_status==="целевое действие"?"Согласовать созвон или встречу сегодня":r.crm_status==="квалифицирован"?"Связаться с лидом сегодня":"Открыть карточку и определить следующий шаг";
 const replyNotify=(r:Reply)=>r.notification_status==="delivery_unknown"?" · уведомление требует проверки":r.notification_status==="pending"||r.notification_status==="sending"?" · BORIS уведомляет владельца":r.notification_status==="pending_email_fallback"?" · BORIS переключает уведомление на резервный email":r.notification_status==="queued_email"?" · уведомление поставлено в email-очередь":r.notification_status==="queued_email_fallback"?" · резервное уведомление поставлено в email-очередь":r.notification_status==="sent_email_fallback"?" · резервное уведомление доставлено на email":r.notification_status==="sent_email"?" · уведомление доставлено на email":r.notification_status==="dead_email"||r.notification_status==="dead_email_fallback"?" · уведомление не доставлено — требуется проверка":"";
 const flow=[
  {icon:"✉",title:"Подключить почту",text:healthyMailbox?"Отправка и получение ответов работают":"Подключаем почту и проверяем доступ",tone:"blue",badge:healthyMailbox?"готово":"шаг 1"},
  {icon:"⌕",title:"Найти компании",text:`Подготовлено кампаний: ${campaigns.length}`,tone:"green",badge:"база"},
  {icon:"↗",title:"Письма",text:"Черновик → проверка → безопасный запуск",tone:"orange",badge:"контроль"},
  {icon:"◎",title:"Ответы и отчёты",text:`Ответов: ${totalReplyCount} · автоматически в CRM`,tone:"purple",badge:"результат"},
 ];
 const totalReady=campaigns.reduce((s,c)=>s+Number(c.ready_count||0),0);
 const totalSentToday=campaigns.reduce((s,c)=>s+Number(c.today?.sent||0),0);
 const activeCampaigns=campaigns.filter(c=>c.status==="active").length;
 const analyticsCampaigns=account==="__owner_outreach__"?ownerCampaigns:campaigns;
 const analyticsSent=analyticsCampaigns.reduce((s,c)=>s+Number(c.tracking?.sent||0),0);
 const analyticsDelivered=analyticsCampaigns.reduce((s,c)=>s+Number(c.tracking?.delivered_estimated||0),0);
 const analyticsHardBounced=analyticsCampaigns.reduce((s,c)=>s+Number(c.tracking?.hard_bounced_unique||0),0);
 const analyticsPositiveReplies=analyticsCampaigns.reduce((s,c)=>s+Number(c.tracking?.positive_replied_unique||0),0);
 const analyticsOptOut=analyticsCampaigns.reduce((s,c)=>s+Number(c.tracking?.opt_out_unique||0),0);
 const analyticsTracked=analyticsCampaigns.reduce((s,c)=>s+Number(c.tracking?.tracked_sent||0),0);
 const analyticsOpened=analyticsCampaigns.reduce((s,c)=>s+Number(c.tracking?.opened_unique||0),0);
 const analyticsLikelyHuman=analyticsCampaigns.reduce((s,c)=>s+Number(c.tracking?.likely_human_opened_unique||0),0);
 const analyticsTechnicalOnly=analyticsCampaigns.reduce((s,c)=>s+Number(c.tracking?.technical_only_opened_unique||0),0);
 const analyticsReplies=analyticsCampaigns.reduce((s,c)=>s+Number(c.tracking?.replied_unique||0),0);
 const analyticsOpenRate=analyticsTracked?Math.round(analyticsOpened*1000/analyticsTracked)/10:0;
 const analyticsLikelyHumanRate=analyticsTracked?Math.round(analyticsLikelyHuman*1000/analyticsTracked)/10:0;
 const analyticsReplyRate=analyticsSent?Math.round(analyticsReplies*1000/analyticsSent)/10:0;
 const previewSubject=(camp.subject_template||BORIS_SUBJECT_TEMPLATE).split("||")[0].trim().replace("{company}","СтройПроект").replace("{city}",camp.regions.split(",")[0]?.trim()||"Москва");
 const previewBody=(camp.body_template||BORIS_BODY_TEMPLATE).replaceAll("{company}","СтройПроект").replaceAll("{city}",camp.regions.split(",")[0]?.trim()||"Москва").replaceAll("{niche}",camp.niche||"Доставка сыпучих материалов").replaceAll("{website}","");
 const previewLines=previewBody.split(/\n+/).map(x=>x.trim()).filter(Boolean);
 const demoRows=[
  ["Почта отправителя",healthyMailbox?.email_address||"sales@company.ru","Письма идут с адреса клиента"],
  ["Ниша",camp.niche||"Доставка сыпучих материалов","BORIS ищет только целевые компании"],
  ["География",camp.regions||"Москва и Московская область","Можно указать несколько городов и регионов"],
  ["Дневной лимит",`${camp.daily_limit||30} писем`,"Постепенный безопасный прогрев"],
  ["На один домен","1 письмо в сутки","Защита от перегрева и повторов"],
  ["Качество контакта",`${camp.min_quality_score||55}+ баллов`,"Слабые адреса не попадают в отправку"],
 ];
 const radarReady=radarHealth?.status==="PASS"&&Boolean(radarHealth?.source_coverage?.coverage_ready);
 const radarQualified=Number(radarStats?.by_status?.qualified||0);
 const radarDraftReady=Number(radarStats?.by_status?.draft_ready||0);
 const radarRejected=Number(radarStats?.by_status?.rejected||0);
 const radarSourceProblems=(radarStats?.sources||[]).filter(s=>s.enabled&&(s.consecutive_failures>0||s.blocked_external||Boolean(s.last_error)));
 const radarMoney=(x:RadarLead)=>{const lo=Number(x.budget_min_rub||0),hi=Number(x.budget_max_rub||0);if(!lo&&!hi)return "";const n=(v:number)=>new Intl.NumberFormat("ru-RU",{maximumFractionDigits:0}).format(v)+" ₽";return lo&&hi&&lo!==hi?`${n(lo)}–${n(hi)}`:n(hi||lo)};
 const radarStatus=(s:string)=>({qualified:"квалифицирован",draft_ready:"готов черновик",rejected:"отсеян",contacted:"контакт отправлен",crm_error:"ошибка CRM"} as Record<string,string>)[s]||s;

 return <AppShell active="prospecting" title="Поиск клиентов и Email" subtitle="База, письма, ответы и управление рассылками"><main className={styles.page}>
  <section className={styles.hero}>
   <div className={styles.heroCopy}>
    <span className={styles.eyebrow}>BORIS · ПОИСК КЛИЕНТОВ И EMAIL</span>
    <h1>Собираем базу, отправляем с почты клиента и превращаем ответы в лиды</h1>
    <p>BORIS сам проходит путь от поиска компаний до ответа: находит сайты и контакты, фильтрует адреса, готовит письмо, отправляет с корпоративной почты клиента и фиксирует ответ в CRM.</p>
    <div className={styles.heroTags}><span>Без общего ящика BORIS</span><span>Без массового спама</span><span>Ответы → CRM</span><a href="/dashboard/prospecting/services">Площадки и размещение услуг →</a></div>
   </div>
   <div className={styles.accountCard}>
    <small>Текущий клиент</small>
    <strong>{account||"Аккаунт не выбран"}</strong>
    <div className={styles.accountState}><i className={mailboxReady?styles.dotOk:styles.dotWait}/>{mailboxReady?"Почта подключена":"Нужно подключить почту"}</div>
   </div>
  </section>

  <section className={styles.metrics}>
   <div className={`${styles.metricCard} ${styles.metricBlue}`}><span className={styles.metricIcon}>⌁</span><div><span>Кампании</span><strong>{campaigns.length}</strong><small>{activeCampaigns} активно</small></div><b>›</b></div>
   <div className={`${styles.metricCard} ${styles.metricGreen}`}><span className={styles.metricIcon}>⌕</span><div><span>Контакты готовы</span><strong>{totalReady}</strong><small>после проверки качества</small></div><b>›</b></div>
   <div className={`${styles.metricCard} ${styles.metricOrange}`}><span className={styles.metricIcon}>↗</span><div><span>Сегодня отправлено</span><strong>{totalSentToday}</strong><small>по безопасному лимиту</small></div><b>›</b></div>
   <div className={`${styles.metricCard} ${styles.metricGreen}`}><span className={styles.metricIcon}>✓</span><div><span>Доставлено</span><strong>{analyticsDelivered}/{analyticsSent}</strong><small>жёстких недоставок: {analyticsHardBounced}</small></div><b>›</b></div>
   <div className={`${styles.metricCard} ${styles.metricBlue}`}><span className={styles.metricIcon}>◉</span><div><span>Открытия</span><strong>≈ {analyticsLikelyHumanRate}%</strong><small>сигналов {analyticsOpened}/{analyticsTracked} ({analyticsOpenRate}%) · тех. загрузок {analyticsTechnicalOnly}</small></div><b>›</b></div>
   <div className={`${styles.metricCard} ${styles.metricPurple}`}><span className={styles.metricIcon}>↩</span><div><span>Ответы</span><strong>{analyticsReplies}</strong><small>{analyticsReplyRate}% · положительных {analyticsPositiveReplies} · отказов {analyticsOptOut}</small></div><b>›</b></div>
  </section>

  <section className={`${styles.radarPanel} ${radarReady?styles.radarPanelOk:styles.radarPanelWarn}`}>
   <div className={styles.radarHead}>
    <div><span>LEAD RADAR · ЖИВОЙ ПОИСК ЗАКАЗОВ</span><h2>BORIS сам ищет новые заказы и складывает подходящие в CRM</h2><p>Это реальные данные production. Обновляются автоматически раз в минуту; владельцу не нужно запускать поиск вручную.</p></div>
    <div className={radarReady?styles.radarHealthOk:styles.radarHealthWarn}><i/>{radarLoadError?"нет данных":radarReady?"работает":radarHealth?.status||"проверка"}</div>
   </div>
   {radarLoadError?<div className={styles.radarError}>{radarLoadError}</div>:<>
    <div className={styles.radarMetrics}>
     <article><span>Всего найдено</span><strong>{radarHealth?.total_leads??radarStats?.total??0}</strong><small>за 24 часа: {radarHealth?.fresh_24h??0}</small></article>
     <article><span>Подходят</span><strong>{radarQualified}</strong><small>квалифицированы автоматически</small></article>
     <article><span>Готов черновик</span><strong>{radarDraftReady}</strong><small>без автоотправки</small></article>
     <article><span>Источники</span><strong>{radarHealth?.source_coverage?.configured_groups??0}/{radarHealth?.source_coverage?.target_groups??20}</strong><small>{radarSourceProblems.length?"BORIS восстанавливает: "+radarSourceProblems.length:"ошибок сейчас нет"}</small></article>
    </div>
    <div className={styles.radarStateLine}>
     <span>Отсеяно слабых: <b>{radarRejected}</b></span>
     <span>Переоценка качества: <b>{radarHealth?.quality_recheck_pending??0}</b></span>
     <span>Зависших отправок: <b>{radarHealth?.stuck_outreach??0}</b></span>
     <span>В восстановлении: <b>{radarHealth?.sources_recovering??0}</b></span>
     <span>Нужно участие владельца: <b>{radarHealth?.owner_action_required?"да":"нет"}</b></span>
    </div>
    <div className={styles.radarLeadGrid}>
     {radarLeads.slice(0,8).map(x=><article className={styles.radarLead} key={x.id}>
      <div className={styles.radarLeadTop}><span>{x.source_key}</span><em>{radarStatus(x.status)}</em></div>
      <strong>{x.title}</strong>
      <small>{x.company_name||"Заказчик"} · оценка {x.score}/100{x.crm_deal_id?" · CRM #"+x.crm_deal_id:""}</small>
      <div><b>{radarMoney(x)||"бюджет не указан"}</b>{x.url&&<a href={x.url} target="_blank" rel="noreferrer">Открыть источник →</a>}</div>
     </article>)}
     {!radarLeads.length&&<div className={styles.radarEmpty}>Подходящих лидов в текущей выборке пока нет. Поиск продолжает работать автоматически.</div>}
    </div>
   </>}
  </section>

  <section className={styles.flowWrap}><div className={styles.flowHead}><div><span>КАК РАБОТАЕТ</span><h2>Одна страница — весь путь клиента</h2></div><small>Настройка → база → письмо → отправка → ответ</small></div><div className={styles.flowGrid}>{flow.map(x=><div key={x.title} className={`${styles.flowCard} ${styles[x.tone]}`}><div className={styles.flowIcon}>{x.icon}</div><div><strong>{x.title}<em>{x.badge}</em></strong><small>{x.text}</small></div><b>›</b></div>)}</div></section>

  <section className={styles.demoPanel}>
   <div className={styles.demoHead}>
    <div><span>ПРИМЕР НАСТРОЙКИ</span><h2>Вот что видит клиент перед запуском</h2><p>Если реальные данные ещё не заполнены, показываем понятный пример. После настройки значения автоматически заменяются на боевые.</p></div>
    <div className={styles.demoBadge}>Пример интерфейса</div>
   </div>
   <div className={styles.demoGrid}>
    {demoRows.map(([title,value,hint],i)=><div className={`${styles.demoRow} ${styles[`demoTone${i%4}`]}`} key={title}><div className={styles.demoRowIcon}>{["✉","⌕","⌖","↗","◎","✓"][i]}</div><div><span>{title}</span><strong>{value}</strong><small>{hint}</small></div></div>)}
   </div>
   <div className={styles.demoRoute}>
    <div><b>Как будет идти рассылка</b><span>База компаний</span><i>→</i><span>Проверенный email</span><i>→</i><span>Почта клиента</span><i>→</i><span>Ответ</span><i>→</i><span>CRM</span></div>
    <small>Основной пароль от почты не нужен. Используется отдельный пароль приложения или безопасная авторизация.</small>
   </div>
  </section>

  <section className={styles.abSection}>
   <div className={styles.abHead}>
    <div><span>A/B-ТЕСТЫ</span><h2>До 5 вариантов на одно направление</h2><p>Каждый вариант — это отдельная связка «тема + продающий текст + баннер». BORIS закрепляет вариант за получателем и сохраняет метку A–E для дальнейшего сравнения ответов.</p></div>
    <label>Сколько вариантов<select value={abCount} onChange={e=>{const n=Number(e.target.value);setAbCount(n);if(activeAb>=n)setActiveAb(n-1)}}>{[1,2,3,4,5].map(n=><option value={n} key={n}>{n}</option>)}</select></label>
   </div>
   <div className={styles.abGrid}>
    {abVariants.slice(0,abCount).map((v,i)=><button type="button" key={v.label} onClick={()=>setActiveAb(i)} className={`${styles.abCard} ${styles[`abTone${i%5}`]} ${activeAb===i?styles.abCardActive:""}`}>
      <div className={styles.abImage}><img src={v.banner_url} alt={`Вариант ${v.label}`}/><span>{v.label}</span></div>
      <div><strong>{v.title}</strong><small>{v.subject.replace("{company}","СтройПроект").replace("{city}","Москва")}</small></div>
      <em>{activeAb===i?"редактируем":"открыть"}</em>
    </button>)}
   </div>
   {abVariants[activeAb]&&<div className={styles.abEditor}>
     <div className={styles.abEditorTop}><div><span>ВАРИАНТ {abVariants[activeAb].label}</span><strong>{abVariants[activeAb].title}</strong></div><small>≈ {Math.round(1000/abCount)} писем из 1000 при равномерном тесте</small></div>
     <label>Тема письма<input value={abVariants[activeAb].subject} onChange={e=>setAbVariants(xs=>xs.map((x,i)=>i===activeAb?{...x,subject:e.target.value}:x))}/></label>
     <label>Текст варианта<textarea rows={10} value={abVariants[activeAb].body} onChange={e=>setAbVariants(xs=>xs.map((x,i)=>i===activeAb?{...x,body:e.target.value}:x))}/></label>
     <div className={styles.abEditorNote}>Картинка уже закреплена за вариантом. Новая генерация не требуется — используются готовые проверенные визуалы.</div>
   </div>}
  </section>

  <section className={styles.pricingSection}>
   <div className={styles.pricingHead}><span>СТОИМОСТЬ РАССЫЛКИ</span><h2>Цена зависит от количества направлений бизнеса</h2><p>Во всех пакетах объём остаётся примерно одинаковым — около <b>1000 писем в месяц</b>. При росте числа направлений увеличивается не количество писем, а число отдельных сегментов, офферов, шаблонов текста, A/B-сценариев и баннеров.</p></div>
   <div className={styles.priceGrid}>
    <article><div className={styles.priceIcon}><i>✦</i><b>1–3</b></div><span>направления</span><strong>10 000 ₽</strong><small>в месяц</small><ul><li>≈1000 писем/месяц</li><li>до 5 A/B-вариантов на направление</li><li>отдельные тексты и баннеры</li></ul></article>
    <article className={styles.pricePopular}><div className={styles.priceIcon}><i>◆</i><b>4–7</b></div><span>направлений</span><strong>14 000 ₽</strong><small>в месяц</small><ul><li>≈1000 писем/месяц суммарно</li><li>раздельные базы и офферы</li><li>свои шаблоны и визуалы</li></ul><em>Оптимальный</em></article>
    <article><div className={styles.priceIcon}><i>◎</i><b>8+</b></div><span>направлений</span><strong>18 000 ₽</strong><small>в месяц</small><ul><li>≈1000 писем/месяц суммарно</li><li>8 и более отдельных сценариев</li><li>максимальная сегментация</li></ul></article>
   </div>
   <div className={styles.priceExplain}><b>Почему цена выше, если писем столько же?</b><span>На каждое новое направление отдельно собирается и чистится база, готовятся офферы, до 5 вариантов текста, изображения/баннеры и логика A/B-теста. То есть растёт объём подготовки и аналитики, а не нагрузка на почтовый ящик.</span></div>
  </section>

  {msg&&<div className={`${styles.notice} ${msgKind==="bad"?styles.noticeBad:msgKind==="ok"?styles.noticeOk:""}`}>{msg}</div>}

  {developmentCampaign&&<section className={[styles.panel,styles.panelOrange].join(" ")}>
   <div className={styles.panelHead}><div className={`${styles.num} ${styles.sectionGlyph}`}>⚙</div><div><h2>РАЗРАБОТКА ПО — МОЯ РАССЫЛКА</h2><p>Отдельная кампания только по SaaS, мобильным приложениям, CRM, личным кабинетам и автоматизации. BORIS-продажи сюда не смешиваются.</p></div></div>
   <div className={styles.runGrid}><div className={styles.runCard}>
    <div className={styles.runTop}><span className={styles.runIcon}>✉</span><em>{campaignStatus(developmentCampaign.status)}</em></div>
    <strong>{developmentCampaign.name}</strong>
    <small>Почта: {developmentCampaign.mailbox_email||"не подключена"} · лимит {developmentCampaign.daily_limit}/день · качество {developmentCampaign.min_quality_score}+</small>
    <small>Сегодня отправлено: {developmentCampaign.sent_today||0} · всего отправлено: {developmentCampaign.sent_total||0} · готово: {developmentCampaign.ready_count||0} · очередь: {developmentCampaign.queued_count||0} · ответы: {developmentCampaign.replied_total||0}</small>
    <small>Сегодня Lead Radar: {developmentCampaign.shortlist?.total||0} карточек · с email {developmentCampaign.shortlist?.with_email||0} · отсеяно {developmentCampaign.shortlist?.rejected||0}</small>
    <small><b>Доказательство боли:</b> готово к письму {developmentCampaign.shortlist?.evidence_ready||0} · требуют анализа {developmentCampaign.shortlist?.analysis_required||0} · средний Pain Score {developmentCampaign.shortlist?.avg_pain_score||0}/100</small>
    <div className={styles.ownerCopySubject}>{(developmentCampaign.subject_template||"").split("||")[0]}</div>
    <details style={{marginTop:10}}><summary style={{cursor:"pointer",fontWeight:700}}>Показать базовый текст кампании</summary><pre style={{whiteSpace:"pre-wrap",fontFamily:"inherit",fontSize:13,lineHeight:1.55,marginTop:10}}>{developmentCampaign.body_template}</pre></details>
   </div></div>
   <div className={styles.runGrid} style={{marginTop:14}}>{developmentLeads.slice(0,6).map((lead)=>{const b=lead.brief_json||{};const ready=b.outreach_gate==="pass";return <div className={styles.runCard} key={`dev-proof-${lead.id}`}>
    <div className={styles.runTop}><span className={styles.runIcon}>{ready?"✓":"⌕"}</span><em>{ready?"боль подтверждена":"нужен анализ"}</em></div>
    <strong>{lead.company||b.company||"Компания"}</strong>
    <small>Pain Score: <b>{b.pain_score||0}/100</b> · сигналов с сайта: {b.site_evidence_count||0} · статус: {lead.outreach_status}</small>
    <p style={{fontSize:13,lineHeight:1.5,margin:"8px 0"}}><b>Гипотеза:</b> {b.pain_hypothesis||"Недостаточно фактов — письмо заблокировано."}</p>
    <details><summary style={{cursor:"pointer",fontWeight:700}}>Почему BORIS решил написать это</summary>
     <div style={{display:"grid",gap:8,marginTop:10}}>{(b.evidence||[]).map((e,idx)=><div key={`${lead.id}-e-${idx}`} style={{padding:"9px 10px",border:"1px solid rgba(120,130,160,.22)",borderRadius:10}}><b>{e.fact}</b>{e.quote&&<small style={{display:"block",marginTop:4}}>«{e.quote}»</small>}{e.source_url&&<a href={e.source_url} target="_blank" rel="noreferrer" style={{fontSize:12}}>Открыть источник →</a>}</div>)}</div>
     {ready&&b.personal_first_message&&<pre style={{whiteSpace:"pre-wrap",fontFamily:"inherit",fontSize:13,lineHeight:1.5,marginTop:10}}>{b.personal_first_message}</pre>}
    </details>
   </div>})}</div>
  </section>}

  {ownerCampaigns.length>0&&<section className={styles.panel}>
   <div className={styles.panelHead}><div className={`${styles.num} ${styles.sectionGlyph}`}>◎</div><div><h2>Личная рассылка BORIS</h2><p>Владелец видит здесь собственную B2B-кампанию отдельно от клиентских рассылок. Клиентам этот контур недоступен.</p></div></div>
   <div className={styles.runGrid}>{ownerCampaigns.map(c=><div className={styles.runCard} key={`owner-${c.id}`}><div className={styles.runTop}><span className={styles.runIcon}>✉</span><em>{campaignStatus(c.status)}</em></div><strong>{c.name}</strong><small>{c.niche} · готово {c.ready_count||0} контактов{c.today?` · отправлено ${c.today.sent} · лимит использован ${c.today.owner_used}/${c.today.effective_daily_cap}`:""}</small>{c.health&&<div className={`${styles.campaignHealth} ${c.health.owner_action_required?styles.campaignHealthBad:c.health.state==="recovering"?styles.campaignHealthWait:styles.campaignHealthOk}`}><b>{c.health.owner_action_required?"Нужно действие владельца":c.health.state==="recovering"?"BORIS восстанавливает":"Контур работает"}</b><span>Очередь: {c.health.queue_pending} · ответы сегодня: {c.health.replies_today} · уведомления лидов: {c.health.reply_alert_pending} · ошибки доставки: {c.health.delivery_unknown}{c.health.reply_alert_delivery_unknown?` · уведомление требует проверки: ${c.health.reply_alert_delivery_unknown}`:""}{c.health.reply_alert_email_dead?` · резервное уведомление не доставлено: ${c.health.reply_alert_email_dead}`:""}{c.health.dead_today?` · недоступных адресов: ${c.health.dead_today}`:""}{c.health.tracking_untracked_after_policy?` · БЕЗ МЕТКИ ПОСЛЕ ВКЛЮЧЕНИЯ: ${c.health.tracking_untracked_after_policy}`:""}{c.health.deliverability?.sample?` · за 7 дней недоступных ${c.health.deliverability.hard_failures}/${c.health.deliverability.sample} (${c.health.deliverability.hard_failure_rate_pct}%)`:""}{c.health.deliverability?.state==="throttled"?" · BORIS автоматически снизил темп":c.health.deliverability?.state==="blocked"?" · отправка остановлена для защиты почты":""}</span></div>}{c.current_copy_stats&&<small>Текущий текст письма: {c.current_copy_stats.sent||0} отправлено · {c.current_copy_stats.positive_replied||0} полезных ответов · {c.current_copy_stats.opt_out||0} отказов</small>}{c.tracking&&<small>Статистика: доставлено {c.tracking.delivered_estimated}/{c.tracking.sent} · недоставок {c.tracking.hard_bounced_unique} · с меткой {c.tracking.tracked_sent}/{c.tracking.tracking_eligible_sent} новых · старых до включения метки {c.tracking.legacy_sent_before_tracking} · сигналы открытия {c.tracking.opened_unique} ({c.tracking.open_rate_pct}%) · вероятно человек {c.tracking.likely_human_opened_unique} ({c.tracking.likely_human_open_rate_pct}%) · тех. загрузок {c.tracking.technical_only_opened_unique} · ответили {c.tracking.replied_unique} ({c.tracking.reply_rate_pct}%) · положительных {c.tracking.positive_replied_unique} · отказов {c.tracking.opt_out_unique}</small>}{(c.tracking?.subjects||[]).filter(s=>s.tracked_sent>0).length>0&&<div className={styles.abResultGrid}>{(c.tracking?.subjects||[]).filter(s=>s.tracked_sent>0).slice(0,6).map(s=><div className={styles.abResultItem} key={s.subject}><b>Тема</b><span>{s.subject}</span><strong>сигналы {s.opened_unique}/{s.tracked_sent} · {s.open_rate_pct}%</strong><em>≈ человек {s.likely_human_open_rate_pct||0}% · ответов {s.replied_unique}</em></div>)}</div>}<div className={styles.ownerCopySubject}>{(c.subject_template||BORIS_SUBJECT_TEMPLATE).split("||")[0]}</div><div className={styles.actions}><button className={styles.go} onClick={()=>useCampaignCopy(c)}>Открыть новое письмо</button></div></div>)}</div>
  </section>}

  {ownerCampaigns.length>0&&<section className={[styles.panel,styles.panelOrange].join(" ")}>
   <div className={styles.panelHead}><div className={[styles.num,styles.sectionGlyph].join(" ")}>↩</div><div><h2>Ответы личной рассылки BORIS</h2><p>Ответы из вашей собственной кампании видны здесь сразу после получения почтой и одновременно попадают в CRM.</p></div></div>
   {!ownerReplies.length?<div className={styles.empty}><span className={styles.emptyIcon}>↩</span><div><strong>Ответов пока нет</strong><small>BORIS продолжает проверять почту автоматически. Первый ответ появится здесь и в CRM без ручного импорта.</small></div></div>:<div className={styles.replyGrid}>{ownerReplies.map((r,i)=><div className={[styles.replyCard,styles["runTone"+(i%4)]].join(" ")} key={"owner-reply-"+r.id}><div className={styles.replyIcon}>↩</div><strong>{r.company||r.from_email}</strong><small>{r.from_email} · {r.subject||"Без темы"}</small><p>{r.body_preview?.slice(0,220)}</p><span>{replyStage(r)} · {replyNext(r)}{r.crm_lead_id?" · Клиент #"+r.crm_lead_id:""}{replyNotify(r)}</span></div>)}</div>}
  </section>}

  <section className={styles.workspace}>
   <article className={`${styles.panel} ${styles.panelBlue}`}>
    <div className={styles.panelHead}><div className={`${styles.num} ${styles.sectionGlyph}`}>✉</div><div><h2>Почта отправителя</h2><p>Выберите свою почту — BORIS настроит отправку и получение ответов автоматически.</p></div></div>
    <div className={styles.providerGrid}>
      <button className={`${styles.providerCard} ${mail.smtp_host.includes("mail.ru")?styles.providerSelected:""}`} onClick={()=>preset("mail")}><span className={styles.providerLogo}>M</span><strong>Mail.ru</strong><small>Подключить за 2 минуты</small><b>→</b></button>
      <button className={`${styles.providerCard} ${mail.smtp_host.includes("yandex")?styles.providerSelected:""}`} onClick={()=>preset("yandex")}><span className={styles.providerLogo}>Я</span><strong>Яндекс</strong><small>Пароль приложения</small><b>→</b></button>
      <button className={`${styles.providerCard} ${mail.smtp_host.includes("gmail")?styles.providerSelected:""}`} onClick={()=>preset("gmail")}><span className={styles.providerLogo}>G</span><strong>Gmail</strong><small>Безопасное подключение</small><b>→</b></button>
    </div>
    <div className={styles.guide}>
      <div className={styles.guideTitle}><span>?</span><div><strong>Как подключить {mail.smtp_host.includes("yandex")?"Яндекс":mail.smtp_host.includes("gmail")?"Gmail":"Mail.ru"}</strong><small>BORIS сам настроит серверы. Основной пароль от почты не нужен.</small></div></div>
      {mail.smtp_host.includes("mail.ru")&&<div className={styles.steps}><div><b>1</b><span>Откройте Mail.ru → <strong>Настройки → Все настройки → Безопасность</strong></span></div><div><b>2</b><span>Откройте <strong>Пароли для внешних приложений</strong> и нажмите «Создать»</span></div><div><b>3</b><span>Назовите пароль <strong>BORIS</strong> и скопируйте его</span></div><div><b>4</b><span>Введите ниже почту и созданный пароль → <strong>Подключить и проверить</strong></span></div></div>}
      {mail.smtp_host.includes("yandex")&&<div className={styles.steps}><div><b>1</b><span>Откройте настройки Яндекс Почты и разрешите доступ почтовым программам по <strong>IMAP</strong></span></div><div><b>2</b><span>В Яндекс ID откройте <strong>Пароли приложений</strong></span></div><div><b>3</b><span>Создайте пароль для почты с названием <strong>BORIS</strong></span></div><div><b>4</b><span>Введите ниже адрес и пароль приложения → <strong>Подключить и проверить</strong></span></div></div>}
      {mail.smtp_host.includes("gmail")&&<div className={styles.steps}><div><b>1</b><span>Для Gmail используем безопасную авторизацию Google — <strong>основной пароль передавать нельзя</strong></span></div><div><b>2</b><span>До подключения OAuth можно использовать отдельный пароль приложения при включённой двухэтапной проверке</span></div><div><b>3</b><span>Введите адрес Gmail и пароль приложения ниже</span></div><div><b>4</b><span>BORIS проверит отправку и получение ответов автоматически</span></div></div>}
    </div>
    <div className={styles.form}>
      <label className={`${styles.fieldCard} ${styles.fieldBlue}`}><span className={styles.fieldIcon}>✉</span><span>Почта</span><input value={mail.email_address} onChange={e=>setMail({...mail,email_address:e.target.value,username:e.target.value})}/></label>
      <label className={`${styles.fieldCard} ${styles.fieldGreen}`}><span className={styles.fieldIcon}>◎</span><span>Имя отправителя</span><input value={mail.display_name} onChange={e=>setMail({...mail,display_name:e.target.value})}/></label>
      <label className={`${styles.fieldCard} ${styles.fieldPurple}`}><span className={styles.fieldIcon}>◆</span><span>Пароль приложения</span><input type="password" placeholder={mailboxReady?"Пароль уже сохранён":"Вставьте пароль приложения"} value={mail.password} onChange={e=>setMail({...mail,password:e.target.value})}/><small className={styles.fieldHint}>{mailboxReady?"Зашифрован и сохранён. Новый вводите только для замены.":"После подключения пароль сохранится зашифрованно."}</small></label>
      <div className={`${styles.fieldActionCard} ${styles.fieldOrange}`}><span className={styles.fieldIcon}>↗</span><div><strong>Проверка подключения</strong><small>Отправка + получение ответов</small></div>{mailboxReady&&!mail.password?<button className={styles.primary} disabled={busy} onClick={checkSaved}>{busy?"Проверяю…":"Проверить"}</button>:<button className={styles.primary} disabled={busy||!mail.email_address||!mail.password} onClick={connect}>{busy?"Проверяю…":"Подключить"}</button>}</div>
    </div>
    <div className={styles.mailList}>{currentMailboxes.map(m=><div className={styles.mailbox} key={m.id}><div className={styles.mailIcon}>✉</div><div><strong>{m.email_address}</strong><small>{m.last_error?"Почтовый сервис не подтвердил подключение":"Отправка и получение ответов подключены"}</small></div><span className={m.last_error?styles.badStatus:styles.goodStatus}>{m.last_error?"нужно проверить":"работает"}</span></div>)}</div>
   </article>

   <article className={`${styles.panel} ${styles.panelPurple}`}>
    <div className={styles.panelHead}><div className={`${styles.num} ${styles.sectionGlyph}`}>✦</div><div><h2>Первый тест</h2><p>Создаём одно красивое письмо в «Черновиках». Оно никуда не отправляется.</p></div></div>
    <div className={styles.emailDemo}>
      <div className={styles.emailWindowTop}>
        <div className={styles.mailDots}><i/><i/><i/></div>
        <div><span>ПРЕДПРОСМОТР ПИСЬМА</span><strong>Полное письмо · прокрутите вниз</strong></div>
        <em>Демо</em>
      </div>
      <div className={styles.emailScroll}>
        <div className={styles.emailHeader}>
          <small>ТЕМА ПИСЬМА</small>
          <h3>{previewSubject}</h3>
          <div className={styles.emailMeta}><span>От: <b>Отдел поставок</b></span><span>Кому: <b>СтройПроект</b></span></div>
        </div>
        <div className={styles.emailBanner}>
          <img src="/reviews/materials.jpg" alt="Сыпучие строительные материалы"/>
          <div className={styles.emailBannerShade}>
            <span>ПОСТАВКА НА ОБЪЕКТ</span>
            <strong>Песок · Щебень · ПГС</strong>
            <small>Расчёт стоимости доставки по городу и области</small>
          </div>
        </div>
        <div className={styles.emailLetter}>
          {previewLines.map((line,i)=><p key={`${i}-${line.slice(0,12)}`} className={line.startsWith("✅")?styles.emailBenefit:line.startsWith("🚚")||line.startsWith("📦")||line.startsWith("🧱")||line.startsWith("⏱")||line.startsWith("💰")||line.startsWith("📍")?styles.emailAccent:""}>{line}</p>)}
        </div>
        <div className={styles.emailCta}><strong>Нужен расчёт?</strong><span>Ответьте на письмо адресом доставки и примерным объёмом — менеджер подготовит стоимость.</span></div>
        <div className={styles.emailFine}>Демонстрационный пример для ниши «Доставка сыпучих материалов».</div>
      </div>
    </div>
    <div id="boris-email-copy-editor" className={styles.copyEditor}>
      <label className={`${styles.copyField} ${styles.fieldBlue}`}><span className={styles.fieldIcon}>✦</span><b>Варианты темы</b><input value={camp.subject_template} onChange={e=>setCamp({...camp,subject_template:e.target.value})}/><small>Разделяйте варианты через || — BORIS будет чередовать их между получателями.</small></label>
      <label className={`${styles.copyField} ${styles.fieldPurple}`}><span className={styles.fieldIcon}>≡</span><b>Текст письма</b><textarea rows={14} value={camp.body_template} onChange={e=>setCamp({...camp,body_template:e.target.value})}/><small>Боевой текст кампании. Переменные: {"{company}"}, {"{city}"}, {"{niche}"}, {"{website}"}.</small></label>
      {copyCampaignId&&<div className={styles.copySaveRow}><span>Редактируется кампания #{copyCampaignId} · A/B {copyAbEnabled?"включён":"выключен"}</span><div className={styles.copySaveActions}><button type="button" className={styles.secondary} onClick={()=>setCopyAbEnabled(v=>!v)}>{copyAbEnabled?"Выключить A/B":"Включить A/B"}</button><button className={styles.secondary} disabled={busy||!camp.subject_template.trim()||camp.body_template.trim().length<120} onClick={saveCopy}>{busy?"Сохраняю…":"Сохранить письмо в кампанию"}</button></div></div>}
    </div>
    {draftMailbox?<button className={styles.primary} disabled={busy||!camp.subject_template.trim()||!camp.body_template.trim()} onClick={()=>makeDraft(draftMailbox.id)}>{busy?"Создаю черновик…":`✉ Создать тестовый черновик · ${draftMailbox.email_address}`}</button>:<button className={styles.primary} disabled>✉ Сначала подключите почту</button>}
    <p className={styles.safety}>После создания откройте папку «Черновики» в своей почте и проверьте письмо. Автоматическая отправка на этом шаге выключена.</p>
   </article>
  </section>

  <section className={`${styles.panel} ${styles.panelGreen}`}>
   <div className={styles.panelHead}><div className={`${styles.num} ${styles.sectionGlyph}`}>⌁</div><div><h2>Кампании</h2><p>После проверки черновика BORIS собирает аудиторию и запускает рассылку с дневным лимитом.</p></div></div>
   <div className={styles.campaignForm}>
     <label className={`${styles.fieldCard} ${styles.fieldBlue}`}><span className={styles.fieldIcon}>⌁</span><span>Название</span><input value={camp.name} onChange={e=>setCamp({...camp,name:e.target.value})}/></label>
     <label className={`${styles.fieldCard} ${styles.fieldGreen}`}><span className={styles.fieldIcon}>⌕</span><span>Ниша</span><input value={camp.niche} onChange={e=>setCamp({...camp,niche:e.target.value})}/></label>
     <label className={`${styles.fieldCard} ${styles.fieldOrange}`}><span className={styles.fieldIcon}>⌖</span><span>Города</span><input value={camp.regions} onChange={e=>setCamp({...camp,regions:e.target.value})}/></label>
     <label className={`${styles.fieldCard} ${styles.fieldPurple}`}><span className={styles.fieldIcon}>✉</span><span>Почта</span><select value={camp.mailbox_id} onChange={e=>setCamp({...camp,mailbox_id:e.target.value})}><option value="">Выберите подключённую почту</option>{currentMailboxes.filter(m=>!m.last_error).map(m=><option key={m.id} value={m.id}>{m.email_address}</option>)}</select></label>
     <label className={`${styles.fieldCard} ${styles.fieldBlue}`}><span className={styles.fieldIcon}>◴</span><span>Максимум в день</span><input type="number" min={1} max={30} value={camp.daily_limit} onChange={e=>setCamp({...camp,daily_limit:Number(e.target.value)})}/></label>
     <div className={`${styles.fieldActionCard} ${styles.fieldGreen}`}><span className={styles.fieldIcon}>＋</span><div><strong>Создать кампанию</strong><small>{abCount} A/B-вариантов · ≈1000 писем/мес.</small></div><button className={styles.secondary} disabled={busy||!camp.name||!camp.niche} onClick={create}>Сохранить</button></div>
   </div>
   <div className={styles.runGrid}>
    {campaigns.map((c,i)=>{
      const ab=(c.ab_stats||[]).filter(s=>/^[A-E]$/.test(s.variant));
      return <div className={`${styles.runCard} ${styles[`runTone${i%4}`]}`} key={c.id}>
       <div className={styles.runTop}><span className={styles.runIcon}>⌁</span><em>{campaignStatus(c.status)}</em></div>
       <strong>{c.name}</strong>
       <small>{c.niche} · база {c.ready_count||0} контактов · A/B {c.ab_variants?.length||1} вариант{(c.ab_variants?.length||1)===1?"":"а"}{c.today?` · сегодня ${c.today.sent}/${c.today.effective_daily_cap} · осталось ${c.today.remaining} · день прогрева ${c.today.ramp_day}${c.today.next_cap&&c.today.next_change_date?` · затем ${c.today.next_cap}/день с ${shortDate(c.today.next_change_date)}`:""}`:` · лимит ${c.daily_limit}/день`}</small>
       {c.current_copy_stats&&<small>Текущий текст письма: {c.current_copy_stats.sent||0} отправлено · {c.current_copy_stats.positive_replied||0} полезных ответов · {c.current_copy_stats.opt_out||0} отказов</small>}
       {c.tracking&&<small>Доставка: {c.tracking.delivered_estimated}/{c.tracking.sent} ({c.tracking.delivery_rate_pct}%) · недоставок {c.tracking.hard_bounced_unique} · открытия: сигналов {c.tracking.opened_unique}/{c.tracking.tracked_sent} ({c.tracking.open_rate_pct}%) · вероятно человек {c.tracking.likely_human_opened_unique} ({c.tracking.likely_human_open_rate_pct}%) · тех. загрузок {c.tracking.technical_only_opened_unique} · ответы: {c.tracking.replied_unique}/{c.tracking.sent} ({c.tracking.reply_rate_pct}%) · покрытие новых писем меткой: {c.tracking.tracking_coverage_pct}% · старых до метки: {c.tracking.legacy_sent_before_tracking}</small>}
       {(c.tracking?.subjects||[]).filter(s=>s.tracked_sent>0).length>0&&<div className={styles.abResultGrid}>{(c.tracking?.subjects||[]).filter(s=>s.tracked_sent>0).slice(0,6).map(s=><div className={styles.abResultItem} key={s.subject}><b>Тема</b><span>{s.subject}</span><strong>сигналы {s.opened_unique}/{s.tracked_sent} · {s.open_rate_pct}%</strong><em>≈ человек {s.likely_human_open_rate_pct||0}% · ответов {s.replied_unique}</em></div>)}</div>}
       {c.current_copy_decision&&<div className={`${styles.abDecision} ${styles[`abDecision_${c.current_copy_decision.state}`]||""}`}><span>{c.current_copy_decision.state==="promising"?"★":c.current_copy_decision.state==="needs_revision"?"!":"◴"}</span><div><strong>Что BORIS делает с новым текстом</strong><p>{c.current_copy_decision.recommendation}</p></div></div>}
       {ab.length>0&&<div className={styles.abResultGrid}>{ab.map(s=>{
         const positive=s.positive_replied||0;
         const optOut=s.opt_out||0;
         const conversion=s.sent?Math.round((positive/s.sent)*1000)/10:0;
         return <div className={styles.abResultItem} key={s.variant}><b>{s.variant}</b><span>{s.sent} отправлено</span><strong>{positive} полезных ответов</strong><em>{conversion}%</em>{optOut>0&&<small>отказов: {optOut}</small>}</div>
       })}</div>}
       {c.ab_decision&&<div className={`${styles.abDecision} ${styles[`abDecision_${c.ab_decision.state}`]||""}`}><span>{c.ab_decision.state==="leader"?"★":c.ab_decision.state==="close"?"≈":"◴"}</span><div><strong>Решение BORIS{c.ab_decision.leader?` · лидер ${c.ab_decision.leader}`:""}</strong><p>{c.ab_decision.recommendation}</p></div></div>}
       <div className={styles.actions}><button onClick={()=>useCampaignCopy(c)}>✉ Письмо</button><button onClick={()=>action(c.id,"audience")}>⌕ Собрать базу</button>{c.status!=="active"?<button className={styles.go} disabled={!c.mailbox_id} onClick={()=>action(c.id,"active")}>▶ Запустить</button>:<button onClick={()=>action(c.id,"paused")}>Ⅱ Пауза</button>}</div>
      </div>
    })}
   </div>
  </section>

  <section className={`${styles.panel} ${styles.panelOrange}`}><div className={styles.panelHead}><div className={`${styles.num} ${styles.sectionGlyph}`}>↩</div><div><h2>Ответы — это лиды</h2><p>Ответ клиента автоматически связывается с компанией, BORIS определяет этап и ставит следующий шаг в CRM.</p></div></div>{!replies.length?<div className={styles.empty}><span className={styles.emptyIcon}>↩</span><div><strong>Ответов пока нет</strong><small>После первого ответа BORIS покажет компанию, этап и следующий шаг.</small></div></div>:<div className={styles.replyGrid}>{replies.map((r,i)=><div className={`${styles.replyCard} ${styles[`runTone${i%4}`]}`} key={r.id}><div className={styles.replyIcon}>↩</div><strong>{r.company||r.from_email}</strong><small>{r.from_email} · {r.subject||"Без темы"}</small><p>{r.body_preview?.slice(0,180)}</p><span>{replyStage(r)} · {replyNext(r)}{r.crm_lead_id?` · Клиент #${r.crm_lead_id}`:""}{replyNotify(r)}</span></div>)}</div>}</section>
 </main></AppShell>
}
