"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

const CARD: any = { background: "#FFFFFF", border: "1px solid #E3E7F0", borderRadius: 16,
  padding: 24, position: "relative", overflow: "hidden" };
const H: any = { fontSize: 18, color: "#1D2939", margin: "0 0 14px", fontWeight: 700 };
const INP: any = { padding: "10px 12px", border: "1.5px solid #E3E7F0", borderRadius: 10, fontSize: 15, width: "100%" };

export default function ProfilePage() {
  const router = useRouter();
  const [p, setP] = useState<any>(null);
  const [rel, setRel] = useState<any[]>([]);
  const [nda, setNda] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [readEnd, setReadEnd] = useState(false);
  const [agree, setAgree] = useState(false);
  const [nr, setNr] = useState<any>({ relation: "супруг(а)", fio: "", phone: "", consent: false });
  const box = useRef<any>(null);

  const auth = () => {
    const t = localStorage.getItem("boris_token");
    if (!t) { router.push("/login"); return null; }
    return { Authorization: "Bearer " + t };
  };

  const load = async () => {
    const h = auth(); if (!h) return;
    const r = await fetch("/api/manager/profile", { headers: h });
    if (r.status === 401) { router.push("/login"); return; }
    const j = await r.json();
    if (j.status === "ok") { setP(j["профиль"] || {}); setRel(j["родственники"] || []); }
    const n = await fetch("/api/manager/nda", { headers: h });
    const nj = await n.json();
    if (nj.status === "ok") setNda(nj);
  };

  useEffect(() => { load(); }, []);

  const f = (k: string, v: any) => setP({ ...(p || {}), [k]: v });

  const save = async () => {
    const h = auth(); if (!h) return;
    setMsg("");
    const r = await fetch("/api/manager/profile/save", {
      method: "POST", headers: { ...h, "Content-Type": "application/json" }, body: JSON.stringify(p) });
    const j = await r.json();
    setMsg(j.status === "ok" ? "Сохранено" : (j.message || "Ошибка"));
  };

  const addRel = async () => {
    const h = auth(); if (!h) return;
    setMsg("");
    const r = await fetch("/api/manager/profile/relative", {
      method: "POST", headers: { ...h, "Content-Type": "application/json" }, body: JSON.stringify(nr) });
    const j = await r.json();
    if (j.status === "ok") { setNr({ relation: "супруг(а)", fio: "", phone: "", consent: false }); load(); }
    else setMsg(j.message || "Ошибка");
  };

  const delRel = async (id: number) => {
    const h = auth(); if (!h) return;
    await fetch("/api/manager/profile/relative", {
      method: "POST", headers: { ...h, "Content-Type": "application/json" },
      body: JSON.stringify({ delete_id: id }) });
    load();
  };

  const acceptNda = async () => {
    const h = auth(); if (!h) return;
    const r = await fetch("/api/manager/nda/accept", {
      method: "POST", headers: { ...h, "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }) });
    const j = await r.json();
    if (j.status === "ok") load();
    else setMsg(j.message || "Ошибка");
  };

  const onScroll = () => {
    const el = box.current;
    if (el && el.scrollTop + el.clientHeight >= el.scrollHeight - 40) setReadEnd(true);
  };

  if (!p) return <div style={{ padding: 40, color: "#667085" }}>Загрузка…</div>;

  return (
    <div className="b-mgr-wrap" style={{ maxWidth: "100%", padding: "clamp(16px,3vw,32px) clamp(12px,2.5vw,36px) 60px",
      fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif", color: "#101828" }}>

      <div className="b-mgr-hero" style={{ background: "linear-gradient(135deg,#2F6FED,#1E4FD8)", borderRadius: 20,
        padding: "28px 32px", color: "#fff", marginBottom: 24, display: "flex",
        justifyContent: "space-between", alignItems: "center", gap: 20, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-0.02em" }}>Данные обо мне</div>
          <div style={{ opacity: 0.85, fontSize: 15 }}>
            {nda && nda["принято"] ? "Коммерческая тайна принята" : "Коммерческая тайна не принята"}
          </div>
        </div>
        <button className="b-mgr-btn" onClick={() => router.push("/manager")}
          style={{ border: "1.5px solid rgba(255,255,255,0.6)", background: "transparent", color: "#fff",
            borderRadius: 10, padding: "10px 18px", cursor: "pointer", fontWeight: 600 }}>Назад</button>
      </div>

      {nda && !nda["принято"] ? (
        <div style={{ marginBottom: 24 }}>
          <h3 style={{ ...H, color: "#B42318" }}>Сначала примите коммерческую тайну</h3>
          <div className="b-mgr-card" style={CARD}>
            <div ref={box} onScroll={onScroll}
              style={{ maxHeight: 340, overflowY: "auto", background: "#F8FAFF", borderRadius: 12,
                padding: 18, whiteSpace: "pre-wrap", fontSize: 15, lineHeight: 1.7, color: "#344054" }}>
              {nda["текст"]}
            </div>
            <div style={{ color: "#667085", fontSize: 14, marginTop: 10 }}>
              Версия {nda["версия"]}. {readEnd ? "Документ прочитан до конца." : "Пролистайте документ до конца."}
            </div>
            <label style={{ display: "flex", gap: 10, alignItems: "flex-start", marginTop: 14, cursor: readEnd ? "pointer" : "default" }}>
              <input type="checkbox" checked={agree} disabled={!readEnd} onChange={e => setAgree(e.target.checked)}
                style={{ marginTop: 4, width: 18, height: 18 }} />
              <span style={{ fontSize: 15, color: readEnd ? "#1D2939" : "#98A2B3" }}>
                Я полностью прочитал соглашение, понимаю его содержание и обязуюсь соблюдать.
                Мне известно об ответственности за разглашение, в том числе по ст. 183 УК РФ.
              </span>
            </label>
            <button className="b-mgr-btn" onClick={acceptNda} disabled={!readEnd || !agree}
              style={{ marginTop: 14, background: (readEnd && agree) ? "#2F6FED" : "#98A2B3", color: "#fff",
                border: "none", borderRadius: 10, padding: "11px 22px",
                cursor: (readEnd && agree) ? "pointer" : "default", fontWeight: 700 }}>Принимаю</button>
          </div>
        </div>
      ) : null}

      {nda && nda["принято"] ? (
        <div className="b-mgr-card" style={{ ...CARD, marginBottom: 24, background: "#ECFDF3", border: "1px solid #ABEFC6" }}>
          <div style={{ color: "#067647", fontWeight: 600 }}>
            Коммерческая тайна принята {nda["когда"] ? String(nda["когда"]).replace("T", " ").slice(0, 16) : ""} · версия {nda["версия"]}
          </div>
          <div style={{ color: "#12805C", fontSize: 14, marginTop: 4 }}>
            Дата, время и IP зафиксированы. Обязательства действуют весь срок работы и три года после.
          </div>
        </div>
      ) : null}

      <h3 style={H}>Личные данные</h3>
      <div className="b-mgr-card" style={{ ...CARD, marginBottom: 24 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%,230px),1fr))", gap: 12 }}>
          <input style={INP} placeholder="Фамилия" value={p["фамилия"] || ""} onChange={e => f("фамилия", e.target.value)} />
          <input style={INP} placeholder="Имя" value={p["имя"] || ""} onChange={e => f("имя", e.target.value)} />
          <input style={INP} placeholder="Отчество" value={p["отчество"] || ""} onChange={e => f("отчество", e.target.value)} />
          <input style={INP} type="date" value={p["дата_рождения"] || ""} onChange={e => f("дата_рождения", e.target.value)} />
          <input style={INP} placeholder="Город" value={p["город"] || ""} onChange={e => f("город", e.target.value)} />
          <input style={INP} placeholder="Телефон основной" value={p["телефон"] || ""} onChange={e => f("телефон", e.target.value)} />
          <input style={INP} placeholder="Телефон второй" value={p["телефон2"] || ""} onChange={e => f("телефон2", e.target.value)} />
          <input style={INP} placeholder="Личная почта" value={p["почта"] || ""} onChange={e => f("почта", e.target.value)} />
        </div>
        <textarea style={{ ...INP, marginTop: 12, minHeight: 70 }} placeholder="Ссылки на соцсети — каждая с новой строки"
          value={p["соцсети"] || ""} onChange={e => f("соцсети", e.target.value)} />
      </div>

      <h3 style={H}>Паспортные данные</h3>
      <div className="b-mgr-card" style={{ ...CARD, marginBottom: 24 }}>
        <div style={{ color: "#667085", fontSize: 14, marginBottom: 12 }}>
          Хранятся в зашифрованном виде. Скан загружать не нужно.
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%,200px),1fr))", gap: 12 }}>
          <input style={INP} placeholder="Серия" value={p["паспорт_серия"] || ""} onChange={e => f("паспорт_серия", e.target.value)} />
          <input style={INP} placeholder="Номер" value={p["паспорт_номер"] || ""} onChange={e => f("паспорт_номер", e.target.value)} />
          <input style={INP} type="date" value={p["паспорт_дата_выдачи"] || ""} onChange={e => f("паспорт_дата_выдачи", e.target.value)} />
          <input style={INP} placeholder="Код подразделения" value={p["паспорт_код"] || ""} onChange={e => f("паспорт_код", e.target.value)} />
        </div>
        <input style={{ ...INP, marginTop: 12 }} placeholder="Кем выдан"
          value={p["паспорт_кем_выдан"] || ""} onChange={e => f("паспорт_кем_выдан", e.target.value)} />
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 28 }}>
        <button className="b-mgr-btn" onClick={save} style={{ background: "#2F6FED", color: "#fff", border: "none",
          borderRadius: 10, padding: "11px 22px", cursor: "pointer", fontWeight: 700 }}>Сохранить данные</button>
        {msg ? <span style={{ color: msg === "Сохранено" ? "#12805C" : "#B42318" }}>{msg}</span> : null}
      </div>

      <h3 style={H}>Близкие родственники</h3>
      <div className="b-mgr-card" style={CARD}>
        <div style={{ color: "#667085", fontSize: 14, marginBottom: 14 }}>
          Данные другого человека можно внести только с его согласия — он должен знать, что вы их передаёте.
        </div>
        {rel.map(r => (
          <div key={r.id} style={{ display: "flex", justifyContent: "space-between", gap: 10,
            padding: "10px 0", borderTop: "1px solid #EAECF0", flexWrap: "wrap" }}>
            <div style={{ fontSize: 15 }}>
              <b>{r["кто"]}</b> · {r["фио"]}{r["телефон"] ? " · " + r["телефон"] : ""}
              <span style={{ color: "#12805C", fontSize: 13 }}> · согласие получено{r["согласие_дата"] ? " " + String(r["согласие_дата"]).slice(0, 10) : ""}</span>
            </div>
            <button onClick={() => delRel(r.id)} style={{ background: "#fff", border: "1.5px solid #F04438",
              color: "#B42318", borderRadius: 10, padding: "6px 14px", cursor: "pointer", fontSize: 14 }}>Удалить</button>
          </div>
        ))}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%,200px),1fr))", gap: 12, marginTop: 16 }}>
          <select style={INP} value={nr.relation} onChange={e => setNr({ ...nr, relation: e.target.value })}>
            <option>супруг(а)</option><option>отец</option><option>мать</option>
            <option>сын</option><option>дочь</option><option>экстренный контакт</option>
          </select>
          <input style={INP} placeholder="ФИО" value={nr.fio} onChange={e => setNr({ ...nr, fio: e.target.value })} />
          <input style={INP} placeholder="Телефон" value={nr.phone} onChange={e => setNr({ ...nr, phone: e.target.value })} />
        </div>
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", marginTop: 12, cursor: "pointer" }}>
          <input type="checkbox" checked={nr.consent} onChange={e => setNr({ ...nr, consent: e.target.checked })}
            style={{ marginTop: 4, width: 18, height: 18 }} />
          <span style={{ fontSize: 15 }}>Подтверждаю, что получил согласие этого человека на передачу его данных.</span>
        </label>
        <button className="b-mgr-btn" onClick={addRel} disabled={!nr.consent}
          style={{ marginTop: 12, background: nr.consent ? "#2F6FED" : "#98A2B3", color: "#fff", border: "none",
            borderRadius: 10, padding: "10px 20px", cursor: nr.consent ? "pointer" : "default", fontWeight: 700 }}>Добавить</button>
      </div>
    </div>
  );
}
