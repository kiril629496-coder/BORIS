"use client";

import { useEffect, useMemo, useState } from "react";
import { useAccounts } from "../../lib/AccountContext";
import { apiGet, apiPost } from "../../lib/api";
import styles from "./page.module.css";
import { humanizeBusinessText } from "../../lib/humanizeBusinessText";

type Connection = "loading" | "not_connected" | "connected" | "error" | "tariff_error";

type Status = { connection: Connection; message: string; avito_user_id?: string; tariff_ok?: boolean | null };

export default function ConnectAvitoPage() {
  const { accounts, selectedAccountId, setSelectedAccount, reload } = useAccounts();
  const [accountId, setAccountId] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [status, setStatus] = useState<Status>({ connection: "loading", message: "Проверяю подключение…" });
  const [busy, setBusy] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [browserSession, setBrowserSession] = useState<any>(null);
  const [executionHealth, setExecutionHealth] = useState<any>(null);
  const [recentReceipt, setRecentReceipt] = useState<any>(null);
  const [isolatedStatus, setIsolatedStatus] = useState<any>(null);
  const [pairCode, setPairCode] = useState("");

  useEffect(() => {
    if (!accounts.length) return;
    const next = selectedAccountId && accounts.some((a) => a.account_id === selectedAccountId)
      ? selectedAccountId : accounts[0].account_id;
    setAccountId(next);
  }, [accounts, selectedAccountId]);

  useEffect(() => {
    if (!accountId) return;
    let active = true;
    setStatus({ connection: "loading", message: "Проверяю подключение…" });
    setPairCode("");
    const loadSession = async () => {
      const [sessionData, healthData, receiptData, isolatedData] = await Promise.all([
        apiGet(`/api/browser-gateway/sessions/${encodeURIComponent(accountId)}`),
        apiGet(`/api/browser-gateway/execution-health/${encodeURIComponent(accountId)}`),
        apiGet(`/api/browser-gateway/receipts/${encodeURIComponent(accountId)}?limit=1`),
        apiGet(`/api/avito/mass-editor/isolated/status?account_id=${encodeURIComponent(accountId)}`),
      ]);
      if (!active) return;
      setBrowserSession(sessionData?.session || null);
      setExecutionHealth(healthData || null);
      setRecentReceipt(receiptData?.receipts?.[0] || null);
      setIsolatedStatus(isolatedData || null);
    };
    apiGet(`/api/accounts/avito_connection?account_id=${encodeURIComponent(accountId)}&verify=true`)
      .then((data) => {
        if (!active) return;
        if (data?.status === "error") setStatus({ connection: "error", message: data.message || "Не удалось проверить подключение", tariff_ok: data?.tariff_ok });
        else setStatus({ connection: data?.connection || "not_connected", message: data?.message || "Не подключено", avito_user_id: data?.avito_user_id, tariff_ok: data?.tariff_ok });
      });
    loadSession();
    const timer = window.setInterval(loadSession, 10000);
    return () => { active = false; window.clearInterval(timer); };
  }, [accountId]);

  const account = useMemo(() => accounts.find((a) => a.account_id === accountId), [accounts, accountId]);

  async function verifyExisting() {
    if (!accountId || busy) return;
    setBusy(true);
    const data = await apiGet(`/api/accounts/avito_connection?account_id=${encodeURIComponent(accountId)}&verify=true`);
    setBusy(false);
    if (data?.status === "error") setStatus({ connection: "error", message: data.message || "Ошибка проверки", tariff_ok: data?.tariff_ok });
    else setStatus({ connection: data?.connection || "error", message: data?.message || "Ошибка проверки", avito_user_id: data?.avito_user_id, tariff_ok: data?.tariff_ok });
  }

  async function connect() {
    if (!accountId || !clientId.trim() || !clientSecret.trim() || busy) return;
    setBusy(true);
    setStatus({ connection: "loading", message: "Проверяю ключи в Авито…" });
    const data = await apiPost("/api/accounts/connect_avito", {
      account_id: accountId,
      client_id: clientId.trim(),
      client_secret: clientSecret.trim(),
    });
    setBusy(false);
    if (data?.connection === "connected") {
      setStatus({ connection: "connected", message: data?.message || "Подключено", avito_user_id: data.avito_user_id, tariff_ok: data?.tariff_ok });
      setClientSecret("");
      reload();
      return;
    }
    setStatus({ connection: data?.connection || "error", message: data?.message || "Ошибка подключения", tariff_ok: data?.tariff_ok });
  }


  async function createBrowserPairCode() {
    if (!accountId || busy) return;
    setBusy(true);
    const data = await apiPost(`/api/avito/mass-editor/gateway/pair-code?account_id=${encodeURIComponent(accountId)}`, {});
    setBusy(false);
    if (data?.code) setPairCode(data.code);
  }

  const good = status.connection === "connected";
  const bad = status.connection === "error" || status.connection === "tariff_error";
  const browserIdentityMismatch = browserSession?.state === "identity_mismatch" || browserSession?.identity_mismatch === true;
  const browserReady = Boolean(browserSession?.online && !browserIdentityMismatch && !browserSession?.requires_login);
  const isolatedSession = isolatedStatus?.session || {};
  const isolatedRoute = isolatedStatus?.route || {};
  const isolatedReady = isolatedSession?.ready === true && isolatedSession?.state === "ready";
  const isolatedReason = String(isolatedRoute?.reason || isolatedSession?.route_reason || "");
  const isolatedReasonLabel = isolatedReason === "proxy_transport_down" ? "Сетевой маршрут не отвечает"
    : isolatedReason === "proxy_auth_failed" ? "Нет доступа к сетевому маршруту"
      : isolatedReason === "proxy_transport_degraded" ? "Сетевой маршрут нестабилен"
        : isolatedReason === "no_avito_usable_exit" ? "Нет доступного маршрута до Avito"
          : isolatedReason ? "Сетевой маршрут требует проверки" : "";
  const isolatedRouteMessage = isolatedReason === "proxy_transport_down"
    ? "Внешний прокси-провайдер сейчас не принимает соединения. BORIS остановил браузерные задачи до восстановления маршрута."
    : isolatedReason === "proxy_auth_failed"
      ? "Прокси-провайдер отклонил авторизацию. Нужно восстановить доступ к сетевому маршруту."
      : isolatedReason === "proxy_transport_degraded"
        ? "Сетевой маршрут до Avito нестабилен. BORIS временно остановил браузерные задачи."
        : isolatedReason === "no_avito_usable_exit"
          ? "Провайдер доступен, но сейчас нет проверенного маршрута до Avito."
          : isolatedSession?.state === "waiting_avito_code"
            ? "Avito запросил код подтверждения. После ввода кода BORIS продолжит эту же изолированную сессию."
            : isolatedSession?.state === "captcha_required"
              ? "Avito запросил дополнительную проверку. Автоматические изменения остановлены."
              : "Серверная сессия включится автоматически, когда модуль BORIS для браузера недоступен и есть безопасный сетевой маршрут.";

  return (
      <main className={styles.page}>
        <section className={styles.hero}>
          <div className={styles.heroIcon}>A</div>
          <div>
            <div className={styles.eyebrow}>ИНТЕГРАЦИЯ AVITO</div>
            <h1>Подключите рабочий аккаунт</h1>
            <p>Свяжите нужный аккаунт с BORIS через ключ приложения (Client ID) и секретный ключ (Client Secret). Это названия полей в Авито. BORIS проверяет ключи напрямую и хранит их в зашифрованном виде.</p>
          </div>
        </section>

        <div className={styles.workspace}>
      <section className={`${styles.card} ${styles.accountPanel}`}>
        <div className={styles.cardHead}>
          <div>
            <h2>Аккаунт для подключения</h2>
            <p>Выберите рабочий аккаунт BORIS. Ключи сохранятся только в нём.</p>
          </div>
          <span className={`${styles.status} ${good ? styles.good : bad ? styles.bad : styles.neutral}`}>
            <i />{status.connection === "loading" ? "Проверка…" : good ? "API подключён" : status.connection === "tariff_error" ? "API подключён" : bad ? "Ошибка API" : "Не подключено"}
          </span>
        </div>

        <label className={styles.label}>Аккаунт BORIS</label>
        <select className={styles.input} value={accountId} onChange={(e) => { setAccountId(e.target.value); setSelectedAccount(e.target.value); }}>
          {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.name}</option>)}
        </select>

        <div className={styles.statusBox} data-kind={status.connection === "error" ? "error" : (good || status.connection === "tariff_error") ? "ok" : "neutral"}>
          <strong>{account?.name || "Аккаунт"}</strong>
          <span>{status.message}</span>
          {status.avito_user_id ? <small>Авито ID: {status.avito_user_id}</small> : null}
        </div>
        {good ? <div className={styles.nextStep}><strong>✓ API подключён. Следующий шаг — выбрать бизнес-цель</strong><div>После цели настройте KPI, затем создайте кампанию и фид.</div><div className={styles.actionRow}><a href={`/dashboard/scenarios?account_id=${encodeURIComponent(accountId)}`} className={styles.primary}>Выбрать бизнес-цель</a><a href={`/dashboard/marketing?account_id=${encodeURIComponent(accountId)}`} className={styles.secondary}>Настроить KPI</a></div></div> : null}

        {good && <button className={`${styles.secondary} ${styles.fullButton}`} type="button" onClick={verifyExisting} disabled={busy}>{busy ? "Проверяю…" : "Проверить подключение ещё раз"}</button>}
      </section>

      <section className={`${styles.card} ${styles.connectPanel}`}>
        <div className={styles.stepTitle}><span>01</span><div><h2>Введите ключи Авито</h2><p>В настройках API Авито найдите «Client ID» и «Client Secret». В BORIS это ключ приложения и секретный ключ.</p></div></div>
        <div className={styles.grid}>
          <label><b>Ключ приложения (Client ID)</b><input className={styles.input} value={clientId} onChange={(e) => setClientId(e.target.value)} autoComplete="off" placeholder="Вставьте Client ID из Авито" /></label>
          <label><b>Секретный ключ (Client Secret)</b><div className={styles.secretWrap}><input className={styles.input} type={showSecret ? "text" : "password"} value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} autoComplete="new-password" placeholder="Вставьте Client Secret из Авито" /><button type="button" onClick={() => setShowSecret((v) => !v)}>{showSecret ? "Скрыть" : "Показать"}</button></div></label>
        </div>
        <div className={styles.tariffNote} data-kind={good && status.tariff_ok === true ? "ok" : status.connection === "tariff_error" ? "error" : status.connection === "loading" ? "neutral" : "warning"}>
          <strong>{good && status.tariff_ok === true ? "Автозагрузка подтверждена" : status.connection === "tariff_error" ? "Avito явно отклонил автозагрузку" : good && status.tariff_ok == null ? "API подключён · автозагрузку проверяем отдельно" : status.connection === "loading" ? "Проверяю подключение" : "Перед подключением проверьте тариф Авито"}</strong>
          <span>{good && status.tariff_ok === true ? "Avito подтвердил доступ к автозагрузке для этого аккаунта." : status.connection === "tariff_error" ? "API работает, но Avito отдельно сообщил об отсутствии доступа к автозагрузке." : good && status.tariff_ok == null ? "Avito не дал надёжного ответа через API о статусе автозагрузки. Это не означает, что она выключена. Если автозагрузка включена в кабинете Avito, BORIS не должен показывать это как ошибку подключения." : status.connection === "loading" ? "BORIS проверяет ключи и сам аккаунт Avito." : "Для категории «Услуги» нужен тариф «Расширенный». Для товаров — «Максимальный»."}</span>
        </div>
        <button className={styles.primary} type="button" onClick={connect} disabled={busy || !accountId || !clientId.trim() || !clientSecret.trim()}>{busy ? "Проверяю подключение…" : good ? "Обновить ключи и проверить" : "Подключить Авито"}</button>
      </section>
      </div>


      <section className={`${styles.card} ${styles.connectPanel}`}>
        <div className={styles.stepTitle}><span>02</span><div><h2>Фоновая сессия BORIS в браузере</h2><p>Нужна только для действий, которых нет в API Avito. Сессия привязывается строго к выбранному аккаунту.</p></div></div>
        <div className={styles.statusBox} data-kind={browserIdentityMismatch ? "error" : browserReady ? "ok" : "neutral"}>
          <strong>{browserIdentityMismatch ? "сессия BORIS в браузере заблокирована: открыт другой профиль Avito" : browserReady ? "✓ сессия BORIS в браузере онлайн" : browserSession?.state === "reauth_required" ? "Нужно войти в Avito" : "сессия BORIS в браузере не подключена"}</strong>
          <span>{browserIdentityMismatch ? "BORIS не выполнит ни чтение, ни изменение объявлений через эту сессию. Откройте Avito именно этого клиента в его отдельном профиле Chrome — после проверки блокировка снимется автоматически." : browserReady ? "BORIS может выполнять фоновые браузерные действия только для этого аккаунта." : "API продолжит работать сам. Для недоступных через API операций подключите отдельную сессию один раз."}</span>
          {browserSession?.gateway?.actual_profile_name && browserIdentityMismatch ? <small>Сейчас открыт профиль: {browserSession.gateway.actual_profile_name}</small> : browserSession?.gateway?.device_name ? <small>{browserSession.gateway.device_name}</small> : null}
        </div>
        <div className={styles.statusBox} data-kind={isolatedReady ? "ok" : isolatedReason ? "error" : "neutral"}>
          <strong>{isolatedReady ? "✓ Серверная изолированная сессия готова" : isolatedReason ? "Серверная сессия временно недоступна" : "Серверная изолированная сессия в резерве"}</strong>
          <span>{isolatedRouteMessage}</span>
          {isolatedReason ? <small>{isolatedReasonLabel} · проверено маршрутов: {isolatedRoute?.tested_ports ?? 0} · доступно: {isolatedRoute?.open_ports ?? 0}</small> : null}
        </div>
        <div className={styles.tariffNote} data-kind="warning">
          <strong>Один клиент Avito = отдельный профиль Chrome</strong>
          <span>Не переключайте аккаунты Avito внутри одного профиля Chrome. BORIS жёстко привязывает сессию BORIS в браузере к выбранному клиенту и блокирует работу при несовпадении, чтобы объявления разных клиентов не смешивались.</span>
        </div>
        {executionHealth ? <div className={styles.statusBox} data-kind={executionHealth?.ready_for_background_work ? "ok" : "neutral"}>
          <strong>{executionHealth?.ready_for_background_work ? "✓ Фоновый контур готов" : "Фоновый контур ждёт подключения сессии BORIS в браузере"}</strong>
          <span>Активных задач: {executionHealth?.queue?.active ?? 0} · Завершено недавно: {executionHealth?.queue?.completed_recent ?? 0} · С ошибками: {executionHealth?.queue?.failed_recent ?? 0}</span>
          {recentReceipt ? <small>Последнее выполнение: {recentReceipt.executor === "avito_api" ? "Avito API" : "сессия BORIS в браузере"} · {recentReceipt.verified === true ? "проверено" : humanizeBusinessText(recentReceipt.status, "завершено")}</small> : null}
        </div> : null}
        {pairCode ? <div className={styles.statusBox} data-kind="ok"><strong>Код подключения: {pairCode}</strong><span>Введите его в модуль BORIS для браузера в профиле Chrome, где открыт именно этот Avito-аккаунт.</span></div> : null}
        <button className={styles.primary} type="button" onClick={createBrowserPairCode} disabled={busy || !accountId}>{browserSession?.online ? "Переподключить сессию BORIS в браузере" : "Подключить сессию BORIS в браузере"}</button>
      </section>

      <section className={styles.how}>
        <div><span>1</span><b>Вводите ключи</b><p>BORIS не показывает сохранённый секретный ключ обратно в интерфейсе.</p></div>
        <div><span>2</span><b>BORIS проверяет Авито</b><p>Проверяются токен, сам аккаунт и доступность автозагрузки.</p></div>
        <div><span>3</span><b>Получаете понятный статус</b><p>«Подключено» или конкретная причина ошибки и подсказка по тарифу.</p></div>
      </section>
      </main>
  );
}
