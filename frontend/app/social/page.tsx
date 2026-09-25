"use client";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";
import { useAccounts } from "../lib/AccountContext";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Icon, IconChip } from "../ui";

const T = {
  blue1:"var(--boris-primary)", blue2:"var(--boris-primary-hover)", purple1:"var(--boris-purple)", purple2:"#6f4bd9",
  orange1:"var(--boris-warning)", orange2:"#c97308", green1:"var(--boris-success)", green2:"#11835f",
  ink:"var(--boris-text-primary)", muted:"var(--boris-text-secondary)", line:"var(--boris-border)", bg:"var(--boris-background)",
};

type Project = any;

function cleanPostText(value: unknown): string {
  return String(value ?? "")
    .replace(/<br\s*\/?\s*>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/<[^>]+>/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function cleanSocialError(value: unknown): string {
  const raw = String(value ?? "").trim();
  if (!raw || /^[A-Z0-9_:-]+$/.test(raw)) return "Не удалось подготовить изображение для публикации";
  return cleanPostText(humanizeBusinessText(raw, "Не удалось подготовить публикацию"));
}

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
  const [paidAuto, setPaidAuto] = useState(false);
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

  const openSocialPayment = async () => {
    const h = auth(); if (!h || !account || busy) return;
    const target = String(sel?.platforms || platforms || "both");
    const pack = target === "tg" ? "post_tg" : target === "vk" ? "post_vk" : "post_both";
    setBusy(true); setMsg("");
    try {
      const r = await fetch(`/api/payments/robokassa/link?account_id=${encodeURIComponent(account)}&pack=${encodeURIComponent(pack)}`, { headers:h });
      const j = await jsonOrThrow(r);
      if (!j?.url) throw new Error(j?.message || "Ссылка на оплату не получена");
      window.location.href = j.url;
    } catch (e: any) {
      setMsg(e?.message || "Не удалось открыть оплату");
    } finally { setBusy(false); }
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
    setPaidAuto(p ? Boolean(p.paid_auto_generation) : false);
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
        paid_auto_generation: paidAuto,
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
      const publishError = j?.errors?.banner || Object.values(j?.errors || {}).filter(Boolean).join("; ");
      setNotice(j.status === "ok"
        ? "✅ Пост опубликован" + (where ? " в " + where : "") + " — он ушёл в канал и убран из черновиков"
        : "⚠️ Не удалось опубликовать" + (publishError ? ": " + publishError : ", попробуйте ещё раз"));
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
    const offset = Number(p?.tz_offset ?? 3);
    const local = new Date(Date.now() + offset * 60 * 60 * 1000);
    const nowMinutes = local.getUTCHours() * 60 + local.getUTCMinutes();
    for (const t of hs) {
      const mins = Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5) || 0);
      if (mins >= nowMinutes) return "сегодня в " + t;
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
      if (j.had_ids === false) {
        setMsg("У публикации нет сохранённых ID площадок — удалять на стороне канала нечего");
      } else if (j.status === "ok") {
        setMsg("✓ Удалено из Telegram и/или ВКонтакте");
      } else {
        const failed = Object.keys(j.errors || {}).map((k) => k === "tg" ? "Telegram" : k === "vk" ? "ВКонтакте" : k).join(", ");
        setMsg("⚠️ Удалено не везде" + (failed ? ": повторю для " + failed : " — можно повторить"));
      }
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

  const sel = projects.find((p) => p.id === selId) || null;
  const running = selId ? !!runningIds[selId] : false;
  const runLog = selId ? (runLogs[selId] || "") : "";
  const drafts = (Array.isArray(posts) ? posts : []).filter((p: any) => p.status === "draft");
  const partials = (Array.isArray(posts) ? posts : []).filter((p: any) => p.status === "partial");
  const errors = (Array.isArray(posts) ? posts : []).filter((p: any) => p.status === "error");
  const published = (Array.isArray(posts) ? posts : []).filter((p: any) => p.status === "published");
  const feedback = notice || msg;
  const card: any = { position:"relative", overflow:"hidden", background:"#fff", border:"1px solid " + T.line, borderRadius:"16px", padding:"24px" };
  const inp: any = { width:"100%", boxSizing:"border-box", padding:"10px 12px", borderRadius:"10px", border:"1px solid " + T.line, fontSize:"14px", fontFamily:"inherit" };
  const label: any = { fontSize:"13px", fontWeight:600, color:T.muted, marginBottom:"6px", display:"block" };
  const btn = (grad: string[]): any => ({ border:"none", borderRadius:"10px", padding:"12px 20px", fontSize:"14px", fontWeight:700, color:"#fff", background:"linear-gradient(135deg," + grad[0] + "," + grad[1] + ")", cursor: busy?"default":"pointer", opacity: busy?0.6:1 });

  const checkBlock = (kind: string, text: string, apply: (t: string) => void) => (
    <div className="boris-f3-a33b8fc2ff7780">
      <button className="soc-btn" disabled={checking} onClick={() => checkPrompt(kind, text)} style={{ border:"1px solid " + T.blue1, color:T.blue1, background:"#fff", borderRadius:"10px", padding:"8px 14px", fontSize:"13px", fontWeight:700, cursor:"pointer" }}>
        🧠 Проверить промт Борисом
      </button>
      {checkKind === kind && (checking || checkResult) && (
        <div style={{ marginTop:"10px", border:"1px solid " + T.line, borderRadius:"12px", padding:"14px", background:"#F8FAFF" }}>
          {checking ? (
            <div style={{ fontSize:"14px", color:T.muted }}>Борис читает промт…</div>
          ) : checkResult && checkResult.status === "ok" ? (
            <>
              {checkResult.verdict && <div className="boris-f3-0e6d61d56e4adc">{checkResult.verdict}</div>}
              {(checkResult.issues || []).map((it: any, i: number) => (
                <div key={i} className="boris-f3-eebe60ffaadcb5">
                  <div className="boris-f3-a151a787e37497">• {it.problem}</div>
                  <div style={{ color:T.muted, marginLeft:"14px" }}>→ {it.fix}</div>
                </div>
              ))}
              {checkResult.improved && (
                <button className="soc-btn" onClick={() => { apply(checkResult.improved); setCheckResult(null); setCheckKind(""); }} style={{ ...btn([T.green1, T.green2]), padding:"8px 16px", fontSize:"13px", marginTop:"6px" }}>✍️ Заменить на улучшенный</button>
              )}
            </>
          ) : (
            <div style={{ fontSize:"14px", color:T.muted }}>{humanizeBusinessText(checkResult && (checkResult.message || checkResult.status), "Не удалось проверить")}</div>
          )}
        </div>
      )}
    </div>
  );

  return (
    <div className="boris-product-route boris-social-page" style={{ minHeight:"100vh", background:T.bg, padding:"28px 20px", fontFamily:"var(--font-inter), Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif", color:T.ink }}>
      <style>{`
        .soc-btn { transition: filter .15s ease, transform .15s ease; }
        .soc-btn:hover { filter: brightness(1.07); transform: translateY(-1px); }
        .soc-in { transition: border-color .15s ease, box-shadow .15s ease; }
        .soc-in:focus { outline:none; border-color:var(--boris-blue); box-shadow:0 0 0 3px rgba(47,111,237,.15); }
      `}</style>
      <div className="boris-f3-5020a5a4600a1c boris-social-reference-workspace">

        <div className="boris-reference-page-head boris-social-reference-head">
          <div className="boris-reference-kicker">КОНТЕНТ И ПУБЛИКАЦИИ</div>
          <h1>Социальные сети</h1>
          <p>Создание, проверка и публикация постов в Telegram и ВКонтакте — в одном рабочем окне.</p>
        </div>

        <button type="button" onClick={() => router.push('/dashboard/video-factory')} style={{ width:'100%', margin:'0 0 16px', border:'1px solid var(--boris-border)', borderRadius:'14px', background:'#fff', padding:'15px 18px', display:'flex', justifyContent:'space-between', alignItems:'center', gap:'12px', cursor:'pointer', textAlign:'left' }}>
          <span><b style={{ display:'block', fontSize:'15px' }}>Видео / Reels / Shorts <span style={{ color:'#b54708', fontSize:'11px' }}>TEST</span></b><span style={{ display:'block', marginTop:'3px', fontSize:'13px', color:T.muted }}>Создание вертикальных роликов, Instagram Reels, YouTube Shorts и расписание публикаций.</span></span>
          <span style={{ color:T.blue1, fontWeight:800 }}>Открыть →</span>
        </button>

        <div className="boris-f3-f1076067fe1ec6 boris-social-projectbar">
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
              <div className="boris-social-card boris-social-card-accent boris-social-status-card" style={{ ...card, background: sel.active ? "#F6FEF9" : "#FFFBFA", border:"1px solid " + (sel.active ? "#A6F4C5" : "#FEE4E2") }}>
                <div className="boris-f3-5756a770169cea">
                  <div>
                    <div style={{ fontSize:"15px", fontWeight:700, color: sel.active ? T.green2 : "#B42318" }}>
                      {sel.active ? "Проект активен" : "Нужен тариф или бесплатный тест"}
                    </div>
                    <div style={{ fontSize:"14px", fontWeight:600, color: planHours(sel).length && sel.paid_auto_generation && sel.scheduler_ok ? T.green2 : "#B54708", marginTop:"6px" }}>
                      {planHours(sel).length && sel.paid_auto_generation && sel.scheduler_ok
                        ? "Автопилот работает — BORIS сам создаёт посты по расписанию " + planHours(sel).join(" и ")
                        : planHours(sel).length && sel.paid_auto_generation
                          ? "Автопилот настроен, но фоновый планировщик сейчас не подтверждает работу"
                          : planHours(sel).length
                            ? "Расписание задано, но автогенерация выключена — посты сами не создаются"
                            : "Автопостинг выключен — укажите время публикаций и включите автогенерацию"}
                    </div>
                    {planHours(sel).length > 0 && sel.paid_auto_generation && sel.scheduler_ok && (
                      <div style={{ fontSize:"14px", color:T.muted, marginTop:"3px" }}>
                        Следующая генерация — {nextRun(sel)} · {sel.mode === "moderate"
                          ? (Number(sel.auto_publish_hours || 0) > 0 ? "черновик выйдет сам через " + Number(sel.auto_publish_hours) + " ч." : "черновик ждёт утверждения")
                          : "публикация уйдёт в канал автоматически"}
                      </div>
                    )}
                    <div style={{ fontSize:"14px", color:T.muted, marginTop:"2px" }}>
                      {sel.platforms === "tg" ? "Только Telegram" : sel.platforms === "vk" ? "Только ВКонтакте" : "ВКонтакте + Telegram"}
                      {" · "}
                      {sel.mode === "moderate" ? "На утверждение" : "Авто"}
                      {sel.left_today != null ? " · осталось сегодня: " + sel.left_today : ""}
                    </div>
                  </div>
                  <div className="boris-f3-88785587a0f4bb">
                    <button className="soc-btn" disabled={running} onClick={runNow} style={{ ...btn([T.green1, T.green2]), whiteSpace:"nowrap", opacity: running?0.6:1 }}>
                      {running ? "Публикую…" : "Запустить сейчас"}
                    </button>
                    <button className="soc-btn" disabled={running} onClick={refresh} style={{ border:"1px solid " + T.line, background:"#fff", color:T.ink, borderRadius:"10px", padding:"12px 18px", fontSize:"14px", fontWeight:700, cursor:"pointer", whiteSpace:"nowrap" }}>Обновить статус</button>
                  </div>
                </div>
                {running && runLog ? <div className="boris-social-background-status">BORIS выполняет задачу в фоне. Статус обновится автоматически.</div> : null}
              </div>
            )}

            {feedback && (
              <div style={{ marginBottom:"14px", background: feedback.startsWith("⚠️") ? "#FFFAEB" : "#F6FEF9", border:"1px solid " + (feedback.startsWith("⚠️") ? "#FEDF89" : "#A6F4C5"), borderRadius:"12px", padding:"12px 16px", fontSize:"14px", fontWeight:600, color: feedback.startsWith("⚠️") ? "#B54708" : T.green2, display:"flex", justifyContent:"space-between", gap:"12px", alignItems:"flex-start" }}>
                <span>{feedback}</span>
                <span onClick={() => { setNotice(""); setMsg(""); }} style={{ cursor:"pointer", color:T.muted, fontWeight:400 }}>✕</span>
              </div>
            )}

            <div className="boris-social-card boris-social-settings-card" style={card}>
              <div onClick={() => setOpen(!open)} className="boris-f3-924fd8daa95655">
                <div className="boris-f3-3df0eea15ca34a"><span className="boris-social-title-icon"><Icon name="settings" size={18} /></span>Настройки проекта{sel ? " — " + (sel.name || "") : ""}</div>
                <div style={{ fontSize:"14px", color:T.blue1, fontWeight:700 }}>{open ? "Свернуть ▾" : "Развернуть ▸"}</div>
              </div>
              {open && (<div className="boris-f3-3517ec692dbecf">
              <div className="boris-f3-7564ec607ed8ff">
                <label style={label}>Название проекта</label>
                <input className="soc-in" style={inp} value={name} onChange={(e) => setName(e.target.value)} placeholder="Например: От Души" />
              </div>
              <div className="boris-f3-f9e9829802291a">
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
                <div className="boris-f3-a277c0166bedaa">
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
                    <option value="moderate">На утверждение — сначала черновик</option>
                  </select>
                </div>
                <div>
                  <label style={label}>Автогенерация по расписанию</label>
                  <select className="soc-in" style={{ ...inp, cursor:"pointer" }} value={paidAuto ? "on" : "off"} onChange={(e) => setPaidAuto(e.target.value === "on")}>
                    <option value="off">Выключена — только ручной запуск</option>
                    <option value="on">Включена — BORIS сам создаёт посты</option>
                  </select>
                  <div style={{ fontSize:"12px", color:T.muted, marginTop:"5px" }}>Включайте только для проекта, где разрешены автоматические AI-генерации.</div>
                </div>
                <div>
                  <label style={label}>Автопубликация черновика</label>
                  <select className="soc-in" style={{ ...inp, cursor:"pointer" }} value={String(autoHours)} onChange={(e) => setAutoHours(Number(e.target.value))}>
                    <option value="0">Нет — жду вашего решения</option>
                    <option value="1">Через 1 час</option>
                    <option value="2">Через 2 часа</option>
                    <option value="3">Через 3 часа</option>
                    <option value="6">Через 6 часов</option>
                    <option value="24">Через сутки</option>
                  </select>
                </div>
                <div>
                  <label style={label}>Источник картинки</label>
                  <select className="soc-in" style={{ ...inp, cursor:"pointer" }} value={imageSource} onChange={(e) => setImageSource(e.target.value)}>
                    <option value="ai">Генерировать ИИ-баннер</option>
                    <option value="pexels">Фото со стока по теме</option>
                  </select>
                </div>
                <div>
                  <label style={label}>Время публикаций (через запятую)</label>
                  <input className="soc-in" style={inp} value={times} onChange={(e) => setTimes(e.target.value)} placeholder="09:00, 19:00" />
                </div>
                <div className="boris-f3-b6d781cbbc10a4">
                  <label className="boris-f3-6e822202a7c2a5">Часовой пояс (время постов считается по нему)</label>
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
                <div className="boris-f3-a277c0166bedaa">
                  <label style={label}>Темы постов (по одной в строке)</label>
                  <textarea className="soc-in" style={{ ...inp, minHeight:"84px", resize:"vertical" }} value={themes} onChange={(e) => setThemes(e.target.value)} placeholder={"поздравление с юбилеем\nстихи на день рождения"} />
                </div>
                <div className="boris-f3-a277c0166bedaa">
                  <label style={label}>Указания Борису — как писать посты</label>
                  <textarea className="soc-in" style={{ ...inp, minHeight:"84px", resize:"vertical" }} value={textPrompt} onChange={(e) => setTextPrompt(e.target.value)} placeholder={"Пиши коротко и по-доброму, в конце — призыв заказать со ссылкой, 3-5 хэштегов."} />
                  {checkBlock("posts", textPrompt, setTextPrompt)}
                </div>
                <div className="boris-f3-a277c0166bedaa">
                  <label style={label}>Указания Борису по баннеру — что рисовать</label>
                  <textarea className="soc-in" style={{ ...inp, minHeight:"84px", resize:"vertical" }} value={bannerPrompt} onChange={(e) => setBannerPrompt(e.target.value)} placeholder={"Фирменные цвета — тёплые пастельные, минимализм, крупный акцент на объекте. Без мелкого текста."} />
                  {checkBlock("banner", bannerPrompt, setBannerPrompt)}
                </div>
              </div>
              <div className="boris-f3-7939306dd85aad">
                <button className="soc-btn" disabled={busy} onClick={saveProject} style={btn([T.green1, T.green2])}>Сохранить проект</button>
                {sel && <button className="soc-btn" disabled={busy} onClick={deleteProject} style={{ ...inp, width:"auto", border:"1px solid #FDA29B", color:"#B42318", background:"#FFFBFA", fontWeight:700, cursor:"pointer" }}>Удалить</button>}
              </div>
              </div>)}
            </div>

            <div className="boris-social-card boris-social-card-accent boris-social-trial-card" style={{ ...card, background:"linear-gradient(135deg,#FFFAEB,#FEF7E6)", border:"1px solid #FEDF89", display:"flex", alignItems:"center", justifyContent:"space-between", flexWrap:"wrap", gap:"14px" }}>
              <div>
                <div className="boris-f3-64affd257171ce"><span className="boris-social-title-icon"><Icon name="award" size={18} /></span>Запустить проект бесплатно</div>
                <div className="boris-f3-0b72695cac1ed1">3 дня, до 6 постов. После пробного периода можно сразу подключить тариф на 30 дней.</div>
              </div>
              <div className="boris-f3-88785587a0f4bb">
                <button className="soc-btn" disabled={busy || (sel && sel.plan === "trial")} onClick={startTrial} style={{ ...btn([T.orange1, T.orange2]), whiteSpace:"nowrap" }}>Начать бесплатно</button>
                <button className="soc-btn" disabled={busy || !account} onClick={openSocialPayment} style={{ ...btn([T.blue1, T.blue2]), whiteSpace:"nowrap" }}>Оплатить тариф</button>
              </div>
            </div>

            {sel && errors.length > 0 && (
              <div className="boris-social-card boris-social-error-card" style={{ ...card, border:"1px solid #FECACA", background:"#FFF7F7" }}>
                <div style={{ fontSize:"16px", fontWeight:800, marginBottom:"8px", color:"#991B1B" }}>Требует внимания ({errors.length})</div>
                <div style={{ fontSize:"13px", color:T.muted, marginBottom:"14px" }}>
                  Текст без баннера не публикуется. Ошибка сохранена, поэтому пост можно безопасно сгенерировать заново.
                </div>
                <div className="boris-f3-85e7427e7ff5bd">
                  {errors.map((p: any) => (
                    <div key={p.id} className="boris-social-post-card" style={{ border:"1px solid #FECACA", borderRadius:"12px", padding:"14px" }}>
                      <div style={{ fontSize:"12px", color:T.muted }}>{p.created_at}{p.publish_at ? " · план: " + p.publish_at : ""}</div>
                      <div style={{ marginTop:"6px", fontSize:"14px", color:T.ink, whiteSpace:"pre-wrap", maxHeight:"90px", overflow:"hidden" }}>{cleanPostText(p.preview || p.text)}</div>
                      <div style={{ marginTop:"8px", fontSize:"13px", fontWeight:700, color:"#B42318" }}>{cleanSocialError(p.error)}</div>
                      <div style={{ marginTop:"12px", display:"flex", gap:"8px", flexWrap:"wrap" }}>
                        <button className="soc-btn" disabled={running || busy} onClick={runNow} style={{ ...btn([T.blue1, T.blue2]), padding:"8px 16px", fontSize:"13px" }}>Повторить генерацию</button>
                        <button className="soc-btn boris-f3-990af6b92041d2" disabled={busy} onClick={() => removePost(p.id)}>Удалить запись</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {sel && partials.length > 0 && (
              <div className="boris-social-card" style={{ ...card, border:"1px solid #FEDF89", background:"#FFFAEB" }}>
                <div style={{ fontSize:"16px", fontWeight:800, marginBottom:"8px", color:"#B54708" }}>Доставлено частично ({partials.length})</div>
                <div style={{ fontSize:"13px", color:T.muted, marginBottom:"14px" }}>
                  Одна площадка уже получила пост. BORIS автоматически повторяет только недостающую площадку — успешная публикация не дублируется.
                </div>
                <div className="boris-f3-85e7427e7ff5bd">
                  {partials.map((p: any) => (
                    <div key={p.id} className="boris-social-post-card" style={{ border:"1px solid #FEDF89", borderRadius:"12px", padding:"14px" }}>
                      <div style={{ fontSize:"12px", color:T.muted }}>{p.created_at}</div>
                      <div style={{ marginTop:"6px", fontSize:"14px", color:T.ink, whiteSpace:"pre-wrap", maxHeight:"90px", overflow:"hidden" }}>{cleanPostText(p.preview || p.text)}</div>
                      <div style={{ marginTop:"8px", fontSize:"13px", color:"#B54708" }}>{cleanSocialError(p.error)}</div>
                      <div style={{ marginTop:"8px", fontSize:"12px", color:T.muted }}>
                        {p.ids?.tg?.message_id ? "✓ Telegram уже доставлен  " : ""}{p.ids?.vk?.post_id ? "✓ ВКонтакте уже доставлен" : ""}
                      </div>
                      <button className="soc-btn" disabled={busy} onClick={() => publishPost(p.id)} style={{ ...btn([T.blue1, T.blue2]), padding:"8px 16px", fontSize:"13px", marginTop:"10px" }}>Повторить недостающую площадку</button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {sel && drafts.length > 0 && (
              <div className="boris-social-card boris-social-drafts-card" style={card}>
                <div className="boris-f3-f07a74e8d6fda4"><span className="boris-social-title-icon"><Icon name="clock" size={18} /></span>Черновики на утверждение ({drafts.length})</div>
                <div className="boris-f3-85e7427e7ff5bd">
                  {drafts.map((p) => (
                    <div key={p.id} className="boris-social-post-card" style={{ border:"1px solid " + T.line, borderRadius:"12px", padding:"14px", display:"flex", gap:"14px", alignItems:"flex-start" }}>
                      {p.banner && bannerUrls[p.banner] && (
                        <div onClick={() => setZoom(bannerUrls[p.banner])} title="Открыть крупно" className="boris-f3-65b4c322b4b9bc">
                          <img src={bannerUrls[p.banner]} alt=""  className="boris-f3-3a90ca11f543bc"/>
                          <div className="boris-f3-31a0154f903402">🔍</div>
                        </div>
                      )}
                      <div className="boris-f3-0070778df76ac3">
                        <div style={{ fontSize:"12px", color:T.muted, marginBottom:"4px" }}>{p.created_at}</div>
                        <div style={{ fontSize:"14px", color:T.ink, whiteSpace:"pre-wrap" }}>{cleanPostText(p.preview || p.text)}</div>
                        <div className="boris-f3-fb169f62bbd1ce">
                          <button className="soc-btn" disabled={busy} onClick={() => publishPost(p.id)} style={{ ...btn([T.green1, T.green2]), padding:"8px 16px", fontSize:"13px" }}>Опубликовать</button>
                          <button className="soc-btn" disabled={busy} onClick={() => { setEditId(editId === p.id ? "" : p.id); setEditText(p.text || ""); setSchedId(""); }} style={{ border:"1px solid " + T.line, background:"#fff", color:T.ink, borderRadius:"10px", padding:"8px 16px", fontSize:"13px", fontWeight:700, cursor:"pointer" }}>Редактировать</button>
                          <button className="soc-btn" disabled={busy} onClick={() => { setSchedId(schedId === p.id ? "" : p.id); setSchedWhen((p.publish_at || "").replace(" ", "T")); setEditId(""); }} style={{ border:"1px solid " + T.line, background:"#fff", color:T.ink, borderRadius:"10px", padding:"8px 16px", fontSize:"13px", fontWeight:700, cursor:"pointer" }}>Когда разместить</button>
                          <button className="soc-btn boris-f3-990af6b92041d2" disabled={busy} onClick={() => removePost(p.id)}>Удалить</button>
                        </div>
                        {p.publish_at
                          ? <div style={{ fontSize:"13px", color:T.green2, marginTop:"8px" }}>🕒 Запланировано на {p.publish_at}</div>
                          : <div style={{ fontSize:"13px", color:T.muted, marginTop:"8px" }}>⏳ Если не изменить, пост выйдет автоматически. Можно отредактировать текст или задать своё время.</div>}
                        {editId === p.id && (
                          <div className="boris-f3-726e8b317f3298">
                            <textarea className="soc-in" style={{ ...inp, minHeight:"170px", resize:"vertical" }} value={editText} onChange={(e) => setEditText(e.target.value)} />
                            <div className="boris-f3-ff3491edc4270f">
                              <button className="soc-btn" disabled={busy} onClick={() => saveEdit(p.id)} style={{ ...btn([T.green1, T.green2]), padding:"8px 16px", fontSize:"13px" }}>Сохранить текст</button>
                              <button className="soc-btn" onClick={() => setEditId("")} style={{ border:"1px solid " + T.line, background:"#fff", color:T.muted, borderRadius:"10px", padding:"8px 16px", fontSize:"13px", fontWeight:700, cursor:"pointer" }}>Отмена</button>
                            </div>
                          </div>
                        )}
                        {schedId === p.id && (
                          <div className="boris-f3-481a83bf80ec38">
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
              <div className="boris-social-card boris-social-best-card" style={card}>
                <div className="boris-f3-0af6424569e1b0"><span className="boris-social-title-icon"><Icon name="award" size={18} /></span>Лучшие посты</div>
                <div style={{ fontSize:"13px", color:T.muted, marginBottom:"14px" }}>
                  Посты, на которые читатели откликались чаще всего. Борис учитывает именно их, когда пишет новые.
                </div>
                <div className="boris-f3-e1c29e9b863f3e">
                  {published
                    .filter((x: any) => x.stats && x.stats.er != null && x.stats.reactions > 0)
                    .sort((a: any, b: any) => (b.stats.er || 0) - (a.stats.er || 0))
                    .slice(0, 3)
                    .map((p: any, i: number) => (
                      <div key={p.id} className="boris-social-post-card" style={{ border:"1px solid " + T.line, borderRadius:"12px", padding:"12px 14px", display:"flex", gap:"12px", alignItems:"flex-start" }}>
                        <div className="boris-f3-34fc330001ffe6">{["🥇","🥈","🥉"][i]}</div>
                        <div className="boris-f3-0070778df76ac3">
                          <div style={{ fontSize:"14px", color:T.ink, maxHeight:"44px", overflow:"hidden" }}>{cleanPostText(p.text)}</div>
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
              <div className="boris-social-card boris-social-published-card" style={card}>
                <div className="boris-f3-f07a74e8d6fda4"><span className="boris-social-title-icon"><Icon name="send" size={18} /></span>Опубликованные ({published.length})</div>
                <div className="boris-f3-85e7427e7ff5bd">
                  {published.map((p) => (
                    <div key={p.id} className="boris-social-post-card" style={{ border:"1px solid " + T.line, borderRadius:"12px", padding:"14px", display:"flex", gap:"14px", alignItems:"flex-start" }}>
                      {p.banner && bannerUrls[p.banner] && (
                        <div onClick={() => setZoom(bannerUrls[p.banner])} title="Открыть крупно" className="boris-f3-7f0a41ed4a6b6c">
                          <img src={bannerUrls[p.banner]} alt=""  className="boris-f3-5eaf4e405b26db"/>
                          <div className="boris-f3-31a0154f903402">🔍</div>
                        </div>
                      )}
                      <div className="boris-f3-0070778df76ac3">
                        <div style={{ fontSize:"12px", color:T.muted, marginBottom:"4px" }}>{p.created_at}</div>
                        <div style={{ fontSize:"14px", color:T.ink, whiteSpace:"pre-wrap", maxHeight:"90px", overflow:"hidden" }}>{cleanPostText(p.preview || p.text)}</div>
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
                        <div className="boris-f3-fb169f62bbd1ce">
                          <button className="soc-btn boris-f3-990af6b92041d2" disabled={busy} onClick={() => unpublishPost(p.id)}>Удалить из канала</button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="boris-social-empty-card" style={{ ...card, textAlign:"center", padding:"40px 24px", color:T.muted }}>
            <div className="boris-f3-b8518f37b1c769 boris-social-empty-icon"><IconChip name="ads" kind="info" size={52} /></div>
            <div style={{ fontSize:"16px", fontWeight:700, color:T.ink, marginBottom:"6px" }}>Пока нет ни одного проекта</div>
            <div className="boris-f3-d25bc62ee86bf2">Создайте проект — задайте каналы, стиль и темы, и бот начнёт постить.</div>
            <button className="soc-btn" onClick={newProject} style={btn([T.blue1, T.blue2])}>＋ Создать первый проект</button>
          </div>
        )}

        {zoom && (
          <div onClick={() => setZoom("")} className="boris-f3-6dde08bc8427d8">
            <img src={zoom} alt=""  className="boris-f3-e8d4dced5de9fe"/>
          </div>
        )}

        {err && <div className="boris-f3-78b52f936c764a">{err}</div>}
      </div>
    </div>
  );
}
