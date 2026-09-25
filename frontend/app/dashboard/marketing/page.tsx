"use client";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import AppShell from "@/app/components/shell/AppShell";
import { useAccounts } from "@/app/lib/AccountContext";
import { Alert, Button, Card, EmptyState, Kpi, KpiRow, Page } from "@/app/ui";
import { color, font, space } from "@/app/ui/tokens";

function authHeaders(extra: Record<string, string> = {}) {
  const token = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return { ...extra, Authorization: `Bearer ${token}` };
}

export default function MarketingPage() {
  const { selectedAccountId, selectedAccount, loading: accountsLoading } = useAccounts();
  const [targetLeads, setTargetLeads] = useState(0);
  const [maxCpl, setMaxCpl] = useState(0);
  const [temperature, setTemperature] = useState("любые");
  const [dailyBudget, setDailyBudget] = useState(0);
  const [hardMaxBid, setHardMaxBid] = useState(40);
  const [bidAutopilot, setBidAutopilot] = useState(false);
  const [goalAuto, setGoalAuto] = useState<boolean | null>(null);
  const [republishEnabled, setRepublishEnabled] = useState(false);
  const [republishIntervalDays, setRepublishIntervalDays] = useState(7);
  const [republishMaxItems, setRepublishMaxItems] = useState(3);
  const [republishMinViews, setRepublishMinViews] = useState(10);
  const [republishZeroDays, setRepublishZeroDays] = useState(7);
  const [republishRuntime, setRepublishRuntime] = useState<any>(null);
  const [readiness, setReadiness] = useState<any>(null);
  const [check, setCheck] = useState<any>(null);
  const [actionMemory, setActionMemory] = useState<any[]>([]);
  const [workspace, setWorkspace] = useState<any>(null);
  const [positionMonitor, setPositionMonitor] = useState<any>(null);
  const [wordstatQuery, setWordstatQuery] = useState("");
  const [wordstat, setWordstat] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [section, setSection] = useState<"overview" | "goals" | "work" | "demand">("overview");

  const load = useCallback(async () => {
    if (!selectedAccountId) return;
    setLoading(true); setError("");
    const id = encodeURIComponent(selectedAccountId);
    try {
      const [settingsRes, checkRes, actionsRes, autoRes, readyRes, workspaceRes, positionRes, republishRes] = await Promise.all([
        fetch(`/api/avito/kpi_settings?account_id=${id}`, { headers: authHeaders(), cache: "no-store" }),
        fetch(`/api/avito/kpi_check?account_id=${id}`, { headers: authHeaders(), cache: "no-store" }),
        fetch(`/api/avito/kpi_action_memory?account_id=${id}&limit=12`, { headers: authHeaders(), cache: "no-store" }),
        fetch(`/api/avito/autopilot_settings?account_id=${id}`, { headers: authHeaders(), cache: "no-store" }),
        fetch(`/api/avito/ai_marketing_readiness?account_id=${id}`, { headers: authHeaders(), cache: "no-store" }),
        fetch(`/api/avito/ai_marketing_daily_workspace?account_id=${id}`, { headers: authHeaders(), cache: "no-store" }),
        fetch(`/api/avito/position_monitor?account_id=${id}&history_limit=120`, { headers: authHeaders(), cache: "no-store" }),
        fetch(`/api/avito/republish_settings?account_id=${id}`, { headers: authHeaders(), cache: "no-store" }),
      ]);
      if (!settingsRes.ok) throw new Error(`HTTP ${settingsRes.status}`);
      const settingsData = await settingsRes.json();
      const settings = settingsData?.settings || {};
      setTargetLeads(Number(settings.target_leads_per_day || 0));
      setMaxCpl(Number(settings.max_cost_per_lead_rub || 0));
      setTemperature(settings.lead_temperature || "любые");
      setDailyBudget(Number(settings.daily_budget_limit_rub || 0));
      setHardMaxBid(Number(settings.hard_max_bid_rub || 40));
      setBidAutopilot(Boolean(settings.bid_autopilot));
      setCheck(checkRes.ok ? await checkRes.json() : null);
      const actionData = actionsRes.ok ? await actionsRes.json() : null;
      setActionMemory(Array.isArray(actionData?.items) ? actionData.items : []);
      const autoData = autoRes.ok ? await autoRes.json() : null;
      setGoalAuto(["goal_auto", "always_auto"].includes(String(autoData?.settings?.mode || ""))); // AUTONOMOUS_MARKETING_MODE_UNIFIED_V1
      setReadiness(readyRes.ok ? await readyRes.json() : null);
      setWorkspace(workspaceRes.ok ? await workspaceRes.json() : null);
      setPositionMonitor(positionRes.ok ? await positionRes.json() : null);
      const republishData = republishRes.ok ? await republishRes.json() : null;
      const republish = republishData?.settings || {};
      setRepublishEnabled(Boolean(republish.schedule_enabled));
      setRepublishIntervalDays(Math.min(30, Math.max(3, Number(republish.interval_days || 7))));
      setRepublishMaxItems(Math.min(5, Math.max(1, Number(republish.max_items_per_cycle || 3))));
      setRepublishMinViews(Math.max(1, Number(republish.min_views_no_contact || 10)));
      setRepublishZeroDays(Math.min(30, Math.max(1, Number(republish.zero_views_days || 7))));
      setRepublishRuntime(republishData?.runtime || null);
    } catch {
      setError("Не удалось получить реальные маркетинговые показатели");
    } finally {
      setLoading(false);
    }
  }, [selectedAccountId]);

  useEffect(() => {
    // Account switch must clear the previous tenant before any new fetch completes.
    setCheck(null); setReadiness(null); setWorkspace(null); setPositionMonitor(null); setActionMemory([]);
    setTargetLeads(0); setMaxCpl(0); setDailyBudget(0); setHardMaxBid(40); setGoalAuto(null);
    setRepublishEnabled(false); setRepublishIntervalDays(7); setRepublishMaxItems(3);
    setRepublishMinViews(10); setRepublishZeroDays(7); setRepublishRuntime(null);
    setWordstat(null); setError(""); setSaved("");
    void load();
  }, [load]);

  async function save() {
    if (!selectedAccountId) return;
    setBusy(true); setError(""); setSaved("");
    try {
      const res = await fetch("/api/avito/set_kpi_settings", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          account_id: selectedAccountId,
          target_leads_per_day: targetLeads,
          max_cost_per_lead_rub: maxCpl,
          daily_budget_limit_rub: dailyBudget,
          hard_max_bid_rub: hardMaxBid,
          bid_autopilot: bidAutopilot,
          lead_temperature: temperature,
          ...(goalAuto !== null ? { goal_auto_enabled: goalAuto } : {}),
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const republishRes = await fetch("/api/avito/set_republish_settings", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          account_id: selectedAccountId,
          enabled: true,
          schedule_enabled: republishEnabled,
          interval_days: Math.min(30, Math.max(3, republishIntervalDays)),
          max_items_per_cycle: Math.min(5, Math.max(1, republishMaxItems)),
          min_views_no_contact: Math.max(1, republishMinViews),
          zero_views_days: Math.min(30, Math.max(1, republishZeroDays)),
        }),
      });
      if (!republishRes.ok) throw new Error(`HTTP ${republishRes.status}`);
      setSaved(republishEnabled ? "Маркетинговые цели и расписание перепубликации сохранены" : "Маркетинговые цели сохранены · перепубликация выключена");
      await load();
    } catch {
      setError("Не удалось сохранить маркетинговые цели");
    } finally { setBusy(false); }
  }

  async function confirmTariff(state: "CONFIRMED_ACTIVE" | "CONFIRMED_NOT_ACTIVE" | "NOT_CONFIRMED") {
    if (!selectedAccountId || !readiness?.required_avito_tariff) return;
    setBusy(true); setError(""); setSaved("");
    try {
      const res = await fetch("/api/avito/ai_marketing_tariff_confirmation", {
        method: "POST",
        credentials: "include",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ account_id: selectedAccountId, state, actual_tariff: readiness.required_avito_tariff }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok || body?.status !== "ok") throw new Error(body?.detail || `HTTP ${res.status}`);

      // Never show optimistic confirmation. Reread the authoritative сервера BORIS
      // projection and require the persisted evidence to match this account.
      const verifyRes = await fetch(`/api/avito/ai_marketing_readiness?account_id=${encodeURIComponent(selectedAccountId)}`, {
        credentials: "include", headers: authHeaders(), cache: "no-store",
      });
      const verified = verifyRes.ok ? await verifyRes.json() : null;
      const expected = state === "CONFIRMED_ACTIVE" ? "CONFIRMED_ACTIVE" : state;
      if (!verified || verified.tariff_confirmation_state !== expected) {
        throw new Error("Подтверждение не сохранилось — повторите действие");
      }
      setReadiness(verified);
      setSaved(state === "CONFIRMED_ACTIVE" ? "Тариф подтверждён и сохранён для этого аккаунта" : "Состояние тарифа сохранено");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить подтверждение тарифа");
    } finally { setBusy(false); }
  }

  function actionStage(a: any) {
    const status = String(a?.status || ""); const effect = String(a?.effect?.status || a?.effect_status || "");
    if (effect === "improved") return "Цена/результат улучшились — изменение признано эффективным";
    if (effect === "worse") return "Изменение оказалось неэффективным — BORIS выберет следующий безопасный шаг";
    if (status === "published") return "Изменение опубликовано · собираю статистику";
    if (status === "publish_requested") return "Изменение отправлено в Avito · ожидаю подтверждение";
    if (status === "feed_applied") return "Изменение готово в рабочем фиде";
    if (status === "safe_feed_ready") return "Подготовил безопасный вариант · проверяю возможность публикации";
    if (status === "prepared") return "Решил проверить объявление · подготовил вариант";
    return a?.reason || "BORIS анализирует результат";
  }

  function wordstatEntries(value: any): Array<[string, unknown]> {
    if (!value || typeof value !== "object" || Array.isArray(value)) return [];
    return Object.entries(value);
  }

  function wordstatValue(value: unknown): string {
    if (value === null || value === undefined || value === "") return "Нет данных";
    if (Array.isArray(value)) return value.map((item) => typeof item === "object" ? Object.values(item as Record<string, unknown>).join(" · ") : String(item)).join(", ");
    if (typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${key}: ${String(item ?? "—")}`).join(" · ");
    if (typeof value === "boolean") return value ? "Да" : "Нет";
    return String(value);
  }

  async function analyzeWordstat() {
    if (!selectedAccountId || !wordstatQuery.trim()) return;
    setBusy(true); setError(""); setWordstat(null);
    try {
      const res = await fetch(`/api/avito/wordstat_analyze?query=${encodeURIComponent(wordstatQuery.trim())}&account_id=${encodeURIComponent(selectedAccountId)}`, {
        headers: authHeaders(),
        cache: "no-store",
      });
      const data = await res.json();
      if (!res.ok || data?.status === "error") throw new Error(data?.message || `HTTP ${res.status}`);
      setWordstat(data);
    } catch {
      setError("Не удалось выполнить анализ спроса");
    } finally { setBusy(false); }
  }

  async function refreshPositions() {
    if (!selectedAccountId || busy) return;
    setBusy(true); setError(""); setSaved("");
    try {
      const id = encodeURIComponent(selectedAccountId);
      const res = await fetch(`/api/avito/position_monitor/refresh?account_id=${id}`, {
        method: "POST", headers: authHeaders(), cache: "no-store",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || body?.status === "error" || body?.status === "blocked") throw new Error(body?.reason || `HTTP ${res.status}`);
      setSaved(body?.status === "queued" ? "Замер позиции поставлен в очередь — BORIS обновит данные автоматически" : body?.status === "fresh" ? "Позиции уже измерены недавно" : "BORIS проверит позиции при ближайшей безопасной сессии Avito");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось запустить проверку позиций");
    } finally { setBusy(false); }
  }

  return (
    <AppShell active="marketing" title="Маркетинг" subtitle="Цели, спрос и переходы в реальные контентные инструменты">
      <Page className="boris-marketing-reference-page">
        {error ? <Alert kind="danger">{error}</Alert> : null}
        {saved ? <Alert kind="success">{saved}</Alert> : null}
        {readiness?.goal_auto_enabled && readiness?.required_avito_tariff && readiness?.required_tariff_active !== true ? <Alert kind="warning"><b>Автопилот готов, но публикация изменений остановлена.</b> Подтвердите тариф Avito «{readiness.required_avito_tariff}» для этого аккаунта. Аналитика и подготовка безопасных изменений продолжают работать.</Alert> : null}
        {!accountsLoading && !selectedAccountId ? <EmptyState title="Не выбран аккаунт" hint="Выберите аккаунт в верхней панели." /> : null}
        {selectedAccountId ? (
          <>
            <section className="boris-marketing-hero">
              <div><span>ИИ-МАРКЕТОЛОГ</span><h1>Управление рекламой без хаоса</h1><p>Сначала главное: цель, результат и что BORIS делает сейчас. Детали разнесены по понятным разделам.</p></div>
              <div className="boris-marketing-hero-status"><small>Сегодня</small><b>{check?.contacts_today ?? "—"} обращений</b><span>{goalAuto ? "Автопилот включён" : "Автопилот выключен"}</span></div>
            </section>
            <nav className="boris-marketing-tabs" aria-label="Разделы маркетинга">
              {[
                ["overview","Обзор"],["goals","Цели и автопилот"],["work","Работа BORIS"],["demand","Анализ спроса"]
              ].map(([key,label]) => <button key={key} type="button" className={section === key ? "is-active" : ""} onClick={() => setSection(key as typeof section)}>{label}</button>)}
            </nav>
            <div className={`boris-marketing-reference-grid boris-marketing-section-${section}`}>
            <Card className="boris-marketing-plan-card" data-marketing-group="overview">
              <div style={{ ...font.small, color: color.muted }}>{selectedAccount?.name || selectedAccountId}</div>
              <h2 style={{ ...font.h2, margin: "6px 0 16px" }}>План / факт</h2>
              {loading ? <div style={font.body}>Загрузка реальных показателей…</div> : (
                <KpiRow>
                  <Kpi value={(check?.target_leads_per_day ?? targetLeads) || "—"} label="лиды · план/день" />
                  <Kpi value={check?.contacts_today ?? "—"} label="лиды · факт сегодня" kind="info" />
                  <Kpi value={check?.cost_per_lead_today != null ? `${check.cost_per_lead_today} ₽` : "—"} label="Цена лида · факт" />
                  <Kpi value={(check?.max_cost_per_lead_rub ?? maxCpl) ? `${check?.max_cost_per_lead_rub ?? maxCpl} ₽` : "—"} label="Цена лида · цель" />
                </KpiRow>
              )}
              {check?.recommendation ? <div style={{ marginTop: space.md, padding: space.md, borderRadius: 12, background: color.bg }}><b>Рекомендация BORIS:</b> {check.recommendation}</div> : null}
            </Card>

            <Card className="boris-marketing-position-card" data-marketing-group="overview">
              <div style={{display:"flex",alignItems:"flex-start",justifyContent:"space-between",gap:12,flexWrap:"wrap"}}>
                <div><div style={{fontSize:11,fontWeight:800,letterSpacing:".08em",textTransform:"uppercase",color:"#2f6df6"}}>Позиция в поиске Avito</div><h2 style={{...font.h2,margin:"4px 0 0"}}>Где находятся объявления</h2><div style={{...font.small,color:color.muted,marginTop:5}}>BORIS сохраняет позицию, изменение места и связывает это с просмотрами, обращениями и CPL. Позиция не отменяет бюджетные ограничения.</div></div>
                <Button onClick={() => void refreshPositions()} disabled={busy}>Проверить сейчас</Button>
              </div>
              {(() => {
                const signal = positionMonitor?.signal || positionMonitor?.latest?.signal || {};
                const rows = Array.isArray(positionMonitor?.latest?.rows) ? positionMonitor.latest.rows : [];
                const health = positionMonitor?.health || {};
                const measuredAt = positionMonitor?.latest?.measured_at;
                return <>
                  <div style={{marginTop:space.md}}><KpiRow>
                    <Kpi value={signal.median_position != null ? Math.round(Number(signal.median_position)) : "—"} label="медианная позиция" />
                    <Kpi value={signal.top_15 ?? 0} label="в топ-15" kind="info" />
                    <Kpi value={signal.low_below_30 ?? 0} label="ниже 30 места" />
                    <Kpi value={signal.hidden ?? 0} label="скрыто из поиска" />
                  </KpiRow></div>
                  {measuredAt ? <div style={{...font.small,color:color.muted,marginTop:8}}>Последний замер: {new Date(measuredAt).toLocaleString("ru-RU")} · источник — кабинет Avito</div> : <div style={{...font.small,color:color.muted,marginTop:8}}>Позиция ещё не измерена для этого аккаунта. BORIS сделает замер автоматически, когда доступна подтверждённая сессия кабинета Avito.</div>}
                  {health?.state === "deferred" && !rows.length ? <div style={{marginTop:10,padding:12,borderRadius:12,background:"#fff8e8",...font.small}}>Монитор позиций ожидает безопасное подключение кабинета этого аккаунта. Остальная работа ИИ-маркетолога продолжается без остановки.</div> : null}
                  {health?.state === "partial" ? <div style={{marginTop:10,padding:12,borderRadius:12,background:"#fff8e8",...font.small}}><b>Замер частичный:</b> BORIS увидел {health.matched ?? rows.length} из {health.inventory ?? "—"} активных объявлений ({health.coverage_pct ?? 0}%). Эти данные используются только для найденных карточек и не выдаются за полный срез кабинета.</div> : null}
                  {rows.length ? <div style={{display:"grid",gap:8,marginTop:12}}>{rows.slice(0,10).map((r:any) => <div key={String(r.item_id || r.title)} style={{display:"grid",gridTemplateColumns:"minmax(0,1fr) auto",gap:12,alignItems:"center",padding:"10px 12px",border:`1px solid ${color.line}`,borderRadius:12}}>
                    <div style={{minWidth:0}}><b style={{display:"block",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{r.title || `Avito #${r.item_id}`}</b><span style={{...font.small,color:color.muted}}>{r.views ?? 0} просмотров · {r.contacts ?? 0} обращений{r.conversion != null ? ` · ${Number(r.conversion).toFixed(1)}%` : ""}</span></div>
                    <div style={{textAlign:"right",whiteSpace:"nowrap"}}><b style={{fontSize:17}}>{r.visibility === "hidden" ? "Скрыто" : r.position != null ? `${r.position} место` : "—"}</b><div style={{...font.small,color:Number(r.delta||0)>0?"#059669":Number(r.delta||0)<0?"#dc2626":color.muted}}>{Number(r.delta||0)>0?`↑ ${Math.abs(Number(r.delta))}`:Number(r.delta||0)<0?`↓ ${Math.abs(Number(r.delta))}`:"без изменения"}</div></div>
                  </div>)}</div> : null}
                </>;
              })()}
            </Card>

            <Card className="boris-marketing-goals-card" data-marketing-group="goals">
              <h2 style={{ ...font.h2, marginBottom: space.md }}>Маркетинговые цели</h2>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: space.md }}>
                <label style={font.small}>Лидов в день<input style={{ width: "100%", display: "block", marginTop: 6 }} type="number" min={0} value={Number(targetLeads) === 0 ? "" : targetLeads} onChange={(e) => setTargetLeads(Number(e.target.value))} /></label>
                <label style={font.small}>Максимальная цена лида, ₽<input style={{ width: "100%", display: "block", marginTop: 6 }} type="number" min={0} value={Number(maxCpl) === 0 ? "" : maxCpl} onChange={(e) => setMaxCpl(Number(e.target.value))} /></label>
                <label style={font.small}>Суточный рекламный бюджет, ₽<input style={{ width: "100%", display: "block", marginTop: 6 }} type="number" min={0} value={Number(dailyBudget) === 0 ? "" : dailyBudget} onChange={(e) => setDailyBudget(Number(e.target.value))} placeholder="Например, 1500" /></label>
                <label style={font.small}>Максимальная ставка за просмотр, ₽<input style={{ width: "100%", display: "block", marginTop: 6 }} type="number" min={1} value={hardMaxBid} onChange={(e) => setHardMaxBid(Math.max(1, Number(e.target.value) || 1))} /></label>
                <label style={font.small}>Температура лида<select style={{ width: "100%", display: "block", marginTop: 6 }} value={temperature} onChange={(e) => setTemperature(e.target.value)}><option>любые</option><option>тёплые</option><option>горячие</option></select></label>
              </div>
              <div style={{ marginTop: space.md, padding: space.md, border: `1px solid ${color.line}`, borderRadius: 12, background: color.bg }}>
                <b>Цель BORIS: получить не менее {targetLeads || "—"} обращений в день при цене обращения не выше {maxCpl || "—"} ₽.</b>
                <label style={{ display:"flex", gap:10, alignItems:"flex-start", marginTop:10, cursor:"pointer", ...font.small }}>
                  <input type="checkbox" checked={goalAuto === true} onChange={(e) => setGoalAuto(e.target.checked)} style={{marginTop:3}} />
                  <span><b>Автоматически достигать цели</b><br/><span style={{color:color.muted}}>BORIS будет сам выполнять разрешённые рекламные действия для достижения плана. Ограничения безопасности сохраняются.</span></span>
                </label>
                <div style={{ ...font.small, color: color.muted, marginTop: 8 }}>Автопилот по цели: <b>{goalAuto === null ? "Требуется настройка" : goalAuto ? "Включён" : "Выключен"}</b>{dailyBudget > 0 ? ` · бюджет ${dailyBudget} ₽/день` : " · денежные действия остановлены: бюджет не задан"} · потолок ставки {hardMaxBid} ₽</div>
                {goalAuto === true && dailyBudget <= 0 ? <div style={{marginTop:10,padding:12,borderRadius:12,background:"#fff8e8",...font.small}}><b>Последующий разгон ставок остановлен.</b> Новое объявление из автозагрузки может получить только одну безопасную стартовую ставку в пределах вашего максимума; дальнейшие повышения требуют суточного рекламного бюджета. Анализ, контент и бесплатные действия продолжаются.</div> : null}
              </div>
              <div style={{ marginTop: space.md, padding: space.md, border: `1px solid ${color.line}`, borderRadius: 16, background: "linear-gradient(135deg,#f8fbff 0%,#fff 70%)" }}>
                <div style={{ display:"flex", justifyContent:"space-between", alignItems:"flex-start", gap:16, flexWrap:"wrap" }}>
                  <div style={{ maxWidth:760 }}>
                    <div style={{fontSize:11,fontWeight:800,letterSpacing:".08em",textTransform:"uppercase",color:"#2f6df6"}}>Перепубликация объявлений</div>
                    <h3 style={{ margin:"5px 0 6px", fontSize:20, color:color.heading }}>Повторный заброс в тот же спрос</h3>
                    <div style={{...font.small,color:color.muted,lineHeight:1.5}}>
                      Одно и то же предложение в разные дни может дать разный результат. BORIS через заданный интервал снова проверяет слабые объявления и делает новый безопасный «заброс» — без бессмысленного удаления тех карточек, которые уже работают.
                    </div>
                  </div>
                  <label style={{display:"flex",gap:9,alignItems:"center",cursor:"pointer",...font.small}}>
                    <input type="checkbox" checked={republishEnabled} onChange={(e) => setRepublishEnabled(e.target.checked)} />
                    <b>{republishEnabled ? "Автоперепубликация включена" : "Автоперепубликация выключена"}</b>
                  </label>
                </div>

                <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))", gap:12, marginTop:16 }}>
                  <label style={font.small}>Интервал, дней
                    <input style={{ width:"100%", display:"block", marginTop:6 }} type="number" min={3} max={30} value={republishIntervalDays} onChange={(e) => setRepublishIntervalDays(Math.min(30,Math.max(3,Number(e.target.value)||3)))} />
                  </label>
                  <label style={font.small}>Объявлений за цикл
                    <input style={{ width:"100%", display:"block", marginTop:6 }} type="number" min={1} max={5} value={republishMaxItems} onChange={(e) => setRepublishMaxItems(Math.min(5,Math.max(1,Number(e.target.value)||1)))} />
                  </label>
                  <label style={font.small}>Просмотров без обращения
                    <input style={{ width:"100%", display:"block", marginTop:6 }} type="number" min={1} value={republishMinViews} onChange={(e) => setRepublishMinViews(Math.max(1,Number(e.target.value)||1))} />
                  </label>
                  <label style={font.small}>Или 0 просмотров, дней
                    <input style={{ width:"100%", display:"block", marginTop:6 }} type="number" min={1} max={30} value={republishZeroDays} onChange={(e) => setRepublishZeroDays(Math.min(30,Math.max(1,Number(e.target.value)||1)))} />
                  </label>
                </div>

                <div style={{ marginTop:16, padding:14, borderRadius:12, background:"#f8fafc", border:"1px solid #edf1f7" }}>
                  <b>Что BORIS делает каждые {republishIntervalDays} дней</b>
                  <ol style={{ margin:"10px 0 0", paddingLeft:20, display:"grid", gap:7, ...font.small }}>
                    <li><b>Проверяет объявления.</b> Ищет карточки с {republishMinViews}+ просмотрами и 0 обращений либо без просмотров {republishZeroDays} дней.</li>
                    <li><b>Берёт до {republishMaxItems} карточек за один цикл.</b> Это даёт несколько «удочек», но не превращает аккаунт в хаотичную массовую перезаливку.</li>
                    <li><b>Делает свежий вариант в фиде.</b> Новый ID, уникализированный заголовок/описание и безопасная галерея из того же направления и города. Работающее активное объявление вслепую не удаляется.</li>
                    <li><b>Не снимает работающие карточки вслепую.</b> Текущий безопасный режим перепубликации создаёт новый вариант в рабочем фиде; снятие и возврат активной карточки допускаются только после отдельного подтверждённого состояния Avito.</li>
                    <li><b>Публикует через существующий рабочий фид и измеряет результат.</b> Следующий цикл начинается только после выбранного интервала.</li>
                  </ol>
                </div>

                <div style={{ ...font.small, color:color.muted, marginTop:10 }}>
                  {republishEnabled
                    ? <>Следующий цикл: <b>{republishRuntime?.next_run_at ? new Date(republishRuntime.next_run_at).toLocaleString("ru-RU") : "при ближайшем тике BORIS после сохранения"}</b>{republishRuntime?.last_cycle_at ? <> · последний: {new Date(republishRuntime.last_cycle_at).toLocaleString("ru-RU")}</> : null}</>
                    : <>Расписание выключено. BORIS продолжает обычную KPI-работу без автоматической перепубликации.</>}
                </div>
              </div>
              {readiness?.required_avito_tariff ? <div style={{ marginTop:space.md, padding:space.md, border:`1px solid ${color.line}`, borderRadius:12 }}>
                <b>Тариф Avito</b>
                <div style={{...font.small, marginTop:6}}>Требуется: <b>{readiness.required_avito_tariff}</b></div>
                <div style={{...font.small, color:color.muted, marginTop:4}}>{readiness.tariff_confirmation_state === "CONFIRMED_ACTIVE" ? `Подтверждено: ${readiness.tariff_confirmation?.actual_tariff || readiness.required_avito_tariff}` : readiness.tariff_confirmation_state === "CONFIRMED_NOT_ACTIVE" ? "Подтверждено: нужный тариф сейчас не активен" : "Тариф пока не подтверждён. Avito API не сообщает BORIS коммерческое название тарифа."}</div>
                {readiness.tariff_confirmation ? <div style={{...font.small, color:color.muted, marginTop:4}}>Источник: {readiness.tariff_confirmation.source === "user_confirmation" ? "подтверждено владельцем" : readiness.tariff_confirmation.source || "—"}{readiness.tariff_confirmation.confirmed_at ? ` · ${new Date(readiness.tariff_confirmation.confirmed_at).toLocaleString("ru-RU")}` : ""}</div> : null}
                <div style={{...font.small, marginTop:4}}>Статус публикации: <b>{readiness.capabilities?.publish_feed_change?.allowed ? "разрешена" : "остановлена"}</b></div>
                <div style={{display:"flex", gap:8, flexWrap:"wrap", marginTop:10}}>
                  <Button onClick={() => void confirmTariff("CONFIRMED_ACTIVE")} disabled={busy}>Да, нужный тариф подключён</Button>
                  <Button onClick={() => void confirmTariff("CONFIRMED_NOT_ACTIVE")} disabled={busy}>Нет, ещё не подключён</Button>
                </div>
              </div> : null}
              <div style={{ marginTop: space.md }}><Button onClick={save} disabled={busy || loading}>{busy ? "Сохраняем…" : goalAuto ? "Сохранить и запустить" : "Сохранить цель и режим"}</Button></div>
            </Card>

            <Card className="boris-marketing-readiness-card" data-marketing-group="overview">
              <h2 style={{...font.h2, marginBottom:space.md}}>Готовность аккаунта</h2>
              <div style={{display:"grid", gap:8, ...font.small}}>
                <div>{readiness?.avito_connected ? "✓" : "○"} Avito подключён</div>
                <div>{readiness?.kpi_configured ? "✓" : "○"} Цель настроена</div>
                <div>{readiness?.goal_auto_enabled ? "✓" : "○"} Автопилот включён</div>
                <div>{readiness?.money_budget_configured ? "✓" : "○"} Суточный рекламный бюджет задан{readiness?.money_budget_configured ? ` · ${readiness.daily_budget_limit_rub} ₽` : " · денежные действия остановлены"}</div>
                <div>{readiness?.required_tariff_active === true ? "✓" : "○"} Требуемый тариф подтверждён</div>
                <div>{readiness?.feed_factory_ready ? "✓" : "○"} Автозагрузка объявлений готова</div>
                <div>{readiness?.mapped_campaign_items > 0 ? "✓" : "○"} BORIS связан с рабочим фидом для {readiness?.mapped_campaign_items ?? 0} из {readiness?.campaign_items ?? 0} объявлений{readiness?.campaign_items > readiness?.mapped_campaign_items ? ` — ещё ${Number(readiness.campaign_items) - Number(readiness.mapped_campaign_items || 0)} объявления не привязаны к рабочему фиду, поэтому BORIS не меняет именно их до безопасной привязки` : " — все объявления доступны для безопасной работы"}</div>
              </div>
              <div className="boris-marketing-capability-summary" data-ready={Object.values(readiness?.capabilities || {}).every((x: any) => x?.allowed) ? "yes" : "no"}>
                <b>{Object.values(readiness?.capabilities || {}).every((x: any) => x?.allowed) ? "Все основные возможности BORIS включены" : "Есть ограничения по отдельным действиям"}</b>
                <span>{Object.values(readiness?.capabilities || {}).every((x: any) => x?.allowed) ? "Анализ, подготовка, публикация через рабочий фид и измерение результата разрешены. Ограничение 10 из 12 относится только к двум объявлениям без безопасной feed-привязки." : "Ниже показано, какое условие нужно выполнить для каждого недоступного действия."}</span>
              </div>
              <div style={{marginTop:space.md, display:"grid", gap:6, ...font.small}}>
                <b>Возможности сейчас</b>
                {Object.entries(readiness?.capabilities || {}).map(([key, raw]: any) => <div key={key}>{raw.allowed ? "✓" : "○"} {({analyze_statistics:"Анализировать статистику",detect_kpi_gap:"Искать отклонение от цели",prepare_safe_change:"Готовить безопасные изменения",publish_feed_change:"Публиковать изменения через Avito",measure_effect:"Измерять эффект"} as any)[key] || key}{!raw.allowed && raw.reason ? ` — ${humanizeBusinessText(raw.reason, "условие пока не выполнено")}` : ""}</div>)}
              </div>
            </Card>

            <Card className="boris-marketing-work-card" data-marketing-group="work">
              <div style={{display:"flex",alignItems:"flex-end",justifyContent:"space-between",gap:16,flexWrap:"wrap",marginBottom:space.md}}>
                <div>
                  <div style={{fontSize:11,fontWeight:800,letterSpacing:".08em",textTransform:"uppercase",color:"#2f6df6"}}>ИИ-маркетолог · живая работа</div>
                  <h2 style={{ ...font.h2, margin:"3px 0 0" }}>Что BORIS улучшает сейчас</h2>
                  <div style={{...font.small,color:color.muted,marginTop:5}}>Только понятные бизнес-задачи: что мешает результату, что меняется и какой эффект ждём.</div>
                </div>
                <Button onClick={load} disabled={loading}>Обновить</Button>
              </div>
              {workspace ? (() => {
                const meta:any = {
                  "Нужно улучшить": {title:"Есть точка роста", icon:"↗", accent:"#ef476f", soft:"#fff3f6"},
                  "BORIS работает": {title:"BORIS улучшает", icon:"✦", accent:"#2f6df6", soft:"#eef5ff"},
                  "Ждём Avito": {title:"Изменение отправлено", icon:"➜", accent:"#8b5cf6", soft:"#f5f3ff"},
                  "Измеряем результат": {title:"Собираем результат", icon:"◔", accent:"#f59e0b", soft:"#fff8e8"},
                  "Стало лучше": {title:"Есть рост", icon:"✓", accent:"#10b981", soft:"#ecfdf5"},
                  "Не помогло": {title:"Ищем следующий ход", icon:"↻", accent:"#f97316", soft:"#fff7ed"},
                  "Нужно решение владельца": {title:"Нужно ваше решение", icon:"!", accent:"#dc2626", soft:"#fff1f2"},
                };
                const active = ["Нужно улучшить","BORIS работает","Ждём Avito","Измеряем результат","Стало лучше","Не помогло","Нужно решение владельца"]
                  .flatMap((group) => (workspace.groups?.[group] || []).map((item:any) => ({group,item})));
                if (!active.length) return <div style={{padding:22,border:`1px solid ${color.line}`,borderRadius:16,background:"linear-gradient(135deg,#f8fbff,#fff)",display:"flex",alignItems:"center",gap:14}}>
                  <span style={{width:46,height:46,borderRadius:14,display:"grid",placeItems:"center",background:"#ecfdf5",color:"#059669",fontSize:22}}>✓</span>
                  <div><div style={{...font.bodyStrong,color:color.heading}}>Сейчас всё под контролем</div><div style={{...font.small,color:color.muted,marginTop:4}}>BORIS не показывает пустые этапы. Новая карточка появится, когда найдётся конкретная точка роста или измеримый результат.</div></div>
                </div>;
                return <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(290px,1fr))",gap:14,alignItems:"stretch"}}>
                  {active.slice(0,9).map(({group,item}:any,idx:number) => {
                    const m=meta[group]||meta["BORIS работает"];
                    const before=item.kpi_before||{}; const after=item.kpi_after||{};
                    const hasAfter=after.views!=null || after.contacts!=null;
                    const problem=String(item.problem||"").replace(/^KPI goal_auto:\s*/i,"").replace(/^goal_auto:\s*/i,"");
                    const change=String(item.what_changed||"").replace(/^KPI goal_auto:\s*/i,"").replace(/^goal_auto:\s*/i,"");
                    return <div key={`${group}-${item.item_id||idx}`} style={{position:"relative",overflow:"hidden",minHeight:205,padding:17,border:`1px solid ${color.line}`,borderRadius:16,background:"linear-gradient(180deg,#fff 0%,#fbfcff 100%)",boxShadow:"0 8px 24px rgba(31,58,103,.06)",display:"flex",flexDirection:"column",gap:10,transition:"transform .18s ease, box-shadow .18s ease"}} className="boris-marketing-live-card">
                      <span style={{position:"absolute",inset:"0 0 auto 0",height:3,background:m.accent}} />
                      <div style={{display:"flex",alignItems:"center",gap:11}}><span style={{width:42,height:42,borderRadius:13,display:"grid",placeItems:"center",background:m.soft,color:m.accent,fontSize:21,fontWeight:800}}>{m.icon}</span><div><div style={{fontSize:11,fontWeight:800,color:m.accent,textTransform:"uppercase",letterSpacing:".06em"}}>{m.title}</div><div style={{fontSize:15.5,fontWeight:750,color:color.heading,marginTop:2}}>{problem || "Работаем над улучшением результата"}</div></div></div>
                      {change ? <div style={{fontSize:13,lineHeight:1.45,color:"#475569"}}><b style={{color:color.heading}}>Что меняется:</b> {change}</div> : null}
                      <div style={{marginTop:"auto",display:"grid",gridTemplateColumns:"repeat(2,minmax(0,1fr))",gap:8}}>
                        <div style={{padding:"9px 10px",borderRadius:11,background:"#f8fafc",border:"1px solid #edf1f7"}}><div style={{fontSize:10.5,color:"#94a3b8",textTransform:"uppercase"}}>Обращения</div><b style={{fontSize:18,color:color.heading}}>{hasAfter ? `${before.contacts??0} → ${after.contacts??0}` : before.contacts ?? "—"}</b></div>
                        <div style={{padding:"9px 10px",borderRadius:11,background:"#f8fafc",border:"1px solid #edf1f7"}}><div style={{fontSize:10.5,color:"#94a3b8",textTransform:"uppercase"}}>Просмотры</div><b style={{fontSize:18,color:color.heading}}>{hasAfter ? `${before.views??0} → ${after.views??0}` : before.views ?? "—"}</b></div>
                      </div>
                      {item.next_step ? <div style={{fontSize:12.5,lineHeight:1.4,color:"#64748b",paddingTop:8,borderTop:"1px solid #eef2f7"}}><b style={{color:color.heading}}>Следующая цель:</b> {item.next_step}</div> : null}
                    </div>;
                  })}
                </div>;
              })() : <div style={{...font.small,color:color.muted}}>Загрузка рабочей очереди…</div>}
            </Card>

            <Card className="boris-marketing-today-card" data-marketing-group="overview">
              <h2 style={{...font.h2,marginBottom:space.md}}>Сегодня BORIS</h2>
              <div className="boris-marketing-today-grid">
                {[
                  ["Точки роста",workspace?.daily_report?.problems_found,"#2f6fff","#eef4ff","◎","Что BORIS нашёл для улучшения"],
                  ["Решения готовы",workspace?.daily_report?.solutions_prepared,"#7c5cff","#f4efff","✦","Готово к применению или проверке"],
                  ["В работе",Number(workspace?.daily_report?.sent_or_published||0)+Number(workspace?.daily_report?.measuring||0),"#0ea5a8","#ecfbf8","↗","Изменения уже выполняются"],
                  ["Получен результат",workspace?.daily_report?.results_received,"#16a36a","#ecfbf4","✓","Есть измеренный итог"],
                ].map(([label,value,accent,soft,icon,hint]) => <div className="boris-marketing-today-tile" key={String(label)} style={{"--tile-accent":accent,"--tile-soft":soft} as React.CSSProperties}>
                  <span className="boris-marketing-today-icon">{icon}</span>
                  <div className="boris-marketing-today-copy"><strong>{value ?? 0}</strong><span>{label}</span><small>{hint}</small></div>
                </div>)}
              </div>
              {Number(workspace?.daily_report?.owner_actions_required || 0) > 0 ? <div className="boris-marketing-owner-alert">Нужно ваше решение: <b>{workspace.daily_report.owner_actions_required}</b> · <a href="/dashboard/factory-decisions">Открыть →</a></div> : null}
              {workspace?.sales_funnel_context ? <div style={{marginTop:space.md,...font.small}}><b>Контекст продаж сегодня:</b> диалогов в МОП {workspace.sales_funnel_context.mop_dialogs_today ?? 0} · лид-записей {workspace.sales_funnel_context.lead_records_today ?? 0} · сделок в системе продаж {workspace.sales_funnel_context.crm_deals_today ?? 0} · выиграно {workspace.sales_funnel_context.won_deals_today ?? 0}. <span style={{color:color.muted}}>Это контекст существующих МОП и системы продаж, не приписываемый конкретному изменению без доказанной связи.</span></div> : null}
              {workspace?.second_candidate_decision ? <div style={{marginTop:space.md,padding:space.md,border:`1px solid ${color.line}`,borderRadius:12}}><b>Следующий кандидат #{workspace.second_candidate_decision.item_id}</b><div style={{...font.small,marginTop:5}}>{workspace.second_candidate_decision.decision === "wait_for_evidence" ? "BORIS пока не меняет объявление." : "BORIS может рассмотреть действие в следующем свободном цикле."}</div><div style={{...font.small,color:color.muted,marginTop:4}}>{humanizeBusinessText(workspace.second_candidate_decision.reason, "Нужно больше данных")}</div>{workspace.second_candidate_decision.becomes_eligible_when ? <div style={{...font.small,marginTop:4}}><b>Когда можно решать:</b> {humanizeBusinessText(workspace.second_candidate_decision.becomes_eligible_when, "После накопления данных")}</div> : null}</div> : null}
            </Card>

            <Card className="boris-marketing-actions-card" data-marketing-group="work">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: space.sm, marginBottom: space.md }}>
                <div>
                  <h2 style={{ ...font.h2, margin: 0 }}>Действия BORIS сегодня</h2>
                  <div style={{ ...font.small, color: color.muted, marginTop: 4 }}>Какое объявление выбрал BORIS, что изменил и как будет измеряться эффект.</div>
                </div>
                <Button onClick={load} disabled={loading}>Обновить</Button>
              </div>
              {!actionMemory.length ? <EmptyState title="Изменений по цели пока нет" hint="Когда автопилот применит безопасную внутреннюю оптимизацию, она появится здесь." /> : (
                <div style={{ display: "grid", gap: space.sm }}>
                  {actionMemory.slice(0, 8).map((a: any) => {
                    const fields = Object.keys(a.changes || {});
                    return <div key={a.operation_id} style={{ border: `1px solid ${color.line}`, borderRadius: 12, padding: space.md }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: space.sm, flexWrap: "wrap" }}>
                        <b>Avito #{a.avito_item_id || "—"}</b>
                        <span style={{ ...font.small, color: color.muted }}>Версия {a.version || "—"}</span>
                      </div>
                      <div style={{ ...font.small, marginTop: 6 }}><b>{actionStage(a)}</b></div>
                      {a.reason ? <div style={{ ...font.small, color: color.muted, marginTop: 4 }}>{humanizeBusinessText(a.reason, "Оптимизация результата")}</div> : null}
                      {a.kpi_before ? <div style={{ ...font.small, marginTop: 8 }}>До изменения: {a.kpi_before.views ?? "—"} просмотров · {a.kpi_before.contacts ?? "—"} обращений · {a.kpi_before.conversion ?? "—"}%</div> : null}
                      {fields.length ? <div style={{ ...font.small, marginTop: 6 }}>Изменено: {fields.map((f: string) => `${humanizeBusinessText(f,"Поле")}: «${a.changes?.[f]?.old ?? ""}» → «${a.changes?.[f]?.new ?? ""}»`).join("; ")}</div> : null}
                      <div style={{ ...font.small, marginTop: 6 }}>Следующая проверка: {a.observation_window?.status === "pending" || a.effect?.status?.startsWith?.("waiting") ? "после публикации/накопления статистики" : "по штатному циклу BORIS"}</div>
                      {a.kpi_after ? <div style={{ ...font.small, marginTop: 6 }}>После изменения: {a.kpi_after.views ?? "—"} просмотров · {a.kpi_after.contacts ?? "—"} обращений · {a.kpi_after.conversion ?? "—"}%</div> : null}
                    </div>;
                  })}
                </div>
              )}
            </Card>

            <Card className="boris-marketing-demand-card" data-marketing-group="demand">
              <h2 style={{ ...font.h2, marginBottom: space.sm }}>Анализ спроса</h2>
              <div style={{ display: "flex", gap: space.sm, flexWrap: "wrap" }}>
                <input value={wordstatQuery} onChange={(e) => setWordstatQuery(e.target.value)} placeholder="Например: ремонт фар Санкт-Петербург" style={{ flex: "1 1 320px", minWidth: 0 }} />
                <Button onClick={analyzeWordstat} disabled={busy || !wordstatQuery.trim()}>Анализировать</Button>
              </div>
              {wordstat ? <div style={{ marginTop: space.md, display: "grid", gap: space.sm }}>
                {wordstatEntries(wordstat).length ? wordstatEntries(wordstat).map(([key, value]) => (
                  <div key={key} style={{ display: "grid", gridTemplateColumns: "minmax(140px, .45fr) minmax(0, 1fr)", gap: space.md, padding: "10px 12px", border: `1px solid ${color.line}`, borderRadius: 10, background: color.bg }}>
                    <div style={{ ...font.small, fontWeight: 700 }}>{key.replaceAll("_", " ")}</div>
                    <div style={font.body}>{wordstatValue(value)}</div>
                  </div>
                )) : <div style={{ ...font.small, color: color.muted }}>Анализ выполнен, но данных для отображения пока нет.</div>}
              </div> : <div style={{ ...font.small, color: color.muted, marginTop: space.sm }}>Результат появится только после фактического ответа сервера BORIS.</div>}
            </Card>

            </div>
            <div className="boris-marketing-footer-links">
              {[
                ["/dashboard/campaign","Объявления","Ассортимент, тексты, публикация и управление рекламой","▤","#2f6fff","#eef4ff"],
                ["/dashboard/campaign/banners","Баннеры","Креативы и изображения для объявлений","▧","#7c5cff","#f4efff"],
                ["/social","Социальные сети","Контент, публикации и работа с соцсетями","◉","#10a9a5","#ecfbf8"],
                ["/dashboard/analytics","Аналитика","Результаты, динамика и контроль эффективности","▥","#f59e0b","#fff8e8"],
              ].map(([href,title,description,icon,accent,soft]) => <Link key={String(href)} href={String(href)} className="boris-marketing-work-link" style={{"--work-accent":accent,"--work-soft":soft} as React.CSSProperties}>
                <span className="boris-marketing-work-icon">{icon}</span>
                <span className="boris-marketing-work-copy"><strong>{title}</strong><small>{description}</small></span>
                <span className="boris-marketing-work-arrow">→</span>
              </Link>)}
            </div>
          </>
        ) : null}
      </Page>
    </AppShell>
  );
}
