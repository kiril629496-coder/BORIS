"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import AppShell from "@/app/components/shell/AppShell";
import { useAccounts } from "@/app/lib/AccountContext";
import { Card, Icon, type IconName } from "@/app/ui";
import styles from "./page.module.css";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

type Client = {
  account_id: string;
  name: string;
  company?: string;
  niche?: string;
  status?: string;
};

type LaunchStep = {
  key: string;
  title: string;
  status: string;
  reason?: string;
};

type LaunchPlan = {
  account_id?: string;
  ready_count?: number;
  blocked_count?: number;
  steps?: LaunchStep[];
  write_gate?: { required?: boolean; confirmed?: boolean };
};

type LaunchHealth = {
  launch_status?: string;
  launch_ready?: boolean;
  readiness_pct?: number;
  blockers?: Array<{ code?: string; title?: string; action?: string; human_required?: boolean }>;
  health?: { knowledge?: { count?: number | null } };
};

type SectionLink = {
  key: string;
  title: string;
  description: string;
  href: string;
  icon: IconName;
  group: "work" | "growth" | "system";
};

const SECTIONS: SectionLink[] = [
  {
    key: "accounts",
    title: "Подключить аккаунты Авито",
    description: "Ключ приложения, секретный ключ и понятный статус подключения Авито.",
    href: "/dashboard/connect-avito",
    icon: "clients",
    group: "work",
  },
  {
    key: "ads",
    title: "Объявления",
    description: "Каталог, медиа, тексты, публикация и интеграции рекламы.",
    href: "/dashboard/campaign",
    icon: "list",
    group: "work",
  },
  {
    key: "messages",
    title: "Сообщения",
    description: "Диалоги, МОП и реактивация клиентов в одном рабочем центре.",
    href: "/messages",
    icon: "messages",
    group: "work",
  },
  {
    key: "sales",
    title: "Продажи",
    description: "Клиенты, сделки, задачи, календарь, РОП и реактивация.",
    href: "/dashboard/crm",
    icon: "analytics",
    group: "growth",
  },
  {
    key: "marketing",
    title: "Маркетинг",
    description: "Цели, спрос и работа ИИ-маркетолога.",
    href: "/dashboard/marketing",
    icon: "ads",
    group: "growth",
  },
  {
    key: "analytics",
    title: "Аналитика",
    description: "Реклама, обращения, продажи и фактический результат.",
    href: "/dashboard/analytics",
    icon: "chart",
    group: "system",
  },
];

function authHeaders(extra: Record<string, string> = {}) {
  const token =
    typeof window === "undefined"
      ? ""
      : localStorage.getItem("boris_token") || "";

  if (!token) {
    throw new Error("AUTH_REQUIRED");
  }

  return {
    ...extra,
    Authorization: `Bearer ${token}`,
  };
}

function normalizeClient(value: any): Client | null {
  const accountId = value?.account_id ?? value?.accountId ?? value?.id;
  if (accountId === undefined || accountId === null) return null;

  return {
    account_id: String(accountId),
    name:
      value?.name ??
      value?.company_name ??
      value?.company ??
      String(accountId),
    company: value?.company ?? value?.company_name,
    niche: value?.company_niche ?? value?.niche,
    status: value?.status ?? value?.state,
  };
}

function extractClients(payload: any): Client[] {
  const candidates = [
    payload,
    payload?.clients,
    payload?.accounts,
    payload?.items,
    payload?.launches,
    payload?.data,
    payload?.data?.clients,
    payload?.data?.accounts,
    payload?.data?.items,
    payload?.data?.launches,
  ];

  for (const candidate of candidates) {
    if (!Array.isArray(candidate)) continue;
    const result = candidate
      .map(normalizeClient)
      .filter((item): item is Client => Boolean(item));
    if (result.length) return result;
  }

  return [];
}

function normalizedStatus(status?: string) {
  return String(status || "WAIT").toUpperCase();
}

function statusLabel(status?: string) {
  const value = normalizedStatus(status);
  if (value === "READY" || value === "DONE" || value === "PASS") return "Готово";
  if (value === "BLOCKED" || value === "FAIL" || value === "FAILED") return "Требует внимания";
  return "Ожидает";
}

function stepSummary(step: LaunchStep) {
  return step.reason || statusLabel(step.status);
}

const BLOCKER_LABELS: Record<string, string> = {
  NO_PRODUCTS: "Не подключён продукт BORIS",
  CORE_BLOCKED: "Не выполнены обязательные условия запуска",
  NO_OWNER: "Не назначен владелец аккаунта",
  FACTORY_STATE_MISSING: "Состояние аккаунта ещё не собрано",
  SCHEDULE_CONFIRMATION_REQUIRED: "Нужно подтвердить автоматизацию",
  APPROVED_SCHEDULE_NOT_APPLIED: "Подтверждённая автоматизация ещё не применена",
  SCHEDULE_MISSING: "Не подтверждено рабочее расписание автоматизации",
};

function ownerText(value: unknown, fallback = "Нет данных") {
  return humanizeBusinessText(value, fallback);
}

export default function CommandCenterPage() {
  const { accounts: accountContextClients, selectedAccountId, setSelectedAccount } = useAccounts();
  const [clients, setClients] = useState<Client[]>([]);
  const [selectedClient, setSelectedClient] = useState("");
  const [plan, setPlan] = useState<LaunchPlan | null>(null);
  const [launchHealth, setLaunchHealth] = useState<LaunchHealth | null>(null);
  const [billing, setBilling] = useState<any>(null);
  const [memorySummary, setMemorySummary] = useState<any>(null);
  const [accountCapabilities, setAccountCapabilities] = useState<any>(null);
  const [loadingClients, setLoadingClients] = useState(true);
  const [loadingPlan, setLoadingPlan] = useState(false);
  const [error, setError] = useState("");
  const [kpi, setKpi] = useState<any>(null);
  const [aiMarketing, setAiMarketing] = useState<any>(null);
  const [aiActions, setAiActions] = useState<any[]>([]);
  const [aiWorkspace, setAiWorkspace] = useState<any>(null);
  const [kpiError, setKpiError] = useState("");
  const [loadingKpi, setLoadingKpi] = useState(false);
  const [search, setSearch] = useState("");
  const [activeScenario, setActiveScenario] = useState<any>(null);
  const [ownerInbox, setOwnerInbox] = useState<any[]>([]);
  const [mopRuntime, setMopRuntime] = useState<any>(null);
  const [billingState, setBillingState] = useState<any>(null);
  const [setupGuideOpen, setSetupGuideOpen] = useState(false);
  // CLIENT_SUPERVISOR_CONTROL_CENTER_UX_V1: one read-only truth bridge from the
  // autonomous client supervisor into the existing client Command Center.
  // client-human-work-v1 keeps manager work separate from BORIS/system faults.
  // client-operational-facts exposes messages/CRM/publication/reactivation facts.
  const [clientSupervisor, setClientSupervisor] = useState<any>(null);
  const [briefTick, setBriefTick] = useState(() => Math.floor(Date.now() / 7200000));

  const loadClients = useCallback(async () => {
    setLoadingClients(true);
    setError("");

    try {
      const response = await fetch("/api/factory/launches", {
        headers: authHeaders(),
        cache: "no-store",
      });

      if (response.status === 401) throw new Error("AUTH_REQUIRED");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const next = extractClients(await response.json());
      setClients(next);
    } catch (currentError) {
      setClients([]);
      setPlan(null);
      setError(
        currentError instanceof Error && currentError.message === "AUTH_REQUIRED"
          ? "Нужна авторизация BORIS"
          : "Не удалось получить фактическое состояние клиентов",
      );
    } finally {
      setLoadingClients(false);
    }
  }, []);

  const loadPlan = useCallback(async (accountId: string) => {
    if (!accountId) {
      setPlan(null);
      return;
    }

    setLoadingPlan(true);
    setError("");

    try {
      const response = await fetch("/api/client-launch/plan", {
        method: "POST",
        cache: "no-store",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ account_id: accountId }),
      });

      if (response.status === 401) throw new Error("AUTH_REQUIRED");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setPlan(await response.json());
    } catch (currentError) {
      setPlan(null);
      setError(
        currentError instanceof Error && currentError.message === "AUTH_REQUIRED"
          ? "Нужна авторизация BORIS"
          : "Не удалось получить фактический план запуска выбранного клиента",
      );
    } finally {
      setLoadingPlan(false);
    }
  }, []);

  const loadClientSupervisor = useCallback(async (accountId: string) => {
    if (!accountId) { setClientSupervisor(null); return; }
    try {
      const response = await fetch(`/api/control-plane/workspace/client-supervisor?account_id=${encodeURIComponent(accountId)}`, { headers: authHeaders(), cache: "no-store" });
      setClientSupervisor(response.ok ? await response.json() : null);
    } catch { setClientSupervisor(null); }
  }, []);

  useEffect(() => {
    void loadClients();
  }, [loadClients]);

  useEffect(() => {
    if (selectedClient) void loadClientSupervisor(selectedClient);
    else setClientSupervisor(null);
  }, [selectedClient, loadClientSupervisor]);

  useEffect(() => {
    if (selectedAccountId) {
      setSelectedClient(selectedAccountId);
    }
  }, [selectedAccountId]);

  useEffect(() => {
    // REAL_STATUS_ONLY_V2: legacy client-launch/plan is a capability contract,
    // not account evidence. It must not drive customer-facing readiness.
    setPlan(null);
    setLoadingPlan(false);
  }, [selectedClient]);

  useEffect(() => {
    setLaunchHealth(null); setBilling(null); setMemorySummary(null); setAccountCapabilities(null);
    if (!selectedClient) return;
    let alive = true;
    Promise.all([
      fetch(`/api/factory/launches/${encodeURIComponent(selectedClient)}`, {headers: authHeaders(), cache:"no-store"}).then(r => r.ok ? r.json() : null),
      fetch(`/api/billing/status?account_id=${encodeURIComponent(selectedClient)}`, {headers: authHeaders(), cache:"no-store"}).then(r => r.ok ? r.json() : null),
      fetch(`/api/memory/summary?account_id=${encodeURIComponent(selectedClient)}`, {headers: authHeaders(), cache:"no-store"}).then(r => r.ok ? r.json() : null),
      fetch(`/api/account-capabilities?account_id=${encodeURIComponent(selectedClient)}`, {headers: authHeaders(), cache:"no-store"}).then(r => r.ok ? r.json() : null),
    ]).then(([launch, bill, memory, caps]) => {
      if (!alive) return;
      setLaunchHealth(launch?.item || null);
      setBilling(bill?.billing || null);
      setMemorySummary(memory || null);
      setAccountCapabilities(caps || null);
    }).catch(() => { if (alive) { setLaunchHealth(null); setBilling(null); setMemorySummary(null); setAccountCapabilities(null); } });
    return () => { alive = false; };
  }, [selectedClient]);

  useEffect(() => {
    if (!selectedClient) {
      setKpi(null);
      setKpiError("");
      return;
    }
    let alive = true;
    setLoadingKpi(true);
    setKpiError("");
    fetch(`/api/avito/kpi_check?account_id=${encodeURIComponent(selectedClient)}`, {
      headers: authHeaders(),
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => { if (alive) setKpi(data); })
      .catch(() => { if (alive) { setKpi(null); setKpiError("Показатели сейчас недоступны"); } })
      .finally(() => { if (alive) setLoadingKpi(false); });
    return () => { alive = false; };
  }, [selectedClient]);

  useEffect(() => {
    if (!selectedClient) { setActiveScenario(null); setOwnerInbox([]); return; }
    let alive = true;
    fetch(`/api/home/overview?account_id=${encodeURIComponent(selectedClient)}`, {headers: authHeaders(), cache:"no-store"})
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (alive) { setActiveScenario(d?.active_scenario || null); setOwnerInbox(d?.owner_inbox || []); } })
      .catch(() => { if (alive) { setActiveScenario(null); setOwnerInbox([]); } });
    return () => { alive = false; };
  }, [selectedClient]);

  useEffect(() => {
    setMopRuntime(null);
    if (!selectedClient) return;
    let alive = true;
    fetch("/api/messenger/sales/settings?account_id=" + encodeURIComponent(selectedClient), {headers: authHeaders(), cache:"no-store"})
      .then(r => r.ok ? r.json() : null).then(d => { if (alive) setMopRuntime(d); }).catch(() => { if (alive) setMopRuntime(null); });
    return () => { alive = false; };
  }, [selectedClient]);

  useEffect(() => {
    const refreshBriefTick = () => setBriefTick(Math.floor(Date.now() / 7200000));
    const timer = window.setInterval(refreshBriefTick, 60000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    setAiMarketing(null); setAiActions([]); setAiWorkspace(null); setBillingState(null);
    if (!selectedClient) return;
    let alive = true;
    Promise.all([
      fetch(`/api/avito/ai_marketing_readiness?account_id=${encodeURIComponent(selectedClient)}`, {headers: authHeaders(), cache:"no-store"}).then(r => r.ok ? r.json() : null),
      fetch(`/api/avito/kpi_action_memory?account_id=${encodeURIComponent(selectedClient)}&limit=8`, {headers: authHeaders(), cache:"no-store"}).then(r => r.ok ? r.json() : null),
      fetch(`/api/avito/ai_marketing_daily_workspace?account_id=${encodeURIComponent(selectedClient)}`, {headers: authHeaders(), cache:"no-store"}).then(r => r.ok ? r.json() : null),
      fetch(`/api/billing/status?account_id=${encodeURIComponent(selectedClient)}`, {headers: authHeaders(), cache:"no-store"}).then(r => r.ok ? r.json() : null),
    ]).then(([ready, actions, workspace, billing]) => {
      if (!alive) return;
      setAiMarketing(ready); setAiActions(Array.isArray(actions?.items) ? actions.items : []); setAiWorkspace(workspace); setBillingState(billing?.billing || null);
    }).catch(() => { if (alive) { setAiMarketing(null); setAiActions([]); setAiWorkspace(null); setBillingState(null); } });
    return () => { alive = false; };
  }, [selectedClient, briefTick]);

  const availableClients = useMemo(() => {
    // SECURITY/PRODUCT SCOPE: the global account context is the authoritative
    // list of accounts available to the signed-in owner. Factory launches may
    // contain internal QA/test rows and must only enrich matching real accounts,
    // never expand the client-facing account list on their own.
    const launchById = new Map<string, Client>();
    for (const client of clients) launchById.set(client.account_id, client);

    const ownerAccounts = accountContextClients
      .filter((account) => !/^qa_/i.test(String(account.account_id || "")))
      .map((account) => {
        const current = launchById.get(account.account_id);
        return {
          account_id: account.account_id,
          name: current?.name || account.name || account.account_id,
          company: current?.company,
          niche: current?.niche,
          status: current?.status,
        } as Client;
      });

    return ownerAccounts;
  }, [clients, accountContextClients]);

  const filteredClients = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return availableClients;

    return availableClients.filter((client) =>
      [client.account_id, client.name, client.company, client.niche]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query)),
    );
  }, [availableClients, search]);

  const selected = availableClients.find((item) => item.account_id === selectedClient) || null;
  const avitoConnected = Boolean(aiMarketing?.avito_connected);
  const paidTier = String(billing?.tier || "none");
  const productActive = ["trial", "tariff_1", "tariff_2"].includes(paidTier) && !billing?.blocked_reason;
  const catalogCount = Number(aiMarketing?.campaign_items || 0);
  const knowledgeCount = Number(memorySummary?.facts || 0);
  const kpiConfigured = Boolean(aiMarketing?.kpi_configured);
  const aiMarketerReady = Boolean(aiMarketing?.ai_marketing_ready);
  const mopEntitled = Boolean(accountCapabilities?.mop?.entitled);
  const launchBlockers = Array.isArray(launchHealth?.blockers) ? launchHealth!.blockers! : [];
  const realSteps: LaunchStep[] = selected ? [
    { key:"account", title:"Аккаунт", status:"READY", reason:"Аккаунт существует и доступен владельцу" },
    { key:"product", title:"Тариф BORIS", status: productActive ? "READY" : "BLOCKED", reason: productActive ? (billing?.tier_name || "Тариф активен") : "Нет активного тарифа BORIS" },
    { key:"avito", title:"Подключение Авито", status: avitoConnected ? "READY" : "BLOCKED", reason: avitoConnected ? "API Авито подключён" : "Ключ приложения и секретный ключ не подключены" },
    { key:"knowledge", title:"Данные о бизнесе", status: knowledgeCount > 0 ? "READY" : "BLOCKED", reason: knowledgeCount > 0 ? `${knowledgeCount} фактов в базе знаний` : "Нет подтверждённых данных о бизнесе" },
    { key:"catalog", title:"Объявления / каталог", status: catalogCount > 0 ? "READY" : "BLOCKED", reason: catalogCount > 0 ? `${catalogCount} рабочих позиций` : "Рабочие позиции ещё не созданы" },
    { key:"kpi", title:"План и KPI", status: kpiConfigured ? "READY" : "BLOCKED", reason: kpiConfigured ? "Дневная цель и цена лида заданы" : "KPI ещё не настроен" },
    { key:"marketing", title:"ИИ-маркетолог", status: aiMarketerReady ? "READY" : "BLOCKED", reason: aiMarketerReady ? "Готов к автономной KPI-работе" : (aiMarketing?.human_blockers?.[0] || "Не все условия для автономной работы выполнены") },
    { key:"mop", title:"ИИ-менеджер", status: mopEntitled ? "READY" : "BLOCKED", reason: mopEntitled ? "Пакет ИИ-менеджера активен" : "Пакет ИИ-менеджера не подключён" },
  ] : [];
  const steps = realSteps;
  const stepDestinations: Record<string, { href: string; action: string }> = {
    account: { href: "/dashboard/company", action: "Открыть данные аккаунта" },
    product: { href: "/dashboard/billing", action: "Открыть тарифы" },
    avito: { href: "/dashboard/connect-avito", action: "Подключить Avito" },
    knowledge: { href: "/memory", action: "Заполнить данные" },
    catalog: { href: "/dashboard/campaign", action: "Открыть объявления" },
    kpi: { href: "/dashboard/marketing", action: "Настроить KPI" },
    marketing: { href: "/dashboard/marketing", action: "Открыть маркетинг" },
    mop: { href: "/messages/mop-settings", action: "Настроить менеджера" },
  };
  const readySteps = steps.filter((step) => normalizedStatus(step.status) === "READY");
  const blockedSteps = steps.filter((step) => normalizedStatus(step.status) === "BLOCKED");
  const waitingSteps: LaunchStep[] = [];
  const readiness = steps.length ? Math.round((readySteps.length / steps.length) * 100) : null;
  const ownerBlockers = [
    ...launchBlockers.filter((b:any) => b?.human_required).map((b:any) => ({ key:`factory-${b.code}`, title: BLOCKER_LABELS[String(b.code)] || b.title || "Требуется действие", reason: b.action || "Проверьте настройку аккаунта" })),
    ...blockedSteps.map((step) => ({ key:`real-${step.key}`, title: step.title, reason: step.reason || "Требуется настройка" })),
  ].filter((item, index, arr) => arr.findIndex(x => x.title === item.title) === index);

  const targetLeads = Number(kpi?.target_leads_per_day || 0);
  const factLeads = Number(kpi?.contacts_today || 0);
  const remainingLeads = targetLeads > 0 ? Math.max(0, targetLeads - factLeads) : null;
  const humanCompanyName = (() => {
    const contextName = accountContextClients.find((item) => item.account_id === selectedClient)?.name;
    const candidates = [contextName, selected?.company, selected?.name, selected?.niche].filter(Boolean).map(String);
    const machineLike = (value: string) => /[_-]\d{3,}$/.test(value) || (/^[a-z0-9_-]+$/i.test(value) && /[_-]/.test(value));
    const clean = candidates.find((value) => !machineLike(value));
    if (clean) return clean;
    const raw = candidates[0] || "вашей компании";
    return raw.replace(/[_-]\d{3,}$/g, "").replace(/[_-]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase()).trim() || "вашей компании";
  })();
  const greetingName = humanCompanyName;
  const dayIndex = Math.floor(Date.now() / 86400000);
  const wishes = [
    "Пусть сегодня реклама приводит именно ваших клиентов, а продажи идут легче.",
    "Желаю сильного темпа, ясных решений и хороших сделок сегодня.",
    "Пусть рабочий день даст больше результата и оставит больше времени на себя.",
    "Сегодня фокус на росте: качественные обращения, уверенные продажи и спокойный контроль.",
    "Пусть бизнес растёт без суеты: BORIS возьмёт рутину на себя, а вам оставит решения.",
    "Желаю энергии, хорошего настроения и клиентов, с которыми приятно работать.",
    "Пусть сегодняшний результат станет ещё одним сильным шагом к вашим целям.",
  ];
  const heroWish = wishes[dayIndex % wishes.length];
  const planPctRaw = targetLeads > 0 ? Math.round((factLeads / targetLeads) * 100) : null;
  const planPct = planPctRaw !== null ? Math.min(100, planPctRaw) : null;
  const heroTitle = selected ? `Добрый день, ${greetingName}!` : "Добрый день!";
  const heroText = !selected
    ? "Выберите компанию — BORIS соберёт живую картину бизнеса."
    : heroWish;
  const goalMessage = !selected
    ? ""
    : targetLeads > 0 && factLeads >= targetLeads
      ? `Сегодня цель уже достигнута: ${factLeads} обращений при плане ${targetLeads}. Теперь задача — удержать качество и конвертировать обращения в продажи.`
      : remainingLeads !== null
        ? `Главная цель на сегодня — получить ещё ${remainingLeads} ${remainingLeads === 1 ? "обращение" : "обращения"} и выполнить план.`
        : "Сегодня BORIS держит фокус на новых обращениях, продажах и эффективности рекламы.";
  const twoHourWindowStart = briefTick * 7200000;
  const businessAchievement = targetLeads > 0 && factLeads >= targetLeads
    ? `Цель по обращениям выполнена: ${factLeads} из ${targetLeads}${factLeads > targetLeads ? ` — выше плана на ${factLeads - targetLeads}` : ""}.`
    : targetLeads > 0 && factLeads > 0
      ? `Получено ${factLeads} из ${targetLeads} целевых обращений — ${planPctRaw}% дневной цели.`
      : factLeads > 0
        ? `Сегодня бизнес получил ${factLeads} ${factLeads === 1 ? "обращение" : "обращений"}.`
        : "Новых обращений за сегодня пока нет — BORIS работает на рост результата.";
  const cplAchievement = kpi?.cost_per_lead_today != null && kpi?.max_cost_per_lead_rub != null
    ? Number(kpi.cost_per_lead_today) <= Number(kpi.max_cost_per_lead_rub)
      ? `Стоимость лида ${kpi.cost_per_lead_today} ₽ — в целевом диапазоне.`
      : `Стоимость лида ${kpi.cost_per_lead_today} ₽; цель — снизить до ${kpi.max_cost_per_lead_rub} ₽.`
    : "";
  const borisWork = businessAchievement;
  const borisPurpose = cplAchievement || (aiWorkspace?.summary?.bottleneck && aiWorkspace.summary.bottleneck !== "Нет доказанной проблемы"
    ? `Следующая цель: ${ownerText(aiWorkspace.summary.bottleneck, "улучшить результат рекламы")}.`
    : "BORIS продолжает работать на рост обращений и продаж.");
  const briefPeriodStart = new Date(twoHourWindowStart).toLocaleTimeString("ru-RU", {hour:"2-digit", minute:"2-digit"});
  const briefPeriodEnd = new Date(twoHourWindowStart + 7200000).toLocaleTimeString("ru-RU", {hour:"2-digit", minute:"2-digit"});

  const borisTariffReady = Boolean(billingState && ["tariff_1","tariff_2"].includes(String(billingState.tier || "")) && billingState.blocked_reason !== "unpaid_upgrade");
  const businessGoalReady = Boolean(mopRuntime?.dialog_goal_text || (mopRuntime?.dialog_goal && mopRuntime.dialog_goal !== "other"));
  const kpiReady = Boolean(aiMarketing?.kpi_configured);
  const feedReady = Boolean(aiMarketing?.feed_factory_ready);
  const sentToAvito = Boolean((Number(aiMarketing?.avito_identity_items || 0) > 0) && aiMarketing?.publication_mapping_ready);
  const modulesReady = Boolean((mopRuntime?.required_questions || []).length || (mopRuntime?.handoff_rules || []).length || (mopRuntime?.dialog_goal && mopRuntime.dialog_goal !== "other") || aiMarketing?.goal_auto_enabled);
  const setupSteps = selected ? [
    { key:"avito", title:"Подключить аккаунт через API Авито", hint:"Добавьте ключ приложения и секретный ключ — BORIS проверит соединение.", done:Boolean(aiMarketing?.avito_connected), href:"/dashboard/connect-avito" },
    { key:"billing", title:"Выбрать и оплатить тариф и услуги", hint:borisTariffReady ? `Активен: ${billingState?.tier_name || "тариф BORIS"}` : "Выберите тариф BORIS и нужные услуги.", done:borisTariffReady, href:"/dashboard/billing" },
    { key:"goal", title:"Выбрать бизнес-цель", hint:"Скажите BORIS, к какому результату должен вести клиента и бизнес.", done:businessGoalReady, href:"/messages/mop-settings" },
    { key:"kpi", title:"Настроить KPI", hint:"Задайте план обращений и допустимую стоимость лида.", done:kpiReady, href:"/dashboard/analytics" },
    { key:"feed", title:"Создать объявления и фид", hint:"Подготовьте ассортимент, тексты, фото и автозагрузку объявлений.", done:feedReady, href:"/dashboard/campaign" },
    { key:"publish", title:"Отправить в Avito", hint:"Проверьте объявления и отправьте готовый фид после согласования.", done:sentToAvito, href:"/dashboard/campaign" },
    { key:"modules", title:"Настроить остальные модули", hint:"МОП, маркетинг, продажи, база знаний и дополнительные сценарии.", done:modulesReady, href:"/ai", optional:true },
  ] : [];
  const coreSetupSteps = setupSteps.filter((step) => !step.optional);
  const setupDoneCount = coreSetupSteps.filter((step) => step.done).length;
  const setupComplete = coreSetupSteps.length > 0 && setupDoneCount === coreSetupSteps.length;
  const currentSetupStep = setupSteps.find((step) => !step.done) || setupSteps[setupSteps.length - 1];
  const setupPct = coreSetupSteps.length ? Math.round((setupDoneCount / coreSetupSteps.length) * 100) : 0;

  return (
    <AppShell
      active="home"
      title="Главная"
      subtitle="Единый командный центр BORIS"
    >
      <div className={styles.referenceDashboard} data-testid="command-center-root">
        {error ? (
          <div className={styles.error} role="alert">
            <strong>Данные временно недоступны.</strong>
            <span>{error}</span>
          </div>
        ) : null}

        <div className={styles.workspaceLayout}>
          <main className={styles.controlCenter}>
            <section className={styles.controlHeader} data-testid="command-center-status">
              <div className={styles.heroMain}>
                <div className={styles.kicker}>ПУЛЬС БИЗНЕСА · СЕГОДНЯ</div>
                <h1>{heroTitle}</h1>
                <p className={styles.heroWish}>✨ {heroText}</p>
                {selected ? <p className={styles.heroGoal}>{goalMessage}</p> : null}
                {selected ? <div className={styles.heroPulseRow}>
                  <span><b>{factLeads}</b> обращений сегодня</span>
                  {planPctRaw !== null ? <span><b>{planPctRaw}%</b> дневного плана</span> : null}
                  {kpi?.cost_per_lead_today != null ? <span><b>{kpi.cost_per_lead_today} ₽</b> цена лида сегодня</span> : null}
                </div> : null}
              </div>
              {selected ? <div className={`${styles.borisBrief} ${!setupComplete ? styles.setupBrief : ""}`}>
                {setupComplete ? <>
                  <div className={styles.borisBriefHead}><span className={styles.liveDot}/><b>ДОСТИЖЕНИЯ · {briefPeriodStart}–{briefPeriodEnd}</b><span>обновление каждые 2 часа</span></div>
                  <strong>{borisWork}</strong>
                  <p>{borisPurpose}</p>
                  <div className={styles.briefActions}><Link href="/dashboard/analytics">Результаты бизнеса →</Link><Link href="/command">Спросить BORIS</Link></div>
                </> : <>
                  <div className={styles.borisBriefHead}><span className={styles.setupDot}/><b>ПЕРВИЧНАЯ НАСТРОЙКА BORIS · {setupDoneCount}/{coreSetupSteps.length}</b><span>{setupPct}% шагов выполнено</span></div>
                  <strong>{currentSetupStep ? `Следующий шаг: ${currentSetupStep.title}` : "Завершаем настройку"}</strong>
                  <p>{currentSetupStep?.hint || "BORIS проверит каждый этап и подскажет, что делать дальше."}</p>
                  <div className={styles.setupProgress}><span style={{width:`${setupPct}%`}} /></div>
                  <div className={styles.briefActions}>
                    <button type="button" className={styles.guideButton} onClick={() => setSetupGuideOpen((value) => !value)}>BORIS поможет вам всё настроить</button>
                    {currentSetupStep ? <Link href={currentSetupStep.href}>Перейти к шагу →</Link> : null}
                  </div>
                </>}
              </div> : null}
              {selected && targetLeads > 0 ? <div className={styles.heroForecast}>
                <span>Темп дня</span><strong>{planPctRaw}%</strong><small>{remainingLeads === 0 ? (factLeads > targetLeads ? `План +${factLeads - targetLeads} ✓` : "План выполнен ✓") : `До цели: ${remainingLeads}`}</small>
                <div className={styles.forecastRing} style={{"--progress": `${planPct || 0}%`} as React.CSSProperties}><b>{factLeads}/{targetLeads}</b></div>
              </div> : null}
            </section>

            {selected && setupGuideOpen ? <section className={styles.setupGuide} aria-label="Мастер настройки BORIS">
              <div className={styles.setupGuideHead}>
                <div><div className={styles.kicker}>BORIS ВЕДЁТ ПО ШАГАМ</div><h2>Настройка кабинета</h2><p>Здесь 6 обязательных шагов первичной настройки. Это отдельный показатель от рабочей готовности аккаунта ниже.</p></div>
                <button type="button" onClick={() => setSetupGuideOpen(false)} aria-label="Закрыть мастер">×</button>
              </div>
              <div className={styles.setupSteps}>
                {setupSteps.map((step, index) => {
                  const current = currentSetupStep?.key === step.key;
                  return <Link href={step.href} key={step.key} className={`${styles.setupStep} ${step.done ? styles.setupStepDone : current ? styles.setupStepCurrent : ""}`}>
                    <span className={styles.setupStepNumber}>{step.done ? "✓" : index + 1}</span>
                    <span className={styles.setupStepText}><strong>{step.title}</strong><small>{step.hint}</small></span>
                    <span className={styles.setupStepState}>{step.done ? "Готово" : step.optional ? "Дополнительно" : current ? "Сделать сейчас" : "Дальше"}</span>
                  </Link>;
                })}
              </div>
              <div className={styles.setupGuideFooter}>
                <span><b>{setupDoneCount}</b> из <b>{coreSetupSteps.length}</b> обязательных шагов выполнено</span>
                {currentSetupStep ? <Link href={currentSetupStep.href}>Продолжить настройку →</Link> : <Link href="/dashboard/analytics">Посмотреть результат →</Link>}
              </div>
            </section> : null}

            <section className={styles.mainWorkGrid}>
              <div className={styles.primaryColumn}>
                <div className={styles.metricRow}>
                  <Link href="/dashboard/analytics" className={styles.metricLink}><Card className={styles.compactMetric}><div className={styles.metricTop}><span className={styles.metricIcon}><Icon name="chart" size={26} /></span><span>Лиды · план</span></div><strong>{kpi?.target_leads_per_day ?? "—"}</strong><small>цель на день</small></Card></Link>
                  <Link href="/dashboard/analytics" className={styles.metricLink}><Card className={styles.compactMetric}><div className={styles.metricTop}><span className={styles.metricIcon}><Icon name="messages" size={26} /></span><span>Лиды · факт</span></div><strong>{kpi?.contacts_today ?? "—"}</strong><small>обращений сегодня</small></Card></Link>
                  <Link href="/dashboard/analytics" className={styles.metricLink}><Card className={styles.compactMetric}><div className={styles.metricTop}><span className={styles.metricIcon}><Icon name="coins" size={26} /></span><span>Цена лида · факт</span></div><strong>{kpi?.cost_per_lead_today != null ? `${kpi.cost_per_lead_today} ₽` : "—"}</strong><small>{kpi?.max_cost_per_lead_rub != null ? `цель ≤ ${kpi.max_cost_per_lead_rub} ₽` : "цель не задана"}</small></Card></Link>
                  <Link href="/ai" className={styles.metricLink}><Card className={styles.compactMetric}><div className={styles.metricTop}><span className={styles.metricIcon}><Icon name="ai" size={26} /></span><span>ИИ-менеджер</span></div><strong>{!accountCapabilities ? "—" : !mopEntitled ? "Не подключён" : mopRuntime?.enabled === false ? "Выключен" : "Включён"}</strong><small>{!mopEntitled ? "Нет активного пакета МОП" : mopRuntime?.work_schedule?.enabled ? "По графику" : "24/7"}</small></Card></Link>
                  <Link href="/dashboard/marketing" className={styles.metricLink}><Card className={styles.compactMetric}><div className={styles.metricTop}><span className={styles.metricIcon}><Icon name="ads" size={26} /></span><span>ИИ-маркетолог</span></div><strong>{!aiMarketing ? "—" : aiMarketing?.ai_marketing_ready ? "Работает" : aiMarketing?.goal_auto_enabled ? "Нужна настройка" : "Выключен"}</strong><small>{aiMarketing?.ai_marketing_ready ? ownerText(aiWorkspace?.summary?.current_work, "Контроль KPI") : ownerText(aiMarketing?.user_action_required, "Не все условия выполнены")}</small></Card></Link>
                  <Link href="/rop" className={styles.metricLink}><Card className={styles.compactMetric}><div className={styles.metricTop}><span className={styles.metricIcon}><Icon name="analytics" size={26} /></span><span>Виртуальный РОП</span></div><strong>Открыть</strong><small>Контроль продаж</small></Card></Link>
                </div>

                <Card className={styles.quickActionsMain} data-testid="command-center-quick">
                  <div className={styles.quickActionsHeader}>
                    <div>
                      <div className={styles.kicker}>Быстрые действия</div>
                      <h2>Перейти к работе</h2>
                    </div>
                    <span>Основные рабочие разделы BORIS</span>
                  </div>
                  <div className={styles.quickActionsGrid}>
                    {SECTIONS.map((item) => (
                      <Link href={item.href} key={item.key} className={styles.quickActionCard}>
                        <span className={styles.sectionIcon}><Icon name={item.icon} size={28} /></span>
                        <span className={styles.quickActionText}>
                          <strong>{item.title}</strong>
                          <small>{item.description}</small>
                        </span>
                        <span className={styles.quickActionArrow}>›</span>
                      </Link>
                    ))}
                  </div>
                </Card>

                <div className={styles.dashboardColumns}>
                  <Card className={styles.workCard}>
                    <div className={styles.panelTitleRow}>
                      <div><div className={styles.kicker}>Бизнес-сценарий</div><h2>Что BORIS делает сейчас</h2></div>
                      <Link href="/dashboard/scenarios">Все сценарии</Link>
                    </div>
                    {activeScenario ? (
                      <div className={styles.scenarioBody}>
                        <strong>{ownerText(activeScenario.workflow?.current_work, activeScenario.title)}</strong>
                        <div className={styles.infoGrid}>
                          <span>Цель <b>{activeScenario.workflow?.goal || activeScenario.title}</b></span>
                          <span>Прогресс <b>{activeScenario.progress?.done || 0}/{activeScenario.progress?.total || 0}</b></span>
                          <span>Результат <b>{activeScenario.workflow?.result ? ownerText(activeScenario.workflow.result, `${activeScenario.progress?.percent || 0}%`) : `${activeScenario.progress?.percent || 0}%`}</b></span>
                          <span>Дальше <b>{ownerText(activeScenario.workflow?.next_action, "Продолжить сценарий")}</b></span>
                        </div>
                        <Link className={styles.primaryLink} href={`/dashboard/scenarios/${activeScenario.id}`}>Продолжить работу</Link>
                      </div>
                    ) : <div className={styles.empty}>Активного сценария нет.</div>}
                  </Card>

                  <Card className={styles.workCard}>
                    <div className={styles.panelTitleRow}>
                      <div><div className={styles.kicker}>ИИ-маркетолог</div><h2>Текущая работа</h2></div>
                      <Link href="/dashboard/marketing">Открыть</Link>
                    </div>
                    <div className={styles.infoGrid}>
                      <span>Проблема <b>{aiWorkspace?.summary?.bottleneck || "Нет доказанной проблемы"}</b></span>
                      <span>BORIS делает <b>{ownerText(aiWorkspace?.summary?.current_work, "Контролирует цель")}</b></span>
                      <span>От владельца <b>{ownerText(aiWorkspace?.summary?.owner_action,"Ничего")}</b></span>
                      <span>Последний результат <b>{ownerText(aiWorkspace?.summary?.last_result?.current_status,"Пока нет")}</b></span>
                    </div>
                  </Card>
                </div>

                <div className={styles.threePanelGrid}>
                  <Card className={styles.workCard}>
                    <div className={styles.panelTitleRow} data-testid="command-center-today"><h2>Действия BORIS сегодня</h2></div>
                    <div className={styles.timelineCompact} data-testid="command-center-journal">
                      {aiActions.length ? aiActions.slice(0,5).map((a:any) => (
                        <div className={styles.timelineRow} key={a.operation_id}>
                          <span>{a.ts ? new Date(a.ts).toLocaleTimeString("ru-RU", {hour:"2-digit",minute:"2-digit"}) : "—"}</span>
                          <div><strong>{ownerText(a.reason, "Оптимизация результата")}</strong><small>{a.status === "published" ? "Опубликовано" : a.status === "safe_feed_ready" ? "Подготовлено" : ownerText(a.status, "В работе")}</small></div>
                        </div>
                      )) : <div className={styles.empty}>Подтверждённых действий сегодня нет.</div>}
                    </div>
                  </Card>

                  <Card className={styles.workCard}>
                    <div className={styles.panelTitleRow} data-testid="command-center-attention"><h2>Требует внимания</h2><Link href="/dashboard/factory-decisions">Все</Link></div>
                    <div className={styles.alertList}>
                      {ownerBlockers.slice(0,5).map((item) => (
                        <div className={styles.alertRow} key={item.key}>
                          <span className={styles.alertDotDanger} />
                          <span><strong>{ownerText(item.title, "Требуется настройка")}</strong><small>{ownerText(item.reason, "Проверьте настройку аккаунта")}</small></span>
                        </div>
                      ))}
                      {!ownerBlockers.length ? <div className={styles.empty}>Критичных действий нет.</div> : null}
                    </div>
                  </Card>

                  <Card className={styles.workCard}>
                    <div className={styles.panelTitleRow}><h2>Решения владельца</h2></div>
                    {ownerInbox.length ? ownerInbox.slice(0,5).map((x:any) => (
                      <div key={`${x.scenario_id}-${x.step_key}`} className={styles.ownerActionRow}>
                        <span>{ownerText(x.title, "Решение владельца")}</span><Link href={x.route || `/dashboard/scenarios/${x.scenario_id}`}>Открыть</Link>
                      </div>
                    )) : <div className={styles.empty}>От вас сейчас ничего не требуется.</div>}
                  </Card>
                </div>
              </div>

            </section>


            {selected && clientSupervisor ? <section className={styles.readinessPanel}>
              <div className={styles.panelTitleRow}>
                <div><div className={styles.kicker}>Автономный контроль клиента</div><h2>Что происходит у клиента прямо сейчас</h2></div>
                <Link href="/dashboard/clients-control">Полное управление клиентами →</Link>
              </div>
              <div className={styles.readinessBottomGrid}>
                <span>Лиды сегодня: <b>{clientSupervisor?.plan_fact?.contacts_today ?? clientSupervisor?.operational?.kpi?.actual_leads ?? "—"}</b> / план <b>{clientSupervisor?.plan_fact?.target_leads_per_day ?? "—"}</b></span>
                <span>Активные объявления: <b>{clientSupervisor?.operational?.publications?.active_items ?? clientSupervisor?.plan_fact?.active_items ?? "—"}</b></span>
                <span>Входящие 24ч: <b>{clientSupervisor?.operational?.messages?.inbound_24h ?? "—"}</b> · без ответа <b>{clientSupervisor?.operational?.messages?.unanswered ?? "—"}</b></span>
                <span>CRM просрочено: <b>{clientSupervisor?.operational?.crm?.overdue_tasks ?? "—"}</b></span>
                <span>Очередь BORIS: <b>{clientSupervisor?.queue?.queued ?? 0}</b> · выполняется <b>{clientSupervisor?.queue?.running ?? 0}</b> · зависло <b>{clientSupervisor?.queue?.stale_running ?? 0}</b></span>
                <span>Вмешательство владельца: <b>{(clientSupervisor?.needs_owner || []).length ? "требуется" : "не требуется"}</b></span>
              </div>
              {(clientSupervisor?.human_work || []).length ? <div className={styles.alertList}>
                {(clientSupervisor.human_work || []).slice(0,4).map((item:any,index:number)=><div className={styles.alertRow} key={`human-${index}`}><span className={styles.alertDotDanger}/><span><strong>Работа менеджера</strong><small>{ownerText(item.reason,"Есть просроченное действие")} · количество: {item.count ?? "—"}</small></span></div>)}
              </div> : null}
            </section> : null}

            <section className={styles.readinessPanel}>
              <div className={styles.panelTitleRow}>
                <div>
                  <div className={styles.kicker}>Готовность рабочего контура</div>
                  <h2>{selected ? selected.name : "Аккаунт не выбран"}</h2>
                  {selected ? <small className={styles.readinessSource}>Только фактические данные текущего аккаунта · без расчёта по наличию модулей</small> : null}
                </div>
                <span className={blockedSteps.length ? styles.statusDanger : readiness === 100 ? styles.statusGood : styles.statusNeutral}>
                  {loadingPlan ? "Обновляем" : blockedSteps.length ? `${blockedSteps.length} требуют настройки` : readiness === 100 ? "Готово" : "В работе"}
                </span>
              </div>

              {steps.length ? (
                <div className={styles.stepsFlow}>
                  {steps.map((step, index) => {
                    const state = normalizedStatus(step.status);
                    const ready = ["READY", "DONE", "PASS"].includes(state);
                    const blocked = ["BLOCKED", "FAIL", "FAILED"].includes(state);
                    const destination = stepDestinations[step.key] || { href: "/dashboard/command-center", action: "Открыть раздел" };
                    return (
                      <Link
                        href={destination.href}
                        className={`${styles.stepCard} ${styles.stepCardLink} ${blocked ? styles.stepCardBlocked : ready ? styles.stepCardReady : styles.stepCardWaiting}`}
                        key={step.key}
                        aria-label={`${destination.action}: ${ownerText(step.title, "Шаг рабочего контура")}`}
                      >
                        <div className={styles.stepCardTop}>
                          <span className={styles.stepIcon}>
                            <Icon name={(["user","briefcase","shield","settings","list","ads","edit","messages","plug","target","check"] as const)[index] || "check"} size={19} />
                          </span>
                          <span className={styles.stepNumber}>{index + 1}</span>
                        </div>
                        <strong>{ownerText(step.title, "Шаг рабочего контура")}</strong>
                        <small>{stepSummary(step)}</small>
                        <div className={styles.stepCardFooter}>
                          <span className={blocked ? styles.stepBlocked : ready ? styles.stepReady : styles.stepWaiting}>
                            {blocked ? "Нужно действие" : ready ? "Готово" : "Ожидает"}
                          </span>
                          <span className={styles.stepAction}>{destination.action} <span aria-hidden="true">→</span></span>
                        </div>
                      </Link>
                    );
                  })}
                </div>
              ) : (
                <div className={styles.empty}>{selected ? "Сервер BORIS пока не вернул шаги плана запуска." : "Выберите клиента, чтобы увидеть рабочий контур."}</div>
              )}
            </section>

            <Card className={styles.readinessBottom}>
              <div className={styles.readinessBottomHead}>
                <div>
                  <div className={styles.kicker}>Рабочая готовность аккаунта</div>
                  <strong>{readiness === null ? "Готовность не рассчитана" : `Рабочий контур: подтверждено ${readySteps.length} из ${steps.length} условий (${readiness}%)`}</strong>
                  {selected ? <small className={styles.readinessSource}>8 условий: аккаунт, тариф, Avito, данные бизнеса, объявления, KPI, ИИ-маркетолог и ИИ-менеджер. Это не проценты первичной настройки.</small> : null}
                </div>
                <Link className={styles.primaryLink} href="/dashboard/connect-avito">{avitoConnected ? "Проверить подключение" : "Подключить Авито"}</Link>
              </div>
              <div className={styles.readinessBottomGrid}>
                <span className={styles.checkOk}>✓ Подтверждено данными: {readySteps.length}</span>
                <span className={blockedSteps.length ? styles.checkBad : styles.checkOk}>• Не готово: {blockedSteps.length}</span>
                <span className={ownerBlockers.length ? styles.checkBad : styles.checkOk}>• Действий владельца: {ownerBlockers.length}</span>
              </div>
            </Card>
          </main>
        </div>
      </div>
    </AppShell>
  );
}
