"use client";
import { useAccounts } from "../lib/AccountContext";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const T = {
  blue1:"#2F6FED", blue2:"#1E4FD8", purple1:"#7C5CFC", purple2:"#6344E8",
  orange1:"#F79009", orange2:"#E8850B", green1:"#12B76A", green2:"#0E9457",
  ink:"#1D2939", muted:"#667085", line:"#E3E7F0", bg:"#F5F7FB",
};

type Project = any;

export default function SocialPage() {
  const router = useRouter();
  const [account, setAccount] = useState("");
  const accCtx = useAccounts();
  const [projects, setProjects] = useState<Project[]>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState("");
  const [channelTg, setChannelTg] = useState("");
  const [vkOwner, setVkOwner] = useState("");
  const [styleSource, setStyleSource] = useState("");
  const [themes, setThemes] = useState("");
  const [times, setTimes] = useState("");
  const [textPrompt, setTextPrompt] = useState("");
  const [imageSource, setImageSource] = useState("ai");
  const [platforms, setPlatforms] = useState("both");
  const [bannerPrompt, setBannerPrompt] = useState("");
  const [contactTg, setContactTg] = useState("");
  const [contactVk, setContactVk] = useState("");
  const [mode, setMode] = useState("auto");
  const [posts, setPosts] = useState<any[]>([]);
  const [bannerUrls, setBannerUrls] = useState<Record<string, string>>({});
  const [open, setOpen] = useState(false);
  const [runningIds, setRunningIds] = useState<Record<string, boolean>>({});
  const [runLogs, setRunLogs] = useState<Record<string, string>>({});
  const setBusyFor = (pid: string, v: boolean) => setRunningIds((prev) => ({ ...prev, [pid]: v }));
  const setLogFor = (pid: string, v: string) => setRunLogs((prev) => ({ ...prev, [pid]: v }));
  const [vkName, setVkName] = useState("");
  const [checkKind, setCheckKind] = useState("");
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<any>(null);
  const [zoom, setZoom] = useState("");
  const [notice, setNotice] = useState("");
  const [autoHours, setAutoHours] = useState(0);
  const [tzOffset, setTzOffset] = useState(3);
  const [editId, setEditId] = useState("");
  const [editText, setEditText] = useState("");
  const [schedId, setSchedId] = useState("");
  const [schedWhen, setSchedWhen] = useState("");

  const auth = () => {
    const t = typeof window !== "undefined" ? localStorage.getItem("boris_token") : null;
    if (!t) { router.push("/login"); return null; }
    return { Authorization: "Bearer " + t };
  };

  const fillForm = (p: Project | null) => {
    setName(p ? (p.name || "") : "");
    setChannelTg(p ? (p.channel_tg || "") : "");
    setVkOwner(p && p.vk_owner_id != null ? String(p.vk_owner_id) : "");
    setStyleSource(p ? (p.style_source || "") : "");
    setThemes(p ? (p.themes || []).join("\n") : "");
    setTimes(p ? (p.times || []).join(", ") : "");
    setTextPrompt(p ? (p.text_prompt || "") : "");
    setImageSource(p ? (p.image_source || "ai") : "ai");
    setPlatforms(p ? (p.platforms || "both") : "both");
    setBannerPrompt(p ? (p.banner_prompt || "") : "");
    setContactTg(p ? (p.contact_tg || "") : "");
    setContactVk(p ? (p.contact_vk || "") : "");
    setMode(p ? (p.mode || "auto") : "auto");
    setAutoHours(p ? Number(p.auto_publish_hours || 0) : 0);
    setTzOffset(p ? Number(p.tz_offset ?? 3) : 3);
  };

  const load = async (keepId?: string) => {
    const h = auth(); if (!h) return;
    // аккаунт берём из общего контекста, а не читаем localStorage сами
    const acc = accCtx.selectedAccountId || "";
    setAccount(acc);
    try {
      const r = await fetch("/api/posting/projects?account_id=" + encodeURIComponent(acc), { headers: h });
      if (r.status === 401) { router.push("/login"); return; }
      const j = await jsonOrThrow(r);
      const list: Project[] = Array.isArray(j.projects) ? j.projects : [];
      setProjects(list);
      const saved = typeof window !== "undefined" ? localStorage.getItem("boris_social_project") : null;
      const wantId = keepId || selId || saved;
      const sel = list.find((p) => p.id === wantId) || list[0] || null;
      if (sel) {
        setSelId(sel.id); setCreating(false); fillForm(sel);
        if (typeof window !== "undefined") localStorage.setItem("boris_social_project", sel.id);
      }
      else { setSelId(null); }
    } catch (e: any) { setErr(String(e)); }
  };

  // При смене аккаунта сбрасываем данные прошлого и тянем новые:
  // на экране не должен оставаться чужой проект.
  useEffect(function () {
    const next = accCtx.selectedAccountId || "";
    if (!next || next === account) return;
    setAccount(next);
    setProjects([]);
    setSelId(null);
    load();
  }, [accCtx.selectedAccountId]);

  useEffect(() => { load(); }, []);

  const selectProject = (id: string) => {
    if (typeof window !== "undefined") localStorage.setItem("boris_social_project", id);
    const p = projects.find((x) => x.id === id) || null;
    setSelId(id); setCreating(false); setMsg(""); fillForm(p);
  };

  const newProject = () => {
    setSelId(null); setCreating(true); setMsg(""); fillForm(null); setOpen(true);
    setName("Новый проект");
  };

  const saveProject = async () => {
    const h = auth(); if (!h) return;
    setBusy(true); setMsg("");
    try {
      const body: any = {
        account_id: account,
        name: name.trim() || "Проект",
        channel_tg: channelTg.trim(),
        vk_owner_id: vkOwner.trim() || null,
        style_source: styleSource.trim(),
        themes: themes.split("\n").map((x) => x.trim()).filter(Boolean),
        times: times.split(",").map((x) => x.trim()).filter(Boolean),
        text_prompt: textPrompt.trim(),
        image_source: imageSource,
        platforms: platforms,
        banner_prompt: bannerPrompt.trim(),
        contact_tg: contactTg.trim(),
        contact_vk: contactVk.trim(),
        mode: mode,
        auto_publish_hours: autoHours,
        tz_offset: tzOffset,
      };
      if (selId && !creating) body.id = selId;
      const r = await fetch("/api/posting/projects/save", { method:"POST", headers:{ ...h, "Content-Type":"application/json" }, body: JSON.stringify(body) });
      const j = await r.json();
      if (j.status === "ok") { setMsg("✓ Проект сохранён"); await load(j.id); }
      else setMsg(j.message || "Ошибка сохранения");
    } catch (e:any) { setMsg(String(e)); } finally { setBusy(false); }
  };

  const deleteProject = async () => {
    const h = auth(); if (!h || !selId) return;
    if (!confirm("Удалить проект «" + name + "»?")) return;
    setBusy(true); setMsg("");
    try {
      await fetch("/api/posting/projects/delete", { method:"POST", headers:{ ...h, "Content-Type":"application/json" }, body: JSON.stringify({ account_id: account, id: selId }) });
      setSelId(null); await load();
    } catch (e:any) { setMsg(String(e)); } finally { setBusy(false); }
  };

  const startTrial = async () => {
    const h = auth(); if (!h || !selId) { setMsg("Сначала сохраните проект"); return; }
    setBusy(true); setMsg("");
    try {
      const r = await fetch("/api/posting/trial", { method:"POST", headers:{ ...h, "Content-Type":"application/json" }, body: JSON.stringify({ account_id: account, project_id: selId, platforms: "both" }) });
      const j = await r.json();
      setMsg(j.message || j.status || "");
      await load(selId);
    } catch (e:any) { setMsg(String(e)); } finally { setBusy(false); }
  };

  const loadPosts = async (projId: string) => {
    const h = auth(); if (!h) return;
    // аккаунт берём из общего контекста, а не читаем localStorage сами
    const acc = account || accCtx.selectedAccountId || "";
    try {
      const r = await fetch("/api/posting/posts?account_id=" + encodeURIComponent(acc) + "&project_id=" + encodeURIComponent(projId), { headers: h });
      const j = await r.json();
      const all = (j.posts || []);
      setPosts(all);
      const urls: Record<string, string> = {};
      for (const p of all) {
        if (p.banner && !bannerUrls[p.banner]) {
          try {
            const br = await fetch("/api/posting/banner?f=" + encodeURIComponent(p.banner), { headers: h });
            if (br.ok) { const blob = await br.blob(); urls[p.banner] = URL.createObjectURL(blob); }
          } catch (e) {}
        }
      }
      if (Object.keys(urls).length) setBannerUrls((prev) => ({ ...prev, ...urls }));
    } catch (e) {}
  };

  useEffect(() => { if (selId) { loadPosts(selId); resumeRun(selId); } else setPosts([]); }, [selId]);

  const publishPost = async (id: string) => {
    const h = auth(); if (!h) return;
    setBusy(true); setMsg("");
    try {
      const r = await fetch("/api/posting/posts/publish", { method:"POST", headers:{ ...h, "Content-Type":"application/json" }, body: JSON.stringify({ account_id: account, post_id: id }) });
      const j = await r.json();
      const res = j.results || {};
      const where = [res.tg ? "Telegram" : "", res.vk ? "ВКонтакте" : ""].filter(Boolean).join(" и ");
      setNotice(j.status === "ok"
        ? "✅ Пост опубликован" + (where ? " в " + where : "") + " — он ушёл в канал и убран из черновиков"
        : "⚠️ Не удалось опубликовать, попробуйте ещё раз");
      if (selId) await loadPosts(selId);
    } finally { setBusy(false); }
  };

  const removePost = async (id: string) => {
    const h = auth(); if (!h) return;
    setBusy(true);
    try {
      await fetch("/api/posting/posts/delete", { method:"POST", headers:{ ...h, "Content-Type":"application/json" }, body: JSON.stringify({ account_id: account, post_id: id }) });
      if (selId) await loadPosts(selId);
    } finally { setBusy(false); }
  };

  const jsonOrThrow = async (r: Response) => {
    const ct = r.headers.get("content-type") || "";
    if (!ct.includes("application/json")) {
      throw new Error(r.status === 429 ? "Слишком часто — подождите пару секунд" : "Сервер занят, попробуйте ещё раз (" + r.status + ")");
    }
    return await r.json();
  };

  const resumeRun = async (projId: string) => {
    const h = auth(); if (!h || !projId) return;
    try {
      const r = await fetch("/api/posting/run_active?project_id=" + encodeURIComponent(projId), { headers: h });
      const j = await jsonOrThrow(r);
      if (j.job && j.job.job_id) {
        setBusyFor(projId, true);
        setLogFor(projId, j.job.log || "Задача выполняется…");
        pollRun(j.job.job_id, projId, 0);
      }
    } catch (e) {}
  };

  const pollRun = async (jobId: string, projId: string, tries: number) => {
    const h = auth(); if (!h) return;
    try {
      const r = await fetch("/api/posting/run_status?job_id=" + encodeURIComponent(jobId), { headers: h });
      const j = await jsonOrThrow(r);
      setLogFor(projId, j.log || "…");
      if (j.done) {
        setBusyFor(projId, false);
        await load(projId);
        await loadPosts(projId);
        return;
      }
    } catch (e:any) {
      setLogFor(projId, "Задача идёт в фоне, жду ответа сервера…");
    }
    if (tries < 150) setTimeout(() => pollRun(jobId, projId, tries + 1), 2500);
    else setBusyFor(projId, false);
  };

  const runNow = async () => {
    const h = auth(); if (!h || !selId) { return; }
    const projId = selId;
    setBusyFor(projId, true);
    setLogFor(projId, "Запускаю в фоне — можно переключать проекты, задача не прервётся.");
    try {
      const r = await fetch("/api/posting/run", { method:"POST", headers:{ ...h, "Content-Type":"application/json" }, body: JSON.stringify({ project_id: projId }) });
      const j = await jsonOrThrow(r);
      if (j.job_id) { pollRun(j.job_id, projId, 0); }
      else { setLogFor(projId, j.log || "не удалось запустить"); setBusyFor(projId, false); }
    } catch (e:any) { setLogFor(projId, String(e.message || e)); setBusyFor(projId, false); }
  };

  const refresh = async () => { await load(selId || undefined); if (selId) await loadPosts(selId); };

  const planHours = (p: any): string[] => {
    const list: string[] = ((p && p.times) || []).map((x: any) => String(x).trim()).filter(Boolean);
    const out: string[] = [];
    for (const t of list) {
      if (t.indexOf(":") >= 0) {
        const h = t.split(":")[0].trim();
        if (/^\d{1,2}$/.test(h) && Number(h) <= 23) out.push(String(Number(h)).padStart(2, "0") + ":00");
      } else {
        const m = t.match(/\d{1,2}/g) || [];
        for (const h of m) if (Number(h) <= 23) out.push(String(Number(h)).padStart(2, "0") + ":00");
      }
    }
    return Array.from(new Set(out)).sort();
  };

  const nextRun = (p: any) => {
    const hs = planHours(p);
    if (!hs.length) return "";
    const now = new Date();
    for (const t of hs) {
      const d = new Date(); d.setHours(Number(t.slice(0, 2)), 0, 0, 0);
      if (d > now) return "сегодня в " + t;
    }
    return "завтра в " + hs[0];
  };

  const resolveVk = async () => {
    const v = vkOwner.trim();
    if (!v) { setVkName(""); return; }
    const h = auth(); if (!h) return;
    setVkName("проверяю…");
    try {
      const r = await fetch("/api/posting/vk_resolve", { method:"POST", headers:{ ...h, "Content-Type":"application/json" }, body: JSON.stringify({ value: v }) });
      const j = await r.json();
      if (j.status === "ok") {
        setVkOwner(String(j.owner_id));
        setVkName("✓ " + (j.name || "группа определена"));
      } else {
        setVkName(j.message || "не удалось определить группу");
      }
    } catch (e:any) { setVkName(String(e)); }
  };

  const unpublishPost = async (id: string) => {
    const h = auth(); if (!h) return;
    if (!confirm("Удалить этот пост из канала и группы?")) return;
    setBusy(true);
    try {
      const r = await fetch("/api/posting/posts/unpublish", { method:"POST", headers:{ ...h, "Content-Type":"application/json" }, body: JSON.stringify({ account_id: account, post_id: id }) });
      const j = await r.json();
      setMsg(j.had_ids === false ? "Убрано из списка — id публикации не сохранён, удалите в канале вручную" : "✓ Удалено из канала");
      if (selId) await loadPosts(selId);
    } finally { setBusy(false); }
  };

  const checkPrompt = async (kind: string, text: string) => {
    const h = auth(); if (!h) return;
    setCheckKind(kind); setChecking(true); setCheckResult(null);
    try {
      const r = await fetch("/api/prompt/check", { method:"POST", headers:{ ...h, "Content-Type":"application/json" }, body: JSON.stringify({ text, kind, account_id: account }) });
      const j = await r.json();
      setCheckResult(j);
    } catch (e:any) { setCheckResult({ status:"error", message:String(e) }); } finally { setChecking(false); }
  };

  const saveEdit = async (id: string) => {
    const h = auth(); if (!h) return;
    setBusy(true);
    try {
      await fetch("/api/posting/posts/update", { method:"POST", headers:{ ...h, "Content-Type":"application/json" }, body: JSON.stringify({ post_id: id, text: editText }) });
      setNotice("✓ Текст сохранён");
      setEditId(""); if (selId) await loadPosts(selId);
    } finally { setBusy(false); }
  };

  const saveSchedule = async (id: string) => {
    const h = auth(); if (!h) return;
    setBusy(true);
    try {
      await fetch("/api/posting/posts/schedule", { method:"POST", headers:{ ...h, "Content-Type":"application/json" }, body: JSON.stringify({ post_id: id, publish_at: schedWhen }) });
      setNotice("🕒 Запланировано на " + schedWhen.replace("T", " ") + " — Борис опубликует сам");
      setSchedId(""); if (selId) await loadPosts(selId);
    } finally { setBusy(false); }
  };

  const pay = () => { setMsg("Оплата скоро будет доступна — пока запускайтесь через бесплатный тест 🎁"); };

  const sel = projects.find((p) => p.id === selId) || null;
  const running = selId ? !!runningIds[selId] : false;
  const runLog = selId ? (runLogs[selId] || "") : "";
  const drafts = (Array.isArray(posts) ? posts : []).filter((p: any) => p.status === "draft");
  const published = (Array.isArray(posts) ? posts : []).filter((p: any) => p.status === "published");
  const card: any = { position:"relative", overflow:"hidden", background:"#fff", border:"1px solid " + T.line, borderRadius:"16px", padding:"24px" };
  const inp: any = { width:"100%", boxSizing:"border-box", padding:"10px 12px", borderRadius:"10px", border:"1px solid " + T.line, fontSize:"14px", fontFamily:"inherit" };
  const label: any = { fontSize:"13px", fontWeight:600, color:T.muted, marginBottom:"6px", display:"block" };
  const btn = (grad: string[]): any => ({ border:"none", borderRadius:"10px", padding:"12px 20px", fontSize:"14px", fontWeight:700, color:"#fff", background:"linear-gradient(135deg," + grad[0] + "," + grad[1] + ")", cursor: busy?"default":"pointer", opacity: busy?0.6:1 });

  const checkBlock = (kind: string, text: string, apply: (t: string) => void) => (
    <div style={{ marginTop:"8px" }}>
      <button className="soc-btn" disabled={checking} onClick={() => checkPrompt(kind, text)} style={{ border:"1px solid " + T.blue1, color:T.blue1, background:"#fff", borderRadius:"10px", padding:"8px 14px", fontSize:"13px", fontWeight:700, cursor:"pointer" }}>
        🧠 Проверить промт Борисом
      </button>
      {checkKind === kind && (checking || checkResult) && (
        <div style={{ marginTop:"10px", border:"1px solid " + T.line, borderRadius:"12px", padding:"14px", background:"#F8FAFF" }}>
          {checking ? (
            <div style={{ fontSize:"14px", color:T.muted }}>Борис читает промт…</div>
          ) : checkResult && checkResult.status === "ok" ? (
            <>
              {checkResult.verdict && <div style={{ fontSize:"14px", fontWeight:700, marginBottom:"10px" }}>{checkResult.verdict}</div>}
              {(checkResult.issues || []).map((it: any, i: number) => (
                <div key={i} style={{ fontSize:"13px", marginBottom:"8px" }}>
                  <div style={{ color:"#B42318" }}>• {it.problem}</div>
                  <div style={{ color:T.muted, marginLeft:"14px" }}>→ {it.fix}</div>
                </div>
              ))}
              {checkResult.improved && (
                <button className="soc-btn" onClick={() => { apply(checkResult.improved); setCheckResult(null); setCheckKind(""); }} style={{ ...btn([T.green1, T.green2]), padding:"8px 16px", fontSize:"13px", marginTop:"6px" }}>✍️ Заменить на улучшенный</button>
              )}
            </>
          ) : (
            <div style={{ fontSize:"14px", color:T.muted }}>{(checkResult && (checkResult.message || checkResult.status)) || "не удалось проверить"}</div>
          )}
        </div>
      )}
    </div>
  );

  return (
    <div style={{ minHeight:"100vh", background:T.bg, padding:"28px 20px", fontFamily:"-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif", color:T.ink }}>
      <style>{`
        .soc-btn { transition: filter .15s ease, transform .15s ease; }
        .soc-btn:hover { filter: brightness(1.07); transform: translateY(-1px); }
        .soc-in { transition: border-color .15s ease, box-shadow .15s ease; }
        .soc-in:focus { outline:none; border-color:#2F6FED; box-shadow:0 0 0 3px rgba(47,111,237,.15); }
      `}</style>
      <div style={{ maxWidth:"100%", margin:"0 auto", display:"flex", flexDirection:"column", gap:"16px" }}>

        <div><a href="/dashboard" style={{ fontSize:"14px", color:T.blue1, textDecoration:"none", fontWeight:600 }}>← Кабинет</a></div>

        <div style={{ display:"flex", alignItems:"center", gap:"14px" }}>
          <div style={{ width:"52px", height:"52px", borderRadius:"50%", background:"linear-gradient(135deg," + T.purple1 + "," + T.purple2 + ")", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"24px" }}>📣</div>
          <div>
            <div style={{ fontSize:"22px", fontWeight:800 }}>Социальные сети</div>
            <div style={{ fontSize:"14px", color:T.muted }}>Борис сам пишет и публикует посты в Telegram и ВКонтакте — в стиле вашего канала.</div>
          </div>
        </div>

        <div style={{ display:"flex", alignItems:"center", gap:"12px", flexWrap:"wrap" }}>
          <span style={{ fontSize:"13px", fontWeight:700, color:T.muted, textTransform:"uppercase", letterSpacing:".03em" }}>Проект</span>
          <select className="soc-in" style={{ ...inp, width:"auto", minWidth:"200px", cursor:"pointer" }} value={selId || ""} onChange={(e) => selectProject(e.target.value)}>
            {projects.length === 0 && <option value="">— проектов нет —</option>}
            {(Array.isArray(projects) ? projects : []).map((p) => (<option key={p.id} value={p.id}>{p.name}{p.active ? " ✓" : ""}</option>))}
          </select>
          <button className="soc-btn" onClick={newProject} style={{ ...btn([T.blue1, T.blue2]) }}>＋ Новый проект</button>
        </div>

        {(sel || creating) ? (
          <>
            {sel && (
              <div style={{ ...card, background: sel.active ? "#F6FEF9" : "#FFFBFA", border:"1px solid " + (sel.active ? "#A6F4C5" : "#FEE4E2") }}>
                <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", flexWrap:"wrap", gap:"12px" }}>
                  <div>
                    <div style={{ fontSize:"15px", fontWeight:700, color: sel.active ? T.green2 : "#B42318" }}>
                      {sel.active ? "✅ Проект активен" : "⛔ Нужен тариф или бесплатный тест"}
                    </div>
                    <div style={{ fontSize:"14px", fontWeight:600, color: planHours(sel).length ? T.green2 : "#B54708", marginTop:"6px" }}>
                      {planHours(sel).length
                        ? "🟢 Автопостинг включён — Борис публикует сам каждый день в " + planHours(sel).join(" и ")
                        : "⚪️ Автопостинг выключен — укажите время публикаций в настройках, и Борис начнёт постить сам"}
                    </div>
                    {planHours(sel).length > 0 && (
                      <div style={{ fontSize:"14px", color:T.muted, marginTop:"3px" }}>
                        Ближайший пост — {nextRun(sel)} · {sel.mode === "moderate" ? "придёт сюда на утверждение" : "уйдёт в канал автоматически"}
                      </div>
                    )}
                    <div style={{ fontSize:"14px", color:T.muted, marginTop:"2px" }}>
                      {sel.platforms === "tg" ? "✈️ Только Telegram" : sel.platforms === "vk" ? "🅥 Только ВКонтакте" : "✈️🅥 ВК + Telegram"}
                      {" · "}
                      {sel.mode === "moderate" ? "✋ На утверждение" : "Авто"}
                      {sel.left_today != null ? " · осталось сегодня: " + sel.left_today : ""}
                    </div>
                  </div>
                  <div style={{ display:"flex", gap:"10px", flexWrap:"wrap" }}>
                    <button className="soc-btn" disabled={running} onClick={runNow} style={{ ...btn([T.green1, T.green2]), whiteSpace:"nowrap", opacity: running?0.6:1 }}>
                      {running ? "⏳ Публикую…" : "▶️ Запустить сейчас"}
                    </button>
                    <button className="soc-btn" disabled={running} onClick={refresh} style={{ border:"1px solid " + T.line, background:"#fff", color:T.ink, borderRadius:"10px", padding:"12px 18px", fontSize:"14px", fontWeight:700, cursor:"pointer", whiteSpace:"nowrap" }}>🔄 Статус</button>
                  </div>
                </div>
                {runLog && (
                  <pre style={{ marginTop:"14px", background:"#0F172A", color:"#E2E8F0", borderRadius:"10px", padding:"12px 14px", fontSize:"12px", lineHeight:1.5, maxHeight:"220px", overflow:"auto", whiteSpace:"pre-wrap" }}>{runLog}</pre>
                )}
              </div>
            )}

            <div style={card}>
              <div onClick={() => setOpen(!open)} style={{ display:"flex", alignItems:"center", justifyContent:"space-between", cursor:"pointer", userSelect:"none" }}>
                <div style={{ fontSize:"16px", fontWeight:800 }}>⚙️ Настройки проекта{sel ? " — " + (sel.name || "") : ""}</div>
                <div style={{ fontSize:"14px", color:T.blue1, fontWeight:700 }}>{open ? "Свернуть ▾" : "Развернуть ▸"}</div>
              </div>
              {open && (<div style={{ marginTop:"18px" }}>
              <div style={{ marginBottom:"16px" }}>
                <label style={label}>Название проекта</label>
                <input className="soc-in" style={inp} value={name} onChange={(e) => setName(e.target.value)} placeholder="Например: От Души" />
              </div>
              <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:"14px" }}>
                <div>
                  <label style={label}>Telegram-канал (куда постить)</label>
                  <input className="soc-in" style={inp} value={channelTg} onChange={(e) => setChannelTg(e.target.value)} placeholder="@my_channel" />
                </div>
                <div>
                  <label style={label}>Группа ВКонтакте — вставьте ссылку на группу или на любой её пост</label>
                  <input className="soc-in" style={inp} value={vkOwner} onChange={(e) => { setVkOwner(e.target.value); setVkName(""); }} onBlur={resolveVk} placeholder="vk.com/club121265252 или vk.com/wall-121265252_7241" />
                  <div style={{ fontSize:"13px", marginTop:"6px", color: vkName.indexOf("✓") === 0 ? T.green2 : T.muted }}>
                    {vkName || "Номер группы определится сам — просто вставьте ссылку и кликните мимо поля."}
                  </div>
                </div>
                <div style={{ gridColumn:"1 / -1" }}>
                  <label style={label}>Канал-образец стиля (Борис учится на его постах)</label>
                  <input className="soc-in" style={inp} value={styleSource} onChange={(e) => setStyleSource(e.target.value)} placeholder="@my_channel" />
                </div>
                <div>
                  <label style={label}>Контакт для заказов в Telegram</label>
                  <input className="soc-in" style={inp} value={contactTg} onChange={(e) => setContactTg(e.target.value)} placeholder="https://t.me/username" />
                </div>
                <div>
                  <label style={label}>Контакт для заказов в ВКонтакте</label>
                  <input className="soc-in" style={inp} value={contactVk} onChange={(e) => setContactVk(e.target.value)} placeholder="https://vk.com/username" />
                </div>
                <div>
                  <label style={label}>Площадки (что оплачивает клиент)</label>
                  <select className="soc-in" style={{ ...inp, cursor:"pointer" }} value={platforms} onChange={(e) => setPlatforms(e.target.value)}>
                    <option value="both">ВК + Telegram (18 000 ₽)</option>
                    <option value="tg">Только Telegram (9 000 ₽)</option>
                    <option value="vk">Только ВКонтакте (12 000 ₽)</option>
                  </select>
                </div>
                <div>
                  <label style={label}>Режим публикации</label>
                  <select className="soc-in" style={{ ...inp, cursor:"pointer" }} value={mode} onChange={(e) => setMode(e.target.value)}>
                    <option value="auto">Авто — публиковать сразу</option>
                    <option value="moderate">✋ На утверждение — сначала черновик</option>
                  </select>
                </div>
                <div>
                  <label style={label}>Автопубликация черновика</label>
                  <select className="soc-in" style={{ ...inp, cursor:"pointer" }} value={String(autoHours)} onChange={(e) => setAutoHours(Number(e.target.value))}>
                    <option value="0">Нет — жду вашего решения</option>
                    <option value="1">Через 1 час</option>
                    <option value="2">Через 2 часа</option>
                    <option value="6">Через 6 часов</option>
                    <option value="24">Через сутки</option>
                  </select>
                </div>
                <div>
                  <label style={label}>Источник картинки</label>
                  <select className="soc-in" style={{ ...inp, cursor:"pointer" }} value={imageSource} onChange={(e) => setImageSource(e.target.value)}>
                    <option value="ai">🎨 Генерировать ИИ-баннер</option>
                    <option value="pexels">📷 Фото со стока по теме</option>
                  </select>
                </div>
                <div>
                  <label style={label}>Время публикаций (через запятую)</label>
                  <input className="soc-in" style={inp} value={times} onChange={(e) => setTimes(e.target.value)} placeholder="09:00, 19:00" />
                </div>
                <div style={{ marginTop:"12px" }}>
                  <label style={{ fontSize:"13px", opacity:0.8 }}>Часовой пояс (время постов считается по нему)</label>
                  <select className="soc-in" style={{ ...inp, cursor:"pointer" }} value={String(tzOffset)} onChange={(e) => setTzOffset(Number(e.target.value))}>
                    <option value="2">Калининград (МСК−1)</option>
                    <option value="3">Москва (МСК)</option>
                    <option value="4">Самара, Ижевск (МСК+1)</option>
                    <option value="5">Екатеринбург, Уфа (МСК+2)</option>
                    <option value="6">Омск (МСК+3)</option>
                    <option value="7">Красноярск, Новосибирск (МСК+4)</option>
                    <option value="8">Иркутск (МСК+5)</option>
                    <option value="9">Якутск, Чита (МСК+6)</option>
                    <option value="10">Владивоток (МСК+7)</option>
                    <option value="11">Магадан (МСК+8)</option>
                    <option value="12">Камчатка (МСК+9)</option>
                  </select>
                </div>
                <div style={{ gridColumn:"1 / -1" }}>
                  <label style={label}>Темы постов (по одной в строке)</label>
                  <textarea className="soc-in" style={{ ...inp, minHeight:"84px", resize:"vertical" }} value={themes} onChange={(e) => setThemes(e.target.value)} placeholder={"поздравление с юбилеем\nстихи на день рождения"} />
                </div>
                <div style={{ gridColumn:"1 / -1" }}>
                  <label style={label}>Указания Борису — как писать посты</label>
                  <textarea className="soc-in" style={{ ...inp, minHeight:"84px", resize:"vertical" }} value={textPrompt} onChange={(e) => setTextPrompt(e.target.value)} placeholder={"Пиши коротко и по-доброму, в конце — призыв заказать со ссылкой, 3-5 хэштегов."} />
                  {checkBlock("posts", textPrompt, setTextPrompt)}
                </div>
                <div style={{ gridColumn:"1 / -1" }}>
                  <label style={label}>Указания Борису по баннеру — что рисовать</label>
                  <textarea className="soc-in" style={{ ...inp, minHeight:"84px", resize:"vertical" }} value={bannerPrompt} onChange={(e) => setBannerPrompt(e.target.value)} placeholder={"Фирменные цвета — тёплые пастельные, минимализм, крупный акцент на объекте. Без мелкого текста."} />
                  {checkBlock("banner", bannerPrompt, setBannerPrompt)}
                </div>
              </div>
              <div style={{ display:"flex", alignItems:"center", gap:"12px", marginTop:"16px", flexWrap:"wrap" }}>
                <button className="soc-btn" disabled={busy} onClick={saveProject} style={btn([T.green1, T.green2])}>Сохранить проект</button>
                {sel && <button className="soc-btn" disabled={busy} onClick={deleteProject} style={{ ...inp, width:"auto", border:"1px solid #FDA29B", color:"#B42318", background:"#FFFBFA", fontWeight:700, cursor:"pointer" }}>Удалить</button>}
                {msg && <span style={{ fontSize:"14px", color: msg.indexOf("✓") === 0 ? T.green2 : T.muted }}>{msg}</span>}
              </div>
              </div>)}
            </div>

            <div style={{ ...card, background:"linear-gradient(135deg,#FFFAEB,#FEF7E6)", border:"1px solid #FEDF89", display:"flex", alignItems:"center", justifyContent:"space-between", flexWrap:"wrap", gap:"14px" }}>
              <div>
                <div style={{ fontSize:"17px", fontWeight:800, color:"#B54708" }}>🎁 Запустить проект бесплатно</div>
                <div style={{ fontSize:"14px", color:"#93370D", marginTop:"2px" }}>2 дня, до 6 постов. Оплата тарифа — скоро.</div>
              </div>
              <div style={{ display:"flex", gap:"10px", flexWrap:"wrap" }}>
                <button className="soc-btn" disabled={busy || (sel && sel.plan === "trial")} onClick={startTrial} style={{ ...btn([T.orange1, T.orange2]), whiteSpace:"nowrap" }}>Начать бесплатно</button>
                <button className="soc-btn" onClick={pay} style={{ border:"1px solid " + T.line, background:"#fff", color:T.muted, borderRadius:"10px", padding:"12px 20px", fontSize:"14px", fontWeight:700, cursor:"pointer", whiteSpace:"nowrap" }}>Оплатить тариф</button>
              </div>
            </div>

            {sel && drafts.length > 0 && (
              <div style={card}>
                <div style={{ fontSize:"16px", fontWeight:800, marginBottom:"14px" }}>✋ Черновики на утверждение ({drafts.length})</div>
                {notice && (
                  <div style={{ marginBottom:"14px", background:"#F6FEF9", border:"1px solid #A6F4C5", borderRadius:"12px", padding:"12px 16px", fontSize:"14px", fontWeight:600, color:T.green2, display:"flex", justifyContent:"space-between", gap:"12px" }}>
                    <span>{notice}</span>
                    <span onClick={() => setNotice("")} style={{ cursor:"pointer", color:T.muted, fontWeight:400 }}>✕</span>
                  </div>
                )}
                <div style={{ display:"flex", flexDirection:"column", gap:"12px" }}>
                  {drafts.map((p) => (
                    <div key={p.id} style={{ border:"1px solid " + T.line, borderRadius:"12px", padding:"14px", display:"flex", gap:"14px", alignItems:"flex-start" }}>
                      {p.banner && bannerUrls[p.banner] && (
                        <div onClick={() => setZoom(bannerUrls[p.banner])} title="Открыть крупно" style={{ position:"relative", width:"110px", height:"110px", flexShrink:0, cursor:"zoom-in" }}>
                          <img src={bannerUrls[p.banner]} alt="" style={{ width:"110px", height:"110px", objectFit:"cover", borderRadius:"8px" }} />
                          <div style={{ position:"absolute", right:"5px", bottom:"5px", background:"rgba(15,23,42,.78)", color:"#fff", borderRadius:"7px", fontSize:"13px", padding:"2px 6px" }}>🔍</div>
                        </div>
                      )}
                      <div style={{ flex:1, minWidth:0 }}>
                        <div style={{ fontSize:"12px", color:T.muted, marginBottom:"4px" }}>{p.created_at}</div>
                        <div style={{ fontSize:"14px", color:T.ink, whiteSpace:"pre-wrap" }}>{p.preview || p.text}</div>
                        <div style={{ display:"flex", gap:"10px", marginTop:"10px", flexWrap:"wrap" }}>
                          <button className="soc-btn" disabled={busy} onClick={() => publishPost(p.id)} style={{ ...btn([T.green1, T.green2]), padding:"8px 16px", fontSize:"13px" }}>✅ Опубликовать</button>
                          <button className="soc-btn" disabled={busy} onClick={() => { setEditId(editId === p.id ? "" : p.id); setEditText(p.text || ""); setSchedId(""); }} style={{ border:"1px solid " + T.line, background:"#fff", color:T.ink, borderRadius:"10px", padding:"8px 16px", fontSize:"13px", fontWeight:700, cursor:"pointer" }}>✏️ Редактировать</button>
                          <button className="soc-btn" disabled={busy} onClick={() => { setSchedId(schedId === p.id ? "" : p.id); setSchedWhen((p.publish_at || "").replace(" ", "T")); setEditId(""); }} style={{ border:"1px solid " + T.line, background:"#fff", color:T.ink, borderRadius:"10px", padding:"8px 16px", fontSize:"13px", fontWeight:700, cursor:"pointer" }}>🕒 Когда разместить</button>
                          <button className="soc-btn" disabled={busy} onClick={() => removePost(p.id)} style={{ border:"1px solid #FDA29B", color:"#B42318", background:"#fff", borderRadius:"10px", padding:"8px 16px", fontSize:"13px", fontWeight:700, cursor:"pointer" }}>🗑 Удалить</button>
                        </div>
                        {p.publish_at
                          ? <div style={{ fontSize:"13px", color:T.green2, marginTop:"8px" }}>🕒 Запланировано на {p.publish_at}</div>
                          : <div style={{ fontSize:"13px", color:T.muted, marginTop:"8px" }}>⏳ Если не изменить, пост выйдет автоматически. Можно отредактировать текст или задать своё время.</div>}
                        {editId === p.id && (
                          <div style={{ marginTop:"10px" }}>
                            <textarea className="soc-in" style={{ ...inp, minHeight:"170px", resize:"vertical" }} value={editText} onChange={(e) => setEditText(e.target.value)} />
                            <div style={{ display:"flex", gap:"10px", marginTop:"8px" }}>
                              <button className="soc-btn" disabled={busy} onClick={() => saveEdit(p.id)} style={{ ...btn([T.green1, T.green2]), padding:"8px 16px", fontSize:"13px" }}>Сохранить текст</button>
                              <button className="soc-btn" onClick={() => setEditId("")} style={{ border:"1px solid " + T.line, background:"#fff", color:T.muted, borderRadius:"10px", padding:"8px 16px", fontSize:"13px", fontWeight:700, cursor:"pointer" }}>Отмена</button>
                            </div>
                          </div>
                        )}
                        {schedId === p.id && (
                          <div style={{ marginTop:"10px", display:"flex", gap:"10px", alignItems:"center", flexWrap:"wrap" }}>
                            <input type="datetime-local" className="soc-in" style={{ ...inp, width:"auto" }} value={schedWhen} onChange={(e) => setSchedWhen(e.target.value)} />
                            <button className="soc-btn" disabled={busy} onClick={() => saveSchedule(p.id)} style={{ ...btn([T.blue1, T.blue2]), padding:"8px 16px", fontSize:"13px" }}>Запланировать</button>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {sel && published.filter((x: any) => x.stats && x.stats.er != null && x.stats.reactions > 0).length > 0 && (
              <div style={card}>
                <div style={{ fontSize:"16px", fontWeight:800, marginBottom:"6px" }}>🏆 Лучшие посты</div>
                <div style={{ fontSize:"13px", color:T.muted, marginBottom:"14px" }}>
                  Посты, на которые читатели откликались чаще всего. Борис учитывает именно их, когда пишет новые.
                </div>
                <div style={{ display:"flex", flexDirection:"column", gap:"10px" }}>
                  {published
                    .filter((x: any) => x.stats && x.stats.er != null && x.stats.reactions > 0)
                    .sort((a: any, b: any) => (b.stats.er || 0) - (a.stats.er || 0))
                    .slice(0, 3)
                    .map((p: any, i: number) => (
                      <div key={p.id} style={{ border:"1px solid " + T.line, borderRadius:"12px", padding:"12px 14px", display:"flex", gap:"12px", alignItems:"flex-start" }}>
                        <div style={{ fontSize:"20px", flexShrink:0 }}>{["🥇","🥈","🥉"][i]}</div>
                        <div style={{ flex:1, minWidth:0 }}>
                          <div style={{ fontSize:"14px", color:T.ink, maxHeight:"44px", overflow:"hidden" }}>{p.text}</div>
                          <div style={{ display:"flex", gap:"14px", marginTop:"8px", fontSize:"13px", color:T.muted, flexWrap:"wrap" }}>
                            <span>👁 {p.stats.views ?? 0}</span>
                            <span>❤️ {p.stats.likes ?? 0}</span>
                            <span>💬 {p.stats.comments ?? 0}</span>
                            <span style={{ fontWeight:700, color:T.green2 }}>вовлечение {p.stats.er}%</span>
                          </div>
                        </div>
                      </div>
                    ))}
                </div>
              </div>
            )}

            {sel && published.length > 0 && (
              <div style={card}>
                <div style={{ fontSize:"16px", fontWeight:800, marginBottom:"14px" }}>📤 Опубликованные ({published.length})</div>
                <div style={{ display:"flex", flexDirection:"column", gap:"12px" }}>
                  {published.map((p) => (
                    <div key={p.id} style={{ border:"1px solid " + T.line, borderRadius:"12px", padding:"14px", display:"flex", gap:"14px", alignItems:"flex-start" }}>
                      {p.banner && bannerUrls[p.banner] && (
                        <div onClick={() => setZoom(bannerUrls[p.banner])} title="Открыть крупно" style={{ position:"relative", width:"90px", height:"90px", flexShrink:0, cursor:"zoom-in" }}>
                          <img src={bannerUrls[p.banner]} alt="" style={{ width:"90px", height:"90px", objectFit:"cover", borderRadius:"8px" }} />
                          <div style={{ position:"absolute", right:"5px", bottom:"5px", background:"rgba(15,23,42,.78)", color:"#fff", borderRadius:"7px", fontSize:"13px", padding:"2px 6px" }}>🔍</div>
                        </div>
                      )}
                      <div style={{ flex:1, minWidth:0 }}>
                        <div style={{ fontSize:"12px", color:T.muted, marginBottom:"4px" }}>{p.created_at}</div>
                        <div style={{ fontSize:"14px", color:T.ink, whiteSpace:"pre-wrap", maxHeight:"90px", overflow:"hidden" }}>{p.text}</div>
                        {p.stats && (
                          <div style={{ display:"flex", gap:"14px", marginTop:"10px", flexWrap:"wrap", fontSize:"13px", color:T.muted, alignItems:"center" }}>
                            <span title="Просмотры">👁 {p.stats.views ?? 0}</span>
                            <span title="Лайки">❤️ {p.stats.likes ?? 0}</span>
                            <span title="Комментарии">💬 {p.stats.comments ?? 0}</span>
                            <span title="Репосты">🔁 {p.stats.reposts ?? 0}</span>
                            {p.stats.er != null && (
                              <span title="Доля читателей, которые отреагировали" style={{ fontWeight:700, color: p.stats.er >= 5 ? T.green2 : T.ink }}>
                                вовлечение {p.stats.er}%
                              </span>
                            )}
                          </div>
                        )}
                        <div style={{ display:"flex", gap:"10px", marginTop:"10px", flexWrap:"wrap" }}>
                          <button className="soc-btn" disabled={busy} onClick={() => unpublishPost(p.id)} style={{ border:"1px solid #FDA29B", color:"#B42318", background:"#fff", borderRadius:"10px", padding:"8px 16px", fontSize:"13px", fontWeight:700, cursor:"pointer" }}>🗑 Удалить из канала</button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        ) : (
          <div style={{ ...card, textAlign:"center", padding:"40px 24px", color:T.muted }}>
            <div style={{ fontSize:"40px", marginBottom:"10px" }}>📣</div>
            <div style={{ fontSize:"16px", fontWeight:700, color:T.ink, marginBottom:"6px" }}>Пока нет ни одного проекта</div>
            <div style={{ fontSize:"14px", marginBottom:"18px" }}>Создайте проект — задайте каналы, стиль и темы, и бот начнёт постить.</div>
            <button className="soc-btn" onClick={newProject} style={btn([T.blue1, T.blue2])}>＋ Создать первый проект</button>
          </div>
        )}

        {zoom && (
          <div onClick={() => setZoom("")} style={{ position:"fixed", top:0, left:0, right:0, bottom:0, background:"rgba(15,23,42,.88)", display:"flex", alignItems:"center", justifyContent:"center", zIndex:9999, cursor:"zoom-out", padding:"24px" }}>
            <img src={zoom} alt="" style={{ maxWidth:"92vw", maxHeight:"92vh", borderRadius:"14px", boxShadow:"0 24px 70px rgba(0,0,0,.55)" }} />
          </div>
        )}

        {err && <div style={{ color:"#B42318", fontSize:"14px" }}>{err}</div>}
      </div>
    </div>
  );
}
