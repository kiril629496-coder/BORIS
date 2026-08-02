"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import HomeShell from "../../../components/HomeShell";
import styles from "../scenarios.module.css";
import { apiGet, apiPost, getAccount, openTab, openRoute } from "../../../lib/api";

// Реестр действий экрана «Категория пока не подключена».
// Кнопки рисуются циклом по этому массиву. Условие показа — ТОЛЬКО по
// машинным полям (category_state / category_reason). Тексты reason
// компонент не разбирает, они идут только на экран, поэтому формулировки
// можно менять на бэке без риска сломать интерфейс.
// Новое действие = новая строка здесь, компонент не трогается.
const CATEGORY_ACTIONS: any[] = [
  {
    key: "request",
    label: "Оставить заявку на подключение",
    enabled: true,
    show: (c: any) => c.category_state === "category_not_connected"
      || c.category_state === "not_found",
  },
  {
    key: "absent",
    label: "Моей категории здесь нет",
    enabled: true,
    show: (c: any) => c.category_state === "need_answer"
      || c.category_reason === "no_confident_option",
  },
  {
    // Включится, когда на бэке появится действие пересчёта категории
    // по введённому тексту и хранение переопределения.
    key: "other",
    label: "Выбрать другую категорию",
    enabled: false,
    show: (c: any) => !!c.category_state,
  },
];


const MARK: any = { completed: "✅", current: "⏳", skipped: "➖", later: "🕓", pending: "•" };

/* Подписи действий. Все ведут в СУЩЕСТВУЮЩИЕ эндпоинты avito.py через
   белый список /api/home/scenario/action. Опасных вызовов здесь нет:
   republish_apply исключён сознательно — он снимает живые объявления. */
const ACTION_LABEL: any = {
  republish_check: "Найти слабые объявления",
  republish_settings: "Показать пороги",
  set_republish_settings: "Запомнить пороги",
  auto_duplicate_run: "Показать, что размножу",
  seller_defaults: "Показать вопросы",
  card_fields: "Проверить карточки",
  parse_site: "Разобрать сайт",
  parsed_products: "Показать найденные товары",
  to_drafts: "Создать черновики",
  rewrite_draft: "Сделать тексты уникальными",
  feed_check: "Проверить файл у Avito",
};

export default function ScenarioPage() {
  const params = useParams();
  const rawId = (params as any)?.id;
  const id = Array.isArray(rawId) ? rawId[0] : rawId;

  const [account, setAccountState] = useState<string>("");
  const [scen, setScen] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [thr, setThr] = useState<any>({ views: "10", days: "7" });
  /* НЕПРЕРЫВНОСТЬ (стандарт, раздел 2): решение принимается ЗДЕСЬ, а не в кабинете.
     Найденные объявления показываем карточками с выбором — контекст не теряется. */
  const [picked, setPicked] = useState<any>({});
  const [confirmData, setConfirmData] = useState<any>(null);
  const [draftConfirm, setDraftConfirm] = useState<any>(null);
  const [seller, setSeller] = useState<any>({});
  const [cardAns, setCardAns] = useState<any>({});
  // Форма заявки на подключение категории: { [card_id]: {open, note, sent, err} }
  const [catReq, setCatReq] = useState<any>({});

  const submitCatRequest = async (c: any) => {
    const id = String(c.id);
    setCatReq((s: any) => ({ ...s, [id]: { ...(s[id] || {}), busy: true, err: "" } }));
    try {
      const r = await apiPost("/api/category-requests", {
        account_id: account,
        scenario_id: String((params as any)?.id || ""),
        category_query: c.category_query || "",
        category_state: c.category_state || "",
        category_reason: c.category_reason || "",
        path: c.path || "",
        question: c.category_question || "",
        options: c.category_options || [],
        context: {
          card_id: c.id,
          title: c.title || "",
          note: (catReq[id] && catReq[id].note) || "",
        },
      });
      if (r && r.ok) {
        setCatReq((s: any) => ({ ...s, [id]: { sent: true } }));
      } else {
        setCatReq((s: any) => ({
          ...s, [id]: { ...(s[id] || {}), busy: false,
                        err: "Не удалось отправить заявку" } }));
      }
    } catch (e: any) {
      setCatReq((s: any) => ({
        ...s, [id]: { ...(s[id] || {}), busy: false,
                      err: "Не удалось отправить заявку" } }));
    }
  };

  const MAX_BATCH = 10;

  useEffect(() => { setAccountState(getAccount()); }, []);

  const load = useCallback((acc: string) => {
    if (!acc || !id) { setLoading(false); return; }
    apiGet("/api/home/scenario/" + id + "?account_id=" + encodeURIComponent(acc))
      .then((d: any) => {
        if (d && d.status === "ok") { setScen(d.scenario); setErr(""); }
        else setErr((d && d.detail) || (d && d.message) || "Сценарий не найден");
      })
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    // Без аккаунта load() не вызывается, а значит setLoading(false) не наступает
    // никогда — страница висела на «Загружаю». Даём HomeShell время подставить
    // аккаунт, после чего честно выходим из загрузки и показываем состояние.
    if (account) { load(account); return; }
    const _noAccountTimer = setTimeout(() => {
      setLoading(false);
      setErr((e) => e || "Аккаунт не выбран");
    }, 3000);
    return () => clearTimeout(_noAccountTimer);
  }, [account, load]);

  const mark = async (stepKey: string, action: string) => {
    setBusy(stepKey + action);
    const r = await apiPost("/api/home/scenario/step", {
      account_id: account, scenario_id: Number(id), step_key: stepKey, action,
    });
    setBusy("");
    if (r && r.status === "ok") setScen(r.scenario);
    else setErr((r && r.message) || "Не удалось сохранить отметку");
  };

  const run = async (stepKey: string, action: string, payload?: any, confirm?: boolean) => {
    setBusy(stepKey + action);
    setErr("");
    const r = await apiPost("/api/home/scenario/action", {
      account_id: account, scenario_id: Number(id),
      step_key: stepKey, action, payload: payload || {}, confirm: !!confirm,
    });
    setBusy("");
    if (r && r.status === "ok") setScen(r.scenario);
    else setErr((r && r.message) || "Не удалось выполнить действие");
  };

  // Две фазы создания черновиков. Первый вызов без confirm возвращает
  // need_confirm и точное число — второй создаёт. Лимита нет намеренно:
  // черновики на Avito не уходят и обратимы, а каталог сайта — сотни позиций.
  // Настройки продавца: одинаковы для всех объявлений клиента, спрашиваем один раз.
  const saveSeller = async (values: any) => {
    setBusy("seller");
    await apiPost("/api/home/scenario/action", {
      account_id: account, scenario_id: Number(id),
      step_key: "seller", action: "seller_defaults",
      payload: { values }, confirm: true,
    });
    setBusy("");
    load(account);
  };

  // Уточнения по карточкам. Ответ на общий вопрос применяется сразу ко всем
  // карточкам, где этого поля не хватало — клиент не отвечает по сто раз.
  const saveCardAnswers = async (answers: any) => {
    setBusy("fields");
    await apiPost("/api/home/scenario/action", {
      account_id: account, scenario_id: Number(id),
      step_key: "fields", action: "card_fields",
      payload: { answers }, confirm: true,
    });
    setBusy("");
    setCardAns({});
    load(account);
  };

  const askDrafts = async (indices: number[]) => {
    setBusy("review");
    const r = await apiPost("/api/home/scenario/action", {
      account_id: account, scenario_id: Number(id),
      step_key: "review", action: "to_drafts",
      payload: { indices }, confirm: false,
    });
    setBusy("");
    const rr = r && r.result;
    if (rr && rr.need_confirm) setDraftConfirm({ indices, summary: rr.summary });
  };

  const doDrafts = async (indices: number[]) => {
    setBusy("review");
    await apiPost("/api/home/scenario/action", {
      account_id: account, scenario_id: Number(id),
      step_key: "drafts", action: "to_drafts",
      payload: { indices }, confirm: true,
    });
    setDraftConfirm(null);
    setPicked({});
    setBusy("");
    load(account);
  };

  const askRemove = async (ids: string[]) => {
    setBusy("apply");
    const r = await apiPost("/api/home/scenario/action", {
      account_id: account, scenario_id: Number(id),
      step_key: "apply", action: "republish_apply",
      payload: { item_ids: ids }, confirm: false,
    });
    setBusy("");
    const res = r && r.result;
    if (res && res.need_confirm) setConfirmData({ ...res, ids });
    else if (r && r.scenario) { setScen(r.scenario); setErr(res ? res.summary : ""); }
  };

  const doRemove = async () => {
    if (!confirmData) return;
    setBusy("apply");
    const r = await apiPost("/api/home/scenario/action", {
      account_id: account, scenario_id: Number(id),
      step_key: "apply", action: "republish_apply",
      payload: { item_ids: confirmData.ids }, confirm: true,
    });
    setBusy("");
    setConfirmData(null);
    setPicked({});
    if (r && r.status === "ok") setScen(r.scenario);
    else setErr((r && r.message) || "Не удалось снять объявления");
  };

  const finish = async (what: "complete" | "cancel") => {
    setBusy(what);
    const r = await apiPost("/api/home/scenario/" + what,
      { account_id: account, scenario_id: Number(id) });
    setBusy("");
    if (r && r.status === "ok") openRoute("/dashboard/home");
    else setErr((r && r.message) || "Не удалось изменить статус");
  };

  const go = (st: any) => { if (st.route) openRoute(st.route); else if (st.tab) openTab(st.tab); };

  const p = scen && scen.progress;
  const allDone = p && p.done >= p.total;

  return (
    <HomeShell active="scenarios" account={account} onAccountChange={setAccountState}>
      <div data-testid="scenario-root">
        <button className={styles.back} onClick={() => openRoute("/dashboard/home")}>
          ← На рабочий стол
        </button>

        {loading && <div className={styles.text}>Загружаю сценарий…</div>}
        {err && (
          <div className={styles.doneBox} data-testid="scenario-error">
            <div className={styles.doneTitle}>Сценарий недоступен</div>
            <div className={styles.barText} style={{ marginBottom: "14px" }}>
              {err}. Возможно, он был завершён или отменён — активные сценарии всегда
              видны на рабочем столе и в каталоге.
            </div>
            <div className={styles.finish}>
              <button className={styles.btnFill} onClick={() => openRoute("/dashboard/scenarios")}>
                Выбрать сценарий
              </button>
              <button className={styles.btn} onClick={() => openRoute("/dashboard/home")}>
                На рабочий стол
              </button>
            </div>
          </div>
        )}

        {!loading && scen && (
          <>
            <h1 className={styles.h1}>{scen.title}</h1>
            <div className={styles.meta}>
              <span>Запущен {scen.created_at ? String(scen.created_at).replace("T", " ").slice(0, 16) : "—"}</span>
              <span className={styles.badge + " " + (scen.status === "active" ? styles.badgeActive
                : scen.status === "completed" ? styles.badgeDone : styles.badgeOff)}>
                {scen.status === "active" ? "В работе" : scen.status === "completed" ? "Завершён" : "Отменён"}
              </span>
            </div>

            <div className={styles.progressBox} data-testid="scenario-progress">
              <div className={styles.planTitle}>{scen.lead}</div>
              <div className={styles.bar}><div className={styles.barFill} style={{ width: p.percent + "%" }} /></div>
              <div className={styles.barText}>Выполнено {p.done} из {p.total} шагов — {p.percent}%</div>
            </div>

            {allDone && (() => {
              const N = scen.numbers || {};
              const byKey: any = {};
              p.steps.forEach((s: any) => { if (s.result) byKey[s.key] = s.result; });
              const found = byKey.find ? byKey.find.count : null;
              const dup = byKey.dupes || null;
              const done: any[] = [];
              if (N.items_total != null) done.push(["Проверено объявлений", N.items_total]);
              if (found != null) done.push(["Найдено устаревших", found]);
              if (byKey.settings) done.push(["Пороги замены", "настроены"]);
              if (dup && dup.raw) {
                const made = dup.raw.created ?? 0;
                done.push([dup.dry_run ? "Дублей запланировано" : "Дублей создано",
                           dup.dry_run ? (dup.raw.planned ?? made) : made]);
              }
              if (N.active_items != null) done.push(["Активных объявлений сейчас", N.active_items]);
              return (
                <div className={styles.doneBox} data-testid="scenario-result">
                  <div className={styles.doneTitle}>Что это значит для бизнеса</div>
                  {(() => {
                    // управленческий вывод: доля слабых от общего числа
                    const total = N.items_total || 0;
                    const share = total && found != null ? found / total : 0;
                    const verdict = share >= 0.4
                      ? "Потенциал оживления кабинета высокий: работать перестала значительная часть объявлений."
                      : share >= 0.15
                      ? "Потенциал оживления средний: часть объявлений заметно просела."
                      : found
                      ? "Кабинет в неплохой форме — слабых объявлений немного."
                      : "Слабых объявлений не найдено, кабинет работает ровно.";
                    return (
                      <>
                        <p className={styles.verdict}>
                          Я проверил {total} {total % 10 === 1 && total % 100 !== 11 ? "объявление" : "объявлений"}
                          {found != null ? ", из них " + found + " рекомендую обновить" : ""}.
                        </p>
                        <p className={styles.verdict}>{verdict}</p>
                        <p className={styles.verdictNext}>
                          Следующий рекомендуемый сценарий: <b>«Получить больше заявок»</b> —
                          настроим цель по обращениям и продвижение под неё.
                        </p>
                      </>
                    );
                  })()}
                  <div className={styles.doneSub}>Цифры</div>
                  <table className={styles.resTable}>
                    <tbody>
                      {done.map(([k, v]: any) => (
                        <tr key={k}><td>{k}</td><td className={styles.resNum}>{v}</td></tr>
                      ))}
                    </tbody>
                  </table>

                  <div className={styles.doneSub}>Что сделал БОРИС</div>
                  <ul className={styles.doneList}>
                    {p.steps.filter((s: any) => s.result && s.result.summary)
                      .map((s: any) => <li key={s.key}>{s.result.summary}</li>)}
                    {p.steps.filter((s: any) => s.result && s.result.summary).length === 0 &&
                      <li>Действий не запускалось — шаги отмечены вручную</li>}
                  </ul>

                  <div className={styles.doneSub}>Что рекомендую дальше</div>
                  <ul className={styles.doneList}>
                    {found ? <li>Просмотреть {found} слабых объявлений и решить, какие снимать —
                      снятие делается во вкладке «Объявления», там видно каждое</li> : null}
                    <li>Выгрузить фид на Avito — публикацию БОРИС за вас не запускает</li>
                    {N.dead_items ? <li>У {N.dead_items} объявлений есть показы, но нет обращений —
                      обычно дело в фотографиях, заголовке или цене</li> : null}
                  </ul>

                  <div className={styles.finish}>
                    <button className={styles.btnFill} onClick={() => openTab("listings")}>
                      Открыть объявления
                    </button>
                    <button className={styles.btn} disabled={!!busy}
                            onClick={() => finish("complete")}>Завершить сценарий</button>
                  </div>
                </div>
              );
            })()}

            {p.steps.map((st: any) => {
              const isCurrent = st.status === "current";
              const isDone = st.status === "completed";
              const isOff = st.status === "skipped" || st.status === "later";
              const res = st.result;
              const working = busy.startsWith(st.key);
              return (
                <div className={styles.step + " " + (isCurrent ? styles.stepCurrent : "") + " " + (isOff ? styles.stepOff : "")}
                     key={st.key} data-testid="scenario-step">
                  <span className={styles.mark}>{MARK[st.status] || "•"}</span>
                  <div className={styles.stepBody}>
                    <div className={styles.stepTitle + " " + (isDone ? styles.stepTitleDone : "")}>{st.title}</div>
                    {st.hint && !isDone && <div className={styles.stepHint}>{st.hint}</div>}

                    {res && (
                      <div className={res.ok === false ? styles.resWarn : styles.resOk} data-testid="step-result">
                        <b>{res.summary}</b>
                        {res.note && <div className={styles.resNote}>{res.note}</div>}
                        {(res.items || []).length > 0 && res.count > 0 && !res.result_type && (
                          <div className={styles.resNext}>
                            Ничего из этого я не снял и не изменил. Посмотрите список
                            и решите сами — снятие делается в кабинете.
                          </div>
                        )}
                        {(res.items || []).length > 0 && (
                          <ul className={styles.resList}>
                            {res.items.slice(0, 6).map((it: any, i: number) => (
                              <li key={i}>{it.title} — <span className={styles.resDim}>{it.reason}</span></li>
                            ))}
                            {res.items.length > 6 && <li className={styles.resDim}>…и ещё {res.items.length - 6}</li>}
                          </ul>
                        )}
                        {res.needs_mode && (
                          <button className={styles.small} onClick={() => openTab("settings")}>
                            Открыть режим работы
                          </button>
                        )}
                        {res.settings && (
                          <div className={styles.resDim}>
                            Сейчас: {res.settings.min_views_no_contact} просмотров без обращений,
                            {" "}{res.settings.zero_views_days} дней без просмотров
                          </div>
                        )}
                      </div>
                    )}

                    {st.key === "fields" && (() => {
                      if (!res) {
                        return isCurrent ? (
                          <div className={styles.resDim}>
                            Нажмите «Проверить карточки» — покажу, что готово,
                            а чего не хватает.
                          </div>
                        ) : null;
                      }
                      const qs = res.questions || [];
                      const cards = res.cards || [];
                      return (
                        <div className={styles.pickBox} data-testid="cards-form">
                          <div className={styles.resDim}>
                            Готово к фиду: <b>{res.ready}</b> из {res.total}.
                            {res.need > 0 ? " Требуют уточнения: " + res.need + "." : ""}
                          </div>

                          {cards.filter((c: any) => !c.ready).slice(0, 8).map((c: any) => (
                            <div className={styles.pickRow} key={c.id}>
                              <span className={styles.pickBody}>
                                <span className={styles.pickTitle}>{c.title}</span>
                                <span className={styles.pickMeta}>
                                  {c.path ? c.path.split(" > ").slice(-2).join(" > ") : "категория не определена"}
                                </span>
                                <span className={styles.pickReason}>
                                  {c.reason ? c.reason
                                    : "не хватает: " + (c.missing || []).slice(0, 5).join(", ")}
                                </span>
                                {c.category_state && (() => {
                                  const rid = String(c.id);
                                  const st = catReq[rid] || {};
                                  const acts = CATEGORY_ACTIONS.filter((a: any) => a.enabled && a.show(c));
                                  if (st.sent) {
                                    return (
                                      <span style={{ display: "block", marginTop: "8px", fontSize: "14px",
                                                     color: "#0E7A4E" }}>
                                        Заявка отправлена. Подключим категорию и сообщим.
                                      </span>
                                    );
                                  }
                                  return (
                                    <span style={{ display: "block", marginTop: "8px" }}>
                                      {c.category_question && (
                                        <span style={{ display: "block", fontSize: "13.5px",
                                                       color: "#8A5A00", marginBottom: "6px" }}>
                                          {c.category_question}
                                          {(c.category_options || []).length > 0
                                            ? " — " + (c.category_options || []).join(", ")
                                            : ""}
                                        </span>
                                      )}
                                      {!st.open && acts.map((a: any) => (
                                        <button
                                          key={a.key}
                                          onClick={() => setCatReq((s: any) => ({
                                            ...s, [rid]: { ...(s[rid] || {}), open: true } }))}
                                          style={{ background: "#fff", color: "#1D2939",
                                                   border: "1.5px solid #E3E7F0", borderRadius: "10px",
                                                   padding: "8px 14px", marginRight: "8px", fontWeight: 600,
                                                   cursor: "pointer", fontSize: "14px" }}>
                                          {a.label}
                                        </button>
                                      ))}
                                      {st.open && (
                                        <span style={{ display: "block" }}>
                                          <textarea
                                            value={st.note || ""}
                                            placeholder="Что вы продаёте? Напишите своими словами — так быстрее подключим."
                                            onChange={(ev) => {
                                              const v = ev.target.value;
                                              setCatReq((s: any) => ({
                                                ...s, [rid]: { ...(s[rid] || {}), note: v } }));
                                            }}
                                            style={{ width: "100%", maxWidth: "520px", minHeight: "70px",
                                                     padding: "8px", borderRadius: "8px",
                                                     border: "1px solid #E3E7F0", fontSize: "14px",
                                                     color: "#1D2939", background: "#FFFFFF" }} />
                                          <span style={{ display: "block", marginTop: "6px" }}>
                                            <button
                                              disabled={!!st.busy}
                                              onClick={() => submitCatRequest(c)}
                                              style={{ background: "#2F6FED", color: "#fff", border: "none",
                                                       borderRadius: "10px", padding: "9px 16px",
                                                       marginRight: "8px", fontWeight: 600,
                                                       cursor: st.busy ? "default" : "pointer",
                                                       fontSize: "14px" }}>
                                              {st.busy ? "Отправляю…" : "Отправить заявку"}
                                            </button>
                                            <button
                                              onClick={() => setCatReq((s: any) => ({
                                                ...s, [rid]: { ...(s[rid] || {}), open: false } }))}
                                              style={{ background: "#fff", color: "#475467",
                                                       border: "1.5px solid #E3E7F0", borderRadius: "10px",
                                                       padding: "9px 16px", fontWeight: 600,
                                                       cursor: "pointer", fontSize: "14px" }}>
                                              Отмена
                                            </button>
                                          </span>
                                          {st.err && (
                                            <span style={{ display: "block", marginTop: "6px",
                                                           fontSize: "13.5px", color: "#D92D20" }}>
                                              {st.err}
                                            </span>
                                          )}
                                        </span>
                                      )}
                                    </span>
                                  );
                                })()}
                              </span>
                            </div>
                          ))}

                          {qs.length > 0 && (
                            <div style={{marginTop:"14px"}}>
                              <div className={styles.resDim}>
                                Ответьте один раз — подставлю во все карточки, где этого не хватает.
                              </div>
                              {qs.map((q: any) => (
                                <label className={styles.pickRow} key={q.tag}>
                                  <span className={styles.pickBody}>
                                    <span className={styles.pickTitle}>
                                      {q.label}
                                      <span className={styles.pickMeta}>
                                        {"  · нужен в " + q.cards + " карт."}
                                      </span>
                                    </span>
                                    <select
                                      value={cardAns[q.tag] || ""}
                                      onChange={e => setCardAns({ ...cardAns, [q.tag]: e.target.value })}
                                      style={{marginTop:"6px", width:"100%", maxWidth:"420px",
                                              padding:"8px", borderRadius:"8px",
                                              border:"1px solid #E3E7F0", fontSize:"15px",
                                              color:"#1D2939", background:"#FFFFFF"}}>
                                      <option value="">— пропустить —</option>
                                      {(q.options || []).map((o: string) => (
                                        <option key={o} value={o}>{o}</option>
                                      ))}
                                    </select>
                                  </span>
                                </label>
                              ))}
                              <button className={styles.smallFill} disabled={!!busy}
                                      data-testid="cards-save"
                                      onClick={() => saveCardAnswers(cardAns)}>
                                {busy === "fields" ? "БОРИС работает…"
                                  : "Применить ко всем (" + Object.keys(cardAns).filter(k => cardAns[k]).length + ")"}
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })()}

                    {st.key === "seller" && (() => {
                      const qs = (res && res.questions) || [];
                      if (!qs.length) {
                        return isCurrent ? (
                          <div className={styles.resDim}>
                            Нажмите «Показать вопросы» — их семь, и они одинаковы
                            для всех ваших объявлений.
                          </div>
                        ) : null;
                      }
                      const filled = qs.filter((q: any) => seller[q.tag] || q.value).length;
                      return (
                        <div className={styles.pickBox} data-testid="seller-form">
                          <div className={styles.resDim}>
                            Эти настройки Avito спрашивает почти у каждой категории.
                            Отвечу один раз — подставлю во все объявления.
                          </div>
                          {qs.map((q: any) => (
                            <label className={styles.pickRow} key={q.tag}>
                              <span className={styles.pickBody}>
                                <span className={styles.pickTitle}>{q.label}</span>
                                <select
                                  value={seller[q.tag] !== undefined ? seller[q.tag] : (q.value || "")}
                                  onChange={e => setSeller({ ...seller, [q.tag]: e.target.value })}
                                  style={{marginTop:"6px", width:"100%", maxWidth:"420px",
                                          padding:"8px", borderRadius:"8px",
                                          border:"1px solid #E3E7F0", fontSize:"15px",
                                          color:"#1D2939", background:"#FFFFFF"}}>
                                  <option value="">— выберите —</option>
                                  {q.options.map((o: string) => (
                                    <option key={o} value={o}>{o}</option>
                                  ))}
                                </select>
                              </span>
                            </label>
                          ))}
                          <button className={styles.smallFill} disabled={!!busy}
                                  data-testid="seller-save"
                                  onClick={() => {
                                    const v: any = {};
                                    qs.forEach((q: any) => {
                                      const val = seller[q.tag] !== undefined ? seller[q.tag] : q.value;
                                      if (val) v[q.tag] = val;
                                    });
                                    saveSeller(v);
                                  }}>
                            {busy === "seller" ? "БОРИС работает…"
                              : "Запомнить настройки (" + filled + " из " + qs.length + ")"}
                          </button>
                        </div>
                      );
                    })()}

                    {st.key === "review" && (() => {
                      const items = (res && res.items) || [];
                      if (!items.length) {
                        return isCurrent ? (
                          <div className={styles.resDim}>
                            Сначала я разберу сайт — найденные товары появятся здесь.
                          </div>
                        ) : null;
                      }
                      const sel = items.filter((i: any) => picked["p" + i.index]);
                      return (
                        <div className={styles.pickBox} data-testid="products-picker">
                          <div className={styles.resDim}>
                            Показываю {items.length} из {res.count || items.length}.
                            {" "}Отметьте товары, которые перенести в объявления.
                            {" "}Черновики видны только вам — на Avito ничего не уйдёт.
                          </div>
                          {items.map((it: any) => {
                            const on = !!picked["p" + it.index];
                            return (
                              <label className={styles.pickRow} key={it.index}>
                                <input type="checkbox" checked={on}
                                  onChange={() => setPicked({ ...picked, ["p" + it.index]: !on })} />
                                <span className={styles.pickBody}>
                                  <span className={styles.pickTitle}>{it.title}</span>
                                  <span className={styles.pickMeta}>
                                    {it.price ? Number(it.price).toLocaleString("ru-RU") + " \u20BD" : null}
                                    {it.photos ? " \u00B7 " + it.photos + " фото" : " \u00B7 без фото"}
                                  </span>
                                </span>
                              </label>
                            );
                          })}
                          {!draftConfirm && (
                            <button className={styles.smallFill} disabled={!sel.length || !!busy}
                                    onClick={() => askDrafts(sel.map((i: any) => i.index))}
                                    data-testid="products-create">
                              {busy === "review" ? "БОРИС работает…"
                                : sel.length ? "Создать черновики (" + sel.length + ")"
                                : "Отметьте товары"}
                            </button>
                          )}
                          {draftConfirm && (
                            <div className={styles.pickBox} data-testid="products-confirm">
                              <div className={styles.resDim}>{draftConfirm.summary}</div>
                              <button className={styles.smallFill} disabled={!!busy}
                                      onClick={() => doDrafts(draftConfirm.indices)}
                                      data-testid="products-confirm-yes">
                                {busy === "review" ? "БОРИС работает…" : "Да, создать"}
                              </button>
                              <button className={styles.small} disabled={!!busy}
                                      onClick={() => setDraftConfirm(null)}>
                                Отмена
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })()}

                    {st.key === "apply" && (() => {
                      const fnd = (p.steps.find((x: any) => x.key === "find") || {}).result;
                      const items = (fnd && fnd.items) || [];
                      if (!items.length) {
                        return isCurrent ? (
                          <div className={styles.resDim}>
                            Сначала выполните первый шаг — я найду объявления, и они появятся здесь.
                          </div>
                        ) : null;
                      }
                      const sel = items.filter((i: any) => picked[i.id]);
                      const limit = sel.length >= MAX_BATCH;
                      return (
                        <div className={styles.pickBox} data-testid="apply-picker">
                          <div className={styles.resDim}>
                            Показываю {items.length} из {fnd.count}. Отмечайте те, что снять —
                            не больше {MAX_BATCH} за раз. Пока вы не подтвердите, я ничего не меняю.
                          </div>
                          {fnd.has_variants && (
                            <div className={styles.variantNote}>
                              Некоторые объявления могут выглядеть одинаково. Это нормально — они
                              являются вариантами одного и того же предложения и отличаются только
                              текстом публикации. Обычно важно выбрать количество объявлений и нужный
                              город, а не конкретный вариант.
                            </div>
                          )}
                          {items.map((it: any) => {
                            const on = !!picked[it.id];
                            return (
                              <label className={styles.pickRow} key={it.id}>
                                <input type="checkbox" checked={on} disabled={!on && limit}
                                  onChange={() => setPicked({ ...picked, [it.id]: !on })} />
                                <span className={styles.pickBody}>
                                  <span className={styles.pickTitle}>{it.title}</span>
                                  <span className={styles.pickMeta}>
                                    {it.price ? Number(it.price).toLocaleString("ru-RU") + " ₽" : null}
                                    {it.address ? " · " + it.address : null}
                                    {it.is_variant ? " · вариант " + it.variant_no : null}
                                  </span>
                                  <span className={styles.pickReason}>{it.reason}</span>
                                  {it.first_of_group && (
                                    <span className={styles.pickHint}>
                                      ℹ Ниже {it.group_size} варианта одного предложения — отличаются только текстом
                                    </span>
                                  )}
                                </span>
                              </label>
                            );
                          })}
                          <button className={styles.smallFill} disabled={!sel.length || !!busy}
                                  onClick={() => askRemove(sel.map((i: any) => i.id))}
                                  data-testid="apply-remove">
                            {busy === "apply" ? "БОРИС работает…"
                              : sel.length ? "Снять выбранные (" + sel.length + ")"
                              : "Отметьте объявления"}
                          </button>
                        </div>
                      );
                    })()}

                    {st.action === "set_republish_settings" && isCurrent && (
                      <div className={styles.thr}>
                        <label className={styles.thrLbl}>Просмотров без обращений
                          <input className={styles.thrInp} type="number" value={thr.views}
                                 onChange={(e: any) => setThr({ ...thr, views: e.target.value })} />
                        </label>
                        <label className={styles.thrLbl}>Дней без просмотров
                          <input className={styles.thrInp} type="number" value={thr.days}
                                 onChange={(e: any) => setThr({ ...thr, days: e.target.value })} />
                        </label>
                      </div>
                    )}

                    {!isDone && (
                      <div className={styles.stepFoot}>
                        {st.action && (
                          <button className={styles.smallFill} disabled={!!busy}
                                  onClick={() => run(st.key, st.action,
                                    st.action === "set_republish_settings"
                                      ? { min_views_no_contact: Number(thr.views) || 10,
                                          zero_views_days: Number(thr.days) || 7, enabled: true }
                                      : {})}>
                            {working ? "БОРИС работает…" : (ACTION_LABEL[st.action] || "Выполнить")}
                          </button>
                        )}
                        {st.action === "auto_duplicate_run" && res && res.dry_run && (
                          <button className={styles.smallFill} disabled={!!busy}
                                  onClick={() => run(st.key, st.action, {}, true)}>
                            Создать дубли
                          </button>
                        )}
                        {(st.tab || st.route) && (
                          <button className={styles.small} onClick={() => go(st)}>Открыть</button>
                        )}
                        {isCurrent && !st.auto && (
                          <button className={styles.small} disabled={!!busy}
                                  onClick={() => mark(st.key, "done")}>Готово</button>
                        )}
                        {isCurrent && (
                          <>
                            <button className={styles.link} disabled={!!busy}
                                    onClick={() => mark(st.key, "later")}>Позже</button>
                            <button className={styles.link} disabled={!!busy}
                                    onClick={() => mark(st.key, "skip")}>Пропустить</button>
                          </>
                        )}
                      </div>
                    )}

                    {isOff && (
                      <div className={styles.stepFoot}>
                        <button className={styles.link} disabled={!!busy}
                                onClick={() => mark(st.key, "reset")}>Вернуть в план</button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}

            {scen.status === "active" && !allDone && (
              <div className={styles.finish}>
                <button className={styles.btnFill} disabled={!!busy} onClick={() => finish("complete")}>
                  Завершить сценарий
                </button>
                <button className={styles.danger} disabled={!!busy} onClick={() => finish("cancel")}>
                  Отменить
                </button>
              </div>
            )}
          </>
        )}
        {confirmData && (
          <div className={styles.overlay} onClick={() => !busy && setConfirmData(null)}>
            <div className={styles.modal} onClick={(e: any) => e.stopPropagation()}
                 data-testid="confirm-remove">
              <h3 className={styles.modalTitle}>Снять {confirmData.ids.length} и поставить замену</h3>
              {/* Предупреждение ВЫШЕ всего: последствия человек должен понять
                  раньше, чем начнёт разглядывать список. */}
              {confirmData.autopilot_warning && (
                <div className={styles.warnBox}>{confirmData.autopilot_warning}</div>
              )}
              <p className={styles.modalLead}>
                Эти объявления уйдут с Avito штатным способом, вместо них я поставлю новые
                того же направления.
              </p>
              <p className={styles.modalLead}>
                Если после подтверждения вы передумаете — объявление можно вернуть.
                Это не необратимая операция.
              </p>
              <ul className={styles.confirmList}>
                {(() => {
                  const fnd = (p.steps.find((x: any) => x.key === "find") || {}).result;
                  const byId: any = {};
                  ((fnd && fnd.items) || []).forEach((i: any) => { byId[i.id] = i; });
                  return confirmData.ids.map((cid: string) => (
                    <li key={cid}>{(byId[cid] && byId[cid].title) || cid}</li>
                  ));
                })()}
              </ul>
              <div className={styles.modalFoot}>
                <button className={styles.btnFill} disabled={!!busy} onClick={doRemove}
                        data-testid="confirm-yes">
                  {busy ? "Снимаю…" : "Снять эти " + confirmData.ids.length}
                </button>
                <button className={styles.btn} disabled={!!busy}
                        onClick={() => setConfirmData(null)}>Отмена</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </HomeShell>
  );
}
