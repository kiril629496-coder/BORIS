"use client";

import { useEffect, useMemo, useState } from "react";
import { useAccounts } from "../../lib/AccountContext";


/*
 * BORIS_MOP_R13_ACCOUNT_BRIDGE
 *
 * Universal account resolution.
 * No client/account is hardcoded.
 */
function borisMopResolveAccountId(): string {
  if (typeof window === "undefined") {
    return "";
  }

  const url = new URL(window.location.href);

  const directCandidates = [
    url.searchParams.get("account_id"),
    url.searchParams.get("accountId"),
  ];

  const storageKeys = [
    "boris_account_id",
    "account_id",
    "accountId",
    "current_account_id",
    "currentAccountId",
    "selected_account_id",
    "selectedAccountId",
    "boris_current_account",
    "boris_current_account_id",
  ];

  for (const value of directCandidates) {
    if (value && value.trim()) {
      return value.trim();
    }
  }

  for (const key of storageKeys) {
    try {
      const value = localStorage.getItem(key);

      if (value && value.trim()) {
        return value.trim();
      }
    } catch {
      // localStorage may be unavailable during restricted browser states.
    }
  }

  /*
   * Some BORIS screens keep the selected account as JSON.
   * Read only known generic account-context keys.
   */
  const jsonKeys = [
    "boris_account",
    "current_account",
    "selected_account",
  ];

  for (const key of jsonKeys) {
    try {
      const raw = localStorage.getItem(key);

      if (!raw) continue;

      const parsed = JSON.parse(raw);

      const candidate =
        parsed?.account_id ??
        parsed?.accountId ??
        parsed?.id;

      if (
        candidate !== undefined &&
        candidate !== null &&
        String(candidate).trim()
      ) {
        return String(candidate).trim();
      }
    } catch {
      // Ignore malformed unrelated storage values.
    }
  }

  return "";
}


type Message = {
  id?: string;
  role: "client" | "manager" | "mop";
  text: string;
};

type BoundMopAccount = { account_id: string; name?: string; bound?: boolean };

type Training = {
  session_id?: string;
  account_id?: string;
  status?: string;
  mode?: string;
  scenario_public?: string;
  trust?: number;
  intent?: number;
  messages?: Message[];
  manager_turns?: number;
  outcome?: string | null;
  review?: {
    score?: number;
    result?: string;
    manager_level?: string;
    summary?: string;
    what_manager_understood?: string[];
    what_manager_missed?: string[];
    main_thinking_error?: string;
    better_thinking?: string;
    example_better_reply?: string;
    next_training_focus?: string;
    coach_message?: string;
  } | null;
  human_feedback?: { score?: number; comment?: string; fact_id?: number } | null;
};

function currentAccount(): string {
  if (typeof window === "undefined") return "";

  const keys = [
    "account_id",
    "accountId",
    "boris_account_id",
    "current_account_id",
  ];

  for (const key of keys) {
    const value = window.localStorage.getItem(key);
    if (value?.trim()) return value.trim();
  }

  const params = new URLSearchParams(window.location.search);
  return (
    params.get("account_id") ||
    params.get("accountId") ||
    ""
  );
}

// BORIS_R14_ACCOUNT_SELFHEAL
async function borisR14ResolveAccountId(): Promise<string> {
  if (typeof window === "undefined") return "";

  const params = new URLSearchParams(window.location.search);

  const direct =
    params.get("account_id") ||
    params.get("accountId") ||
    "";

  const storageKeys = [
    "boris_account_id",
    "account_id",
    "accountId",
    "current_account_id",
    "currentAccountId",
    "selected_account_id",
    "selectedAccountId",
    "boris_current_account_id",
  ];

  const storage =
    storageKeys
      .map((key) => {
        try {
          return window.localStorage.getItem(key) || "";
        } catch {
          return "";
        }
      })
      .find(Boolean) || "";

  const first = direct || storage;

  if (first) {
    for (const key of storageKeys) {
      try {
        window.localStorage.setItem(key, first);
      } catch {}
    }

    return first;
  }

  const token =
    window.localStorage.getItem("boris_token") || "";

  const headers: Record<string, string> = {};

  if (token) {
    headers.Authorization = "Bearer " + token;
  }

  try {
    const response = await fetch(
      "/api/inbox/accounts",
      {
        method: "GET",
        headers,
        credentials: "include",
      },
    );

    if (!response.ok) {
      return "";
    }

    const payload = await response.json();

    const accounts = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.accounts)
        ? payload.accounts
        : Array.isArray(payload?.items)
          ? payload.items
          : [];

    const selected = accounts.find(
      (account: any) =>
        account?.account_id != null ||
        account?.accountId != null ||
        account?.id != null,
    );

    if (!selected) {
      return "";
    }

    const resolved = String(
      selected.account_id ??
      selected.accountId ??
      selected.id ??
      "",
    ).trim();

    if (!resolved) {
      return "";
    }

    for (const key of storageKeys) {
      try {
        window.localStorage.setItem(key, resolved);
      } catch {}
    }

    try {
      const currentUrl = new URL(window.location.href);

      if (!currentUrl.searchParams.get("account_id")) {
        currentUrl.searchParams.set(
          "account_id",
          resolved,
        );

        window.history.replaceState(
          {},
          "",
          currentUrl.toString(),
        );
      }
    } catch {}

    return resolved;
  } catch {
    return "";
  }
}


function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};

  const token = window.localStorage.getItem("boris_token");

  if (!token) return {};

  return {
    Authorization: `Bearer ${token}`,
  };
}

export default function MopTrainingPage() {
  const { selectedAccountId } = useAccounts();
  const [accountId, setAccountId] = useState<string>("");
  const [training, setTraining] = useState<Training | null>(null);
  const [message, setMessage] = useState("");
  const [humanScore, setHumanScore] = useState(0);
  const [humanComment, setHumanComment] = useState("");
  const [feedbackSaved, setFeedbackSaved] = useState(false);
  const [loading, setLoading] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [error, setError] = useState("");
  const [started, setStarted] = useState(false);
  const [practiceScenario, setPracticeScenario] = useState("");
  const [boundAccounts, setBoundAccounts] = useState<BoundMopAccount[]>([]);
  const [mopReady, setMopReady] = useState<boolean | null>(null);
  const [mopPanel, setMopPanel] = useState<any>(null);


  useEffect(() => {
    if (selectedAccountId && selectedAccountId !== "all") {
      setAccountId(selectedAccountId);
      try {
        localStorage.setItem("boris_account_id", selectedAccountId);
        const u = new URL(window.location.href);
        u.searchParams.set("account_id", selectedAccountId);
        window.history.replaceState({}, "", u.toString());
      } catch {}
    }
  }, [selectedAccountId]);

  useEffect(() => {
    void borisR14ResolveAccountId().then((resolved) => {
      if (resolved) {
        setAccountId(resolved);
      }
    });

    const resolved = borisMopResolveAccountId();

    if (resolved) {
      setAccountId((current) => current || resolved);
    }
  }, []);


  useEffect(() => {
    /*
     * BORIS_MOP_R132_REAL_ACCOUNT_BOOTSTRAP
     *
     * Priority:
     *   1. URL account_id
     *   2. URL accountId
     *   3. selected account from /messages
     *   4. existing BORIS account storage
     *   5. /api/inbox/accounts fallback
     */

    let cancelled = false;

    async function resolveTrainingAccount() {
      const direct = currentAccount();

      if (direct) {
        if (!cancelled) {
          setAccountId(direct);
        }
        return;
      }

      /*
       * The unified inbox stores the account selected by the operator.
       */
      try {
        const stored =
          localStorage.getItem("boris_account_id");

        if (stored && stored.trim()) {
          if (!cancelled) {
            setAccountId(stored.trim());
          }
          return;
        }
      } catch {}

      /*
       * Last fallback:
       * discover accounts from the real inbox API.
       */
      try {
        const token =
          localStorage.getItem("boris_token");

        const response = await fetch(
          "/api/inbox/accounts",
          {
            headers: token
              ? {
                  Authorization:
                    "Bearer " + token,
                }
              : {},
          },
        );

        if (!response.ok) {
          throw new Error(
            "accounts HTTP " + response.status,
          );
        }

        const payload = await response.json();

        const rawAccounts =
          Array.isArray(payload)
            ? payload
            : Array.isArray(payload?.accounts)
              ? payload.accounts
              : Array.isArray(payload?.data)
                ? payload.data
                : Array.isArray(payload?.items)
                  ? payload.items
                  : [];

        const accounts = rawAccounts
          .map((account: any) => {
            const id =
              account?.account_id ??
              account?.accountId ??
              account?.id;

            if (
              id === undefined ||
              id === null ||
              String(id).trim() === ""
            ) {
              return null;
            }

            return String(id);
          })
          .filter(Boolean);

        /*
         * Safe automatic selection only when exactly
         * one account exists.
         *
         * If there are several and no selected account,
         * do NOT silently train the wrong client.
         */
        if (accounts.length === 1) {
          const selected = accounts[0];

          try {
            localStorage.setItem(
              "boris_account_id",
              selected,
            );
          } catch {}

          if (!cancelled) {
            setAccountId(selected);
          }

          return;
        }

        if (!cancelled) {
          setAccountId("");
        }
      } catch (error) {
        console.error(
          "BORIS MOP account bootstrap failed",
          error,
        );

        if (!cancelled) {
          setAccountId("");
        }
      }
    }

    resolveTrainingAccount();

    return () => {
      cancelled = true;
    };
  }, []);


  useEffect(() => {
    let cancelled = false;
    async function loadMopAvailability() {
      try {
        const token = localStorage.getItem("boris_token") || "";
        const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
        const res = await fetch("/api/ai/accounts?product=mop", { headers, cache: "no-store" });
        const data = await res.json();
        const active = Array.isArray(data?.accounts) ? data.accounts.filter((a: BoundMopAccount) => a.bound) : [];
        if (cancelled) return;
        setBoundAccounts(active);
        if (!accountId && active.length === 1) setAccountId(active[0].account_id);
        const selected = accountId || (active.length === 1 ? active[0].account_id : "");
        if (!selected) { setMopReady(null); setMopPanel(null); return; }
        const panelRes = await fetch(`/api/ai/account_panel?product=mop&account_id=${encodeURIComponent(selected)}`, { headers, cache: "no-store" });
        const panel = await panelRes.json();
        if (cancelled) return;
        const ready = panel?.mop?.training_available === true || Boolean(panel?.enabled && panel?.mop?.package_active && panel?.mop?.owner_enabled);
        setMopReady(ready); setMopPanel(panel?.mop || null);
      } catch {
        if (!cancelled) { setMopReady(null); setMopPanel(null); }
      }
    }
    void loadMopAvailability();
    return () => { cancelled = true; };
  }, [accountId]);

  const messages = useMemo(
    () => training?.messages || [],
    [training],
  );

  async function api(
    url: string,
    body: Record<string, unknown>,
  ) {
    // Один клик = один POST. Раньше UI до четырёх раз повторял 502/таймаут,
    // поэтому один сбой провайдера превращался в серию платных вызовов и
    // быстрее открывал circuit breaker. Повторы/recovery принадлежат backend.
    const controller = new AbortController();
    const timeoutMs = url.includes("/mop-combat/sparring/turn")
      ? 25000
      : url.includes("/mop-training/")
        ? 140000
        : 60000;
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          ...authHeaders(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      const raw = await response.text();
      let payload: any = null;
      try { payload = raw ? JSON.parse(raw) : null; } catch { payload = null; }
      if (response.ok) return payload;
      const detail = payload?.detail || payload?.message || raw || `HTTP ${response.status}`;
      throw new Error(String(detail));
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        throw new Error("Ответ МОП занял слишком много времени. Повторный запрос автоматически не отправлялся.");
      }
      throw e;
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function startTraining() {
    if (!accountId) {
      setError(
        "Не найден account_id. Откройте кабинет клиента и запустите тренировку оттуда.",
      );
      return;
    }

    setLoading(true);
    setError("");
    setTraining(null);
    setMessage("");

    try {
      const payload = await api(
        "/api/mop-training/start",
        {
          account_id: accountId,
        },
      );

      const nextTraining = payload?.training;

      if (!nextTraining?.session_id) {
        throw new Error(
          "MOP start вернул ответ без session_id",
        );
      }

      setTraining(nextTraining);
      setStarted(true);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Не удалось запустить тренировку",
      );
    } finally {
      setLoading(false);
    }
  }

  async function startMopSparring() {
    if (!accountId) { setError("Выберите аккаунт, где подключён МОП."); return; }
    if (mopReady === false) { setError("На этом аккаунте МОП не активен. Подключите МОП или включите его пакет/режим работы."); return; }
    const scenarioOpeners: Record<string, string> = {
      "Клиент спрашивает только цену": "Сколько стоит?",
      "Клиент отвечает односложно": "Да.",
      "Клиент говорит: дорого": "Дорого.",
      "Клиент сравнивает с конкурентом": "У конкурентов дешевле. Чем вы лучше?",
      "Клиент не хочет оставлять телефон": "Телефон оставлять не хочу. Можно здесь в чате?",
      "Клиент готов на замер": "Да, на замер готов. Что дальше?",
      "Клиент просит расчёт": "Можете сначала сделать расчёт?",
      "Клиент раздражён": "Можно просто ответить по делу без лишних вопросов?",
      "Клиент пропал после цены": "Здравствуйте. Я раньше писал по цене и решил вернуться к вопросу.",
      "Клиент возвращается через несколько дней": "Здравствуйте. Писал вам несколько дней назад, вопрос ещё актуален.",
    };
    setLoading(true); setError(""); setTraining(null); setMessage("");
    try {
      const payload = await api("/api/mop-combat/sparring/start", {
        account_id: accountId,
        business_goal: practiceScenario
          ? `Сценарий тренировки: ${practiceScenario}. МОП должен отвечать естественно как живой менеджер и вести к следующему шагу без давления.`
          : "Отработать реальный диалог и довести клиента до следующего шага",
      });
      if (!payload?.training?.session_id) throw new Error("Не удалось запустить спарринг МОП");
      let nextTraining = { ...payload.training, mode: "mop_combat_sparring", scenario_public: practiceScenario || payload.training.business_goal || "Вы играете клиента. МОП отвечает по текущим настройкам и памяти аккаунта." };
      const opener = practiceScenario ? scenarioOpeners[practiceScenario] : "";
      if (opener) {
        const firstTurn = await api("/api/mop-combat/sparring/turn", { account_id: accountId, session_id: payload.training.session_id, message: opener });
        if (firstTurn?.training) nextTraining = { ...firstTurn.training, mode: "mop_combat_sparring", scenario_public: nextTraining.scenario_public };
      }
      setTraining(nextTraining); setStarted(true); setMessage("");
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось запустить спарринг"); }
    finally { setLoading(false); }
  }

  async function sendMessage() {
    const text = message.trim();

    if (!text || !training?.session_id || !accountId) {
      return;
    }

    setLoading(true);
    setError("");

    try {
      const isCombat = training?.mode === "mop_combat_sparring";
      const payload = await api(
        isCombat ? "/api/mop-combat/sparring/turn" : (training?.mode === "mop_sparring" ? "/api/mop-training/sparring/reply" : "/api/mop-training/reply"),
        isCombat
          ? { account_id: accountId, session_id: training.session_id, message: text }
          : { account_id: accountId, session_id: training.session_id, text },
      );
      if (isCombat && payload?.training) {
        payload.training = { ...payload.training, mode: "mop_combat_sparring", scenario_public: training.scenario_public };
      }

      if (!payload?.training) {
        throw new Error(
          "MOP reply вернул ответ без training",
        );
      }

      setTraining(payload.training);
      setMessage("");
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "МОП не смог ответить",
      );
    } finally {
      setLoading(false);
    }
  }

  async function finishTraining() {
    if (!training?.session_id || !accountId) return;

    setFinishing(true);
    setError("");

    try {
      if (training.mode === "mop_combat_sparring") {
        const payload = await api(
          "/api/mop-combat/sparring/finish",
          {
            account_id: accountId,
            session_id: training.session_id,
          },
        );
        if (!payload?.training) {
          throw new Error("Не удалось завершить спарринг МОП");
        }
        setTraining({
          ...payload.training,
          mode: "mop_combat_sparring",
          scenario_public: training.scenario_public,
        });
        return;
      }
      const payload = await api(
        "/api/mop-training/finish",
        {
          account_id: accountId,
          session_id: training.session_id,
        },
      );

      if (!payload?.training) {
        throw new Error(
          "MOP finish вернул ответ без training",
        );
      }

      setTraining(payload.training);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Не удалось завершить тренировку",
      );
    } finally {
      setFinishing(false);
    }
  }

  async function saveHumanFeedback() {
    if (!training?.session_id || !accountId || humanScore < 1 || humanScore > 10 || humanComment.trim().length < 8) return;
    setLoading(true); setError("");
    try {
      if (training.mode === "mop_combat_sparring") {
        const lastMopTurn = [...(training.messages || [])]
          .reverse()
          .find((m) => m.role === "mop" && m.id);
        if (!lastMopTurn?.id) {
          throw new Error("Сначала получите хотя бы один ответ МОПа в спарринге.");
        }

        const feedbackPayload = await api(
          "/api/mop-combat/sparring/feedback",
          {
            account_id: accountId,
            session_id: training.session_id,
            turn_id: lastMopTurn.id,
            score: humanScore,
            comment: humanComment.trim(),
            flags: [],
          },
        );

        const learningPayload = await api(
          "/api/mop-combat/sparring/learning",
          {
            account_id: accountId,
            session_id: training.session_id,
            turn_id: lastMopTurn.id,
            action: "remember",
            rule_text: "",
          },
        );

        const nextTraining =
          learningPayload?.training ||
          feedbackPayload?.training ||
          training;
        const factId = learningPayload?.feedback?.fact_id;
        const policyBlocked = Boolean(learningPayload?.policy_blocked || learningPayload?.feedback?.policy_blocked);

        setTraining({
          ...nextTraining,
          mode: "mop_combat_sparring",
          scenario_public: training.scenario_public,
          human_feedback: {
            score: humanScore,
            comment: humanComment.trim(),
            fact_id: factId,
          },
        });
        if (policyBlocked) {
          setFeedbackSaved(false);
          setError(
            learningPayload?.policy_message ||
            learningPayload?.feedback?.policy_message ||
            "Правило сохранено в истории тренировки, но не применено: оно конфликтует с обязательными ограничениями МОПа."
          );
          return;
        }
        setFeedbackSaved(true);
        return;
      }

      const payload = await api(
        "/api/mop-training/feedback",
        {
          account_id: accountId,
          session_id: training.session_id,
          score: humanScore,
          comment: humanComment.trim(),
        },
      );
      if (!payload?.training) throw new Error("Не удалось сохранить обратную связь");
      setTraining(payload.training); setFeedbackSaved(true);
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось сохранить обратную связь"); }
    finally { setLoading(false); }
  }

  async function updateLearnedRule() {
    const factId = training?.human_feedback?.fact_id;
    if (!factId || !accountId || humanComment.trim().length < 3) return;
    setLoading(true); setError("");
    try {
      await api("/api/mop-training/rule/update", { account_id: accountId, fact_id: factId, value: humanComment.trim() });
      setFeedbackSaved(true);
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось исправить правило"); }
    finally { setLoading(false); }
  }

  async function deleteLearnedRule() {
    const factId = training?.human_feedback?.fact_id;
    if (!factId || !accountId || !window.confirm("Удалить это правило из обучения МОП?")) return;
    setLoading(true); setError("");
    try {
      await api("/api/mop-training/rule/delete", { account_id: accountId, fact_id: factId });
      setTraining((current) => current ? ({...current, human_feedback: null}) : current);
      setFeedbackSaved(false);
      setHumanComment("");
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось удалить правило"); }
    finally { setLoading(false); }
  }

  function resetTraining() {
    setTraining(null);
    setMessage("");
    setError("");
    setStarted(false);
    setHumanScore(0); setHumanComment(""); setFeedbackSaved(false);
  }

  const review = training?.review;

  return (
    <main
      data-mop-training-contract="v2-single-post-timeout"
      className="boris-mop-training-reference"
      style={{
        minHeight: "100vh",
        background: "#f6f8fc",
        padding: "clamp(14px,2vw,28px)",
        color: "#172033",
      }}
    >
      <div className="boris-mop-training-shell mop-training-shell"
        style={{
          maxWidth: 1440,
          margin: "0 auto",
        }}
      >
        <div
          className="mop-training-main-card boris-card"
          style={{
            background: "#ffffff",
            borderRadius: 24,
            padding: "clamp(18px,2.2vw,30px)",
            boxShadow: "0 8px 28px rgba(20,35,70,.06)",
            border: "1px solid #e8edf5",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: 20,
              alignItems: "flex-start",
              marginBottom: 24,
            }}
          >
            <div>
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 700,
                  color: "#4776e6",
                  letterSpacing: ".08em",
                  textTransform: "uppercase",
                  marginBottom: 8,
                }}
              >
                ТРЕНИРОВКА МОП
              </div>

              <h1
                style={{
                  margin: 0,
                  fontSize: 32,
                  lineHeight: 1.15,
                }}
              >
                Живая тренировка МОП
              </h1>

              <p
                style={{
                  margin: "10px 0 0",
                  color: "#667085",
                  fontSize: 15,
                }}
              >
                Реальный клиент отвечает на каждое
                сообщение менеджера.
              </p>
            </div>

            <div
              style={{
                padding: "10px 14px",
                borderRadius: 12,
                background: started
                  ? "#ecfdf3"
                  : "#f2f4f7",
                color: started
                  ? "#027a48"
                  : "#667085",
                fontWeight: 700,
                fontSize: 13,
              }}
            >
              {started
                ? "● ТРЕНИРОВКА АКТИВНА"
                : "○ ГОТОВО К ЗАПУСКУ"}
            </div>
          </div>

          {!training && (
            <section
              className="mop-training-launch-card"
              style={{
                padding: 30,
                borderRadius: 18,
                background: "#ffffff",
                border: "1px solid #e1e8f3",
              }}
            >
              <div
                style={{
                  fontSize: 17,
                  fontWeight: 700,
                  marginBottom: 8,
                }}
              >
                Готовы проверить менеджера?
              </div>

              <div
                style={{
                  color: "#667085",
                  lineHeight: 1.6,
                  marginBottom: 20,
                }}
              >
                BORIS создаст реалистичного покупателя
                с собственной мотивацией, бюджетом,
                сомнениями и возражениями.
              </div>

              <div className="mop-training-kpi-grid" style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(240px,1fr))",gap:12,marginBottom:18}}>
                <div className="mop-training-kpi-card mop-training-kpi-blue" style={{padding:14,borderRadius:14,background:"#fff",border:"1px solid #dce7ff"}}>
                  <div style={{fontSize:11,fontWeight:800,color:"#667085",textTransform:"uppercase",letterSpacing:".05em"}}>Активный МОП</div>
                  <select value={accountId} onChange={(e)=>{ const v=e.target.value; setAccountId(v); try{localStorage.setItem("boris_account_id",v); const u=new URL(window.location.href); u.searchParams.set("account_id",v); window.history.replaceState({},"",u.toString());}catch{} }} style={{width:"100%",marginTop:8,minHeight:42,border:"1px solid #cfd8e8",borderRadius:10,padding:"0 10px",background:"#fff",fontWeight:700}}>
                    <option value="">Выберите аккаунт с МОП</option>
                    {boundAccounts.map(a=><option key={a.account_id} value={a.account_id}>{a.name || a.account_id}</option>)}
                  </select>
                  <div style={{fontSize:12,color:mopReady===false?"#b42318":"#027a48",marginTop:8}}>{mopReady===false?"МОП на выбранном аккаунте не активен":mopReady===true?`МОП активен · ${mopPanel?.state || "работает"}`:`Подключённых МОП: ${boundAccounts.length}`}</div>
                </div>
                <div className="mop-training-kpi-card mop-training-kpi-violet" style={{padding:14,borderRadius:14,background:"linear-gradient(135deg,#ffffff,#f8fbff)",border:"1px solid #dce7ff"}}>
                  <div style={{fontSize:11,fontWeight:800,color:"#667085",textTransform:"uppercase",letterSpacing:".05em"}}>Обучение аккаунта</div>
                  <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8,marginTop:8}}>
                    <div><b style={{fontSize:20}}>{mopPanel?.confirmed_rules ?? "—"}</b><div style={{fontSize:11,color:"#667085"}}>подтверждённых правил</div></div>
                    <div><b style={{fontSize:20}}>{mopPanel?.avg_sparring_score ?? "—"}</b><div style={{fontSize:11,color:"#667085"}}>средняя оценка</div></div>
                  </div>
                </div>
              </div>

              <div className="mop-training-scenarios" style={{marginBottom:14}}><div style={{fontSize:12,fontWeight:800,marginBottom:7}}>Практические сценарии</div><div style={{display:"flex",gap:6,flexWrap:"wrap"}}>{[
                "Клиент спрашивает только цену","Клиент отвечает односложно","Клиент говорит: дорого","Клиент сравнивает с конкурентом","Клиент не хочет оставлять телефон","Клиент готов на замер","Клиент просит расчёт","Клиент раздражён","Клиент пропал после цены","Клиент возвращается через несколько дней"
              ].map(x=><button key={x} onClick={()=>setPracticeScenario(x)} style={{border:"1px solid #d0d5dd",background:practiceScenario===x?"#dbeafe":"#fff",borderRadius:9,padding:"7px 9px",fontSize:11,cursor:"pointer"}}>{x}</button>)}</div>{practiceScenario?<div style={{fontSize:12,color:"#475467",marginTop:7}}>Выбрано: {practiceScenario}. После запуска BORIS сам подаст живую первую реплику клиента; дальше продолжайте диалог и сохраните конкретное правило по результату.</div>:null}</div>

              <button
                onClick={startMopSparring}
                disabled={loading}
                style={{
                  border: 0,
                  borderRadius: 12,
                  padding: "13px 20px",
                  background: "#315bea",
                  color: "#fff",
                  fontWeight: 800,
                  cursor: loading ? "default" : "pointer",
                  opacity: loading ? .65 : 1,
                  minWidth: 240,
                }}
              >
                {loading ? "Запускаю спарринг..." : "Начать спарринг с МОП"}
              </button>
              <div style={{ marginTop: 10, fontSize: 12, color: "#667085" }}>В этом режиме вы пишете как клиент, а МОП отвечает по текущей цели, инструкциям, памяти и накопленному обучению. Сообщения в Avito не отправляются.</div>
            </section>
          )}

          {training && (
            <>
              <div
                className="mop-training-active-grid"
                style={{
                  display: "grid",
                  gridTemplateColumns:
                    "minmax(0,1fr) 250px",
                  gap: 20,
                }}
              >
                <section
                  style={{
                    background: "#fbfcfe",
                    border: "1px solid #e7ebf2",
                    borderRadius: 18,
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      padding: "18px 20px",
                      borderBottom:
                        "1px solid #e7ebf2",
                    }}
                  >
                    <div
                      style={{
                        fontSize: 12,
                        color: "#667085",
                        marginBottom: 5,
                      }}
                    >
                      СИТУАЦИЯ
                    </div>

                    <div
                      style={{
                        fontWeight: 600,
                      }}
                    >
                      {training.scenario_public ||
                        "Учебный клиент"}
                    </div>
                  </div>

                  <div
                    style={{
                      padding: 20,
                      minHeight: 360,
                      maxHeight: 560,
                      overflowY: "auto",
                      display: "flex",
                      flexDirection: "column",
                      gap: 12,
                    }}
                  >
                    {messages.length === 0 ? (
                      <div style={{minHeight:320,display:"grid",placeItems:"center",textAlign:"center",padding:"24px"}}>
                        <div style={{maxWidth:520}}>
                          <div style={{width:58,height:58,borderRadius:18,display:"grid",placeItems:"center",margin:"0 auto 14px",background:"linear-gradient(135deg,#eaf0ff,#f7f9ff)",fontSize:28}}>🥊</div>
                          <div style={{fontSize:20,fontWeight:800,color:"#172033"}}>Спарринг готов</div>
                          <div style={{fontSize:13,color:"#667085",lineHeight:1.55,marginTop:7}}>Вы — клиент. Напишите первую реплику, а активный МОП этого аккаунта ответит по реальным настройкам, памяти и обучению.</div>
                          <div style={{display:"flex",gap:7,justifyContent:"center",flexWrap:"wrap",marginTop:14}}>{["Добрый день, сколько стоит?","Можно сделать замер?","Почему так дорого?","Сравниваю с другой компанией"].map(x=><button key={x} onClick={()=>setMessage(x)} style={{border:"1px solid #d7e0ef",background:"#fff",borderRadius:10,padding:"8px 10px",fontSize:12,cursor:"pointer"}}>{x}</button>)}</div>
                        </div>
                      </div>
                    ) : messages.map(
                      (item, index) => {
                        const client =
                          item.role === "client";

                        return (
                          <div
                            key={`${index}-${item.text}`}
                            style={{
                              display: "flex",
                              justifyContent:
                                client
                                  ? "flex-start"
                                  : "flex-end",
                            }}
                          >
                            <div
                              style={{
                                maxWidth: "78%",
                                padding:
                                  "12px 15px",
                                borderRadius: 15,
                                background:
                                  client
                                    ? "#ffffff"
                                    : "#315bea",
                                color: client
                                  ? "#172033"
                                  : "#ffffff",
                                border: client
                                  ? "1px solid #e4e8ef"
                                  : "none",
                                lineHeight: 1.5,
                                whiteSpace:
                                  "pre-wrap",
                              }}
                            >
                              <div
                                style={{
                                  fontSize: 11,
                                  fontWeight: 700,
                                  opacity: .65,
                                  marginBottom: 4,
                                }}
                              >
                                {client
                                  ? "ВЫ · КЛИЕНТ"
                                  : "МОП"}
                              </div>

                              {item.text}
                            </div>
                          </div>
                        );
                      },
                    )}
                  </div>

                  {training.status ===
                    "active" && (
                    <div
                      style={{
                        padding: 16,
                        borderTop:
                          "1px solid #e7ebf2",
                        background: "#fff",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          gap: 10,
                        }}
                      >
                        <textarea
                          value={message}
                          onChange={(e) =>
                            setMessage(
                              e.target.value,
                            )
                          }
                          onKeyDown={(e) => {
                            if (
                              e.key === "Enter" &&
                              !e.shiftKey
                            ) {
                              e.preventDefault();
                              sendMessage();
                            }
                          }}
                          placeholder="Напишите сообщение от лица клиента..."
                          disabled={loading}
                          rows={3}
                          style={{
                            flex: 1,
                            resize: "vertical",
                            border:
                              "1px solid #d8dee9",
                            borderRadius: 12,
                            padding: 12,
                            font:
                              "inherit",
                            outline: "none",
                          }}
                        />

                        <button
                          onClick={sendMessage}
                          disabled={
                            loading ||
                            !message.trim()
                          }
                          style={{
                            alignSelf:
                              "flex-end",
                            border: 0,
                            borderRadius: 12,
                            padding:
                              "12px 18px",
                            background:
                              "#315bea",
                            color: "#fff",
                            fontWeight: 700,
                            cursor:
                              loading
                                ? "default"
                                : "pointer",
                            opacity:
                              loading ||
                              !message.trim()
                                ? .55
                                : 1,
                          }}
                        >
                          {loading
                            ? "..."
                            : "Ответить"}
                        </button>
                      </div>

                      <div
                        style={{
                          marginTop: 8,
                          fontSize: 11,
                          color: "#98a2b3",
                        }}
                      >
                        Enter — отправить ·
                        Shift+Enter — новая строка
                      </div>
                    </div>
                  )}
                </section>

                <aside
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 12,
                  }}
                >
                  <div
                    style={{
                      background: "#fff",
                      border:
                        "1px solid #e7ebf2",
                      borderRadius: 16,
                      padding: 18,
                    }}
                  >
                    <div
                      style={{
                        fontSize: 12,
                        color: "#667085",
                        marginBottom: 8,
                      }}
                    >
                      {training.mode === "mop_combat_sparring" ? "ПАМЯТЬ МОП" : "ДОВЕРИЕ КЛИЕНТА"}
                    </div>

                    <div
                      style={{
                        fontSize: 32,
                        fontWeight: 800,
                      }}
                    >
                      {training.mode === "mop_combat_sparring" ? (mopPanel?.confirmed_rules ?? 0) : `${training.trust ?? 0}%`}
                    </div>
                  </div>

                  <div
                    style={{
                      background: "#fff",
                      border:
                        "1px solid #e7ebf2",
                      borderRadius: 16,
                      padding: 18,
                    }}
                  >
                    <div
                      style={{
                        fontSize: 12,
                        color: "#667085",
                        marginBottom: 8,
                      }}
                    >
                      {training.mode === "mop_combat_sparring" ? "СТАТУС МОП" : "НАМЕРЕНИЕ"}
                    </div>

                    <div
                      style={{
                        fontSize: 32,
                        fontWeight: 800,
                      }}
                    >
                      {training.mode === "mop_combat_sparring" ? (mopPanel?.state || "активен") : `${training.intent ?? 0}%`}
                    </div>
                  </div>

                  <div
                    style={{
                      background: "#fff",
                      border:
                        "1px solid #e7ebf2",
                      borderRadius: 16,
                      padding: 18,
                    }}
                  >
                    <div
                      style={{
                        fontSize: 12,
                        color: "#667085",
                        marginBottom: 8,
                      }}
                    >
                      ХОД ДИАЛОГА
                    </div>

                    <div
                      style={{
                        fontSize: 22,
                        fontWeight: 800,
                      }}
                    >
                      {training.manager_turns ||
                        0}
                    </div>

                    <div
                      style={{
                        color: "#667085",
                        fontSize: 12,
                      }}
                    >
                      ответов менеджера
                    </div>
                  </div>

                  {training.status ===
                    "active" && (
                    <button
                      onClick={finishTraining}
                      disabled={finishing}
                      style={{
                        border: 0,
                        borderRadius: 12,
                        padding: 13,
                        background: "#172033",
                        color: "#fff",
                        fontWeight: 700,
                        cursor:
                          finishing
                            ? "default"
                            : "pointer",
                        opacity: finishing
                          ? .6
                          : 1,
                      }}
                    >
                      {finishing
                        ? "РОП разбирает..."
                        : "Завершить и получить разбор"}
                    </button>
                  )}

                  <button
                    onClick={resetTraining}
                    style={{
                      border:
                        "1px solid #d8dee9",
                      borderRadius: 12,
                      padding: 12,
                      background: "#fff",
                      color: "#475467",
                      fontWeight: 600,
                    }}
                  >
                    Новая тренировка
                  </button>
                </aside>
              </div>

              {review && (
                <section
                  style={{
                    marginTop: 20,
                    padding: 24,
                    borderRadius: 18,
                    background: "#fff",
                    border:
                      "1px solid #dfe5ef",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent:
                        "space-between",
                      gap: 20,
                      alignItems:
                        "flex-start",
                      marginBottom: 20,
                    }}
                  >
                    <div>
                      <div
                        style={{
                          fontSize: 12,
                          color: "#667085",
                          marginBottom: 5,
                        }}
                      >
                        РАЗБОР РОП
                      </div>

                      <h2
                        style={{
                          margin: 0,
                          fontSize: 24,
                        }}
                      >
                        Результат тренировки
                      </h2>
                    </div>

                    <div
                      style={{
                        fontSize: 34,
                        fontWeight: 800,
                      }}
                    >
                      {review.score ?? 0}
                      <span
                        style={{
                          fontSize: 14,
                          color: "#667085",
                        }}
                      >
                        /100
                      </span>
                    </div>
                  </div>

                  {review.summary && (
                    <div
                      style={{
                        padding: 16,
                        borderRadius: 12,
                        background: "#f7f9fc",
                        marginBottom: 16,
                        lineHeight: 1.55,
                      }}
                    >
                      {review.summary}
                    </div>
                  )}

                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns:
                        "1fr 1fr",
                      gap: 16,
                    }}
                  >
                    <div>
                      <div
                        style={{
                          fontWeight: 700,
                          marginBottom: 8,
                        }}
                      >
                        Что менеджер понял
                      </div>

                      {(review.what_manager_understood ||
                        []).map(
                        (x, i) => (
                          <div
                            key={i}
                            style={{
                              marginBottom: 6,
                              color:
                                "#475467",
                            }}
                          >
                            • {x}
                          </div>
                        ),
                      )}
                    </div>

                    <div>
                      <div
                        style={{
                          fontWeight: 700,
                          marginBottom: 8,
                        }}
                      >
                        Что менеджер упустил
                      </div>

                      {(review.what_manager_missed ||
                        []).map(
                        (x, i) => (
                          <div
                            key={i}
                            style={{
                              marginBottom: 6,
                              color:
                                "#475467",
                            }}
                          >
                            • {x}
                          </div>
                        ),
                      )}
                    </div>
                  </div>

                  {review.main_thinking_error && (
                    <div
                      style={{
                        marginTop: 18,
                        padding: 16,
                        borderRadius: 12,
                        background: "#fff7ed",
                        border:
                          "1px solid #fed7aa",
                      }}
                    >
                      <b>
                        Главная ошибка мышления:
                      </b>
                      <div
                        style={{
                          marginTop: 5,
                        }}
                      >
                        {
                          review.main_thinking_error
                        }
                      </div>
                    </div>
                  )}

                  {review.better_thinking && (
                    <div
                      style={{
                        marginTop: 12,
                        padding: 16,
                        borderRadius: 12,
                        background: "#ecfdf3",
                        border:
                          "1px solid #bbf7d0",
                      }}
                    >
                      <b>
                        Как надо было мыслить:
                      </b>
                      <div
                        style={{
                          marginTop: 5,
                        }}
                      >
                        {review.better_thinking}
                      </div>
                    </div>
                  )}

                  {review.example_better_reply && (
                    <div
                      style={{
                        marginTop: 12,
                        padding: 16,
                        borderRadius: 12,
                        background: "#eff6ff",
                        border:
                          "1px solid #bfdbfe",
                      }}
                    >
                      <b>
                        Сильный вариант ответа:
                      </b>
                      <div
                        style={{
                          marginTop: 5,
                          whiteSpace:
                            "pre-wrap",
                        }}
                      >
                        {
                          review.example_better_reply
                        }
                      </div>
                    </div>
                  )}

                  {review.next_training_focus && (
                    <div
                      style={{
                        marginTop: 12,
                        padding: 16,
                        borderRadius: 12,
                        background: "#f8f9fc",
                      }}
                    >
                      <b>
                        Следующий фокус:
                      </b>
                      <div
                        style={{
                          marginTop: 5,
                        }}
                      >
                        {
                          review.next_training_focus
                        }
                      </div>
                    </div>
                  )}

                  <div style={{ marginTop: 16, padding: 16, borderRadius: 12, background: "#f8fafc", border: "1px solid #e2e8f0" }}>
                    <b>Ваша оценка МОП</b>
                    <div style={{ fontSize: 12, color: "#667085", marginTop: 4 }}>Оцените результат и напишите, что МОП должен учитывать в следующих тренировках и реальных диалогах.</div>
                    <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 9 }}>{[1,2,3,4,5,6,7,8,9,10].map(n => <button key={n} onClick={()=>{setHumanScore(n);setFeedbackSaved(false)}} style={{border:"1px solid #d0d5dd",borderRadius:8,padding:"6px 8px",background:humanScore===n?"#dbeafe":"#fff",cursor:"pointer"}}>{n}</button>)}</div>
                    <textarea value={humanComment} onChange={e=>{setHumanComment(e.target.value);setFeedbackSaved(false)}} rows={4} placeholder="Подробный комментарий: что было хорошо, что изменить, какое правило запомнить" style={{width:"100%",boxSizing:"border-box",marginTop:9,border:"1px solid #d0d5dd",borderRadius:10,padding:10}} />
                    <button disabled={loading || humanScore<1 || humanComment.trim().length<8} onClick={saveHumanFeedback} style={{marginTop:8,border:"1px solid #bfdbfe",background:"#eff6ff",borderRadius:9,padding:"8px 11px",fontWeight:700,cursor:"pointer"}}>Сохранить обучение</button>
                    {feedbackSaved || training?.human_feedback ? <div style={{fontSize:12,color:"#027a48",marginTop:7}}>Сохранено. МОП запомнил правило: «{training?.human_feedback?.comment || humanComment}». Оно войдёт в следующий контекст МОП.</div> : null}
                    {training?.human_feedback?.fact_id ? <div style={{display:"flex",gap:7,marginTop:8,flexWrap:"wrap"}}><button disabled={loading || humanComment.trim().length<3} onClick={updateLearnedRule} style={{border:"1px solid #d0d5dd",background:"#fff",borderRadius:9,padding:"7px 10px",cursor:"pointer"}}>Исправить правило</button><button disabled={loading} onClick={deleteLearnedRule} style={{border:"1px solid #fecaca",background:"#fff7f7",color:"#b42318",borderRadius:9,padding:"7px 10px",cursor:"pointer"}}>Удалить правило</button></div> : null}
                  </div>

                  {review.coach_message && (
                    <div
                      style={{
                        marginTop: 12,
                        fontSize: 15,
                        lineHeight: 1.6,
                        color: "#344054",
                      }}
                    >
                      {review.coach_message}
                    </div>
                  )}
                </section>
              )}
            </>
          )}

          {error && (
            <div
              style={{
                marginTop: 18,
                padding: 14,
                borderRadius: 12,
                background: "#fef3f2",
                border: "1px solid #fecdca",
                color: "#b42318",
                whiteSpace: "pre-wrap",
              }}
            >
              <b>Ошибка:</b> {error}
            </div>
          )}
        </div>
      </div>
    <style jsx global>{`
.boris-mop-training-reference{background:linear-gradient(180deg,#f8fbff 0%,#f4f7fb 100%)!important;padding:clamp(20px,2.2vw,34px)!important}
.boris-mop-training-shell{max-width:none!important;margin:0!important;width:100%!important}
.mop-training-main-card{background:transparent!important;border:0!important;box-shadow:none!important;border-radius:0!important;padding:0!important}
.mop-training-main-card>div:first-child{min-height:142px!important;margin-bottom:18px!important;padding:28px 30px!important;border:1px solid #e2e9f4!important;border-radius:24px!important;background:linear-gradient(135deg,#fff 0%,#f7faff 62%,#eef4ff 100%)!important;box-shadow:0 12px 34px rgba(31,59,120,.07)!important;position:relative!important;overflow:hidden!important}
.mop-training-main-card>div:first-child:after{content:"";position:absolute;right:-45px;top:-78px;width:250px;height:250px;border-radius:50%;background:linear-gradient(135deg,rgba(49,91,234,.13),rgba(124,92,255,.05));pointer-events:none}
.mop-training-main-card>div:first-child h1{font-size:clamp(30px,3vw,44px)!important;letter-spacing:-.035em!important;color:#14213d!important}
.mop-training-main-card>div:first-child p{font-size:15px!important;max-width:720px!important;color:#71809a!important}
.mop-training-main-card>div:first-child>div:last-child{border:1px solid #dde6f5!important;background:rgba(255,255,255,.82)!important;color:#53627a!important;border-radius:999px!important;padding:10px 15px!important;box-shadow:0 5px 15px rgba(35,55,95,.04)!important;z-index:1!important}
.mop-training-launch-card{padding:26px!important;border-radius:24px!important;border:1px solid #e3eaf4!important;background:#fff!important;box-shadow:0 10px 30px rgba(25,45,90,.055)!important}
.mop-training-launch-card>div:first-child{font-size:21px!important;color:#14213d!important}
.mop-training-launch-card>div:nth-child(2){font-size:14px!important;max-width:850px!important;margin-bottom:22px!important}
.mop-training-kpi-grid{grid-template-columns:minmax(320px,1.3fr) minmax(280px,.7fr)!important;gap:16px!important;margin-bottom:18px!important}
.mop-training-kpi-card{min-height:118px!important;padding:18px!important;border-radius:18px!important;box-shadow:none!important;border:1px solid #e2e9f4!important}
.mop-training-kpi-blue{background:linear-gradient(135deg,#f8fbff,#eef4ff)!important}.mop-training-kpi-violet{background:linear-gradient(135deg,#fbf9ff,#f4f1ff)!important}
.mop-training-kpi-card select{min-height:46px!important;border:1px solid #d5deeb!important;border-radius:12px!important;padding:0 13px!important;font-size:14px!important}
.mop-training-scenarios{padding:17px!important;border:1px solid #e7edf5!important;border-radius:18px!important;background:#fbfcfe!important;margin-bottom:18px!important}
.mop-training-scenarios button{border:1px solid #dfe6f1!important;background:#fff!important;border-radius:999px!important;padding:9px 12px!important;font-size:12px!important;color:#53627a!important;transition:.18s ease!important}
.mop-training-scenarios button:hover{transform:translateY(-1px);border-color:#a9bdf6!important;box-shadow:0 5px 14px rgba(49,91,234,.08)!important}
.mop-training-launch-card>button{min-width:280px!important;padding:14px 20px!important;border-radius:13px!important;background:linear-gradient(135deg,#315bea,#5b74f2)!important;box-shadow:0 10px 22px rgba(49,91,234,.22)!important;font-size:14px!important}
.mop-training-active-grid{grid-template-columns:minmax(0,1fr) minmax(280px,330px)!important;gap:18px!important}
.mop-training-active-grid>section,.mop-training-active-grid>aside{border:1px solid #e3eaf4!important;border-radius:22px!important;background:#fff!important;box-shadow:0 8px 28px rgba(25,45,90,.05)!important}
.mop-training-active-grid textarea,.mop-training-active-grid input,.mop-training-active-grid select{border:1px solid #d8e1ed!important;border-radius:12px!important;background:#fff!important}
@media(max-width:1050px){.mop-training-kpi-grid,.mop-training-active-grid{grid-template-columns:1fr!important}}
@media(max-width:720px){.boris-mop-training-reference{padding:14px!important}.mop-training-main-card>div:first-child{padding:20px!important;min-height:auto!important;flex-direction:column!important}.mop-training-launch-card{padding:18px!important}.mop-training-kpi-grid{grid-template-columns:1fr!important}.mop-training-launch-card>button{width:100%!important;min-width:0!important}}
`}</style>
</main>
  );
}
