"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const CARD: any = { background: "#FFFFFF", border: "1px solid #E3E7F0", borderRadius: 16,
  padding: 24, position: "relative", overflow: "hidden" };
const H: any = { fontSize: 18, color: "#1D2939", margin: "0 0 14px", fontWeight: 700 };
const INP: any = { padding: "10px 12px", border: "1.5px solid #E3E7F0", borderRadius: 10, fontSize: 15, width: "100%" };
const STATUSES = ["новый", "в работе", "пробный", "оплатил", "отказ"];
const SCOL: any = { "новый": "#667085", "в работе": "#2F6FED", "пробный": "#E8850B", "оплатил": "#12805C", "отказ": "#B42318" };

export default function ClientsPage() {
  const router = useRouter();
  const [list, setList] = useState<any>(null);
  const [cal, setCal] = useState<any>(null);
  const [q, setQ] = useState("");
  const [edit, setEdit] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [noteFor, setNoteFor] = useState<number | null>(null);
  const [noteText, setNoteText] = useState("");
  const [noteDate, setNoteDate] = useState("");

  const auth = () => {
    const t = localStorage.getItem("boris_token");
    if (!t) { router.push("/login"); return null; }
    return { Authorization: "Bearer " + t };
  };

  const load = async (query?: string) => {
    const h = auth(); if (!h) return;
    try {
      const r = await fetch("/api/manager/leads" + (query ? "?q=" + encodeURIComponent(query) : ""), { headers: h });
      if (r.status === 401) { router.push("/login"); return; }
      const j = await r.json();
      if (j.status === "ok") setList(j);
    } catch (e) {}
    try {
      const c = await fetch("/api/manager/calendar", { headers: h });
      const cj = await c.json();
      if (cj.status === "ok") setCal(cj);
    } catch (e) {}
  };

  useEffect(() => { load(); }, []);

  const save = async () => {
    const h = auth(); if (!h) return;
    setMsg("");
    const r = await fetch("/api/manager/leads/save", {
      method: "POST", headers: { ...h, "Content-Type": "application/json" },
      body: JSON.stringify(edit),
    });
    const j = await r.json();
    if (j.status === "ok") { setEdit(null); setMsg("Сохранено"); load(q); }
    else setMsg(j.message || "Ошибка");
  };

  const addNote = async (leadId: number) => {
    const h = auth(); if (!h) return;
    if (!noteText.trim()) return;
    await fetch("/api/manager/leads/note", {
      method: "POST", headers: { ...h, "Content-Type": "application/json" },
      body: JSON.stringify({ lead_id: leadId, text: noteText, remind_at: noteDate || null, kind: noteDate ? "reminder" : "note" }),
    });
    setNoteText(""); setNoteDate(""); setNoteFor(null); load(q);
  };

  const f = (k: string, v: any) => setEdit({ ...(edit || {}), [k]: v });

  if (!list) return <div style={{ padding: 40, color: "#667085" }}>Загрузка…</div>;

  return (
    <div className="b-mgr-wrap" style={{ maxWidth: "100%", padding: "clamp(16px,3vw,32px) clamp(12px,2.5vw,36px) 60px",
      fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif", color: "#101828" }}>

      <div className="b-mgr-hero" style={{ background: "linear-gradient(135deg,#2F6FED,#1E4FD8)", borderRadius: 20, padding: "28px 32px",
        color: "#fff", marginBottom: 24, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 20, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-0.02em" }}>Мои клиенты</div>
          <div style={{ opacity: 0.85, fontSize: 15 }}>Всего карточек: {list["всего"]}</div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button onClick={() => setEdit({ status: "новый" })}
            style={{ border: "none", background: "#fff", color: "#2F6FED", borderRadius: 10,
              padding: "10px 18px", cursor: "pointer", fontWeight: 700 }}>+ Новый клиент</button>
          <button className="b-mgr-btn" onClick={() => router.push("/manager")}
            style={{ border: "1.5px solid rgba(255,255,255,0.6)", background: "transparent", color: "#fff",
              borderRadius: 10, padding: "10px 18px", cursor: "pointer", fontWeight: 600 }}>Назад</button>
        </div>
      </div>

      {cal && (cal["просрочено"].length || cal["сегодня"].length) ? (
        <div style={{ marginBottom: 24 }}>
          <h3 style={H}>На сегодня</h3>
          <div className="b-mgr-card" style={CARD}>
            {[["просрочено", "#B42318"], ["сегодня", "#E8850B"], ["дальше", "#667085"]].map(([k, c]: any) => (
              (cal[k] || []).length ? (
                <div key={k} style={{ marginBottom: 12 }}>
                  <div style={{ color: c, fontWeight: 700, fontSize: 15, marginBottom: 6, textTransform: "capitalize" }}>{k} — {cal[k].length}</div>
                  {cal[k].map((n: any) => (
                    <div key={n.id} style={{ color: "#475467", fontSize: 15, padding: "6px 0" }}>
                      <b style={{ color: "#1D2939" }}>{n["кто"]}</b>
                      {n["телефон"] ? <span style={{ color: "#98A2B3" }}> · {n["телефон"]}</span> : null}
                      <span> — {n["текст"]}</span>
                      <span style={{ color: c, fontSize: 13 }}> · {String(n["когда"]).slice(0, 10)}</span>
                    </div>
                  ))}
                </div>
              ) : null
            ))}
          </div>
        </div>
      ) : null}

      {edit ? (
        <div className="b-mgr-card" style={{ ...CARD, marginBottom: 24 }}>
          <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 14 }}>{edit.id ? "Карточка клиента" : "Новый клиент"}</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%,230px),1fr))", gap: 12 }}>
            <input style={INP} placeholder="Имя" value={edit.name || ""} onChange={e => f("name", e.target.value)} />
            <input style={INP} placeholder="Фамилия" value={edit.surname || ""} onChange={e => f("surname", e.target.value)} />
            <input style={INP} placeholder="Должность" value={edit.position || ""} onChange={e => f("position", e.target.value)} />
            <input style={INP} placeholder="Телефон" value={edit.phone || ""} onChange={e => f("phone", e.target.value)} />
            <input style={INP} placeholder="Почта" value={edit.email || ""} onChange={e => f("email", e.target.value)} />
            <input style={INP} placeholder="Компания" value={edit.company || ""} onChange={e => f("company", e.target.value)} />
            <input style={INP} placeholder="Сайт" value={edit.site || ""} onChange={e => f("site", e.target.value)} />
            <input style={INP} placeholder="Чем занимается" value={edit.niche || ""} onChange={e => f("niche", e.target.value)} />
            <select style={INP} value={edit.status || "новый"} onChange={e => f("status", e.target.value)}>
              {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <textarea style={{ ...INP, marginTop: 12, minHeight: 80 }} placeholder="Комментарий"
            value={edit.comment || ""} onChange={e => f("comment", e.target.value)} />
          <div style={{ display: "flex", gap: 10, marginTop: 14, alignItems: "center" }}>
            <button onClick={save} style={{ background: "#2F6FED", color: "#fff", border: "none", borderRadius: 10,
              padding: "11px 20px", cursor: "pointer", fontWeight: 700 }}>Сохранить</button>
            <button onClick={() => setEdit(null)} style={{ background: "#fff", border: "1.5px solid #D0D5DD",
              color: "#475467", borderRadius: 10, padding: "11px 20px", cursor: "pointer" }}>Отмена</button>
            {msg ? <span style={{ color: "#12805C" }}>{msg}</span> : null}
          </div>
        </div>
      ) : null}

      <div style={{ display: "flex", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
        <input style={{ ...INP, maxWidth: 380 }} placeholder="Поиск: имя, телефон, компания, комментарий"
          value={q} onChange={e => { setQ(e.target.value); load(e.target.value); }} />
        {Object.keys(list["по_статусам"] || {}).map((s: string) => (
          <span key={s} style={{ background: (SCOL[s] || "#667085") + "18", color: SCOL[s] || "#667085",
            borderRadius: 20, padding: "8px 14px", fontSize: 14, fontWeight: 600 }}>{s}: {list["по_статусам"][s]}</span>
        ))}
      </div>

      {list["клиенты"].length === 0 ? (
        <div className="b-mgr-card" style={CARD}>
          <div style={{ color: "#667085" }}>Пока пусто. Нажмите «Новый клиент» и записывайте всех, кому звоните — даже если он ещё не зарегистрировался.</div>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(min(100%,340px),1fr))", gap: 14 }}>
          {list["клиенты"].map((c: any) => (
            <div key={c.id} className="b-mgr-card" style={CARD}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10, marginBottom: 8 }}>
                <div style={{ fontWeight: 700, fontSize: 16, color: "#1D2939" }}>
                  {[c["имя"], c["фамилия"]].filter(Boolean).join(" ") || c["компания"] || "Без имени"}
                </div>
                <span style={{ background: (SCOL[c["статус"]] || "#667085") + "18", color: SCOL[c["статус"]] || "#667085",
                  borderRadius: 20, padding: "4px 12px", fontSize: 13, fontWeight: 600, whiteSpace: "nowrap" }}>{c["статус"]}</span>
              </div>
              <div style={{ color: "#475467", fontSize: 15, lineHeight: 1.7 }}>
                {c["должность"] ? <div>{c["должность"]}{c["компания"] ? ", " + c["компания"] : ""}</div> : (c["компания"] ? <div>{c["компания"]}</div> : null)}
                {c["телефон"] ? <div><b>{c["телефон"]}</b></div> : null}
                {c["почта"] ? <div>{c["почта"]}</div> : null}
                {c["сайт"] ? <div><a href={c["сайт"].startsWith("http") ? c["сайт"] : "https://" + c["сайт"]} target="_blank" style={{ color: "#2F6FED" }}>{c["сайт"]}</a></div> : null}
                {c["чем_занимается"] ? <div style={{ color: "#667085" }}>{c["чем_занимается"]}</div> : null}
                {c["комментарий"] ? <div style={{ color: "#667085", marginTop: 6 }}>{c["комментарий"]}</div> : null}
              </div>
              {(c["напоминания"] || []).length ? (
                <div style={{ marginTop: 10, borderTop: "1px solid #EAECF0", paddingTop: 10 }}>
                  {c["напоминания"].slice(0, 4).map((n: any) => (
                    <div key={n.id} style={{ fontSize: 14, color: n["сделано"] ? "#98A2B3" : "#475467", padding: "3px 0" }}>
                      {n["напомнить"] ? <b style={{ color: "#E8850B" }}>{String(n["напомнить"]).slice(0, 10)} </b> : null}{n["текст"]}
                    </div>
                  ))}
                </div>
              ) : null}
              <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                <button onClick={() => setEdit(c.id ? { ...c, name: c["имя"], surname: c["фамилия"], position: c["должность"],
                  phone: c["телефон"], email: c["почта"], company: c["компания"], site: c["сайт"],
                  niche: c["чем_занимается"], comment: c["комментарий"], status: c["статус"] } : c)}
                  style={{ background: "#fff", border: "1.5px solid #2F6FED", color: "#2F6FED", borderRadius: 10,
                    padding: "8px 14px", cursor: "pointer", fontWeight: 600, fontSize: 14 }}>Изменить</button>
                <button onClick={() => setNoteFor(noteFor === c.id ? null : c.id)}
                  style={{ background: "#fff", border: "1.5px solid #D0D5DD", color: "#475467", borderRadius: 10,
                    padding: "8px 14px", cursor: "pointer", fontSize: 14 }}>Запись / напоминание</button>
              </div>
              {noteFor === c.id ? (
                <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <input style={{ ...INP, flex: 1, minWidth: 160 }} placeholder="Что записать" value={noteText} onChange={e => setNoteText(e.target.value)} />
                  <input style={{ ...INP, width: 160 }} type="date" value={noteDate} onChange={e => setNoteDate(e.target.value)} />
                  <button onClick={() => addNote(c.id)} style={{ background: "#2F6FED", color: "#fff", border: "none",
                    borderRadius: 10, padding: "10px 16px", cursor: "pointer", fontWeight: 700 }}>ОК</button>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
