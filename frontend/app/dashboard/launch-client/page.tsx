"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useAccounts } from "../../lib/AccountContext";
import { apiFetch, apiGet } from "../../lib/api";
import styles from "./page.module.css";

type Account = {
  account_id?: string;
  id?: string;
  name?: string;
  title?: string;
};

type LaunchResult = {
  status: "READY" | "NEEDS_INPUT" | "BLOCKED";
  reason: string;
  human_message: string;
  questions?: Array<{
    field?: string;
    fact_kind?: string;
    question_text?: string;
    reason?: string;
  }>;
  owner_items?: Array<{
    issue_type?: string;
    priority?: number;
    title?: string;
    detail?: string;
  }>;
  supervisor?: Record<string, unknown>;
};

type HandoffAction = {
  id?: string; module?: string; title?: string; human_description?: string; status?: string;
};

type HandoffState = {
  current_phase?: string; available_modules?: string[]; missing_modules?: string[];
  completed_actions?: HandoffAction[]; owner_required_actions?: HandoffAction[];
  current_action?: string | null; next_action?: string | null; result?: Record<string, unknown> | null;
};

type Lifecycle = {
  status: string; started?: boolean; steps_total?: number; steps_done?: number;
  working_now?: string | null; completed_steps?: Array<{key?: string; title?: string}>;
  waiting_reason?: string | null; owner_action_required?: unknown; result?: { post_prepared?: HandoffState; [key: string]: unknown } | null;
  next_action?: string | null; last_error?: string | null; retry_count?: number; next_retry_at?: string | null;
};

type CheckResponse = {
  status: string;
  mode: string;
  account_id: string;
  launch: LaunchResult;
};

const STATUS_TEXT: Record<string, string> = {
  READY: "Готов к запуску",
  NEEDS_INPUT: "Нужны данные",
  BLOCKED: "Запуск заблокирован",
};

const MODULE_LABELS: Record<string, string> = {
  "CRM": "Клиенты и сделки",
  "Integrations": "Интеграции",
  "Billing / tariff": "Тарифы и финансы",
  "Campaign / Ads": "Объявления и реклама",
  "AI Marketing": "ИИ-маркетолог",
  "Analytics": "Аналитика",
  "MOP": "ИИ-менеджер",
  "ROP": "Виртуальный РОП",
  "Reactivation": "Возврат клиентов",
  "Social": "Социальные сети",
  "Sitebuild": "Создание сайтов",
};

const MODULE_ROUTES: Record<string, string> = {
  "CRM": "/dashboard/crm",
  "Integrations": "/dashboard/crm/integrations",
  "Billing / tariff": "/dashboard/billing",
  "Campaign / Ads": "/dashboard/campaign",
  "AI Marketing": "/dashboard/marketing",
  "Analytics": "/dashboard/analytics",
  "MOP": "/ai",
  "ROP": "/rop",
  "Reactivation": "/reactivation",
  "Social": "/social",
  "Sitebuild": "/dashboard/sitebuild",
};


function humanText(value: unknown, fallback = "Готово к следующему шагу") {
  let text = String(value || "").trim();
  if (!text) return fallback;
  const replacements: Array<[RegExp,string]> = [
    [/OWNER_READY/gi, "Клиент готов к работе"],
    [/OWNER_REQUIRED/gi, "Требуется подтверждение владельца"],
    [/FAILED_RECOVERABLE/gi, "Нужно повторить внутренний шаг"],
    [/HANDOFF_RUNNING/gi, "Подключаем рабочие возможности"],
    [/PREPARED/gi, "Внутренняя подготовка завершена"],
    [/write[-_ ]?gate/gi, "подтверждение владельца"],
    [/provisioning/gi, "подготовка аккаунта"],
    [/factory/gi, "подготовка рабочего контура"],
  ];
  for (const [rx, label] of replacements) text = text.replace(rx, label);
  text = text.replaceAll("_", " ").replace(/\s+/g, " ").trim();
  if (!/[А-Яа-яЁё]/.test(text) && /[A-Za-z]{3,}/.test(text)) return fallback;
  return text;
}

function getAccountId(account: Account): string {
  return String(account.account_id || account.id || "");
}

function getAccountLabel(account: Account): string {
  return (
    account.name ||
    account.title ||
    account.account_id ||
    account.id ||
    "Без названия"
  );
}

export default function LaunchClientPage() {
  const router = useRouter();
  const { selectedAccountId, setSelectedAccount } = useAccounts();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountId, setAccountId] = useState("");
  const [result, setResult] = useState<LaunchResult | null>(null);
  const [lifecycle, setLifecycle] = useState<Lifecycle | null>(null);
  const [loadingAccounts, setLoadingAccounts] = useState(true);
  const [checking, setChecking] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;

    async function loadAccounts() {
      try {
        setLoadingAccounts(true);

        const data = await apiGet("/api/accounts/list");
        if (data?.status === "error") {
          throw new Error(data.message || "Не удалось загрузить аккаунты");
        }

        const rows: Account[] =
          Array.isArray(data)
            ? data
            : Array.isArray(data?.accounts)
            ? data.accounts
            : Array.isArray(data?.items)
            ? data.items
            : [];

        if (!active) return;

        setAccounts(rows);

        if (rows.length > 0) {
          const contextId = selectedAccountId && rows.some((row) => getAccountId(row) === selectedAccountId)
            ? selectedAccountId
            : getAccountId(rows[0]);
          setAccountId(contextId);
        }
      } catch (e) {
        if (!active) return;
        setError(e instanceof Error ? e.message : "Не удалось загрузить клиентов");
      } finally {
        if (active) setLoadingAccounts(false);
      }
    }

    loadAccounts();

    return () => {
      active = false;
    };
  }, []);

  const selectedAccount = useMemo(
    () =>
      accounts.find(
        (account) => getAccountId(account) === accountId
      ),
    [accounts, accountId]
  );

  function openWorkingModule(module: string) {
    const route = MODULE_ROUTES[module];
    if (!route || !accountId) return;
    setSelectedAccount(accountId);
    router.push(route);
  }

  async function checkClient() {
    if (!accountId) return;

    setChecking(true);
    setError("");

    try {
      const response = await apiFetch(
        `/api/factory/new-client-launch/${encodeURIComponent(accountId)}`,
        { cache: "no-store" }
      );

      if (!response.ok) {
        throw new Error(`Проверка запуска: HTTP ${response.status}`);
      }

      const data: CheckResponse = await response.json();

      setResult(data.launch);
      const lifecycleResponse = await apiFetch(`/api/factory/new-client-launch/${encodeURIComponent(accountId)}/lifecycle`, { cache: "no-store" });
      if (lifecycleResponse.ok) { const lifecycleData = await lifecycleResponse.json(); setLifecycle(lifecycleData.lifecycle); }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка проверки клиента");
    } finally {
      setChecking(false);
    }
  }

  async function launchClient() {
    if (!accountId) return;

    setLaunching(true);
    setError("");

    try {
      const postPrepared = ["PREPARED", "HANDOFF_RUNNING", "OWNER_REQUIRED", "FAILED_HANDOFF_RECOVERABLE", "OWNER_READY"].includes(lifecycle?.status || "");
      const response = await apiFetch(
        postPrepared
          ? `/api/factory/new-client-launch/${encodeURIComponent(accountId)}/handoff`
          : "/api/factory/new-client-launch/run",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: postPrepared ? undefined : JSON.stringify({ account_id: accountId }),
        }
      );

      const data = await response.json();

      if (!response.ok) {
        const detail =
          data?.detail?.message ||
          data?.detail?.human_message ||
          `HTTP ${response.status}`;

        throw new Error(detail);
      }

      if (data.after) setResult(data.after);
      setLifecycle(data.lifecycle || data.handoff?.lifecycle || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка запуска клиента");
    } finally {
      setLaunching(false);
    }
  }

  const handoff = lifecycle?.result?.post_prepared;
  const canLaunch = !!result && result.status === "READY" && lifecycle?.status !== "OWNER_READY";

  const nextAction = !result
    ? "Сначала проверьте клиента — BORIS сам определит, что мешает запуску."
    : result.status === "READY"
    ? "Клиент готов. Можно запустить безопасную внутреннюю настройку рабочего контура."
    : result.status === "NEEDS_INPUT"
    ? result.owner_items?.length
      ? "Нужно решение владельца. После него повторите проверку — до этого рабочий контур не запускается."
      : "Нужны недостающие данные. Заполните их в существующем контуре клиента и повторите проверку."
    : "Запуск временно недоступен. Сначала устраните указанную причину — BORIS покажет, что именно требует внимания.";

  return (
    <main data-boris-g34v2="launch-client" className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.eyebrow}>ГОТОВНОСТЬ К РАБОТЕ</div>
          <h1 data-boris-g34v2-heading="1">Готовность аккаунта</h1>
          <p>
            Здесь видно, готов ли аккаунт к работе, что уже настроено и что ещё требует внимания.
            После проверки BORIS сам продолжит доступные безопасные действия.
          </p>
        </div>
      </div>

      <section data-boris-g34v2-card="1" className={styles.card}>
        <div data-boris-g34v2-card="1" className={styles.cardHeader}>
          <div className={styles.iconChip}>✓</div>
          <div><h2 data-boris-g34v2-heading="1">Путь запуска нового клиента</h2><p>BORIS ведёт по шагам и не пропускает обязательные условия Avito.</p></div>
        </div>
        <div className={styles.list}>
          {[
            ["1","Подключить Avito по API","Client ID + Client Secret · BORIS проверит ключи и доступ к аккаунту","/dashboard/connect-avito"],
            ["2","Выбрать бизнес-цель","Зафиксировать, какой результат нужен бизнесу","/dashboard/scenarios"],
            ["3","Настроить KPI","План лидов, максимальная цена лида и режим автоматической работы","/dashboard/marketing"],
            ["4","Создать кампанию и объявления","Описать задачу обычными словами — BORIS соберёт структуру кампании","/dashboard/campaign"],
            ["5","Проверить категории и фид","Ответить только на обязательные параметры и пройти проверку Avito","/dashboard/campaign"],
            ["6","Согласовать и запустить в Avito","Внешняя выгрузка только после проверки и подтверждения владельца","/dashboard/campaign"],
          ].map(([n,title,text,route])=><a key={n} href={`${route}?account_id=${encodeURIComponent(accountId || "")}`} className={styles.listItem} style={{textDecoration:"none",color:"inherit"}}><div className={styles.listNumber}>{n}</div><div><strong>{title}</strong><p>{text}</p></div></a>)}
        </div>
        <div style={{marginTop:14,padding:"12px 14px",borderRadius:12,background:"#fff8e6",border:"1px solid #fde3a7",fontSize:13,lineHeight:1.5,color:"#7a4b00"}}><b>Обязательное условие Avito:</b> категория «Услуги» — активный тариф <b>«Расширенный»</b>; категория «Товары» — активный тариф <b>«Максимальный»</b>.</div>
      </section>

      <section data-boris-g34v2-card="1" className={styles.card}>
        <div data-boris-g34v2-card="1" className={styles.cardHeader}>
          <div className={styles.iconChip}>01</div>

          <div>
            <h2 data-boris-g34v2-heading="1">Выберите клиента</h2>
            <p>Аккаунт, который нужно проверить или запустить.</p>
          </div>
        </div>

        <div className={styles.controls}>
          <select
            value={accountId}
            onChange={(e) => {
              setAccountId(e.target.value);
              setSelectedAccount(e.target.value);
              setResult(null);
              setLifecycle(null);
              setError("");
            }}
            disabled={loadingAccounts}
            className={styles.select}
          >
            {loadingAccounts && (
              <option>Загрузка клиентов...</option>
            )}

            {!loadingAccounts && accounts.length === 0 && (
              <option value="">Клиенты не найдены</option>
            )}

            {accounts.map((account) => {
              const id = getAccountId(account);

              return (
                <option key={id} value={id}>
                  {getAccountLabel(account)}
                  {id ? ` — ${id}` : ""}
                </option>
              );
            })}
          </select>

          <button
            type="button"
            onClick={checkClient}
            disabled={!accountId || checking}
            className={styles.secondaryButton}
          >
            {checking ? "Проверяю..." : "Проверить готовность"}
          </button>
        </div>
      </section>

      {error && (
        <section data-boris-g34v2-card="1" className={`${styles.card} ${styles.errorCard}`}>
          <strong>Не удалось выполнить действие</strong>
          <p>{error}</p>
        </section>
      )}

      {result && (
        <>
          <section data-boris-g34v2-card="1" className={styles.card}>
            <div data-boris-g34v2-card="1" className={styles.cardHeader}>
              <div className={styles.iconChip}>02</div>

              <div>
                <h2 data-boris-g34v2-heading="1">Результат проверки</h2>
                <p>
                  {selectedAccount
                    ? getAccountLabel(selectedAccount)
                    : accountId}
                </p>
              </div>
            </div>

            <div data-boris-g34v2-card="1"
              className={`${styles.statusBox} ${
                result.status === "READY"
                  ? styles.ready
                  : result.status === "NEEDS_INPUT"
                  ? styles.warning
                  : styles.blocked
              }`}
            >
              <div data-boris-g34v2-card="1" className={styles.statusTitle}>
                {STATUS_TEXT[result.status] || humanText(result.status,"Состояние запуска уточняется")}
              </div>

              <div data-boris-g34v2-card="1" className={styles.statusReason}>
                {humanText(result.human_message,"BORIS проверяет следующий шаг запуска")}
              </div>

            </div>
          </section>

          {!!result.questions?.length && (
            <section data-boris-g34v2-card="1" className={styles.card}>
              <div data-boris-g34v2-card="1" className={styles.cardHeader}>
                <div className={styles.iconChip}>?</div>

                <div>
                  <h2 data-boris-g34v2-heading="1">Что нужно уточнить</h2>
                  <p>
                    Эти данные нужны для корректной работы аккаунта.
                  </p>
                </div>
              </div>

              <div className={styles.list}>
                {result.questions.map((item, index) => (
                  <div data-boris-g34v2-card="1" className={styles.listItem} key={index}>
                    <div className={styles.listNumber}>{index + 1}</div>

                    <div>
                      <strong>
                        {item.question_text || item.field || "Нужен дополнительный факт"}
                      </strong>

                      {item.reason && (
                        <p>{humanText(item.reason, "Нужно выполнить условие запуска")}</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {!!result.owner_items?.length && (
            <section data-boris-g34v2-card="1" className={styles.card}>
              <div data-boris-g34v2-card="1" className={styles.cardHeader}>
                <div className={styles.iconChip}>!</div>

                <div>
                  <h2 data-boris-g34v2-heading="1">Нужно решение владельца</h2>
                  <p>
                    Для этих действий требуется ваше подтверждение.
                  </p>
                </div>
              </div>

              <div className={styles.list}>
                {result.owner_items.map((item, index) => (
                  <div data-boris-g34v2-card="1" className={styles.listItem} key={index}>
                    <div className={styles.listNumber}>{index + 1}</div>

                    <div>
                      <strong>
                        {humanText(item.title || item.issue_type,"Требуется решение")}
                      </strong>

                      {item.detail && (
                        <p>{humanText(item.detail,"Нужно выполнить внешний шаг")}</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {lifecycle?.started && (
            <section data-boris-g34v2-card="1" className={styles.card}>
              <div className={styles.cardHeader}>
                <div className={styles.iconChip}>03</div>
                <div>
                  <h2 data-boris-g34v2-heading="1">Сейчас BORIS</h2>
                  <p>{humanText(lifecycle.working_now || lifecycle.waiting_reason, "Внутренняя подготовка завершена")}</p>
                </div>
              </div>
              <div className={styles.statusBox}>
                <div className={styles.statusTitle}>{lifecycle.status === "PREPARED" ? "Клиент подготовлен" : lifecycle.status === "FAILED_RECOVERABLE" ? "BORIS восстановит шаг" : "Запуск в работе"}</div>
                <div className={styles.statusReason}>Готово {lifecycle.steps_done || 0} из {lifecycle.steps_total || 0} внутренних шагов</div>
              </div>
              {!!lifecycle.completed_steps?.length && <div className={styles.list}>{lifecycle.completed_steps.map((step, index) => <div className={styles.listItem} key={step.key || index}><div className={styles.listNumber}>✓</div><div><strong>{step.title}</strong></div></div>)}</div>}
            </section>
          )}

          {handoff && (
            <section data-boris-g34v2-card="1" className={styles.card}>
              <div className={styles.cardHeader}>
                <div className={styles.iconChip}>04</div>
                <div>
                  <h2 data-boris-g34v2-heading="1">Готовность рабочих модулей</h2>
                  <p>{lifecycle?.status === "OWNER_READY" ? "Клиент готов к работе" : "BORIS завершает безопасную передачу в рабочий режим"}</p>
                </div>
              </div>
              {!!handoff.available_modules?.length && (
                <div className={styles.list}>
                  {handoff.available_modules.map((module) => (
                    <div className={styles.listItem} key={`available-${module}`}>
                      <div className={styles.listNumber}>✓</div>
                      <div style={{ flex: 1 }}>
                        <strong>{MODULE_LABELS[module] || module}</strong>
                        <p>Доступно по текущему набору возможностей.</p>
                        {lifecycle?.status === "OWNER_READY" && MODULE_ROUTES[module] ? (
                          <button type="button" className={styles.secondaryButton} onClick={() => openWorkingModule(module)}>
                            Открыть рабочий раздел
                          </button>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {!!handoff.owner_required_actions?.filter((item) => item.status !== "COMPLETED").length && (
                <div className={styles.list}>
                  {handoff.owner_required_actions.filter((item) => item.status !== "COMPLETED").map((item) => (
                    <div className={styles.listItem} key={item.id}><div className={styles.listNumber}>!</div><div><strong>{humanText(item.title, "Нужно действие владельца")}</strong>{item.human_description && <p>{humanText(item.human_description, "Требуется действие владельца")}</p>}</div></div>
                  ))}
                </div>
              )}
              {!!handoff.missing_modules?.length && (
                <div className={styles.list}>
                  {handoff.missing_modules.map((module) => (
                    <div className={styles.listItem} key={`missing-${module}`}><div className={styles.listNumber}>—</div><div><strong>{MODULE_LABELS[module] || module}</strong><p>Эта возможность сейчас не подключена к аккаунту.</p></div></div>
                  ))}
                </div>
              )}
            </section>
          )}

          <section data-boris-g34v2-card="1" className={styles.card}>
            <div className={styles.cardHeader}>
              <div className={styles.iconChip}>{lifecycle?.started ? "04" : "03"}</div>
              <div>
                <h2 data-boris-g34v2-heading="1">Что делать сейчас</h2>
                <p>{lifecycle?.next_action ? humanText(lifecycle.next_action, nextAction) : nextAction}</p>
              </div>
            </div>
          </section>

          <section data-boris-g34v2-card="1" className={styles.actionCard}>
            <div>
              <h2 data-boris-g34v2-heading="1">{lifecycle?.status === "PREPARED" ? "Продолжить до рабочего состояния" : lifecycle?.status === "OWNER_READY" ? "Клиент готов к работе" : "Запустить внутреннюю настройку"}</h2>
              <p>
                BORIS продолжит настройку автоматически там, где это безопасно. Действия, требующие вашего подтверждения, будут показаны отдельно.
              </p>
            </div>

            <button
              type="button"
              onClick={launchClient}
              disabled={!canLaunch || launching}
              className={styles.primaryButton}
            >
              {launching ? "Запускаю..." : lifecycle?.status === "PREPARED" ? "Продолжить настройку" : lifecycle?.status === "OWNER_READY" ? "Клиент готов" : result.status === "READY" ? "Запустить клиента" : "Сначала устранить блокер"}
            </button>
          </section>
        </>
      )}
    </main>
  );
}
