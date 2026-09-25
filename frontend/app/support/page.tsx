"use client";
import ClientSupport from "../components/ClientSupport";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const CARD: any = { background: "#FFFFFF", border: "1px solid var(--boris-border)", borderRadius: 16,
  padding: 20, position: "relative", overflow: "hidden" };
const SCOL: any = { "новое": "#B42318", "в работе": "#E8850B", "решено": "#12805C" };

export default function SupportPage() {
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  const [filter, setFilter] = useState("");
  const [replyFor, setReplyFor] = useState<number | null>(null);
  const [replyText, setReplyText] = useState("");
  // Админские ручки поддержки отвечают «Доступ запрещён» всем, кроме владельца.
  // Спрашиваем сервер, а не гадаем по роли из localStorage: если отказ —
  // показываем клиенту его собственный экран обращений.
  const [denied, setDenied] = useState<boolean | null>(null);
  useEffect(() => {
    const t = typeof window !== "undefined" ? localStorage.getItem("boris_token") : null;
    if (!t) { setDenied(false); return; }
    fetch("/api/support/all", { headers: { Authorization: "Bearer " + t } })
      .then(async (r) => ({ status: r.status, data: await r.json().catch(() => null) }))
      .then(({ status, data }) => setDenied(status === 401 || status === 403 || Boolean(data && data.status === "error")))
      .catch(() => setDenied(false));
  }, []);

  const auth = () => {
    const t = localStorage.getItem("boris_token");
    if (!t) { router.push("/login"); return null; }
    return { Authorization: "Bearer " + t };
  };

  const load = async (st?: string) => {
    const h = auth(); if (!h) return;
    try {
      const r = await fetch("/api/support/all" + (st ? "?status=" + encodeURIComponent(st) : ""), { headers: h });
      if (r.status === 401) { router.push("/login"); return; }
      const j = await r.json();
      if (j.status === "ok") setData(j); else setErr(j.message || "Ошибка");
    } catch (e: any) { setErr(String(e)); }
  };

  useEffect(() => { load(); }, []);

  const send = async (id: number) => {
    const h = auth(); if (!h || !replyText.trim()) return;
    await fetch("/api/support/reply", {
      method: "POST", headers: { ...h, "Content-Type": "application/json" },
      body: JSON.stringify({ ticket_id: id, text: replyText }) });
    setReplyText(""); setReplyFor(null); load(filter);
  };

  const setStatus = async (id: number, status: string) => {
    const h = auth(); if (!h) return;
    await fetch("/api/support/status", {
      method: "POST", headers: { ...h, "Content-Type": "application/json" },
      body: JSON.stringify({ ticket_id: id, status }) });
    load(filter);
  };

  // Client role is determined independently from the owner-only ticket list.
  // Check it before waiting for admin data; otherwise a client receives status:error
  // from /api/support/all and stays forever on «Загрузка…».
  if (denied === null) return null;
  if (denied) return <ClientSupport />;
  if (err) return <div data-boris-g33="support" className="boris-f3-ddfbf53220e06f">{err}</div>;
  if (!data) return <div className="boris-f3-854e69659fafc8">Загрузка…</div>;

  const counts = data["по_статусам"] || {};

  return (
    <div className="b-mgr-wrap boris-f3-687e573a4cf13b boris-g2-page">

      <div className="b-mgr-hero boris-f3-9a771c9d7c218b">
        <div>
          <div className="boris-f3-05435878a10f53">Обращения клиентов</div>
          <div className="boris-f3-70e4bdb187a357">
            Новых: {counts["новое"] || 0} · в работе: {counts["в работе"] || 0} · решено: {counts["решено"] || 0}
          </div>
        </div>
        <button className="b-mgr-btn boris-f3-a9d1549ee372ad" onClick={() => router.push("/dashboard")}
>В кабинет</button>
      </div>

      <div className="boris-f3-589ada0163513c">
        {["", "новое", "в работе", "решено"].map(s => (
          <button data-boris-g33-interactive="1" data-boris-g33-card="1" key={s || "all"} onClick={() => { setFilter(s); load(s); }}
            style={{ background: filter === s ? "var(--boris-blue)" : "#fff", color: filter === s ? "#fff" : "var(--boris-text-2)",
              border: "1.5px solid " + (filter === s ? "var(--boris-blue)" : "#D0D5DD"), borderRadius: 20,
              padding: "8px 16px", cursor: "pointer", fontSize: 14, fontWeight: 600 }}>
            {s || "все"}
          </button>
        ))}
      </div>

      {data["обращения"].length === 0 ? (
        <div data-boris-g33-card="1" style={CARD}><span className="boris-f3-1b02eb650d31b1">Обращений нет.</span></div>
      ) : (
        <div className="boris-f3-b51a97e67feb8c">
          {data["обращения"].map((t: any) => (
            <div data-boris-g33-card="1" key={t["номер"]} className="b-mgr-card boris-g1-card" data-boris-section="support" style={CARD}>
              <div className="boris-f3-05709e2fceded2">
                <div className="boris-f3-252286067d1d2a">
                  №{t["номер"]} · {t["имя"] || "без имени"}
                  {t["контакт"] ? <span className="boris-f3-9a322f6286407a"> · {t["контакт"]}</span> : null}
                  {t["account_id"] ? <span className="boris-f3-18dbc6ee3d2627"> · {t["account_id"]}</span> : null}
                </div>
                <div className="boris-f3-9d24370899051e">
                  <span data-boris-g33-card="1" style={{ background: (SCOL[t["статус"]] || "var(--boris-text-2)") + "18", color: SCOL[t["статус"]] || "var(--boris-text-2)",
                    borderRadius: 20, padding: "4px 12px", fontSize: 13, fontWeight: 600 }}>{t["статус"]}</span>
                  <span className="boris-f3-acca32db2b4a84">{String(t["создано"]).replace("T", " ").slice(0, 16)}</span>
                </div>
              </div>
              <div className="boris-f3-5d4ca3b2d66c59">{t["текст"]}</div>

              {(t["ответы"] || []).map((r: any, i: number) => (
                <div data-boris-g33-card="1" key={i} style={{ marginTop: 8, padding: "8px 12px", borderRadius: 10, fontSize: 15,
                  background: r["кто"] === "support" ? "#F0F4FF" : "#F8FAFF", color: "var(--boris-text-2)" }}>
                  <b>{r["кто"] === "support" ? "Мы" : "Клиент"}:</b> {r["текст"]}
                  <span className="boris-f3-acca32db2b4a84"> · {String(r["когда"]).replace("T", " ").slice(0, 16)}</span>
                </div>
              ))}

              <div className="boris-f3-67260b8ef6e68a">
                <button onClick={() => setReplyFor(replyFor === t["номер"] ? null : t["номер"])}
 className="boris-f3-b17009aa7970d8">Ответить</button>
                {t["статус"] !== "решено" ? (
                  <button onClick={() => setStatus(t["номер"], "решено")}
 className="boris-f3-239c18c8815520">Решено</button>
                ) : (
                  <button onClick={() => setStatus(t["номер"], "в работе")}
 className="boris-f3-be01b8fd3446bd">Вернуть в работу</button>
                )}
              </div>

              {replyFor === t["номер"] ? (
                <div className="boris-f3-500c453a975f1b">
                  <textarea value={replyText} onChange={e => setReplyText(e.target.value)}
                    placeholder="Ответ клиенту — придёт ему в кабинет и в Telegram"
  className="boris-f3-802309e3271fcd"/>
                  <button onClick={() => send(t["номер"])}
 className="boris-f3-9996ab886f73d1">Отправить</button>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
