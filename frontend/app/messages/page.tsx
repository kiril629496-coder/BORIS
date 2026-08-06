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
  item_title: string; item_url: string; chat_url?: string; last_text: string; last_at: string;
  unread: number; answered: boolean };
type Msg = { direction: "in" | "out"; text: string; at: string; is_new?: boolean;
  msg_type?: string; content_type?: string; media_ref?: string; voice_url?: string };
type Thread = { account_id: string; account_name: string; avito_chat_id: string;
  client_name: string; item_title: string; item_url: string; chat_url?: string; phone: string };

const MARK_STYLE: Record<string, { bg: string; fg: string; text: string }> = {
  hot: { bg: "#FEE2E2", fg: "#991B1B", text: "ждёт ответа" },
  late: { bg: "#FEF3C7", fg: "#92400E", text: "просрочено" },
  reactivation: { bg: "#DBEAFE", fg: "#1E40AF", text: "можно вернуть" },
};

function markLabel(m: any) {
  if (!m) return null;
  const st = MARK_STYLE[m.queue];
  if (!st) return null;
  return (
    <span style={{ background: st.bg, color: st.fg, borderRadius: 6, padding: "1px 6px",
      marginRight: 6, fontSize: 12, fontWeight: 600, whiteSpace: "nowrap" }}>
      {st.text} · {m.age_days} дн{m.has_draft ? " · черновик" : ""}
    </span>
  );
}

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
  const [marks, setMarks] = useState<Record<string, any>>({});
  const [crmTasks, setCrmTasks] = useState<any[]>([]);
  const [crmBadges, setCrmBadges] = useState<Record<string, any>>({});
  const [crmOpen, setCrmOpen] = useState(false);
  const [tgChat, setTgChat] = useState("");
  const [tgSaved, setTgSaved] = useState(false);
  const [mopDraft, setMopDraft] = useState<any>(null);
  const [mopText, setMopText] = useState("");
  const [mopEdit, setMopEdit] = useState(false);
  const [sleepMode, setSleepMode] = useState(false);
  const [sleepQueues, setSleepQueues] = useState<any[]>([]);
  const [sleepQueue, setSleepQueue] = useState("");
  const [sleepItems, setSleepItems] = useState<any[]>([]);
  const [crmForm, setCrmForm] = useState(false);
  const [crmDate, setCrmDate] = useState("");
  const [crmTime, setCrmTime] = useState("");
  const [crmType, setCrmType] = useState("позвонить");
  const [crmTitle, setCrmTitle] = useState("");
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
    // scope="all" — служебное значение фронта. Проверка изоляции его не знает и
    // отвечает 403, поэтому для всех аккаунтов параметр просто не передаём.
    const mk = await aGet("/api/reactivation/marks"
      + (scope === "all" ? "" : `?account_id=${encodeURIComponent(scope)}`));
    if (mk && mk.status === "ok") setMarks(mk.marks || {});
    if (scope !== "all") {
      const bg = await aGet(`/api/crm/badges?account_id=${encodeURIComponent(scope)}`);
      if (bg && bg.status === "ok") setCrmBadges(bg.badges || {});
    }
  }, [aGet, scope, query]);

  const loadThread = useCallback(async (acc: string, cid: string) => {
    const j = await aGet(`/api/inbox/thread?account_id=${encodeURIComponent(acc)}&avito_chat_id=${encodeURIComponent(cid)}`);
    if (j && j.status === "ok") { setThread(j.dialog); setMessages(j.messages || []); }
    const tk = await aGet(`/api/crm/tasks?account_id=${encodeURIComponent(acc)}&chat_id=${encodeURIComponent(cid)}`);
    if (tk && tk.status === "ok") setCrmTasks(tk.tasks || []);
    const dr = await aGet(`/api/messenger/pending_drafts?account_id=${encodeURIComponent(acc)}`);
    if (dr && dr.status === "ok") {
      const mine = (dr.drafts || []).find((x: any) => x.avito_chat_id === cid);
      setMopDraft(mine || null);
      setMopText(mine ? mine.text || "" : "");
      setMopEdit(false);
    }
  }, [aGet]);

  const loadSleep = useCallback(async () => {
    const q = await aGet("/api/reactivation/queues");
    if (q) {
      // ручка отдаёт словарь: hot / late / reactivation, каждый с title и count
      const list = ["hot", "late", "reactivation"]
        .filter((k) => q[k])
        .map((k) => ({ key: k, title: q[k].title, count: q[k].count }));
      setSleepQueues(list);
      const first = sleepQueue || (list[0] && list[0].key) || "reactivation";
      setSleepQueue(first);
      const c = await aGet("/api/reactivation/candidates?queue=" + encodeURIComponent(first));
      setSleepItems((c && c.items) || []);
    }
  }, [aGet, sleepQueue]);

  useEffect(() => { if (sleepMode) loadSleep(); }, [sleepMode, loadSleep]);
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
      <div style={{ display: "flex", gap: 6, padding: "0 8px 10px" }}>
        <button onClick={() => setSleepMode(false)} style={{ flex: 1, border: "1px solid " + color.line, background: sleepMode ? "#fff" : color.blue, color: sleepMode ? color.heading : "#fff", borderRadius: 8, padding: "7px 10px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>Активные</button>
        <button onClick={() => setSleepMode(true)} style={{ flex: 1, border: "1px solid " + color.line, background: sleepMode ? color.blue : "#fff", color: sleepMode ? "#fff" : color.heading, borderRadius: 8, padding: "7px 10px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>Спящие</button>
      </div>
      {sleepMode && (
        <div style={{ padding: "0 8px 10px" }}>
          {sleepQueues.length > 0 && (
            <select value={sleepQueue} onChange={async (e) => { const q = e.target.value; setSleepQueue(q); const c = await aGet("/api/reactivation/candidates?queue=" + encodeURIComponent(q)); setSleepItems((c && (c.items || c.candidates)) || []); }} style={{ width: "100%", border: "1px solid " + color.line, borderRadius: 8, padding: "7px 9px", fontSize: 13, marginBottom: 8 }}>
              {sleepQueues.map((q: any, i: number) => <option key={i} value={q.key || q.name}>{(q.title || q.label || q.name || q.key) + (q.count != null ? " — " + q.count : "")}</option>)}
            </select>
          )}
          {sleepItems.length === 0 && <div style={{ ...font.small, textAlign: "center", padding: 24 }}>В этой очереди пока пусто</div>}
          {sleepItems.map((c: any, i: number) => (
            <div key={i} style={{ border: "1px solid " + color.line, borderRadius: 10, padding: "10px 12px", marginBottom: 8 }}>
              <div style={{ ...font.small, fontWeight: 700, color: color.heading }}>{c.client_name || c.title || c.item_title || "Клиент"}</div>
              <div style={font.small}>{(c.last_message || c.note || "").slice(0, 90)}</div>
              <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                <button onClick={async () => { await aPost("/api/reactivation/candidate/" + c.id + "/take", {}); loadSleep(); }} style={{ border: "none", background: color.blue, color: "#fff", borderRadius: 8, padding: "5px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>Взять в работу</button>
                <button onClick={async () => { await aPost("/api/reactivation/candidate/" + c.id + "/exclude", {}); loadSleep(); }} style={{ border: "1px solid " + color.line, background: "#fff", borderRadius: 8, padding: "5px 12px", fontSize: 12, cursor: "pointer" }}>Исключить</button>
              </div>
            </div>
          ))}
        </div>
      )}
      {!sleepMode && dialogs.map((d, i) => (
        <ListItem key={d.account_id + d.avito_chat_id}
          active={openCid === d.avito_chat_id} onClick={() => open(d)}
          dot={DOT[i % DOT.length]}
          title={<>{d.client_name}{scope === "all" ? (
            <span style={{ ...font.small, marginLeft: 6 }}>· {d.account_name}</span>) : null}</>}
          subtitle={<>{markLabel(marks[d.avito_chat_id])}{d.item_title}{d.last_text ? " — " + d.last_text : ""}</>}
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
              Открыть объявление <Icon name="arrow" size={13} tone="accent" />
            </a>
          ) : null}
          {thread.chat_url ? (
            <a href={thread.chat_url} target="_blank" rel="noreferrer"
               style={{ ...font.small, color: color.blue, textDecoration: "none",
                        display: "flex", alignItems: "center", gap: 4 }}>
              Переписка в Avito <Icon name="arrow" size={13} tone="accent" />
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

        <div style={{ marginTop: space.sm, border: "1px solid " + color.line, borderRadius: radius.sm, overflow: "hidden" }}>
          <div onClick={() => setCrmOpen(!crmOpen)}
               style={{ padding: "9px 12px", cursor: "pointer", display: "flex", alignItems: "center",
                        gap: space.sm, background: color.surfaceAlt, ...font.small }}>
            <b style={{ color: color.heading }}>Напоминания</b>
            {crmTasks.filter((t: any) => t.status === "overdue").length > 0 && (
              <span style={{ background: "#FEE4E2", color: "#B42318", borderRadius: 10, padding: "1px 8px", fontWeight: 700 }}>
                просрочено {crmTasks.filter((t: any) => t.status === "overdue").length}
              </span>
            )}
            {crmTasks.filter((t: any) => t.status === "today").length > 0 && (
              <span style={{ background: "#D1FADF", color: "#027A48", borderRadius: 10, padding: "1px 8px", fontWeight: 700 }}>
                сегодня {crmTasks.filter((t: any) => t.status === "today").length}
              </span>
            )}
            {crmTasks.length === 0 && <span>задач нет</span>}
            <span style={{ marginLeft: "auto" }}>{crmOpen ? "свернуть" : "развернуть"}</span>
          </div>

          {crmOpen && (
            <div style={{ padding: "10px 12px" }}>
              <div style={{ display: "flex", gap: space.sm, alignItems: "center",
                            paddingBottom: "9px", marginBottom: "4px",
                            borderBottom: "1px solid " + color.line, flexWrap: "wrap" }}>
                <span style={font.small}>Напоминания в Telegram:</span>
                <input value={tgChat} onChange={(e) => setTgChat(e.target.value)}
                       placeholder="номер чата, например -1001234567890"
                       style={{ flex: 1, minWidth: 170, border: "1px solid " + color.line,
                                borderRadius: radius.sm, padding: "6px 9px", fontSize: 13 }} />
                <button onClick={async () => {
                  if (!tgChat.trim()) return;
                  await aPost("/api/accounts/" + encodeURIComponent(thread.account_id) + "/update",
                              { telegram_chat_id: tgChat.trim() });
                  setTgSaved(true);
                }} style={{ background: "#2F6FED", color: "#FFFFFF", border: "none",
                            borderRadius: radius.sm, padding: "6px 14px", fontSize: 13,
                            fontWeight: 600, cursor: "pointer" }}>Сохранить</button>
                <span style={font.small}>
                  {tgSaved ? "Сохранено" : "узнать номер: перешлите сообщение боту @userinfobot"}
                </span>
              </div>
              {crmTasks.filter((t: any) => t.status !== "done").map((t: any) => (
                <div key={t.id} style={{ display: "flex", alignItems: "flex-start", gap: space.sm,
                                         padding: "7px 0", borderBottom: "1px solid " + color.line }}>
                  <span style={{ width: 8, height: 8, borderRadius: 4, marginTop: 6, flexShrink: 0,
                                 background: t.status === "overdue" ? "#F04438" : t.status === "today" ? "#12B76A" : "#98A2B3" }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ ...font.small, color: color.heading, fontWeight: 600 }}>{t.title}</div>
                    <div style={font.small}>{t.type} · {t.due_date}{t.due_time ? " " + t.due_time : ""}</div>
                    {t.comment ? <div style={font.small}>{t.comment}</div> : null}
                  </div>
                  <button onClick={async () => { await aPost("/api/crm/tasks/done", { account_id: thread.account_id, task_id: t.id });
                                                 loadThread(thread.account_id, thread.avito_chat_id); }}
                          style={{ border: "1px solid " + color.line, background: "#fff", borderRadius: 8,
                                   padding: "4px 10px", fontSize: 12, cursor: "pointer" }}>Готово</button>
                  <button onClick={async () => { const d = new Date(); d.setDate(d.getDate() + 1);
                                                 await aPost("/api/crm/tasks/update", { account_id: thread.account_id, task_id: t.id, due_date: d.toISOString().slice(0, 10) });
                                                 loadThread(thread.account_id, thread.avito_chat_id); }}
                          style={{ border: "1px solid " + color.line, background: "#fff", borderRadius: 8,
                                   padding: "4px 10px", fontSize: 12, cursor: "pointer" }}>+1 день</button>
                </div>
              ))}

              {!crmForm ? (
                <button onClick={() => { setCrmForm(true); setCrmDate(new Date().toISOString().slice(0, 10)); }}
                        style={{ marginTop: 10, border: "1px solid " + color.line, background: "#fff",
                                 borderRadius: 8, padding: "6px 12px", fontSize: 13, cursor: "pointer" }}>
                  + Добавить напоминание
                </button>
              ) : (
                <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 7 }}>
                  <input value={crmTitle} onChange={(e) => setCrmTitle(e.target.value)} placeholder="Что нужно сделать"
                         style={{ border: "1px solid " + color.line, borderRadius: 8, padding: "7px 10px", fontSize: 13 }} />
                  <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
                    <input type="date" value={crmDate} onChange={(e) => setCrmDate(e.target.value)}
                           style={{ border: "1px solid " + color.line, borderRadius: 8, padding: "6px 8px", fontSize: 13 }} />
                    <input type="time" value={crmTime} onChange={(e) => setCrmTime(e.target.value)}
                           style={{ border: "1px solid " + color.line, borderRadius: 8, padding: "6px 8px", fontSize: 13 }} />
                    <select value={crmType} onChange={(e) => setCrmType(e.target.value)}
                            style={{ border: "1px solid " + color.line, borderRadius: 8, padding: "6px 8px", fontSize: 13 }}>
                      {["позвонить", "написать", "отправить фото", "отправить КП", "запросить оплату", "уточнить доставку", "другое"]
                        .map((x) => <option key={x} value={x}>{x}</option>)}
                    </select>
                  </div>
                  <div style={{ display: "flex", gap: 7 }}>
                    <button onClick={async () => {
                              if (!crmTitle.trim()) return;
                              await aPost("/api/crm/tasks", { account_id: thread.account_id, chat_id: thread.avito_chat_id,
                                                              due_date: crmDate, due_time: crmTime, type: crmType, title: crmTitle });
                              setCrmTitle(""); setCrmTime(""); setCrmForm(false);
                              loadThread(thread.account_id, thread.avito_chat_id); }}
                            style={{ border: "none", background: color.blue, color: "#fff", borderRadius: 8,
                                     padding: "7px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>Сохранить</button>
                    <button onClick={() => setCrmForm(false)}
                            style={{ border: "1px solid " + color.line, background: "#fff", borderRadius: 8,
                                     padding: "7px 14px", fontSize: 13, cursor: "pointer" }}>Отмена</button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {mopDraft && (
        <div style={{ margin: "0 16px 12px", border: "1px solid #A6F4C5", background: "#F6FEF9", borderRadius: radius.sm, padding: "12px 14px" }}>
          <div style={{ ...font.small, fontWeight: 700, color: "#027A48", marginBottom: 8 }}>Борис подготовил ответ — проверьте перед отправкой</div>
          {mopEdit
            ? <textarea value={mopText} onChange={(e) => setMopText(e.target.value)} style={{ width: "100%", minHeight: 120, border: "1px solid " + color.line, borderRadius: 8, padding: "9px 11px", fontSize: 14, resize: "vertical" }} />
            : <div style={{ fontSize: 14, color: color.heading, whiteSpace: "pre-wrap" }}>{mopText}</div>}
          <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
            <button disabled={sending} onClick={async () => { setSending(true); await aPost("/api/messenger/draft/approve", { draft_id: mopDraft.draft_id, text: mopText }); setMopDraft(null); setSending(false); if (thread) loadThread(thread.account_id, thread.avito_chat_id); }} style={{ border: "none", background: "#12B76A", color: "#fff", borderRadius: 8, padding: "8px 16px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>Отправить клиенту</button>
            <button onClick={() => setMopEdit(!mopEdit)} style={{ border: "1px solid " + color.line, background: "#fff", borderRadius: 8, padding: "8px 16px", fontSize: 13, cursor: "pointer" }}>{mopEdit ? "Готово" : "Изменить"}</button>
            <button onClick={async () => { await aPost("/api/messenger/draft/discard", { draft_id: mopDraft.draft_id }); setMopDraft(null); }} style={{ border: "1px solid #FDA29B", color: "#B42318", background: "#fff", borderRadius: 8, padding: "8px 16px", fontSize: 13, cursor: "pointer" }}>Отклонить</button>
          </div>
        </div>
      )}

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
