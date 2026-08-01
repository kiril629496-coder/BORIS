"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { getToken, openRoute } from "../lib/api";
import { Page, PageHeader, Card, Button, Input, Alert, Icon, Kpi, KpiRow } from "../ui";
import { SplitLayout, Pane, ListItem, Bubble, Composer } from "../ui/chat";
import { color, space, font, radius, avatarPalette } from "../ui/tokens";

const POLL = 5000;
const DOT = avatarPalette;
const STC: Record<string, string> = {
  connected: color.green, disabled: color.muted, subscription_expired: color.muted,
  invalid_credentials: color.red, permission_error: color.red, error: color.red,
  api_unavailable: color.orange, requires_reconnect: color.orange, checking: color.orange,
  connecting: color.orange, paid_empty: color.lineStrong, empty: color.lineStrong,
};

type Totals = { accounts: number; new_msgs: number; unanswered: number; answered_today: number };
type Acc = { account_id: string; name: string; phone: string; status: string; unread: number };
type Dlg = { account_id: string; account_name: string; avito_chat_id: string; client_name: string;
  item_title: string; item_url: string; last_text: string; last_at: string;
  unread: number; answered: boolean };
type Msg = { direction: "in" | "out"; text: string; at: string; is_new?: boolean;
  msg_type?: string; content_type?: string; media_ref?: string; voice_url?: string };
type Thread = { account_id: string; account_name: string; avito_chat_id: string;
  client_name: string; item_title: string; item_url: string; phone: string };

export default function MessagesPage() {
  const [totals, setTotals] = useState<Totals>({ accounts: 0, new_msgs: 0, unanswered: 0, answered_today: 0 });
  const [accounts, setAccounts] = useState<Acc[]>([]);
  const [dialogs, setDialogs] = useState<Dlg[]>([]);
  const [scope, setScope] = useState("all");
  const [query, setQuery] = useState("");
  const [openCid, setOpenCid] = useState<string | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState("");
  const [narrow, setNarrow] = useState(false);
  const streamRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const check = () => setNarrow(window.innerWidth < 900);
    check(); window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  const aGet = useCallback(async (u: string) => {
    const t = getToken(); if (!t) { openRoute("/login"); return null; }
    try {
      const r = await fetch(u, { headers: { Authorization: "Bearer " + t } });
      if (r.status === 401) { setErr("401"); return null; }
      if (r.status === 403) { setErr("403"); return null; }
      return await r.json();
    } catch { return null; }
  }, []);

  const aPost = useCallback(async (u: string, b: any) => {
    const t = getToken(); if (!t) { openRoute("/login"); return null; }
    try {
      const r = await fetch(u, { method: "POST",
        headers: { Authorization: "Bearer " + t, "Content-Type": "application/json" },
        body: JSON.stringify(b) });
      return await r.json();
    } catch { return null; }
  }, []);

  const loadAccounts = useCallback(async () => {
    const j = await aGet("/api/inbox/accounts");
    if (j && j.status === "ok") { setErr(""); setAccounts(j.accounts || []); if (j.totals) setTotals(j.totals); }
  }, [aGet]);

  const loadDialogs = useCallback(async () => {
    const j = await aGet(`/api/inbox/dialogs?account_id=${encodeURIComponent(scope)}&q=${encodeURIComponent(query)}`);
    if (j && j.status === "ok") setDialogs(j.dialogs || []);
  }, [aGet, scope, query]);

  const loadThread = useCallback(async (acc: string, cid: string) => {
    const j = await aGet(`/api/inbox/thread?account_id=${encodeURIComponent(acc)}&avito_chat_id=${encodeURIComponent(cid)}`);
    if (j && j.status === "ok") { setThread(j.dialog); setMessages(j.messages || []); }
  }, [aGet]);

  useEffect(() => { loadAccounts(); }, [loadAccounts]);
  useEffect(() => { loadDialogs(); }, [loadDialogs]);
  useEffect(() => {
    const t = setInterval(() => {
      loadAccounts(); loadDialogs();
      if (openCid && thread) loadThread(thread.account_id, openCid);
    }, POLL);
    return () => clearInterval(t);
  }, [loadAccounts, loadDialogs, loadThread, openCid, thread]);

  useEffect(() => {
    if (streamRef.current) streamRef.current.scrollTop = streamRef.current.scrollHeight;
  }, [messages]);

  const open = async (d: Dlg) => {
    setOpenCid(d.avito_chat_id);
    await loadThread(d.account_id, d.avito_chat_id);
    await aPost("/api/inbox/mark_read", { account_id: d.account_id, avito_chat_id: d.avito_chat_id });
    loadAccounts(); loadDialogs();
  };

  const send = async () => {
    if (!draft.trim() || !thread || sending) return;
    setSending(true);
    const r = await aPost("/api/inbox/send", {
      account_id: thread.account_id, avito_chat_id: thread.avito_chat_id, text: draft.trim() });
    setSending(false);
    if (r && r.status === "ok") {
      setDraft(""); loadThread(thread.account_id, thread.avito_chat_id); loadDialogs();
    } else alert((r && r.message) || "Не удалось отправить");
  };

  const scopeName = scope === "all" ? "Все аккаунты в одном окне"
    : (accounts.find((a) => a.account_id === scope)?.name || "");

  if (err === "403") {
    return (
      <Page>
        <PageHeader title="Единое окно сообщений" />
        <Alert kind="danger">Доступ к инбоксу под этим аккаунтом ограничен (403).</Alert>
      </Page>
    );
  }

  const step: 0 | 1 | 2 = thread ? 2 : (scope !== "all" || dialogs.length ? 1 : 0);

  const paneAccounts = (
    <Pane title="Аккаунты"
          action={<Button kind="ghost" onClick={() => openRoute("/messages/setup")}
                          style={{ padding: "6px 10px" }}>+</Button>}>
      <ListItem title="Все аккаунты" dot={color.blue} active={scope === "all"}
                count={totals.new_msgs} onClick={() => setScope("all")} />
      {accounts.length === 0 ? (
        <div style={{ ...font.small, textAlign: "center", padding: space.xl }}>
          Нет подключённых аккаунтов.<br />
          <a onClick={() => openRoute("/messages/setup")}
             style={{ color: color.blue, cursor: "pointer" }}>+ Подключить аккаунт</a>
        </div>
      ) : null}
      {accounts.map((a) => (
        <ListItem key={a.account_id} title={a.name} subtitle={a.phone || undefined}
                  dot={STC[a.status] || color.muted} count={a.unread}
                  active={scope === a.account_id} onClick={() => setScope(a.account_id)} />
      ))}
    </Pane>
  );

  const paneDialogs = (
    <Pane title={scope === "all" ? "Диалоги (все аккаунты)" : "Диалоги — " + scopeName}>
      <div style={{ padding: space.sm }}>
        <Input placeholder="Поиск: текст, объявление, ID диалога"
               value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      {dialogs.length === 0 ? (
        <div style={{ ...font.small, textAlign: "center", padding: space.xl }}>Диалогов пока нет</div>
      ) : null}
      {dialogs.map((d, i) => (
        <ListItem key={d.account_id + d.avito_chat_id}
          active={openCid === d.avito_chat_id} onClick={() => open(d)}
          dot={DOT[i % DOT.length]}
          title={<>{d.client_name}{scope === "all" ? (
            <span style={{ ...font.small, marginLeft: 6 }}>· {d.account_name}</span>) : null}</>}
          subtitle={<>{d.item_title}{d.last_text ? " — " + d.last_text : ""}</>}
          meta={d.last_at} count={d.unread} danger={!d.answered} />
      ))}
    </Pane>
  );

  const paneChat = !thread ? (
    <Pane><div style={{ ...font.small, textAlign: "center", padding: 60 }}>
      Выберите диалог слева</div></Pane>
  ) : (
    <Card padding={0} style={{ display: "flex", flexDirection: "column",
                               width: "100%", overflow: "hidden" }}>
      <div style={{ padding: "13px 16px", borderBottom: "1px solid " + color.line }}>
        <div style={{ display: "flex", alignItems: "center", gap: space.md }}>
          <div style={{ ...font.h3, flex: 1, minWidth: 0 }}>{thread.client_name}</div>
          {thread.item_url ? (
            <a href={thread.item_url} target="_blank" rel="noreferrer"
               style={{ ...font.small, color: color.blue, textDecoration: "none",
                        display: "flex", alignItems: "center", gap: 4 }}>
              Открыть на Авито <Icon name="arrow" size={13} tone="accent" />
            </a>
          ) : null}
        </div>
        <div style={{ ...font.small, marginTop: 3 }}>{thread.item_title}</div>
        <div style={{ display: "flex", gap: space.xl, flexWrap: "wrap", marginTop: space.sm,
                      background: color.surfaceAlt, borderRadius: radius.sm,
                      padding: "8px 12px", ...font.small }}>
          <span>Аккаунт: <b style={{ color: color.heading }}>{thread.account_name}</b></span>
          {thread.phone ? (
            <span>Телефон: <a href={`tel:${thread.phone}`}
              style={{ color: color.blue, textDecoration: "none" }}>{thread.phone}</a></span>
          ) : null}
          <span>ID диалога: {thread.avito_chat_id}</span>
        </div>
      </div>

      <div ref={streamRef} style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: space.lg }}>
        {messages.map((m, i) => {
          if (m.content_type === "voice") {
            return <Bubble key={i} outgoing={m.direction === "out"} time={m.at}
              text={<audio controls preload="none" src={m.voice_url || ""}
                           style={{ maxWidth: 240, height: 34 }} />} />;
          }
          if (m.content_type === "image" && (m.media_ref || "").startsWith("http")) {
            return <Bubble key={i} outgoing={m.direction === "out"} time={m.at}
              text={<img src={m.media_ref} alt="фото" loading="lazy" decoding="async"
                         style={{ maxWidth: 220, borderRadius: radius.sm, display: "block" }} />} />;
          }
          if (m.content_type === "image") {
            return <Bubble key={i} system text="Фото от покупателя — откройте на Авито" />;
          }
          if (m.msg_type === "system") {
            return <Bubble key={i} system
              text={"Avito: " + m.text.replace(/^\[Системное сообщение\]\s*/, "")} />;
          }
          return <Bubble key={i} outgoing={m.direction === "out"} time={m.at}
                         fresh={m.direction === "in" && m.is_new} text={m.text} />;
        })}
      </div>

      <Composer value={draft} onChange={setDraft} onSend={send} sending={sending} />
    </Card>
  );

  return (
    <Page>
      {err === "401" ? (
        <Alert kind="warning">
          Сессия истекла. Обновите страницу или войдите заново.{" "}
          <a onClick={() => openRoute("/login")}
             style={{ color: color.blue, cursor: "pointer", fontWeight: 700 }}>Войти</a>
        </Alert>
      ) : null}

      <PageHeader title="Сообщения Авито" subtitle={scopeName} />

      <KpiRow>
        <Kpi value={totals.accounts} label="Всего аккаунтов" />
        <Kpi value={totals.new_msgs} label="Новые сообщения" kind="info" />
        <Kpi value={totals.unanswered} label="Не отвечено"
             kind={totals.unanswered ? "danger" : "neutral"} />
        <Kpi value={totals.answered_today} label="Сегодня ответов" kind="success" />
      </KpiRow>

      <div style={{ marginTop: space.md }}>
        <SplitLayout narrow={narrow} step={step}
          onBack={() => { if (thread) { setThread(null); setOpenCid(null); } else setScope("all"); }}
          left={paneAccounts} middle={paneDialogs} right={paneChat} />
      </div>
    </Page>
  );
}
