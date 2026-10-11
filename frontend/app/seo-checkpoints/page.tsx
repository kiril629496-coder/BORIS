"use client";

import {
  AlertCircle,
  BarChart3,
  ChevronDown,
  CircleDot,
  Download,
  ExternalLink,
  Folder,
  LayoutGrid,
  ListChecks,
  Menu,
  MoreVertical,
  Play,
  Puzzle,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";

type Autofill = {
  email: string;
  username: string;
  password: string;
  phone: string;
  business_name: string;
  website: string;
  description: string;
  category_queries: string[];
  address: string;
  city: string;
  country: string;
  birth_date: string;
  contact_name: string;
};

type CheckpointEntry = {
  id: string;
  platform: string;
  name: string;
  action_url: string;
  host: string;
  checkpoint: string | null;
  action: string | null;
  after_completion: string | null;
  flow_kind: "account_onboarding" | "direct_listing";
  target_site: string | null;
  target_sites: string[];
  iks: number | null;
  iks_tier: string | null;
  human_effort_score: number | null;
  top_5_platform: boolean;
  autofill_fields: string[];
  autofill: Autofill;
};

type CenterPayload = {
  status: string;
  generated_at: string;
  extension_protocol: number;
  security: Record<string, unknown>;
  summary: {
    unique_platforms: number;
    entries: number;
    top_5_platforms: string[];
    direct_listing_pending?: number;
  };
  entries: CheckpointEntry[];
};

type ExtensionStatus = {
  state?: string;
  submittedAt?: string;
  pageUrl?: string;
  filledAt?: string;
  attemptedAt?: string;
};

type ServerResult = {
  error?: string;
  verification?: {
    status?: string;
    checkpoint?: string | null;
    publication_url?: string;
  };
};

type FilterKey = "all" | "top" | "captcha" | "ready" | "errors";
type SortMode = "priority" | "iks" | "name";
type StatusTone = "captcha" | "ready" | "submitted" | "filled" | "error" | "action";

type QueueStatus = {
  tone: StatusTone;
  label: string;
  detail: string;
};

const palette = [
  ["#2563EB", "#DBEAFE"],
  ["#7C3AED", "#EDE9FE"],
  ["#EA580C", "#FFEDD5"],
  ["#059669", "#D1FAE5"],
  ["#DB2777", "#FCE7F3"],
  ["#0891B2", "#CFFAFE"],
  ["#4F46E5", "#E0E7FF"],
  ["#DC2626", "#FEE2E2"],
];

async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const token =
    typeof window !== "undefined" ? localStorage.getItem("boris_token") : null;
  const headers = new Headers(init.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(input, { ...init, headers, cache: "no-store" });
  if (response.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
  }
  return response;
}

function checkpointLabel(value: string | null) {
  const labels: Record<string, string> = {
    captcha_required: "CAPTCHA",
    math_antibot_required: "Anti-bot проверка",
    terms_acceptance_required: "Принятие условий",
    registration_agreement_required: "Правила регистрации",
    captcha_terms_registration_required: "Аккаунт + Terms + CAPTCHA",
    captcha_age_and_terms_required: "CAPTCHA + возраст + Terms",
    captcha_age_declaration_required: "CAPTCHA + возраст",
    truthful_company_identity_required: "Данные компании",
    business_email_required: "Корпоративная почта",
    external_account_sso_required: "Внешний аккаунт / SSO",
    email_verification_required: "Подтверждение email",
    sms_or_verification_code_required: "Код подтверждения",
  };
  return labels[value || ""] || value || "Ручное подтверждение";
}

function tierLabel(value: string | null) {
  if (value === "high") return "HIGH";
  if (value === "medium") return "MEDIUM";
  if (value === "low") return "LOW";
  return "UNKNOWN";
}

function priorityScore(entry: CheckpointEntry) {
  return (
    (entry.top_5_platform ? 10000 : 0) +
    Number(entry.human_effort_score || 0) * 10 +
    Number(entry.iks || 0)
  );
}

function priorityMeta(entry: CheckpointEntry) {
  const score = Number(entry.human_effort_score || 0);
  if (entry.top_5_platform || score >= 600) {
    return { label: "Высокий", bars: 3, color: "#EF4444" };
  }
  if (score >= 350) {
    return { label: "Средний", bars: 2, color: "#F59E0B" };
  }
  return { label: "Низкий", bars: 1, color: "#94A3B8" };
}

function projectsFor(entry: CheckpointEntry) {
  if (entry.target_site) return [entry.target_site];
  return entry.target_sites || [];
}

function hashIndex(value: string, length: number) {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }
  return hash % length;
}

function initials(value: string) {
  const words = String(value || "")
    .replace(/[^p{L}p{N}]+/gu, " ")
    .trim()
    .split(/s+/)
    .filter(Boolean);
  if (!words.length) return "B";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

function ChromeMark({ size = 38 }: { size?: number }) {
  return (
    <span
      className="relative inline-flex shrink-0 items-center justify-center rounded-full shadow-sm"
      style={{
        width: size,
        height: size,
        background:
          "conic-gradient(#ef4444 0 33%, #facc15 33% 66%, #22c55e 66% 100%)",
      }}
      aria-hidden="true"
    >
      <span
        className="rounded-full border-[3px] border-white bg-blue-500"
        style={{ width: size * 0.48, height: size * 0.48 }}
      />
    </span>
  );
}

function BorisLogo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-600 to-blue-400 text-xl font-black text-white shadow-[0_10px_26px_rgba(37,99,235,0.28)]">
        B
      </div>
      {!compact && (
        <div className="leading-tight">
          <div className="text-lg font-black tracking-tight text-slate-950">
            BORIS
          </div>
          <div className="text-xs font-semibold text-slate-500">
            SEO Checkpoint Center
          </div>
        </div>
      )}
    </div>
  );
}

function PlatformMark({ entry }: { entry: CheckpointEntry }) {
  const [foreground, background] =
    palette[hashIndex(entry.platform || entry.name, palette.length)];
  return (
    <div
      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-xs font-black shadow-sm ring-1 ring-black/[0.04]"
      style={{ color: foreground, background }}
      aria-hidden="true"
    >
      {initials(entry.name)}
    </div>
  );
}

function PriorityBars({
  bars,
  color,
}: {
  bars: number;
  color: string;
}) {
  return (
    <span className="inline-flex h-5 items-end gap-[2px]" aria-hidden="true">
      {[1, 2, 3].map((bar) => (
        <span
          key={bar}
          className="w-[3px] rounded-full"
          style={{
            height: 4 + bar * 4,
            background: bar <= bars ? color : "#CBD5E1",
          }}
        />
      ))}
    </span>
  );
}

function SelectBox({
  value,
  onChange,
  children,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  ariaLabel: string;
}) {
  return (
    <label className="relative block">
      <span className="sr-only">{ariaLabel}</span>
      <select
        aria-label={ariaLabel}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-11 appearance-none rounded-2xl border border-slate-200 bg-white pl-4 pr-10 text-sm font-semibold text-slate-700 outline-none transition hover:border-blue-200 focus:border-blue-400 focus:ring-4 focus:ring-blue-100"
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
    </label>
  );
}

function SidebarItem({
  icon: Icon,
  label,
  href,
  active = false,
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  href: string;
  active?: boolean;
}) {
  return (
    <a
      href={href}
      className={
        "flex items-center gap-3 rounded-2xl px-4 py-3 text-sm font-bold transition " +
        (active
          ? "bg-blue-50 text-blue-700 shadow-[inset_0_0_0_1px_rgba(59,130,246,0.08)]"
          : "text-slate-600 hover:bg-slate-50 hover:text-slate-950")
      }
    >
      <Icon className="h-[18px] w-[18px]" />
      <span>{label}</span>
    </a>
  );
}

function queueStatus(
  entry: CheckpointEntry,
  extensionStatus: ExtensionStatus | undefined,
  result: ServerResult | undefined
): QueueStatus {
  if (result?.error) {
    return {
      tone: "error",
      label: "ОШИБКА",
      detail: "Нужна повторная проверка",
    };
  }

  const verifiedStatus = String(result?.verification?.status || "").toLowerCase();
  if (verifiedStatus === "verified" || verifiedStatus === "ready") {
    return {
      tone: "ready",
      label: "ГОТОВО",
      detail: "Проверено BORIS",
    };
  }

  if (
    verifiedStatus === "submitted_pending_verification" ||
    extensionStatus?.state === "submitted"
  ) {
    return {
      tone: "submitted",
      label: "ОТПРАВЛЕНО",
      detail: "BORIS проверяет",
    };
  }

  if (
    extensionStatus?.state === "filled" ||
    extensionStatus?.state === "attempted"
  ) {
    return {
      tone: "filled",
      label: "ЗАПОЛНЕНО",
      detail: "Остался ручной шаг",
    };
  }

  if (String(entry.checkpoint || "").includes("captcha")) {
    return {
      tone: "captcha",
      label: "CAPTCHA",
      detail: "Ожидает проверки",
    };
  }

  return {
    tone: "action",
    label: "ПОДТВЕРЖДЕНИЕ",
    detail: "Нужно действие",
  };
}

function StatusBadge({ status }: { status: QueueStatus }) {
  const styles: Record<StatusTone, string> = {
    captcha: "bg-amber-50 text-amber-700 ring-amber-200",
    ready: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    submitted: "bg-blue-50 text-blue-700 ring-blue-200",
    filled: "bg-cyan-50 text-cyan-700 ring-cyan-200",
    error: "bg-rose-50 text-rose-700 ring-rose-200",
    action: "bg-violet-50 text-violet-700 ring-violet-200",
  };
  const dots: Record<StatusTone, string> = {
    captcha: "bg-amber-400",
    ready: "bg-emerald-500",
    submitted: "bg-blue-500",
    filled: "bg-cyan-500",
    error: "bg-rose-500",
    action: "bg-violet-500",
  };

  return (
    <div className="min-w-0">
      <span
        className={
          "inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-[11px] font-black ring-1 ring-inset " +
          styles[status.tone]
        }
      >
        <span className={"h-2 w-2 rounded-full " + dots[status.tone]} />
        {status.label}
      </span>
      <div className="mt-1.5 truncate text-[11px] font-medium text-slate-400">
        {status.detail}
      </div>
    </div>
  );
}

function Pill({
  children,
  tone = "slate",
}: {
  children: ReactNode;
  tone?: "blue" | "amber" | "violet" | "slate" | "green";
}) {
  const styles = {
    blue: "bg-blue-50 text-blue-700 ring-blue-100",
    amber: "bg-amber-50 text-amber-700 ring-amber-100",
    violet: "bg-violet-50 text-violet-700 ring-violet-100",
    slate: "bg-slate-100 text-slate-600 ring-slate-200",
    green: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  };
  return (
    <span
      className={
        "inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-bold ring-1 ring-inset " +
        styles[tone]
      }
    >
      {children}
    </span>
  );
}

export default function SeoCheckpointsPage() {
  const [data, setData] = useState<CenterPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [extensionConnected, setExtensionConnected] = useState(false);
  const [statuses, setStatuses] = useState<Record<string, ExtensionStatus>>({});
  const [serverResults, setServerResults] = useState<Record<string, ServerResult>>(
    {}
  );
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [projectFilter, setProjectFilter] = useState("all");
  const [sortMode, setSortMode] = useState<SortMode>("priority");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const ackedRef = useRef<Record<string, string>>({});

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/seo-checkpoints-data");
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload?.detail || "Не удалось загрузить очередь");
      }
      setData(payload);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить очередь");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.source !== window) return;
      const msg = event.data;
      if (!msg || msg.source !== "BORIS_CHECKPOINT_EXTENSION") return;
      if (msg.type === "READY") {
        setExtensionConnected(true);
      }
      if (msg.type === "STATUS") {
        setExtensionConnected(true);
        setStatuses(msg.statuses || {});
      }
      if (msg.type === "SYNC_ERROR") {
        setExtensionConnected(true);
        setError(
          `Расширение подключено, но не получило owner-конфигурацию: ${
            msg.error || "unknown"
          }`
        );
      }
    };

    window.addEventListener("message", handler);
    window.postMessage(
      { source: "BORIS_CHECKPOINT_CENTER", type: "PING" },
      "*"
    );

    const timer = window.setInterval(() => {
      window.postMessage(
        { source: "BORIS_CHECKPOINT_CENTER", type: "POLL_STATUS" },
        "*"
      );
    }, 2500);

    return () => {
      window.removeEventListener("message", handler);
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!data) return;
    window.postMessage(
      {
        source: "BORIS_CHECKPOINT_CENTER",
        type: "SYNC_CONFIG",
      },
      "*"
    );
  }, [data]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  useEffect(() => {
    if (!data) return;
    const byId = new Map(data.entries.map((entry) => [entry.id, entry]));

    for (const [entryId, status] of Object.entries(statuses)) {
      if (status.state !== "submitted" || !status.submittedAt) continue;
      const statusSignature = `${status.submittedAt}|${status.pageUrl || ""}`;
      if (ackedRef.current[entryId] === statusSignature) continue;

      const entry = byId.get(entryId);
      if (!entry) continue;

      ackedRef.current[entryId] = statusSignature;
      apiFetch("/seo-checkpoints-complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          entry_id: entry.id,
          platform: entry.platform,
          target_site: entry.target_site,
          page_url: status.pageUrl || entry.action_url,
        }),
      })
        .then(async (response) => {
          const payload = await response.json();
          if (!response.ok) throw new Error(payload?.detail || "Ошибка проверки");
          setServerResults((prev) => ({ ...prev, [entryId]: payload }));
          window.postMessage(
            {
              source: "BORIS_CHECKPOINT_CENTER",
              type: "ACK_ENTRY",
              entryId,
              submittedAt: status.submittedAt,
            },
            "*"
          );
          window.setTimeout(load, 1500);
        })
        .catch((e: unknown) => {
          setServerResults((prev) => ({
            ...prev,
            [entryId]: {
              error: e instanceof Error ? e.message : "Ошибка проверки",
            },
          }));
        });
    }
  }, [statuses, data]);

  const entries = useMemo(() => data?.entries ?? [], [data]);

  const topEntries = useMemo(
    () => entries.filter((entry) => entry.top_5_platform),
    [entries]
  );

  const captchaEntries = useMemo(
    () =>
      entries.filter((entry) =>
        String(entry.checkpoint || "").includes("captcha")
      ),
    [entries]
  );

  const entryStatuses = useMemo(() => {
    const map = new Map<string, QueueStatus>();
    for (const entry of entries) {
      map.set(
        entry.id,
        queueStatus(entry, statuses[entry.id], serverResults[entry.id])
      );
    }
    return map;
  }, [entries, statuses, serverResults]);

  const readyCount = useMemo(
    () =>
      entries.filter((entry) => {
        const tone = entryStatuses.get(entry.id)?.tone;
        return tone === "ready" || tone === "submitted";
      }).length,
    [entries, entryStatuses]
  );

  const errorCount = useMemo(
    () =>
      entries.filter((entry) => entryStatuses.get(entry.id)?.tone === "error")
        .length,
    [entries, entryStatuses]
  );

  const projectOptions = useMemo(() => {
    const projects = new Set<string>();
    for (const entry of entries) {
      for (const project of projectsFor(entry)) projects.add(project);
    }
    return [...projects].sort((a, b) => a.localeCompare(b, "ru"));
  }, [entries]);

  const visibleEntries = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("ru");

    const filtered = entries.filter((entry) => {
      const status = entryStatuses.get(entry.id);
      if (filter === "top" && !entry.top_5_platform) return false;
      if (
        filter === "captcha" &&
        !String(entry.checkpoint || "").includes("captcha")
      ) {
        return false;
      }
      if (
        filter === "ready" &&
        status?.tone !== "ready" &&
        status?.tone !== "submitted"
      ) {
        return false;
      }
      if (filter === "errors" && status?.tone !== "error") return false;

      const projects = projectsFor(entry);
      if (projectFilter !== "all" && !projects.includes(projectFilter)) {
        return false;
      }

      if (!normalizedQuery) return true;
      const haystack = [
        entry.name,
        entry.host,
        entry.platform,
        entry.action,
        checkpointLabel(entry.checkpoint),
        ...projects,
      ]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase("ru");

      return haystack.includes(normalizedQuery);
    });

    return [...filtered].sort((a, b) => {
      if (sortMode === "iks") {
        return Number(b.iks || 0) - Number(a.iks || 0);
      }
      if (sortMode === "name") {
        return a.name.localeCompare(b.name, "ru");
      }
      return priorityScore(b) - priorityScore(a);
    });
  }, [
    entries,
    entryStatuses,
    filter,
    projectFilter,
    query,
    sortMode,
  ]);

  const selectedEntries = useMemo(
    () => entries.filter((entry) => selectedIds.has(entry.id)),
    [entries, selectedIds]
  );

  const allVisibleSelected =
    visibleEntries.length > 0 &&
    visibleEntries.every((entry) => selectedIds.has(entry.id));

  const openEntries = (selected: CheckpointEntry[]) => {
    if (!selected.length) return;

    if (extensionConnected) {
      window.postMessage(
        {
          source: "BORIS_CHECKPOINT_CENTER",
          type: "OPEN_ENTRIES",
          entryIds: selected.map((entry) => entry.id),
        },
        "*"
      );
      return;
    }

    selected.slice(0, 12).forEach((entry) => {
      window.open(entry.action_url, "_blank", "noopener,noreferrer");
    });
  };

  const toggleSelected = (entryId: string) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(entryId)) next.delete(entryId);
      else next.add(entryId);
      return next;
    });
  };

  const toggleAllVisible = () => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (allVisibleSelected) {
        visibleEntries.forEach((entry) => next.delete(entry.id));
      } else {
        visibleEntries.forEach((entry) => next.add(entry.id));
      }
      return next;
    });
  };

  const filterItems: Array<{
    key: FilterKey;
    label: string;
    count: number;
    activeClass: string;
  }> = [
    {
      key: "all",
      label: "Все",
      count: entries.length,
      activeClass: "bg-blue-600 text-white shadow-blue-200",
    },
    {
      key: "top",
      label: "ТОП",
      count: topEntries.length,
      activeClass: "bg-indigo-600 text-white shadow-indigo-200",
    },
    {
      key: "captcha",
      label: "CAPTCHA",
      count: captchaEntries.length,
      activeClass: "bg-amber-500 text-white shadow-amber-200",
    },
    {
      key: "ready",
      label: "Готовые",
      count: readyCount,
      activeClass: "bg-emerald-600 text-white shadow-emerald-200",
    },
    {
      key: "errors",
      label: "Ошибки",
      count: errorCount,
      activeClass: "bg-rose-600 text-white shadow-rose-200",
    },
  ];

  if (loading) {
    return (
      <main className="min-h-screen bg-[#F5F8FC] text-slate-950">
        <div className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6">
          <div className="h-16 animate-pulse rounded-3xl bg-white shadow-sm" />
          <div className="mt-5 grid gap-4 xl:grid-cols-[230px_minmax(0,1fr)]">
            <div className="hidden h-[620px] animate-pulse rounded-3xl bg-white xl:block" />
            <div className="space-y-4">
              <div className="h-40 animate-pulse rounded-[28px] bg-white" />
              <div className="h-24 animate-pulse rounded-3xl bg-white" />
              {[1, 2, 3, 4, 5].map((row) => (
                <div
                  key={row}
                  className="h-20 animate-pulse rounded-3xl bg-white"
                />
              ))}
            </div>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#F5F8FC] text-slate-950">
      <header className="sticky top-0 z-50 border-b border-slate-200/80 bg-white/95 backdrop-blur-xl">
        <div className="mx-auto flex h-[72px] max-w-[1720px] items-center gap-4 px-4 sm:px-6 xl:px-8">
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            className="flex h-11 w-11 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-600 shadow-sm xl:hidden"
            aria-label="Открыть меню"
          >
            <Menu className="h-5 w-5" />
          </button>

          <BorisLogo />

          <nav className="ml-6 hidden items-center gap-1 lg:flex">
            <a
              href="/seo-checkpoints"
              className="flex items-center gap-2 rounded-2xl bg-blue-50 px-4 py-2.5 text-sm font-bold text-blue-700"
            >
              <ListChecks className="h-4 w-4" />
              Очередь
            </a>
            <a
              href="/dashboard"
              className="flex items-center gap-2 rounded-2xl px-4 py-2.5 text-sm font-bold text-slate-500 transition hover:bg-slate-50 hover:text-slate-900"
            >
              <BarChart3 className="h-4 w-4" />
              Статистика
            </a>
            <a
              href="/settings"
              className="flex items-center gap-2 rounded-2xl px-4 py-2.5 text-sm font-bold text-slate-500 transition hover:bg-slate-50 hover:text-slate-900"
            >
              <Settings className="h-4 w-4" />
              Настройки
            </a>
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <div className="hidden items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-3.5 py-2 md:flex">
              <ChromeMark size={32} />
              <div>
                <div
                  className={
                    "flex items-center gap-1.5 text-xs font-black " +
                    (extensionConnected ? "text-emerald-700" : "text-amber-700")
                  }
                >
                  <span
                    className={
                      "h-2 w-2 rounded-full " +
                      (extensionConnected ? "bg-emerald-500" : "bg-amber-400")
                    }
                  />
                  {extensionConnected
                    ? "Расширение подключено"
                    : "Расширение не найдено"}
                </div>
                <div className="mt-0.5 text-[10px] font-semibold text-slate-400">
                  BORIS Checkpoint Autofill
                </div>
              </div>
            </div>

            <a
              href="/settings"
              className="hidden h-11 w-11 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-500 transition hover:border-blue-200 hover:text-blue-600 sm:flex"
              aria-label="Настройки"
            >
              <Settings className="h-5 w-5" />
            </a>

            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-indigo-500 text-sm font-black text-white shadow-md">
              K
            </div>
          </div>
        </div>
      </header>

      {sidebarOpen && (
        <div className="fixed inset-0 z-[60] xl:hidden">
          <button
            aria-label="Закрыть меню"
            className="absolute inset-0 bg-slate-950/25 backdrop-blur-sm"
            onClick={() => setSidebarOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 w-[290px] overflow-y-auto bg-white p-5 shadow-2xl">
            <div className="mb-6 flex items-center justify-between">
              <BorisLogo />
              <button
                type="button"
                onClick={() => setSidebarOpen(false)}
                className="flex h-10 w-10 items-center justify-center rounded-xl bg-slate-100 text-slate-500"
                aria-label="Закрыть меню"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="space-y-1">
              <SidebarItem
                icon={ListChecks}
                label="Очередь проверок"
                href="/seo-checkpoints"
                active
              />
              <SidebarItem icon={Folder} label="Мои проекты" href="/dashboard" />
              <SidebarItem
                icon={BarChart3}
                label="Статистика"
                href="/dashboard"
              />
              <SidebarItem icon={Settings} label="Настройки" href="/settings" />
            </div>
          </aside>
        </div>
      )}

      <div className="mx-auto grid max-w-[1720px] gap-5 px-4 py-5 sm:px-6 xl:grid-cols-[230px_minmax(0,1fr)] xl:px-8">
        <aside className="hidden xl:block">
          <div className="sticky top-[92px] space-y-4">
            <section className="rounded-[28px] border border-slate-200 bg-white p-3 shadow-[0_16px_45px_rgba(15,23,42,0.05)]">
              <div className="space-y-1">
                <SidebarItem
                  icon={ListChecks}
                  label="Очередь проверок"
                  href="/seo-checkpoints"
                  active
                />
                <SidebarItem icon={Folder} label="Мои проекты" href="/dashboard" />
                <SidebarItem
                  icon={BarChart3}
                  label="Статистика"
                  href="/dashboard"
                />
                <SidebarItem icon={Settings} label="Настройки" href="/settings" />
              </div>
            </section>

            <section
              className={
                "rounded-[28px] border p-5 shadow-[0_16px_45px_rgba(15,23,42,0.04)] " +
                (extensionConnected
                  ? "border-emerald-200 bg-gradient-to-br from-emerald-50 to-white"
                  : "border-amber-200 bg-gradient-to-br from-amber-50 to-white")
              }
            >
              <div className="flex items-center gap-3">
                <ChromeMark size={40} />
                <div
                  className={
                    "text-sm font-black " +
                    (extensionConnected ? "text-emerald-700" : "text-amber-700")
                  }
                >
                  {extensionConnected ? "Расширение установлено" : "Нужно расширение"}
                </div>
              </div>
              <p className="mt-3 text-xs font-medium leading-5 text-slate-500">
                BORIS Checkpoint Autofill заполняет обычные поля и экономит время
                на ручной работе.
              </p>
              <a
                href="/downloads/boris-seo-checkpoint-autofill.zip"
                className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-xs font-black text-slate-700 transition hover:border-blue-200 hover:text-blue-700"
              >
                <Download className="h-4 w-4" />
                {extensionConnected ? "Скачать ещё раз" : "Скачать расширение"}
              </a>
            </section>

            <section className="rounded-[24px] border border-blue-100 bg-white p-4 shadow-[0_16px_45px_rgba(15,23,42,0.04)]">
              <div className="flex items-center gap-2 text-sm font-black text-slate-900">
                <ShieldCheck className="h-4 w-4 text-blue-600" />
                Центр доступен
              </div>
              <p className="mt-2 text-xs font-medium leading-5 text-slate-500">
                CAPTCHA, Terms и реальные данные остаются только под твоим
                контролем.
              </p>
            </section>

            <div className="px-3 pt-3 text-xs font-medium leading-5 text-slate-400">
              © 2026 BORIS
              <br />
              SEO Checkpoint Center
              <br />
              v1.0.0
            </div>
          </div>
        </aside>

        <section className="min-w-0">
          <section className="overflow-hidden rounded-[30px] border border-blue-100 bg-gradient-to-br from-white via-blue-50/55 to-sky-50/70 shadow-[0_22px_55px_rgba(37,99,235,0.07)]">
            <div className="grid gap-6 p-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center lg:p-7">
              <div className="min-w-0">
                <div className="text-[11px] font-black uppercase tracking-[0.22em] text-blue-600">
                  BORIS SEO CHECKPOINT CENTER
                </div>
                <div className="mt-2 flex flex-col gap-3 lg:flex-row lg:items-end lg:gap-8">
                  <h1 className="text-3xl font-black tracking-[-0.035em] text-slate-950 sm:text-[38px]">
                    Очередь проверок
                  </h1>
                  <p className="max-w-xl text-sm font-medium leading-6 text-slate-500">
                    Проходи только CAPTCHA, вводи код и подтверждай реальные
                    данные. BORIS заполняет обычные поля, перепроверяет площадку и
                    продолжает публикацию.
                  </p>
                </div>
              </div>

              <div className="grid min-w-[320px] grid-cols-3 gap-2.5">
                <div className="rounded-2xl border border-white/80 bg-white/90 p-3 shadow-sm">
                  <div className="flex items-center gap-2">
                    <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-50 text-violet-600">
                      <LayoutGrid className="h-5 w-5" />
                    </div>
                    <div>
                      <div className="text-xl font-black text-slate-950">
                        {data?.summary?.unique_platforms || 0}
                      </div>
                      <div className="text-[10px] font-bold leading-3 text-slate-400">
                        уникальных
                        <br />
                        площадок
                      </div>
                    </div>
                  </div>
                </div>

                <div className="rounded-2xl border border-white/80 bg-white/90 p-3 shadow-sm">
                  <div className="flex items-center gap-2">
                    <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600">
                      <ListChecks className="h-5 w-5" />
                    </div>
                    <div>
                      <div className="text-xl font-black text-slate-950">
                        {data?.summary?.entries || 0}
                      </div>
                      <div className="text-[10px] font-bold leading-3 text-slate-400">
                        конкретных
                        <br />
                        вкладок
                      </div>
                    </div>
                  </div>
                </div>

                <div className="rounded-2xl border border-white/80 bg-white/90 p-3 shadow-sm">
                  <div className="flex h-full items-center gap-2">
                    <ChromeMark size={35} />
                    <div className="min-w-0">
                      <div
                        className={
                          "text-[11px] font-black leading-4 " +
                          (extensionConnected
                            ? "text-emerald-700"
                            : "text-amber-700")
                        }
                      >
                        {extensionConnected ? "Подключено" : "Не найдено"}
                      </div>
                      <div className="mt-0.5 text-[9px] font-bold text-slate-400">
                        Autofill
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <div className="sticky top-[72px] z-30 -mx-2 mt-3 bg-[#F5F8FC]/95 px-2 py-3 backdrop-blur-xl">
            <section className="rounded-[24px] border border-slate-200 bg-white p-3 shadow-[0_12px_32px_rgba(15,23,42,0.05)]">
              <div className="flex flex-wrap gap-2.5">
                <button
                  type="button"
                  onClick={() => openEntries(topEntries)}
                  className="inline-flex h-11 items-center gap-2 rounded-2xl bg-gradient-to-r from-blue-600 to-blue-500 px-5 text-sm font-black text-white shadow-[0_8px_22px_rgba(37,99,235,0.24)] transition hover:-translate-y-0.5 hover:shadow-[0_12px_28px_rgba(37,99,235,0.30)]"
                >
                  <Play className="h-4 w-4 fill-current" />
                  Открыть ТОП-5
                  <span className="rounded-full bg-white/20 px-2 py-0.5 text-[10px]">
                    {topEntries.length}
                  </span>
                </button>

                <button
                  type="button"
                  onClick={() => openEntries(captchaEntries)}
                  className="inline-flex h-11 items-center gap-2 rounded-2xl border border-blue-200 bg-white px-5 text-sm font-black text-blue-700 transition hover:border-blue-300 hover:bg-blue-50"
                >
                  <Puzzle className="h-4 w-4" />
                  CAPTCHA
                  <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] text-blue-700">
                    {captchaEntries.length}
                  </span>
                </button>

                <button
                  type="button"
                  onClick={() => openEntries(entries)}
                  className="inline-flex h-11 items-center gap-2 rounded-2xl border border-slate-200 bg-white px-5 text-sm font-black text-slate-700 transition hover:border-blue-200 hover:text-blue-700"
                >
                  <LayoutGrid className="h-4 w-4" />
                  Открыть все
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px]">
                    {entries.length}
                  </span>
                </button>

                <button
                  type="button"
                  onClick={load}
                  className="inline-flex h-11 items-center gap-2 rounded-2xl border border-slate-200 bg-white px-5 text-sm font-black text-slate-700 transition hover:border-blue-200 hover:text-blue-700"
                >
                  <RefreshCw className="h-4 w-4" />
                  Обновить
                </button>

                <a
                  href="/downloads/boris-seo-checkpoint-autofill.zip"
                  className="ml-auto inline-flex h-11 items-center gap-2 rounded-2xl border border-slate-200 bg-white px-5 text-sm font-black text-slate-700 transition hover:border-emerald-200 hover:bg-emerald-50 hover:text-emerald-700"
                >
                  <Download className="h-4 w-4" />
                  <span className="hidden sm:inline">Скачать расширение</span>
                </a>
              </div>

              <div className="mt-3 flex flex-col gap-2.5 xl:flex-row xl:items-center">
                <label className="relative min-w-0 flex-1">
                  <Search className="pointer-events-none absolute left-4 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-slate-400" />
                  <input
                    ref={searchRef}
                    type="search"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Поиск по домену, названию или проекту..."
                    className="h-11 w-full rounded-2xl border border-slate-200 bg-slate-50/70 pl-11 pr-16 text-sm font-semibold text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-100"
                  />
                  <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded-lg border border-slate-200 bg-white px-2 py-1 text-[10px] font-black text-slate-400 shadow-sm">
                    ⌘ K
                  </span>
                </label>

                <div className="flex min-w-0 gap-1.5 overflow-x-auto pb-1 xl:pb-0">
                  {filterItems.map((item) => {
                    const active = filter === item.key;
                    return (
                      <button
                        key={item.key}
                        type="button"
                        onClick={() => setFilter(item.key)}
                        className={
                          "whitespace-nowrap rounded-2xl px-3.5 py-2.5 text-xs font-black shadow-sm transition " +
                          (active
                            ? item.activeClass
                            : "bg-slate-100 text-slate-500 hover:bg-slate-200")
                        }
                      >
                        {item.label} ({item.count})
                      </button>
                    );
                  })}
                </div>

                <div className="flex gap-2 overflow-x-auto pb-1 xl:pb-0">
                  <SelectBox
                    ariaLabel="Фильтр по проекту"
                    value={projectFilter}
                    onChange={setProjectFilter}
                  >
                    <option value="all">Все проекты</option>
                    {projectOptions.map((project) => (
                      <option key={project} value={project}>
                        {project}
                      </option>
                    ))}
                  </SelectBox>

                  <SelectBox
                    ariaLabel="Сортировка"
                    value={sortMode}
                    onChange={(value) => setSortMode(value as SortMode)}
                  >
                    <option value="priority">Сначала приоритет</option>
                    <option value="iks">Сначала ИКС</option>
                    <option value="name">По названию</option>
                  </SelectBox>
                </div>
              </div>
            </section>

            {selectedIds.size > 0 && (
              <section className="mt-2 flex flex-wrap items-center gap-3 rounded-2xl border border-blue-200 bg-blue-50 px-4 py-3 shadow-sm">
                <div className="text-sm font-black text-blue-900">
                  Выбрано: {selectedIds.size}
                </div>
                <button
                  type="button"
                  onClick={() => openEntries(selectedEntries)}
                  className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-xs font-black text-white transition hover:bg-blue-700"
                >
                  <ExternalLink className="h-4 w-4" />
                  Открыть выбранные
                </button>
                <button
                  type="button"
                  onClick={() => setSelectedIds(new Set())}
                  className="rounded-xl px-3 py-2 text-xs font-black text-blue-700 hover:bg-blue-100"
                >
                  Снять выбор
                </button>
              </section>
            )}
          </div>

          {error && (
            <div className="mb-3 flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-700">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <section className="rounded-[28px] border border-slate-200 bg-white shadow-[0_18px_50px_rgba(15,23,42,0.05)]">
            <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
              <div>
                <div className="flex items-center gap-2 text-base font-black text-slate-950">
                  <ListChecks className="h-5 w-5 text-blue-600" />
                  Очередь проверок
                </div>
                <div className="mt-1 text-xs font-semibold text-slate-400">
                  Показано {visibleEntries.length} из {entries.length}
                </div>
              </div>
              <div className="hidden items-center gap-2 text-xs font-semibold text-slate-400 sm:flex">
                <Sparkles className="h-4 w-4 text-blue-500" />
                Сначала самые полезные действия
              </div>
            </div>

            {visibleEntries.length === 0 ? (
              <div className="flex min-h-[360px] flex-col items-center justify-center px-6 text-center">
                <div className="flex h-16 w-16 items-center justify-center rounded-3xl bg-blue-50 text-blue-600">
                  <Search className="h-7 w-7" />
                </div>
                <div className="mt-5 text-xl font-black text-slate-950">
                  Ничего не найдено
                </div>
                <p className="mt-2 max-w-sm text-sm font-medium leading-6 text-slate-500">
                  Попробуй изменить фильтр, проект или строку поиска.
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setQuery("");
                    setFilter("all");
                    setProjectFilter("all");
                  }}
                  className="mt-5 rounded-2xl bg-blue-600 px-5 py-2.5 text-sm font-black text-white"
                >
                  Сбросить фильтры
                </button>
              </div>
            ) : (
              <>
                <div className="hidden xl:block">
                  <div className="grid grid-cols-[38px_30px_minmax(135px,1.05fr)_minmax(180px,1.45fr)_minmax(130px,1fr)_130px_95px_105px_26px] items-center gap-2.5 border-b border-slate-100 bg-slate-50/70 px-4 py-3 text-[10px] font-black uppercase tracking-[0.08em] text-slate-400">
                    <label className="flex items-center justify-center">
                      <input
                        type="checkbox"
                        checked={allVisibleSelected}
                        onChange={toggleAllVisible}
                        className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                        aria-label="Выбрать все видимые"
                      />
                    </label>
                    <div>#</div>
                    <div>Площадка</div>
                    <div>Задача / описание</div>
                    <div>Проект(ы)</div>
                    <div>Статус</div>
                    <div>Приоритет</div>
                    <div>Действия</div>
                    <div />
                  </div>

                  <div className="divide-y divide-slate-100">
                    {visibleEntries.map((entry, index) => {
                      const status =
                        entryStatuses.get(entry.id) ||
                        queueStatus(
                          entry,
                          statuses[entry.id],
                          serverResults[entry.id]
                        );
                      const priority = priorityMeta(entry);
                      const projects = projectsFor(entry);
                      const checked = selectedIds.has(entry.id);
                      const server = serverResults[entry.id];

                      return (
                        <div
                          key={entry.id}
                          className={
                            "group grid grid-cols-[38px_30px_minmax(135px,1.05fr)_minmax(180px,1.45fr)_minmax(130px,1fr)_130px_95px_105px_26px] items-center gap-2.5 px-4 py-3.5 transition " +
                            (checked
                              ? "bg-blue-50/80"
                              : "bg-white hover:bg-blue-50/35")
                          }
                        >
                          <label className="flex items-center justify-center">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggleSelected(entry.id)}
                              className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                              aria-label={`Выбрать ${entry.name}`}
                            />
                          </label>

                          <div className="text-xs font-black text-slate-400">
                            {index + 1}
                          </div>

                          <div className="flex min-w-0 items-center gap-3">
                            <PlatformMark entry={entry} />
                            <div className="min-w-0">
                              <div className="truncate text-sm font-black text-slate-900">
                                {entry.name}
                              </div>
                              <div className="mt-0.5 truncate text-[11px] font-semibold text-slate-400">
                                {entry.host}
                              </div>
                            </div>
                          </div>

                          <div className="min-w-0">
                            <div className="truncate text-sm font-bold text-slate-800">
                              {checkpointLabel(entry.checkpoint)}
                            </div>
                            <div className="mt-1 line-clamp-1 text-[11px] font-medium text-slate-400">
                              {entry.action || entry.after_completion || "Ручное подтверждение"}
                            </div>
                            {server?.error && (
                              <div className="mt-1 truncate text-[10px] font-semibold text-rose-600">
                                {server.error}
                              </div>
                            )}
                          </div>

                          <div className="flex min-w-0 flex-wrap gap-1.5">
                            {entry.top_5_platform && <Pill tone="blue">ТОП</Pill>}
                            {String(entry.checkpoint || "").includes("captcha") && (
                              <Pill tone="amber">CAPTCHA</Pill>
                            )}
                            <Pill
                              tone={
                                entry.iks_tier === "high"
                                  ? "violet"
                                  : entry.iks_tier === "medium"
                                  ? "blue"
                                  : "slate"
                              }
                            >
                              {tierLabel(entry.iks_tier)}
                              {entry.iks ? ` · ИКС ${entry.iks}` : ""}
                            </Pill>
                            {projects.slice(0, 1).map((project) => (
                              <Pill key={project} tone="slate">
                                {project.replace(".ru", "").replace(".pro", "")}
                              </Pill>
                            ))}
                            {projects.length > 1 && (
                              <Pill tone="slate">+{projects.length - 1}</Pill>
                            )}
                            {entry.flow_kind === "account_onboarding" && (
                              <Pill tone="green">1 аккаунт</Pill>
                            )}
                          </div>

                          <StatusBadge status={status} />

                          <div className="flex items-center gap-2">
                            <PriorityBars
                              bars={priority.bars}
                              color={priority.color}
                            />
                            <span className="text-[11px] font-bold text-slate-600">
                              {priority.label}
                            </span>
                          </div>

                          <button
                            type="button"
                            onClick={() => openEntries([entry])}
                            className="inline-flex h-9 items-center justify-center gap-2 rounded-xl border border-blue-200 bg-white px-3 text-xs font-black text-blue-700 shadow-sm transition hover:border-blue-500 hover:bg-blue-600 hover:text-white"
                          >
                            <ExternalLink className="h-3.5 w-3.5" />
                            Открыть
                          </button>

                          <a
                            href={entry.action_url}
                            target="_blank"
                            rel="noreferrer"
                            className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                            title="Открыть без расширения"
                          >
                            <MoreVertical className="h-4 w-4" />
                          </a>
                        </div>
                      );
                    })}
                  </div>
                </div>

                <div className="grid gap-3 p-3 xl:hidden">
                  {visibleEntries.map((entry, index) => {
                    const status =
                      entryStatuses.get(entry.id) ||
                      queueStatus(
                        entry,
                        statuses[entry.id],
                        serverResults[entry.id]
                      );
                    const priority = priorityMeta(entry);
                    const projects = projectsFor(entry);
                    const checked = selectedIds.has(entry.id);

                    return (
                      <article
                        key={entry.id}
                        className={
                          "rounded-3xl border p-4 shadow-sm transition " +
                          (checked
                            ? "border-blue-300 bg-blue-50"
                            : "border-slate-200 bg-white")
                        }
                      >
                        <div className="flex items-start gap-3">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleSelected(entry.id)}
                            className="mt-1 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                            aria-label={`Выбрать ${entry.name}`}
                          />
                          <PlatformMark entry={entry} />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="text-[11px] font-black text-slate-400">
                                #{index + 1}
                              </span>
                              <h2 className="truncate text-sm font-black text-slate-900">
                                {entry.name}
                              </h2>
                            </div>
                            <div className="mt-0.5 truncate text-[11px] font-semibold text-slate-400">
                              {entry.host}
                            </div>
                          </div>
                          <StatusBadge status={status} />
                        </div>

                        <div className="mt-4 rounded-2xl bg-slate-50 p-3">
                          <div className="text-sm font-bold text-slate-800">
                            {checkpointLabel(entry.checkpoint)}
                          </div>
                          <p className="mt-1 text-xs font-medium leading-5 text-slate-500">
                            {entry.action ||
                              entry.after_completion ||
                              "Ручное подтверждение"}
                          </p>
                        </div>

                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {entry.top_5_platform && <Pill tone="blue">ТОП</Pill>}
                          {String(entry.checkpoint || "").includes("captcha") && (
                            <Pill tone="amber">CAPTCHA</Pill>
                          )}
                          <Pill tone="slate">
                            {tierLabel(entry.iks_tier)}
                            {entry.iks ? ` · ИКС ${entry.iks}` : ""}
                          </Pill>
                          {projects.map((project) => (
                            <Pill key={project} tone="slate">
                              {project}
                            </Pill>
                          ))}
                        </div>

                        <div className="mt-4 flex items-center justify-between gap-3 border-t border-slate-100 pt-4">
                          <div className="flex items-center gap-2">
                            <PriorityBars
                              bars={priority.bars}
                              color={priority.color}
                            />
                            <span className="text-xs font-bold text-slate-500">
                              {priority.label}
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={() => openEntries([entry])}
                            className="inline-flex h-10 items-center gap-2 rounded-xl bg-blue-600 px-4 text-xs font-black text-white shadow-sm"
                          >
                            <ExternalLink className="h-4 w-4" />
                            Открыть
                          </button>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </>
            )}
          </section>

          <div className="mt-4 flex flex-col gap-2 rounded-2xl border border-amber-200 bg-amber-50/80 px-4 py-3 text-xs font-semibold leading-5 text-amber-800 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-2">
              <CircleDot className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                CAPTCHA не решается автоматически. BORIS не принимает Terms, не
                придумывает возраст, адрес или личность.
              </span>
            </div>
            <div className="shrink-0 font-black text-amber-900">
              Контроль остаётся у тебя
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
