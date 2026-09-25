"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, getAccount, openRoute, openTab } from "../lib/api";

/* Единая навигация по шагам бизнес-сценария.
   Источник истины — backend (business_scenarios.steps_state).
   В URL лежат только указатели: ?scenario=<id>&step=<key>.
   Никаких условий на конкретный шаг или вкладку внутри компонента. */

type Step = {
  key: string;
  title: string;
  status: string;
  auto?: boolean;
  manual?: string | null;
  tab?: string | null;
  route?: string | null;
  index?: number;
  prev_key?: string | null;
  next_key?: string | null;
};

const PASSED = ["completed", "skipped"];

const wrap: any = {
  position: "sticky", bottom: 0, zIndex: 40,
  background: "#FFFFFF", border: "1.5px solid var(--boris-border)", borderRadius: "12px",
  padding: "12px 14px", margin: "16px 110px 8px 0",
  boxShadow: "0 -2px 12px rgba(16,24,40,0.06)",
};
const head: any = { fontSize: "13px", color: "var(--boris-text-2)", marginBottom: "2px" };
const name: any = { fontSize: "16px", fontWeight: 700, color: "var(--boris-text-2)", marginBottom: "10px" };
const rowS: any = { display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" };
const btnMain: any = {
  background: "var(--boris-blue)", color: "#FFFFFF", border: "none", borderRadius: "10px",
  padding: "11px 20px", fontSize: "15px", fontWeight: 700, cursor: "pointer",
};
const btnMainOff: any = { ...btnMain, background: "var(--boris-border)", color: "var(--boris-text-3)", cursor: "not-allowed" };
const btnGhost: any = {
  background: "#FFFFFF", color: "var(--boris-text-2)", border: "1.5px solid var(--boris-border)",
  borderRadius: "10px", padding: "11px 16px", fontSize: "15px", cursor: "pointer",
};
const exitS: any = {
  background: "none", border: "none", color: "var(--boris-text-2)", fontSize: "14px",
  cursor: "pointer", marginLeft: "auto", textDecoration: "underline",
};
const okS: any = { fontSize: "14px", color: "#12B76A", fontWeight: 600, marginBottom: "8px" };
const hintS: any = { fontSize: "13px", color: "var(--boris-text-2)", marginTop: "8px" };
const errS: any = { fontSize: "14px", color: "#F04438", marginTop: "8px" };

function dropParams() {
  if (typeof window === "undefined") return;
  const u = new URL(window.location.href);
  u.searchParams.delete("scenario");
  u.searchParams.delete("step");
  window.location.replace(u.toString());
}

export function ScenarioNavigation(props: {
  scenarioId: string | number;
  currentStepId?: string | null;
  onExit?: () => void;
}) {
  const { scenarioId, currentStepId, onExit } = props;
  const [scen, setScen] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  const account = typeof window !== "undefined" ? getAccount() : "";

  const load = useCallback(async () => {
    const r = await apiGet(
      "/api/home/scenario/" + scenarioId + "?account_id=" + encodeURIComponent(account || "")
    );
    if (r && r.scenario && r.scenario.progress) setScen(r.scenario);
    else setScen(null);
  }, [scenarioId, account]);

  useEffect(() => { load(); }, [load]);
  // Общий канал обновления: любой экран, сохранивший данные, шлёт
  // window.dispatchEvent(new Event("boris:data-changed")) — панель
  // перечитывает состояние сценария с бэкенда. Без веток на конкретный шаг.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const h = () => { load(); };
    window.addEventListener("boris:data-changed", h);
    return () => window.removeEventListener("boris:data-changed", h);
  }, [load]);

  // сценарный режим включается только на подтверждённом сервером активном прогоне
  if (!scen || !scen.progress || scen.status !== "active") return null;

  const steps: Step[] = scen.progress.steps || [];
  const total = scen.progress.total || steps.length;
  const cur =
    steps.filter((s) => s.key === currentStepId)[0] ||
    steps.filter((s) => s.key === scen.progress.current)[0] ||
    steps[0];
  if (!cur) return null;

  const prev = cur.prev_key ? steps.filter((s) => s.key === cur.prev_key)[0] : null;
  const next = cur.next_key ? steps.filter((s) => s.key === cur.next_key)[0] : null;
  const passed = PASSED.indexOf(cur.status) >= 0;
  const canMarkDone = !cur.auto;
  const isLast = !cur.next_key;
  const allPassed = steps.length > 0 && steps.every((s) => PASSED.indexOf(s.status) >= 0);
  const working = busy !== "";

  const goStep = (st?: Step | null) => {
    if (!st) return;
    const ctx = { scenario: scenarioId, step: st.key };
    if (st.route) openRoute(st.route, ctx);
    else if (st.tab) openTab(st.tab, ctx);
    else openRoute("/dashboard/scenarios/" + scenarioId);
  };

  const markDone = async () => {
    const r = await apiPost("/api/home/scenario/step", {
      account_id: account,
      scenario_id: Number(scenarioId),
      step_key: cur.key,
      action: "done",
    });
    if (r && r.status === "ok") {
      if (r.scenario) setScen(r.scenario);
      return true;
    }
    setErr((r && (r.message || r.detail)) || "Не удалось отметить шаг выполненным");
    return false;
  };

  const onBack = () => { if (!working) goStep(prev); };

  const onContinue = async () => {
    if (working) return;
    setBusy("next"); setErr("");
    let ok = true;
    if (!passed && canMarkDone) ok = await markDone();
    setBusy("");
    if (ok) goStep(next);
  };

  const onFinish = async () => {
    if (working) return;
    setBusy("finish"); setErr("");
    let ok = true;
    if (!passed && canMarkDone) ok = await markDone();
    if (!ok) { setBusy(""); return; }
    const r = await apiPost("/api/home/scenario/complete", {
      account_id: account,
      scenario_id: Number(scenarioId),
    });
    setBusy("");
    if (r && r.status === "ok") openRoute("/dashboard/home");
    else setErr((r && (r.message || r.detail)) || "Не удалось завершить сценарий");
  };

  let mainLabel = "Продолжить →";
  let mainAction: any = onContinue;
  let mainOff = false;
  let hint = "";

  if (isLast) {
    mainAction = onFinish;
    if (passed) mainLabel = "Завершить сценарий";
    else if (canMarkDone) mainLabel = "Готово, завершить сценарий";
    else { mainLabel = "Завершить сценарий"; mainOff = true; hint = "Сначала выполните текущий шаг: " + cur.title; }
  } else if (!passed) {
    if (canMarkDone) mainLabel = "Готово, дальше →";
    else { mainOff = true; hint = "Сначала выполните текущий шаг: " + cur.title; }
  }

  if (working) mainLabel = "Сохраняю…";

  return (
    <div style={wrap} data-testid="scenario-nav">
      <div style={head}>
        Бизнес-сценарий «{scen.title}» · шаг {cur.index || steps.indexOf(cur) + 1} из {total}
      </div>
      <div style={name}>{cur.title}</div>

      {allPassed && <div style={okS}>✓ Все шаги выполнены</div>}
      {!allPassed && passed && <div style={okS}>✓ Шаг выполнен</div>}

      <div style={rowS}>
        {prev && (
          <button style={btnGhost} onClick={onBack} disabled={working} data-testid="scenario-nav-back">
            ← Назад
          </button>
        )}
        <button
          style={mainOff || working ? btnMainOff : btnMain}
          onClick={mainAction}
          disabled={mainOff || working}
          data-testid="scenario-nav-next"
        >
          {mainLabel}
        </button>
        <button style={exitS} onClick={onExit || dropParams} disabled={working}>
          Выйти из сценария
        </button>
      </div>

      {hint !== "" && <div style={hintS}>{hint}</div>}
      {err !== "" && <div style={errS}>{err} — попробуйте ещё раз</div>}
    </div>
  );
}

/* Обёртка: сама читает указатели из URL и включается только при них.
   Смонтирована один раз в корневом layout, поэтому работает и на вкладках
   монолита, и на отдельных страницах-шагах. Вне сценария не рисует ничего. */
export default function ScenarioNavBar() {
  const [ctx, setCtx] = useState<{ id: string; step: string } | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const q = new URLSearchParams(window.location.search);
    const id = q.get("scenario");
    if (id) setCtx({ id: id, step: q.get("step") || "" });
    else setCtx(null);
  }, []);

  if (!ctx) return null;
  return <ScenarioNavigation scenarioId={ctx.id} currentStepId={ctx.step} />;
}
