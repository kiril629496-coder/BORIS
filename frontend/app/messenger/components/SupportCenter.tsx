"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, LifeBuoy, Search } from "lucide-react";
import styles from "../messenger.module.css";

export type SupportTicket = {
  ticket_id: number;
  account_id: string;
  account_name: string;
  conversation_id: number | null;
  status: string;
  priority: string;
  category: string;
  module: string;
  last_text: string;
  last_message_at?: string | null;
  updated_at?: string | null;
  unread_count: number;
  requires_owner: boolean;
  assigned_user_id?: number | null;
};

type Inbox = {
  status: string;
  counts?: { total?: number; unread?: number; requires_owner?: number; p0?: number; p1?: number };
  items?: SupportTicket[];
};


type DiagnosticJob = { status?: string; error_text?: string; id?: number; kind?: string };
type DiagnosticControl = { status?: string; block_reason?: string; error_code?: string; error_message?: string; module?: string };
type DiagnosticNotification = { status?: string; last_error?: string; event_type?: string };
type DiagnosticCampaign = { id?: number; status?: string; name?: string };
type DiagnosticReason = { count?: number; reason?: string };
type SupportDiagnostics = {
  status?: string;
  account?: { account_id?: string; name?: string };
  kpi?: { target_leads_per_day?: number | null; red_cpl_rub?: number | null; daily_budget_limit_rub?: number | null; hard_max_bid_rub?: number | null };
  autopilot?: { mode?: string | null; full_ai_marketer?: boolean | null; trial_until?: string | null };
  mop?: {
    sent_today?: number; human_required_now?: number; waiting_external_now?: number; pending_now?: number;
    send_errors_24h?: number; reasons?: DiagnosticReason[];
  };
  jobs?: DiagnosticJob[];
  controls?: DiagnosticControl[];
  notifications?: DiagnosticNotification[];
  campaigns?: DiagnosticCampaign[];
};
type SupportReport7 = {
  status?: string; tickets?: number; resolved?: number; p0?: number; p1?: number; repeat_accounts?: number;
  median_first_response_sec?: number | null; median_resolution_sec?: number | null;
};

type Props = {
  platformAdmin: boolean;
  onOpenTicket: (ticket: SupportTicket) => void;
  onStatus: (message: string) => void;
};

function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = localStorage.getItem("boris_token") || localStorage.getItem("boris_messenger_token");
  return token ? { Authorization: "Bearer " + token } : {};
}

function label(value: string) {
  if (value === "NEW") return "Новый";
  if (value === "IN_PROGRESS") return "В работе";
  if (value === "WAIT_CLIENT") return "Ждём клиента";
  if (value === "WAIT_INTERNAL") return "Нужна команда";
  if (value === "RESOLVED") return "Решено";
  return value || "Новый";
}

function age(value: string | null | undefined, nowMs: number) {
  if (!value || !nowMs) return "только что";
  const delta = nowMs - new Date(value).getTime();
  if (!Number.isFinite(delta) || delta < 60000) return "только что";
  const minutes = Math.floor(delta / 60000);
  if (minutes < 60) return String(minutes) + " мин";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return String(hours) + " ч " + String(minutes % 60) + " мин";
  return String(Math.floor(hours / 24)) + " дн";
}

function durationLabel(seconds?: number | null) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return "—";
  const value = Math.max(0, Math.round(Number(seconds)));
  if (value < 60) return String(value) + " сек";
  const minutes = Math.floor(value / 60);
  if (minutes < 60) return String(minutes) + " мин";
  const hours = Math.floor(minutes / 60);
  return String(hours) + " ч " + String(minutes % 60) + " мин";
}

function slaState(ticket: SupportTicket, nowMs: number) {
  if (ticket.status === "RESOLVED" || ticket.status === "WAIT_CLIENT") return { key: "ok", label: "" };
  const raw = ticket.last_message_at || ticket.updated_at;
  const stamp = raw ? new Date(raw).getTime() : nowMs;
  const minutes = nowMs ? Math.max(0, Math.floor((nowMs - stamp) / 60000)) : 0;
  const attentionAt = ticket.priority === "P0" ? 10 : ticket.priority === "P1" ? 30 : 60;
  const overdueAt = ticket.priority === "P0" ? 20 : ticket.priority === "P1" ? 60 : 180;
  if (minutes >= overdueAt) return { key: "overdue", label: "Просрочено" };
  if (minutes >= attentionAt) return { key: "attention", label: "Требует внимания" };
  return { key: "ok", label: "В срок" };
}

export default function SupportCenter({ platformAdmin, onOpenTicket, onStatus }: Props) {
  const [items, setItems] = useState<SupportTicket[]>([]);
  const [counts, setCounts] = useState<Inbox["counts"]>({});
  const [statusFilter, setStatusFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [accountFilter, setAccountFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [moduleFilter, setModuleFilter] = useState("");
  const [periodFilter, setPeriodFilter] = useState("all");
  const [assignmentFilter, setAssignmentFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [diagnosticsLoadingId, setDiagnosticsLoadingId] = useState<number | null>(null);
  const [diagnosticsByTicket, setDiagnosticsByTicket] = useState<Record<number, SupportDiagnostics>>({});
  const [report7, setReport7] = useState<SupportReport7 | null>(null);
  const [clockMs, setClockMs] = useState(0);

  const load = useCallback(async () => {
    if (!platformAdmin) return;
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (statusFilter) params.set("status", statusFilter);
      if (query.trim()) params.set("q", query.trim());
      params.set("limit", "200");
      const response = await fetch("/api/support/owner/inbox?" + params.toString(), {
        cache: "no-store",
        headers: authHeaders(),
      });
      const data = (await response.json().catch(() => ({}))) as Inbox & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Не удалось загрузить поддержку");
      setItems(data.items || []);
      setCounts(data.counts || {});
    } catch (error) {
      onStatus(error instanceof Error ? error.message : "Не удалось загрузить поддержку");
    } finally {
      setLoading(false);
    }
  }, [platformAdmin, statusFilter, query, onStatus]);

  const loadReport = useCallback(async () => {
    if (!platformAdmin) return;
    try {
      const response = await fetch("/api/support/owner/report?days=7", {
        cache: "no-store",
        headers: authHeaders(),
      });
      const data = (await response.json().catch(() => ({}))) as SupportReport7;
      if (response.ok) setReport7(data);
    } catch {
      // Report is supplemental; the live inbox must remain usable if it is unavailable.
    }
  }, [platformAdmin]);

  useEffect(() => {
    if (!platformAdmin) return;
    const initial = window.setTimeout(() => {
      setClockMs(Date.now());
      void load();
      void loadReport();
    }, 0);
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 5000);
    const reportTimer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadReport();
    }, 60_000);
    const clockTimer = window.setInterval(() => setClockMs(Date.now()), 30_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
      window.clearInterval(reportTimer);
      window.clearInterval(clockTimer);
    };
  }, [platformAdmin, load, loadReport]);

  const filterOptions = useMemo(() => {
    const uniq = (values: string[]) => Array.from(new Set(values.filter(Boolean))).sort((a, b) => a.localeCompare(b, "ru"));
    return {
      accounts: uniq(items.map(item => item.account_name || "")),
      categories: uniq(items.map(item => item.category || "")),
      modules: uniq(items.map(item => item.module || "")),
    };
  }, [items]);

  const visibleItems = useMemo(() => {
    const now = clockMs;
    return items.filter(ticket => {
      if (priorityFilter && ticket.priority !== priorityFilter) return false;
      if (accountFilter && (ticket.account_name || "") !== accountFilter) return false;
      if (categoryFilter && (ticket.category || "") !== categoryFilter) return false;
      if (moduleFilter && (ticket.module || "") !== moduleFilter) return false;
      if (assignmentFilter === "assigned" && !ticket.assigned_user_id) return false;
      if (assignmentFilter === "unassigned" && ticket.assigned_user_id) return false;
      if (periodFilter !== "all") {
        const raw = ticket.last_message_at || ticket.updated_at;
        const stamp = raw ? new Date(raw).getTime() : 0;
        const windowMs = periodFilter === "today" ? 24 * 60 * 60 * 1000 : 7 * 24 * 60 * 60 * 1000;
        if (!stamp || now - stamp > windowMs) return false;
      }
      return true;
    });
  }, [items, priorityFilter, accountFilter, categoryFilter, moduleFilter, periodFilter, assignmentFilter, clockMs]);

  const priorityItems = useMemo(() => {
    const rank = (ticket: SupportTicket) => {
      const priority = ticket.priority === "P0" ? 0 : ticket.priority === "P1" ? 1 : 2;
      const owner = ticket.requires_owner ? 0 : 1;
      const stamp = new Date(ticket.last_message_at || ticket.updated_at || 0).getTime() || 0;
      return [owner, priority, -stamp];
    };
    return items
      .filter(ticket => ticket.status !== "RESOLVED" && (ticket.requires_owner || ticket.priority === "P0" || ticket.priority === "P1"))
      .sort((a, b) => {
        const ar = rank(a), br = rank(b);
        for (let i = 0; i < ar.length; i += 1) if (ar[i] !== br[i]) return ar[i] - br[i];
        return a.ticket_id - b.ticket_id;
      })
      .slice(0, 5);
  }, [items]);

  const loadDiagnostics = async (ticket: SupportTicket) => {
    if (diagnosticsByTicket[ticket.ticket_id]) {
      setDiagnosticsByTicket(current => {
        const next = { ...current };
        delete next[ticket.ticket_id];
        return next;
      });
      return;
    }
    setDiagnosticsLoadingId(ticket.ticket_id);
    try {
      const response = await fetch("/api/support/owner/tickets/" + String(ticket.ticket_id) + "/diagnostics", {
        cache: "no-store",
        headers: authHeaders(),
      });
      const data = (await response.json().catch(() => ({}))) as SupportDiagnostics & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Диагностика недоступна");
      setDiagnosticsByTicket(current => ({ ...current, [ticket.ticket_id]: data }));
    } catch (error) {
      onStatus(error instanceof Error ? error.message : "Не удалось выполнить диагностику");
    } finally {
      setDiagnosticsLoadingId(current => current === ticket.ticket_id ? null : current);
    }
  };

  const updateStatus = async (ticket: SupportTicket, next: string) => {
    try {
      const response = await fetch("/api/support/owner/tickets/" + String(ticket.ticket_id) + "/status", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ status: next }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Не удалось изменить статус");
      await load();
      onStatus(next === "RESOLVED" ? "Обращение закрыто." : "Статус поддержки обновлён.");
    } catch (error) {
      onStatus(error instanceof Error ? error.message : "Не удалось изменить статус");
    }
  };

  if (!platformAdmin) return null;

  const filters: Array<[string, string]> = [
    ["", "Все"],
    ["NEW", "Новые"],
    ["IN_PROGRESS", "В работе"],
    ["WAIT_CLIENT", "Ждём клиента"],
    ["WAIT_INTERNAL", "Нужна команда"],
    ["RESOLVED", "Решённые"],
  ];

  return <div className={styles.supportWorkspace}>
    <div className={styles.supportHero}>
      <div>
        <span className={styles.kicker}>OWNER SUPPORT CENTER</span>
        <h2>Поддержка клиентов</h2>
        <p>Все обращения компаний BORIS в одном окне.</p>
      </div>
      <div className={styles.supportCounters}>
        <span><small>Требует меня</small><strong>{counts?.requires_owner || 0}</strong></span>
        <span><small>Непрочитано</small><strong>{counts?.unread || 0}</strong></span>
        <span><small>Критично</small><strong>{(counts?.p0 || 0) + (counts?.p1 || 0)}</strong></span>
      </div>
    </div>

    {report7 ? <div className={styles.supportReport7}>
      <span><small>Обращений · 7 дней</small><strong>{report7.tickets ?? 0}</strong></span>
      <span><small>Решено</small><strong>{report7.resolved ?? 0}</strong></span>
      <span><small>Первый ответ</small><strong>{durationLabel(report7.median_first_response_sec)}</strong></span>
      <span><small>Решение</small><strong>{durationLabel(report7.median_resolution_sec)}</strong></span>
      <span><small>P0 + P1</small><strong>{Number(report7.p0 || 0) + Number(report7.p1 || 0)}</strong></span>
      <span><small>Повторные клиенты</small><strong>{report7.repeat_accounts ?? 0}</strong></span>
    </div> : null}

    {priorityItems.length ? <div className={styles.supportPriorityCenter}>
      <div className={styles.supportPriorityHead}>
        <div><span className={styles.kicker}>СЕЙЧАС</span><h3>Требует меня сейчас: {priorityItems.length}</h3></div>
        <small>Сначала показываем непрочитанные P0/P1 и обращения, где ждут ваш ответ.</small>
      </div>
      <div className={styles.supportPriorityList}>
        {priorityItems.map((ticket, index) => <button type="button" key={ticket.ticket_id} onClick={() => onOpenTicket(ticket)}>
          <span className={styles.supportPriorityNumber}>{index + 1}</span>
          <span><strong>{ticket.account_name || "Без привязанного аккаунта"}</strong><small>{ticket.last_text || ticket.category || "Новое обращение"}</small></span>
          <em>{ticket.priority}</em>
        </button>)}
      </div>
    </div> : null}

    <div className={styles.supportToolbar}>
      <label className={styles.supportSearch}>
        <Search size={16} />
        <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Клиент, вопрос или номер обращения" />
      </label>
      <div className={styles.supportFilters}>
        {filters.map(([value, text]) => <button key={value || "all"} type="button" className={statusFilter === value ? styles.tabActive : ""} onClick={() => setStatusFilter(value)}>{text}</button>)}
      </div>
      <div className={styles.supportAdvancedFilters}>
        <select aria-label="Приоритет поддержки" value={priorityFilter} onChange={event => setPriorityFilter(event.target.value)}>
          <option value="">Любой приоритет</option><option value="P0">P0 · критично</option><option value="P1">P1 · важно</option><option value="P2">P2 · обычный</option>
        </select>
        <select aria-label="Клиент поддержки" value={accountFilter} onChange={event => setAccountFilter(event.target.value)}>
          <option value="">Все клиенты</option>{filterOptions.accounts.map(value => <option key={value} value={value}>{value}</option>)}
        </select>
        <select aria-label="Категория поддержки" value={categoryFilter} onChange={event => setCategoryFilter(event.target.value)}>
          <option value="">Все категории</option>{filterOptions.categories.map(value => <option key={value} value={value}>{value}</option>)}
        </select>
        <select aria-label="Модуль поддержки" value={moduleFilter} onChange={event => setModuleFilter(event.target.value)}>
          <option value="">Все модули</option>{filterOptions.modules.map(value => <option key={value} value={value}>{value}</option>)}
        </select>
        <select aria-label="Период поддержки" value={periodFilter} onChange={event => setPeriodFilter(event.target.value)}>
          <option value="all">За всё время</option><option value="today">За сутки</option><option value="7d">За 7 дней</option>
        </select>
        <select aria-label="Исполнитель поддержки" value={assignmentFilter} onChange={event => setAssignmentFilter(event.target.value)}>
          <option value="all">Любой исполнитель</option><option value="unassigned">Без исполнителя</option><option value="assigned">Назначено</option>
        </select>
      </div>
    </div>

    {loading && !items.length ? <div className={styles.internalListEmpty}><LifeBuoy size={24} /><strong>Загружаем обращения…</strong></div> : null}

    <div className={styles.supportList}>
      {visibleItems.map(ticket => <article key={ticket.ticket_id} className={styles.supportTicket} data-priority={ticket.priority} data-owner={ticket.requires_owner ? "1" : "0"} data-sla={slaState(ticket, clockMs).key}>
        <div className={styles.supportTicketTop}>
          <div className={styles.supportClient}>
            <span className={styles.supportClientAvatar}>{(ticket.account_name || "К").slice(0, 1).toUpperCase()}</span>
            <span><strong>{ticket.account_name || "Без привязанного аккаунта"}</strong><small>Обращение №{ticket.ticket_id} · {ticket.category || "Поддержка"}{ticket.module ? " · " + ticket.module : ""}</small></span>
          </div>
          <div className={styles.supportTicketMeta}>
            {ticket.priority === "P0" || ticket.priority === "P1" ? <b><AlertTriangle size={14} /> {ticket.priority}</b> : null}
            {ticket.unread_count > 0 ? <em>{ticket.unread_count} новых</em> : null}
            {slaState(ticket, clockMs).label ? <i className={styles.supportSla} data-sla={slaState(ticket, clockMs).key}>{slaState(ticket, clockMs).label}</i> : null}
            <span>{age(ticket.last_message_at || ticket.updated_at, clockMs)}</span>
          </div>
        </div>
        <p className={styles.supportLastText}>{ticket.last_text || "Клиент открыл чат поддержки."}</p>
        <div className={styles.supportTicketBottom}>
          <span className={styles.supportStatus} data-status={ticket.status}>{label(ticket.status)}</span>
          {ticket.requires_owner ? <strong className={styles.supportRequiresOwner}>Требует ответа</strong> : null}
          <div>
            {ticket.status !== "RESOLVED" ? <button type="button" className={styles.secondaryAction} onClick={() => void updateStatus(ticket, ticket.status === "WAIT_CLIENT" ? "IN_PROGRESS" : "WAIT_CLIENT")}>{ticket.status === "WAIT_CLIENT" ? "Вернуть в работу" : "Ждём клиента"}</button> : null}
            {ticket.status !== "RESOLVED" ? <button type="button" className={styles.secondaryAction} onClick={() => void updateStatus(ticket, "RESOLVED")}>Решено</button> : null}
            <button type="button" className={styles.secondaryAction} onClick={() => void loadDiagnostics(ticket)} disabled={diagnosticsLoadingId === ticket.ticket_id}>{diagnosticsLoadingId === ticket.ticket_id ? "Проверяем…" : diagnosticsByTicket[ticket.ticket_id] ? "Скрыть диагностику" : "Диагностика BORIS"}</button>
            <button type="button" className={styles.primaryAction} disabled={!ticket.conversation_id} onClick={() => onOpenTicket(ticket)}>Открыть клиента</button>
          </div>
        </div>
        {diagnosticsByTicket[ticket.ticket_id] ? <div className={styles.supportDiagnostics}>
          <div className={styles.supportDiagnosticsHead}>
            <div><span className={styles.kicker}>ДИАГНОСТИКА BORIS</span><strong>{diagnosticsByTicket[ticket.ticket_id]?.account?.name || ticket.account_name}</strong></div>
            <small>Только безопасные служебные данные. Секреты и токены не загружаются.</small>
          </div>
          <div className={styles.supportDiagnosticsGrid}>
            <section>
              <span>MOP сегодня</span>
              <strong>{diagnosticsByTicket[ticket.ticket_id]?.mop?.sent_today ?? 0} отправлено</strong>
              <small>Человек: {diagnosticsByTicket[ticket.ticket_id]?.mop?.human_required_now ?? 0} · внешний блок: {diagnosticsByTicket[ticket.ticket_id]?.mop?.waiting_external_now ?? 0} · очередь: {diagnosticsByTicket[ticket.ticket_id]?.mop?.pending_now ?? 0}</small>
            </section>
            <section>
              <span>KPI / автопилот</span>
              <strong>{diagnosticsByTicket[ticket.ticket_id]?.kpi?.target_leads_per_day ?? "—"} лидов/день · CPL {diagnosticsByTicket[ticket.ticket_id]?.kpi?.red_cpl_rub ?? "—"} ₽</strong>
              <small>Режим: {diagnosticsByTicket[ticket.ticket_id]?.autopilot?.mode || "не задан"} · AI-маркетолог: {diagnosticsByTicket[ticket.ticket_id]?.autopilot?.full_ai_marketer ? "включён" : "выключен"}</small>
            </section>
            <section>
              <span>Фоновые задачи</span>
              <strong>{(diagnosticsByTicket[ticket.ticket_id]?.jobs || []).filter(job => ["queued","running","pending"].includes(String(job.status || "").toLowerCase())).length} активных</strong>
              <small>Последних задач: {(diagnosticsByTicket[ticket.ticket_id]?.jobs || []).length} · ошибок: {(diagnosticsByTicket[ticket.ticket_id]?.jobs || []).filter(job => Boolean(job.error_text)).length}</small>
            </section>
            <section>
              <span>Контроль модулей</span>
              <strong>{(diagnosticsByTicket[ticket.ticket_id]?.controls || []).filter(item => String(item.status || "").toLowerCase() === "pass").length} PASS</strong>
              <small>Блоков/ошибок: {(diagnosticsByTicket[ticket.ticket_id]?.controls || []).filter(item => item.block_reason || item.error_code || item.error_message).length}</small>
            </section>
            <section>
              <span>Уведомления 24 ч</span>
              <strong>{(diagnosticsByTicket[ticket.ticket_id]?.notifications || []).filter(item => String(item.status || "").toLowerCase() === "sent").length} отправлено</strong>
              <small>Ошибок: {(diagnosticsByTicket[ticket.ticket_id]?.notifications || []).filter(item => item.last_error || String(item.status || "").toLowerCase() === "failed").length}</small>
            </section>
            <section>
              <span>Кампании</span>
              <strong>{(diagnosticsByTicket[ticket.ticket_id]?.campaigns || []).length} последних</strong>
              <small>{(diagnosticsByTicket[ticket.ticket_id]?.campaigns || []).slice(0, 3).map(item => "#" + item.id + " " + (item.status || "—")).join(" · ") || "Нет кампаний"}</small>
            </section>
          </div>
          {(diagnosticsByTicket[ticket.ticket_id]?.mop?.reasons || []).length ? <div className={styles.supportDiagnosticsReasons}>
            <strong>Где нужен человек / внешний доступ</strong>
            {(diagnosticsByTicket[ticket.ticket_id]?.mop?.reasons || []).slice(0, 5).map((item, index) => <span key={String(index)}>{item.count} × {item.reason || "Причина не указана"}</span>)}
          </div> : null}
        </div> : null}
      </article>)}
      {!loading && !visibleItems.length ? <div className={styles.internalListEmpty}><LifeBuoy size={28} /><strong>Обращений по фильтру нет</strong><small>Новые вопросы клиентов появятся здесь автоматически.</small></div> : null}
    </div>
  </div>;
}
