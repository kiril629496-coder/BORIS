"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

export default function AgencyPage() {
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [prev, setPrev] = useState<any>(null);
  const [err, setErr] = useState("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const auth = () => {
    const t = localStorage.getItem("boris_token");
    if (!t) { router.push("/login"); return null; }
    return { Authorization: "Bearer " + t };
  };

  const load = async () => {
    const h = auth(); if (!h) return;
    try {
      const [a, b] = await Promise.all([
        fetch("/api/accounts/agency_overview", { headers: h }),
        fetch("/api/accounts/billing_preview", { headers: h }),
      ]);
      if (a.status === 401) { router.push("/login"); return; }
      const aj = await a.json(); const bj = await b.json();
      if (aj.status !== "ok") { setErr(aj.message || "Ошибка загрузки"); return; }
      setData(aj); setPrev(bj.status === "ok" ? bj : null);
    } catch (e: any) { setErr(String(e)); }
  };

  useEffect(() => { load(); }, []);

  const addAccount = async () => {
    const h = auth(); if (!h) return;
    setBusy(true); setMsg("");
    try {
      const r = await fetch("/api/accounts/add_account", {
        method: "POST", headers: { ...h, "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName }),
      });
      const j = await r.json();
      if (j.status !== "ok") { setMsg(j.message || "Ошибка"); return; }
      setMsg(j["сообщение"] || "Аккаунт подключён");
      setNewName("");
      await load();
      if (j["доплата"] > 0) {
        const l = await fetch(`/api/payments/robokassa/link?account_id=${encodeURIComponent(j.account_id)}&pack=acc_upgrade`, { headers: h });
        const lj = await l.json();
        if (lj.status === "ok" && lj.url) window.open(lj.url, "_blank");
      }
    } finally { setBusy(false); }
  };

const HOVER = `
.b-ag-card { transition: transform .2s ease, box-shadow .2s ease; }
.b-ag-card:hover { transform: translateY(-3px); box-shadow: 0 12px 24px rgba(16,24,40,.10); }
.b-ag-btn { transition: transform .18s cubic-bezier(.34,1.56,.64,1), box-shadow .18s ease; }
.b-ag-btn:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(16,24,40,.14); }
`;
  const card: any = { background: "#FFFFFF", border: "1px solid #E3E7F0", borderRadius: 16, padding: "22px 24px", position: "relative", overflow: "hidden" };
  const money = (n: any) => (Number(n) || 0).toLocaleString("ru-RU") + " \u20BD";

  if (err) return <div style={{ padding: 40, color: "#B42318" }}>{err}</div>;
  if (!data) return <div style={{ padding: 40, color: "#667085" }}>Загрузка…</div>;

  return (
    <div style={{ maxWidth: 1120, margin: "0 auto", padding: "28px 20px 60px",
                  fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif", color: "#101828" }}>
      <style dangerouslySetInnerHTML={{ __html: HOVER }} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 22 }}>
        <h1 style={{ fontSize: 26, fontWeight: 700, margin: 0 }}>Мои аккаунты</h1>
        <button onClick={() => router.push("/dashboard")}
          style={{ border: "1px solid #D0D5DD", background: "#fff", borderRadius: 8,
                   padding: "8px 14px", cursor: "pointer", color: "#475467" }}>В кабинет</button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 14, marginBottom: 20 }}>
        <div className="b-ag-card" style={card}>
          <div style={{ position: "absolute", top: -30, right: -30, width: 90, height: 90, borderRadius: "50%", background: "linear-gradient(135deg,#4C8DFF,#2F6FED)", opacity: 0.08 }} />
          <div style={{ width: 44, height: 44, borderRadius: "50%", background: "linear-gradient(135deg,#4C8DFF,#2F6FED)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, marginBottom: 12 }}>🏢</div>
          <div style={{ fontSize: 26, fontWeight: 800, color: "#1D2939", letterSpacing: "-0.02em" }}>{data["аккаунтов"]}</div>
          <div style={{ fontSize: 14, color: "#667085", marginTop: 2 }}>аккаунтов</div>
        </div>
        <div className="b-ag-card" style={card}>
          <div style={{ position: "absolute", top: -30, right: -30, width: 90, height: 90, borderRadius: "50%", background: "linear-gradient(135deg,#9B87F5,#7C5CFC)", opacity: 0.08 }} />
          <div style={{ width: 44, height: 44, borderRadius: "50%", background: "linear-gradient(135deg,#9B87F5,#7C5CFC)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, marginBottom: 12 }}>💳</div>
          <div style={{ fontSize: 26, fontWeight: 800, color: "#1D2939", letterSpacing: "-0.02em" }}>
            {data["из_них_платных"] ? money(data["цена_за_аккаунт"]) : "По договору"}
          </div>
          <div style={{ fontSize: 14, color: "#667085", marginTop: 2 }}>
            {data["из_них_платных"] ? "за аккаунт · Тариф 1" : "тариф и оплата"}
          </div>
        </div>
        <div className="b-ag-card" style={card}>
          <div style={{ position: "absolute", top: -30, right: -30, width: 90, height: 90, borderRadius: "50%", background: "linear-gradient(135deg,#32D583,#12805C)", opacity: 0.08 }} />
          <div style={{ width: 44, height: 44, borderRadius: "50%", background: "linear-gradient(135deg,#32D583,#12805C)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, marginBottom: 12 }}>💰</div>
          <div style={{ fontSize: 26, fontWeight: 800, color: "#1D2939", letterSpacing: "-0.02em" }}>
            {data["из_них_платных"] ? money(data["к_оплате_за_период"]) : "\u2014"}
          </div>
          <div style={{ fontSize: 14, color: "#667085", marginTop: 2 }}>
            {data["из_них_платных"] ? "к оплате за период" : "счёт выставляется вручную"}
          </div>
        </div>
        <div className="b-ag-card" style={card}>
          <div style={{ position: "absolute", top: -30, right: -30, width: 90, height: 90, borderRadius: "50%", background: "linear-gradient(135deg,#FDB022,#F79009)", opacity: 0.08 }} />
          <div style={{ width: 44, height: 44, borderRadius: "50%", background: "linear-gradient(135deg,#FDB022,#F79009)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, marginBottom: 12 }}>⏳</div>
          <div style={{ fontSize: 26, fontWeight: 800, color: "#1D2939", letterSpacing: "-0.02em" }}>{data["дней_до_конца_периода"] ?? "—"}</div>
          <div style={{ fontSize: 14, color: "#667085", marginTop: 2 }}>дней до списания</div>
        </div>
      </div>

      <div style={{ ...card, marginBottom: 18 }}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Подключить ещё аккаунт</div>
        {prev && (
          <div style={{ color: "#667085", fontSize: 13, marginBottom: 12 }}>
            Доплата сейчас — <b style={{ color: "#101828" }}>{money(prev["доплата_за_ещё_один"])}</b> за
            оставшиеся {prev["дней_до_конца_периода"]} дней. Со следующего периода:
            {" "}{money(prev["со_следующего_периода"])} за {(prev["аккаунтов_сейчас"] || 0) + 1} аккаунтов
            ({money(prev["цена_за_аккаунт_станет"])} за каждый).
          </div>
        )}
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <input placeholder="Название (например: Мебель Казань)" value={newName}
            onChange={e => setNewName(e.target.value)}
            style={{ padding: "9px 12px", border: "1px solid #D0D5DD", borderRadius: 8, minWidth: 280 }} />
          <button onClick={addAccount} disabled={busy}
            style={{ background: busy ? "#98A2B3" : "#2F6FED", color: "#fff", border: "none",
                     borderRadius: 8, padding: "10px 18px", cursor: busy ? "default" : "pointer", fontWeight: 600 }}>
            {busy ? "Подключаем…" : "Подключить и оплатить"}</button>
          {msg && <span style={{ color: "#067647", fontSize: 13 }}>{msg}</span>}
        </div>
      </div>

      <div style={card}>
        <div style={{ fontWeight: 600, marginBottom: 12 }}>Аккаунты</div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead><tr style={{ textAlign: "left", color: "#667085", fontSize: 12 }}>
            <th style={{ padding: "8px 6px" }}>Название</th><th>Тариф</th>
            <th>Баннеры</th><th>Объявления</th><th>Avito</th>
          </tr></thead>
          <tbody>
            {data["аккаунты"].map((a: any) => (
              <tr key={a.account_id} style={{ borderTop: "1px solid #EAECF0" }}>
                <td style={{ padding: "10px 6px" }}>
                  {a["название"]}
                  <div style={{ color: "#98A2B3", fontSize: 12 }}>{a.account_id}</div>
                </td>
                <td>{a["тариф"]}</td>
                <td style={{ color: a["лимит_баннеров_исчерпан"] ? "#B42318" : "#101828",
                             fontWeight: a["лимит_баннеров_исчерпан"] ? 600 : 400 }}>
                  {a["баннеры"]}{a["лимит_баннеров_исчерпан"] ? " — исчерпан" : ""}
                </td>
                <td>{a["объявления"]}</td>
                <td style={{ color: a["avito_подключён"] ? "#067647" : "#B54708" }}>
                  {a["avito_подключён"] ? "подключён" : "не подключён"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
