"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "../../components/shell/AppShell";
import styles from "./home.module.css";
import { apiGet, apiPost, getAccount, openTab, openRoute } from "../../lib/api";

const QUICK = [
  { type: "avito_start", title: "Запустить Авито с нуля" },
  { type: "more_leads", title: "Получить больше заявок" },
  { type: "revive", title: "Оживить кабинет" },
];

function hello() {
  const h = new Date().getHours();
  if (h < 5) return "Доброй ночи";
  if (h < 12) return "Доброе утро";
  if (h < 18) return "Добрый день";
  return "Добрый вечер";
}

export default function HomePage() {
  const [account, setAccountState] = useState<string>("");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [avitoOpen, setAvitoOpen] = useState(false);
  const [keys, setKeys] = useState<any>({ client_id: "", client_secret: "", user_id: "" });
  const [keysBusy, setKeysBusy] = useState(false);
  const [keysErr, setKeysErr] = useState("");

  useEffect(() => {
    setAccountState(getAccount());
  }, []);

  const load = useCallback((acc: string) => {
    if (!acc) {
      setLoading(false);
      return;
    }
    setLoading(true);
    apiGet("/api/home/overview?account_id=" + encodeURIComponent(acc))
      .then((d: any) => {
        if (d && d.status === "ok") {
          setData(d);
          setErr("");
        } else {
          setErr((d && d.message) || "Не удалось загрузить данные");
        }
      })
      .catch(() => setErr("Не удалось загрузить данные"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    // P0.2: раньше при пустом account загрузка не запускалась вовсе и
    // loading висел вечно — клиент без аккаунта не видел кнопку подключения.
    // load сам обрабатывает пустое значение и снимает loading.
    load(account);
  }, [account, load]);

  const go = (item: any) => {
    // Ключи Avito добавляются к СУЩЕСТВУЮЩЕМУ аккаунту через готовый
    // /api/accounts/set_avito_keys. Раньше карточка вела на /agency, где форма
    // создаёт ВТОРОЙ аккаунт и считает за него доплату - это была ловушка.
    if (item.key === "no_avito") {
      setKeysErr("");
      setAvitoOpen(true);
      return;
    }
    if (item.route) openRoute(item.route);
    else if (item.tab) openTab(item.tab);
  };

  const saveKeys = async () => {
    if (!keys.client_id.trim() || !keys.client_secret.trim()) {
      setKeysErr("Заполните Client ID и Client Secret");
      return;
    }
    setKeysBusy(true);
    setKeysErr("");
    const r = await apiPost("/api/accounts/set_avito_keys", {
      account_id: account,
      avito_client_id: keys.client_id.trim(),
      avito_client_secret: keys.client_secret.trim(),
      avito_user_id: keys.user_id.trim(),
    });
    setKeysBusy(false);
    if (r && r.status === "ok") {
      setAvitoOpen(false);
      setKeys({ client_id: "", client_secret: "", user_id: "" });
      load(account);
    } else {
      setKeysErr((r && r.message) || "Не удалось сохранить ключи");
    }
  };

  const statusLine = () => {
    if (!data) return { dot: "⚪", text: "БОРИС ещё не запущен" };
    const a = data.attention || [];
    if (a.some((x: any) => x.priority === "critical")) return { dot: "🔴", text: "Требуется ваше действие" };
    if (a.some((x: any) => x.priority === "important")) return { dot: "🟡", text: "Ожидает решения" };
    if (data.numbers && data.numbers.active_items > 0) return { dot: "🟢", text: "БОРИС работает" };
    return { dot: "⚪", text: "БОРИС ещё не запущен" };
  };

  const s = statusLine();
  const num = (data && data.numbers) || {};
  const today = (data && data.today) || { lines: [], empty: true, total: 0 };
  const scen = data && data.active_scenario;

  return (
    <AppShell
      active="home"
      title="Рабочий стол"
      subtitle="Выберите бизнес-задачу — BORIS составит план и подключит нужные инструменты"
      oldTab="listings"
    >
      <div data-testid="home-root">
        <div className={styles.hero}>
          <div className={styles.status} data-testid="home-status">
            <span>{s.dot}</span>
            <span>{s.text}</span>
          </div>
          <h1 className={styles.heroTitle}>Что сделать сегодня?</h1>
          <p className={styles.heroLead}>
            Поставьте задачу — я составлю план, подключу нужные возможности
            и покажу, что от вас требуется.
          </p>
          <div className={styles.heroRow}>
            <button className={styles.primary} onClick={() => openTab("plan")} data-testid="btn-task">
              Поставить задачу БОРИСу
            </button>
            <button className={styles.onHero} onClick={() => openRoute("/dashboard/scenarios")} data-testid="btn-scenarios">
              Выбрать сценарий
            </button>
          </div>
        </div>

        {loading && <div className={styles.cardText}>Загружаю данные аккаунта…</div>}
        {err && !loading && <div className={styles.empty}><div className={styles.emptyTitle}>{err}</div></div>}

        {!loading && !account && (
          <div className={styles.empty} data-testid="home-no-account">
            <div className={styles.emptyTitle}>Аккаунт Avito ещё не подключён</div>
            <div className={styles.emptyText}>
              Добавьте аккаунт — после этого я смогу видеть объявления и работать с ними.
            </div>
            <button className={styles.btnFill} onClick={() => openRoute("/dashboard?connect=avito")}>Подключить аккаунт</button>
          </div>
        )}

        {!loading && data && (
          <>
            <div className={styles.section} data-testid="home-attention">
              <h2 className={styles.h}>Что требует внимания</h2>
              {(data.attention || []).length === 0 ? (
                <div className={styles.empty}>
                  <div className={styles.emptyTitle}>Всё в порядке</div>
                  <div className={styles.emptyText}>Срочных задач по этому аккаунту нет.</div>
                </div>
              ) : (
                <div className={styles.grid}>
                  {data.attention.map((c: any) => (
                    <div className={styles.card} key={c.key}>
                      <span className={styles.tag + " " + (styles as any)[c.priority]}>
                        {c.priority === "critical" ? "Критично"
                          : c.priority === "important" ? "Важно"
                          : c.priority === "opportunity" ? "Возможность" : "Информация"}
                      </span>
                      <div className={styles.cardTitle}>{c.title}</div>
                      <div className={styles.cardText}>{c.text}</div>
                      <div className={styles.cardFoot}>
                        <button className={styles.btn} onClick={() => go(c)}>{c.action}</button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {scen && (
              <div className={styles.section} data-testid="home-active-scenario">
                <h2 className={styles.h}>Текущий сценарий</h2>
                <div className={styles.card}>
                  <div className={styles.cardTitle}>{scen.title}</div>
                  <div className={styles.bar}>
                    <div className={styles.barFill} style={{ width: scen.progress.percent + "%" }} />
                  </div>
                  <div className={styles.barText}>
                    Выполнено {scen.progress.done} из {scen.progress.total} шагов
                  </div>
                  <ul className={styles.stepList}>
                    {scen.progress.steps.slice(0, 6).map((st: any) => (
                      <li className={styles.stepRow} key={st.key}>
                        <span>{st.status === "completed" ? "✅" : st.status === "current" ? "⏳" : st.status === "skipped" ? "➖" : "•"}</span>
                        <span className={st.status === "completed" ? styles.stepDone : ""}>{st.title}</span>
                      </li>
                    ))}
                  </ul>
                  <div className={styles.cardFoot}>
                    <button className={styles.btnFill} onClick={() => openRoute("/dashboard/scenarios/" + scen.id)}>
                      Продолжить сценарий
                    </button>
                  </div>
                </div>
              </div>
            )}

            <div className={styles.section} data-testid="home-today">
              <h2 className={styles.h}>Сегодня БОРИС</h2>
              {today.empty ? (
                <div className={styles.empty}>
                  <div className={styles.emptyTitle}>Сегодня я ещё не запускал задачи по этому аккаунту</div>
                  <div className={styles.emptyText}>Поставьте первую задачу или выберите сценарий.</div>
                  <button className={styles.btnFill} onClick={() => openRoute("/dashboard/scenarios")}>
                    Выбрать сценарий
                  </button>
                </div>
              ) : (
                <div className={styles.stats}>
                  {today.lines.map((l: any) => (
                    <div className={styles.stat} key={l.action}>
                      <div className={styles.statNum}>{l.count}</div>
                      <div className={styles.statLabel}>{l.title}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {num.has_stats && (
              <div className={styles.section} data-testid="home-numbers">
                <h2 className={styles.h}>Ваши объявления</h2>
                <div className={styles.stats}>
                  <div className={styles.stat}>
                    <div className={styles.statNum}>{num.active_items}</div>
                    <div className={styles.statLabel}>активных объявлений</div>
                  </div>
                  <div className={styles.stat}>
                    <div className={styles.statNum}>{num.views}</div>
                    <div className={styles.statLabel}>показов</div>
                  </div>
                  <div className={styles.stat}>
                    <div className={styles.statNum}>{num.contacts}</div>
                    <div className={styles.statLabel}>контактов</div>
                  </div>
                  <div className={styles.stat}>
                    <div className={styles.statNum}>{num.ready_to_publish}</div>
                    <div className={styles.statLabel}>готовы к публикации</div>
                  </div>
                </div>
                <div className={styles.barText} style={{ marginTop: "10px" }}>
                  Данные на {num.stats_date}
                </div>
              </div>
            )}

            <div className={styles.section} data-testid="home-journal">
              <h2 className={styles.h}>Что делал БОРИС</h2>
              {(data.journal || []).length === 0 ? (
                <div className={styles.empty}>
                  <div className={styles.emptyTitle}>Записей пока нет</div>
                  <div className={styles.emptyText}>
                    Здесь появится всё, что я сделаю по вашему аккаунту.
                  </div>
                </div>
              ) : (
                <div className={styles.log}>
                  {data.journal.map((e: any, i: number) => (
                    <div className={styles.logRow} key={i}>
                      <span className={styles.logTime}>
                        {String(e.ts || "").replace("T", " ").slice(5, 16)}
                      </span>
                      <span>{e.human || e.action}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className={styles.section} data-testid="home-quick">
              <h2 className={styles.h}>С чего начать</h2>
              <div className={styles.grid}>
                {QUICK.map((q) => (
                  <div className={styles.card} key={q.type}>
                    <div className={styles.cardTitle}>{q.title}</div>
                    <div className={styles.cardText}>
                      Составлю план и проведу по шагам, подключая нужные разделы.
                    </div>
                    <div className={styles.cardFoot}>
                      <button className={styles.btn} onClick={() => openRoute("/dashboard/scenarios")}>
                        Выбрать задачу
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
        {avitoOpen && (
          <div className={styles.overlay} onClick={() => !keysBusy && setAvitoOpen(false)}>
            <div className={styles.modal} onClick={(e: any) => e.stopPropagation()} data-testid="avito-modal">
              <h3 className={styles.modalTitle}>Подключение Avito</h3>
              <p className={styles.modalLead}>
                Ключи добавятся к вашему аккаунту. Новый аккаунт не создаётся, доплаты нет.
              </p>

              <div className={styles.tip}>
                <b>Где взять ключи</b>
                <div>Нужен тариф Avito «Расширенный» или «Максимальный».</div>
                <div>В кабинете Avito: Профиль и настройки → Интеграции и API.</div>
                <div>Client ID и Client Secret находятся внизу страницы.</div>
              </div>

              {keysErr && <div className={styles.errBox}>{keysErr}</div>}

              <div className={styles.field}>
                <label className={styles.label}>Client ID</label>
                <input className={styles.input} value={keys.client_id}
                  onChange={(e: any) => setKeys({ ...keys, client_id: e.target.value })} />
              </div>
              <div className={styles.field}>
                <label className={styles.label}>Client Secret</label>
                <input className={styles.input} value={keys.client_secret}
                  onChange={(e: any) => setKeys({ ...keys, client_secret: e.target.value })} />
              </div>
              <div className={styles.field}>
                <label className={styles.label}>User ID — необязательно</label>
                <input className={styles.input} value={keys.user_id}
                  onChange={(e: any) => setKeys({ ...keys, user_id: e.target.value })} />
              </div>

              <div className={styles.modalFoot}>
                <button className={styles.btnFill} onClick={saveKeys} disabled={keysBusy} data-testid="avito-save">
                  {keysBusy ? "Сохраняю…" : "Подключить"}
                </button>
                <button className={styles.btn} onClick={() => setAvitoOpen(false)} disabled={keysBusy}>
                  Отмена
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
