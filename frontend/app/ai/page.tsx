"use client";

import { useEffect, useState } from "react";
import {
  Page, PageHeader, Card, Button, Choice, Badge, Alert, EmptyState,
} from "../ui";
import { Shell } from "../ui/Sidebar";
import { color, space, font } from "../ui/tokens";

type Acc = {
  account_id: string; name: string; avito_user_id: string; bound: boolean;
  memory_shared: boolean; group_key: string; memory_mode: string;
};
type Sum = {
  product_title: string; accounts: { account_id: string; name: string }[];
  memory_shared: boolean; facts: number; confirmed: number;
  conflicts: number; usable: number;
};
type Lic = { qty: number; active: boolean; paid_until: string; unlimited: boolean };

const PRODUCTS = [
  { key: "mop", title: "AI-менеджер по продажам", hint: "отвечает клиентам в переписке" },
  { key: "rop", title: "AI-руководитель отдела продаж", hint: "контролирует качество и обучает" },
];
const MODES: [string, string][] = [
  ["one", "Один аккаунт"],
  ["selected", "Несколько выбранных"],
  ["all", "Все аккаунты клиента"],
];

function api(path: string, init?: RequestInit) {
  const token =
    typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return fetch(path, {
    ...(init || {}),
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + token,
      ...((init && (init.headers as Record<string, string>)) || {}),
    },
  });
}

export default function AiSetupPage() {
  const [product, setProduct] = useState("rop");
  // Режим ответов МОПа по каждому аккаунту: аренда и продажа
  // могут работать по-разному.
  const [mopModes, setMopModes] = useState<any>({});
  const [accs, setAccs] = useState<Acc[]>([]);
  const [sum, setSum] = useState<Sum | null>(null);
  const [lic, setLic] = useState<Record<string, Lic>>({});
  const [owner, setOwner] = useState(false);
  const [mode, setMode] = useState("selected");
  const [picked, setPicked] = useState<string[]>([]);
  const [shared, setShared] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState(false);

  useEffect(() => { load(product); }, [product]);
  useEffect(() => {
    if (product !== "mop" || !sum || !sum.accounts) { setMopModes({}); return; }
    Promise.all(sum.accounts.map((a: any) =>
      api("/api/ai/mop_mode?account_id=" + encodeURIComponent(a.account_id))
        .then((d: any) => [a.account_id, d]).catch(() => [a.account_id, null])))
      .then((pairs: any[]) => {
        const m: any = {};
        pairs.forEach(([k, v]) => { if (v && v.status === "ok") m[k] = v; });
        setMopModes(m);
      });
  }, [product, sum]);

  async function load(p: string) {
    try {
      const [a, s, l] = await Promise.all([
        api("/api/ai/accounts?product=" + p),
        api("/api/ai/summary?product=" + p),
        api("/api/ai/licenses"),
      ]);
      if (a.ok) {
        const d = await a.json();
        const list: Acc[] = d.accounts || [];
        setAccs(list);
        const bound = list.filter((x) => x.bound).map((x) => x.account_id);
        setPicked(bound);
        if (bound.length) setShared(list.some((x) => x.bound && x.memory_shared));
        if (bound.length === 1) setMode("one");
        else if (bound.length && bound.length === list.length) setMode("all");
        else if (bound.length) setMode("selected");
      }
      if (s.ok) setSum(await s.json());
      if (l.ok) { const d = await l.json(); setLic(d.licenses || {}); setOwner(!!d.owner); }
    } catch { setErr(true); setNote("Не удалось загрузить аккаунты"); }
  }

  async function save() {
    setBusy(true); setNote(""); setErr(false);
    try {
      const r = await api("/api/ai/bind", {
        method: "POST",
        body: JSON.stringify({
          product, mode, account_ids: mode === "all" ? [] : picked, memory_shared: shared,
        }),
      });
      const d = await r.json().catch(() => ({}));
      setErr(!r.ok);
      setNote(d.message || d.detail || "Готово");
      await load(product);
    } catch { setErr(true); setNote("Ошибка сети"); }
    setBusy(false);
  }

  const cur = PRODUCTS.find((p) => p.key === product);
  const myLic = lic[product];
  const limit = owner ? "без ограничений" :
    myLic && myLic.active ? `оплачено ${myLic.qty} шт. до ${myLic.paid_until}` : "не оплачено";

  return (
    <Shell activeKey="aisetup">
    <Page>
      <PageHeader
        title="Подключение AI-сотрудников"
        subtitle="Выберите, какие аккаунты Авито обслуживает AI и общая ли у них память."
        actions={PRODUCTS.map((p) => (
          <Button key={p.key} kind={product === p.key ? "secondary" : "ghost"}
                  onClick={() => setProduct(p.key)}>{p.title}</Button>
        ))}
      />

      {note ? <Alert kind={err ? "danger" : "success"}>{note}</Alert> : null}

      <div style={{ display: "flex", gap: space.lg, alignItems: "flex-start", flexWrap: "wrap" }}>
        <Card style={{ flex: "1 1 560px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: space.md }}>
            <div style={{ flex: 1 }}>
              <div style={font.h3 as React.CSSProperties}>{cur ? cur.title : ""}</div>
              <div style={{ ...font.small, marginTop: 3 }}>{cur ? cur.hint : ""}</div>
            </div>
            <Badge kind={owner ? "info" : myLic && myLic.active ? "success" : "warning"}>
              {limit}
            </Badge>
          </div>

          <div style={{ ...font.bodyStrong, marginTop: space.xl, marginBottom: space.md }}>
            Какие аккаунты обслуживать
          </div>
          {MODES.map(([k, label]) => (
            <Choice key={k} type="radio" checked={mode === k}
                    onToggle={() => setMode(k)} title={label} />
          ))}

          {mode !== "all" ? (
            <div style={{ marginTop: space.lg }}>
              <div style={{ ...font.bodyStrong, marginBottom: space.md }}>
                Аккаунты ({picked.length} из {accs.length})
              </div>
              {accs.map((a) => (
                <Choice key={a.account_id} type={mode === "one" ? "radio" : "checkbox"}
                        checked={picked.includes(a.account_id)}
                        onToggle={() => mode === "one"
                          ? setPicked([a.account_id])
                          : setPicked((p) => p.includes(a.account_id)
                              ? p.filter((x) => x !== a.account_id)
                              : [...p, a.account_id])}
                        title={a.name}
                        hint={a.avito_user_id ? "ID " + a.avito_user_id : a.account_id}
                        right={<Badge kind={a.memory_mode === "strict" ? "success" : "neutral"}>
                          память {a.memory_mode}
                        </Badge>} />
              ))}
            </div>
          ) : null}

          <div style={{ marginTop: space.lg }}>
            <Choice checked={shared} onToggle={() => setShared(!shared)}
                    title="Общая память для выбранных аккаунтов"
                    hint="Знание из одного кабинета доступно во втором. Источник факта сохраняется вместе с аккаунтом." />
          </div>

          <Button onClick={save} disabled={busy || (mode !== "all" && !picked.length)}
                  style={{ marginTop: space.lg }}>
            Сохранить подключение
          </Button>
        </Card>

        <div style={{ flex: "0 1 360px" }}>
          {sum && sum.accounts.length ? (
            <Card>
              <div style={font.h3 as React.CSSProperties}>{sum.product_title}</div>
              <div style={{ marginTop: space.md }}>
                {sum.accounts.map((a) => {
                  const m = mopModes[a.account_id];
                  return (
                  <div key={a.account_id} style={{ marginBottom: space.sm }}>
                    <div style={{ ...font.body, color: color.heading }}>☑ {a.name}</div>
                    {product === "mop" && m ? (
                      <div style={{ ...font.small, marginTop: 4 }}>
                        {m.can_change ? (
                          <span>
                            <button onClick={async () => {
                              await api("/api/ai/mop_mode", { method: "POST",
                                body: JSON.stringify({ account_id: a.account_id, contour: "legacy" }) });
                              setMopModes({ ...mopModes, [a.account_id]: { ...m, contour: "legacy" } });
                            }} style={{ border: "none", cursor: "pointer", marginRight: 6,
                                        borderRadius: 6, padding: "3px 9px", fontSize: 12,
                                        background: m.contour === "legacy" ? "#2F6FED" : "#EEF1F6",
                                        color: m.contour === "legacy" ? "#fff" : "#475467" }}>
                              Отвечать сразу
                            </button>
                            <button onClick={async () => {
                              await api("/api/ai/mop_mode", { method: "POST",
                                body: JSON.stringify({ account_id: a.account_id, contour: "new" }) });
                              setMopModes({ ...mopModes, [a.account_id]: { ...m, contour: "new" } });
                            }} style={{ border: "none", cursor: "pointer",
                                        borderRadius: 6, padding: "3px 9px", fontSize: 12,
                                        background: m.contour === "new" ? "#2F6FED" : "#EEF1F6",
                                        color: m.contour === "new" ? "#fff" : "#475467" }}>
                              Показывать перед отправкой
                            </button>
                          </span>
                        ) : (<span>{m.title}</span>)}
                      </div>
                    ) : null}
                  </div>
                  );
                })}
              </div>
              <div style={{ marginTop: space.sm }}>
                <Badge kind={sum.memory_shared ? "success" : "neutral"}>
                  {sum.memory_shared ? "Память общая" : "Память раздельная"}
                </Badge>
              </div>
              <div style={{ height: 1, background: color.line, margin: `${space.lg}px 0` }} />
              <Row k="Фактов" v={sum.facts} />
              <Row k="Подтверждено" v={sum.confirmed} c={color.green} />
              <Row k="Конфликтов" v={sum.conflicts} c={sum.conflicts ? color.red : undefined} />
              <Row k="Используется AI" v={sum.usable} c={color.blue} />
            </Card>
          ) : (
            <EmptyState title="AI пока не подключён"
                        hint="Выберите аккаунты слева и сохраните подключение." />
          )}
        </div>
      </div>
    </Page>
    </Shell>
  );
}

function Row({ k, v, c }: { k: string; v: number; c?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0" }}>
      <span style={font.body as React.CSSProperties}>{k}</span>
      <span style={{ fontSize: 15, fontWeight: 700, color: c || color.heading }}>{v}</span>
    </div>
  );
}
