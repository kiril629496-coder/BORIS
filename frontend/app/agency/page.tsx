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
  const card: any = { background: "#FFFFFF", border: "1px solid var(--boris-border)", borderRadius: 16, padding: "22px 24px", position: "relative", overflow: "hidden" };
  const money = (n: any) => (Number(n) || 0).toLocaleString("ru-RU") + " \u20BD";

  if (err) return <div className="boris-f3-ddfbf53220e06f">{err}</div>;
  if (!data) return <div className="boris-f3-854e69659fafc8">Загрузка…</div>;

  return (
    <div className="boris-f3-969bbd8b9ad4ce">
      <style dangerouslySetInnerHTML={{ __html: HOVER }} />
      <div className="boris-f3-64ed56d7822636">
        <h1 className="boris-f3-099e185f4df478">Мои аккаунты</h1>
        <button onClick={() => router.push("/dashboard")}
 className="boris-f3-a7b849c9695f3b">В кабинет</button>
      </div>

      <div className="boris-f3-d01dee0f0e5c30">
        <div className="b-ag-card" style={card}>
          <div  className="boris-f3-33c29569f4a764"/>
          <div className="boris-f3-08f009b795c643">🏢</div>
          <div className="boris-f3-175c77b3fec00c">{data["аккаунтов"]}</div>
          <div className="boris-f3-7d1cfed0dd4f28">аккаунтов</div>
        </div>
        <div className="b-ag-card" style={card}>
          <div  className="boris-f3-9448ebed6526ed"/>
          <div className="boris-f3-de396af9715221">💳</div>
          <div className="boris-f3-175c77b3fec00c">
            {data["из_них_платных"] ? money(data["цена_за_аккаунт"]) : "По договору"}
          </div>
          <div className="boris-f3-7d1cfed0dd4f28">
            {data["из_них_платных"] ? "за аккаунт · Тариф 1" : "тариф и оплата"}
          </div>
        </div>
        <div className="b-ag-card" style={card}>
          <div  className="boris-f3-9774f493ed4b2a"/>
          <div className="boris-f3-1d9cee7d690987">💰</div>
          <div className="boris-f3-175c77b3fec00c">
            {data["из_них_платных"] ? money(data["к_оплате_за_период"]) : "\u2014"}
          </div>
          <div className="boris-f3-7d1cfed0dd4f28">
            {data["из_них_платных"] ? "к оплате за период" : "счёт выставляется вручную"}
          </div>
        </div>
        <div className="b-ag-card" style={card}>
          <div  className="boris-f3-b9f3f1733e5a8c"/>
          <div className="boris-f3-d0660dc5c6beb9">⏳</div>
          <div className="boris-f3-175c77b3fec00c">{data["дней_до_конца_периода"] ?? "—"}</div>
          <div className="boris-f3-7d1cfed0dd4f28">дней до списания</div>
        </div>
      </div>

      <div style={{ ...card, marginBottom: 18 }}>
        <div className="boris-f3-d5370c51632efc">Подключить ещё аккаунт</div>
        {prev && (
          <div className="boris-f3-8b09689183ea25">
            Доплата сейчас — <b className="boris-f3-872175c4f12c7c">{money(prev["доплата_за_ещё_один"])}</b> за
            оставшиеся {prev["дней_до_конца_периода"]} дней. Со следующего периода:
            {" "}{money(prev["со_следующего_периода"])} за {(prev["аккаунтов_сейчас"] || 0) + 1} аккаунтов
            ({money(prev["цена_за_аккаунт_станет"])} за каждый).
          </div>
        )}
        <div className="boris-f3-defeae6db5822b">
          <input placeholder="Название (например: Мебель Казань)" value={newName}
            onChange={e => setNewName(e.target.value)}
  className="boris-f3-151dbb44cfda1b"/>
          <button onClick={addAccount} disabled={busy}
            style={{ background: busy ? "var(--boris-text-3)" : "var(--boris-blue)", color: "#fff", border: "none",
                     borderRadius: 8, padding: "10px 18px", cursor: busy ? "default" : "pointer", fontWeight: 600 }}>
            {busy ? "Подключаем…" : "Подключить и оплатить"}</button>
          {msg && <span className="boris-f3-83fefc2d13e1fa">{msg}</span>}
        </div>
      </div>

      <div style={card}>
        <div className="boris-f3-6c4905b5de58d0">Аккаунты</div>
        <table className="boris-f3-246798c345c9bb">
          <thead><tr className="boris-f3-d718f25d17f67a">
            <th className="boris-f3-ed7579f15d16d2">Название</th><th>Тариф</th>
            <th>Баннеры</th><th>Объявления</th><th>Avito</th>
          </tr></thead>
          <tbody>
            {data["аккаунты"].map((a: any) => (
              <tr key={a.account_id} className="boris-f3-7df93423a4b246">
                <td className="boris-f3-08770b5ea674a8">
                  {a["название"]}
                  <div className="boris-f3-b5875a093e2b8b">{a.account_id}</div>
                </td>
                <td>{a["тариф"]}</td>
                <td style={{ color: a["лимит_баннеров_исчерпан"] ? "#B42318" : "var(--boris-text)",
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
