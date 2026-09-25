"use client";
import { Button } from "@/app/ui";
import { apiFetch } from "@/app/lib/api";


import { useCallback, useEffect, useMemo, useState } from "react";

type Finding = Record<string, any>;

type QueueLevel = "hot" | "warm" | "possible";

async function api(path: string, init?: RequestInit) {
  const response = await apiFetch(path, {
    credentials: "include",
    cache: "no-store",
    ...init,
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status}: ${body}`);
  }

  return response.json();
}

function qualityAllowed(item: Finding) {
  // in_work никогда не прячем автоматически.
  if (item?.status === "in_work") return true;

  const q = item?.payload?.quality_gate;

  if (q && q.qualified === false) {
    return false;
  }

  return true;
}

function borisSaleScore(item: Finding) {
  return Number(
    item?.payload?.sale_score?.score ??
    item?.score_total ??
    0
  );
}

function queueLevel(item: Finding): QueueLevel {
  const score = borisSaleScore(item);
  const intent = String(item?.intent || "");

  const text = String(item?.text || "").toLowerCase();

  const pain =
    text.includes("нет заявок") ||
    text.includes("результата нет") ||
    text.includes("потратили много денег") ||
    text.includes("нужна автоматизация") ||
    text.includes("заявок почти нет");

  if (
    score >= 80 ||
    (intent === "buy_service" && score >= 70) ||
    (intent === "buy_service" && pain)
  ) {
    return "hot";
  }

  if (
    score >= 60 ||
    intent === "buy_service"
  ) {
    return "warm";
  }

  return "possible";
}

function levelName(level: QueueLevel) {
  if (level === "hot") return "Горячий";
  if (level === "warm") return "Тёплый";
  return "Возможный";
}

function levelIcon(level: QueueLevel) {
  if (level === "hot") return "🔥";
  if (level === "warm") return "●";
  return "○";
}

function contactText(item: Finding) {
  const extracted =
    item?.payload?.boris_extracted_contacts ||
    item?.payload?._boris_extracted_contacts ||
    {};

  const parts: string[] = [];

  if (item?.author_username) {
    parts.push(
      String(item.author_username).startsWith("@")
        ? item.author_username
        : `@${item.author_username}`
    );
  }

  for (const username of extracted.usernames || []) {
    if (!parts.includes(username)) {
      parts.push(username);
    }
  }

  for (const phone of extracted.phones || []) {
    if (!parts.includes(phone)) {
      parts.push(phone);
    }
  }

  return parts.join(" · ");
}

function reasonText(item: Finding) {
  const text = String(item?.text || "").toLowerCase();

  const reasons: string[] = [];

  if (item?.intent === "buy_service") {
    reasons.push("ищет исполнителя");
  }

  if (
    text.includes("нет заявок") ||
    text.includes("заявок почти нет")
  ) {
    reasons.push("есть проблема с заявками");
  }

  if (text.includes("потратили много денег")) {
    reasons.push("уже тратил бюджет");
  }

  if (text.includes("нужна автоматизация")) {
    reasons.push("нужна автоматизация");
  }

  if (
    text.includes("на постоянку") ||
    text.includes("постоянной основе")
  ) {
    reasons.push("ищет долгосрочно");
  }

  if (
    text.includes("авито") ||
    text.includes("avito") ||
    text.includes("авитолог")
  ) {
    reasons.push("релевантен BORIS / Avito");
  }

  if (!reasons.length && item?.matched_terms?.length) {
    reasons.push(
      `совпало: ${item.matched_terms.slice(0, 3).join(", ")}`
    );
  }

  return reasons.slice(0, 4).join(" · ");
}

function shortDate(value: any) {
  if (!value) return "";

  try {
    return new Date(value).toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

export default function MonitoringPage() {
  const [items, setItems] = useState<Finding[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [autopilot, setAutopilot] = useState<any>(null);
  const [tab, setTab] =
    useState<"queue" | "work" | "all">("queue");

  const [replyFor, setReplyFor] =
    useState<string | null>(null);

  const [replies, setReplies] =
    useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      // Берём запас, фильтрация quality идёт здесь.
      const [data, auto] = await Promise.all([
        api("/api/monitoring/findings?limit=100&min_score=40"),
        api("/api/monitoring/sales-autopilot").catch(() => null),
      ]);

      const rows = data?.items || data?.findings || [];
      setItems(rows);
      setAutopilot(auto);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const qualified = useMemo(
    () => items.filter(qualityAllowed),
    [items]
  );

  const visible = useMemo(() => {
    let rows = qualified;

    if (tab === "queue") {
      rows = rows.filter(
        x =>
          !x.lead_id &&
          !["rejected", "closed"].includes(x.status)
      );
    }

    if (tab === "work") {
      rows = rows.filter(
        x =>
          Boolean(x.lead_id) ||
          x.status === "in_work"
      );
    }

    return [...rows].sort((a, b) => {
      const rank = {
        hot: 3,
        warm: 2,
        possible: 1,
      };

      const ra = rank[queueLevel(a)];
      const rb = rank[queueLevel(b)];

      if (ra !== rb) return rb - ra;

      return (
        Number(b.score_total || 0) -
        Number(a.score_total || 0)
      );
    });
  }, [qualified, tab]);

  const counters = useMemo(() => {
    let hot = 0;
    let warm = 0;
    let possible = 0;
    let inWork = 0;

    for (const item of qualified) {
      const level = queueLevel(item);

      if (level === "hot") hot++;
      if (level === "warm") warm++;
      if (level === "possible") possible++;

      if (
        item.lead_id ||
        item.status === "in_work"
      ) {
        inWork++;
      }
    }

    return { hot, warm, possible, inWork };
  }, [qualified]);

  async function prepareReply(item: Finding) {
    setBusy(item.id);
    setError("");

    try {
      const data = await api(
        `/api/monitoring/findings/${item.id}/reply`,
        { method: "POST" }
      );

      const text =
        data?.reply ||
        data?.text ||
        data?.suggested_reply ||
        "";

      setReplies(prev => ({
        ...prev,
        [item.id]: text,
      }));

      setReplyFor(item.id);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  async function createLead(item: Finding) {
    setBusy(item.id);
    setError("");

    try {
      await api(
        `/api/monitoring/findings/${item.id}/lead`,
        { method: "POST" }
      );

      await load();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  async function setOutcome(
    item: Finding,
    outcome: string,
    reason?: string
  ) {
    setBusy(item.id);
    setError("");

    try {
      await api(
        `/api/monitoring/findings/${item.id}/outcome`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            outcome,
            reason: reason || null,
          }),
        }
      );

      await load();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  async function reject(item: Finding) {
    setBusy(item.id);
    setError("");

    try {
      // API ожидает status как Query parameter.
      await api(
        `/api/monitoring/findings/${item.id}/status?status=rejected`,
        { method: "POST" }
      );

      await load();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  async function copyText(text: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {}
  }

  return (
    <div data-boris-g34v2="monitoring"

     className="boris-f3-92a5ce328c9e32">
      <div

       className="boris-f3-cb53e8d4180803">
        <div>
          <div

           className="boris-f3-f35e4a01e05c3d">
            BORIS · ПОИСК КЛИЕНТОВ
          </div>

          <h1 data-boris-g34v2-heading="1"

           className="boris-f3-b466ae086661e3">
            Рассылка и продажи
          </h1>

          <div

           className="boris-f3-a30c0ccfeb6d80">
            BORIS сам ищет спрос, отсекает вакансии и саморекламу, пишет подходящим лидам,
            ловит ответы, делает один follow-up и передаёт заинтересованных в продажи.
          </div>
        </div>

        <Button
          onClick={load}
          disabled={loading}

         className="boris-f3-93928d2d5eb5c2">
          {loading ? "Обновляю…" : "Обновить"}
        </Button>
      </div>

      {autopilot && (
        <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:10,margin:"14px 0 18px",padding:14,border:"1px solid rgba(47,111,237,.18)",borderRadius:16,background:"linear-gradient(135deg,rgba(47,111,237,.07),rgba(16,185,129,.05))"}}>
          <div style={{gridColumn:"1 / -1",display:"flex",alignItems:"center",justifyContent:"space-between",gap:10,flexWrap:"wrap"}}><div><b style={{fontSize:15}}>Автопилот включён</b><div style={{fontSize:12,color:"#64748b",marginTop:3}}>Ручной approve для подходящих high-fit лидов не нужен · FIT от {autopilot.min_fit} · {autopilot.send_window}</div>{autopilot.scan_state==="cooldown"?<div style={{fontSize:12,color:"#b45309",marginTop:5,fontWeight:700}}>Сбор новых Telegram-сообщений временно на безопасной паузе до {new Date(autopilot.scan_cooldown_until).toLocaleString("ru-RU")}. Готовая очередь, ответы и продажи продолжают работать.</div>:<div style={{fontSize:12,color:"#047857",marginTop:5,fontWeight:700}}>Сбор новых Telegram-лидов работает.</div>}</div><span style={{padding:"6px 10px",borderRadius:999,background:"#dcfce7",color:"#047857",fontSize:12,fontWeight:800}}>РАБОТАЕТ АВТОМАТИЧЕСКИ</span></div>
          {[
            ["Отправлено сегодня", `${autopilot.sent_today}/${autopilot.daily_cap}`],
            ["Follow-up сегодня", autopilot.followups_today],
            ["Ответили", autopilot.replied_total],
            ["Квалифицированы", autopilot.qualified_total],
            ["Свежие ждут", autopilot.fresh_external_waiting],
            ["Реактивация ждёт", autopilot.reactivation_waiting],
            ["Отсечено safety", autopilot.safety_filtered_total],
            ["Источников Telegram", autopilot.active_sources],
          ].map(([label,value])=><div key={String(label)} style={{background:"#fff",border:"1px solid rgba(127,127,127,.14)",borderRadius:12,padding:"10px 12px"}}><div style={{fontSize:11,color:"#64748b"}}>{label}</div><div style={{fontSize:21,fontWeight:850,marginTop:2}}>{String(value ?? 0)}</div></div>)}
        </div>
      )}

      {error && (
        <div

         className="boris-f3-4182404401e7f4">
          {error}
        </div>
      )}

      <div

       className="boris-f3-057f2dacf4b38a">
        {[
          ["🔥 Горячие", counters.hot],
          ["● Тёплые", counters.warm],
          ["○ Возможные", counters.possible],
          ["В работе", counters.inWork],
        ].map(([name, value]) => (
          <div
            key={String(name)}

           className="boris-f3-95378d85322469">
            <div

             className="boris-f3-040c4dccd7a28a">
              {name}
            </div>

            <div

             className="boris-f3-891e3c8f081924">
              {String(value)}
            </div>
          </div>
        ))}
      </div>

      <div

       className="boris-f3-f8d97dcfb18172">
        {[
          ["queue", "Очередь"],
          ["work", "В работе"],
          ["all", "Все подходящие"],
        ].map(([key, label]) => {
          const active = tab === key;

          return (
            <Button
              key={key}
              onClick={() => setTab(key as any)}
              style={{
                minHeight: 38,
                padding: "0 15px",
                borderRadius: 10,
                border: active
                  ? "1px solid var(--boris-blue)"
                  : "1px solid rgba(127,127,127,.2)",
                background: active
                  ? "rgba(47,111,237,.08)"
                  : "#fff",
                color: active
                  ? "var(--boris-blue)"
                  : "inherit",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {label}
            </Button>
          );
        })}
      </div>

      <div className="boris-f3-b51a97e67feb8c">
        {visible.map(item => {
          const level = queueLevel(item);
          const contact = contactText(item);
          const reply =
            replies[item.id] ||
            item.suggested_reply ||
            "";

          return (
            <div data-boris-g34v2-card="1"
              key={item.id}
              style={{
                background: "#fff",
                borderRadius: 17,
                border:
                  level === "hot"
                    ? "1px solid rgba(47,111,237,.42)"
                    : "1px solid rgba(127,127,127,.18)",
                boxShadow:
                  "0 5px 18px rgba(0,0,0,.035)",
                padding: 20,
              }}
            >
              <div

               className="boris-f3-6343d546217d27">
                <div>
                  <div

                   className="boris-f3-33ff8b3e899e47">
                    <span
                      style={{
                        fontWeight: 750,
                        color:
                          level === "hot"
                            ? "var(--boris-blue)"
                            : "inherit",
                      }}
                    >
                      {levelIcon(level)}{" "}
                      {levelName(level)}
                    </span>

                    <span

                     className="boris-f3-72b9df392a44e7">
                      {item.source_title ||
                        item.chat_title ||
                        "Telegram"}
                    </span>

                    {item.lead_id && (
                      <span

                       className="boris-f3-fa237411f92bfd">
                        Уже лид
                      </span>
                    )}
                  </div>

                  <div

                   className="boris-f3-5d5b94e4435504">
                    {shortDate(
                      item.posted_at ||
                      item.collected_at
                    )}
                    {" · "}
                    BORIS {borisSaleScore(item)}/100
                    {" · match "}
                    {Number(item.score_total || 0)}/100
                    {item.intent
                      ? ` · ${item.intent}`
                      : ""}
                  </div>
                </div>
              </div>

              <div

               className="boris-f3-4ffa5209a0e787">
                {item.text}
              </div>

              {reasonText(item) && (
                <div

                 className="boris-f3-281ebd649f1379">
                  <strong>
                    Почему BORIS считает это лидом:
                  </strong>{" "}
                  {reasonText(item)}
                </div>
              )}

              <div

               className="boris-f3-214ca7e5303fc8">
                <div>
                  <span className="boris-f3-5a692dc73cf75e">
                    Контакт:
                  </span>{" "}
                  <strong>
                    {contact || "в исходном сообщении"}
                  </strong>
                </div>

                {item.rule_name && (
                  <div>
                    <span className="boris-f3-5a692dc73cf75e">
                      Правило:
                    </span>{" "}
                    {item.rule_name}
                  </div>
                )}
              </div>

              {replyFor === item.id && reply && (
                <div

                 className="boris-f3-ed11a061d74b3c">
                  <div

                   className="boris-f3-210c5fe5d51858">
                    Ответ, подготовленный BORIS
                  </div>

                  <div

                   className="boris-f3-bad84de16b6b13">
                    {reply}
                  </div>

                  <Button
                    onClick={() => copyText(reply)}

                   className="boris-f3-75573b4a6d6836">
                    Скопировать ответ
                  </Button>
                </div>
              )}

              <div

               className="boris-f3-85e2b2d4231d10">
                {item.status !== "in_work" && (
                  <Button
                    onClick={() =>
                      setOutcome(item, "in_work")
                    }
                    disabled={busy === item.id}

                   className="boris-f3-66973bb7430f7a">
                    В работу
                  </Button>
                )}

                {item.status === "in_work" && (
                  <>
                    <Button
                      onClick={() =>
                        setOutcome(item, "contacted")
                      }
                      disabled={busy === item.id}
                    >
                      Связались
                    </Button>

                    <Button
                      onClick={() =>
                        setOutcome(item, "replied")
                      }
                      disabled={busy === item.id}
                    >
                      Ответил
                    </Button>

                    <Button
                      onClick={() =>
                        setOutcome(item, "qualified")
                      }
                      disabled={busy === item.id}
                    >
                      Целевой
                    </Button>

                    <Button
                      onClick={() =>
                        setOutcome(item, "won")
                      }
                      disabled={busy === item.id}
                    >
                      Сделка
                    </Button>

                    <Button
                      onClick={() =>
                        setOutcome(
                          item,
                          "lost",
                          "no_response"
                        )
                      }
                      disabled={busy === item.id}
                    >
                      Потерян
                    </Button>
                  </>
                )}

                {!item.lead_id && (
                  <Button
                    onClick={() => createLead(item)}
                    disabled={busy === item.id}

                   className="boris-f3-86701a470dea7a">
                    Создать вручную
                  </Button>
                )}

                <Button
                  onClick={() => prepareReply(item)}
                  disabled={busy === item.id}

                 className="boris-f3-f27147dee1e33f">
                  Показать вариант ответа
                </Button>

                {item.url && (
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"

                   className="boris-f3-c861053e9906a7">
                    Открыть в Telegram
                  </a>
                )}

                {!item.lead_id &&
                  item.status !== "in_work" && (
                    <Button
                      onClick={() =>
                        setOutcome(
                          item,
                          "not_fit",
                          "irrelevant"
                        )
                      }
                      disabled={busy === item.id}

                     className="boris-f3-19ad89e5e30dc2">
                      Не подходит
                    </Button>
                  )}
              </div>
            </div>
          );
        })}

        {!loading && visible.length === 0 && (
          <div

           className="boris-f3-437cd04aa565b3">
            В этой очереди сейчас нет лидов.
          </div>
        )}
      </div>
    </div>
  );
}
