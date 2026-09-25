"use client";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

import { useRouter } from "next/navigation";
import CRMContextPanel from "./CRMContextPanel";
import ManagerWorkspacePanel from "./ManagerWorkspacePanel";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

type SlotSetup = {
  id?: number;
  status?: string;
  account_id?: string;
  account_name?: string;
};

type Acc = {
  account_id: string;
  name: string;
  phone?: string;
  status?: string;
  sync_status?: string;
  last_sync?: string | null;
  last_sync_error?: string;
  platform?: string;
  mop_enabled?: boolean;
  mop_mode?: string;
  unread?: number;
  attention?: number;
};

type Dlg = {
  account_id: string;
  account_name: string;
  avito_chat_id: string;
  client_name: string;
  item_title?: string;
  last_text?: string;
  last_at?: string;
  unread?: number;
  is_purchase?: boolean;
};

type Msg = {
  id?: string | number;
  text?: string;
  body?: string;
  message?: string;
  direction?: string;
  sender?: string;
  at?: string;
  created_at?: string;
  is_read?: boolean | null;
  read_at_iso?: string | null;
  author_kind?: string;
  author_label?: string;
};

type Thread = {
  account_id: string;
  account_name?: string;
  avito_chat_id: string;
  client_name?: string;
  dialog?: {
    assigned_user_id?: number | null;
    assigned_name?: string;
    assigned_mine?: boolean;
  };
  messages?: Msg[];
  items?: Msg[];
  draft?: {
    status?: string;
    text?: string;
    reason?: string;
    can_retry?: boolean;
    source?: string;
    draft_id?: string;
  } | null;
};

const API = "";

function tokenHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};

  const token = localStorage.getItem("boris_token");

  return token
    ? { Authorization: "Bearer " + token }
    : {};
}

function formatTime(value?: string) {
  if (!value) return "";

  try {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;

    return d.toLocaleTimeString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return value;
  }
}

async function apiGet<T = any>(url: string): Promise<T | null> {
  const response = await fetch(API + url, {
    headers: tokenHeaders(),
    cache: "no-store",
  });

  if (!response.ok) return null;

  return response.json();
}

async function apiPost<T = any>(
  url: string,
  body: unknown,
): Promise<T | null> {
  const response = await fetch(API + url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...tokenHeaders(),
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) return null;

  return response.json();
}

function Icon({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <span
      style={{
        width: 38,
        height: 38,
        minWidth: 38,
        borderRadius: 12,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#f5f7fb",
        fontSize: 20,
      }}
    >
      {children}
    </span>
  );
}

function ModuleCard({
  icon,
  title,
  subtitle,
  href,
  accent,
}: {
  icon: string;
  title: string;
  subtitle: string;
  href: string;
  accent: string;
}) {
  return (
    <a
      href={href}
      style={{
        flex: "1 1 170px",
        minWidth: 160,
        textDecoration: "none",
        color: "#111827",
        border: "1px solid #e5e7eb",
        borderRadius: 16,
        padding: 15,
        background: "#fff",
        display: "flex",
        alignItems: "center",
        gap: 12,
        boxShadow: "0 4px 14px rgba(15,23,42,.035)",
        transition: "transform .15s ease, box-shadow .15s ease",
      }}
    >
      <span
        style={{
          width: 42,
          height: 42,
          minWidth: 42,
          borderRadius: 13,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          background: accent,
          fontSize: 21,
        }}
      >
        {icon}
      </span>

      <span style={{ minWidth: 0 }}>
        <span
          style={{
            display: "block",
            fontWeight: 750,
            fontSize: 14,
            marginBottom: 3,
          }}
        >
          {title}
        </span>

        <span
          style={{
            display: "block",
            color: "var(--boris-text-muted)",
            fontSize: 13.5,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {subtitle}
        </span>
      </span>
    </a>
  );
}



// BORIS_R14_MESSAGES_ACCOUNT_STORAGE
function borisR14RememberAccount(accountId: string) {
  if (typeof window === "undefined") return;

  const value = String(accountId || "").trim();

  if (!value) return;

  const keys = [
    "boris_account_id",
    "account_id",
    "accountId",
    "current_account_id",
    "currentAccountId",
    "selected_account_id",
    "selectedAccountId",
    "boris_current_account_id",
  ];

  for (const key of keys) {
    try {
      window.localStorage.setItem(key, value);
    } catch {}
  }
}


// BORIS_R15_HARD_ACCOUNT_BRIDGE
function borisR15RememberSelectedAccount(value: unknown) {
  if (typeof window === "undefined") return;

  const accountId = String(value ?? "").trim();

  if (!accountId) return;

  const keys = [
    "boris_account_id",
    "account_id",
    "accountId",
    "current_account_id",
    "currentAccountId",
    "selected_account_id",
    "selectedAccountId",
    "boris_current_account_id",
  ];

  for (const key of keys) {
    try {
      window.localStorage.setItem(key, accountId);
    } catch {}
  }
}


// BORIS_R16_MOP_ACCOUNT_NAV
function borisR16MopHref(accountId: string): string {
  const value = String(accountId || "").trim();

  if (!value || value === "all") {
    return "/messages/mop-training";
  }

  return (
    "/messages/mop-training?account_id=" +
    encodeURIComponent(value)
  );
}

export default function MessagesPage() {
  const [accounts, setAccounts] = useState<Acc[]>([]);
  const [setupSlots, setSetupSlots] = useState<SlotSetup[]>([]);
  const [dialogs, setDialogs] = useState<Dlg[]>([]);
  const [dialogRenderLimit, setDialogRenderLimit] = useState(80);
  const [thread, setThread] = useState<Thread | null>(null);
  const [threadKey, setThreadKey] = useState("");
  const [restoreThreadKey, setRestoreThreadKey] = useState("");

  const [scope, setScope] = useState("all");
  const [mopSettingsSummary, setMopSettingsSummary] = useState("Выберите аккаунт");
  const [mopBoundCount, setMopBoundCount] = useState(0);
  const [quickActionsCollapsed, setQuickActionsCollapsed] = useState(false);
  const [quickActionsSettingsOpen, setQuickActionsSettingsOpen] = useState(false);
  const [hiddenQuickActions, setHiddenQuickActions] = useState<string[]>([]);

  const quickActionOptions = [
    ["crm", "Клиенты и сделки"],
    ["training", "Тренировка МОП"],
    ["review", "Разбор диалогов МОП"],
    ["reactivation", "Реактивация клиентов"],
    ["settings", "Настройки МОП"],
    ["connect", "Подключение аккаунта"],
  ] as const;

  const persistQuickActions = useCallback((hidden: string[], collapsed = quickActionsCollapsed) => {
    try {
      localStorage.setItem("boris_messages_hidden_quick_actions", JSON.stringify(hidden));
      localStorage.setItem("boris_messages_quick_actions_collapsed", collapsed ? "1" : "0");
    } catch {}
  }, [quickActionsCollapsed]);

  const toggleQuickAction = useCallback((key: string) => {
    setHiddenQuickActions((prev) => {
      const next = prev.includes(key) ? prev.filter((x) => x !== key) : [...prev, key];
      persistQuickActions(next);
      return next;
    });
  }, [persistQuickActions]);

  const scopedHref = useCallback((path: string) => {
    if (!scope || scope === "all") return path;
    const q = new URLSearchParams();
    q.set("account_id", scope);
    if (thread?.account_id === scope && thread?.avito_chat_id) q.set("chat_id", thread.avito_chat_id);
    const sep = path.includes("?") ? "&" : "?";
    return `${path}${sep}${q.toString()}`;
  }, [scope, thread?.account_id, thread?.avito_chat_id]);

  useEffect(() => {
    if (!scope || scope === "all") { setMopSettingsSummary("Выберите аккаунт"); return; }
    apiGet<any>(`/api/messenger/sales/settings?account_id=${encodeURIComponent(scope)}`).then((d) => {
      if (!d || d.status !== "ok") { setMopSettingsSummary("Состояние недоступно"); return; }
      if (d.enabled === false) { setMopSettingsSummary("МОП выключен"); return; }
      const ws = d.work_schedule || {};
      if (!ws.enabled) { setMopSettingsSummary("МОП включён · 24/7"); return; }
      const names = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"];
      const days = (ws.days || []).map((x:number)=>names[x]).filter(Boolean).join(", ");
      setMopSettingsSummary(`МОП включён · ${days || "—"}, ${ws.start || "—"}–${ws.end || "—"}`);
    }).catch(() => setMopSettingsSummary("Состояние недоступно"));
  }, [scope]);
  useEffect(() => {
    apiGet<any>("/api/ai/accounts?product=mop").then((d) => {
      const rows = Array.isArray(d?.accounts) ? d.accounts : [];
      setMopBoundCount(rows.filter((x:any)=>x?.bound).length);
    }).catch(() => setMopBoundCount(0));
  }, [scope]);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedAccount = params.get("account_id") || "";
    const requestedChat = params.get("chat_id") || "";
    const requestedAttention = params.get("attention") || "";
    let savedAccount = "";
    let savedThread = "";
    try {
      savedAccount = localStorage.getItem("boris_account_id") || "";
      savedThread = localStorage.getItem("boris_messages_selected_thread") || "";
    } catch {}
    const initialAccount = requestedAccount || savedAccount;
    if (initialAccount) setScope(initialAccount);
    if (requestedChat && initialAccount) {
      setRestoreThreadKey(`${initialAccount}:${requestedChat}`);
    } else if (savedThread) {
      setRestoreThreadKey(savedThread);
    }
    if (requestedAttention) setManagerQueueFilter(requestedAttention === "1" ? "all" : requestedAttention);
  }, []);
  useEffect(() => {
    try {
      const rawHidden = localStorage.getItem("boris_messages_hidden_quick_actions");
      const parsed = rawHidden ? JSON.parse(rawHidden) : [];
      if (Array.isArray(parsed)) setHiddenQuickActions(parsed.map(String));
      setQuickActionsCollapsed(localStorage.getItem("boris_messages_quick_actions_collapsed") === "1");
    } catch {}
  }, []);
  const [managerQueue, setManagerQueue] = useState<any[]>([]);
  const [managerQueueSummary, setManagerQueueSummary] = useState<any>({});
  const [managerQueueFilter, setManagerQueueFilter] = useState("all");
  const [managerSkipped, setManagerSkipped] = useState<string[]>([]);
  const [managerQueueAccountId, setManagerQueueAccountId] = useState("");

  // BORIS_R212_CRM_ROUTER
  const borisR212Router = useRouter();

  useEffect(() => {
    const routes: Record<string, string> = {
      "Клиенты": "/dashboard/crm/workspace?tab=clients",
      "Сделки": "/dashboard/crm/workspace?tab=deals",
      "Задачи": "/dashboard/crm/workspace?tab=tasks",
      "Календарь": "/dashboard/crm/workspace?tab=calendar",
      "Воронка продаж": "/dashboard/crm/workspace?tab=pipeline",
    };

    const handler = (event: MouseEvent) => {
      let node = event.target as HTMLElement | null;

      for (
        let i = 0;
        i < 8 && node;
        i++,
        node = node.parentElement
      ) {
        const text = (node.innerText || "")
          .replace(/\s+/g, " ")
          .trim();

        for (const [label, url] of Object.entries(routes)) {
          if (!text.startsWith(label)) continue;

          const card =
            text.includes("Все клиенты") ||
            text.includes("Воронка и сделки") ||
            text.includes("Мои задачи") ||
            text.includes("Встречи и события") ||
            text.includes("Аналитика и отчёты") ||
            text.includes("Аналитика и отчеты");

          if (!card) continue;

          event.preventDefault();
          event.stopPropagation();

          borisR212Router.push(url);
          return;
        }
      }
    };

    document.addEventListener(
      "click",
      handler,
      true,
    );

    return () =>
      document.removeEventListener(
        "click",
        handler,
        true,
      );
  }, [borisR212Router]);

  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");

  useEffect(() => {
    setDialogRenderLimit(80);
  }, [scope, query]);
  const [mopSuggestions, setMopSuggestions] = useState<Array<{label:string;text:string}>>([]);
  const [suggestionsBusy, setSuggestionsBusy] = useState(false);

  useEffect(() => {
    const focusReply = (event: Event) => {
      const d = (event as CustomEvent).detail || {};
      if (d.kind !== "focus_reply" || d.account_id !== thread?.account_id || d.avito_chat_id !== thread?.avito_chat_id) return;
      const el = document.getElementById("boris-message-reply") as HTMLTextAreaElement | null;
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      el?.focus();
    };
    window.addEventListener("boris-manager-primary-action", focusReply as EventListener);
    return () => window.removeEventListener("boris-manager-primary-action", focusReply as EventListener);
  }, [thread?.account_id, thread?.avito_chat_id]);

  const [loadingAccounts, setLoadingAccounts] = useState(false);
  const [loadingDialogs, setLoadingDialogs] = useState(false);
  const [loadingThread, setLoadingThread] = useState(false);
  const [sending, setSending] = useState(false);
  const [loadError, setLoadError] = useState("");
  const needsAccountConnection = setupSlots.some((slot) =>
    ["paid_empty", "connecting", "error"].includes(String(slot.status || ""))
  );

  const loadAccounts = useCallback(async () => {
    setLoadingAccounts(true);
    setLoadError("");

    try {
      const [j, slotData] = await Promise.all([
        apiGet<any>("/api/inbox/accounts"),
        apiGet<any>("/api/inbox/slots"),
      ]);
      if (!j) throw new Error("Не удалось загрузить аккаунты");

      const list = j?.accounts || j?.items || j || [];
      setAccounts(Array.isArray(list) ? list : []);
      const slotList = slotData?.slots || [];
      setSetupSlots(Array.isArray(slotList) ? slotList : []);
    } catch (e) {
      setAccounts([]);
      setSetupSlots([]);
      setLoadError(e instanceof Error ? e.message : "Не удалось загрузить аккаунты");
    } finally {
      setLoadingAccounts(false);
    }
  }, []);

  const loadDialogs = useCallback(async (silent = false) => {
    if (!silent) {
      setLoadingDialogs(true);
      setLoadError("");
    }

    try {
      const url =
        `/api/inbox/dialogs?account_id=${encodeURIComponent(scope)}` +
        `&q=${encodeURIComponent(query)}`;

      const j = await apiGet<any>(url);
      if (!j) throw new Error("Не удалось загрузить диалоги");

      const list =
        j?.dialogs ||
        j?.items ||
        j ||
        [];

      setDialogs(Array.isArray(list) ? list : []);
      if (silent) setLoadError("");
    } catch (e) {
      if (!silent) {
        setDialogs([]);
        setLoadError(e instanceof Error ? e.message : "Не удалось загрузить диалоги");
      }
    } finally {
      if (!silent) setLoadingDialogs(false);
    }
  }, [scope, query]);

  const loadThread = useCallback(
    async (accountId: string, chatId: string, silent = false) => {
      if (!silent) {
        setLoadingThread(true);
        setMopSuggestions([]);
        setThread(null);
        setThreadKey("");
      }

      try {
        const j = await apiGet<any>(
          `/api/inbox/thread?account_id=${encodeURIComponent(accountId)}` +
          `&avito_chat_id=${encodeURIComponent(chatId)}`,
        );
        if (!j) throw new Error("Не удалось открыть диалог");

        const payload = j?.dialog ? { ...j, ...j.dialog, dialog: j.dialog } : j;
        if (payload?.account_id === accountId && payload?.avito_chat_id === chatId) {
          setThread((current) => {
            if (silent && current && (current.account_id !== accountId || current.avito_chat_id !== chatId)) return current;
            if (silent && current) {
              const authoritative: Msg[] = payload.messages || payload.items || [];
              const localPending = (current.messages || current.items || []).filter(
                (m: Msg) => String(m.id || "").startsWith("local:"),
              );
              const unconfirmed = localPending.filter((local: Msg) => {
                const localText = (local.text || local.body || local.message || "").trim();
                return !authoritative.some((remote: Msg) => {
                  const remoteText = (remote.text || remote.body || remote.message || "").trim();
                  const outgoing = remote.direction === "out" || remote.direction === "outgoing" || remote.sender === "manager" || remote.sender === "business";
                  return outgoing && remoteText === localText;
                });
              });
              return unconfirmed.length ? { ...payload, messages: [...authoritative, ...unconfirmed] } : payload;
            }
            return payload;
          });
          setThreadKey(`${accountId}:${chatId}`);
          try {
            localStorage.setItem("boris_messages_selected_thread", `${accountId}:${chatId}`);
            localStorage.setItem("boris_account_id", accountId);
          } catch {}
          if (silent) setLoadError("");
        } else if (!silent) {
          setThread(null);
          setThreadKey("");
          throw new Error("Получен контекст другого диалога");
        }
      } catch (e) {
        if (!silent) {
          setThread(null);
          setThreadKey("");
          setLoadError(e instanceof Error ? e.message : "Не удалось открыть диалог");
        }
      } finally {
        if (!silent) setLoadingThread(false);
      }
    },
    [],
  );

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    loadDialogs();
  }, [loadDialogs]);

  // Live workspace: BORIS must update an open conversation without requiring
  // the operator to reload the browser tab. The server-side Avito poller keeps
  // the canonical DB current; this lightweight UI poll only projects that state.
  // Silent refresh preserves the draft, selected dialog and MOP controls.
  useEffect(() => {
    const refreshVisible = () => {
      if (document.visibilityState !== "visible") return;
      void loadDialogs(true);
      if (thread?.account_id && thread?.avito_chat_id) {
        void loadThread(thread.account_id, thread.avito_chat_id, true);
      }
    };
    const timer = window.setInterval(refreshVisible, 4000);
    document.addEventListener("visibilitychange", refreshVisible);
    window.addEventListener("focus", refreshVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
      window.removeEventListener("focus", refreshVisible);
    };
  }, [loadDialogs, loadThread, thread?.account_id, thread?.avito_chat_id]);

  // Keep the workspace useful even before the operator clicks a row:
  // load the first visible dialog without changing its read state.
  useEffect(() => {
    if (loadingDialogs || loadingThread || thread || dialogs.length === 0) return;
    if (restoreThreadKey) {
      const restored = dialogs.find((d) => `${d.account_id}:${d.avito_chat_id}` === restoreThreadKey);
      setRestoreThreadKey("");
      if (restored?.account_id && restored?.avito_chat_id) {
        void loadThread(restored.account_id, restored.avito_chat_id);
        return;
      }
    }
    // Desktop benefits from a preselected conversation, but on a phone the
    // expected interaction is list -> dialog. Auto-opening the first row makes
    // the dialog list effectively unreachable on first entry.
    const mobileListMode = typeof window !== "undefined"
      && window.matchMedia("(max-width: 900px)").matches;
    if (mobileListMode) return;

    const first = dialogs[0];
    if (first?.account_id && first?.avito_chat_id) {
      void loadThread(first.account_id, first.avito_chat_id);
    }
  }, [dialogs, loadingDialogs, loadingThread, thread, loadThread, restoreThreadKey]);

  // Reply text is operator state, not server projection. Preserve it per dialog
  // across refresh/navigation while silent polling keeps the canonical thread fresh.
  useEffect(() => {
    if (!thread?.account_id || !thread?.avito_chat_id) return;
    try {
      setDraft(localStorage.getItem(`boris_messages_draft:${thread.account_id}:${thread.avito_chat_id}`) || "");
    } catch {
      setDraft("");
    }
  }, [thread?.account_id, thread?.avito_chat_id]);

  const openDialog = async (dialog: Dlg) => {
    await loadThread(
      dialog.account_id,
      dialog.avito_chat_id,
    );

    if (typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches) {
      window.requestAnimationFrame(() => {
        document.querySelector('[data-testid="messages-workspace"]')?.scrollIntoView({
          block: "start",
          behavior: "auto",
        });
      });
    }

    await apiPost(
      "/api/inbox/mark_read",
      {
        account_id: dialog.account_id,
        avito_chat_id: dialog.avito_chat_id,
      },
    );

    await loadAccounts();
    await loadDialogs();
  };

  const loadManagerQueue = useCallback(async (accountId: string) => {
    if (!accountId || accountId === "all") { setManagerQueue([]); setManagerQueueSummary({}); setManagerQueueAccountId(""); return []; }
    const j = await apiGet<any>(`/api/inbox/attention?account_id=${encodeURIComponent(accountId)}`);
    const rows = (j?.items || []).filter((x:any)=>x.account_id===accountId);
    setManagerQueue(rows); setManagerQueueSummary(j?.summary || {}); setManagerQueueAccountId(accountId);
    return rows;
  }, []);

  useEffect(() => { setManagerQueue([]); setManagerQueueSummary({}); setManagerQueueFilter("all"); setManagerSkipped([]); setThread(null); void loadManagerQueue(scope); }, [scope, loadManagerQueue]);

  const managerVisibleQueue = useMemo(() => managerQueueFilter === "all" ? managerQueue : managerQueue.filter((x:any)=>x.reasons?.some((r:any)=>r.kind===managerQueueFilter)), [managerQueue, managerQueueFilter]);

  const openManagerQueue = async (filter = managerQueueFilter) => {
    if (!scope || scope === "all") return;
    setManagerQueueFilter(filter);
    const rows = await loadManagerQueue(scope);
    const visible = filter === "all" ? rows : rows.filter((x:any)=>x.reasons?.some((r:any)=>r.kind===filter));
    if (visible[0]) await openAttentionDialog(visible[0].account_id, visible[0].avito_chat_id);
  };

  const advanceManagerQueue = async (accountId: string, completedChatId: string) => {
    const rows = await loadManagerQueue(accountId);
    const visible = managerQueueFilter === "all" ? rows : rows.filter((x:any)=>x.reasons?.some((r:any)=>r.kind===managerQueueFilter));
    const next = visible.find((x:any)=>x.avito_chat_id !== completedChatId && !managerSkipped.includes(x.avito_chat_id)) || visible.find((x:any)=>x.avito_chat_id !== completedChatId);
    if (next) await openAttentionDialog(next.account_id, next.avito_chat_id);
  };

  useEffect(() => {
    const fn = (event: Event) => { const d=(event as CustomEvent).detail||{}; if(d.account_id && d.avito_chat_id) void advanceManagerQueue(d.account_id,d.avito_chat_id); };
    window.addEventListener("boris-manager-action-completed", fn as EventListener);
    return () => window.removeEventListener("boris-manager-action-completed", fn as EventListener);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [managerQueueFilter, managerSkipped]);

  const skipManagerClient = async () => {
    if (!thread) return;
    setManagerSkipped(prev=>prev.includes(thread.avito_chat_id)?prev:[...prev,thread.avito_chat_id]);
    const next = managerVisibleQueue.find((x:any)=>x.avito_chat_id !== thread.avito_chat_id && !managerSkipped.includes(x.avito_chat_id)) || managerVisibleQueue.find((x:any)=>x.avito_chat_id !== thread.avito_chat_id);
    if (next) await openAttentionDialog(next.account_id,next.avito_chat_id);
  };

  const remindManagerLater = () => {
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("boris-manager-remind-later", {detail:{account_id:thread?.account_id,avito_chat_id:thread?.avito_chat_id}}));
  };

  const openAttentionDialog = async (accountId: string, chatId: string) => {
    borisR15RememberSelectedAccount(accountId);
    setScope(accountId);
    await loadThread(accountId, chatId);
    await apiPost("/api/inbox/mark_read", { account_id: accountId, avito_chat_id: chatId });
    await loadAccounts();
    await loadDialogs();
    await loadManagerQueue(accountId);
  };

  const refreshCurrentThread = () => {
    if (!thread) return;
    void loadThread(thread.account_id, thread.avito_chat_id);
  };

  const loadMopSuggestions = async () => {
    if (!thread || suggestionsBusy) return;
    setSuggestionsBusy(true);
    try {
      const result = await apiPost<any>(
        "/api/inbox/reply_suggestions",
        { account_id: thread.account_id, avito_chat_id: thread.avito_chat_id },
      );
      if (result?.status === "ok") {
        setMopSuggestions(result.suggestions || []);
      } else {
        alert(result?.message || "МОП не смог подготовить варианты");
      }
    } finally {
      setSuggestionsBusy(false);
    }
  };

  const send = async () => {
    if (!draft.trim() || !thread || sending) return;

    const accountId = thread.account_id;
    const chatId = thread.avito_chat_id;
    const sentText = draft.trim();
    setSending(true);

    try {
      const result = await apiPost<any>(
        "/api/inbox/send",
        {
          account_id: accountId,
          avito_chat_id: chatId,
          text: sentText,
        },
      );

      if (result?.status === "ok") {
        try {
          localStorage.removeItem(`boris_messages_draft:${accountId}:${chatId}`);
        } catch {}

        // The operator must see a successfully accepted outgoing immediately.
        // Do not wait for Avito history propagation or a broad inbox sync: append
        // a UI-only optimistic row, then reconcile it with the authoritative
        // thread shortly afterwards. This eliminates the old "send -> reload tab"
        // workflow even when Avito's read-after-write visibility is delayed.
        const optimistic: Msg = {
          id: `local:${Date.now()}`,
          text: sentText,
          direction: "out",
          at: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          author_kind: "boris_user",
          author_label: "Вы",
        };
        setThread((current) => {
          if (!current || current.account_id !== accountId || current.avito_chat_id !== chatId) return current;
          const base = current.messages || current.items || [];
          if (base.some((m) => m.direction === "out" && (m.text || m.body || m.message || "").trim() === sentText)) {
            return current;
          }
          return { ...current, messages: [...base, optimistic] };
        });
        setDraft("");

        void loadDialogs(true);
        window.setTimeout(() => {
          void loadThread(accountId, chatId, true);
        }, 900);
      } else {
        alert(
          result?.message ||
          "Не удалось отправить сообщение",
        );
      }
    } finally {
      setSending(false);
    }
  };

  const totalUnread = useMemo(
    () =>
      accounts.reduce(
        (sum, account) =>
          sum + Number(account.unread || 0),
        0,
      ),
    [accounts],
  );

  const unanswered = useMemo(
    () =>
      dialogs.filter(
        (d) => Number(d.unread || 0) > 0,
      ).length,
    [dialogs],
  );

  const activeDialogs = useMemo(
    () =>
      dialogs.filter(
        (d) => !d.is_purchase,
      ),
    [dialogs],
  );

  return (
    <div
      className="boris-product-route boris-messages-page"
      data-mobile-thread-open={thread ? "1" : "0"}
      style={{
        minHeight: "100vh",
        background: "var(--boris-background)",
        color: "var(--boris-text-primary)",
      }}
    >
      <main
        style={{
          maxWidth: 1450,
          margin: "0 auto",
          padding: "26px 28px 42px",
        }}
      >
        {/* HEADER */}

        <div
          className="messages-workspace-head"
          style={{
            display: "block",
            marginBottom: 24,
          }}
        >
          <div style={{display:"none"}}>

            <div
              style={{
                fontSize: 28,
                fontWeight: 800,
                letterSpacing: "-.5px",
              }}
            >
              Сообщения
            </div>

            <div
              style={{
                marginTop: 5,
                color: "#94a3b8",
                fontSize: 14,
              }}
            >
              Все аккаунты и обращения в одном окне
            </div>
          </div>

          <div style={{ width: "100%", marginTop: 18 }}>
            <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 16, marginBottom: 10, flexWrap: "wrap" }}>
              <div>
                <div style={{ color: "#2f6df6", fontSize: 11, fontWeight: 800, letterSpacing: ".08em", textTransform: "uppercase" }}>Быстрые действия</div>
                <div style={{ color: "#182230", fontSize: 20, fontWeight: 850, marginTop: 2 }}>Перейти к работе</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <button type="button" onClick={() => setQuickActionsSettingsOpen((v) => !v)} style={{ minHeight: 36, border: "1px solid #dbe4f0", borderRadius: 10, background: "#fff", color: "#475569", padding: "7px 10px", fontWeight: 750, cursor: "pointer" }}>
                  {quickActionsSettingsOpen ? "Готово" : "Настроить кнопки"}
                </button>
                <button type="button" onClick={() => { const next = !quickActionsCollapsed; setQuickActionsCollapsed(next); persistQuickActions(hiddenQuickActions, next); }} style={{ minHeight: 36, border: "1px solid #dbeafe", borderRadius: 10, background: "#eff6ff", color: "#2563eb", padding: "7px 10px", fontWeight: 800, cursor: "pointer" }}>
                  {quickActionsCollapsed ? "Показать панель" : "Свернуть панель"}
                </button>
              </div>
            </div>
            {quickActionsSettingsOpen ? (
              <div className="messages-quick-actions-settings" style={{ display: "flex", flexWrap: "wrap", gap: 8, margin: "0 0 10px", padding: 10, border: "1px solid #e5e7eb", borderRadius: 12, background: "#f8fafc" }}>
                {quickActionOptions.map(([key, label]) => {
                  const visible = !hiddenQuickActions.includes(key);
                  return <button key={key} type="button" onClick={() => toggleQuickAction(key)} style={{ minHeight: 36, border: visible ? "1px solid #bfdbfe" : "1px solid #e5e7eb", borderRadius: 999, background: visible ? "#eff6ff" : "#fff", color: visible ? "#1d4ed8" : "#64748b", padding: "7px 11px", fontWeight: 750, cursor: "pointer" }}>{visible ? "✓ " : "+ "}{label}</button>;
                })}
                <button type="button" onClick={() => { setHiddenQuickActions([]); persistQuickActions([]); }} style={{ minHeight: 36, border: "1px solid #e5e7eb", borderRadius: 999, background: "#fff", color: "#475569", padding: "7px 11px", fontWeight: 750, cursor: "pointer" }}>Показать все</button>
              </div>
            ) : null}
            <div className="messages-work-cards" style={{ display: quickActionsCollapsed ? "none" : "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 14 }}>
              <a hidden={hiddenQuickActions.includes("crm")} href={scopedHref("/dashboard/crm")} className="messages-work-card" style={{ textDecoration: "none", minHeight: 132, padding: "18px", borderRadius: 18, border: "1px solid #dfe7f5", borderTop: "3px solid #315bea", background: "linear-gradient(180deg,#ffffff 0%,#f8fbff 100%)", boxShadow: "0 8px 24px rgba(30,64,175,.06)", display: "grid", gridTemplateColumns: "44px 1fr 22px", alignItems: "center", gap: 12 }}>
                <span style={{ width: 44, height: 44, borderRadius: 13, display: "grid", placeItems: "center", background: "#eef4ff", color: "#315bea", fontSize: 21 }}>👥</span>
                <span style={{ minWidth: 0 }}><strong style={{ display: "flex", alignItems:"center", gap:7, color: "#182230", fontSize: 15.5 }}>Клиенты и сделки <em style={{fontStyle:"normal",fontSize:10,fontWeight:800,color:"#2563eb",background:"#eaf2ff",borderRadius:999,padding:"3px 7px"}}>{managerQueue.length} в работе</em></strong><small style={{ display: "block", color: "#7a8699", fontSize: 12.5, lineHeight: 1.35, marginTop: 4 }}>Клиенты, сделки и задачи · очередь обновляется по реальным обращениям</small></span>
                <span style={{ color: "#315bea", fontSize: 25, lineHeight: 1 }}>›</span>
              </a>
              <a hidden={hiddenQuickActions.includes("training")} href={borisR16MopHref(scope)} className="messages-work-card" style={{ textDecoration: "none", minHeight: 132, padding: "18px", borderRadius: 18, border: "1px solid #f3dce2", borderTop: "3px solid #ef476f", background: "linear-gradient(180deg,#ffffff 0%,#fff8fa 100%)", boxShadow: "0 8px 24px rgba(225,29,72,.06)", display: "grid", gridTemplateColumns: "44px 1fr 22px", alignItems: "center", gap: 12 }}>
                <span style={{ width: 44, height: 44, borderRadius: 13, display: "grid", placeItems: "center", background: "#fff1f2", color: "#e11d48", fontSize: 21 }}>🥊</span>
                <span style={{ minWidth: 0 }}><strong style={{ display: "flex", alignItems:"center", gap:7, color: "#182230", fontSize: 15.5 }}>Тренировка МОП <em style={{fontStyle:"normal",fontSize:10,fontWeight:800,color:"#be123c",background:"#fff1f2",borderRadius:999,padding:"3px 7px"}}>{mopBoundCount} активн.</em></strong><small style={{ display: "block", color: "#7a8699", fontSize: 12.5, lineHeight: 1.35, marginTop: 4 }}>{scope==="all"?"Выберите любой аккаунт с подключённым МОП внутри тренировки":mopSettingsSummary}</small></span>
                <span style={{ color: "#e11d48", fontSize: 25, lineHeight: 1 }}>›</span>
              </a>
              <a hidden={hiddenQuickActions.includes("review")} href={scopedHref("/messages/mop-review")} className="messages-work-card" style={{ textDecoration: "none", minHeight: 132, padding: "18px", borderRadius: 18, border: "1px solid #d9f0e5", borderTop: "3px solid #10b981", background: "linear-gradient(180deg,#ffffff 0%,#f6fffb 100%)", boxShadow: "0 8px 24px rgba(16,185,129,.06)", display: "grid", gridTemplateColumns: "44px 1fr 22px", alignItems: "center", gap: 12 }}>
                <span style={{ width: 44, height: 44, borderRadius: 13, display: "grid", placeItems: "center", background: "#ecfdf5", color: "#059669", fontSize: 21 }}>🧠</span>
                <span style={{ minWidth: 0 }}><strong style={{ display: "flex", alignItems:"center", gap:7, color: "#182230", fontSize: 15.5 }}>Разбор диалогов МОП <em style={{fontStyle:"normal",fontSize:10,fontWeight:800,color:"#047857",background:"#ecfdf5",borderRadius:999,padding:"3px 7px"}}>реальные</em></strong><small style={{ display: "block", color: "#7a8699", fontSize: 12.5, lineHeight: 1.35, marginTop: 4 }}>Реальные ответы виртуального МОП · оценка сотрудника · правило в память</small></span>
                <span style={{ color: "#059669", fontSize: 25, lineHeight: 1 }}>›</span>
              </a>
              <a hidden={hiddenQuickActions.includes("reactivation")} href={scopedHref("/reactivation")} className="messages-work-card" style={{ textDecoration: "none", minHeight: 132, padding: "18px", borderRadius: 18, border: "1px solid #fde7c7", borderTop: "3px solid #f59e0b", background: "linear-gradient(180deg,#ffffff 0%,#fffbeb 100%)", boxShadow: "0 8px 24px rgba(245,158,11,.07)", display: "grid", gridTemplateColumns: "44px 1fr 22px", alignItems: "center", gap: 12 }}>
                <span style={{ width: 44, height: 44, borderRadius: 13, display: "grid", placeItems: "center", background: "#fffbeb", color: "#d97706", fontSize: 21 }}>↩</span>
                <span style={{ minWidth: 0 }}><strong style={{ display: "block", color: "#182230", fontSize: 15.5 }}>Реактивация клиентов</strong><small style={{ display: "block", color: "#7a8699", fontSize: 12.5, lineHeight: 1.35, marginTop: 4 }}>Вернуть клиентов, которые перестали отвечать, через реальные диалоги и готовые сообщения BORIS</small></span>
                <span style={{ color: "#d97706", fontSize: 25, lineHeight: 1 }}>›</span>
              </a>
              <a hidden={hiddenQuickActions.includes("settings")} href={scopedHref("/messages/mop-settings")} className="messages-work-card" style={{ textDecoration: "none", minHeight: 132, padding: "18px", borderRadius: 18, border: "1px solid #e7e1f8", borderTop: "3px solid #7c5cff", background: "linear-gradient(180deg,#ffffff 0%,#fbf9ff 100%)", boxShadow: "0 8px 24px rgba(124,92,255,.06)", display: "grid", gridTemplateColumns: "44px 1fr 22px", alignItems: "center", gap: 12 }}>
                <span style={{ width: 44, height: 44, borderRadius: 13, display: "grid", placeItems: "center", background: "#f5f3ff", color: "#7c3aed", fontSize: 21 }}>⚙</span>
                <span style={{ minWidth: 0 }}><strong style={{ display: "flex", alignItems:"center", gap:7, color: "#182230", fontSize: 15.5 }}>Настройки МОП <em style={{fontStyle:"normal",fontSize:10,fontWeight:800,color:"#6d28d9",background:"#f5f3ff",borderRadius:999,padding:"3px 7px"}}>работает</em></strong><small style={{ display: "block", color: "#7a8699", fontSize: 12.5, lineHeight: 1.35, marginTop: 4 }}>{scope==="all"?"Режим, график, цели и правила по выбранному аккаунту":mopSettingsSummary}</small></span>
                <span style={{ color: "#7c3aed", fontSize: 25, lineHeight: 1 }}>›</span>
              </a>
              {needsAccountConnection ? (
                <a hidden={hiddenQuickActions.includes("connect")} href="/messages/setup" className="messages-work-card" style={{ textDecoration: "none", minHeight: 132, padding: "18px", borderRadius: 18, border: "1px solid #bae6fd", borderTop: "3px solid #0ea5e9", background: "linear-gradient(180deg,#ffffff 0%,#f0f9ff 100%)", boxShadow: "0 8px 24px rgba(14,165,233,.08)", display: "grid", gridTemplateColumns: "44px 1fr 22px", alignItems: "center", gap: 12 }}>
                  <span style={{ width: 44, height: 44, borderRadius: 13, display: "grid", placeItems: "center", background: "#e0f2fe", color: "#0284c7", fontSize: 21 }}>🔌</span>
                  <span style={{ minWidth: 0 }}><strong style={{ display: "block", color: "#182230", fontSize: 15.5 }}>Подключить оплаченный аккаунт</strong><small style={{ display: "block", color: "#64748b", fontSize: 12.5, lineHeight: 1.35, marginTop: 3 }}>Оплата уже подтверждена. Подключите Avito — после успешной активации эта карточка исчезнет сама.</small></span>
                  <span style={{ color: "#0284c7", fontSize: 25, lineHeight: 1 }}>›</span>
                </a>
              ) : null}
            </div>
          </div>
        </div>

        {loadError ? (
          <div role="alert" style={{ marginBottom: 16, padding: "12px 14px", borderRadius: 10, background: "#fff1f2", color: "#b42318", fontSize: 13.5, fontWeight: 650 }}>
            {loadError}
          </div>
        ) : null}

        {/* KPI: API failure must never be presented as real zeroes. */}

        {!loadError ? <div
          className="boris-messages-kpis"
          style={{
            display: "grid",
            gridTemplateColumns:
              "repeat(4,minmax(0,1fr))",
            gap: 16,
            marginBottom: 18,
          }}
        >
          {[
            {
              label: "Всего аккаунтов",
              value: accounts.length,
              icon: "👥",
              accent: "#eff6ff",
            },
            {
              label: "Новые сообщения",
              value: totalUnread,
              icon: "💬",
              accent: "#eff6ff",
            },
            {
              label: "Не отвечено",
              value: unanswered,
              icon: "◷",
              accent: "#fff1f2",
            },
            {
              label: "Активные диалоги",
              value: activeDialogs.length,
              icon: "✓",
              accent: "#ecfdf5",
            },
          ].map((kpi) => (
            <div
              key={kpi.label}
              style={{
                background: "#fff",
                border: "1px solid #e5e7eb",
                borderRadius: 16,
                padding: "18px 18px",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                boxShadow:
                  "0 4px 14px rgba(15,23,42,.035)",
              }}
            >
              <div>
                <div
                  style={{
                    color: "#94a3b8",
                    fontSize: 13.5,
                    marginBottom: 7,
                  }}
                >
                  {kpi.label}
                </div>

                <div
                  style={{
                    fontSize: 25,
                    fontWeight: 800,
                    color:
                      kpi.label === "Не отвечено"
                        ? "#ef4444"
                        : "#111827",
                  }}
                >
                  {kpi.value}
                </div>
              </div>

              <Icon>{kpi.icon}</Icon>
            </div>
          ))}
        </div> : null}

        {/* MAIN WORKSPACE */}

        <div
          className="boris-messages-workspace"
          data-mobile-view={thread ? "thread" : "list"}
          data-testid="messages-workspace"
          style={{
            display: "grid",
            gridTemplateColumns:
              "220px minmax(300px,340px) minmax(430px,1fr) 286px",
            gap: 12,
            minHeight: 620,
          }}
        >
          {/* ACCOUNTS */}

          <section className="boris-messages-panel boris-messages-accounts-panel"
            style={{
              background: "#fff",
              border: "1px solid #e5e7eb",
              borderRadius: 16,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                padding: "15px 16px",
                borderBottom: "1px solid #eef2f7",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <strong>Аккаунты</strong>

              <button
                onClick={loadAccounts}
                aria-label="Обновить аккаунты"
                title="Обновить аккаунты"
                style={{
                  border: "1px solid #e5e7eb",
                  background: "#fff",
                  borderRadius: 10,
                  width: 44,
                  minWidth: 44,
                  height: 44,
                  minHeight: 44,
                  cursor: "pointer",
                  display: "grid",
                  placeItems: "center",
                  fontSize: 20,
                  lineHeight: 1,
                }}
              >
                ↻
              </button>
            </div>

            <div style={{ padding: 10 }}>
              <button
                className="boris-messages-account-card boris-messages-account-card-all"
                onClick={() => {
                  setScope("all");
                  setThread(null);
                  setThreadKey("");
                  setManagerQueue([]);
                  setManagerQueueSummary({});
                  setManagerQueueAccountId("");
                  setManagerQueueFilter("all");
                }}
                style={{
                  width: "100%",
                  border: "none",
                  borderRadius: 11,
                  padding: "12px 11px",
                  background:
                    scope === "all"
                      ? "#eff6ff"
                      : "#fff",
                  color:
                    scope === "all"
                      ? "#2563eb"
                      : "#334155",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  cursor: "pointer",
                  fontWeight: 700,
                }}
              >
                <span>● Все аккаунты</span>
                <span>{accounts.length}</span>
              </button>

              {accounts.map((account) => (
                <button
                  className="boris-messages-account-card"
                  key={account.account_id}
                  onClick={() => {
                    borisR15RememberSelectedAccount(account.account_id);
                              setScope(account.account_id);
                    setManagerQueue([]);
                    setManagerQueueSummary({});
                    setManagerQueueAccountId("");
                    setManagerQueueFilter("all");

                    // BORIS_MOP_R132_ACCOUNT_CONTEXT
                    // Preserve the account selected in the unified inbox
                    // so MOP training opens for the same client.
                    try {
                      localStorage.setItem(
                        "boris_account_id",
                        String(account.account_id),
                      );
                    } catch {}
                    setThread(null);
                    setThreadKey("");
                  }}
                  style={{
                    width: "100%",
                    border: "none",
                    borderRadius: 11,
                    padding: "12px 11px",
                    marginTop: 5,
                    background:
                      scope === account.account_id
                        ? "#eff6ff"
                        : "#fff",
                    textAlign: "left",
                    cursor: "pointer",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      gap: 8,
                      alignItems: "center",
                    }}
                  >
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: 50,
                        background: "#10b981",
                      }}
                    />

                    <span
                      style={{
                        fontWeight: 700,
                        fontSize: 13.5,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {account.name}
                    </span>
                  </div>

                  <div style={{ marginTop: 7, paddingLeft: 16, display: "flex", alignItems: "center", gap: 7 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 999, background: account.status === "connected" && account.sync_status !== "history" ? "#10b981" : "#94a3b8", boxShadow: account.status === "connected" && account.sync_status !== "history" ? "0 0 0 4px rgba(16,185,129,.10)" : "none" }} />
                    <span style={{ fontSize: 12.5, fontWeight: 700, color: account.status === "connected" && account.sync_status !== "history" ? "#059669" : "#64748b" }}>
                      {account.status === "connected" && account.sync_status !== "history" ? "Активен" : "Неактивен"}
                    </span>
                  </div>
                </button>
              ))}

              {loadingAccounts &&
                <div
                  style={{
                    color: "#94a3b8",
                    padding: 12,
                    fontSize: 13.5,
                  }}
                >
                  Обновляем…
                </div>
              }
            </div>
          </section>

          {/* DIALOGS */}

          <section className="boris-messages-panel boris-messages-dialogs-panel"
            style={{
              background: "#fff",
              border: "1px solid #e5e7eb",
              borderRadius: 16,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                padding: "15px 16px",
                borderBottom: "1px solid #eef2f7",
              }}
            >
              {scope !== "all" ? <div className="boris-mop-runtime-card"><div><span className="boris-mop-runtime-kicker">ИИ-МЕНЕДЖЕР</span><strong>{mopSettingsSummary}</strong><small>Отвечает клиентам в едином окне по действующим правилам и графику аккаунта.</small></div><div className="boris-mop-runtime-actions"><a href={scopedHref("/ai")}>Открыть МОП</a><a href={scopedHref("/messages/mop-settings")}>Режим и график</a><a href={scopedHref("/messages/mop-training")}>Обучение</a><a href={scopedHref("/settings/notifications")}>Уведомления</a></div></div> : null}

              {scope !== "all" && managerQueueAccountId === scope ? <div className="boris-messages-manager-queue" style={{marginBottom:14,padding:14,borderRadius:16,background:"linear-gradient(180deg,#ffffff,#f8fbff)",border:"1px solid #dfe8f5",boxShadow:"0 8px 22px rgba(31,58,103,.055)"}}><div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center"}}><div><div style={{fontWeight:900,fontSize: 13.5}}>Очередь менеджера</div><div style={{fontSize: 13.5,color:"#64748b",marginTop:2}}>BORIS ведёт от самого срочного клиента к следующему</div></div><button onClick={()=>openManagerQueue("all")} disabled={!managerQueue.length} style={{border:0,borderRadius:10,background:"#2563eb",color:"#fff",fontWeight:800,padding:"9px 14px",cursor:"pointer",whiteSpace:"nowrap",minWidth:138}}>Открыть очередь</button></div><div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(110px,1fr))",gap:4,marginTop:8}}>{[["all","Осталось",managerQueueSummary.remaining ?? managerQueue.length],["overdue_task","Просрочено",managerQueueSummary.overdue ?? 0],["phone_received","Телефон",managerQueueSummary.phone ?? 0],["unread","Ждут",managerQueueSummary.waiting ?? 0],["reactivation","Реактивация",managerQueueSummary.reactivation ?? 0]].map(([k,l,n]:any)=><button className="boris-messages-queue-filter" key={k} onClick={()=>openManagerQueue(k)} style={{border:"1px solid #e5e7eb",borderRadius:8,background:managerQueueFilter===k?"#eff6ff":"#fff",padding:5,cursor:"pointer",textAlign:"left"}}><b style={{fontSize: 13.5}}>{n}</b><div style={{fontSize: 13.5,color:"#64748b"}}>{l}</div></button>)}</div><div style={{fontSize: 13.5,color:"#64748b",marginTop:7}}>Обработано сегодня: {managerQueueSummary.processed_today ?? 0} · создано задач: {managerQueueSummary.tasks_created_today ?? 0} · выполнено задач: {managerQueueSummary.processed_today ?? 0} · реактивация: {managerQueueSummary.reactivation_today ?? 0} · целевых действий: {managerQueueSummary.target_actions_today ?? 0}</div>{managerQueue.length===0 ? <div style={{marginTop:8,padding:8,borderRadius:8,background:"#ecfdf5",fontSize: 13.5,fontWeight:800}}>Все приоритетные клиенты на сегодня разобраны · обработано {managerQueueSummary.processed_today ?? 0} · создано задач {managerQueueSummary.tasks_created_today ?? 0} · получено контактов {managerQueueSummary.phone ?? 0} · реактивация {managerQueueSummary.reactivation_today ?? 0} · целевых действий {managerQueueSummary.target_actions_today ?? 0}{managerQueueSummary.next_reminder_at ? <><br/>Следующий клиент вернётся в очередь: {new Date(managerQueueSummary.next_reminder_at).toLocaleString("ru-RU")}</> : null}</div> : thread ? <div style={{display:"flex",gap:6,marginTop:8}}><button onClick={skipManagerClient} style={{border:"1px solid #dbe3ee",background:"#fff",borderRadius:8,padding:"6px 8px",fontSize: 13.5,cursor:"pointer"}}>Следующий клиент</button><button onClick={()=>{setThread(null);window.scrollTo({top:0,behavior:"smooth"})}} style={{border:"1px solid #dbe3ee",background:"#fff",borderRadius:8,padding:"6px 8px",fontSize: 13.5,cursor:"pointer"}}>Назад к очереди</button><button onClick={remindManagerLater} style={{border:"1px solid #dbe3ee",background:"#fff",borderRadius:8,padding:"6px 8px",fontSize: 13.5,cursor:"pointer"}}>Вернуться позже</button></div> : null}</div> : null}
              <div
                style={{
                  fontWeight: 800,
                  marginBottom: 11,
                }}
              >
                Диалоги{" "}
                {scope === "all"
                  ? "(все аккаунты)"
                  : ""}
              </div>

              <input
                value={query}
                onChange={(e) =>
                  setQuery(e.target.value)
                }
                placeholder="Поиск: текст, объявление, ID диалога"
                style={{
                  width: "100%",
                  boxSizing: "border-box",
                  border: "1px solid #e5e7eb",
                  borderRadius: 10,
                  padding: "10px 11px",
                  outline: "none",
                  fontSize: 13.5,
                }}
              />

              <div style={{ marginTop: 10, color: "#64748b", fontSize: 12 }}>
                Показываются реальные диалоги подключённых аккаунтов
              </div>
            </div>

            <div
              onScroll={(event) => {
                const el = event.currentTarget;
                if (el.scrollHeight - el.scrollTop - el.clientHeight < 180) {
                  setDialogRenderLimit((limit) => Math.min(dialogs.length, limit + 80));
                }
              }}
              style={{
                maxHeight: 520,
                overflowY: "auto",
              }}
            >
              {loadingDialogs && (
                <div
                  style={{
                    padding: 20,
                    color: "#94a3b8",
                    fontSize: 13.5,
                  }}
                >
                  Загружаем диалоги…
                </div>
              )}

              {!loadingDialogs &&
                dialogs.slice(0, dialogRenderLimit).map((dialog) => (
                  <button
                    className="boris-messages-dialog-card"
                    data-testid="message-dialog-card"
                    key={
                      dialog.account_id +
                      ":" +
                      dialog.avito_chat_id
                    }
                    onClick={() =>
                      openDialog(dialog)
                    }
                    style={{
                      width: "100%",
                      border: "none",
                      borderBottom:
                        "1px solid #f1f5f9",
                      background:
                        thread?.avito_chat_id ===
                        dialog.avito_chat_id
                          ? "#f8fbff"
                          : "#fff",
                      textAlign: "left",
                      padding: "13px 14px",
                      cursor: "pointer",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent:
                          "space-between",
                        gap: 8,
                      }}
                    >
                      <div
                        style={{
                          minWidth: 0,
                        }}
                      >
                        <div
                          style={{
                            display: "flex",
                            gap: 5,
                            alignItems: "center",
                            fontSize: 13.5,
                            fontWeight: 750,
                          }}
                        >
                          <span
                            style={{
                              width: 7,
                              height: 7,
                              borderRadius: 50,
                              background:
                                Number(
                                  dialog.unread || 0,
                                ) > 0
                                  ? "#2563eb"
                                  : "#10b981",
                            }}
                          />

                          <span style={{minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                            {dialog.client_name ||
                              "Покупатель"}
                          </span>

                          <span
                            style={{
                              color: "#94a3b8",
                              fontWeight: 500,
                              minWidth: 0,
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            ·{" "}
                            {dialog.account_name}
                          </span>
                        </div>

                        <div
                          style={{
                            marginTop: 5,
                            color: "#64748b",
                            fontSize: 13.5,
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {dialog.last_text ||
                            dialog.item_title ||
                            "Нет сообщения"}
                        </div>
                      </div>

                      <div
                        style={{
                          minWidth: 30,
                          textAlign: "right",
                        }}
                      >
                        <div
                          style={{
                            color: "#94a3b8",
                            fontSize: 13.5,
                          }}
                        >
                          {formatTime(
                            dialog.last_at,
                          )}
                        </div>

                        {Number(
                          dialog.unread || 0,
                        ) > 0 && (
                          <span
                            style={{
                              display:
                                "inline-flex",
                              marginTop: 5,
                              minWidth: 19,
                              height: 19,
                              borderRadius: 50,
                              alignItems:
                                "center",
                              justifyContent:
                                "center",
                              background:
                                "#fee2e2",
                              color: "#ef4444",
                              fontSize: 13.5,
                              fontWeight: 800,
                            }}
                          >
                            {dialog.unread}
                          </span>
                        )}
                      </div>
                    </div>
                  </button>
                ))}

              {!loadingDialogs &&
                dialogs.length === 0 && (
                  <div
                    style={{
                      padding: 35,
                      textAlign: "center",
                      color: "#94a3b8",
                      fontSize: 13.5,
                    }}
                  >
                    Диалогов пока нет
                  </div>
                )}
            </div>
          </section>

          {/* THREAD */}

          <section className="boris-messages-panel boris-messages-thread-panel"
            data-testid="message-thread"
            style={{
              background: "#fff",
              border: "1px solid #e5e7eb",
              borderRadius: 16,
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
            }}
          >
            {!thread ? (
              <div
                style={{
                  flex: 1,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "#94a3b8",
                  padding: 30,
                  textAlign: "center",
                }}
              >
                <div
                  style={{
                    fontSize: 54,
                    opacity: .35,
                    marginBottom: 12,
                  }}
                >
                  💬
                </div>

                <strong
                  style={{
                    color: "#334155",
                    fontSize: 15,
                  }}
                >
                  Выберите диалог слева
                </strong>

                <div
                  style={{
                    marginTop: 6,
                    fontSize: 13.5,
                  }}
                >
                  Здесь появится переписка
                  и информация о клиенте
                </div>
              </div>
            ) : (threadKey !== `${thread.account_id}:${thread.avito_chat_id}` || (scope !== "all" && thread.account_id !== scope)) ? (
              <div style={{padding:30,textAlign:"center",color:"#64748b"}}>Загружаем выбранный аккаунт…</div>
            ) : (
              <>
                <div
                  style={{
                    padding: "15px 17px",
                    borderBottom:
                      "1px solid #eef2f7",
                    display: "flex",
                    justifyContent:
                      "space-between",
                    gap: 10,
                  }}
                >
                  <button
                    type="button"
                    className="boris-messages-mobile-thread-back"
                    onClick={() => {
                      setThread(null);
                      setThreadKey("");
                      setRestoreThreadKey("");
                      try {
                        localStorage.removeItem("boris_messages_selected_thread");
                      } catch {}
                      if (typeof window !== "undefined") {
                        window.requestAnimationFrame(() => {
                          document.querySelector(".boris-messages-dialogs-panel")?.scrollIntoView({
                            block: "start",
                            behavior: "auto",
                          });
                        });
                      }
                    }}
                    aria-label="К списку диалогов"
                    data-testid="message-thread-back"
                  >
                    <span aria-hidden="true">←</span>
                    <span>Диалоги</span>
                  </button>
                  <div className="boris-messages-thread-person">
                    <div
                      style={{
                        fontWeight: 800,
                      }}
                    >
                      {thread.client_name ||
                        "Клиент"}
                    </div>

                    <div
                      style={{
                        color: "#94a3b8",
                        fontSize: 13.5,
                        marginTop: 3,
                      }}
                    >
                      {thread.account_name}
                    </div>
                  </div>

                  <a
                    href={`/dashboard/crm?account_id=${encodeURIComponent(thread.account_id)}&chat_id=${encodeURIComponent(thread.avito_chat_id)}`}
                    style={{
                      textDecoration: "none",
                      borderRadius: 9,
                      padding: "8px 11px",
                      background: "#eff6ff",
                      color: "#2563eb",
                      fontWeight: 700,
                      fontSize: 13.5,
                    }}
                  >
                    Открыть клиента и сделку
                  </a>
                </div>

                <div
                  style={{
                    flex: 1,
                    overflowY: "auto",
                    padding: 18,
                    background: "#f8fafc",
                  }}
                >
                  {loadingThread && (
                    <div
                      style={{
                        textAlign: "center",
                        color: "#94a3b8",
                        padding: 30,
                      }}
                    >
                      Загружаем переписку…
                    </div>
                  )}

                  {!loadingThread &&
                    (
                      thread.messages ||
                      thread.items ||
                      []
                    ).map((message, index) => {
                      const text =
                        message.text ||
                        message.body ||
                        message.message ||
                        "";

                      const outgoing =
                        message.direction === "out" ||
                        message.direction ===
                          "outgoing" ||
                        message.sender === "manager" ||
                        message.sender === "business";

                      return (
                        <div
                          key={
                            String(
                              message.id ??
                              index,
                            )
                          }
                          style={{
                            display: "flex",
                            justifyContent:
                              outgoing
                                ? "flex-end"
                                : "flex-start",
                            marginBottom: 9,
                          }}
                        >
                          <div
                            style={{
                              maxWidth: "76%",
                              padding:
                                "10px 12px",
                              borderRadius:
                                outgoing
                                  ? "13px 13px 3px 13px"
                                  : "13px 13px 13px 3px",
                              background:
                                outgoing
                                  ? "#2878f0"
                                  : "#fff",
                              color:
                                outgoing
                                  ? "#fff"
                                  : "#334155",
                              boxShadow:
                                "0 2px 8px rgba(15,23,42,.05)",
                              fontSize: 13.5,
                              lineHeight: 1.45,
                              whiteSpace: "pre-wrap",
                              overflowWrap: "anywhere",
                              wordBreak: "break-word",
                              minWidth: 0,
                            }}
                          >
                            {outgoing && message.author_label ? (
                              <div style={{fontSize:11,fontWeight:650,opacity:.82,marginBottom:5}}>
                                {message.author_label}
                              </div>
                            ) : null}
                            {text}

                            <div
                              style={{
                                marginTop: 4,
                                fontSize: 13.5,
                                opacity: .65,
                                textAlign:
                                  "right",
                              }}
                            >
                              {formatTime(
                                message.at ||
                                  message.created_at,
                              )}
                              {outgoing ? (
                                <span style={{ marginLeft: 6, fontWeight: 700 }}>
                                  {message.is_read === true ? "✓✓ прочитано" : message.is_read === false ? "✓ доставлено" : "✓ отправлено"}
                                </span>
                              ) : null}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                </div>

                <div className="boris-messages-suggestions" style={{ margin: "0 12px 10px", padding: 12, border: "1px solid #e2e8f0", borderRadius: 12, background: "#fff" }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                    <div><div style={{ fontSize: 13.5, fontWeight: 800 }}>Ответы МОПа</div><div style={{ fontSize: 12, color: "#64748b", marginTop: 2 }}>Выберите готовый вариант, при необходимости отредактируйте и отправьте.</div></div>
                    <button type="button" onClick={loadMopSuggestions} disabled={suggestionsBusy} style={{ border: "1px solid #cbd5e1", borderRadius: 9, background: "#fff", padding: "6px 9px", fontWeight: 750, cursor: suggestionsBusy ? "default" : "pointer" }}>{suggestionsBusy ? "Готовлю…" : "Предложить ответы"}</button>
                  </div>
                  {mopSuggestions.length > 0 ? <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                    {mopSuggestions.map((x, i) => <button key={`${x.label}-${i}`} type="button" onClick={() => setDraft(x.text)} style={{ textAlign: "left", border: "1px solid #dbeafe", borderRadius: 10, background: "#f8fbff", padding: 10, cursor: "pointer" }}><div style={{ fontSize: 12, fontWeight: 800, color: "#1d4ed8", marginBottom: 4 }}>{x.label}</div><div style={{ fontSize: 13.5, color: "#334155", whiteSpace: "pre-wrap" }}>{x.text}</div></button>)}
                  </div> : null}
                </div>

                {thread.draft?.text && (
                  <div
                    className="boris-messages-ai-draft"
                    style={{
                      margin: "0 12px 10px",
                      padding: 12,
                      border: "1px solid #bfdbfe",
                      borderRadius: 12,
                      background: "#eff6ff",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: 10,
                        marginBottom: 7,
                      }}
                    >
                      <div style={{ fontSize: 13.5, fontWeight: 800, color: "#1d4ed8" }}>
                        Черновик BORIS · {thread.draft.status === "draft_ready" ? "готов к отправке" : humanizeBusinessText(thread.draft.status, "в работе")}
                      </div>
                      <button
                        type="button"
                        onClick={() => setDraft(thread.draft?.text || "")}
                        style={{
                          border: "1px solid #93c5fd",
                          borderRadius: 9,
                          background: "#fff",
                          color: "#1d4ed8",
                          padding: "6px 9px",
                          fontSize: 13.5,
                          fontWeight: 750,
                          cursor: "pointer",
                        }}
                      >
                        Использовать в ответе
                      </button>
                    </div>
                    <div style={{ whiteSpace: "pre-wrap", fontSize: 13.5, lineHeight: 1.45, color: "#334155" }}>
                      {thread.draft.text}
                    </div>
                  </div>
                )}

                <div
                  className="boris-messages-composer"
                  style={{
                    padding: 12,
                    borderTop:
                      "1px solid #eef2f7",
                    display: "flex",
                    gap: 8,
                  }}
                >
                  <input
                    id="boris-message-reply"
                    data-testid="message-reply-input"
                    value={draft}
                    onChange={(e) => {
                      const value = e.target.value;
                      setDraft(value);
                      if (thread?.account_id && thread?.avito_chat_id) {
                        try {
                          const key = `boris_messages_draft:${thread.account_id}:${thread.avito_chat_id}`;
                          if (value) localStorage.setItem(key, value);
                          else localStorage.removeItem(key);
                        } catch {}
                      }
                    }}
                    onKeyDown={(e) => {
                      if (
                        e.key === "Enter" &&
                        !e.shiftKey
                      ) {
                        e.preventDefault();
                        send();
                      }
                    }}
                    placeholder="Напишите сообщение клиенту…"
                    style={{
                      flex: 1,
                      border:
                        "1px solid #dbe3ef",
                      borderRadius: 11,
                      padding:
                        "11px 12px",
                      outline: "none",
                    }}
                  />

                  <button
                    data-testid="send-message"
                    onClick={send}
                    disabled={
                      sending ||
                      !draft.trim()
                    }
                    style={{
                      border: "none",
                      borderRadius: 11,
                      padding:
                        "0 17px",
                      background:
                        sending ||
                        !draft.trim()
                          ? "#cbd5e1"
                          : "#2878f0",
                      color: "#fff",
                      fontWeight: 750,
                      cursor:
                        sending ||
                        !draft.trim()
                          ? "default"
                          : "pointer",
                    }}
                  >
                    {sending
                      ? "…"
                      : "Отправить"}
                  </button>
                </div>
              </>
            )}
          </section>

          {/* CONTEXT — compact dialog workspace. Full tools expand only on demand. */}
          <aside className="boris-messages-context-panel">
            {!thread || threadKey !== `${thread.account_id}:${thread.avito_chat_id}` ? (
              <div className="boris-context-empty">
                <span>↗</span>
                <strong>Работа по клиенту</strong>
                <small>Выберите диалог — появятся факты о клиенте, рекомендация BORIS и контроль МОП.</small>
              </div>
            ) : (
              <>
                <div className="boris-context-head">
                  <div><small>РАБОТА ПО КЛИЕНТУ</small><strong>{thread.client_name || "Клиент"}</strong></div>
                  <a href={`/dashboard/crm?account_id=${encodeURIComponent(thread.account_id)}&chat_id=${encodeURIComponent(thread.avito_chat_id)}`}>Клиент и сделка ↗</a>
                </div>
                <div style={{margin:"10px 10px 0",padding:"10px 11px",borderRadius:10,background:"#f8fbff",border:"1px solid #e5edf8",fontSize:11.5,lineHeight:1.45,color:"#64748b"}}>
                  <b style={{color:"#334155"}}>Что здесь откуда:</b><br/>Факты клиента берутся из переписки и данных клиента. Рекомендации BORIS помечены отдельно и не выдаются за слова клиента.
                </div>
                <div className="boris-context-stack">
                  <details className="boris-context-card">
                    <summary><span className="boris-context-icon">👤</span><span><b>Факты и сделка</b><small>Источник: переписка + данные клиента</small></span></summary>
                    <div className="boris-context-body"><CRMContextPanel accountId={thread.account_id} chatId={thread.avito_chat_id} /></div>
                  </details>
                  <details className="boris-context-card">
                    <summary><span className="boris-context-icon">✓</span><span><b>Что делать дальше</b><small>Рекомендация BORIS для сотрудника</small></span></summary>
                    <div className="boris-context-body"><ManagerWorkspacePanel
                      account={accounts.find((a) => a.account_id === thread.account_id) || null}
                      thread={thread}
                      onOpenDialog={openAttentionDialog}
                      onRefreshThread={refreshCurrentThread}
                      onPrimaryActionDone={advanceManagerQueue}
                    /></div>
                  </details>
                  <a href={`/messages/mop-review?account_id=${encodeURIComponent(thread.account_id)}&chat_id=${encodeURIComponent(thread.avito_chat_id)}`} style={{display:"grid",gridTemplateColumns:"38px 1fr 18px",alignItems:"center",gap:9,textDecoration:"none",padding:"11px 12px",border:"1px solid #e5ebf4",borderRadius:12,background:"#fff",color:"inherit"}}>
                    <span style={{width:38,height:38,borderRadius:10,display:"grid",placeItems:"center",background:"#ecfdf5"}}>🧠</span>
                    <span><b style={{display:"block",fontSize:13.5,color:"#172033"}}>Разобрать работу МОП</b><small style={{display:"block",fontSize:11.5,color:"#7a8699",marginTop:2}}>Реальные ответы МОП · оценка · комментарий · обучение</small></span>
                    <span style={{color:"#10b981",fontSize:20}}>›</span>
                  </a>
                </div>
              </>
            )}
          </aside>
        </div>

        {/* BOTTOM CRM / SALES STRIP */}

        <div
          className="boris-messages-bottom-grid"
          style={{
            display: "grid",
            gridTemplateColumns:
              "repeat(5,minmax(0,1fr))",
            gap: 10,
            marginTop: 14,
          }}
        >
          <ModuleCard
            icon="👤"
            title="Клиенты"
            subtitle="Все клиенты и база"
            href={scopedHref("/dashboard/crm/workspace?tab=clients")}
            accent="#eff6ff"
          />

          <ModuleCard
            icon="💜"
            title="Сделки"
            subtitle="Воронка и сделки"
            href={scopedHref("/dashboard/crm/workspace?tab=deals")}
            accent="#f5f3ff"
          />

          <ModuleCard
            icon="📋"
            title="Задачи"
            subtitle="Мои задачи и планы"
            href="/dashboard/crm/workspace?tab=tasks"
            accent="#fff7ed"
          />

          <ModuleCard
            icon="📅"
            title="Календарь"
            subtitle="Встречи и события"
            href="/dashboard/crm/workspace?tab=calendar"
            accent="#eff6ff"
          />

          <ModuleCard
            icon="🔽"
            title="Воронка продаж"
            subtitle="Аналитика и отчёты"
            href="/dashboard/crm"
            accent="#fff1f2"
          />
        </div>
      </main>
    </div>
  );
}
