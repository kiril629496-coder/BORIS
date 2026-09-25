"use client";
import { useCallback, useEffect, useState } from "react";
import { Card, Button, Badge, Alert, Icon } from "../ui";
import { color, font, space, radius } from "../ui/tokens";

function tok() {
  return typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
}
function acc() {
  return typeof window === "undefined" ? "" : localStorage.getItem("boris_currentAccount") || "";
}

export default function Proposals() {
  const [data, setData] = useState<any>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    const a = acc();
    if (!a) return;
    try {
      const r = await fetch("/api/avito/ads_proposals?account_id=" + encodeURIComponent(a),
        { headers: { Authorization: "Bearer " + tok() } });
      if (r.ok) setData(await r.json());
    } catch { /* экран не должен падать из-за одной ручки */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  const act = async (path: string, run: string, word: string) => {
    setBusy(run); setNote("");
    try {
      const r = await fetch("/api/avito/" + path, {
        method: "POST",
        headers: { Authorization: "Bearer " + tok(), "Content-Type": "application/json" },
        body: JSON.stringify({ account_id: acc(), run_id: run }),
      });
      setNote(r.ok ? word : "Не удалось выполнить");
      await load();
    } catch { setNote("Ошибка сети"); }
    setBusy("");
  };

  const runs = (data && data["запуски"]) || [];
  const waiting = runs.filter((r: any) => r["ожидает"] > 0);
  const applied = runs.filter((r: any) => r["применено"] > 0);
  const total = waiting.reduce((s: number, r: any) => s + r["ожидает"], 0);
  if (!waiting.length && !applied.length) return null;

  return (
    <Card>
      <div style={{ display: "flex", alignItems: "center", gap: space.md, flexWrap: "wrap" }}>
        <span style={{ width: 34, height: 34, borderRadius: radius.sm, background: color.blueSoft,
                       display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon name="zap" size={18} tone="accent" />
        </span>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ ...font.h3 as React.CSSProperties, margin: 0 }}>
            {total ? `BORIS предлагает изменить ${total} заголовков` : "Изменения применены"}
          </div>
          <div style={font.small}>
            Меняется только заголовок · любое изменение можно вернуть
          </div>
        </div>
        {total ? (
          <Button kind="ghost" onClick={() => setOpen(!open)}>
            {open ? "Свернуть" : "Посмотреть"}
          </Button>
        ) : null}
        {applied.map((run: any) => (
          <Button key={run["запуск"]} kind="ghost" disabled={busy === run["запуск"]}
                  onClick={() => act("ads_rollback", run["запуск"], "Прежние заголовки возвращены")}>
            Вернуть как было
          </Button>
        ))}
      </div>

      {note ? <div style={{ marginTop: space.sm }}><Alert kind="info">{note}</Alert></div> : null}

      {open && waiting.map((run: any) => (
        <div key={run["запуск"]} style={{ marginTop: space.md, paddingTop: space.md,
                                          borderTop: "1px solid " + color.line }}>
          {run["предложения"].filter((p: any) => p["статус"] === "ожидает").map((p: any) => (
            <div key={p.id} style={{ display: "flex", gap: space.md, alignItems: "baseline",
                                     padding: "7px 0", flexWrap: "wrap" }}>
              <span style={{ ...font.small, textDecoration: "line-through", minWidth: 190 }}>
                {p["было"]}
              </span>
              <span style={{ ...font.body, color: color.heading, flex: 1, minWidth: 220 }}>
                {p["стало"]}
              </span>
              <span style={font.small}>{(p["почему"] || [])[0] || ""}</span>
            </div>
          ))}
          <div style={{ marginTop: space.sm }}>
            <Button disabled={busy === run["запуск"]}
                    onClick={() => act("ads_publish", run["запуск"], "Заголовки обновлены")}>
              Применить {run["ожидает"]}
            </Button>
          </div>
        </div>
      ))}
    </Card>
  );
}
