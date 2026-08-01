"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const INP: any = { border: "1.5px solid #E3E7F0", borderRadius: 10, padding: "11px 14px", fontSize: 15, background: "#FFFFFF", color: "#1D2939" };
const CARD: any = { background: "#FFFFFF", border: "1px solid #E3E7F0", borderRadius: 16,
  padding: 24, position: "relative", overflow: "hidden", height: "100%" };
const CARD_C: any = { ...CARD, display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center" };
const GRID: any = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))", gap: 14, alignItems: "stretch" };

function Stat({ icon, grad, color, name, value, badge }: any) {
  return (
    <div className="b-mgr-card" style={CARD_C}>
      <div style={{ position: "absolute", top: -40, right: -40, width: 120, height: 120,
        borderRadius: "50%", background: grad, opacity: 0.08 }} />
      <div className="b-mgr-ic" style={{ width: 64, height: 64, borderRadius: "50%", background: grad, display: "flex",
        alignItems: "center", justifyContent: "center", fontSize: 28, marginBottom: 16,
        boxShadow: "0 8px 20px " + color + "40" }}>{icon}</div>
      <div style={{ fontWeight: "bold", fontSize: 16, color: "#1D2939", marginBottom: 8, minHeight: 40 }}>{name}</div>
      <div style={{ fontWeight: 800, fontSize: 26, letterSpacing: "-0.02em", marginBottom: 14, color: "#1D2939" }}>{value}</div>
      {badge ? <div style={{ background: color + "18", color: color, fontSize: 13, fontWeight: "bold",
        borderRadius: 20, padding: "5px 14px" }}>{badge}</div> : null}
    </div>
  );
}

function ExtraPicker({ price, cat, setCat, keys }: any) {
  const money = (n: any) => (Number(n) || 0).toLocaleString("ru-RU") + " \u20BD";
  const row: any = (i: number) => ({ color: "#475467", fontSize: 15, padding: "10px 0",
    borderTop: i ? "1px solid #EAECF0" : "none" });
  return (
    <div className="b-mgr-card" style={{ background: "#FFFFFF", border: "1px solid #E3E7F0",
      borderRadius: 16, padding: 24, position: "relative", overflow: "hidden" }}>
      <select value={cat} onChange={e => setCat(e.target.value)}
        style={{ padding: "11px 14px", border: "1.5px solid #E3E7F0", borderRadius: 10,
          minWidth: 280, fontSize: 15, marginBottom: 16 }}>
        <option>Докупка баннеров</option>
        <option>Баннеры для магазина</option>
        <option>ИИ-менеджер</option>
        <option>Лимиты и правила</option>
      </select>
      {cat === "Докупка баннеров" ? (
        <div>{price[keys.b].map((x: any, i: number) => (
          <div key={i} style={row(i)}>{x["пакет"]} — <b style={{ color: "#1D2939" }}>{money(x["цена"])}</b>
            <span style={{ color: "#98A2B3", fontSize: 14 }}> ({x["примечание"]})</span></div>
        ))}</div>
      ) : null}
      {cat === "Баннеры для магазина" ? (
        <div>{price[keys.m].map((x: any, i: number) => (
          <div key={i} style={row(i)}>{x["пакет"]} — <b style={{ color: "#1D2939" }}>{money(x["цена"])}</b></div>
        ))}</div>
      ) : null}
      {cat === "ИИ-менеджер" ? (
        <div>{price[keys.a].map((x: any, i: number) => (
          <div key={i} style={row(i)}>{x["пакет"]} — <b style={{ color: "#1D2939" }}>{money(x["цена"])}</b></div>
        ))}</div>
      ) : null}
      {cat === "Лимиты и правила" ? (
        <div>
          {Object.keys(price["лимиты"]).map((k: string, i: number) => (
            <div key={k} style={row(i)}><b style={{ color: "#1D2939" }}>{k}</b>: {price["лимиты"][k]}</div>
          ))}
          <div style={{ background: "#FFFAEB", border: "1px solid #FEDF89", borderRadius: 12,
            padding: 12, marginTop: 14, fontSize: 14, color: "#93370D" }}>{price["важно"]}</div>
          <div style={{ color: "#667085", fontSize: 15, marginTop: 10 }}>Пробный период: {price["пробный_период"]}</div>
        </div>
      ) : null}
    </div>
  );
}

export default function ManagerPage() {
  const [secTab, setSecTab] = useState(0);
  const [callOpen, setCallOpen] = useState(false);
  const [coldCat, setColdCat] = useState(0);
  const [cf, setCf] = useState<any>({ company: "", contact_name: "", contact_role: "ЛПР", business: "", comment: "", agreement: "", remind_at: "" });
  const [callMsg, setCallMsg] = useState("");
  const saveCall = async () => {
    setCallMsg("");
    const t = typeof window !== "undefined" ? localStorage.getItem("boris_token") : null;
    const r = await fetch("/api/manager/calls/add", { method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + t },
      body: JSON.stringify(cf) });
    const j = await r.json();
    setCallMsg(j.status === "ok" ? "Записано" : (j.message || "Ошибка"));
    if (j.status === "ok") setCf({ company: "", contact_name: "", contact_role: "ЛПР", business: "", comment: "", agreement: "", remind_at: "" });
  };
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);
  const [seg, setSeg] = useState<any>(null);
  const [calls, setCalls] = useState("");
  const [talks, setTalks] = useState("");
  const [actMsg, setActMsg] = useState("");
  const [price, setPrice] = useState<any>(null);
  const [know, setKnow] = useState<any>(null);
  const [kTopic, setKTopic] = useState("");
  const [kOpen, setKOpen] = useState<number | null>(null);
  const [kSearch, setKSearch] = useState("");
  const [knowProd, setKnowProd] = useState<any>(null);
  const [pTopic, setPTopic] = useState("");
  const [pOpen, setPOpen] = useState<number | null>(null);
  const [extraCat, setExtraCat] = useState("Докупка баннеров");
  const [extraCatPriv, setExtraCatPriv] = useState("Докупка баннеров");

  useEffect(() => {
    const t = localStorage.getItem("boris_token");
    if (!t) { router.push("/login"); return; }
    fetch("/api/manager/overview", { headers: { Authorization: "Bearer " + t } })
      .then(r => { if (r.status === 401) { router.push("/login"); return null; } return r.json(); })
      .then(j => { if (!j) return; if (j.status !== "ok") setErr(j.message || "Ошибка"); else setData(j); })
      .catch(e => setErr(String(e)));
    fetch("/api/manager/by_segment", { headers: { Authorization: "Bearer " + t } })
      .then(r => r.json()).then(j => { if (j && j.status === "ok") setSeg(j["сегменты"]); })
      .catch(() => {});
    fetch("/api/manager/pricebook", { headers: { Authorization: "Bearer " + t } })
      .then(r => r.json()).then(j => { if (j && j.status === "ok") setPrice(j); })
      .catch(() => {});
    fetch("/api/manager/knowledge?section=%D0%BE%20%D0%BF%D1%80%D0%BE%D0%B4%D1%83%D0%BA%D1%82%D0%B5", { headers: { Authorization: "Bearer " + t } })
      .then(r => r.json()).then(j => {
        if (j && j.status === "ok") { setKnowProd(j["темы"]); setPTopic(Object.keys(j["темы"])[0] || ""); }
      })
      .catch(() => {});
    fetch("/api/manager/knowledge", { headers: { Authorization: "Bearer " + t } })
      .then(r => r.json()).then(j => {
        if (j && j.status === "ok") { setKnow(j["темы"]); setKTopic(Object.keys(j["темы"])[0] || ""); }
      })
      .catch(() => {});
  }, []);

  const saveActivity = async () => {
    const t = localStorage.getItem("boris_token");
    setActMsg("");
    try {
      const r = await fetch("/api/manager/log_activity", {
        method: "POST", headers: { Authorization: "Bearer " + t, "Content-Type": "application/json" },
        body: JSON.stringify({ calls: Number(calls) || 0, talks: Number(talks) || 0 }),
      });
      const j = await r.json();
      setActMsg(j.status === "ok" ? "Записано" : (j.message || "Ошибка"));
    } catch (e) { setActMsg("Ошибка сети"); }
  };

  const money = (n: any) => (Number(n) || 0).toLocaleString("ru-RU") + " \u20BD";

  if (err) return <div style={{ padding: 40, color: "#B42318" }}>{err}</div>;
  if (!data) return <div style={{ padding: 40, color: "#667085" }}>Загрузка…</div>;

  const next = data["до_следующего_грейда"];
  const f = data["воронка"] || {};
  const clients = data["клиенты"] || [];
  const pct = (v: any) => (v === null || v === undefined) ? "—" : v + "%";
  const H: any = { fontSize: 18, color: "#1D2939", margin: "0 0 14px", fontWeight: 700 };

  return (
    <div className="b-mgr-wrap" style={{ maxWidth: "100%", margin: "0 auto", padding: "clamp(16px, 3vw, 32px) clamp(12px, 2.5vw, 36px) 60px",
      fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif", color: "#101828" }}>

      <div className="b-mgr-hero" style={{ background: "linear-gradient(135deg,#2F6FED,#1E4FD8)", borderRadius: 20,
        padding: "36px 40px", color: "#fff", marginBottom: 28, display: "flex",
        justifyContent: "space-between", alignItems: "center", gap: 24, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 28, fontWeight: 800, letterSpacing: "-0.02em", marginBottom: 6 }}>Кабинет менеджера</div>
          <div style={{ opacity: 0.85, fontSize: 15, marginBottom: 14 }}>Ваша ссылка для регистрации клиентов</div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <code style={{ fontSize: 17, fontWeight: 600 }}>{data["ссылка"]}</code>
            <button className="b-mgr-btn" onClick={() => { navigator.clipboard.writeText(data["ссылка"] || ""); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
              style={{ border: "none", background: "rgba(255,255,255,0.22)", color: "#fff", borderRadius: 10,
                padding: "9px 16px", cursor: "pointer", fontWeight: 700 }}>{copied ? "Скопировано" : "Скопировать"}</button>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ opacity: 0.85, fontSize: 15 }}>К выплате за {data["период"]}</div>
          <div style={{ fontSize: 38, fontWeight: 800, letterSpacing: "-0.02em" }}>{money(data["итого_к_выплате"])}</div>
          <div style={{ opacity: 0.85, fontSize: 14 }}>оклад {money(data["оклад"])} + {data["процент"]}%</div>
          <button className="b-mgr-btn" onClick={() => router.push("/manager/profile")}
            style={{ marginTop: 12, marginRight: 8, border: "1.5px solid rgba(255,255,255,0.6)", background: "transparent",
              color: "#fff", borderRadius: 10, padding: "8px 18px", cursor: "pointer", fontWeight: 700 }}>Данные обо мне</button>
          <button className="b-mgr-btn" onClick={() => router.push("/manager/clients")}
            style={{ marginTop: 12, marginRight: 8, border: "none", background: "#fff",
              color: "#2F6FED", borderRadius: 10, padding: "8px 18px", cursor: "pointer", fontWeight: 700 }}>Мои клиенты</button>
          <button onClick={() => { localStorage.removeItem("boris_token"); router.push("/login"); }}
            style={{ marginTop: 12, border: "1.5px solid rgba(255,255,255,0.6)", background: "transparent",
              color: "#fff", borderRadius: 10, padding: "8px 16px", cursor: "pointer", fontWeight: 600 }}>Выйти</button>
        </div>
      </div>

      <div style={{ marginBottom: 32 }}>
        <div style={GRID}>
          <Stat icon="👥" grad="linear-gradient(135deg,#4C8DFF,#2F6FED)" color="#2F6FED"
            name="Клиентов приведено" value={data["клиентов_всего"]} />
          <Stat icon="💼" grad="linear-gradient(135deg,#3DBE93,#12805C)" color="#12805C"
            name="Продажи тарифов" value={money(data["продажи_тарифов"])} badge={"комиссия " + money(data["начислено_с_тарифов"])} />
          <Stat icon="🎁" grad="linear-gradient(135deg,#F79009,#E8850B)" color="#E8850B"
            name="Продажи допуслуг" value={money(data["продажи_апсейлов"])} badge={"комиссия " + money(data["начислено_с_апсейлов"])} />
          <Stat icon="🏆" grad="linear-gradient(135deg,#7C5CFC,#5B3FD9)" color="#5B3FD9"
            name="Ваш грейд" value={data["процент"] + "%"} badge={data["оклад"] ? "оклад " + money(data["оклад"]) : "без оклада"} />
        </div>
      </div>

      {next ? (
        <div style={{ ...CARD, background: "#FFFAEB", border: "1px solid #FEDF89" }}>
          <b>До следующего грейда:</b> продать ещё {money(next["нужно_продать_ещё"])} — тогда оклад {money(next["тогда_оклад"])} и {next["тогда_процент"]}%
        </div>
      ) : null}

      <div style={{ marginTop: 32 }}>
        <h3 style={H}>Воронка за {data["период"]}</h3>
        <div className="b-mgr-card" style={CARD}>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 20 }}>
            <span style={{ color: "#667085", fontSize: 15 }}>Сегодня сделано:</span>
            <input placeholder="звонков" value={calls} onChange={e => setCalls(e.target.value)}
              style={{ padding: "9px 12px", border: "1.5px solid #E3E7F0", borderRadius: 10, width: 110 }} />
            <input placeholder="разговоров" value={talks} onChange={e => setTalks(e.target.value)}
              style={{ padding: "9px 12px", border: "1.5px solid #E3E7F0", borderRadius: 10, width: 130 }} />
            <button onClick={saveActivity} style={{ background: "#2F6FED", color: "#fff", border: "none",
              borderRadius: 10, padding: "11px 20px", cursor: "pointer", fontWeight: 700 }}>Записать</button>
            {actMsg ? <span style={{ color: "#12805C" }}>{actMsg}</span> : null}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%, 150px),1fr))", gap: 14 }}>
            {[["Звонков", f["звонков"], null], ["Разговоров", f["разговоров"], f["звонок_в_разговор"]],
              ["Регистраций", f["зарегистрировались"], f["разговор_в_регистрацию"]],
              ["Оплатили", f["оплатили"], f["регистрация_в_оплату"]]].map((row: any) => (
              <div key={row[0]} style={{ background: "#F8FAFF", borderRadius: 12, padding: "14px 16px" }}>
                <div style={{ color: "#667085", fontSize: 13 }}>{row[0]}</div>
                <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-0.02em" }}>{row[1] || 0}</div>
                {row[2] !== null ? <div style={{ color: "#2F6FED", fontSize: 13 }}>конверсия {pct(row[2])}</div> : null}
              </div>
            ))}
          </div>
          <div style={{ marginTop: 16, color: "#667085", fontSize: 15, display: "flex", gap: 24, flexWrap: "wrap" }}>
            <span>Средний чек: <b style={{ color: "#1D2939" }}>{money(f["средний_чек"])}</b></span>
            <span>С одного звонка: <b style={{ color: "#12805C" }}>{money(f["рублей_с_звонка"])}</b></span>
            <span>Звонок в оплату: <b style={{ color: "#1D2939" }}>{pct(f["звонок_в_оплату"])}</b></span>
          </div>
        </div>
      </div>

      {seg ? (
        <div style={{ marginTop: 32 }}>
          <h3 style={H}>Частники и агентства</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%, 300px),1fr))", gap: 14 }}>
            {[["частник", "#2F6FED", "linear-gradient(135deg,#4C8DFF,#2F6FED)", "Частники (1 аккаунт)"],
              ["агентство", "#5B3FD9", "linear-gradient(135deg,#7C5CFC,#5B3FD9)", "Агентства (2 и больше)"]].map((row: any) => {
              const sv = (seg && seg[row[0]]) || {};
              return (
                <div key={row[0]} className="b-mgr-card" style={CARD}>
                  <div style={{ position: "absolute", top: -40, right: -40, width: 120, height: 120,
                    borderRadius: "50%", background: row[2], opacity: 0.08 }} />
                  <div style={{ fontWeight: "bold", fontSize: 16, color: "#1D2939", marginBottom: 14 }}>{row[3]}</div>
                  <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-0.02em", marginBottom: 12 }}>{money(sv["всего_продано"])}</div>
                  <div style={{ color: "#667085", fontSize: 15, lineHeight: 1.8 }}>
                    Клиентов: <b style={{ color: "#1D2939" }}>{sv["клиентов"] || 0}</b>, сделок: <b style={{ color: "#1D2939" }}>{sv["сделок"] || 0}</b><br />
                    Тарифы: <b style={{ color: "#1D2939" }}>{money(sv["продажи_тарифов"])}</b>, допуслуги: <b style={{ color: "#1D2939" }}>{money(sv["продажи_апсейлов"])}</b><br />
                    Средний чек: <b style={{ color: "#1D2939" }}>{money(sv["средний_чек"])}</b>, комиссия: <b style={{ color: "#12805C" }}>{money(sv["комиссия"])}</b>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      <div style={{ marginTop: 32 }}>
        <h3 style={H}>Мои клиенты</h3>
        <div className="b-mgr-card" style={CARD}>
          {clients.length === 0 ? (
            <div style={{ color: "#667085" }}>Пока никого. Раздайте ссылку из шапки, и клиенты появятся здесь.</div>
          ) : (
            <div style={{ overflowX: "auto", WebkitOverflowScrolling: "touch" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 15, minWidth: 620 }}>
              <thead><tr style={{ textAlign: "left", color: "#667085", fontSize: 13 }}>
                <th style={{ padding: "8px 6px" }}>Клиент</th><th>Тариф</th><th>Принёс</th><th>Пришёл</th><th>Подписка до</th>
              </tr></thead>
              <tbody>
                {clients.map((c: any) => (
                  <tr key={c.account_id} style={{ borderTop: "1px solid #EAECF0" }}>
                    <td style={{ padding: "10px 6px" }}>{c.email}
                      <div style={{ color: "#98A2B3", fontSize: 13 }}>{c.account_id}</div></td>
                    <td>{c["тариф"]}</td>
                    <td>{money(c["принёс"])}</td>
                    <td>{c["пришёл"] ? String(c["пришёл"]).slice(0, 10) : "-"}</td>
                    <td>{c["подписка_до"] ? String(c["подписка_до"]).slice(0, 10) : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>
      </div>

      {price ? (
        <div style={{ marginTop: 32 }}>
          <h3 style={H}>Прайс-лист</h3>

          <div style={{ fontWeight: 700, fontSize: 16, color: "#1D2939", marginBottom: 4 }}>Частнику — свой бизнес</div>
          <div style={{ color: "#667085", fontSize: 15, marginBottom: 14 }}>{price["частник_подзаголовок"]}</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: 14, marginBottom: 28 }}>
            {(price["частник_тарифы"] || []).map((t: any, i: number) => {
              const grad = i === 0 ? "linear-gradient(135deg,#4C8DFF,#2F6FED)" : "linear-gradient(135deg,#7C5CFC,#5B3FD9)";
              const col = i === 0 ? "#2F6FED" : "#5B3FD9";
              return (
                <div key={i} className="b-mgr-card" style={CARD}>
                  <div style={{ position: "absolute", top: -40, right: -40, width: 120, height: 120,
                    borderRadius: "50%", background: grad, opacity: 0.08 }} />
                  <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                    <div className="b-mgr-ic" style={{ width: 44, height: 44, borderRadius: "50%", background: grad,
                      display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18 }}>{i === 0 ? "\u{1F680}" : "\u{1F451}"}</div>
                    <div style={{ fontWeight: "bold", fontSize: 16, color: "#1D2939" }}>{t["название"]}</div>
                  </div>
                  <div style={{ fontWeight: 800, fontSize: 26, letterSpacing: "-0.02em" }}>{money(t["цена"])}</div>
                  <div style={{ color: "#667085", fontSize: 14, marginBottom: 12 }}>за {t["период"]}</div>
                  {(t["входит"] || []).map((x: string, j: number) => (
                    <div key={j} style={{ color: "#475467", fontSize: 15, marginBottom: 3 }}>
                      <span style={{ color: col, fontWeight: 700 }}>+</span> {x}
                    </div>
                  ))}
                  <div style={{ background: col + "18", color: col, fontSize: 13, fontWeight: 600,
                    borderRadius: 12, padding: "8px 12px", marginTop: 12 }}>Кому: {t["кому"]}</div>
                </div>
              );
            })}
          </div>

          <div style={{ fontWeight: 700, fontSize: 16, color: "#1D2939", margin: "0 0 4px" }}>Допуслуги для частника</div>
          <div style={{ color: "#667085", fontSize: 15, marginBottom: 14 }}>Докупаются сверх тарифа и сгорают вместе с ним.</div>
          <div style={{ marginBottom: 28 }}>
            <ExtraPicker price={price} cat={extraCatPriv} setCat={setExtraCatPriv} keys={{ b: "частник_докупка_баннеров", m: "частник_баннеры_магазина", a: "частник_ии_менеджер" }} />
          </div>

          <div style={{ fontWeight: 700, fontSize: 16, color: "#1D2939", marginBottom: 4 }}>Агентству и маркетологу — несколько клиентов</div>
          <div style={{ color: "#667085", fontSize: 15, marginBottom: 14 }}>{price["агентство_подзаголовок"]}</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 250px), 1fr))", gap: 14, marginBottom: 14 }}>
            {price["форматы"].map((pf: any, i: number) => {
              const grads = ["linear-gradient(135deg,#4C8DFF,#2F6FED)", "linear-gradient(135deg,#3DBE93,#12805C)",
                "linear-gradient(135deg,#F79009,#E8850B)", "linear-gradient(135deg,#7C5CFC,#5B3FD9)"];
              const cols = ["#2F6FED", "#12805C", "#E8850B", "#5B3FD9"];
              const ics = ["1", "2-4", "5-14", "15+"];
              return (
                <div key={i} className="b-mgr-card" style={CARD_C}>
                  <div style={{ position: "absolute", top: -40, right: -40, width: 120, height: 120,
                    borderRadius: "50%", background: grads[i % 4], opacity: 0.08 }} />
                  <div className="b-mgr-ic" style={{ width: 64, height: 64, borderRadius: "50%", background: grads[i % 4],
                    display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, fontWeight: 800,
                    color: "#fff", marginBottom: 16, boxShadow: "0 8px 20px " + cols[i % 4] + "40" }}>{ics[i % 4]}</div>
                  <div style={{ fontWeight: "bold", fontSize: 16, color: "#1D2939", marginBottom: 8, minHeight: 40 }}>{pf["формат"]}</div>
                  <div style={{ fontWeight: 800, fontSize: 26, letterSpacing: "-0.02em", marginBottom: 6 }}>{money(pf["тариф_1_за_аккаунт"])}</div>
                  <div style={{ color: "#667085", fontSize: 14, marginBottom: 4 }}>за каждый аккаунт в месяц</div>
                  <div style={{ color: "#667085", fontSize: 14, marginBottom: 12 }}>Тариф 2 — {money(pf["тариф_2_за_аккаунт"])} за аккаунт</div>
                  <div style={{ background: cols[i % 4] + "18", color: cols[i % 4], fontSize: 13, fontWeight: "bold",
                    borderRadius: 20, padding: "5px 14px" }}>{pf["пример"]}</div>
                </div>
              );
            })}
          </div>
          <div style={{ fontWeight: 700, fontSize: 16, color: "#1D2939", margin: "28px 0 4px" }}>Допуслуги и лимиты</div>
          <div style={{ color: "#667085", fontSize: 15, marginBottom: 14 }}>Действуют одинаково для частников и для агентств. Докупаются сверх тарифа.</div>
          <ExtraPicker price={price} cat={extraCat} setCat={setExtraCat} keys={{ b: "докупка_баннеров", m: "баннеры_для_магазина", a: "ии_менеджер" }} />
        </div>
      ) : null}

      {know ? (
        <div style={{ marginTop: 32 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 18 }}>
            {["Заходы в звонке", "Что отвечать клиенту", "О БОРИСЕ"].map((t, i) => (
              <button key={i} onClick={() => setSecTab(i)} style={{ background: secTab === i ? "#2F6FED" : "#FFFFFF", border: "1.5px solid " + (secTab === i ? "#2F6FED" : "#E3E7F0"), color: secTab === i ? "#FFFFFF" : "#475467", borderRadius: 10, padding: "11px 20px", fontSize: 15, fontWeight: 700, cursor: "pointer" }}>{t}</button>
            ))}
            <div style={{ flex: 1 }} />
            <button onClick={() => setCallOpen(v => !v)} style={{ background: callOpen ? "#2F6FED" : "#FFFFFF", border: "1.5px solid #2F6FED", color: callOpen ? "#FFFFFF" : "#2F6FED", borderRadius: 10, padding: "11px 18px", fontSize: 15, fontWeight: 700, cursor: "pointer" }}>📞 {callOpen ? "Закрыть звонок" : "Режим звонка"}</button>
          </div>
          <div style={{ display: "none" }}>
            <h3 style={H}>Заходы в холодном звонке</h3>
            <button onClick={() => setCallOpen(v => !v)} style={{ background: callOpen ? "#2F6FED" : "#FFFFFF", border: "1.5px solid #2F6FED", color: callOpen ? "#FFFFFF" : "#2F6FED", borderRadius: 10, padding: "8px 14px", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>📞 {callOpen ? "Закрыть звонок" : "Режим звонка"}</button>
          </div>
          {callOpen ? (
            <div className="b-mgr-card" style={{ ...CARD, borderColor: "#2F6FED", marginBottom: 16 }}>
              <div style={{ fontWeight: 700, fontSize: 16, color: "#1D2939", marginBottom: 14 }}>Запись разговора</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))", gap: 12, marginBottom: 12 }}>
                <input value={cf.company} onChange={e => setCf({ ...cf, company: e.target.value })} placeholder="Компания" style={INP} />
                <input value={cf.contact_name} onChange={e => setCf({ ...cf, contact_name: e.target.value })} placeholder="ФИО контакта" style={INP} />
                <select value={cf.contact_role} onChange={e => setCf({ ...cf, contact_role: e.target.value })} style={INP}>
                  <option>ЛПР</option><option>ЛВР</option><option>секретарь</option><option>непонятно</option>
                </select>
                <input value={cf.business} onChange={e => setCf({ ...cf, business: e.target.value })} placeholder="Чем занимаются" style={INP} />
              </div>
              <textarea value={cf.comment} onChange={e => setCf({ ...cf, comment: e.target.value })} placeholder="Комментарий — что говорил, что болит" style={{ ...INP, width: "100%", minHeight: 70, marginBottom: 12, boxSizing: "border-box" }} />
              <textarea value={cf.agreement} onChange={e => setCf({ ...cf, agreement: e.target.value })} placeholder="О чём договорились" style={{ ...INP, width: "100%", minHeight: 50, marginBottom: 12, boxSizing: "border-box" }} />
              <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ fontSize: 14, color: "#667085" }}>Напомнить:</span>
                <input type="date" value={cf.remind_at} onChange={e => setCf({ ...cf, remind_at: e.target.value })} style={{ ...INP, width: 170 }} />
                <button onClick={saveCall} style={{ background: "#2F6FED", color: "#FFF", border: "none", borderRadius: 10, padding: "11px 20px", fontSize: 15, fontWeight: 700, cursor: "pointer" }}>Сохранить</button>
                {callMsg ? <span style={{ color: "#12805C", fontSize: 14 }}>{callMsg}</span> : null}
              </div>
            </div>
          ) : null}
          <div className="b-mgr-card" style={{ ...CARD, display: secTab === 0 ? "block" : "none" }}>
            <select value={coldCat} onChange={e => setColdCat(Number(e.target.value))} style={{ ...INP, marginBottom: 16, minWidth: 260 }}>
              <option value={0}>Выйти на решающего</option>
              <option value={1}>Понять, что у них сейчас</option>
              <option value={2}>Нащупать боль</option>
              <option value={3}>Закрыть на следующий шаг</option>
            </select>
            {[
              { t: "Выйти на решающего", c: [
                "Подскажите, кто у вас ведёт Авито — вы сами или отдельный человек?",
                "С кем можно обсудить продвижение на Авито? Хочу сразу к нужному человеку.",
                "Я по вопросу вашего аккаунта на Авито — соедините с тем, кто им занимается.",
                "А решение по рекламному бюджету за кем — за вами?",
              ]},
              { t: "Понять, что у них сейчас", c: [
                "Вы на Расширенном тарифе Авито работаете или на Максимальном?",
                "Сколько аккаунтов ведёте? Один или несколько?",
                "Сколько примерно объявлений висит сейчас?",
                "Кто пишет тексты и делает фото — сами или подрядчик?",
              ]},
              { t: "Нащупать боль", c: [
                "Что сейчас в Авито больше всего не устраивает?",
                "Сколько времени в неделю уходит на объявления?",
                "Знаете, во сколько вам обходится один контакт?",
                "Объявления поднимаете вручную или как-то автоматизировано?",
                "Бывает, что объявления улетают вниз и вы это замечаете не сразу?",
              ]},
              { t: "Закрыть на следующий шаг", c: [
                "Что хотелось бы решить в Авито в первую очередь?",
                "Давайте я покажу на вашем же аккаунте, что можно улучшить — 15 минут.",
                "Скину ссылку, посмотрите сами. Когда удобно созвониться и обсудить?",
              ]},
            ].map((g: any, i: number) => (
              <div key={i} style={{ display: i === coldCat ? "block" : "none" }}>
                {g.c.map((line: string, j: number) => (
                  <div key={j} style={{ padding: "9px 0", borderTop: j ? "1px solid #EAECF0" : "none", fontSize: 15, color: "#1D2939" }}>— {line}</div>
                ))}
              </div>
            ))}
            <div style={{ background: "#F2F6FF", borderRadius: 10, padding: "12px 14px", fontSize: 14, color: "#475467" }}>
              Не продавайте в первом звонке. Задача — понять, кто ведёт Авито, что болит и договориться о показе.
            </div>
          </div>

          <div style={{ display: "none" }}><h3 style={H}>Что отвечать клиенту</h3></div>
          <div className="b-mgr-card" style={{ ...CARD, display: secTab === 1 ? "block" : "none" }}>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
              <select value={kTopic} onChange={e => { setKTopic(e.target.value); setKOpen(null); }}
                style={{ padding: "11px 14px", border: "1.5px solid #E3E7F0", borderRadius: 10, minWidth: 220, fontSize: 15 }}>
                {Object.keys(know).map((t: string) => <option key={t} value={t}>{t}</option>)}
              </select>
              <input placeholder="Поиск по всем темам" value={kSearch} onChange={e => setKSearch(e.target.value)}
                style={{ padding: "11px 14px", border: "1.5px solid #E3E7F0", borderRadius: 10, minWidth: 260, flex: 1, fontSize: 15 }} />
            </div>
            {(kSearch
              ? Object.keys(know).reduce((acc: any[], t: string) => acc.concat(know[t].map((x: any) => ({ ...x, _t: t }))), [])
                  .filter((x: any) => (x["вопрос"] + " " + x["ответ"]).toLowerCase().indexOf(kSearch.toLowerCase()) >= 0)
              : (know[kTopic] || []).map((x: any) => ({ ...x, _t: kTopic }))
            ).map((x: any) => (
              <div key={x.id} style={{ borderTop: "1px solid #EAECF0" }}>
                <button onClick={() => setKOpen(kOpen === x.id ? null : x.id)}
                  style={{ width: "100%", textAlign: "left", background: "none", border: "none", cursor: "pointer",
                    padding: "14px 0", fontSize: 16, fontWeight: 600, color: "#1D2939", display: "flex",
                    justifyContent: "space-between", gap: 12 }}>
                  <span>{kSearch ? <span style={{ color: "#98A2B3", fontWeight: 400 }}>{x._t} · </span> : null}{x["вопрос"]}</span>
                  <span style={{ color: "#2F6FED" }}>{kOpen === x.id ? "-" : "+"}</span>
                </button>
                {kOpen === x.id ? (
                  <div style={{ paddingBottom: 16, color: "#475467", fontSize: 15, lineHeight: 1.7 }}>{x["ответ"]}</div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {knowProd ? (
        <div style={{ marginTop: 32 }}>
          <div style={{ display: "none" }}><h3 style={H}>О БОРИСЕ: боль клиента и решение</h3></div>
          <div className="b-mgr-card" style={{ ...CARD, display: secTab === 2 ? "block" : "none" }}>
            <select value={pTopic} onChange={e => { setPTopic(e.target.value); setPOpen(null); }}
              style={{ padding: "11px 14px", border: "1.5px solid #E3E7F0", borderRadius: 10,
                minWidth: 280, fontSize: 15, marginBottom: 16 }}>
              {Object.keys(knowProd).map((t: string) => <option key={t} value={t}>{t}</option>)}
            </select>
            {(knowProd[pTopic] || []).map((x: any) => (
              <div key={x.id} style={{ borderTop: "1px solid #EAECF0" }}>
                <button onClick={() => setPOpen(pOpen === x.id ? null : x.id)}
                  style={{ width: "100%", textAlign: "left", background: "none", border: "none", cursor: "pointer",
                    padding: "14px 0", fontSize: 16, fontWeight: 600, color: "#1D2939", display: "flex",
                    justifyContent: "space-between", gap: 12 }}>
                  <span>{x["вопрос"]}</span>
                  <span style={{ color: "#2F6FED" }}>{pOpen === x.id ? "-" : "+"}</span>
                </button>
                {pOpen === x.id ? (
                  <div style={{ paddingBottom: 16, color: "#475467", fontSize: 15, lineHeight: 1.7 }}>{x["ответ"]}</div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
