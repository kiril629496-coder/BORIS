"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const CARD: any = { background: "#FFFFFF", border: "1px solid #E3E7F0", borderRadius: 16,
  padding: 20, position: "relative", overflow: "hidden" };
const SCOL: any = { "новое": "#B42318", "в работе": "#E8850B", "решено": "#12805C" };

export default function SupportPage() {
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  const [filter, setFilter] = useState("");
  const [replyFor, setReplyFor] = useState<number | null>(null);
  const [replyText, setReplyText] = useState("");

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

  if (err) return <div style={{ padding: 40, color: "#B42318" }}>{err}</div>;
  if (!data) return <div style={{ padding: 40, color: "#667085" }}>Загрузка…</div>;

  const counts = data["по_статусам"] || {};

  return (
    <div className="b-mgr-wrap" style={{ maxWidth: "100%", padding: "clamp(16px,3vw,32px) clamp(12px,2.5vw,36px) 60px",
      fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif", color: "#101828" }}>

      <div className="b-mgr-hero" style={{ background: "linear-gradient(135deg,#2F6FED,#1E4FD8)", borderRadius: 20,
        padding: "28px 32px", color: "#fff", marginBottom: 24, display: "flex",
        justifyContent: "space-between", alignItems: "center", gap: 20, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-0.02em" }}>Обращения клиентов</div>
          <div style={{ opacity: 0.85, fontSize: 15 }}>
            Новых: {counts["новое"] || 0} · в работе: {counts["в работе"] || 0} · решено: {counts["решено"] || 0}
          </div>
        </div>
        <button className="b-mgr-btn" onClick={() => router.push("/dashboard")}
          style={{ border: "1.5px solid rgba(255,255,255,0.6)", background: "transparent", color: "#fff",
            borderRadius: 10, padding: "10px 18px", cursor: "pointer", fontWeight: 600 }}>В кабинет</button>
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        {["", "новое", "в работе", "решено"].map(s => (
          <button key={s || "all"} onClick={() => { setFilter(s); load(s); }}
            style={{ background: filter === s ? "#2F6FED" : "#fff", color: filter === s ? "#fff" : "#475467",
              border: "1.5px solid " + (filter === s ? "#2F6FED" : "#D0D5DD"), borderRadius: 20,
              padding: "8px 16px", cursor: "pointer", fontSize: 14, fontWeight: 600 }}>
            {s || "все"}
          </button>
        ))}
      </div>

      {data["обращения"].length === 0 ? (
        <div style={CARD}><span style={{ color: "#667085" }}>Обращений нет.</span></div>
      ) : (
        <div style={{ display: "grid", gap: 14 }}>
          {data["обращения"].map((t: any) => (
            <div key={t["номер"]} className="b-mgr-card" style={CARD}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
                <div style={{ fontWeight: 700, fontSize: 16 }}>
                  №{t["номер"]} · {t["имя"] || "без имени"}
                  {t["контакт"] ? <span style={{ color: "#667085", fontWeight: 400 }}> · {t["контакт"]}</span> : null}
                  {t["account_id"] ? <span style={{ color: "#98A2B3", fontWeight: 400, fontSize: 14 }}> · {t["account_id"]}</span> : null}
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <span style={{ background: (SCOL[t["статус"]] || "#667085") + "18", color: SCOL[t["статус"]] || "#667085",
                    borderRadius: 20, padding: "4px 12px", fontSize: 13, fontWeight: 600 }}>{t["статус"]}</span>
                  <span style={{ color: "#98A2B3", fontSize: 13 }}>{String(t["создано"]).replace("T", " ").slice(0, 16)}</span>
                </div>
              </div>
              <div style={{ color: "#344054", fontSize: 15, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{t["текст"]}</div>

              {(t["ответы"] || []).map((r: any, i: number) => (
                <div key={i} style={{ marginTop: 8, padding: "8px 12px", borderRadius: 10, fontSize: 15,
                  background: r["кто"] === "support" ? "#F0F4FF" : "#F8FAFF", color: "#344054" }}>
                  <b>{r["кто"] === "support" ? "Мы" : "Клиент"}:</b> {r["текст"]}
                  <span style={{ color: "#98A2B3", fontSize: 13 }}> · {String(r["когда"]).replace("T", " ").slice(0, 16)}</span>
                </div>
              ))}

              <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                <button onClick={() => setReplyFor(replyFor === t["номер"] ? null : t["номер"])}
                  style={{ background: "#fff", border: "1.5px solid #2F6FED", color: "#2F6FED", borderRadius: 10,
                    padding: "8px 14px", cursor: "pointer", fontWeight: 600, fontSize: 14 }}>Ответить</button>
                {t["статус"] !== "решено" ? (
                  <button onClick={() => setStatus(t["номер"], "решено")}
                    style={{ background: "#fff", border: "1.5px solid #12805C", color: "#12805C", borderRadius: 10,
                      padding: "8px 14px", cursor: "pointer", fontSize: 14 }}>Решено</button>
                ) : (
                  <button onClick={() => setStatus(t["номер"], "в работе")}
                    style={{ background: "#fff", border: "1.5px solid #D0D5DD", color: "#475467", borderRadius: 10,
                      padding: "8px 14px", cursor: "pointer", fontSize: 14 }}>Вернуть в работу</button>
                )}
              </div>

              {replyFor === t["номер"] ? (
                <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                  <textarea value={replyText} onChange={e => setReplyText(e.target.value)}
                    placeholder="Ответ клиенту — придёт ему в кабинет и в Telegram"
                    style={{ flex: 1, minWidth: 240, minHeight: 70, padding: "10px 12px",
                      border: "1.5px solid #E3E7F0", borderRadius: 10, fontSize: 15 }} />
                  <button onClick={() => send(t["номер"])}
                    style={{ background: "#2F6FED", color: "#fff", border: "none", borderRadius: 10,
                      padding: "10px 20px", cursor: "pointer", fontWeight: 700, alignSelf: "flex-start" }}>Отправить</button>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
