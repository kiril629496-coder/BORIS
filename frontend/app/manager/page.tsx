"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import GlobalLogoutButton from "../components/shell/GlobalLogoutButton";

const INP: any = { border: "1.5px solid var(--boris-border)", borderRadius: 10, padding: "11px 14px", fontSize: 15, background: "#FFFFFF", color: "var(--boris-text)" };
const CARD: any = { background: "#FFFFFF", border: "1px solid var(--boris-border)", borderRadius: 16,
  padding: 24, position: "relative", overflow: "hidden", height: "100%" };
const CARD_C: any = { ...CARD, display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center" };
const GRID: any = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))", gap: 14, alignItems: "stretch" };

function Stat({ icon, grad, color, name, value, badge }: any) {
  return (
    <div data-boris-g33-card="1" data-boris-g33="manager" className="b-mgr-card boris-g1-card" style={CARD_C}>
      <div data-boris-g33-card="1" style={{ position: "absolute", top: -40, right: -40, width: 120, height: 120,
        borderRadius: "50%", background: grad, opacity: 0.08 }} />
      <div data-boris-g33-card="1" className="b-mgr-ic" style={{ width: 64, height: 64, borderRadius: "50%", background: grad, display: "flex",
        alignItems: "center", justifyContent: "center", fontSize: 28, marginBottom: 16,
        boxShadow: "0 8px 20px " + color + "40" }}>{icon}</div>
      <div className="boris-f3-fd3e9712aab69b">{name}</div>
      <div className="boris-f3-1260ee1f38a927">{value}</div>
      {badge ? <div data-boris-g33-card="1" style={{ background: color + "18", color: color, fontSize: 13, fontWeight: "bold",
        borderRadius: 20, padding: "5px 14px" }}>{badge}</div> : null}
    </div>
  );
}

function ExtraPicker({ price, cat, setCat, keys }: any) {
  const money = (n: any) => (Number(n) || 0).toLocaleString("ru-RU") + " \u20BD";
  const row: any = (i: number) => ({ color: "var(--boris-text-2)", fontSize: 15, padding: "10px 0",
    borderTop: i ? "1px solid var(--boris-border)" : "none" });
  return (
    <div data-boris-g33-card="1" className="b-mgr-card boris-f3-25916e188e3e1b">
      <select value={cat} onChange={e => setCat(e.target.value)}
 className="boris-f3-fcd5646b69b4d7">
        <option>Докупка баннеров</option>
        <option>Баннеры для магазина</option>
        <option>ИИ-менеджер</option>
        <option>Лимиты и правила</option>
      </select>
      {cat === "Докупка баннеров" ? (
        <div>{price[keys.b].map((x: any, i: number) => (
          <div key={i} style={row(i)}>{x["пакет"]} — <b className="boris-f3-872175c4f12c7c">{money(x["цена"])}</b>
            <span className="boris-f3-6052e51bb1b504"> ({x["примечание"]})</span></div>
        ))}</div>
      ) : null}
      {cat === "Баннеры для магазина" ? (
        <div>{price[keys.m].map((x: any, i: number) => (
          <div key={i} style={row(i)}>{x["пакет"]} — <b className="boris-f3-872175c4f12c7c">{money(x["цена"])}</b></div>
        ))}</div>
      ) : null}
      {cat === "ИИ-менеджер" ? (
        <div>{price[keys.a].map((x: any, i: number) => (
          <div key={i} style={row(i)}>{x["пакет"]} — <b className="boris-f3-872175c4f12c7c">{money(x["цена"])}</b></div>
        ))}</div>
      ) : null}
      {cat === "Лимиты и правила" ? (
        <div>
          {Object.keys(price["лимиты"]).map((k: string, i: number) => (
            <div key={k} style={row(i)}><b className="boris-f3-872175c4f12c7c">{k}</b>: {price["лимиты"][k]}</div>
          ))}
          <div className="boris-f3-e770d66f7a3753">{price["важно"]}</div>
          <div className="boris-f3-823be2d22dcda5">Пробный период: {price["пробный_период"]}</div>
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

  if (err) return <div className="boris-f3-ddfbf53220e06f">{err}</div>;
  if (!data) return <div className="boris-f3-854e69659fafc8">Загрузка…</div>;

  const next = data["до_следующего_грейда"];
  const f = data["воронка"] || {};
  const clients = data["клиенты"] || [];
  const pct = (v: any) => (v === null || v === undefined) ? "—" : v + "%";
  const H: any = { fontSize: 18, color: "var(--boris-text)", margin: "0 0 14px", fontWeight: 700 };

  return (
    <div className="b-mgr-wrap boris-f3-ea3876d0be0290">

      <div className="b-mgr-hero boris-f3-486ad0bc16a1ae">
        <div>
          <div className="boris-f3-27e5ed277ad43a">Кабинет менеджера</div>
          <div className="boris-f3-9520ae863aaed5">Ваша ссылка для регистрации клиентов</div>
          <div className="boris-f3-5fa1a763790fe5">
            <code className="boris-f3-46cd72f873a643">{data["ссылка"]}</code>
            <button className="b-mgr-btn boris-f3-3ae2f5b23fef91" onClick={() => { navigator.clipboard.writeText(data["ссылка"] || ""); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
>{copied ? "Скопировано" : "Скопировать"}</button>
          </div>
        </div>
        <div className="boris-f3-9b75d3888176d0">
          <div className="boris-f3-70e4bdb187a357">К выплате за {data["период"]}</div>
          <div className="boris-f3-cc27b84b8ff56c">{money(data["итого_к_выплате"])}</div>
          <div className="boris-f3-0ceb81a1551f37">оклад {money(data["оклад"])} + {data["процент"]}%</div>
          <button className="b-mgr-btn boris-f3-af552203ec1f86" onClick={() => router.push("/manager/profile")}
>Данные обо мне</button>
          <button className="b-mgr-btn boris-f3-ea27570bb0de91" onClick={() => router.push("/manager/clients")}
>Мои клиенты</button>
          <GlobalLogoutButton />
        </div>
      </div>

      <div className="boris-f3-293242ee3e224b">
        <div style={GRID}>
          <Stat icon="👥" grad="linear-gradient(135deg,var(--boris-blue),var(--boris-blue))" color="var(--boris-blue)"
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

      <div className="boris-f3-d0ca3f1f6af79c">
        <h3 data-boris-g33-heading="1" style={H}>Воронка за {data["период"]}</h3>
        <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={CARD}>
          <div className="boris-f3-a8a3e8571e1455">
            <span className="boris-f3-8e1a4be9b97bb8">Сегодня сделано:</span>
            <input placeholder="звонков" value={calls} onChange={e => setCalls(e.target.value)}
  className="boris-f3-36249bbd23438f"/>
            <input placeholder="разговоров" value={talks} onChange={e => setTalks(e.target.value)}
  className="boris-f3-c874606585dc81"/>
            <button onClick={saveActivity} className="boris-f3-63b25169227c59">Записать</button>
            {actMsg ? <span className="boris-f3-c109a5f8c65cc1">{actMsg}</span> : null}
          </div>
          <div className="boris-f3-cc71bbd6bd0c5c">
            {[["Звонков", f["звонков"], null], ["Разговоров", f["разговоров"], f["звонок_в_разговор"]],
              ["Регистраций", f["зарегистрировались"], f["разговор_в_регистрацию"]],
              ["Оплатили", f["оплатили"], f["регистрация_в_оплату"]]].map((row: any) => (
              <div key={row[0]} className="boris-f3-7d04c04c85cc93">
                <div className="boris-f3-498e792763208b">{row[0]}</div>
                <div className="boris-f3-05435878a10f53">{row[1] || 0}</div>
                {row[2] !== null ? <div className="boris-f3-a9aeb20b07feaa">конверсия {pct(row[2])}</div> : null}
              </div>
            ))}
          </div>
          <div className="boris-f3-49638e70b639db">
            <span>Средний чек: <b className="boris-f3-872175c4f12c7c">{money(f["средний_чек"])}</b></span>
            <span>С одного звонка: <b className="boris-f3-c109a5f8c65cc1">{money(f["рублей_с_звонка"])}</b></span>
            <span>Звонок в оплату: <b className="boris-f3-872175c4f12c7c">{pct(f["звонок_в_оплату"])}</b></span>
          </div>
        </div>
      </div>

      {seg ? (
        <div className="boris-f3-d0ca3f1f6af79c">
          <h3 data-boris-g33-heading="1" style={H}>Частники и агентства</h3>
          <div className="boris-f3-a2a14d10f2fdff">
            {[["частник", "var(--boris-blue)", "linear-gradient(135deg,var(--boris-blue),var(--boris-blue))", "Частники (1 аккаунт)"],
              ["агентство", "#5B3FD9", "linear-gradient(135deg,#7C5CFC,#5B3FD9)", "Агентства (2 и больше)"]].map((row: any) => {
              const sv = (seg && seg[row[0]]) || {};
              return (
                <div data-boris-g33-card="1" key={row[0]} className="b-mgr-card boris-g1-card" style={CARD}>
                  <div data-boris-g33-card="1" style={{ position: "absolute", top: -40, right: -40, width: 120, height: 120,
                    borderRadius: "50%", background: row[2], opacity: 0.08 }} />
                  <div className="boris-f3-714e7f77aeb751">{row[3]}</div>
                  <div className="boris-f3-dbb5e664f64eb2">{money(sv["всего_продано"])}</div>
                  <div className="boris-f3-046669cdb71dec">
                    Клиентов: <b className="boris-f3-872175c4f12c7c">{sv["клиентов"] || 0}</b>, сделок: <b className="boris-f3-872175c4f12c7c">{sv["сделок"] || 0}</b><br />
                    Тарифы: <b className="boris-f3-872175c4f12c7c">{money(sv["продажи_тарифов"])}</b>, допуслуги: <b className="boris-f3-872175c4f12c7c">{money(sv["продажи_апсейлов"])}</b><br />
                    Средний чек: <b className="boris-f3-872175c4f12c7c">{money(sv["средний_чек"])}</b>, комиссия: <b className="boris-f3-c109a5f8c65cc1">{money(sv["комиссия"])}</b>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      <div className="boris-f3-d0ca3f1f6af79c">
        <h3 data-boris-g33-heading="1" style={H}>Мои клиенты</h3>
        <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={CARD}>
          {clients.length === 0 ? (
            <div className="boris-f3-1b02eb650d31b1">Пока никого. Раздайте ссылку из шапки, и клиенты появятся здесь.</div>
          ) : (
            <div className="boris-f3-acdd942cc0f8aa">
            <table className="boris-f3-e406f3818e42b4">
              <thead><tr className="boris-f3-66510198203b81">
                <th className="boris-f3-ed7579f15d16d2">Клиент</th><th>Тариф</th><th>Принёс</th><th>Пришёл</th><th>Подписка до</th>
              </tr></thead>
              <tbody>
                {clients.map((c: any) => (
                  <tr key={c.account_id} className="boris-f3-7df93423a4b246">
                    <td className="boris-f3-08770b5ea674a8">{c.email}
                      <div className="boris-f3-acca32db2b4a84">{c.account_id}</div></td>
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
        <div className="boris-f3-d0ca3f1f6af79c">
          <h3 data-boris-g33-heading="1" style={H}>Прайс-лист</h3>

          <div className="boris-f3-79fd38373d1921">Частнику — свой бизнес</div>
          <div className="boris-f3-c2f06c3f770f6c">{price["частник_подзаголовок"]}</div>
          <div className="boris-f3-b77e5f7b73acba">
            {(price["частник_тарифы"] || []).map((t: any, i: number) => {
              const grad = i === 0 ? "linear-gradient(135deg,var(--boris-blue),var(--boris-blue))" : "linear-gradient(135deg,#7C5CFC,#5B3FD9)";
              const col = i === 0 ? "var(--boris-blue)" : "#5B3FD9";
              return (
                <div data-boris-g33-card="1" key={i} className="b-mgr-card boris-g1-card" style={CARD}>
                  <div data-boris-g33-card="1" style={{ position: "absolute", top: -40, right: -40, width: 120, height: 120,
                    borderRadius: "50%", background: grad, opacity: 0.08 }} />
                  <div className="boris-f3-6fa11126873364">
                    <div data-boris-g33-card="1" className="b-mgr-ic" style={{ width: 44, height: 44, borderRadius: "50%", background: grad,
                      display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18 }}>{i === 0 ? "\u{1F680}" : "\u{1F451}"}</div>
                    <div className="boris-f3-e4f73db1f37ba3">{t["название"]}</div>
                  </div>
                  <div className="boris-f3-7a1d228e8806fc">{money(t["цена"])}</div>
                  <div className="boris-f3-0622f8a0c53da4">за {t["период"]}</div>
                  {(t["входит"] || []).map((x: string, j: number) => (
                    <div key={j} className="boris-f3-fda31a772615c0">
                      <span style={{ color: col, fontWeight: 700 }}>+</span> {x}
                    </div>
                  ))}
                  <div data-boris-g33-card="1" style={{ background: col + "18", color: col, fontSize: 13, fontWeight: 600,
                    borderRadius: 12, padding: "8px 12px", marginTop: 12 }}>Кому: {t["кому"]}</div>
                </div>
              );
            })}
          </div>

          <div className="boris-f3-f164d38dde4259">Допуслуги для частника</div>
          <div className="boris-f3-c2f06c3f770f6c">Докупаются сверх тарифа и сгорают вместе с ним.</div>
          <div className="boris-f3-789a0b989d3ed6">
            <ExtraPicker price={price} cat={extraCatPriv} setCat={setExtraCatPriv} keys={{ b: "частник_докупка_баннеров", m: "частник_баннеры_магазина", a: "частник_ии_менеджер" }} />
          </div>

          <div className="boris-f3-79fd38373d1921">Агентству и маркетологу — несколько клиентов</div>
          <div className="boris-f3-c2f06c3f770f6c">{price["агентство_подзаголовок"]}</div>
          <div className="boris-f3-d173dfd9f68048">
            {price["форматы"].map((pf: any, i: number) => {
              const grads = ["linear-gradient(135deg,var(--boris-blue),var(--boris-blue))", "linear-gradient(135deg,#3DBE93,#12805C)",
                "linear-gradient(135deg,#F79009,#E8850B)", "linear-gradient(135deg,#7C5CFC,#5B3FD9)"];
              const cols = ["var(--boris-blue)", "#12805C", "#E8850B", "#5B3FD9"];
              const ics = ["1", "2-4", "5-14", "15+"];
              return (
                <div data-boris-g33-card="1" key={i} className="b-mgr-card boris-g1-card" style={CARD_C}>
                  <div data-boris-g33-card="1" style={{ position: "absolute", top: -40, right: -40, width: 120, height: 120,
                    borderRadius: "50%", background: grads[i % 4], opacity: 0.08 }} />
                  <div data-boris-g33-card="1" className="b-mgr-ic" style={{ width: 64, height: 64, borderRadius: "50%", background: grads[i % 4],
                    display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, fontWeight: 800,
                    color: "#fff", marginBottom: 16, boxShadow: "0 8px 20px " + cols[i % 4] + "40" }}>{ics[i % 4]}</div>
                  <div className="boris-f3-fd3e9712aab69b">{pf["формат"]}</div>
                  <div className="boris-f3-aabda2850dc9b3">{money(pf["тариф_1_за_аккаунт"])}</div>
                  <div className="boris-f3-d2bd8ae4ffd390">за каждый аккаунт в месяц</div>
                  <div className="boris-f3-0622f8a0c53da4">Тариф 2 — {money(pf["тариф_2_за_аккаунт"])} за аккаунт</div>
                  <div data-boris-g33-card="1" style={{ background: cols[i % 4] + "18", color: cols[i % 4], fontSize: 13, fontWeight: "bold",
                    borderRadius: 20, padding: "5px 14px" }}>{pf["пример"]}</div>
                </div>
              );
            })}
          </div>
          <div className="boris-f3-fc59cf674f2462">Допуслуги и лимиты</div>
          <div className="boris-f3-c2f06c3f770f6c">Действуют одинаково для частников и для агентств. Докупаются сверх тарифа.</div>
          <ExtraPicker price={price} cat={extraCat} setCat={setExtraCat} keys={{ b: "докупка_баннеров", m: "баннеры_для_магазина", a: "ии_менеджер" }} />
        </div>
      ) : null}

      {know ? (
        <div className="boris-f3-d0ca3f1f6af79c">
          <div className="boris-f3-201358ce506bb4">
            {["Заходы в звонке", "Что отвечать клиенту", "О БОРИСЕ"].map((t, i) => (
              <button data-boris-g33-interactive="1" data-boris-g33-card="1" key={i} onClick={() => setSecTab(i)} style={{ background: secTab === i ? "var(--boris-blue)" : "#FFFFFF", border: "1.5px solid " + (secTab === i ? "var(--boris-blue)" : "var(--boris-border)"), color: secTab === i ? "#FFFFFF" : "var(--boris-text-2)", borderRadius: 10, padding: "11px 20px", fontSize: 15, fontWeight: 700, cursor: "pointer" }}>{t}</button>
            ))}
            <div  className="boris-f3-6253876a649a63"/>
            <button data-boris-g33-interactive="1" data-boris-g33-card="1" onClick={() => setCallOpen(v => !v)} style={{ background: callOpen ? "var(--boris-blue)" : "#FFFFFF", border: "1.5px solid var(--boris-blue)", color: callOpen ? "#FFFFFF" : "var(--boris-blue)", borderRadius: 10, padding: "11px 18px", fontSize: 15, fontWeight: 700, cursor: "pointer" }}>📞 {callOpen ? "Закрыть звонок" : "Режим звонка"}</button>
          </div>
          <div className="boris-f3-d0466aa33fa806">
            <h3 data-boris-g33-heading="1" style={H}>Заходы в холодном звонке</h3>
            <button data-boris-g33-interactive="1" data-boris-g33-card="1" onClick={() => setCallOpen(v => !v)} style={{ background: callOpen ? "var(--boris-blue)" : "#FFFFFF", border: "1.5px solid var(--boris-blue)", color: callOpen ? "#FFFFFF" : "var(--boris-blue)", borderRadius: 10, padding: "8px 14px", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>📞 {callOpen ? "Закрыть звонок" : "Режим звонка"}</button>
          </div>
          {callOpen ? (
            <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={{ ...CARD, borderColor: "var(--boris-blue)", marginBottom: 16 }}>
              <div className="boris-f3-04372cc2eead79">Запись разговора</div>
              <div className="boris-f3-8acc68105e7c33">
                <input value={cf.company} onChange={e => setCf({ ...cf, company: e.target.value })} placeholder="Компания" style={INP} />
                <input value={cf.contact_name} onChange={e => setCf({ ...cf, contact_name: e.target.value })} placeholder="ФИО контакта" style={INP} />
                <select value={cf.contact_role} onChange={e => setCf({ ...cf, contact_role: e.target.value })} style={INP}>
                  <option>ЛПР</option><option>ЛВР</option><option>секретарь</option><option>непонятно</option>
                </select>
                <input value={cf.business} onChange={e => setCf({ ...cf, business: e.target.value })} placeholder="Чем занимаются" style={INP} />
              </div>
              <textarea value={cf.comment} onChange={e => setCf({ ...cf, comment: e.target.value })} placeholder="Комментарий — что говорил, что болит" style={{ ...INP, width: "100%", minHeight: 70, marginBottom: 12, boxSizing: "border-box" }} />
              <textarea value={cf.agreement} onChange={e => setCf({ ...cf, agreement: e.target.value })} placeholder="О чём договорились" style={{ ...INP, width: "100%", minHeight: 50, marginBottom: 12, boxSizing: "border-box" }} />
              <div className="boris-f3-49b71788a2c08a">
                <span className="boris-f3-1b3d61260084bb">Напомнить:</span>
                <input type="date" value={cf.remind_at} onChange={e => setCf({ ...cf, remind_at: e.target.value })} style={{ ...INP, width: 170 }} />
                <button onClick={saveCall} className="boris-f3-52a799d0d2d6f6">Сохранить</button>
                {callMsg ? <span className="boris-f3-75fac1667e1767">{callMsg}</span> : null}
              </div>
            </div>
          ) : null}
          <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={{ ...CARD, display: secTab === 0 ? "block" : "none" }}>
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
                  <div key={j} style={{ padding: "9px 0", borderTop: j ? "1px solid var(--boris-border)" : "none", fontSize: 15, color: "var(--boris-text)" }}>— {line}</div>
                ))}
              </div>
            ))}
            <div className="boris-f3-f2ee2cd1dc9395">
              Не продавайте в первом звонке. Задача — понять, кто ведёт Авито, что болит и договориться о показе.
            </div>
          </div>

          <div className="boris-f3-d0466aa33fa806"><h3 data-boris-g33-heading="1" style={H}>Что отвечать клиенту</h3></div>
          <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={{ ...CARD, display: secTab === 1 ? "block" : "none" }}>
            <div className="boris-f3-102e838d8d6cf4">
              <select value={kTopic} onChange={e => { setKTopic(e.target.value); setKOpen(null); }}
 className="boris-f3-a5911d9e95d99f">
                {Object.keys(know).map((t: string) => <option key={t} value={t}>{t}</option>)}
              </select>
              <input placeholder="Поиск по всем темам" value={kSearch} onChange={e => setKSearch(e.target.value)}
  className="boris-f3-c4e36e0f470920"/>
            </div>
            {(kSearch
              ? Object.keys(know).reduce((acc: any[], t: string) => acc.concat(know[t].map((x: any) => ({ ...x, _t: t }))), [])
                  .filter((x: any) => (x["вопрос"] + " " + x["ответ"]).toLowerCase().indexOf(kSearch.toLowerCase()) >= 0)
              : (know[kTopic] || []).map((x: any) => ({ ...x, _t: kTopic }))
            ).map((x: any) => (
              <div key={x.id} className="boris-f3-7df93423a4b246">
                <button onClick={() => setKOpen(kOpen === x.id ? null : x.id)}
 className="boris-f3-a4cefd84cbd495">
                  <span>{kSearch ? <span className="boris-f3-9d6308b3aa9bf2">{x._t} · </span> : null}{x["вопрос"]}</span>
                  <span className="boris-f3-4860cba009c490">{kOpen === x.id ? "-" : "+"}</span>
                </button>
                {kOpen === x.id ? (
                  <div className="boris-f3-b98b83c9cc7799">{x["ответ"]}</div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {knowProd ? (
        <div className="boris-f3-d0ca3f1f6af79c">
          <div className="boris-f3-d0466aa33fa806"><h3 data-boris-g33-heading="1" style={H}>О БОРИСЕ: боль клиента и решение</h3></div>
          <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={{ ...CARD, display: secTab === 2 ? "block" : "none" }}>
            <select value={pTopic} onChange={e => { setPTopic(e.target.value); setPOpen(null); }}
 className="boris-f3-fcd5646b69b4d7">
              {Object.keys(knowProd).map((t: string) => <option key={t} value={t}>{t}</option>)}
            </select>
            {(knowProd[pTopic] || []).map((x: any) => (
              <div key={x.id} className="boris-f3-7df93423a4b246">
                <button onClick={() => setPOpen(pOpen === x.id ? null : x.id)}
 className="boris-f3-a4cefd84cbd495">
                  <span>{x["вопрос"]}</span>
                  <span className="boris-f3-4860cba009c490">{pOpen === x.id ? "-" : "+"}</span>
                </button>
                {pOpen === x.id ? (
                  <div className="boris-f3-b98b83c9cc7799">{x["ответ"]}</div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
