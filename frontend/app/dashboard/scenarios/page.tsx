"use client";

import { useCallback, useEffect, useState } from "react";
import HomeShell from "../../components/HomeShell";
import styles from "./scenarios.module.css";
import { apiGet, apiPost, getAccount, openRoute } from "../../lib/api";

export default function ScenariosPage() {
  const [account, setAccountState] = useState<string>("");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [wizard, setWizard] = useState<any>(null);
  const [values, setValues] = useState<any>({});
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    setAccountState(getAccount());
  }, []);

  const load = useCallback((acc: string) => {
    if (!acc) {
      setLoading(false);
      return;
    }
    setLoading(true);
    apiGet("/api/home/scenarios?account_id=" + encodeURIComponent(acc))
      .then((d: any) => {
        if (d && d.status === "ok") setData(d);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (account) load(account);
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
      openRoute("/dashboard/scenarios/" + r.scenario.id);
    } else {
      setErr((r && r.message) || "Не удалось запустить сценарий");
    }
  };

  return (
    <HomeShell active="scenarios" account={account} onAccountChange={setAccountState}>
      <div data-testid="scenarios-root">
        <div className={styles.head}>
          <h1 className={styles.h1}>Что вы хотите получить?</h1>
          <p className={styles.lead}>
            Выберите бизнес-задачу. БОРИС составит план и подключит нужные инструменты —
            вам не нужно выбирать, какой раздел открыть.
          </p>
        </div>

        {loading && <div className={styles.text}>Загружаю…</div>}

        {!loading && !account && (
          <div className={styles.card}>
            <div className={styles.title}>Сначала подключите аккаунт Avito</div>
            <div className={styles.text}>Без аккаунта сценарии запускать не на чем.</div>
            <div className={styles.foot}>
              <button className={styles.btnFill} onClick={() => openRoute("/agency")}>
                Добавить аккаунт
              </button>
            </div>
          </div>
        )}

        {!loading && data && (
          <>
            <div className={styles.grid} data-testid="scenarios-available">
              {(data.available || []).map((s: any) => (
                <div className={styles.card} key={s.scenario_type}>
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
                        <button
                          className={styles.btnFill}
                          onClick={() => openRoute("/dashboard/scenarios/" + s.existing_id)}
                        >
                          Продолжить
                        </button>
                        <button className={styles.btnGhost} onClick={() => openWizard(s)}>
                          Изменить данные
                        </button>
                      </>
                    ) : (
                      <button
                        className={styles.btnFill}
                        onClick={() => openWizard(s)}
                        data-testid={"start-" + s.scenario_type}
                      >
                        Запустить
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <h2 className={styles.h2}>Скоро</h2>
            <div className={styles.grid} data-testid="scenarios-soon">
              {(data.soon || []).map((s: any) => (
                <div className={styles.card + " " + styles.soon} key={s.scenario_type}>
                  <span className={styles.soonTag}>Скоро</span>
                  <div className={styles.title}>{s.title}</div>
                  <div className={styles.text}>
                    Появится после запуска первых трёх сценариев.
                  </div>
                  <div className={styles.foot}>
                    <span className={styles.btnDisabled}>Пока недоступно</span>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

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
                <button className={styles.btnFill} onClick={start} disabled={saving} data-testid="wizard-start">
                  {saving ? "Запускаю…" : "Запустить план"}
                </button>
                <button className={styles.btn} onClick={() => setWizard(null)} disabled={saving}>
                  Отмена
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </HomeShell>
  );
}
