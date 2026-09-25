"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import AppShell from "@/app/components/shell/AppShell";
import { useAccounts } from "@/app/lib/AccountContext";
import { Alert, Button, Card, EmptyState, Page } from "@/app/ui";
import { color, font, space } from "@/app/ui/tokens";

function authHeaders(extra: Record<string, string> = {}) {
  const token = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return { ...extra, Authorization: `Bearer ${token}` };
}

export default function ImportSitePage() {
  const { selectedAccountId, selectedAccount, loading: accountsLoading } = useAccounts();
  const [urls, setUrls] = useState("");
  const [limit, setLimit] = useState(50);
  const [products, setProducts] = useState<any[]>([]);
  const [sourceUrls, setSourceUrls] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopTimer = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
  }, []);

  const load = useCallback(async () => {
    if (!selectedAccountId) return;
    setLoading(true); setError("");
    try {
      const res = await fetch(`/api/parser/parsed_products?account_id=${encodeURIComponent(selectedAccountId)}`, { headers: authHeaders(), cache: "no-store" });
      const data = await res.json();
      if (!res.ok || data?.status === "error") throw new Error(data?.message || `HTTP ${res.status}`);
      setProducts(Array.isArray(data?.products) ? data.products : []);
      setSourceUrls(Array.isArray(data?.source_urls) ? data.source_urls : data?.source_url ? [data.source_url] : []);
    } catch {
      setError("Не удалось получить ранее выгруженные товары");
    } finally { setLoading(false); }
  }, [selectedAccountId]);

  useEffect(() => { void load(); return stopTimer; }, [load, stopTimer]);

  async function start() {
    if (!selectedAccountId) return;
    const list = urls.split("\n").map((item) => item.trim()).filter(Boolean);
    if (!list.length) return;
    stopTimer(); setRunning(true); setError(""); setProgress("Создаём задачу выгрузки…");
    try {
      const res = await fetch("/api/tasks/create", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ account_id: selectedAccountId, task_type: "parse_site", payload: { urls: list, account_id: selectedAccountId, limit } }),
      });
      const data = await res.json();
      if (!res.ok || data?.status !== "ok" || !data?.task_id) throw new Error(data?.message || "TASK_CREATE_FAILED");
      setUrls("");
      const taskId = data.task_id;
      setProgress(`Задача ${taskId} запущена`);
      timer.current = setInterval(async () => {
        try {
          const statusRes = await fetch(`/api/tasks/status/${encodeURIComponent(String(taskId))}`, { headers: authHeaders(), cache: "no-store" });
          const status = await statusRes.json();
          if (status?.result) {
            const result = typeof status.result === "string" ? JSON.parse(status.result) : status.result;
            if (Array.isArray(result?.results)) setProducts(result.results);
            if (result?.total) setProgress(`Обработано ${result.done || 0} из ${result.total}`);
          }
          if (status?.status === "done" || status?.status === "error") {
            stopTimer(); setRunning(false);
            if (status.status === "error") setError(status.error_message || "Выгрузка завершилась с ошибкой");
            else setProgress("Выгрузка завершена");
            await load();
          }
        } catch {
          stopTimer(); setRunning(false); setError("Не удалось получить статус задачи выгрузки");
        }
      }, 3000);
    } catch {
      setRunning(false); setError("Не удалось запустить выгрузку с сайта");
    }
  }

  return (
    <AppShell active="parser" title="Выгрузка с сайта" subtitle="Импорт товаров через существующий parser/task pipeline">
      <Page>
        {error ? <Alert kind="danger">{error}</Alert> : null}
        {!accountsLoading && !selectedAccountId ? <EmptyState title="Не выбран аккаунт" hint="Выберите аккаунт в верхней панели." /> : null}
        {selectedAccountId ? <>
          <Card>
            <div style={{ ...font.small, color: color.muted }}>{selectedAccount?.name || selectedAccountId}</div>
            <h2 style={{ ...font.h2, margin: "6px 0 8px" }}>Добавить товары с сайта</h2>
            <div style={{ ...font.body, color: color.muted, marginBottom: space.md }}>Одна ссылка на строку. BORIS автоматически обработает каждую страницу.</div>
            <textarea rows={5} value={urls} onChange={(e) => setUrls(e.target.value)} placeholder="https://example.ru/catalog/..." style={{ width: "100%", boxSizing: "border-box", resize: "vertical" }} />
            <div style={{ display: "flex", alignItems: "center", gap: space.sm, flexWrap: "wrap", marginTop: space.sm }}>
              <label style={font.small}>Лимит <input type="number" min={1} max={500} value={Number(limit) === 0 ? "" : limit} onChange={(e) => setLimit(Number(e.target.value))} style={{ width: 90 }} /></label>
              <Button onClick={start} disabled={running || !urls.trim()}>{running ? "Выгружаем…" : "Начать выгрузку"}</Button>
              {progress ? <span style={{ ...font.small, color: color.muted }}>{progress}</span> : null}
            </div>
          </Card>
          <div style={{ height: space.lg }} />
          <Card>
            <div style={{ display: "flex", justifyContent: "space-between", gap: space.md, flexWrap: "wrap", alignItems: "center" }}>
              <h2 style={{ ...font.h2, margin: 0 }}>Полученные товары</h2>
              <Button kind="ghost" onClick={() => void load()} disabled={loading}>{loading ? "Обновляем…" : "Обновить"}</Button>
            </div>
            {sourceUrls.length ? <div style={{ ...font.small, color: color.muted, marginTop: space.sm }}>Источники: {sourceUrls.join(" · ")}</div> : null}
            {products.length ? <div style={{ marginTop: space.md }}>{products.slice(0, 50).map((item, index) => (
              <div key={String(item.id ?? item.url ?? index)} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: space.md, padding: "10px 0", borderTop: index ? `1px solid ${color.line}` : "none" }}>
                <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{item.title || item.name || item.url || `Товар ${index + 1}`}</span>
                <span style={{ ...font.small, color: color.muted }}>{item.price != null ? String(item.price) : ""}</span>
              </div>
            ))}</div> : <EmptyState title="Выгруженных товаров пока нет" hint="После успешной задачи parse_site товары появятся здесь." />}
            <div style={{ marginTop: space.md }}><Link href="/dashboard/campaign/catalog">Открыть ассортимент →</Link></div>
          </Card>
        </> : null}
      </Page>
    </AppShell>
  );
}
