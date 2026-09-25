"use client";

// Индекс NPS на Главной. Считает бэкенд (/api/nps/summary): промоутеры (9-10)
// минус критики (0-6). Своя вёрстка, потому что Главная на CSS-модулях
// и компоненты из общего набора там смотрятся чужеродно.
import React from "react";

export default function NpsCard() {
  const [nps, setNps] = React.useState<number | null>(null);
  const [count, setCount] = React.useState(0);
  const [ready, setReady] = React.useState(false);

  React.useEffect(() => {
    const t = typeof window === "undefined" ? null : localStorage.getItem("boris_token");
    if (!t) return;
    fetch("/api/nps/summary", { headers: { Authorization: "Bearer " + t } })
      .then((r) => r.json())
      .then((d) => {
        if (d && d.status === "ok") {
          setNps(typeof d.nps === "number" ? d.nps : null);
          setCount(d.answers_count || 0);
        }
        setReady(true);
      })
      .catch(() => setReady(true));
  }, []);

  if (!ready) return null;

  const color = nps === null ? "var(--boris-text-2)" : nps >= 50 ? "#12B76A" : nps >= 0 ? "var(--boris-blue)" : "#B42318";

  return (
    <div style={{
      background: "#fff", border: "1px solid var(--boris-border)", borderRadius: 14,
      padding: "16px 20px", marginBottom: 16,
      display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap",
    }}>
      <div style={{ fontSize: 34, fontWeight: 700, color, lineHeight: 1 }}>
        {count && nps !== null ? nps : "—"}
      </div>
      <div style={{ flex: 1, minWidth: 200 }}>
        <div style={{ fontSize: 15, fontWeight: 600, color: "var(--boris-text)" }}>Индекс удовлетворённости</div>
        <div style={{ fontSize: 13, color: "var(--boris-text-2)", marginTop: 2 }}>
          {count
            ? "Промоутеры минус критики · учтено ответов: " + count
            : "Пока никто не оценил работу — опрос появится после нескольких дней работы"}
        </div>
      </div>
    </div>
  );
}
