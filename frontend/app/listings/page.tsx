"use client";
import { useCallback, useEffect, useState } from "react";
import { Page, Card, Button, Badge, EmptyState, Alert, Input } from "../ui";
import AppShell from "../components/shell/AppShell";
import { color, font, space } from "../ui/tokens";
import Proposals from "./Proposals";
import MassEditor from "./MassEditor";

type Item = {
  id: number; "заголовок": string; "цена": number | null; "статус": string;
  "категория": string; "просмотры_7д": number; "обращения_7д": number;
  "конверсия_7д": number | null;
  "ставка": number | null;
  "почему": string; "совет": string;
};

function api(path: string) {
  const t = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return fetch(path, { headers: { Authorization: "Bearer " + t } });
}

const TABS = [
  { key: "all", label: "Все" },
  { key: "active", label: "Активные" },
  { key: "dead", label: "Без просмотров" },
  { key: "silent", label: "Просмотры без обращений" },
  { key: "working", label: "Приносят обращения" },
];

export default function ListingsPage() {
  const [data, setData] = useState<any>(null);
  const [tab, setTab] = useState("all");
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState("contacts");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [account, setAccount] = useState("");

  useEffect(() => {
    const acc = typeof window === "undefined" ? "" : localStorage.getItem("boris_currentAccount") || "";
    setAccount(acc);
  }, []);

  const load = useCallback(async () => {
    if (!account) { setLoading(false); return; }
    try {
      const r = await api("/api/avito/items_overview?account_id=" + encodeURIComponent(account) + "&days=7");
      if (r.ok) setData(await r.json()); else setErr("Не удалось загрузить объявления");
    } catch { setErr("Ошибка сети"); }
    setLoading(false);
  }, [account]);
  useEffect(() => { load(); }, [load]);

  const all: Item[] = (data && data.items) || [];
  const shown = all.filter((i) => {
    if (q && !i["заголовок"].toLowerCase().includes(q.toLowerCase())) return false;
    if (tab === "active") return i["статус"] === "active";
    if (tab === "dead") return i["статус"] === "active" && i["просмотры_7д"] === 0;
    if (tab === "silent") return i["просмотры_7д"] > 0 && i["обращения_7д"] === 0;
    if (tab === "working") return i["обращения_7д"] > 0;
    return true;
  }).sort((a, b) => {
    if (sortKey === "views") return b["просмотры_7д"] - a["просмотры_7д"];
    if (sortKey === "price") return (b["цена"] || 0) - (a["цена"] || 0);
    return b["обращения_7д"] - a["обращения_7д"];
  });

  if (loading) {
    return (
      <AppShell active="listings" title="Объявления">
        <Page />
      </AppShell>
    );
  }

  return (
    <AppShell
      active="listings"
      title="Объявления"
      subtitle={
        data && data["дата_снимка"]
          ? `${data["всего"]} позиций · данные за 7 дней на ${data["дата_снимка"]}`
          : "Данные за последние 7 дней"
      }
    >
      <Page>
        {err ? <Alert kind="danger">{err}</Alert> : null}
        <MassEditor account={account} />
        <Proposals />
        {!all.length ? (
          <EmptyState title="Объявлений пока нет"
            hint="Статистика собирается раз в сутки. Если аккаунт подключён недавно, данные появятся завтра." />
        ) : (
          <>
            <div data-boris-g33="listings" style={{ display: "flex", gap: space.md, flexWrap: "wrap",
                          marginBottom: space.lg }}>
              {[["Всего", data["всего"], color.heading],
                ["Приносят обращения", data["с_обращениями"], color.green],
                ["Почти нет просмотров", data["без_просмотров"], color.red],
                ["Активных", data["активных"], color.heading]].map((k: any) => (
                <Card key={k[0]} style={{ flex: "1 1 160px", minWidth: 150 }}>
                  <div style={{ ...font.kpi, color: k[2] }}>{k[1]}</div>
                  <div style={font.small}>{k[0]}</div>
                </Card>
              ))}
            </div>
            <div style={{ display: "flex", gap: space.sm, flexWrap: "wrap", marginBottom: space.md }}>
              {TABS.map((t) => (
                <Button key={t.key} kind={tab === t.key ? "secondary" : "ghost"}
                        onClick={() => setTab(t.key)}>{t.label}</Button>
              ))}
              <span style={{ ...font.small, alignSelf: "center", marginLeft: space.md }}>
                Сортировать:
              </span>
              {[["contacts", "по обращениям"], ["views", "по просмотрам"],
                ["price", "по цене"]].map((s) => (
                <Button key={s[0]} kind={sortKey === s[0] ? "secondary" : "ghost"}
                        onClick={() => setSortKey(s[0])}>{s[1]}</Button>
              ))}
              <span style={{ marginLeft: "auto", minWidth: 200 }}>
                <Input value={q} placeholder="Поиск по названию"
                       onChange={(e: any) => setQ(e.target.value)} />
              </span>
            </div>
            <Card>
              <div style={{ ...font.small, marginBottom: space.sm }}>
                Показано {shown.length} из {all.length}
              </div>
              {shown.map((i) => (
                <div key={i.id} style={{ display: "flex", gap: space.md, alignItems: "center",
                                         padding: "11px 0", borderTop: "1px solid " + color.line,
                                         flexWrap: "wrap" }}>
                  <div style={{ flex: 1, minWidth: 220 }}>
                    <div style={{ ...font.body, color: color.heading }}>{i["заголовок"]}</div>
                    <div style={font.small}>
                      {i["почему"] ? i["почему"] : i["категория"]}
                      {i["совет"] ? " \u2192 " + i["совет"] : ""}
                    </div>
                  </div>
                  <div style={{ ...font.body, color: color.heading, minWidth: 96 }}>
                    {i["цена"] ? (i["цена"] as number).toLocaleString("ru-RU") + " \u20BD" : "\u2014"}
                  </div>
                  <div style={{ minWidth: 92, ...font.small }}>
                    {i["просмотры_7д"]} просм.
                  </div>
                  <div style={{ minWidth: 96, ...font.small,
                                color: i["обращения_7д"] > 0 ? color.green : color.text }}>
                    {i["обращения_7д"]} обращ.
                  </div>
                  <div style={{ minWidth: 70 }}>
                    {i["статус"] === "active"
                      ? <Badge kind="success">активно</Badge>
                      : <Badge kind="neutral">{i["статус"] || "\u2014"}</Badge>}
                  </div>
                </div>
              ))}
              {!shown.length ? (
                <div style={{ ...font.small, padding: space.lg, textAlign: "center" }}>
                  Ничего не найдено
                </div>
              ) : null}
            </Card>
          </>
        )}
      </Page>
    </AppShell>
  );
}
