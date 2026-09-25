"use client";
import { Button, Card as BorisCard, EmptyState, Icon, type IconName } from "@/app/ui";


import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAccounts } from "@/app/lib/AccountContext";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

type Overview = {
  open_deals: number;
  won_deals: number;
  lost_deals: number;
  won_amount_kopeks: number;
  stale_deals: number;
  open_tasks: number;
  overdue_tasks: number;
  contacts: number;
  conversations: number;
};

type Deal = {
  id: number;
  title: string;
  amount_kopeks?: number | null;
  stage_id: number;
  status: string;
  source?: string | null;
  contact_id?: number | null;
  contact_name?: string | null;
  contact_phone?: string | null;
  responsible_user_id?: number | null;
  responsible_email?: string | null;
  avito_account_id?: string | null;
  avito_chat_id?: string | null;
  last_activity_at?: string | null;
  next_action_at?: string | null;
  visual_type?: string | null;
  business_activity?: string | null;
};

type Column = {
  id: number;
  name: string;
  position: number;
  semantic_type: string;
  count: number;
  amount_kopeks: number;
  deals: Deal[];
};

type Kanban = {
  pipeline: {
    id: number;
    name: string;
  } | null;
  columns: Column[];
};

type Contact = {
  id: number;
  display_name?: string | null;
  primary_phone?: string | null;
  primary_email?: string | null;
  company_name?: string | null;
  deal_count: number;
  open_deals: number;
  next_action_title?: string | null;
  next_action_at?: string | null;
  visual_type?: string | null;
  business_activity?: string | null;
};

type Inquiry = { id:number; account_id:string; external_chat_id:string; title?:string|null; unread_count:number; last_message_at?:string|null; contact_id?:number|null; contact_name?:string|null; contact_phone?:string|null; lead_stage?:string|null; has_phone?:boolean|null; msg_count?:number|null; incoming_count?:number|null; item_title?:string|null; reminder_at?:string|null; note?:string|null; assigned_user_id?:number|null; };
type CRMTask = { id:number; title:string; description?:string|null; due_at?:string|null; status:string; source?:string|null; deal_id?:number|null; contact_id?:number|null; deal_title?:string|null; contact_name?:string|null; assigned_user_id?:number|null; responsible_user_id?:number|null; overdue_seconds?:number|null; reminder_count?:number|null; last_reminded_at?:string|null; escalation_level?:string|null; owner_action_required?:boolean|null; };

function crmTaskReminderLabel(task: CRMTask): string {
  const count=Number(task.reminder_count||0);
  const sec=Math.max(0,Number(task.overdue_seconds||0));
  const level=String(task.escalation_level||"none");
  if(!count && !sec && level==="none") return "";
  const age=sec>=86400 ? String(Math.max(1,Math.floor(sec/86400)))+" д." : String(Math.max(1,Math.floor(sec/3600)))+" ч.";
  const title=level==="manager_critical"?"Критично для менеджера":level==="manager_urgent"?"Срочно для менеджера":level==="manager_repeat"?"Повторное напоминание менеджеру":level==="manager_reminder"?"BORIS напомнил менеджеру":"Контроль BORIS";
  return title+(sec?" · просрочено "+age:"")+(count?" · напоминаний BORIS: "+String(count):"");
}

type ReactivationCandidate = {
  id:number; account_id:string; chat:string; title?:string|null; reason?:string|null;
  status?:string|null; age_days?:number|null; score?:number|null; queue?:string|null;
  lead_temperature?:string|null; last_contact_at?:string|null; recommendation?:string|null;
  intent?:string|null; qualification_stage?:string|null; target_action?:string|null; next_action?:string|null;
  phone_received?:boolean|null; had_calculation?:boolean|null;
};

type ClientPoolItem = {
  key:string;
  accountId:string;
  contactId?:number|null;
  inquiryId?:number|null;
  dealId?:number|null;
  taskId?:number|null;
  chatId?:string|null;
  name:string;
  phone?:string|null;
  email?:string|null;
  company?:string|null;
  badge:string;
  reason?:string|null;
  nextAction?:string|null;
  nextActionAt?:string|null;
  source?:string|null;
};

type Analytics = {
  days: number;
  totals: { deals:number; won:number; lost:number; open:number; won_amount_kopeks:number; };
  funnel?: { inquiries:number; contacts_received:number; deals:number; won:number; lost:number; inquiry_to_deal_pct:number|null; deal_to_won_pct:number|null; };
  quality?: { no_next_action:number; no_responsible:number; no_amount:number; no_phone:number; };
  quality_deals?: Array<{id:number;title:string;stage_name?:string|null;contact_name?:string|null;amount_kopeks?:number|null;responsible_user_id?:number|null;next_action_at?:string|null;primary_phone?:string|null;reasons:string[]}>;
  pipeline_value?: {open_deals:number;deals_with_amount:number;open_amount_kopeks:number;weighted_forecast_kopeks:number|null;forecast_reason?:string|null};
  managers?: Array<{responsible_user_id?:number|null;responsible_name:string;open_deals:number;won:number;lost:number;won_amount_kopeks:number;no_next_action:number;overdue_tasks:number}>;
  calls?: {total:number;answered:number;missed:number;linked_to_deal:number;analyzed:number;callback_overdue:number};
  lost_reasons?: Array<{reason:string;deals:number}>;
  stages: Array<{
    id: number;
    name: string;
    deals: number;
    amount_kopeks: number;
  }>;
  sources: Array<{
    source: string;
    deals: number;
    won: number;
    won_amount_kopeks: number;
  }>;
};

async function api<T>(
  url: string,
  options: RequestInit = {}
): Promise<T> {
  // BORIS_CRM_PAGE_AUTH_V3
  // Все запросы CRM обязаны использовать тот же JWT,
  // с которым пользователь вошёл в основной BORIS.
  const token =
    typeof window !== "undefined"
      ? localStorage.getItem("boris_token")
      : null;

  if (!token || token === "undefined" || token === "null") {
    throw new Error("AUTH_REQUIRED");
  }

  const headers = new Headers(options.headers || {});

  headers.set("Authorization", "Bearer " + token);

  if (
    options.body &&
    !(options.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(url, {
    ...options,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });

  if (res.status === 401) {
    throw new Error("AUTH_REQUIRED");
  }

  if (res.status === 403) {
    throw new Error("ACCESS_DENIED");
  }

  if (!res.ok) {
    let detail = "";

    try {
      detail = await res.text();
    } catch {}

    throw new Error(
      "CRM_HTTP_" +
        res.status +
        (detail ? ": " + detail.slice(0, 300) : "")
    );
  }

  return (await res.json()) as T;
}

function stageRecommendation(stageName: string, hasPhone?: boolean | null) {
  const s = String(stageName || "").toLowerCase();
  if (s.includes("нов")) return hasPhone ? "Позвонить и уточнить потребность" : "Ответить и уточнить потребность";
  if (s.includes("связ")) return "Уточнить размеры, бюджет и срок";
  if (s.includes("квалиф")) return "Подготовить расчёт / предложение";
  if (s.includes("в работе")) return "Зафиксировать следующий договорённый шаг";
  if (s.includes("кп") || s.includes("предлож")) return "Получить решение по предложению";
  if (s.includes("договор")) return "Контролировать договор и оплату";
  return "Назначить следующий шаг";
}

function contactVisual(contact: Contact): { icon: IconName; kind: string; tone: string; glyph?: string } {
  const company = String(contact.company_name || "").trim();
  const name = String(contact.display_name || "").trim();
  const hay = `${company} ${name}`.toLowerCase();
  const explicit=String(contact.visual_type||"auto").toLowerCase();
  const activity=String(contact.business_activity||"").toLowerCase();
  if(explicit==="female") return {icon:"user",kind:"Частный клиент",tone:"rose",glyph:"👩"};
  if(explicit==="male") return {icon:"user",kind:"Частный клиент",tone:"blue",glyph:"👨"};
  if(explicit==="neutral") return {icon:"user",kind:"Частный клиент",tone:"blue"};
  if(explicit==="company") {
    const map:Record<string,{icon:IconName,kind:string,tone:string}>={production:{icon:"building",kind:"Компания · производство",tone:"amber"},trade:{icon:"building",kind:"Компания · торговля",tone:"emerald"},transport:{icon:"briefcase",kind:"Компания · транспорт",tone:"cyan"},marketing:{icon:"chart",kind:"Компания · маркетинг",tone:"violet"},services:{icon:"briefcase",kind:"Компания · услуги",tone:"cyan"},other:{icon:"building",kind:"Компания",tone:"emerald"}};
    return map[activity]||map.other;
  }
  const businessProfile = Boolean(company) || /(^|\s)(ооо|ип|магазин|студия|салон|компания|производство|товары)(\s|$)|мебел|кухн|строй|автосервис|логист|агентств/.test(hay);
  if (businessProfile) {
    if (/кух|мебел|шкаф|гардероб|интерьер|строй|ремонт|производ/.test(hay)) return {icon:"building",kind:"Компания · производство",tone:"amber"};
    if (/авто|машин|транс|логист|достав|сервис/.test(hay)) return {icon:"briefcase",kind:"Компания · транспорт и услуги",tone:"cyan"};
    if (/маркет|реклам|digital|агент/.test(hay)) return {icon:"chart",kind:"Компания · маркетинг",tone:"violet"};
    if (/магазин|товар|продаж|салон/.test(hay)) return {icon:"building",kind:"Компания · торговля",tone:"emerald"};
    return {icon:"building",kind:"Компания",tone:"emerald"};
  }
  const first = name.split(/\s+/)[0].toLowerCase();
  const humanName=/^[а-яёa-z]{3,}$/i.test(first) && !/^(клиент|пользователь|частное|товары|unknown|boom|fe|ст)$/i.test(first);
  if(!humanName) return {icon:"user",kind:"Частный клиент",tone:"blue"};
  const female = /(а|я|ия)$/.test(first) && !/(никита|илья|фома|кузьма|лука)$/.test(first);
  const male = /(й|р|н|м|в|д|г|б|л|с|т|к|п|ф|х|ц|ч|ш|щ)$/.test(first) && !/(гульзиган|сабрин)$/i.test(first);
  if(female) return {icon:"user",kind:"Частный клиент",tone:"rose",glyph:"👩"};
  if(male) return {icon:"user",kind:"Частный клиент",tone:"blue",glyph:"👨"};
  return {icon:"user",kind:"Частный клиент",tone:"blue"};
}

function money(v?: number | null) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format((v || 0) / 100);
}

export default function CRMPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { selectedAccountId, selectedAccount, loading: accountsLoading } = useAccounts();

  type CRMTab = "today"|"inquiries"|"kanban"|"contacts"|"tasks"|"calendar"|"analytics"|"rop"|"reactivation";
  const normalizeTab = (raw: string | null): CRMTab => {
    const map: Record<string, CRMTab> = { today:"today", inquiries:"inquiries", kanban:"kanban", deals:"kanban", pipeline:"kanban", contacts:"contacts", clients:"contacts", tasks:"tasks", calendar:"calendar", analytics:"analytics", rop:"rop", reactivation:"reactivation" };
    return map[String(raw || "").toLowerCase()] || "today";
  };
  const [tab, setTab] = useState<CRMTab>(() => normalizeTab(searchParams.get("tab")));
  const [overview, setOverview] = useState<Overview | null>(null);
  const [kanban, setKanban] = useState<Kanban | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [inquiries, setInquiries] = useState<Inquiry[]>([]);
  const [tasks, setTasks] = useState<CRMTask[]>([]);
  const [salesSettings, setSalesSettings] = useState<any>(null);
  const [savingDealTrigger, setSavingDealTrigger] = useState(false);
  const [newDealOpen,setNewDealOpen]=useState(false);
  const [newDealTitle,setNewDealTitle]=useState("");
  const [newDealContact,setNewDealContact]=useState("");
  const [newDealAmount,setNewDealAmount]=useState("");
  const [creatingDeal,setCreatingDeal]=useState(false);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [poolSearch, setPoolSearch] = useState("");
  const [reactivationCandidates, setReactivationCandidates] = useState<ReactivationCandidate[]>([]);
  const [reactivationLoading, setReactivationLoading] = useState(false);
  const [actionClient, setActionClient] = useState<ClientPoolItem | null>(null);
  const [actionDetail, setActionDetail] = useState<any>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [quickTaskTitle, setQuickTaskTitle] = useState("");
  const [quickTaskDue, setQuickTaskDue] = useState("");
  const [quickDealTitle, setQuickDealTitle] = useState("");
  const [quickDealAmount, setQuickDealAmount] = useState("");

  async function load() {
    setLoading(true);
    setError("");

    try {
      if (!selectedAccountId) {
        setOverview(null); setKanban(null); setContacts([]); setAnalytics(null); setInquiries([]); setTasks([]); setSalesSettings(null);
        return;
      }
      const q = encodeURIComponent(selectedAccountId);
      const [o, k, c, a, iq, ts, ss] = await Promise.all([
        api<Overview>(`/api/crm/product/overview?account_id=${q}`),
        api<Kanban>(`/api/crm/product/kanban?account_id=${q}`),
        api<Contact[]>(`/api/crm/product/contacts?account_id=${q}`),
        api<Analytics>(`/api/crm/product/analytics?days=30&account_id=${q}`),
        api<Inquiry[]>(`/api/crm/product/inquiries?account_id=${q}`),
        api<CRMTask[]>(`/api/crm/product/tasks?account_id=${q}`),
        api<any>(`/api/messenger/sales/settings?account_id=${q}`),
      ]);

      setOverview(o);
      setKanban(k);
      setContacts(c);
      setAnalytics(a); setInquiries(iq); setTasks(ts); setSalesSettings(ss);
    } catch (e) {
      setOverview(null);
      setKanban(null);
      setContacts([]);
      setAnalytics(null); setInquiries([]); setTasks([]); setSalesSettings(null);
      setError(e instanceof Error ? e.message : "Ошибка системы продаж");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [selectedAccountId]);

  useEffect(() => {
    setTab(normalizeTab(searchParams.get("tab")));
  }, [searchParams]);

  useEffect(() => {
    if (tab !== "reactivation" || !selectedAccountId) return;
    let cancelled = false;
    setReactivationLoading(true);
    api<{items?:ReactivationCandidate[]}>(`/api/reactivation/candidates?queue=reactivation&account_id=${encodeURIComponent(selectedAccountId)}`)
      .then((data) => { if (!cancelled) setReactivationCandidates(Array.isArray(data?.items) ? data.items : []); })
      .catch(() => { if (!cancelled) setReactivationCandidates([]); })
      .finally(() => { if (!cancelled) setReactivationLoading(false); });
    return () => { cancelled = true; };
  }, [tab, selectedAccountId]);

  const sectionMeta: Record<CRMTab,{title:string;subtitle:string;empty:string}> = {
    today:{title:"Клиенты на сегодня",subtitle:"Все клиенты, по которым сегодня есть действие, просрочка или новое обращение.",empty:"На сегодня нет клиентов, требующих действия."},
    inquiries:{title:"Новые клиенты",subtitle:"Входящие обращения до создания сделки. Откройте клиента и сразу ответьте или переведите в сделку.",empty:"Новых клиентов без сделки сейчас нет."},
    kanban:{title:"Клиенты в воронке",subtitle:"Клиенты со сделками на всех этапах продаж. Все действия доступны из одной карточки.",empty:"В воронке пока нет клиентов."},
    contacts:{title:"Все клиенты",subtitle:"Единый список клиентов с диалогами, сделками, задачами и следующими действиями.",empty:"Клиентов пока нет."},
    tasks:{title:"Клиенты с задачами",subtitle:"Клиенты, по которым есть открытые задачи, звонки, напоминания или обязательства.",empty:"Открытых задач по клиентам нет."},
    calendar:{title:"Клиенты по календарю",subtitle:"Клиенты с ближайшими действиями, отсортированные по срокам.",empty:"Запланированных действий по клиентам нет."},
    analytics:{title:"Клиенты, влияющие на показатели",subtitle:"Сделки и клиенты, где аналитика видит проблемы качества ведения или риск потери.",empty:"Проблемных клиентов по аналитике сейчас нет."},
    rop:{title:"Клиенты для контроля РОП",subtitle:"Клиенты, где руководителю нужно проверить следующий шаг, ответственного или качество ведения.",empty:"Клиентов, требующих контроля РОП, сейчас нет."},
    reactivation:{title:"Клиенты для реактивации",subtitle:"Клиенты, которых BORIS считает кандидатами на возврат. Диалог и реактивация доступны прямо из списка.",empty:"Кандидатов на реактивацию сейчас нет."},
  };

  const clientPool = useMemo<ClientPoolItem[]>(() => {
    const pool = new Map<string,ClientPoolItem>();
    const columns = kanban?.columns || [];
    const deals = columns.flatMap((column) => column.deals || []);
    const contactById = new Map<number,Contact>(contacts.map((contact) => [Number(contact.id), contact]));
    const dealById = new Map<number,Deal>(deals.map((deal) => [Number(deal.id), deal]));
    const qualityByDeal = new Map<number,string>((analytics?.quality_deals || []).map((item) => [Number(item.id), (item.reasons || []).join(" · ")]));

    const merge = (item: ClientPoolItem) => {
      const stableKey = item.contactId ? `contact-${item.contactId}` : item.chatId ? `chat-${item.chatId}` : item.key;
      const prev = pool.get(stableKey);
      const reasons = Array.from(new Set([prev?.reason, item.reason].filter(Boolean) as string[]));
      pool.set(stableKey, {
        ...(prev || item),
        ...item,
        key: stableKey,
        contactId: item.contactId ?? prev?.contactId ?? null,
        inquiryId: item.inquiryId ?? prev?.inquiryId ?? null,
        dealId: item.dealId ?? prev?.dealId ?? null,
        taskId: item.taskId ?? prev?.taskId ?? null,
        chatId: item.chatId ?? prev?.chatId ?? null,
        phone: item.phone ?? prev?.phone ?? null,
        email: item.email ?? prev?.email ?? null,
        company: item.company ?? prev?.company ?? null,
        nextAction: item.nextAction ?? prev?.nextAction ?? null,
        nextActionAt: item.nextActionAt ?? prev?.nextActionAt ?? null,
        source: item.source ?? prev?.source ?? null,
        reason: reasons.join(" · ") || null,
      });
    };

    const addContact = (contact: Contact, badge = "Клиент", reason?: string | null) => {
      merge({
        key:`contact-${contact.id}`,
        accountId:selectedAccountId || "",
        contactId:contact.id,
        name:contact.display_name || contact.company_name || `Клиент #${contact.id}`,
        phone:contact.primary_phone,
        email:contact.primary_email,
        company:contact.company_name,
        badge,
        reason:reason || null,
        nextAction:contact.next_action_title,
        nextActionAt:contact.next_action_at,
      });
    };

    const addDeal = (deal: Deal, badge = "Сделка", reason?: string | null) => {
      const contact = deal.contact_id ? contactById.get(Number(deal.contact_id)) : undefined;
      const stage = columns.find((column) => column.deals?.some((item) => Number(item.id) === Number(deal.id)));
      merge({
        key:deal.contact_id ? `contact-${deal.contact_id}` : `deal-${deal.id}`,
        accountId:selectedAccountId || deal.avito_account_id || "",
        contactId:deal.contact_id,
        dealId:deal.id,
        chatId:deal.avito_chat_id,
        name:contact?.display_name || deal.contact_name || deal.title || `Сделка #${deal.id}`,
        phone:contact?.primary_phone || deal.contact_phone,
        email:contact?.primary_email,
        company:contact?.company_name,
        badge,
        reason:reason || qualityByDeal.get(Number(deal.id)) || (stage?.name ? `Этап: ${stage.name}` : null),
        nextAction:deal.next_action_at ? "Следующее действие запланировано" : stageRecommendation(stage?.name || "", Boolean(contact?.primary_phone || deal.contact_phone)),
        nextActionAt:deal.next_action_at,
        source:deal.source,
      });
    };

    const addTask = (task: CRMTask, badge = "Задача") => {
      const deal = task.deal_id ? dealById.get(Number(task.deal_id)) : undefined;
      const contactId = task.contact_id || deal?.contact_id || null;
      const contact = contactId ? contactById.get(Number(contactId)) : undefined;
      merge({
        key:contactId ? `contact-${contactId}` : task.deal_id ? `deal-${task.deal_id}` : `task-${task.id}`,
        accountId:selectedAccountId || deal?.avito_account_id || "",
        contactId,
        dealId:task.deal_id || deal?.id || null,
        taskId:task.id,
        chatId:deal?.avito_chat_id,
        name:contact?.display_name || task.contact_name || deal?.contact_name || task.deal_title || `Клиент по задаче #${task.id}`,
        phone:contact?.primary_phone || deal?.contact_phone,
        email:contact?.primary_email,
        company:contact?.company_name,
        badge,
        reason:task.due_at && new Date(task.due_at) < new Date() ? "Просрочено" : task.description || null,
        nextAction:task.title,
        nextActionAt:task.due_at,
        source:task.source || deal?.source,
      });
    };

    const addInquiry = (inquiry: Inquiry) => {
      const contact = inquiry.contact_id ? contactById.get(Number(inquiry.contact_id)) : undefined;
      merge({
        key:inquiry.contact_id ? `contact-${inquiry.contact_id}` : `chat-${inquiry.external_chat_id}`,
        accountId:inquiry.account_id || selectedAccountId || "",
        contactId:inquiry.contact_id,
        inquiryId:inquiry.id,
        chatId:inquiry.external_chat_id,
        name:contact?.display_name || inquiry.contact_name || inquiry.title || "Новый клиент",
        phone:contact?.primary_phone || inquiry.contact_phone,
        email:contact?.primary_email,
        company:contact?.company_name,
        badge:inquiry.has_phone || Number(inquiry.incoming_count || 0) >= 2 ? "Новое обращение · готово к работе" : "Новое обращение",
        reason:inquiry.item_title || inquiry.lead_stage || "Входящий диалог",
        nextAction:inquiry.has_phone ? "Перевести в сделку и связаться" : "Ответить и уточнить потребность",
        nextActionAt:inquiry.reminder_at || inquiry.last_message_at,
        source:"Avito",
      });
    };

    const openTasks = tasks
      .filter((task) => task.status === "open")
      .slice()
      .sort((a,b) => (a.due_at ? new Date(a.due_at).getTime() : Number.MAX_SAFE_INTEGER) - (b.due_at ? new Date(b.due_at).getTime() : Number.MAX_SAFE_INTEGER));

    if (tab === "today") {
      const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate()+1); tomorrow.setHours(23,59,59,999);
      openTasks.filter((task) => !task.due_at || new Date(task.due_at) <= tomorrow).forEach((task) => addTask(task, "Сегодня"));
      inquiries.forEach(addInquiry);
      (analytics?.quality_deals || []).slice(0,30).forEach((item) => {
        const deal = dealById.get(Number(item.id));
        if (deal) addDeal(deal, "Требует внимания", (item.reasons || []).join(" · "));
      });
    } else if (tab === "inquiries") {
      inquiries.forEach(addInquiry);
    } else if (tab === "kanban") {
      deals.forEach((deal) => addDeal(deal, deal.status === "open" ? "Воронка · в работе" : deal.status === "won" ? "Воронка · выиграно" : "Воронка · потеряно"));
    } else if (tab === "contacts") {
      contacts.forEach((contact) => addContact(contact, contact.open_deals ? "Клиент · есть сделка" : "Клиент"));
    } else if (tab === "tasks") {
      openTasks.forEach((task) => addTask(task, "Открытая задача"));
    } else if (tab === "calendar") {
      openTasks.forEach((task) => addTask(task, task.due_at && new Date(task.due_at) < new Date() ? "Календарь · просрочено" : "Календарь"));
    } else if (tab === "analytics") {
      (analytics?.quality_deals || []).forEach((item) => {
        const deal = dealById.get(Number(item.id));
        if (deal) addDeal(deal, "Аналитика · требует внимания", (item.reasons || []).join(" · "));
      });
      if (!pool.size) deals.filter((deal) => deal.status === "open").forEach((deal) => addDeal(deal, "Аналитика · активная сделка"));
    } else if (tab === "rop") {
      (analytics?.quality_deals || []).forEach((item) => {
        const deal = dealById.get(Number(item.id));
        if (deal) addDeal(deal, "Контроль РОП", (item.reasons || []).join(" · "));
      });
      deals.filter((deal) => deal.status === "open" && (!deal.next_action_at || !deal.responsible_user_id)).forEach((deal) => addDeal(deal, "Контроль РОП", !deal.responsible_user_id ? "Не назначен ответственный" : "Нет следующего шага"));
    } else if (tab === "reactivation") {
      reactivationCandidates.forEach((candidate) => {
        const relatedDeal = deals.find((deal) => String(deal.avito_chat_id || "") === String(candidate.chat || ""));
        const relatedContact = relatedDeal?.contact_id ? contactById.get(Number(relatedDeal.contact_id)) : undefined;
        merge({
          key:relatedContact ? `contact-${relatedContact.id}` : relatedDeal ? `deal-${relatedDeal.id}` : `reactivation-${candidate.id}`,
          accountId:candidate.account_id || selectedAccountId || "",
          contactId:relatedContact?.id || relatedDeal?.contact_id || null,
          dealId:relatedDeal?.id || null,
          chatId:candidate.chat,
          name:relatedContact?.display_name || relatedDeal?.contact_name || candidate.title || "Клиент для реактивации",
          phone:relatedContact?.primary_phone || relatedDeal?.contact_phone || null,
          email:relatedContact?.primary_email || null,
          company:relatedContact?.company_name || null,
          badge:candidate.lead_temperature === "hot" ? "Реактивация · горячий" : "Реактивация",
          reason:[candidate.reason, candidate.intent, candidate.qualification_stage].filter(Boolean).join(" · ") || "Клиент перестал отвечать",
          nextAction:candidate.recommendation || candidate.next_action || candidate.target_action || "Вернуть клиента в диалог",
          nextActionAt:candidate.last_contact_at,
          source:"Реактивация",
        });
      });
    }

    return Array.from(pool.values());
  }, [tab, selectedAccountId, contacts, inquiries, tasks, kanban, analytics, reactivationCandidates]);

  const visibleClientPool = useMemo(() => {
    const q = poolSearch.trim().toLowerCase();
    if (!q) return clientPool;
    return clientPool.filter((item) => [item.name,item.phone,item.email,item.company,item.badge,item.reason,item.nextAction,item.source].filter(Boolean).some((value) => String(value).toLowerCase().includes(q)));
  }, [clientPool, poolSearch]);

  async function openClientActions(item: ClientPoolItem) {
    setActionClient(item);
    setActionDetail(null);
    setQuickTaskTitle(item.nextAction || "Связаться с клиентом");
    setQuickTaskDue("");
    setQuickDealTitle(`Сделка · ${item.name}`);
    setQuickDealAmount("");
    if (!item.contactId) return;
    setActionLoading(true);
    try {
      const detail = await api<any>(`/api/crm/product/contacts/${item.contactId}?account_id=${encodeURIComponent(item.accountId || selectedAccountId || "")}`);
      setActionDetail(detail);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить действия по клиенту");
    } finally {
      setActionLoading(false);
    }
  }

  async function refreshActionDetail() {
    if (!actionClient?.contactId) return;
    const detail = await api<any>(`/api/crm/product/contacts/${actionClient.contactId}?account_id=${encodeURIComponent(actionClient.accountId || selectedAccountId || "")}`);
    setActionDetail(detail);
  }

  async function createQuickTaskForClient() {
    if (!actionClient || !quickTaskTitle.trim()) return;
    if (!actionClient.contactId && !actionClient.dealId) {
      setError("Для постановки задачи сначала создайте сделку или карточку клиента.");
      return;
    }
    setActionBusy(true);
    try {
      const accountId = actionClient.accountId || selectedAccountId || "";
      await api(`/api/crm/product/tasks?account_id=${encodeURIComponent(accountId)}`, {
        method:"POST",
        body:JSON.stringify({
          contact_id:actionClient.contactId || null,
          deal_id:actionClient.dealId || null,
          title:quickTaskTitle.trim(),
          due_at:quickTaskDue ? new Date(quickTaskDue).toISOString() : null,
        }),
      });
      setQuickTaskTitle("");
      setQuickTaskDue("");
      await load();
      await refreshActionDetail();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось создать задачу");
    } finally {
      setActionBusy(false);
    }
  }

  async function createQuickDealForClient() {
    if (!actionClient) return;
    if (!actionClient.contactId && actionClient.chatId) {
      await promoteActionInquiry();
      return;
    }
    if (!actionClient.contactId || !quickDealTitle.trim()) return;
    setActionBusy(true);
    try {
      const accountId = actionClient.accountId || selectedAccountId || "";
      await api(`/api/crm/deals`, {
        method:"POST",
        body:JSON.stringify({
          title:quickDealTitle.trim(),
          account_id:accountId,
          contact_id:actionClient.contactId,
          amount_kopeks:quickDealAmount ? Number(quickDealAmount)*100 : null,
          source:"manual",
        }),
      });
      setQuickDealAmount("");
      await load();
      await refreshActionDetail();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось создать сделку");
    } finally {
      setActionBusy(false);
    }
  }

  async function promoteActionInquiry() {
    if (!actionClient?.chatId) return;
    setActionBusy(true);
    try {
      await api("/api/crm/bridge/promote-inquiry", {
        method:"POST",
        body:JSON.stringify({account_id:actionClient.accountId || selectedAccountId || "", avito_chat_id:actionClient.chatId}),
      });
      await load();
      setActionClient(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось перевести обращение в сделку");
    } finally {
      setActionBusy(false);
    }
  }

  async function completeActionTask(taskId:number) {
    if (!actionClient) return;
    setActionBusy(true);
    try {
      await api(`/api/crm/product/tasks/${taskId}?account_id=${encodeURIComponent(actionClient.accountId || selectedAccountId || "")}`, {method:"PATCH",body:JSON.stringify({status:"done"})});
      await load();
      await refreshActionDetail();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось завершить задачу");
    } finally {
      setActionBusy(false);
    }
  }

  function openTab(next: CRMTab) {
    setTab(next);
    setPoolSearch("");
    const qs = new URLSearchParams(searchParams.toString());
    if (next === "today") qs.delete("tab"); else qs.set("tab", next);
    router.replace(`/dashboard/crm${qs.toString() ? `?${qs.toString()}` : ""}`, { scroll: false });
    window.setTimeout(() => document.getElementById("boris-crm-client-pool")?.scrollIntoView({behavior:"smooth",block:"start"}), 0);
  }

  const dealTriggerLabels: Record<string,string> = {
    first_inquiry: "С первого обращения",
    after_engaged: "После содержательного продолжения диалога",
    after_qualification: "После квалификации",
    after_contact: "После получения телефона / контакта",
    after_target_action: "После целевого действия",
  };

  async function saveDealTrigger(next: string) {
    if (!selectedAccountId || !salesSettings) return;
    setSavingDealTrigger(true);
    try {
      const q = encodeURIComponent(selectedAccountId);
      await api(`/api/messenger/sales/settings?account_id=${q}`, {
        method: "POST",
        body: JSON.stringify({...salesSettings, deal_trigger: next}),
      });
      setSalesSettings({...salesSettings, deal_trigger: next});
      await load();
    } finally {
      setSavingDealTrigger(false);
    }
  }

  async function moveDealToStage(dealId: number, stage: Column) {
    let lost_reason: string | undefined;
    if (stage.semantic_type === "lost") {
      lost_reason = window.prompt("Почему сделка потеряна? Причина обязательна для аналитики.", "")?.trim() || "";
      if (!lost_reason) return;
    }
    try {
      await api(`/api/crm/bridge/deal/${dealId}/stage`, {
        method: "POST",
        body: JSON.stringify({stage_id: stage.id, lost_reason}),
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось изменить этап сделки");
    }
  }

  async function createManualDeal() {
    if (!selectedAccountId || !newDealTitle.trim()) return;
    setCreatingDeal(true);
    try {
      const result=await api<{ok:boolean;id:number}>(`/api/crm/deals`,{
        method:"POST",
        body:JSON.stringify({
          title:newDealTitle.trim(),
          account_id:selectedAccountId,
          contact_id:newDealContact?Number(newDealContact):null,
          amount_kopeks:newDealAmount?Number(newDealAmount)*100:null,
          source:"manual",
        }),
      });
      setNewDealOpen(false);setNewDealTitle("");setNewDealContact("");setNewDealAmount("");
      await load();
      if(result?.id) router.push(`/dashboard/crm/deal/${result.id}`);
    } catch(e) {
      setError(e instanceof Error?e.message:"Не удалось создать сделку");
    } finally { setCreatingDeal(false); }
  }

  const filteredContacts = useMemo(() => {
    const q = search.trim().toLowerCase();

    if (!q) return contacts;

    return contacts.filter((c) =>
      [
        c.display_name,
        c.primary_phone,
        c.primary_email,
        c.company_name,
      ]
        .filter(Boolean)
        .some((x) => String(x).toLowerCase().includes(q))
    );
  }, [contacts, search]);

  return (
    <div className="boris-product-route boris-crm-page" style={{
      minHeight: "100vh",
      background: "var(--boris-background)",
      padding: 24,
      color: "var(--boris-text-primary)",
    }}>
      <div style={{ maxWidth: 1800, margin: "0 auto" }}>
        <div className="boris-crm-header" style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 20,
          alignItems: "flex-start",
          marginBottom: 20,
        }}>
          <div>
            <div style={{
              color: "var(--boris-blue)",
              fontWeight: 700,
              fontSize: 13.5,
              marginBottom: 5,
            }}>
              BORIS · ПРОДАЖИ
            </div>
            <div style={{fontSize:13,color:"var(--boris-text-2)",marginBottom:6}}>
              {accountsLoading ? "Загрузка аккаунта…" : selectedAccount ? `Аккаунт: ${selectedAccount.name}` : selectedAccountId ? `Аккаунт: ${selectedAccountId}` : "Выберите аккаунт в верхней панели"}
            </div>

            <h1 style={{
              margin: 0,
              fontSize: 30,
              letterSpacing: "-0.03em",
            }}>
              Продажи
            </h1>

            <div style={{
              marginTop: 7,
              color: "var(--boris-text-2)",
              fontSize: 14,
            }}>
              Рабочее место продаж: обращения → квалификация → сделка → следующее действие → результат.
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap:"wrap" }}>
            <Button onClick={()=>setNewDealOpen(true)} style={buttonPrimary}>+ Новая сделка</Button>
            <Button
              onClick={() => router.push("/messages")}
              style={buttonGhost}
            >
              Сообщения
            </Button>

            <Button
              onClick={() => router.push("/dashboard/crm/integrations")}
              style={buttonGhost}
            >
              Интеграции
            </Button>

            <Button onClick={load} style={buttonPrimary}>
              Обновить
            </Button>
          </div>
        </div>

        {newDealOpen ? <div className="boris-crm-modal-backdrop" onMouseDown={(e)=>{if(e.target===e.currentTarget)setNewDealOpen(false)}}>
          <div className="boris-crm-modal-card">
            <div className="boris-crm-section-header"><div><div className="boris-crm-kicker">НОВАЯ СДЕЛКА</div><h2 style={{margin:"3px 0 0"}}>Создать вручную</h2></div><button className="boris-crm-modal-close" onClick={()=>setNewDealOpen(false)}>×</button></div>
            <label className="boris-crm-form-label">Название сделки</label><input autoFocus value={newDealTitle} onChange={e=>setNewDealTitle(e.target.value)} placeholder="Например: Кухня для Елены, Уфа" className="boris-crm-form-input"/>
            <label className="boris-crm-form-label">Клиент</label><select value={newDealContact} onChange={e=>setNewDealContact(e.target.value)} className="boris-crm-form-input"><option value="">Без привязанного клиента</option>{contacts.map(c=><option key={c.id} value={c.id}>{c.display_name||`Клиент #${c.id}`}{c.primary_phone?` · ${c.primary_phone}`:""}</option>)}</select>
            <label className="boris-crm-form-label">Сумма, ₽</label><input value={newDealAmount} onChange={e=>setNewDealAmount(e.target.value.replace(/[^0-9]/g,""))} placeholder="Введите сумму" className="boris-crm-form-input"/>
            <div style={{display:"flex",gap:8,justifyContent:"flex-end",marginTop:16}}><Button style={buttonGhost} onClick={()=>setNewDealOpen(false)}>Отмена</Button><Button disabled={creatingDeal||!newDealTitle.trim()} style={buttonPrimary} onClick={()=>void createManualDeal()}>{creatingDeal?"Создаём…":"Создать сделку"}</Button></div>
          </div>
        </div> : null}

        {actionClient ? (() => {
          const detailContact = actionDetail?.contact || null;
          const conversations = Array.isArray(actionDetail?.conversations) ? actionDetail.conversations : [];
          const detailDeals = Array.isArray(actionDetail?.deals) ? actionDetail.deals : [];
          const detailTasks = Array.isArray(actionDetail?.tasks) ? actionDetail.tasks : [];
          const actionChatId = actionClient.chatId || conversations[0]?.external_chat_id || "";
          const actionPhone = actionClient.phone || detailContact?.primary_phone || "";
          const actionEmail = actionClient.email || detailContact?.primary_email || "";
          const accountId = actionClient.accountId || selectedAccountId || "";
          return <div className="boris-crm-modal-backdrop" onMouseDown={(e)=>{if(e.target===e.currentTarget)setActionClient(null)}}>
            <div className="boris-crm-modal-card" style={{width:"min(1120px,calc(100vw - 40px))",maxWidth:1120,maxHeight:"calc(100vh - 40px)",overflow:"auto"}}>
              <div className="boris-crm-section-header">
                <div style={{minWidth:0}}>
                  <div className="boris-crm-kicker">{actionClient.badge}</div>
                  <h2 style={{margin:"3px 0 3px",fontSize:23,overflowWrap:"anywhere"}}>{actionClient.name}</h2>
                  <div style={{fontSize:12,color:"var(--boris-text-2)"}}>{actionClient.company || actionPhone || actionEmail || actionClient.source || "Контакт уточняется"}</div>
                </div>
                <button className="boris-crm-modal-close" onClick={()=>setActionClient(null)}>×</button>
              </div>

              <div style={{display:"flex",gap:7,flexWrap:"wrap",marginTop:12,paddingBottom:13,borderBottom:"1px solid var(--boris-border-soft)"}}>
                {actionClient.contactId ? <Button style={buttonPrimary} onClick={()=>router.push(`/dashboard/crm/contact/${actionClient.contactId}`)}>Открыть карточку клиента</Button> : null}
                {actionClient.dealId ? <Button style={buttonGhost} onClick={()=>router.push(`/dashboard/crm/deal/${actionClient.dealId}`)}>Открыть сделку</Button> : null}
                {actionChatId ? <Button style={buttonGhost} onClick={()=>router.push(`/messages?account_id=${encodeURIComponent(accountId)}&chat_id=${encodeURIComponent(actionChatId)}`)}>Открыть диалог</Button> : null}
                {actionPhone ? <a href={`tel:${actionPhone}`} style={{...buttonGhost,textDecoration:"none",display:"inline-flex",alignItems:"center",fontSize:13,color:"var(--boris-text-primary)"}}>Позвонить {actionPhone}</a> : null}
                {actionEmail ? <a href={`mailto:${actionEmail}`} style={{...buttonGhost,textDecoration:"none",display:"inline-flex",alignItems:"center",fontSize:13,color:"var(--boris-text-primary)"}}>Написать email</a> : null}
                <Button style={buttonGhost} onClick={()=>router.push(`/rop?account_id=${encodeURIComponent(accountId)}`)}>Открыть в РОП</Button>
                {actionChatId ? <Button style={buttonGhost} onClick={()=>router.push(`/reactivation?account_id=${encodeURIComponent(accountId)}&chat_id=${encodeURIComponent(actionChatId)}`)}>Реактивация</Button> : null}
              </div>

              {actionLoading ? <div style={{padding:"24px 0",color:"var(--boris-text-2)"}}>Загружаем сделки, задачи и диалоги клиента…</div> : null}

              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(300px,1fr))",gap:14,marginTop:14}}>
                <div style={{display:"grid",gap:14,alignContent:"start"}}>
                  <section style={{border:"1px solid var(--boris-border)",borderRadius:13,padding:14}}>
                    <div className="boris-crm-kicker">СЛЕДУЮЩЕЕ ДЕЙСТВИЕ</div>
                    <h3 style={{margin:"3px 0 5px"}}>Поставить задачу клиенту</h3>
                    <div style={{fontSize:12,color:"var(--boris-text-2)",marginBottom:10}}>Звонок, сообщение, расчёт, замер, контроль оплаты или любое следующее действие.</div>
                    <input value={quickTaskTitle} onChange={(e)=>setQuickTaskTitle(e.target.value)} placeholder="Что нужно сделать" className="boris-crm-form-input"/>
                    <input type="datetime-local" value={quickTaskDue} onChange={(e)=>setQuickTaskDue(e.target.value)} className="boris-crm-form-input" style={{marginTop:8}}/>
                    <Button disabled={actionBusy || !quickTaskTitle.trim() || (!actionClient.contactId && !actionClient.dealId)} style={{...buttonPrimary,width:"100%",marginTop:9}} onClick={()=>void createQuickTaskForClient()}>{actionBusy?"Сохраняем…":"Создать задачу"}</Button>
                    {!actionClient.contactId && !actionClient.dealId ? <div style={{fontSize:11,color:"var(--boris-text-3)",marginTop:7}}>Для нового обращения сначала создайте сделку — после этого задачи и следующий шаг будут доступны.</div> : null}
                  </section>

                  <section style={{border:"1px solid var(--boris-border)",borderRadius:13,padding:14}}>
                    <div className="boris-crm-kicker">СДЕЛКА</div>
                    <h3 style={{margin:"3px 0 5px"}}>{actionClient.contactId ? "Создать ещё одну сделку" : "Перевести клиента в сделку"}</h3>
                    {actionClient.contactId ? <>
                      <input value={quickDealTitle} onChange={(e)=>setQuickDealTitle(e.target.value)} placeholder="Название сделки" className="boris-crm-form-input"/>
                      <input value={quickDealAmount} onChange={(e)=>setQuickDealAmount(e.target.value.replace(/[^0-9]/g,""))} placeholder="Сумма, ₽" className="boris-crm-form-input" style={{marginTop:8}}/>
                      <Button disabled={actionBusy || !quickDealTitle.trim()} style={{...buttonPrimary,width:"100%",marginTop:9}} onClick={()=>void createQuickDealForClient()}>{actionBusy?"Создаём…":"Создать сделку"}</Button>
                    </> : actionChatId ? <>
                      <div style={{fontSize:12,color:"var(--boris-text-2)",lineHeight:1.45}}>BORIS создаст сделку из текущего обращения, сохранив аккаунт и диалог клиента.</div>
                      <Button disabled={actionBusy} style={{...buttonPrimary,width:"100%",marginTop:9}} onClick={()=>void promoteActionInquiry()}>{actionBusy?"Создаём…":"Создать сделку из обращения"}</Button>
                    </> : <div style={{fontSize:12,color:"var(--boris-text-3)"}}>Нет привязанного диалога или карточки клиента для автоматического создания сделки.</div>}
                  </section>
                </div>

                <div style={{display:"grid",gap:14,alignContent:"start"}}>
                  <section style={{border:"1px solid var(--boris-border)",borderRadius:13,padding:14}}>
                    <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"baseline"}}><div><div className="boris-crm-kicker">СДЕЛКИ КЛИЕНТА</div><h3 style={{margin:"3px 0 0"}}>Текущая работа</h3></div><span style={{fontSize:12,color:"var(--boris-text-3)"}}>{detailDeals.length}</span></div>
                    {!detailDeals.length ? <div style={{fontSize:12,color:"var(--boris-text-3)",padding:"12px 0 2px"}}>{actionClient.dealId ? "Откройте текущую сделку кнопкой выше." : "Сделок по карточке клиента пока нет."}</div> :
                    <div style={{marginTop:8}}>{detailDeals.map((deal:any)=><button key={deal.id} onClick={()=>router.push(`/dashboard/crm/deal/${deal.id}`)} style={{width:"100%",textAlign:"left",border:0,borderTop:"1px solid var(--boris-border-soft)",background:"#fff",padding:"10px 2px",cursor:"pointer"}}>
                      <div style={{display:"flex",justifyContent:"space-between",gap:10}}><b>{deal.title || `Сделка #${deal.id}`}</b><span style={{fontSize:11,fontWeight:800}}>{deal.status==="won"?"Выиграна":deal.status==="lost"?"Потеряна":"В работе"}</span></div>
                      <div style={{fontSize:11,color:"var(--boris-text-3)",marginTop:3}}>{deal.stage_name || "Этап не задан"}{deal.next_action_at ? ` · ${new Date(deal.next_action_at).toLocaleString("ru-RU")}` : " · следующий шаг не назначен"}</div>
                    </button>)}</div>}
                  </section>

                  <section style={{border:"1px solid var(--boris-border)",borderRadius:13,padding:14}}>
                    <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"baseline"}}><div><div className="boris-crm-kicker">ЗАДАЧИ</div><h3 style={{margin:"3px 0 0"}}>Что нужно сделать</h3></div><span style={{fontSize:12,color:"var(--boris-text-3)"}}>{detailTasks.filter((task:any)=>task.status==="open").length} открытых</span></div>
                    {!detailTasks.length ? <div style={{fontSize:12,color:"var(--boris-text-3)",padding:"12px 0 2px"}}>{actionClient.nextAction || "Задач по клиенту пока нет."}</div> :
                    <div style={{marginTop:8}}>{detailTasks.slice(0,20).map((task:any)=>{
                      const overdue=task.status==="open" && task.due_at && new Date(task.due_at)<new Date();
                      return <div key={task.id} style={{padding:"9px 0",borderTop:"1px solid var(--boris-border-soft)",display:"grid",gridTemplateColumns:"1fr auto",gap:8,alignItems:"center"}}>
                        <div style={{minWidth:0}}><b style={{fontSize:12,textDecoration:task.status==="done"?"line-through":"none"}}>{task.title}</b><div style={{fontSize:11,color:overdue?"#b42318":"var(--boris-text-3)",marginTop:3}}>{task.status==="done"?"Выполнено":task.due_at?new Date(task.due_at).toLocaleString("ru-RU"):"Срок не задан"}</div>{task.status==="open"&&crmTaskReminderLabel(task)?<div style={{fontSize:11,fontWeight:750,color:task.escalation_level==="manager_critical"?"#b42318":"var(--boris-text-2)",marginTop:4}}>{crmTaskReminderLabel(task)}</div>:null}</div>
                        {task.status==="open" ? <Button disabled={actionBusy} style={{...buttonGhost,padding:"6px 9px"}} onClick={()=>void completeActionTask(Number(task.id))}>Готово</Button> : null}
                      </div>;
                    })}</div>}
                  </section>
                </div>
              </div>
            </div>
          </div>;
        })() : null}

        {error ? (
          <div style={{
            marginBottom: 14,
            padding: 12,
            background: "#FEF3F2",
            color: "var(--b-danger-text)",
            borderRadius: 10,
          }}>
            {error}
          </div>
        ) : null}

        {!loading && !error && overview ? (
        <div className="boris-crm-metrics-grid" style={{
          display: "grid",
          gridTemplateColumns: "repeat(6,minmax(0,1fr))",
          gap: 10,
          marginBottom: 18,
        }}>
          <Metric label="Сделок в работе" value={overview?.open_deals || 0} />
          <Metric label="Выиграно" value={overview?.won_deals || 0} />
          <Metric label="Выручка" value={money(overview?.won_amount_kopeks)} />
          <Metric label="Без следующего шага 3+ дня" value={overview?.stale_deals || 0} />
          <Metric label="Просрочено задач" value={overview?.overdue_tasks || 0} />
          <Metric label="Клиентов" value={overview?.contacts || 0} />
        </div>

        ) : null}

        <BorisCard className="boris-crm-section-launcher" style={{...card,marginBottom:14}}>
          <div className="boris-crm-section-header">
            <div>
              <div className="boris-crm-kicker">РАЗДЕЛЫ ПРОДАЖ</div>
              <h2 style={{margin:"2px 0 0",fontSize:20}}>Перейти к работе</h2>
            </div>
            <span>Все основные действия с клиентами и продажами</span>
          </div>
          <div className="boris-crm-section-grid">
            {([
              {key:"today",title:"Сегодня",description:"Что сделать сейчас, задачи и контроль продаж.",icon:"tasks" as IconName},
              {key:"inquiries",title:`Новые клиенты${inquiries.length ? ` · ${inquiries.length}` : ""}`,description:"Кого нужно довести до сделки.",icon:"messages" as IconName},
              {key:"kanban",title:"Воронка продаж",description:"Сделки, этапы, суммы и следующие действия.",icon:"list" as IconName},
              {key:"contacts",title:"Клиенты",description:"Контакты, история, сделки, задачи и диалоги.",icon:"clients" as IconName},
              {key:"tasks",title:`Задачи${tasks.filter(t=>t.status==="open").length ? ` · ${tasks.filter(t=>t.status==="open").length}` : ""}`,description:"Звонки, напоминания, сроки и выполненные действия.",icon:"tasks" as IconName},
              {key:"calendar",title:"Календарь",description:"Сегодня, завтра и ближайшие действия по клиентам.",icon:"calendar" as IconName},
              {key:"analytics",title:"Аналитика",description:"Конверсия, качество ведения клиентов и причины потерь.",icon:"chart" as IconName},
              {key:"rop",title:"РОП",description:"Контроль качества продаж, звонки, переписки и рекомендации руководителя.",icon:"analytics" as IconName},
              {key:"reactivation",title:`Реактивация${reactivationCandidates.length ? ` · ${reactivationCandidates.length}` : ""}`,description:"Вернуть клиентов, которые остановились и перестали отвечать.",icon:"messages" as IconName},
            ] as const).map(item=><button key={item.key} onClick={()=>openTab(item.key)} className={`boris-crm-section-card ${tab===item.key?"boris-crm-section-card-active":""}`}>
              <span className="boris-crm-section-icon"><Icon name={item.icon} size={20}/></span>
              <span className="boris-crm-section-text"><strong>{item.title}</strong><small>{item.description}</small></span>
              <span className="boris-crm-section-arrow">›</span>
            </button>)}
          </div>
        </BorisCard>

        {!loading && !error ? (
          <div id="boris-crm-client-pool" style={{scrollMarginTop:18,marginBottom:14}}>
            <BorisCard className="boris-crm-card boris-crm-client-pool" style={{...card,padding:18}}>
              <div style={{display:"flex",justifyContent:"space-between",gap:14,alignItems:"flex-start",flexWrap:"wrap"}}>
                <div style={{minWidth:260,flex:"1 1 520px"}}>
                  <div className="boris-crm-kicker">КЛИЕНТЫ ЭТОГО РАЗДЕЛА</div>
                  <h2 style={{margin:"3px 0 5px",fontSize:22}}>{sectionMeta[tab].title}</h2>
                  <div style={{fontSize:13,color:"var(--boris-text-2)",lineHeight:1.45}}>{sectionMeta[tab].subtitle}</div>
                </div>
                <div style={{display:"flex",gap:8,flexWrap:"wrap",alignItems:"center"}}>
                  <span style={{display:"inline-flex",alignItems:"center",height:38,padding:"0 12px",borderRadius:10,background:"var(--boris-blue-soft)",color:"var(--boris-blue)",fontWeight:800,fontSize:13}}>{visibleClientPool.length} клиентов</span>
                  {tab==="rop" ? <Button style={buttonGhost} onClick={()=>router.push(`/rop?account_id=${encodeURIComponent(selectedAccountId || "")}`)}>Открыть полный РОП</Button> : null}
                  {tab==="reactivation" ? <Button style={buttonGhost} onClick={()=>router.push(`/reactivation?account_id=${encodeURIComponent(selectedAccountId || "")}`)}>Открыть центр реактивации</Button> : null}
                </div>
              </div>

              <div style={{display:"flex",alignItems:"center",gap:8,marginTop:14,padding:"0 12px",height:44,border:"1px solid var(--boris-border)",borderRadius:11,background:"#fff"}}>
                <Icon name="search" size={18}/>
                <input value={poolSearch} onChange={(e)=>setPoolSearch(e.target.value)} placeholder="Найти клиента по имени, телефону, компании, задаче или причине" style={{border:0,outline:0,width:"100%",fontSize:13.5,background:"transparent",color:"var(--boris-text-primary)"}}/>
              </div>

              {tab==="reactivation" && reactivationLoading ? <div style={{padding:"22px 0",color:"var(--boris-text-2)",fontSize:13}}>Загружаем кандидатов на реактивацию…</div> :
              !visibleClientPool.length ? <div style={{padding:"22px 0 6px"}}><EmptyState title={sectionMeta[tab].empty} hint="Когда у клиента появится действие в этом разделе, BORIS автоматически покажет его здесь."/></div> :
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(320px,1fr))",gap:10,marginTop:14}}>
                {visibleClientPool.map((item)=>{
                  const overdue=!!item.nextActionAt && new Date(item.nextActionAt)<new Date();
                  return <div key={item.key} onClick={()=>void openClientActions(item)} style={{border:"1px solid var(--boris-border)",borderRadius:14,padding:14,background:"#fff",cursor:"pointer",minWidth:0,display:"grid",gap:10,boxShadow:"0 1px 2px rgba(16,24,40,.03)"}}>
                    <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"flex-start"}}>
                      <div style={{minWidth:0}}>
                        <div style={{fontSize:10,fontWeight:900,letterSpacing:".05em",color:"var(--boris-blue)",textTransform:"uppercase"}}>{item.badge}</div>
                        <div style={{fontSize:16,fontWeight:850,marginTop:3,overflowWrap:"anywhere"}}>{item.name}</div>
                        <div style={{fontSize:12,color:"var(--boris-text-3)",marginTop:3,overflowWrap:"anywhere"}}>{item.company || item.phone || item.email || humanizeBusinessText(item.source,"Контакт уточняется")}</div>
                      </div>
                      <span style={{width:34,height:34,borderRadius:10,background:"var(--boris-blue-soft)",color:"var(--boris-blue)",display:"grid",placeItems:"center",flex:"0 0 auto"}}><Icon name="clients" size={18}/></span>
                    </div>
                    {item.reason ? <div style={{fontSize:12,color:"var(--boris-text-2)",lineHeight:1.4,padding:"8px 10px",borderRadius:9,background:"var(--boris-background)"}}>{humanizeBusinessText(item.reason,"Причина уточняется")}</div> : null}
                    <div style={{fontSize:12,lineHeight:1.4,color:overdue?"#b42318":"var(--boris-text-2)"}}>
                      <b>{overdue?"Просрочено":"Следующее действие"}:</b> {humanizeBusinessText(item.nextAction,"Открыть клиента и назначить следующий шаг")}{item.nextActionAt ? ` · ${new Date(item.nextActionAt).toLocaleString("ru-RU")}` : ""}
                    </div>
                    <div style={{display:"flex",gap:6,flexWrap:"wrap"}} onClick={(e)=>e.stopPropagation()}>
                      {item.contactId ? <Button style={buttonGhost} onClick={()=>router.push(`/dashboard/crm/contact/${item.contactId}`)}>Карточка</Button> : null}
                      {item.dealId ? <Button style={buttonGhost} onClick={()=>router.push(`/dashboard/crm/deal/${item.dealId}`)}>Сделка</Button> : null}
                      {item.chatId ? <Button style={buttonGhost} onClick={()=>router.push(`/messages?account_id=${encodeURIComponent(item.accountId || selectedAccountId || "")}&chat_id=${encodeURIComponent(item.chatId || "")}`)}>Диалог</Button> : null}
                      <Button style={buttonPrimary} onClick={()=>void openClientActions(item)}>Все действия</Button>
                    </div>
                  </div>;
                })}
              </div>}
            </BorisCard>
          </div>
        ) : null}

        {loading ? (
          <BorisCard className="boris-crm-card" style={card}>Загрузка продаж…</BorisCard>
        ) : null}

        {!loading && !error && tab === "today" ? (
          <div className="boris-crm-analytics-grid" style={{display:"grid",gridTemplateColumns:"1.15fr .85fr",gap:14}}>
            <BorisCard className="boris-crm-card boris-crm-work-card" style={card}>
              <div className="boris-crm-kicker">РАБОЧИЙ ДЕНЬ</div><h3 style={{margin:"3px 0 0"}}>Что сделать сейчас</h3>
              <div style={{fontSize:13,color:"var(--boris-text-2)",marginBottom:12}}>Сначала просроченные задачи, затем сегодняшние и ближайшие действия. BORIS сам напоминает ответственным менеджерам; владелец подключается только по отдельному внешнему или денежному блокеру.</div>
              {!tasks.filter(t=>t.status==="open").length ? <EmptyState title="Срочных задач нет" hint="Новые задачи и напоминания появятся здесь автоматически или после создания менеджером."/> : tasks.filter(t=>t.status==="open").slice(0,12).map(t=><div key={t.id} style={{padding:"11px 0",borderBottom:"1px solid var(--boris-border-soft)",display:"grid",gridTemplateColumns:"1fr auto",gap:10}}><div><b>{t.title}</b><div style={{fontSize:12,color:"var(--boris-text-3)",marginTop:3}}>{t.contact_name || t.deal_title || "Клиент"}{t.due_at ? ` · ${new Date(t.due_at).toLocaleString("ru-RU")}` : " · срок не задан"}</div>{crmTaskReminderLabel(t)?<div style={{fontSize:11,fontWeight:750,color:t.escalation_level==="manager_critical"?"#b42318":"var(--boris-text-2)",marginTop:4}}>{crmTaskReminderLabel(t)}</div>:null}</div><div style={{display:"flex",gap:6}}><Button style={buttonGhost} onClick={()=>t.deal_id?router.push(`/dashboard/crm/deal/${t.deal_id}`):router.push('/messages')}>Открыть</Button><Button style={buttonGhost} onClick={async()=>{await api(`/api/crm/product/tasks/${t.id}?account_id=${encodeURIComponent(selectedAccountId || "")}`,{method:"PATCH",body:JSON.stringify({status:"done"})});await load();}}>Готово</Button></div></div>)}
            </BorisCard>
            <BorisCard className="boris-crm-card boris-crm-work-card" style={card}>
              <div className="boris-crm-kicker">КОНТРОЛЬ</div><h3 style={{margin:"3px 0 0"}}>Контроль продаж</h3>
              <div style={{display:"grid",gap:10}}>
                <Metric label="Новых обращений без сделки" value={inquiries.length}/>
                <Metric label="Просрочено задач" value={overview?.overdue_tasks || 0}/>
                <Metric label="Сделок без движения 3+ дня" value={overview?.stale_deals || 0}/>
              </div>
              <div style={{marginTop:14,paddingTop:12,borderTop:"1px solid var(--boris-border-soft)"}}>
                <div style={{fontSize:12,fontWeight:800,marginBottom:6}}>Когда BORIS автоматически создаёт сделку</div>
                <select value={salesSettings?.deal_trigger || "after_engaged"} disabled={!salesSettings || savingDealTrigger} onChange={(e)=>void saveDealTrigger(e.target.value)} style={{width:"100%",minHeight:40,border:"1px solid var(--boris-border)",borderRadius:9,padding:"0 10px",background:"#fff"}}>
                  {Object.entries(dealTriggerLabels).map(([v,l])=><option key={v} value={v}>{l}</option>)}
                </select>
                <div style={{marginTop:7,fontSize:12,color:"var(--boris-text-2)",lineHeight:1.45}}>
                  Сейчас: <b>{dealTriggerLabels[salesSettings?.deal_trigger || "after_engaged"] || "по правилу аккаунта"}</b>. Первичный вопрос не теряется: клиент остаётся в «Новых клиентах», а после выполнения условия BORIS сам переводит его в «Сделки» вместе с контактом и историей.
                </div>
              </div>
            </BorisCard>
          </div>
        ) : null}

        {!loading && !error && tab === "today" && (analytics?.quality_deals || []).length ? (
          <BorisCard className="boris-crm-card boris-crm-work-card" style={{...card,marginTop:14}}>
            <div className="boris-crm-section-header">
              <div><div className="boris-crm-kicker">ТРЕБУЮТ ВНИМАНИЯ</div><h3 style={{margin:"3px 0 0"}}>Сделки, которые надо привести в порядок</h3></div>
              <span>{analytics?.quality_deals?.length || 0} проблемных сделок</span>
            </div>
            <div className="boris-crm-attention-grid">
              {(analytics?.quality_deals || []).slice(0,12).map(item=><button key={item.id} className="boris-crm-attention-card" onClick={()=>router.push(`/dashboard/crm/deal/${item.id}`)}>
                <span className="boris-crm-attention-main"><strong>{item.contact_name || item.title || `Сделка #${item.id}`}</strong><small>{item.stage_name || "Этап не задан"}</small><span>{(item.reasons || []).map((x:string)=>humanizeBusinessText(x,"требуется проверка")).join(" · ")}</span></span>
                <span className="boris-crm-section-arrow">›</span>
              </button>)}
            </div>
          </BorisCard>
        ) : null}

        {!loading && !error && tab === "inquiries" ? (
          <div className="boris-crm-inquiries-view">
            <div className="boris-crm-view-heading"><div><div className="boris-crm-kicker">НОВЫЕ ОБРАЩЕНИЯ</div><h2>Кого нужно довести до сделки</h2><p>Это не второй раздел сообщений. Здесь только входящие клиенты, по которым BORIS ещё не создал сделку.</p></div><div className="boris-crm-view-count">{inquiries.length} ждут решения</div></div>
            {!inquiries.length ? <BorisCard className="boris-crm-card" style={card}><EmptyState title="Все обращения разобраны" hint="Новые клиенты без сделки появятся здесь автоматически."/></BorisCard> : <div className="boris-crm-inquiry-grid">{inquiries.map(i=>{const mature=!!i.has_phone||Number(i.incoming_count||0)>=2;return <div className={`boris-crm-inquiry-tile ${mature?"boris-crm-inquiry-ready":""}`} key={i.id}><span className="boris-crm-inquiry-icon"><Icon name={mature?"target":"messages"} size={22}/></span><div className="boris-crm-inquiry-main"><small>{mature?"ГОТОВО К РАБОТЕ":"НОВОЕ ОБРАЩЕНИЕ"}</small><strong>{i.contact_name||i.title||"Клиент"}</strong><span>{i.item_title||"Avito"}</span><p>{i.has_phone?"Контакт получен — можно переводить в сделку.":Number(i.incoming_count||0)>=2?"Диалог развивается — BORIS ждёт условия автоматического создания сделки.":"Первичное обращение — нужно понять потребность клиента."}</p></div><div className="boris-crm-inquiry-meta"><span>{i.last_message_at?new Date(i.last_message_at).toLocaleDateString("ru-RU"):"Дата не указана"}</span><span>Входящих: {Number(i.incoming_count||0)}</span></div><div className="boris-crm-inquiry-actions"><Button style={buttonGhost} onClick={()=>router.push(`/messages?account_id=${encodeURIComponent(i.account_id)}&chat_id=${encodeURIComponent(i.external_chat_id)}`)}>Ответить клиенту</Button><Button style={buttonPrimary} onClick={async()=>{await api('/api/crm/bridge/promote-inquiry',{method:'POST',body:JSON.stringify({account_id:i.account_id,avito_chat_id:i.external_chat_id})});await load();openTab('kanban');}}>Создать сделку</Button></div></div>})}</div>}
          </div>
        ) : null}

        {!loading && !error && tab === "kanban" ? (
          <div className="boris-crm-kanban" style={{
            display: "flex",
            gap: 12,
            overflowX: "auto",
            paddingBottom: 20,
          }}>
            {!(kanban?.columns || []).length ? (
              <div style={{ minWidth: "100%" }}>
                <EmptyState title="Сделок пока нет" hint="Когда появятся реальные сделки, они появятся в воронке." />
              </div>
            ) : (kanban?.columns || []).map((column) => (
              <div
                key={column.id}
                className="boris-crm-kanban-column"
                onDragOver={(e)=>e.preventDefault()}
                onDrop={(e)=>{e.preventDefault(); const dealId=Number(e.dataTransfer.getData("text/boris-crm-deal")); if(dealId) void moveDealToStage(dealId,column);}}
                style={{
                  minWidth: 310,
                  width: 310,
                  alignSelf: "flex-start",
                }}
              >
                <div style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 10,
                  marginBottom: 8,
                  padding: "0 2px",
                }}>
                  <div>
                    <div style={{
                      fontSize: 13.5,
                      fontWeight: 700,
                    }}>
                      {column.name}
                    </div>

                    <div style={{
                      fontSize: 13.5,
                      color: "var(--boris-text-3)",
                      marginTop: 2,
                    }}>
                      {column.count} · {money(column.amount_kopeks)}
                    </div>
                  </div>
                </div>

                <div style={{
                  background: "var(--boris-border-soft)",
                  borderRadius: 14,
                  padding: 8,
                  minHeight: 150,
                }}>
                  {column.deals.map((deal) => (
                    <Button className="boris-crm-deal-row"
                      key={deal.id}
                      draggable
                      onDragStart={(e)=>{e.dataTransfer.setData("text/boris-crm-deal",String(deal.id));e.dataTransfer.effectAllowed="move";}}
                      onClick={() =>
                        router.push(`/dashboard/crm/deal/${deal.id}`)
                      }
                      style={{
                        display: "block",
                        width: "100%",
                        textAlign: "left",
                        border: "1px solid var(--boris-border)",
                        background: "#FFFFFF",
                        borderRadius: 12,
                        padding: 12,
                        marginBottom: 8,
                        cursor: "pointer",
                      }}
                    >
                      <div className="boris-crm-deal-head">
                        <span className="boris-crm-deal-icon"><Icon name="briefcase" size={19}/></span>
                        <span className="boris-crm-deal-title"><strong>{deal.title}</strong><small>{deal.contact_name || "Клиент"}</small></span>
                        <Icon name="arrow" size={16}/></div>

                      <div style={{display:"flex",gap:7,flexWrap:"wrap",marginTop:7,fontSize:11,color:"var(--boris-text-3)"}}>
                        <span>{deal.responsible_email ? `Ответственный: ${deal.responsible_email}` : deal.responsible_user_id ? `Ответственный #${deal.responsible_user_id}` : "Ответственный не назначен"}</span>
                        {deal.contact_phone ? <span>· {deal.contact_phone}</span> : null}
                      </div>

                      <div style={{
                        display: "flex",
                        justifyContent: "space-between",
                        marginTop: 10,
                        gap: 8,
                      }}>
                        <span style={{
                          fontSize: 13.5,
                          fontWeight: 700,
                          color: "var(--boris-text-2)",
                        }}>
                          {money(deal.amount_kopeks)}
                        </span>

                        <span style={{
                          fontSize: 13.5,
                          color: "var(--boris-text-3)",
                        }}>
                          {deal.source || "—"}
                        </span>
                      </div>
                      <div className={deal.next_action_at?"boris-crm-next-action":"boris-crm-next-action boris-crm-next-action-recommended"} style={{marginTop:8,fontSize:12,fontWeight:650,color:deal.next_action_at && new Date(deal.next_action_at)<new Date()?"#b42318":"var(--boris-text-2)"}}>{deal.next_action_at ? `${new Date(deal.next_action_at)<new Date()?"Просрочено":new Date(deal.next_action_at).toDateString()===new Date().toDateString()?"Сегодня":"Следующее действие"}: ${new Date(deal.next_action_at).toLocaleString("ru-RU")}` : `BORIS рекомендует: ${stageRecommendation(column.name,Boolean(deal.contact_phone))}`}</div>
                    </Button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {!loading && !error && tab === "contacts" ? (
          <div className="boris-crm-contacts-view">
            <div className="boris-crm-view-heading"><div><div className="boris-crm-kicker">КЛИЕНТЫ</div><h2>Кому продаём и что делать дальше</h2><p>Каждая карточка — конкретный клиент: контакт, активные сделки и быстрый вход в работу.</p></div><div className="boris-crm-view-count">{filteredContacts.length} клиентов</div></div>
            <div className="boris-crm-search-wrap"><Icon name="search" size={18}/><input className="boris-crm-search" value={search} onChange={(e)=>setSearch(e.target.value)} placeholder="Найти по имени, телефону, email или компании" /></div>
            {!filteredContacts.length ? <BorisCard className="boris-crm-card" style={card}><EmptyState title="Клиентов пока нет" hint="Контакты появятся после синхронизации реальных диалогов и сделок." /></BorisCard> :
            <div className="boris-crm-contact-grid">{filteredContacts.map(contact=>{const visual=contactVisual(contact);return <button key={contact.id} className={`boris-crm-contact-tile boris-crm-contact-${visual.tone}`} onClick={()=>router.push(`/dashboard/crm/contact/${contact.id}`)}>
              <span className="boris-crm-contact-avatar">{visual.glyph?<span className="boris-crm-person-glyph" aria-hidden="true">{visual.glyph}</span>:<Icon name={visual.icon} size={25}/>}</span>
              <span className="boris-crm-contact-main"><small>{visual.kind}</small><strong>{contact.display_name||contact.company_name||"Без имени"}</strong>{contact.company_name&&contact.company_name!==contact.display_name?<em>{contact.company_name}</em>:null}</span>
              <span className="boris-crm-contact-facts"><span><Icon name="messages" size={15}/>{contact.primary_phone||contact.primary_email||"Контакт не указан"}</span><span><Icon name="briefcase" size={15}/>{contact.open_deals ? `${contact.open_deals} ${contact.open_deals===1?"сделка в работе":"сделки в работе"}` : "Нет активной сделки"}</span><span className={contact.open_deals&&!contact.next_action_title?"boris-crm-contact-next-missing":""}><Icon name="clock" size={15}/>{contact.next_action_title ? `Следующий шаг: ${contact.next_action_title}${contact.next_action_at?` · ${new Date(contact.next_action_at).toLocaleDateString("ru-RU")}`:""}` : contact.open_deals ? "Следующий шаг не назначен" : "Действий по клиенту пока нет"}</span></span>
              <span className="boris-crm-contact-action">Открыть клиента <Icon name="arrow" size={16}/></span>
            </button>})}</div>}
          </div>
        ) : null}

        {!loading && !error && tab === "tasks" ? (
          <div style={{display:"grid",gap:14}}>
            <BorisCard className="boris-crm-card boris-crm-work-card" style={card}>
              <div className="boris-crm-section-header"><div><div className="boris-crm-kicker">ЗАДАЧИ ПО КЛИЕНТАМ</div><h3 style={{margin:"3px 0 0"}}>Что нужно сделать по клиентам</h3><p style={{margin:"5px 0 0",fontSize:12,color:"var(--boris-text-3)"}}>BORIS контролирует просрочки и повторно напоминает менеджерам автоматически. Обычная просрочка не становится задачей владельца.</p></div><span>{tasks.filter(t=>t.status==="open").length} открытых · {tasks.filter(t=>t.status==="done").length} выполненных</span></div>
              {!tasks.length ? <EmptyState title="Задач пока нет" hint="Создавайте следующий шаг в карточке сделки или клиента — он появится здесь."/> : (
                <div className="boris-crm-task-list">
                  {tasks.slice().sort((a,b)=>{
                    const ao=a.status==="open"?0:1, bo=b.status==="open"?0:1; if(ao!==bo)return ao-bo;
                    const ad=a.due_at?new Date(a.due_at).getTime():Number.MAX_SAFE_INTEGER, bd=b.due_at?new Date(b.due_at).getTime():Number.MAX_SAFE_INTEGER; return ad-bd;
                  }).map(t=>{
                    const overdue=t.status==="open"&&!!t.due_at&&new Date(t.due_at)<new Date();
                    return <div key={t.id} className={`boris-crm-task-card ${overdue?"boris-crm-task-card-overdue":""}`}>
                      <div style={{minWidth:0}}><strong>{t.title}</strong><div style={{fontSize:12,color:"var(--boris-text-3)",marginTop:4}}>{t.contact_name||t.deal_title||"Клиент"}{t.due_at?` · ${overdue?"просрочено ":""}${new Date(t.due_at).toLocaleString("ru-RU")}`:" · срок не задан"}</div><div style={{fontSize:11,color:"var(--boris-text-3)",marginTop:3}}>{t.status==="done"?"Выполнено":t.status==="cancelled"?"Отменено":"В работе"}</div>{t.status==="open"&&crmTaskReminderLabel(t)?<div style={{fontSize:11,fontWeight:800,color:t.escalation_level==="manager_critical"?"#b42318":"var(--boris-text-2)",marginTop:4}}>{crmTaskReminderLabel(t)}</div>:null}</div>
                      <div style={{display:"flex",gap:6,flexWrap:"wrap",justifyContent:"flex-end"}}>{t.deal_id?<Button style={buttonGhost} onClick={()=>router.push(`/dashboard/crm/deal/${t.deal_id}`)}>Открыть сделку</Button>:t.contact_id?<Button style={buttonGhost} onClick={()=>router.push(`/dashboard/crm/contact/${t.contact_id}`)}>Открыть клиента</Button>:null}{t.status==="open"?<Button style={buttonPrimary} onClick={async()=>{await api(`/api/crm/product/tasks/${t.id}?account_id=${encodeURIComponent(selectedAccountId||"")}`,{method:"PATCH",body:JSON.stringify({status:"done"})});await load();}}>Готово</Button>:null}</div>
                    </div>;
                  })}
                </div>
              )}
            </BorisCard>
          </div>
        ) : null}

        {!loading && !error && tab === "calendar" ? (
          <div style={{display:"grid",gap:14}}>
            <BorisCard className="boris-crm-card boris-crm-work-card" style={card}>
              <div className="boris-crm-section-header"><div><div className="boris-crm-kicker">КАЛЕНДАРЬ ПРОДАЖ</div><h3 style={{margin:"3px 0 0"}}>Ближайшие действия</h3></div><span>Те же задачи по клиентам, разложенные по срокам</span></div>
              {(() => {
                const now=new Date();
                const startToday=new Date(now.getFullYear(),now.getMonth(),now.getDate());
                const startTomorrow=new Date(startToday); startTomorrow.setDate(startTomorrow.getDate()+1);
                const startAfterTomorrow=new Date(startToday); startAfterTomorrow.setDate(startAfterTomorrow.getDate()+2);
                const weekEnd=new Date(startToday); weekEnd.setDate(weekEnd.getDate()+8);
                const open=tasks.filter(t=>t.status==="open");
                const groups=[
                  {key:"overdue",title:"Просрочено",items:open.filter(t=>t.due_at&&new Date(t.due_at)<now)},
                  {key:"today",title:"Сегодня",items:open.filter(t=>{if(!t.due_at)return false;const d=new Date(t.due_at);return d>=now&&d<startTomorrow;})},
                  {key:"tomorrow",title:"Завтра",items:open.filter(t=>{if(!t.due_at)return false;const d=new Date(t.due_at);return d>=startTomorrow&&d<startAfterTomorrow;})},
                  {key:"week",title:"Ближайшие 7 дней",items:open.filter(t=>{if(!t.due_at)return false;const d=new Date(t.due_at);return d>=startAfterTomorrow&&d<weekEnd;})},
                  {key:"later",title:"Позже",items:open.filter(t=>t.due_at&&new Date(t.due_at)>=weekEnd)},
                  {key:"nodue",title:"Без срока",items:open.filter(t=>!t.due_at)},
                ];
                return <div className="boris-crm-calendar-grid">{groups.map(g=><div key={g.key} className={`boris-crm-calendar-column boris-crm-calendar-${g.key}`}><div className="boris-crm-calendar-title"><strong>{g.title}</strong><span>{g.items.length}</span></div>{!g.items.length?<div className="boris-crm-calendar-empty">Нет действий</div>:g.items.slice().sort((a,b)=>(a.due_at?new Date(a.due_at).getTime():0)-(b.due_at?new Date(b.due_at).getTime():0)).map(t=><button key={t.id} className="boris-crm-calendar-task" onClick={()=>t.deal_id?router.push(`/dashboard/crm/deal/${t.deal_id}`):t.contact_id?router.push(`/dashboard/crm/contact/${t.contact_id}`):undefined}><strong>{t.title}</strong><small>{t.contact_name||t.deal_title||"Клиент"}</small>{t.due_at?<small>{new Date(t.due_at).toLocaleString("ru-RU")}</small>:null}{crmTaskReminderLabel(t)?<small style={{fontWeight:800}}>{crmTaskReminderLabel(t)}</small>:null}</button>)}</div>)}</div>;
              })()}
            </BorisCard>
          </div>
        ) : null}

        {!loading && !error && tab === "analytics" && analytics ? (
          <div className="boris-crm-analytics-modern">
            <div className="boris-crm-view-heading"><div><div className="boris-crm-kicker">АНАЛИТИКА ПРОДАЖ</div><h2>Где теряются продажи и что исправить</h2><p>Только показатели, которые помогают руководителю принять действие по воронке, качеству ведения клиентов и команде.</p></div></div>
            <div className="boris-crm-analytics-hero-grid">
              {[
                ["messages","Обращения",analytics.funnel?.inquiries??0,"Входящий спрос","blue"],
                ["handshake","Создано сделок",analytics.funnel?.deals??0,analytics.funnel?.inquiry_to_deal_pct==null?"Конверсия: недостаточно данных":`Конверсия: ${analytics.funnel.inquiry_to_deal_pct}%`,"violet"],
                ["award","Выиграно",analytics.funnel?.won??0,analytics.funnel?.deal_to_won_pct==null?"Результат: недостаточно данных":`Из сделок: ${analytics.funnel.deal_to_won_pct}%`,"emerald"],
                ["alert","Потеряно",analytics.funnel?.lost??0,"Причины — ниже","rose"],
              ].map(([icon,title,value,hint,tone])=><div key={String(title)} className={`boris-crm-insight-card boris-crm-insight-${tone}`}><span className="boris-crm-insight-icon"><Icon name={icon as IconName} size={22}/></span><div><small>{title}</small><strong>{value}</strong><p>{hint}</p></div></div>)}
            </div>
            <div className="boris-crm-call-summary">
              <div className="boris-crm-panel-title"><span><Icon name="support" size={19}/></span><div><strong>Звонки с Avito</strong><small>Что произошло после входящего звонка</small></div></div>
              <div className="boris-crm-quality-grid">
                <div><Icon name="support" size={18}/><span>Всего звонков</span><b>{analytics.calls?.total??0}</b></div>
                <div><Icon name="check" size={18}/><span>Отвечено</span><b>{analytics.calls?.answered??0}</b></div>
                <div><Icon name="alert" size={18}/><span>Пропущено</span><b>{analytics.calls?.missed??0}</b></div>
                <div><Icon name="handshake" size={18}/><span>Связаны со сделкой</span><b>{analytics.calls?.linked_to_deal??0}</b></div>
                <div><Icon name="ai" size={18}/><span>Есть разбор РОП</span><b>{analytics.calls?.analyzed??0}</b></div>
                <div><Icon name="clock" size={18}/><span>Просрочен перезвон</span><b>{analytics.calls?.callback_overdue??0}</b></div>
              </div>
            </div>
            <div className="boris-crm-analytics-grid-modern">
              <section className="boris-crm-analytics-panel"><div className="boris-crm-panel-title"><span><Icon name="list" size={19}/></span><div><strong>Сделки по этапам</strong><small>Где сейчас находится выручка</small></div></div><div className="boris-crm-stage-list">{analytics.stages.map(stage=><div key={stage.id}><span>{stage.name}</span><b>{stage.deals}</b><em>{money(stage.amount_kopeks)}</em></div>)}</div></section>
              <section className="boris-crm-analytics-panel"><div className="boris-crm-panel-title"><span><Icon name="shield" size={19}/></span><div><strong>Качество ведения клиентов</strong><small>Что мешает довести сделки до результата</small></div></div><div className="boris-crm-quality-grid">{[["clock","Без следующего шага",analytics.quality?.no_next_action??0],["user","Без ответственного",analytics.quality?.no_responsible??0],["coins","Без суммы",analytics.quality?.no_amount??0],["messages","Без телефона",analytics.quality?.no_phone??0]].map(([i,l,v])=><div key={String(l)}><Icon name={i as IconName} size={18}/><span>{l}</span><b>{v}</b></div>)}</div></section>
              <section className="boris-crm-analytics-panel"><div className="boris-crm-panel-title"><span><Icon name="target" size={19}/></span><div><strong>Потенциал воронки</strong><small>Сколько денег подтверждено данными</small></div></div><div className="boris-crm-pipeline-value"><strong>{analytics.pipeline_value?.deals_with_amount?money(analytics.pipeline_value.open_amount_kopeks):"Недостаточно данных"}</strong><span>{analytics.pipeline_value?.deals_with_amount??0} из {analytics.pipeline_value?.open_deals??0} сделок имеют сумму</span><p>{humanizeBusinessText(analytics.pipeline_value?.forecast_reason,"Вероятностный прогноз пока недоступен.")}</p></div></section>
              <section className="boris-crm-analytics-panel"><div className="boris-crm-panel-title"><span><Icon name="clients" size={19}/></span><div><strong>Ответственные</strong><small>Контроль загрузки и просрочек менеджеров</small></div></div>{!(analytics.managers||[]).length?<div className="boris-crm-empty-inline">Недостаточно данных.</div>:(analytics.managers||[]).map(m=><div className="boris-crm-manager-row" key={String(m.responsible_user_id??'none')}><span className="boris-crm-manager-avatar"><Icon name="user" size={17}/></span><div><strong>{m.responsible_name}</strong><small>{m.open_deals} в работе · {m.won} выиграно · {m.overdue_tasks} просрочено · {m.no_next_action} без шага</small></div></div>)}</section>
              <section className="boris-crm-analytics-panel"><div className="boris-crm-panel-title"><span><Icon name="alert" size={19}/></span><div><strong>Причины потерь</strong><small>Почему сделки не дошли до оплаты</small></div></div>{!(analytics.lost_reasons||[]).length?<div className="boris-crm-empty-inline">Потерянных сделок за период нет.</div>:(analytics.lost_reasons||[]).map(x=><div className="boris-crm-simple-row" key={x.reason}><span>{humanizeBusinessText(x.reason,"Причина не указана")}</span><b>{x.deals}</b></div>)}</section>
              <section className="boris-crm-analytics-panel"><div className="boris-crm-panel-title"><span><Icon name="globe" size={19}/></span><div><strong>Источники</strong><small>Откуда приходят сделки и деньги</small></div></div>{analytics.sources.map(source=><div className="boris-crm-simple-row" key={source.source}><span>{humanizeBusinessText(source.source,"Источник")}</span><b>{source.deals} сделок · {source.won} выиграно · {money(source.won_amount_kopeks)}</b></div>)}</section>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

const card: React.CSSProperties = {
  background: "#FFFFFF",
  border: "1px solid var(--boris-border)",
  borderRadius: 14,
  padding: 16,
};

const buttonGhost: React.CSSProperties = {
  border: "1px solid var(--boris-border)",
  background: "#FFFFFF",
  borderRadius: 10,
  padding: "9px 13px",
  cursor: "pointer",
};

const buttonPrimary: React.CSSProperties = {
  border: 0,
  background: "var(--boris-blue)",
  color: "#FFFFFF",
  borderRadius: 10,
  padding: "9px 14px",
  cursor: "pointer",
  fontWeight: 700,
};

function Tab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      className={active ? "boris-crm-tab boris-crm-tab-active" : "boris-crm-tab"}
      onClick={onClick}
      style={{
        border: "1px solid var(--boris-border)",
        background: active ? "var(--boris-blue-soft)" : "#FFFFFF",
        color: active ? "var(--boris-blue)" : "var(--boris-text-2)",
        borderRadius: 9,
        padding: "8px 12px",
        fontWeight: 700,
        cursor: "pointer",
      }}
    >
      {children}
    </Button>
  );
}

function Metric({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="boris-crm-metric" style={{
      background: "#FFFFFF",
      border: "1px solid var(--boris-border)",
      borderRadius: 12,
      padding: 13,
    }}>
      <div style={{
        fontSize: 13.5,
        color: "var(--boris-text-3)",
      }}>
        {label}
      </div>

      <div style={{
        marginTop: 5,
        fontSize: 19,
        fontWeight: 800,
      }}>
        {value}
      </div>
    </div>
  );
}
