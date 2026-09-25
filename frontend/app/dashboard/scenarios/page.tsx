"use client";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";
import { Card as BorisCard } from "@/app/ui";

import { Button } from "@/app/ui";


import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import styles from "./scenarios.module.css";
import { apiGet, apiPost, getAccount, openRoute } from "../../lib/api";
import { useAccounts } from "../../lib/AccountContext";

export default function ScenariosPage() {
  const { selectedAccountId } = useAccounts();
  const searchParams = useSearchParams();
  const explicitAccount = String(searchParams.get("account_id") || "").trim();
  const [account, setAccountState] = useState<string>("");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [wizard, setWizard] = useState<any>(null);
  const [values, setValues] = useState<any>({});
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    setData(null); setWizard(null); setValues({}); setErr("");
    setAccountState(explicitAccount || selectedAccountId || getAccount());
  }, [explicitAccount, selectedAccountId]);

  const load = useCallback((acc: string) => {
    // Каталог грузим и без аккаунта: «Запустить Авито с нуля» доступен новичку,
    // остальные приходят с available:false и понятной причиной.
    setLoading(true);
    setErr("");
    apiGet("/api/home/scenarios?account_id=" + encodeURIComponent(acc || ""))
      .then((d: any) => {
        if (d && d.status === "ok") {
          setData(d);
          return;
        }
        setData(null);
        setErr((d && (d.message || d.detail)) || "Не удалось загрузить бизнес-сценарии");
      })
      .catch(() => {
        setData(null);
        setErr("Не удалось загрузить бизнес-сценарии");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load(account);
  }, [account, load]);

  const openWizard = (s: any) => {
    setErr("");
    setValues({});
    setWizard(s);
  };

  const start = async () => {
    if (!wizard) return;
    const missing = (wizard.fields || [])
      .filter((f: any) => f.required && !String(values[f.key] || "").trim())
      .map((f: any) => f.label);
    if (missing.length) {
      setErr("Заполните: " + missing.join(", "));
      return;
    }
    setSaving(true);
    setErr("");
    const r = await apiPost("/api/home/scenario/start", {
      account_id: account,
      scenario_type: wizard.scenario_type,
      input_data: values,
    });
    setSaving(false);
    if (r && r.status === "ok" && r.scenario) {
      openRoute("/dashboard/scenarios/" + r.scenario.id + "?account_id=" + encodeURIComponent(account));
    } else {
      setErr((r && r.message) || "Не удалось запустить сценарий");
    }
  };

  return (
    <div data-testid="scenarios-root" className={styles.scenariosRoot}>
      <div className={styles.referenceStage}>
        <main className={styles.catalogColumn}>
        <div className={styles.head}>
          <h1 className={styles.h1}>Что вы хотите получить?</h1>
          <p className={styles.lead}>
            Выберите бизнес-задачу. БОРИС составит план и подключит нужные инструменты —
            вам не нужно выбирать, какой раздел открыть.
          </p>

          <details className={styles.howDetails}>
            <summary className={styles.howSummary}>
              <span className={styles.howSummaryText}>
                <strong>Как это работает</strong>
                <small>4 шага от задачи до готового плана</small>
              </span>
              <span className={styles.howChevron} aria-hidden="true">⌄</span>
            </summary>
            <div className={styles.howSteps}>
              <div className={styles.howStep}>
                <span className={styles.howNumber}>1</span>
                <div><strong>Выберите бизнес-задачу</strong><small>Не инструмент, а результат, который хотите получить.</small></div>
              </div>
              <div className={styles.howStep}>
                <span className={styles.howNumber}>2</span>
                <div><strong>BORIS уточнит данные</strong><small>Только те, которые нужны выбранному рабочему сценарию.</small></div>
              </div>
              <div className={styles.howStep}>
                <span className={styles.howNumber}>3</span>
                <div><strong>Покажет план</strong><small>Шаги, состояние и действия останутся в существующем механизме сценариев.</small></div>
              </div>
              <div className={styles.howStep}>
                <span className={styles.howNumber}>4</span>
                <div><strong>Выполняет доступные шаги</strong><small>Важные действия владельца остаются за владельцем.</small></div>
              </div>
            </div>
          </details>
        </div>

        {loading && <div className={styles.text}>Загружаю…</div>}

        {!loading && err && !wizard && (
          <BorisCard className={styles.card}>
            <div className={styles.title}>Сценарии временно недоступны</div>
            <div className={styles.text}>{err}</div>
            <div className={styles.foot}>
              <Button className={styles.btnGhost} onClick={() => load(account)}>Повторить</Button>
            </div>
          </BorisCard>
        )}

        {!loading && !err && !account && (
          <BorisCard className={styles.card}>
            <div className={styles.title}>Подключите аккаунт Авито</div>
            <div className={styles.text}>Первый сценарий можно запустить уже сейчас — он и проведёт вас через подключение.</div>
            <div className={styles.foot}>
              <Button className={styles.btnFill} onClick={() => openRoute("/dashboard?connect=avito")}>
                Добавить аккаунт
              </Button>
            </div>
          </BorisCard>
        )}

        {!loading && !err && data && (
          <>
            {data.recommended ? (() => {
              const rec = data.recommended;
              const recCard = (data.available || []).find((x: any) => x.scenario_type === rec.scenario_type);
              return (
                <BorisCard className={styles.card} data-testid="scenario-recommended">
                  <div className={styles.scenarioIcon} aria-hidden="true">→</div>
                  <div className={styles.title}>BORIS рекомендует начать отсюда</div>
                  <div className={styles.text}><b>{rec.title || recCard?.title}</b></div>
                  <div className={styles.text}>{humanizeBusinessText(rec.reason, "BORIS выбрал следующий шаг по текущему состоянию аккаунта.")}</div>
                  <div className={styles.foot}>
                    {rec.existing_id ? (
                      <Button className={styles.btnFill} onClick={() => openRoute("/dashboard/scenarios/" + rec.existing_id + "?account_id=" + encodeURIComponent(account))}>
                        Продолжить этот сценарий
                      </Button>
                    ) : rec.available && recCard ? (
                      <Button className={styles.btnFill} onClick={() => openWizard(recCard)}>
                        Начать рекомендуемый сценарий
                      </Button>
                    ) : (
                      <span className={styles.btnDisabled}>
                        {humanizeBusinessText(rec.blocked_reason, "Сначала нужно снять указанный блокер")}
                      </span>
                    )}
                  </div>
                </BorisCard>
              );
            })() : null}
            <div className={styles.grid} data-testid="scenarios-available">
              {(data.available || []).map((s: any, index: number) => (
                <BorisCard className={styles.card} key={s.scenario_type}>
                  <div className={styles.scenarioIcon} aria-hidden="true">{String(index + 1).padStart(2, "0")}</div>
                  <div className={styles.title}>{s.title}</div>
                  <div className={styles.text}>{s.lead}</div>
                  <div className={styles.mods}>
                    {(s.modules || []).map((m: string) => (
                      <span className={styles.mod} key={m}>{m}</span>
                    ))}
                  </div>
                  <div className={styles.foot}>
                    {s.existing_id ? (
                      <>
                        <Button
                          className={styles.btnFill}
                          onClick={() => openRoute("/dashboard/scenarios/" + s.existing_id + "?account_id=" + encodeURIComponent(account))}
                        >
                          Продолжить
                        </Button>
                        <Button className={styles.btnGhost} onClick={() => openWizard(s)}>
                          Изменить данные
                        </Button>
                      </>
                    ) : s.available === false ? (
                      <span className={styles.btnDisabled}
                            data-testid={"blocked-" + s.scenario_type}>
                        {humanizeBusinessText(s.blocked_reason, "Пока недоступно")}
                      </span>
                    ) : (
                      <Button
                        className={styles.btnFill}
                        onClick={() => openWizard(s)}
                        data-testid={"start-" + s.scenario_type}
                      >
                        Запустить
                      </Button>
                    )}
                  </div>
                </BorisCard>
              ))}
            </div>

            {(data.soon || []).length > 0 && (
              <>
                <h2 className={styles.h2}>Скоро</h2>
                <div className={styles.grid} data-testid="scenarios-soon">
                  {(data.soon || []).map((s: any) => (
                    <div className={styles.card + " " + styles.soon} key={s.scenario_type}>
                      <span className={styles.soonTag}>Скоро</span>
                      <div className={styles.title}>{s.title}</div>
                      <div className={styles.text}>
                        {humanizeBusinessText(s.blocked_reason, "Для этого сценария пока не подключена необходимая возможность.")}
                      </div>
                      <div className={styles.foot}>
                        <span className={styles.btnDisabled}>Технически заблокировано</span>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        )}

        </main>
      </div>

        {wizard && (
          <div className={styles.overlay} onClick={() => !saving && setWizard(null)}>
            <div className={styles.modal} onClick={(e: any) => e.stopPropagation()} data-testid="wizard">
              <h3 className={styles.modalTitle}>{wizard.title}</h3>
              <p className={styles.modalLead}>{wizard.lead}</p>

              {err && <div className={styles.err}>{err}</div>}

              {(wizard.fields || []).map((f: any) => (
                <div className={styles.field} key={f.key}>
                  <label className={styles.label}>
                    {f.label}
                    {f.required ? "" : " — необязательно"}
                  </label>
                  {f.type === "select" ? (
                    <select
                      className={styles.select}
                      value={values[f.key] || ""}
                      onChange={(e: any) => setValues({ ...values, [f.key]: e.target.value })}
                    >
                      <option value="">Выберите</option>
                      {(f.options || []).map((o: string) => (
                        <option key={o} value={o}>{o}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      className={styles.input}
                      type={f.type === "number" ? "number" : "text"}
                      placeholder={f.placeholder || ""}
                      value={values[f.key] || ""}
                      onChange={(e: any) => setValues({ ...values, [f.key]: e.target.value })}
                    />
                  )}
                  {f.placeholder && f.type !== "select" && (
                    <div className={styles.hint}>{f.placeholder}</div>
                  )}
                </div>
              ))}

              <div className={styles.plan}>
                <div className={styles.planTitle}>Что я сделаю</div>
                <ol className={styles.planList}>
                  {((wizard.plan && wizard.plan.length ? wizard.plan : wizard.modules) || []).map((m: string) => (
                    <li key={m}>{m}</li>
                  ))}
                </ol>
              </div>

              <div className={styles.modalFoot}>
                <Button className={styles.btnFill} onClick={start} disabled={saving} data-testid="wizard-start">
                  {saving ? "Запускаю…" : "Запустить план"}
                </Button>
                <Button className={styles.btn} onClick={() => setWizard(null)} disabled={saving}>
                  Отмена
                </Button>
              </div>
            </div>
          </div>
        )}
    </div>
  );
}
