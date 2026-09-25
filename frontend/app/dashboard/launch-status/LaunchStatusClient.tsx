"use client";
import { Card as BorisCard } from "@/app/ui";
import { useAccounts } from "@/app/lib/AccountContext";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

import { Button } from "@/app/ui";


// ZERO_TOUCH_LAUNCH_UI_V1

import { useEffect, useMemo, useState } from "react";

type Blocker = {
  code: string;
  field?: string;
  title: string;
  action?: string;
  human_required?: boolean;
};

type AutomationItem = {
  field: string;
  job_kind: string;
  state: string;
  confirmation_status?: string | null;
  active: boolean;
  cron_expr?: string | null;
  source?: string | null;
};

type LaunchItem = {
  account_id: string;
  launch_status: string;
  launch_ready: boolean;
  readiness_pct: number;

  pending_decisions: number;
  approved_decisions: number;
  rejected_decisions: number;

  required_schedules: number;
  active_schedules: number;
  derived_values: number;

  health: {
    factory?: {
      status?: string | null;
      eligible_zero_touch?: boolean;
      readiness_pct?: number;
    };

    onboarding?: {
      present?: boolean;
    };

    knowledge?: {
      available?: boolean;
      count?: number | null;
    };

    automation?: {
      required?: number;
      active?: number;
      items?: AutomationItem[];
    };
  };

  blockers: Blocker[];

  updated_at?: string | null;
  launched_at?: string | null;
};

type Response = {
  status: string;

  summary: {
    total: number;
    launched: number;
    needs_decision: number;
    blocked: number;
    applying: number;
  };

  items: LaunchItem[];
};

const STATUS: Record<
  string,
  {
    title: string;
    text: string;
    tone: "green" | "yellow" | "red" | "blue";
  }
> = {
  LAUNCHED: {
    title: "Запущен",
    text: "BORIS может обслуживать аккаунт в текущей конфигурации.",
    tone: "green",
  },

  NEEDS_DECISION: {
    title: "Нужно решение",
    text: "BORIS подготовил действие, которое требует подтверждения владельца.",
    tone: "yellow",
  },

  APPLYING: {
    title: "Применяется",
    text: "Решение уже подтверждено. BORIS завершает настройку.",
    tone: "blue",
  },

  BLOCKED_NO_OWNER: {
    title: "Нет владельца",
    text: "Нужно связать аккаунт с владельцем.",
    tone: "red",
  },

  BLOCKED_CORE: {
    title: "Есть блокер",
    text: "Не выполнен обязательный системный контракт.",
    tone: "red",
  },

  BLOCKED_FACTORY: {
    title: "Рабочий контур не готов",
    text: "BORIS ещё не собрал полное состояние аккаунта.",
    tone: "red",
  },

  BLOCKED_AUTOMATION: {
    title: "Автоматизация не готова",
    text: "Нужное расписание ещё не доказано или не применено.",
    tone: "red",
  },

  NO_PRODUCTS: {
    title: "Нет продукта",
    text: "Для аккаунта не подключён продукт BORIS.",
    tone: "red",
  },

  NOT_ZERO_TOUCH_READY: {
    title: "Не готов",
    text: "Аккаунт пока не прошёл автоматическую проверку готовности.",
    tone: "red",
  },
};

function api(path: string) {
  const token = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: "Bearer " + token } : {}),
    },
  });
}

function toneStyle(
  tone: "green" | "yellow" | "red" | "blue"
): React.CSSProperties {
  const map = {
    green: {
      background: "#ecfdf3",
      color: "#067647",
    },

    yellow: {
      background: "#fff8e7",
      color: "#946200",
    },

    red: {
      background: "#fff1f1",
      color: "var(--b-danger-text)",
    },

    blue: {
      background: "var(--boris-blue-soft)",
      color: "#175cd3",
    },
  };

  return map[tone];
}

export default function LaunchStatusClient() {
  const { accounts } = useAccounts();
  const accountName = (id: string) => accounts.find((a) => String(a.account_id) === String(id))?.name || "Рабочий аккаунт";
  const [data, setData] = useState<Response | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");

    try {
      const response = await api(
        "/api/factory/launches"
      );

      const body = await response.json();

      if (!response.ok) {
        throw new Error(
          body?.detail
            ? humanizeBusinessText((body?.detail as any)?.message || body.detail,"Не удалось загрузить статус запуска")
            : "Не удалось загрузить статус запуска"
        );
      }

      setData(body);
    } catch (e: any) {
      setError(
        humanizeBusinessText(e?.message,"Не удалось загрузить статус запуска")
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const items = data?.items || [];

  const sorted = useMemo(() => {
    return [...items].sort((a, b) => {
      const priority = (x: LaunchItem) => {
        if (x.launch_status === "NEEDS_DECISION") return 0;
        if (!x.launch_ready) return 1;
        return 2;
      };

      return (
        priority(a) - priority(b) ||
        a.account_id.localeCompare(b.account_id)
      );
    });
  }, [items]);

  if (loading) {
    return (
      <main style={styles.page} className="boris-g35b-page">
        <div style={styles.loading}>
          Проверяю клиентов…
        </div>
      </main>
    );
  }

  return (
    <main style={styles.page} className="boris-g35b-page">
      <div style={styles.top}>
        <div>
          <h1 style={styles.title} className="boris-g35b-title">
            Запуск клиентов
          </h1>

          <p style={styles.subtitle} className="boris-g35b-subtitle">
            BORIS сам показывает, кто уже запущен,
            где нужно ваше решение и что мешает
            автоматическому старту.
          </p>
        </div>

        <Button
          onClick={load}
          style={styles.refresh}
        >
          Обновить
        </Button>
      </div>

      {error ? (
        <div style={styles.error}>
          <div>{error}</div>
          <Button onClick={load} style={{ marginTop: 12 }}>
            Повторить проверку
          </Button>
        </div>
      ) : null}

      {!error && data && sorted.length === 0 ? (
        <div style={styles.card}>
          <div style={styles.blockTitle}>Клиентов для запуска пока нет</div>
          <div style={styles.explain}>
            BORIS не видит ни одного клиентского контура. Добавьте клиента один раз — дальше готовность, блокеры и запуск BORIS будет контролировать сам.
          </div>
          <a href="/dashboard/launch-client" style={styles.decisionLink}>
            Добавить клиента
          </a>
        </div>
      ) : null}

      {!error && data ? (
        <div style={styles.metrics}>
          <Metric
            value={data.summary.total}
            label="Всего"
          />

          <Metric
            value={data.summary.launched}
            label="Запущено"
          />

          <Metric
            value={data.summary.needs_decision}
            label="Нужно решение"
          />

          <Metric
            value={data.summary.blocked}
            label="Блокеры"
          />
        </div>
      ) : null}

      {!error ? <div style={styles.stack}>
        {sorted.map((item) => {
          const meta =
            STATUS[item.launch_status] ||
            {
              title: humanizeBusinessText(item.launch_status,"Состояние запуска уточняется"),
              text: "",
              tone: "blue" as const,
            };

          const automation =
            item.health?.automation?.items || [];

          return (
            <section
              key={item.account_id}
              style={styles.card} className="boris-g35b-status-card"
            >
              <BorisCard style={styles.cardTop} className="boris-g35b-status-card">
                <div style={{ minWidth: 0 }}>
                  <BorisCard style={styles.account} className="boris-g35b-status-card">
                    {accountName(item.account_id)}
                  </BorisCard>

                  <div style={styles.meta}>
                    Готовность {item.readiness_pct}%
                    {" · "}
                    Данных определено: {item.derived_values}
                    {" · "}
                    Автоматизация: {item.active_schedules}/{item.required_schedules}
                  </div>
                </div>

                <div
                  style={{
                    ...styles.status,
                    ...toneStyle(meta.tone),
                  }}
                >
                  {meta.title}
                </div>
              </BorisCard>

              <div style={styles.explain}>
                {meta.text}
              </div>

              {item.pending_decisions > 0 ? (
                <a
                  href="/dashboard/factory-decisions"
                  style={styles.decisionLink}
                >
                  Открыть решения
                  {" · "}
                  {item.pending_decisions}
                </a>
              ) : null}

              {automation.length > 0 ? (
                <div style={styles.block}>
                  <div style={styles.blockTitle}>
                    Автоматизация
                  </div>

                  <div style={styles.rows}>
                    {automation.map((x) => (
                      <div
                        key={x.field}
                        style={styles.row}
                      >
                        <div>
                          <div style={styles.rowTitle}>
                            {x.field === "schedule.autopilot"
                              ? "Автопилот"
                              : x.field === "schedule.mop_poll"
                              ? "МОП — сообщения"
                              : humanizeBusinessText(x.field,"Автоматическая задача")}
                          </div>

                          <div style={styles.rowSub}>
                            {x.cron_expr
                              ? "Расписание настроено"
                              : humanizeBusinessText(x.confirmation_status || x.state,"Состояние уточняется")}
                          </div>
                        </div>

                        <div style={styles.smallStatus} className="boris-g35b-stat">
                          {humanizeBusinessText(x.state,"Состояние уточняется")}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {item.blockers?.length > 0 ? (
                <div style={styles.block}>
                  <div style={styles.blockTitle}>
                    Что мешает запуску
                  </div>

                  <div style={styles.rows}>
                    {item.blockers.map(
                      (blocker, index) => (
                        <BorisCard
                          key={
                            blocker.code +
                            ":" +
                            (blocker.field || "") +
                            ":" +
                            index
                          }
                          style={styles.blocker} className="boris-g35b-status-card"
                        >
                          <div>
                            <div style={styles.rowTitle}>
                              {humanizeBusinessText(blocker.title,"Есть препятствие для запуска")}
                            </div>

                            {blocker.action ? (
                              <div style={styles.rowSub}>
                                {humanizeBusinessText(blocker.action,"BORIS проверяет безопасный следующий шаг")}
                              </div>
                            ) : null}
                          </div>

                          <span
                            style={
                              blocker.human_required
                                ? styles.human
                                : styles.system
                            }
                          >
                            {blocker.human_required
                              ? "Нужно от вас"
                              : "BORIS"}
                          </span>
                        </BorisCard>
                      )
                    )}
                  </div>
                </div>
              ) : null}

              {item.launch_ready ? (
                <div style={styles.ready}>
                  ✓ BORIS обслуживает аккаунт автоматически
                  в текущем разрешённом контуре.
                </div>
              ) : null}
            </section>
          );
        })}
      </div> : null}
    </main>
  );
}

function Metric({
  value,
  label,
}: {
  value: number;
  label: string;
}) {
  return (
    <div style={styles.metric}>
      <div style={styles.metricValue}>
        {value}
      </div>

      <div style={styles.metricLabel}>
        {label}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100vh",
    padding: 32,
    background: "#f6f8fb",
    color: "#172033",
  },

  top: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 20,
    marginBottom: 22,
  },

  title: {
    margin: 0,
    fontSize: 32,
    lineHeight: 1.15,
    fontWeight: 760,
    letterSpacing: "-0.03em",
  },

  subtitle: {
    margin: "8px 0 0",
    maxWidth: 720,
    color: "var(--boris-text-2)",
    fontSize: 14,
    lineHeight: 1.55,
  },

  refresh: {
    border: "1px solid #dce2ea",
    borderRadius: 11,
    background: "#fff",
    color: "var(--boris-text-2)",
    padding: "10px 15px",
    fontWeight: 650,
    cursor: "pointer",
  },

  error: {
    padding: 14,
    borderRadius: 12,
    background: "#fff1f1",
    color: "var(--b-danger-text)",
    marginBottom: 16,
  },

  loading: {
    background: "#fff",
    border: "1px solid #e5e9f0",
    borderRadius: 18,
    padding: 24,
  },

  metrics: {
    display: "grid",
    gridTemplateColumns:
      "repeat(auto-fit,minmax(150px,1fr))",
    gap: 12,
    marginBottom: 18,
  },

  metric: {
    background: "#fff",
    border: "1px solid #e5e9f0",
    borderRadius: 16,
    padding: "16px 18px",
  },

  metricValue: {
    fontSize: 27,
    fontWeight: 760,
  },

  metricLabel: {
    marginTop: 3,
    color: "#7a8597",
    fontSize: 12,
  },

  stack: {
    display: "grid",
    gap: 14,
  },

  card: {
    background: "#fff",
    border: "1px solid #e5e9f0",
    borderRadius: 18,
    padding: 20,
    boxShadow:
      "0 4px 18px rgba(18,38,63,0.04)",
  },

  cardTop: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 16,
  },

  account: {
    fontSize: 16,
    fontWeight: 720,
    overflowWrap: "anywhere",
  },

  meta: {
    marginTop: 5,
    color: "#8a94a5",
    fontSize: 12,
  },

  status: {
    borderRadius: 999,
    padding: "7px 10px",
    whiteSpace: "nowrap",
    fontSize: 11,
    fontWeight: 700,
  },

  explain: {
    marginTop: 12,
    color: "var(--boris-text-2)",
    fontSize: 13,
    lineHeight: 1.5,
  },

  decisionLink: {
    display: "inline-block",
    marginTop: 12,
    padding: "9px 12px",
    borderRadius: 10,
    background: "#1769ff",
    color: "#fff",
    textDecoration: "none",
    fontSize: 12,
    fontWeight: 700,
  },

  block: {
    marginTop: 16,
    borderTop: "1px solid #eef1f5",
    paddingTop: 15,
  },

  blockTitle: {
    marginBottom: 9,
    fontSize: 12,
    fontWeight: 700,
    color: "var(--boris-text-2)",
  },

  rows: {
    display: "grid",
    gap: 8,
  },

  row: {
    padding: "11px 12px",
    borderRadius: 11,
    background: "#f8fafc",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
  },

  blocker: {
    padding: "11px 12px",
    borderRadius: 11,
    border: "1px solid #edf0f4",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
  },

  rowTitle: {
    fontSize: 13,
    fontWeight: 650,
  },

  rowSub: {
    marginTop: 3,
    color: "#8a94a5",
    fontSize: 11,
  },

  smallStatus: {
    color: "var(--boris-text-2)",
    fontSize: 10,
    fontWeight: 700,
  },

  human: {
    borderRadius: 999,
    padding: "5px 8px",
    background: "#fff8e7",
    color: "#946200",
    fontSize: 10,
    whiteSpace: "nowrap",
  },

  system: {
    borderRadius: 999,
    padding: "5px 8px",
    background: "var(--boris-blue-soft)",
    color: "#175cd3",
    fontSize: 10,
    whiteSpace: "nowrap",
  },

  ready: {
    marginTop: 15,
    padding: 11,
    borderRadius: 11,
    background: "#ecfdf3",
    color: "#067647",
    fontSize: 12,
    fontWeight: 650,
  },
};
