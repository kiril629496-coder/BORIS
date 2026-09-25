"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useAccounts } from "../../lib/AccountContext";
import { apiGet, apiPost } from "../../lib/api";
import { Alert, Button, Card, EmptyState, IconChip, Page } from "../../ui";
import { color, font, space } from "../../ui/tokens";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

type Item = {
  id: string | number;
  "заголовок"?: string;
  "статус"?: string;
  "просмотры_7д"?: number;
  "обращения_7д"?: number;
  "конверсия_7д"?: number | null;
  "почему"?: string;
  "совет"?: string;
  recommendation_group?: string;
  recommendation_actionable?: boolean;
  recommendation_measurement?: { status?: string; action?: string; old_bid_rub?: number; new_bid_rub?: number; effect?: string; required_days?: number } | null;
};

export default function AnalyticsPage() {
  const { selectedAccountId, selectedAccount, loading: accountsLoading } = useAccounts();
  const [overview, setOverview] = useState<any>(null);
  const [settings, setSettings] = useState<any>(null);
  const [kpi, setKpi] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedRecommendations, setSelectedRecommendations] = useState<Array<string | number>>([]);
  const [applying, setApplying] = useState<Array<string | number>>([]);
  const [applyMessage, setApplyMessage] = useState("");

  const load = useCallback(async () => {
    if (!selectedAccountId) {
      setOverview(null);
      setSettings(null);
      setKpi(null);
      return;
    }

    setLoading(true);
    setError("");
    const id = encodeURIComponent(selectedAccountId);

    try {
      const [itemsData, settingsData, kpiData] = await Promise.all([
        apiGet(`/api/avito/items_overview?account_id=${id}&days=7`),
        apiGet(`/api/avito/kpi_settings?account_id=${id}`),
        apiGet(`/api/avito/kpi_check?account_id=${id}`),
      ]);

      const failed = [itemsData, settingsData, kpiData].find((x: any) => x?.status === "error");
      if (failed) {
        setError(failed.message || "Часть аналитики временно недоступна");
      }

      setOverview(itemsData);
      setSettings(settingsData?.settings || null);
      setKpi(kpiData);
    } catch {
      setError("Не удалось получить реальные данные аналитики");
    } finally {
      setLoading(false);
    }
  }, [selectedAccountId]);

  useEffect(() => {
    setSelectedRecommendations([]);
    setApplying([]);
    setApplyMessage("");
    void load();
  }, [load]);

  const items: Item[] = Array.isArray(overview?.items) ? overview.items : [];
  const totals = useMemo(() => {
    return items.reduce(
      (acc, item) => {
        acc.views += Number(item["просмотры_7д"] || 0);
        acc.contacts += Number(item["обращения_7д"] || 0);
        return acc;
      },
      { views: 0, contacts: 0 },
    );
  }, [items]);
  const conversion = totals.views > 0 ? (totals.contacts / totals.views) * 100 : null;
  const topItems = useMemo(
    () => [...items]
      .filter((item) => Number(item["обращения_7д"] || 0) > 0)
      .sort((a, b) => Number(b["обращения_7д"] || 0) - Number(a["обращения_7д"] || 0))
      .slice(0, 5),
    [items],
  );
  const actionableTopItems = useMemo(() => topItems.filter((item) => Boolean(item["совет"] && item.recommendation_actionable)), [topItems]);
  const allActionableSelected = actionableTopItems.length > 0 && actionableTopItems.every((item) => selectedRecommendations.includes(item.id));

  const applyRecommendations = useCallback(async (ids: Array<string | number>) => {
    if (!selectedAccountId || !ids.length) return;
    setApplying(ids);
    setApplyMessage("");
    try {
      const result = await apiPost("/api/avito/ai_marketing_apply_recommendations", {
        account_id: selectedAccountId,
        item_ids: ids.map((id) => Number(id)),
      });
      const applied = Number(result?.applied || 0);
      const blocked = Number(result?.blocked || 0);
      const errors = Number(result?.errors || 0);
      setApplyMessage(`ИИ-маркетолог: применено ${applied}${blocked ? ` · заблокировано защитой ${blocked}` : ""}${errors ? ` · ошибок ${errors}` : ""}`);
      setSelectedRecommendations((prev) => prev.filter((id) => !ids.includes(id)));
      await load();
    } catch {
      setApplyMessage("Не удалось передать команду ИИ-маркетологу");
    } finally {
      setApplying([]);
    }
  }, [selectedAccountId, load]);

  return (
    <Page className="boris-analytics-reference-page">
      <div className="boris-analytics-reference-head">
        <div>
          <div className="boris-analytics-kicker">Реальные данные · 7 дней</div>
          <h1>Аналитика</h1>
          <p>{selectedAccount ? selectedAccount.name : "Выбранный аккаунт"} · результат рекламы и план/факт</p>
        </div>
        <Button onClick={() => void load()} disabled={loading || !selectedAccountId}>{loading ? "Обновляем…" : "Обновить"}</Button>
      </div>

      {error ? <Alert kind="danger">{error}</Alert> : null}
      {!accountsLoading && !selectedAccountId ? <EmptyState title="Не выбран аккаунт" hint="Выберите аккаунт в верхней панели, чтобы увидеть его реальные показатели." /> : null}

      {selectedAccountId && !error ? (
        <>
          {overview?.automation ? <Alert kind={overview.automation.status === "ok" && overview.automation.apply_enabled && overview.automation.mandate_active ? "info" : "warning"}>
            <b>{overview.automation.apply_enabled && overview.automation.mandate_active ? "Автопилот BORIS активен" : "Автопилот BORIS не исполняет действия"}</b>
            {` · последний цикл ${overview.automation.finished_at ? new Date(overview.automation.finished_at).toLocaleTimeString("ru-RU", {hour:"2-digit",minute:"2-digit"}) : "—"}`}
            {overview.automation.mode ? ` · ${humanizeBusinessText(overview.automation.mode,"автоматический режим")}` : ""}
            {overview.automation.applied ? ` · применено ${overview.automation.applied}` : ""}
            {overview.automation.blocked ? ` · защитой остановлено ${overview.automation.blocked}` : ""}
          </Alert> : null}
          <div className="boris-analytics-kpi-strip boris-analytics-kpi-strip-three">
            <Link href="/dashboard/campaign" className="boris-analytics-kpi-card boris-analytics-kpi-card-large" data-tone="blue">
              <IconChip name="list" kind="info" size={56} />
              <div className="boris-analytics-kpi-copy"><span>Реклама</span><strong>{overview?.["активных"] ?? "—"} активных</strong><small>{totals.views.toLocaleString("ru-RU")} просмотров · {overview?.["без_просмотров"] ?? 0} без просмотров</small></div><span className="boris-analytics-kpi-arrow" aria-hidden="true">›</span>
            </Link>
            <Link href="/messages" className="boris-analytics-kpi-card boris-analytics-kpi-card-large" data-tone="green">
              <IconChip name="messages" kind="success" size={56} />
              <div className="boris-analytics-kpi-copy"><span>Обращения</span><strong>{totals.contacts.toLocaleString("ru-RU")} за 7 дней</strong><small>Конверсия {conversion == null ? "—" : `${conversion.toFixed(1)}%`} · открыть диалоги</small></div><span className="boris-analytics-kpi-arrow" aria-hidden="true">›</span>
            </Link>
            <Link href="/dashboard/marketing" className="boris-analytics-kpi-card boris-analytics-kpi-card-large" data-tone="violet">
              <IconChip name="chart" kind="info" size={56} />
              <div className="boris-analytics-kpi-copy"><span>Эффективность</span><strong>{kpi?.cost_per_lead_today != null ? `${kpi.cost_per_lead_today} ₽ CPL` : "CPL —"}</strong><small>{kpi?.max_cost_per_lead_rub != null ? `Цель ≤ ${kpi.max_cost_per_lead_rub} ₽` : "Цель CPL не задана"} · ИИ-маркетолог</small></div><span className="boris-analytics-kpi-arrow" aria-hidden="true">›</span>
            </Link>
          </div>

          <div className="boris-analytics-main-grid">
            <Card className="boris-analytics-plan-card">
              <div className="boris-analytics-card-head"><div><span>Сегодня</span><h2>План / факт</h2></div><Link href="/dashboard/marketing">Настроить цель</Link></div>
              {kpi?.status === "no_goal" ? <Alert kind="info">{kpi.message || "Цель не задана."}</Alert> : (
                <div className="boris-analytics-plan-grid">
                  <div><span>Лиды · план</span><strong>{kpi?.target_leads_per_day ?? settings?.target_leads_per_day ?? "—"}</strong></div>
                  <div><span>Лиды · факт</span><strong>{kpi?.contacts_today ?? "—"}</strong></div>
                  <div><span>Цена лида · цель</span><strong>{kpi?.max_cost_per_lead_rub != null ? `${kpi.max_cost_per_lead_rub} ₽` : settings?.max_cost_per_lead_rub != null ? `${settings.max_cost_per_lead_rub} ₽` : "—"}</strong></div>
                  <div><span>Цена лида · факт</span><strong>{kpi?.cost_per_lead_today != null ? `${kpi.cost_per_lead_today} ₽` : "—"}</strong></div>
                </div>
              )}
              {kpi?.recommendation ? <div className="boris-analytics-recommendation"><b>Рекомендация BORIS</b><span>{kpi.recommendation}</span></div> : null}
            </Card>

            <Card className="boris-analytics-funnel-card">
              <div className="boris-analytics-card-head"><div><span>Воронка</span><h2>Просмотры → обращения</h2></div></div>
              <div className="boris-analytics-funnel">
                <div><span>Просмотры</span><strong>{totals.views.toLocaleString("ru-RU")}</strong><i style={{width:"100%"}} /></div>
                <div><span>Обращения</span><strong>{totals.contacts.toLocaleString("ru-RU")}</strong><i style={{width:`${totals.views ? Math.max(8, Math.min(100, (totals.contacts / totals.views) * 100)) : 0}%`}} /></div>
                <div className="boris-analytics-conversion-value"><strong>{conversion == null ? "—" : `${conversion.toFixed(1)}%`}</strong><span>конверсия</span></div>
              </div>
            </Card>
          </div>

          <Card className="boris-analytics-top-card">
            <div className="boris-analytics-card-head"><div><span>Результат объявлений</span><h2>Лучшие по обращениям</h2></div><Link href="/listings">Все объявления</Link></div>
            {actionableTopItems.length ? <div style={{display:"flex",gap:10,alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",margin:"10px 0 12px"}}>
              <label style={{display:"inline-flex",alignItems:"center",gap:8,fontSize:13,fontWeight:700,cursor:"pointer"}}><input type="checkbox" checked={allActionableSelected} onChange={() => setSelectedRecommendations(allActionableSelected ? [] : actionableTopItems.map((item) => item.id))} /> Выбрать все рекомендации</label>
              <Button onClick={() => void applyRecommendations(selectedRecommendations)} disabled={!selectedRecommendations.length || applying.length > 0}>{applying.length > 1 ? "Передаём ИИ-маркетологу…" : `Применить выбранные (${selectedRecommendations.length})`}</Button>
            </div> : null}
            {applyMessage ? <Alert kind="info">{applyMessage}</Alert> : null}
            {overview?.status === "empty" ? <EmptyState title="Статистика ещё не собрана" hint={overview.message || "Данные появятся после первого суточного снимка Avito."} /> : topItems.length ? (
              <div className="boris-analytics-table">
                <div className="boris-analytics-table-head" style={{gridTemplateColumns:"minmax(0,1fr) 92px 92px 92px 150px"}}><span>Объявление</span><span>Просмотры</span><span>Обращения</span><span>Конверсия</span><span>Действие</span></div>
                {topItems.map((item) => {
                  const actionable = Boolean(item["совет"] && item.recommendation_actionable);
                  const checked = selectedRecommendations.includes(item.id);
                  const busy = applying.includes(item.id);
                  return <div className="boris-analytics-table-row" style={{gridTemplateColumns:"minmax(0,1fr) 92px 92px 92px 150px"}} key={String(item.id)}>
                    <div><strong>{item["заголовок"] || `Объявление ${item.id}`}</strong>{item["совет"] || item["почему"] ? <small>{item["совет"] || item["почему"]}</small> : null}</div>
                    <span>{Number(item["просмотры_7д"] || 0)}</span>
                    <span className="boris-analytics-positive">{Number(item["обращения_7д"] || 0)}</span>
                    <span>{item["конверсия_7д"] == null ? "—" : `${item["конверсия_7д"]}%`}</span>
                    <span style={{display:"flex",alignItems:"center",gap:8,justifyContent:"flex-end"}}>{actionable ? <><input aria-label={`Выбрать рекомендацию ${item.id}`} type="checkbox" checked={checked} onChange={() => setSelectedRecommendations((prev) => checked ? prev.filter((id) => id !== item.id) : [...prev, item.id])} /><Button onClick={() => void applyRecommendations([item.id])} disabled={busy || applying.length > 0}>{busy ? "Применяем…" : "Применить"}</Button></> : item.recommendation_measurement?.status === "waiting_measurement" ? <small style={{color:color.muted,fontWeight:700}}>Измеряем результат · {item.recommendation_measurement.old_bid_rub ?? "—"} → {item.recommendation_measurement.new_bid_rub ?? "—"} ₽</small> : item.recommendation_measurement?.status === "measured" ? <small style={{color:color.muted,fontWeight:700}}>Замер завершён · {item.recommendation_measurement.effect || "результат сохранён"}</small> : <small style={{color:color.muted}}>Нет действия</small>}</span>
                  </div>;
                })}
              </div>
            ) : <div className="boris-analytics-empty-line">За выбранный период объявлений с обращениями пока нет.</div>}
          </Card>

          <div className="boris-analytics-footer-links"><Link href="/listings">Объявления →</Link><Link href="/rop">Продажи и звонки →</Link><Link href="/dashboard/crm">Клиенты и сделки →</Link></div>
        </>
      ) : null}
    </Page>
  );
}
