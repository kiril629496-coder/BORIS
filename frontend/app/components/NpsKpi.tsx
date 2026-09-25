"use client";

// Индекс NPS считается на бэкенде (/api/nps/summary): промоутеры (9-10)
// минус критики (0-6), делённые на число ответов. Компонент сам ходит за
// цифрой, чтобы не трогать состояние страницы, в которую его вставляют.
import React from "react";
import { Kpi } from "../ui";

export default function NpsKpi() {
  const [nps, setNps] = React.useState<number | null>(null);
  const [count, setCount] = React.useState(0);

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
      })
      .catch(() => {});
  }, []);

  if (!count) return <Kpi value="—" label="индекс NPS · нет ответов" />;
  const kind = nps === null ? "neutral" : nps >= 50 ? "success" : nps >= 0 ? "info" : "danger";
  return <Kpi value={nps === null ? "—" : String(nps)} label={"индекс NPS · " + count + " отв."} kind={kind as any} />;
}
