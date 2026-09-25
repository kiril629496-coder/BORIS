"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

const CARD: any = { background: "#FFFFFF", border: "1px solid var(--boris-border)", borderRadius: 16,
  padding: 24, position: "relative", overflow: "hidden" };
const H: any = { fontSize: 18, color: "var(--boris-text)", margin: "0 0 14px", fontWeight: 700 };
const INP: any = { padding: "10px 12px", border: "1.5px solid var(--boris-border)", borderRadius: 10, fontSize: 15, width: "100%" };
const STATUSES = ["новый", "в работе", "в диалоге", "квалифицирован", "целевое действие", "пробный", "оплатил", "сделка", "отказ"];
const SCOL: any = { "новый": "var(--boris-text-2)", "в работе": "var(--boris-blue)", "в диалоге": "var(--boris-blue)", "квалифицирован": "#7C3AED", "целевое действие": "#0E7490", "пробный": "#E8850B", "оплатил": "#12805C", "сделка": "#12805C", "отказ": "#B42318" };
const SOURCE_LABEL: Record<string,string> = { telegram_reactivation:"Telegram · реактивация", telegram_prospecting:"Telegram · новый лид", email_outreach:"Email · рассылка" };
const FILTER_SOURCE_KEY = "boris_manager_clients_source";
const FILTER_QUERY_KEY = "boris_manager_clients_query";

export default function ClientsPage() {
  const router = useRouter();
  const [list, setList] = useState<any>(null);
  const [cal, setCal] = useState<any>(null);
  const [q, setQ] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [edit, setEdit] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [noteFor, setNoteFor] = useState<number | null>(null);
  const [noteText, setNoteText] = useState("");
  const [noteDate, setNoteDate] = useState("");
  const loadSeq = useRef(0);
  const initialLoadDone = useRef(false);
  const leadsAbort = useRef<AbortController | null>(null);
  const queryRef = useRef("");
  const sourceFilterRef = useRef("");

  const auth = () => {
    const t = localStorage.getItem("boris_token");
    if (!t) { router.push("/login"); return null; }
    return { Authorization: "Bearer " + t };
  };

  const load = async (query?: string, source?: string) => {
    const requestedQuery = String(query || "");
    const requestedSource = String(source || "");
    // A late/stale reload must never erase the user's current filter/search.
    // Event handlers update refs synchronously before starting their request.
    if (initialLoadDone.current &&
        (requestedQuery !== queryRef.current || requestedSource !== sourceFilterRef.current)) {
      return;
    }
    const seq = ++loadSeq.current;
    const h = auth(); if (!h) return;
    leadsAbort.current?.abort();
    const controller = new AbortController();
    leadsAbort.current = controller;
    try {
      const params = new URLSearchParams();
      if (requestedQuery) params.set("q", requestedQuery);
      if (requestedSource) params.set("source", requestedSource);
      const r = await fetch("/api/manager/leads" + (params.toString() ? "?" + params.toString() : ""), { headers: h, signal: controller.signal });
      if (r.status === 401) { router.push("/login"); return; }
      const j = await r.json();
      if (j.status === "ok" &&
          seq === loadSeq.current &&
          requestedQuery === queryRef.current &&
          requestedSource === sourceFilterRef.current) {
        setList(j);
      }
    } catch (e:any) {
      if (e?.name !== "AbortError") console.warn("manager leads load failed");
    }
    if (seq !== loadSeq.current) return;
    try {
      const c = await fetch("/api/manager/calendar", { headers: h });
      const cj = await c.json();
      if (cj.status === "ok" && seq === loadSeq.current) setCal(cj);
    } catch (e) {}
  };

  useEffect(() => {
    if (initialLoadDone.current) return;
    initialLoadDone.current = true;
    let savedQuery = "";
    let savedSource = "";
    try {
      savedQuery = sessionStorage.getItem(FILTER_QUERY_KEY) || "";
      savedSource = sessionStorage.getItem(FILTER_SOURCE_KEY) || "";
    } catch {}
    queryRef.current = savedQuery;
    sourceFilterRef.current = savedSource;
    setQ(savedQuery);
    setSourceFilter(savedSource);
    load(savedQuery, savedSource);
  }, []);

  const save = async () => {
    const h = auth(); if (!h) return;
    setMsg("");
    const r = await fetch("/api/manager/leads/save", {
      method: "POST", headers: { ...h, "Content-Type": "application/json" },
      body: JSON.stringify(edit),
    });
    const j = await r.json();
    if (j.status === "ok") {
      setEdit(null);
      setMsg("Сохранено");
      load(queryRef.current, sourceFilterRef.current);
    }
    else setMsg(j.message || "Ошибка");
  };

  const addNote = async (leadId: number) => {
    const h = auth(); if (!h) return;
    if (!noteText.trim()) return;
    await fetch("/api/manager/leads/note", {
      method: "POST", headers: { ...h, "Content-Type": "application/json" },
      body: JSON.stringify({ lead_id: leadId, text: noteText, remind_at: noteDate || null, kind: noteDate ? "reminder" : "note" }),
    });
    setNoteText("");
    setNoteDate("");
    setNoteFor(null);
    load(queryRef.current, sourceFilterRef.current);
  };

  const f = (k: string, v: any) => setEdit({ ...(edit || {}), [k]: v });

  if (!list) return <div data-boris-g33="manager-clients" className="boris-f3-854e69659fafc8">Загрузка…</div>;

  return (
    <div className="b-mgr-wrap boris-f3-687e573a4cf13b">

      <div className="b-mgr-hero boris-f3-9a771c9d7c218b">
        <div>
          <div className="boris-f3-05435878a10f53">Мои клиенты</div>
          <div className="boris-f3-70e4bdb187a357">Всего карточек: {list["всего"]}</div>
        </div>
        <div className="boris-f3-31e4e2a25260f1">
          <button onClick={() => setEdit({ status: "новый" })}
 className="boris-f3-f5d1cc89f97e1f">+ Новый клиент</button>
          <button className="b-mgr-btn boris-f3-a9d1549ee372ad" onClick={() => router.push("/manager")}
>Назад</button>
        </div>
      </div>

      {(list["источники"] || []).length ? (
        <div style={{marginBottom:24}}>
          <h3 data-boris-g33-heading="1" style={H}>Откуда приходят лиды</h3>
          {sourceFilter ? <div style={{display:"flex",alignItems:"center",gap:10,margin:"-4px 0 12px",fontSize:13,color:"var(--boris-text-2)"}}>
            <span>Показан канал: <b>{SOURCE_LABEL[sourceFilter] || sourceFilter}</b></span>
            <button type="button" onClick={()=>{
              sourceFilterRef.current="";
              try { sessionStorage.removeItem(FILTER_SOURCE_KEY); } catch {}
              setSourceFilter("");
              load(queryRef.current,"");
            }} style={{border:0,background:"#EEF4FF",color:"#315BEA",borderRadius:999,padding:"6px 10px",fontWeight:700,cursor:"pointer"}}>Показать все</button>
          </div> : null}
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:12}}>
            {(list["источники"] || []).map((s:any) => (
              <button type="button" data-boris-g33-card="1" data-manager-source-card={s.source} className="b-mgr-card boris-g1-card" key={s.source}
                onClick={() => {
                  const next=sourceFilterRef.current===s.source?"":s.source;
                  sourceFilterRef.current=next;
                  try {
                    if (next) sessionStorage.setItem(FILTER_SOURCE_KEY,next);
                    else sessionStorage.removeItem(FILTER_SOURCE_KEY);
                  } catch {}
                  setSourceFilter(next);
                  load(queryRef.current,next);
                }}
                style={{...CARD,padding:18,minHeight:142,textAlign:"left",cursor:"pointer",width:"100%",border:sourceFilter===s.source?"2px solid #315BEA":"1px solid var(--boris-border)",boxShadow:sourceFilter===s.source?"0 10px 24px rgba(49,91,234,.12)":"none"}}>
                <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"flex-start"}}>
                  <div style={{fontSize:15,fontWeight:800,color:"var(--boris-text)"}}>{s.label || SOURCE_LABEL[s.source] || s.source}</div>
                  {Number(s.open_tasks||0)>0 ? <span style={{padding:"3px 8px",borderRadius:999,background:"#FFF4E5",color:"#A15C00",fontSize:11,fontWeight:800}}>Нужно действие: {s.open_tasks}</span> : null}
                </div>
                <div style={{fontSize:32,fontWeight:850,color:"var(--boris-text)",marginTop:14,lineHeight:1}}>{s.total||0}</div>
                <div style={{fontSize:12,color:"var(--boris-text-3)",marginTop:5}}>лидов в CRM</div>
                <div style={{display:"flex",gap:10,flexWrap:"wrap",marginTop:12,fontSize:12,color:"var(--boris-text-2)"}}>
                  <span>В диалоге: <b>{s.dialog||0}</b></span>
                  <span>Квалифицировано: <b>{s.qualified||0}</b></span>
                  <span>Целевое действие: <b>{s.target_action||0}</b></span>
                  {Number(s.won||0)>0 ? <span>Сделки: <b>{s.won}</b></span> : null}
                </div>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {cal && (cal["просрочено"].length || cal["сегодня"].length) ? (
        <div className="boris-f3-a097a3f007033c">
          <h3 data-boris-g33-heading="1" style={H}>На сегодня</h3>
          <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={CARD}>
            {[["просрочено", "#B42318"], ["сегодня", "#E8850B"], ["дальше", "var(--boris-text-2)"]].map(([k, c]: any) => (
              (cal[k] || []).length ? (
                <div key={k} className="boris-f3-4636770b4968a8">
                  <div style={{ color: c, fontWeight: 700, fontSize: 15, marginBottom: 6, textTransform: "capitalize" }}>{k} — {cal[k].length}</div>
                  {cal[k].map((n: any) => (
                    <div key={n.id} className="boris-f3-647cb672b0d8fb">
                      <b className="boris-f3-872175c4f12c7c">{n["кто"]}</b>
                      {n["телефон"] ? <span className="boris-f3-1288a6f277a856"> · {n["телефон"]}</span> : null}
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
        <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={{ ...CARD, marginBottom: 24 }}>
          <div className="boris-f3-5e49d339e9a851">{edit.id ? "Карточка клиента" : "Новый клиент"}</div>
          <div className="boris-f3-ef731a5e7e9ead">
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
          <div className="boris-f3-a4d35ca1a66fdd">
            <button onClick={save} className="boris-f3-63b25169227c59">Сохранить</button>
            <button onClick={() => setEdit(null)} className="boris-f3-c1cbaf0440a915">Отмена</button>
            {msg ? <span className="boris-f3-c109a5f8c65cc1">{msg}</span> : null}
          </div>
        </div>
      ) : null}

      <div className="boris-f3-e9dbc0e8a1b554">
        <input data-manager-lead-search="1" style={{ ...INP, maxWidth: 380 }} placeholder="Поиск: имя, телефон, компания, комментарий"
          value={q} onChange={e => {
            const value=e.target.value;
            queryRef.current=value;
            try {
              if (value) sessionStorage.setItem(FILTER_QUERY_KEY,value);
              else sessionStorage.removeItem(FILTER_QUERY_KEY);
            } catch {}
            setQ(value);
            load(value, sourceFilterRef.current);
          }} />
        {Object.keys(list["по_статусам"] || {}).map((s: string) => (
          <span data-boris-g33-card="1" key={s} style={{ background: (SCOL[s] || "var(--boris-text-2)") + "18", color: SCOL[s] || "var(--boris-text-2)",
            borderRadius: 20, padding: "8px 14px", fontSize: 14, fontWeight: 600 }}>{s}: {list["по_статусам"][s]}</span>
        ))}
      </div>

      {list["клиенты"].length === 0 ? (
        <div data-boris-g33-card="1" className="b-mgr-card boris-g1-card" style={CARD}>
          <div className="boris-f3-1b02eb650d31b1">Пока пусто. Нажмите «Новый клиент» и записывайте всех, кому звоните — даже если он ещё не зарегистрировался.</div>
        </div>
      ) : (
        <div className="boris-f3-0853fb9f167dff">
          {list["клиенты"].map((c: any) => (
            <div data-boris-g33-card="1" data-manager-lead-card="1" key={c.id} className="b-mgr-card boris-g1-card" style={CARD}>
              <div className="boris-f3-c31e076174f0bf">
                <div className="boris-f3-b103a187982ddf">
                  {[c["имя"], c["фамилия"]].filter(Boolean).join(" ") || c["компания"] || "Без имени"}
                  {c["источник"] ? <span style={{display:"inline-block",marginLeft:8,padding:"3px 8px",borderRadius:999,background:"#EEF4FF",color:"#315BEA",fontSize:11,fontWeight:700,verticalAlign:"middle"}}>{SOURCE_LABEL[c["источник"]] || c["источник"]}</span> : null}
                </div>
                <span data-boris-g33-card="1" style={{ background: (SCOL[c["статус"]] || "var(--boris-text-2)") + "18", color: SCOL[c["статус"]] || "var(--boris-text-2)",
                  borderRadius: 20, padding: "4px 12px", fontSize: 13, fontWeight: 600, whiteSpace: "nowrap" }}>{c["статус"]}</span>
              </div>
              <div className="boris-f3-bbfb08d1f13b68">
                {c["должность"] ? <div>{c["должность"]}{c["компания"] ? ", " + c["компания"] : ""}</div> : (c["компания"] ? <div>{c["компания"]}</div> : null)}
                {c["телефон"] ? <div><b>{c["телефон"]}</b></div> : null}
                {c["почта"] ? <div>{c["почта"]}</div> : null}
                {c["сайт"] ? <div><a href={c["сайт"].startsWith("http") ? c["сайт"] : "https://" + c["сайт"]} target="_blank" className="boris-f3-4860cba009c490">{c["сайт"]}</a></div> : null}
                {c["чем_занимается"] ? <div className="boris-f3-1b02eb650d31b1">{c["чем_занимается"]}</div> : null}
                {c["комментарий"] ? <div className="boris-f3-f7e53b52ed6108">{c["комментарий"]}</div> : null}
              </div>
              {(c["напоминания"] || []).length ? (
                <div className="boris-f3-d7b0ae1490c612">
                  {c["напоминания"].slice(0, 4).map((n: any) => (
                    <div key={n.id} style={{ fontSize: 14, color: n["сделано"] ? "var(--boris-text-3)" : "var(--boris-text-2)", padding: "3px 0" }}>
                      {n["напомнить"] ? <b className="boris-f3-883e2277a6a9a4">{String(n["напомнить"]).slice(0, 10)} </b> : null}{n["текст"]}
                    </div>
                  ))}
                </div>
              ) : null}
              <div className="boris-f3-67260b8ef6e68a">
                <button onClick={() => setEdit(c.id ? { ...c, name: c["имя"], surname: c["фамилия"], position: c["должность"],
                  phone: c["телефон"], email: c["почта"], company: c["компания"], site: c["сайт"],
                  niche: c["чем_занимается"], comment: c["комментарий"], status: c["статус"] } : c)}
 className="boris-f3-b17009aa7970d8">Изменить</button>
                <button onClick={() => setNoteFor(noteFor === c.id ? null : c.id)}
 className="boris-f3-be01b8fd3446bd">Запись / напоминание</button>
              </div>
              {noteFor === c.id ? (
                <div className="boris-f3-80fa4688aa4f42">
                  <input style={{ ...INP, flex: 1, minWidth: 160 }} placeholder="Что записать" value={noteText} onChange={e => setNoteText(e.target.value)} />
                  <input style={{ ...INP, width: 160 }} type="date" value={noteDate} onChange={e => setNoteDate(e.target.value)} />
                  <button onClick={() => addNote(c.id)} className="boris-f3-d48619c5be0e2e">ОК</button>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
