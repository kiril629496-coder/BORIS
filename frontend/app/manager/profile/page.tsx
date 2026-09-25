"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

const CARD: any = { background: "#FFFFFF", border: "1px solid var(--boris-border)", borderRadius: 16,
  padding: 24, position: "relative", overflow: "hidden" };
const H: any = { fontSize: 18, color: "var(--boris-text)", margin: "0 0 14px", fontWeight: 700 };
const INP: any = { padding: "10px 12px", border: "1.5px solid var(--boris-border)", borderRadius: 10, fontSize: 15, width: "100%" };

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

  if (!p) return <div data-boris-g33="manager-profile" className="boris-f3-854e69659fafc8">Загрузка…</div>;

  return (
    <div className="b-mgr-wrap boris-f3-687e573a4cf13b">

      <div className="b-mgr-hero boris-f3-9a771c9d7c218b">
        <div>
          <div className="boris-f3-05435878a10f53">Данные обо мне</div>
          <div className="boris-f3-70e4bdb187a357">
            {nda && nda["принято"] ? "Коммерческая тайна принята" : "Коммерческая тайна не принята"}
          </div>
        </div>
        <button className="b-mgr-btn boris-f3-a9d1549ee372ad" onClick={() => router.push("/manager")}
>Назад</button>
      </div>

      {nda && !nda["принято"] ? (
        <div className="boris-f3-a097a3f007033c">
          <h3 data-boris-g33-heading="1" style={{ ...H, color: "#B42318" }}>Сначала примите коммерческую тайну</h3>
          <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={CARD}>
            <div ref={box} onScroll={onScroll}
 className="boris-f3-5604e561d9b53e">
              {nda["текст"]}
            </div>
            <div className="boris-f3-5c604f80dd30b9">
              Версия {nda["версия"]}. {readEnd ? "Документ прочитан до конца." : "Пролистайте документ до конца."}
            </div>
            <label style={{ display: "flex", gap: 10, alignItems: "flex-start", marginTop: 14, cursor: readEnd ? "pointer" : "default" }}>
              <input type="checkbox" checked={agree} disabled={!readEnd} onChange={e => setAgree(e.target.checked)}
  className="boris-f3-1de64d931e4ff7"/>
              <span style={{ fontSize: 15, color: readEnd ? "var(--boris-text)" : "var(--boris-text-3)" }}>
                Я полностью прочитал соглашение, понимаю его содержание и обязуюсь соблюдать.
                Мне известно об ответственности за разглашение, в том числе по ст. 183 УК РФ.
              </span>
            </label>
            <button data-boris-g33-interactive="1" data-boris-g33-card="1" className="b-mgr-btn" onClick={acceptNda} disabled={!readEnd || !agree}
              style={{ marginTop: 14, background: (readEnd && agree) ? "var(--boris-blue)" : "var(--boris-text-3)", color: "#fff",
                border: "none", borderRadius: 10, padding: "11px 22px",
                cursor: (readEnd && agree) ? "pointer" : "default", fontWeight: 700 }}>Принимаю</button>
          </div>
        </div>
      ) : null}

      {nda && nda["принято"] ? (
        <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={{ ...CARD, marginBottom: 24, background: "#ECFDF3", border: "1px solid #ABEFC6" }}>
          <div className="boris-f3-0e17be08ccf945">
            Коммерческая тайна принята {nda["когда"] ? String(nda["когда"]).replace("T", " ").slice(0, 16) : ""} · версия {nda["версия"]}
          </div>
          <div className="boris-f3-39f4993ad5fb2e">
            Дата, время и IP зафиксированы. Обязательства действуют весь срок работы и три года после.
          </div>
        </div>
      ) : null}

      <h3 data-boris-g33-heading="1" style={H}>Личные данные</h3>
      <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={{ ...CARD, marginBottom: 24 }}>
        <div className="boris-f3-ef731a5e7e9ead">
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

      <h3 data-boris-g33-heading="1" style={H}>Паспортные данные</h3>
      <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={{ ...CARD, marginBottom: 24 }}>
        <div className="boris-f3-0622f8a0c53da4">
          Хранятся в зашифрованном виде. Скан загружать не нужно.
        </div>
        <div className="boris-f3-07738f8c98ab1d">
          <input style={INP} placeholder="Серия" value={p["паспорт_серия"] || ""} onChange={e => f("паспорт_серия", e.target.value)} />
          <input style={INP} placeholder="Номер" value={p["паспорт_номер"] || ""} onChange={e => f("паспорт_номер", e.target.value)} />
          <input style={INP} type="date" value={p["паспорт_дата_выдачи"] || ""} onChange={e => f("паспорт_дата_выдачи", e.target.value)} />
          <input style={INP} placeholder="Код подразделения" value={p["паспорт_код"] || ""} onChange={e => f("паспорт_код", e.target.value)} />
        </div>
        <input style={{ ...INP, marginTop: 12 }} placeholder="Кем выдан"
          value={p["паспорт_кем_выдан"] || ""} onChange={e => f("паспорт_кем_выдан", e.target.value)} />
      </div>

      <div className="boris-f3-059df1ee8dead5">
        <button className="b-mgr-btn boris-f3-f6bf24ae7396c1" onClick={save}>Сохранить данные</button>
        {msg ? <span style={{ color: msg === "Сохранено" ? "#12805C" : "#B42318" }}>{msg}</span> : null}
      </div>

      <h3 data-boris-g33-heading="1" style={H}>Близкие родственники</h3>
      <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={CARD}>
        <div className="boris-f3-4beb2b8821a308">
          Данные другого человека можно внести только с его согласия — он должен знать, что вы их передаёте.
        </div>
        {rel.map(r => (
          <div key={r.id} className="boris-f3-7bce70397eb6c4">
            <div className="boris-f3-e49c04bf11ac05">
              <b>{r["кто"]}</b> · {r["фио"]}{r["телефон"] ? " · " + r["телефон"] : ""}
              <span className="boris-f3-68f2158b741975"> · согласие получено{r["согласие_дата"] ? " " + String(r["согласие_дата"]).slice(0, 10) : ""}</span>
            </div>
            <button onClick={() => delRel(r.id)} className="boris-f3-407dba4efe75ad">Удалить</button>
          </div>
        ))}
        <div className="boris-f3-9ea84cffc5c555">
          <select style={INP} value={nr.relation} onChange={e => setNr({ ...nr, relation: e.target.value })}>
            <option>супруг(а)</option><option>отец</option><option>мать</option>
            <option>сын</option><option>дочь</option><option>экстренный контакт</option>
          </select>
          <input style={INP} placeholder="ФИО" value={nr.fio} onChange={e => setNr({ ...nr, fio: e.target.value })} />
          <input style={INP} placeholder="Телефон" value={nr.phone} onChange={e => setNr({ ...nr, phone: e.target.value })} />
        </div>
        <label className="boris-f3-1c7c57b544bc18">
          <input type="checkbox" checked={nr.consent} onChange={e => setNr({ ...nr, consent: e.target.checked })}
  className="boris-f3-1de64d931e4ff7"/>
          <span className="boris-f3-e49c04bf11ac05">Подтверждаю, что получил согласие этого человека на передачу его данных.</span>
        </label>
        <button data-boris-g33-interactive="1" data-boris-g33-card="1" className="b-mgr-btn" onClick={addRel} disabled={!nr.consent}
          style={{ marginTop: 12, background: nr.consent ? "var(--boris-blue)" : "var(--boris-text-3)", color: "#fff", border: "none",
            borderRadius: 10, padding: "10px 20px", cursor: nr.consent ? "pointer" : "default", fontWeight: 700 }}>Добавить</button>
      </div>
    </div>
  );
}
