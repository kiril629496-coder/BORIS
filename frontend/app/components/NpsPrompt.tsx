"use client";

// Опрос удовлетворённости. Экран НЕ знает, пробный это период или регулярный
// отзыв — он просто рисует то, что вернул /api/nps/pending. Значит второй
// сценарий добавляется на бэкенде, без правки этого файла.
import React from "react";
import { usePathname } from "next/navigation";
import { useAccounts } from "../lib/AccountContext";

const BLUE = "var(--boris-blue)";
const HIDE = ["/", "/login", "/verify", "/onboarding", "/oferta", "/privacy", "/dashboard/client-success", "/client/report", "/dashboard/billing"];

type Q = { key: string; type: string; text: string; options?: string[] };

export default function NpsPrompt() {
  const pathname = usePathname();
  const acc = useAccounts();
  const [data, setData] = React.useState<any>(null);
  const [answers, setAnswers] = React.useState<Record<string, any>>({});
  const [sent, setSent] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const accountId = acc.selectedAccountId || "";

  const hidden = HIDE.some((b) => (b === "/" ? pathname === "/" : pathname.startsWith(b)));

  React.useEffect(() => {
    setData(null);
    setAnswers({});
    setSent(false);
    setBusy(false);
    setError("");
    if (hidden || !accountId || typeof window === "undefined") return;
    const t = localStorage.getItem("boris_token");
    if (!t || sessionStorage.getItem("boris_nps_skip")) return;
    const headers = { Authorization: "Bearer " + t };
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const loadDue = async () => {
      try {
        const pr = await fetch("/api/payments/status?account_id=" + encodeURIComponent(accountId), { headers, cache: "no-store" });
        const p = await pr.json().catch(() => null);
        const pay = p?.payment;
        const days = pay?.has_payment ? Number(pay.days_left) : null;
        if (cancelled || (days !== null && Number.isFinite(days) && days <= 0)) return;
        if (document.querySelector('[role="dialog"], textarea:focus, input:focus')) {
          timer = setTimeout(loadDue, 2500);
          return;
        }
        const nr = await fetch("/api/nps/pending?account_id=" + encodeURIComponent(accountId), { headers, cache: "no-store" });
        const d = await nr.json().catch(() => null);
        if (!cancelled && d?.ask) setData(d);
      } catch {}
    };
    void loadDue();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [accountId, hidden]);

  if (!data || hidden) return null;

  const qs: Q[] = data.questions || [];
  const scoreDone = answers.score !== undefined;

  const send = async () => {
    setBusy(true);
    setError("");
    try {
      const t = localStorage.getItem("boris_token");
      const r = await fetch("/api/nps/answer?account_id=" + encodeURIComponent(accountId), {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + t },
        body: JSON.stringify({ product: data.product, day: data.day, kind: data.kind || "trial", answers }),
      });
      const result = await r.json().catch(() => null);
      if (!r.ok || !result || result.status !== "ok" || result.saved !== true) {
        throw new Error("nps_not_saved");
      }
      setSent(true);
      setBusy(false);
      setTimeout(() => setData(null), 2200);
    } catch {
      setBusy(false);
      setError("Не удалось сохранить ответ. Попробуйте ещё раз — опрос не закроется, пока ответ не сохранён.");
    }
  };

  const close = () => {
    try {
      sessionStorage.setItem("boris_nps_skip", "1");
      const t = localStorage.getItem("boris_token");
      fetch("/api/nps/dismiss?account_id=" + encodeURIComponent(accountId), {
        method: "POST", headers: { Authorization: "Bearer " + t },
      }).catch(() => {});
    } catch {}
    setData(null);
  };

  const box: React.CSSProperties = {
    position: "fixed", right: 20, bottom: 96, width: 380, maxWidth: "calc(100vw - 40px)",
    background: "#fff", border: "1px solid var(--boris-border)", borderRadius: 16,
    boxShadow: "0 12px 40px rgba(16,24,40,0.16)", padding: 20, zIndex: 1100,
  };

  if (sent) {
    return (
      <div style={box} role="dialog" aria-label="Опрос BORIS">
        <div style={{ fontSize: 16, fontWeight: 700, color: "var(--boris-text)", marginBottom: 6 }}>Спасибо!</div>
        <div style={{ fontSize: 14, color: "var(--boris-text-2)" }}>Мы читаем каждый ответ — это помогает нам стать лучше.</div>
      </div>
    );
  }

  return (
    <div style={box} role="dialog" aria-label="Опрос BORIS">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: "var(--boris-text)" }}>Как вам {data.product_name}?</div>
        <button aria-label="Закрыть опрос" onClick={close} style={{ border: "none", background: "none", fontSize: 20, color: "var(--boris-text-3)", cursor: "pointer", lineHeight: 1 }}>×</button>
      </div>

      {qs.map((q) => (
        <div key={q.key} style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 14, color: "var(--boris-text-2)", marginBottom: 7 }}>{q.text}</div>

          {q.type === "score" && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              {Array.from({ length: 11 }, (_, i) => i).map((n) => {
                const on = answers.score === n;
                return (
                  <button key={n} onClick={() => setAnswers({ ...answers, score: n })}
                    style={{ width: 28, height: 30, borderRadius: 7, cursor: "pointer",
                      border: "1px solid " + (on ? BLUE : "var(--boris-border)"),
                      background: on ? BLUE : "#fff", color: on ? "#fff" : "var(--boris-text-2)",
                      fontSize: 13, fontWeight: 600 }}>{n}</button>
                );
              })}
            </div>
          )}

          {q.type === "text" && (
            <textarea value={answers[q.key] || ""} onChange={(e) => setAnswers({ ...answers, [q.key]: e.target.value })}
              rows={2} placeholder="Необязательно"
              style={{ width: "100%", padding: "8px 10px", borderRadius: 9, border: "1px solid var(--boris-border)",
                fontSize: 14, fontFamily: "inherit", resize: "vertical", boxSizing: "border-box" }} />
          )}

          {q.type === "choice" && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {(q.options || []).map((o) => {
                const on = answers[q.key] === o;
                return (
                  <button key={o} onClick={() => setAnswers({ ...answers, [q.key]: o })}
                    style={{ padding: "6px 12px", borderRadius: 8, cursor: "pointer", fontSize: 13,
                      border: "1px solid " + (on ? BLUE : "var(--boris-border)"),
                      background: on ? BLUE : "#fff", color: on ? "#fff" : "var(--boris-text-2)" }}>{o}</button>
                );
              })}
            </div>
          )}
        </div>
      ))}

      {error && <div role="alert" style={{ marginBottom: 10, padding: "9px 10px", borderRadius: 9, background: "#FEF3F2", color: "#B42318", fontSize: 13, lineHeight: 1.4 }}>{error}</div>}

      <button onClick={send} disabled={!scoreDone || busy}
        style={{ width: "100%", padding: "10px 0", borderRadius: 10, border: "none", fontSize: 14, fontWeight: 600,
          background: scoreDone && !busy ? BLUE : "var(--boris-border)", color: scoreDone && !busy ? "#fff" : "var(--boris-text-3)",
          cursor: scoreDone && !busy ? "pointer" : "default" }}>
        {busy ? "Отправляю…" : "Отправить"}
      </button>
    </div>
  );
}
