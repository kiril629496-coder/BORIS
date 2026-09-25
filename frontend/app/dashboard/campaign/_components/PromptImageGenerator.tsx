"use client";

import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Card } from "../../../ui";
import { color } from "../../../ui/tokens";
import { apiGet, apiPost } from "../../../lib/api";

const SHOTS = ["HERO_45_LEFT", "HERO_45_RIGHT", "FRONT", "WIDE", "DETAIL_FACADE", "DETAIL_HARDWARE", "DETAIL_COUNTERTOP"];

export default function PromptImageGenerator({ account }: { account: string }) {
  const [prompt, setPrompt] = useState("");
  const [count, setCount] = useState(4);
  const [sid, setSid] = useState<number | null>(null);
  const [session, setSession] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");

  const codes = useMemo(() => SHOTS.slice(0, Math.max(1, count - 1)), [count]);

  async function refresh(id: number) {
    const r = await apiGet(`/api/photo/session/${id}`);
    if (r?.session) setSession(r.session);
    return r?.session;
  }

  useEffect(() => {
    if (!sid || !["running", "deriving", "derived_running"].includes(session?.status || "")) return;
    const t = window.setInterval(() => refresh(sid).catch(() => {}), 4000);
    return () => window.clearInterval(t);
  }, [sid, session?.status]);

  async function create() {
    if (!account || prompt.trim().length < 12) return;
    setBusy("create"); setErr(""); setNote("");
    try {
      const r = await apiPost("/api/photo/session", {
        account_id: account,
        product_id: 0,
        preset: "sovremennyy",
        shots: codes,
        mode: "free_concept",
        prompt: prompt.trim(),
        batch_confirmed: false,
      });
      setSid(r.session_id);
      await refresh(r.session_id);
      setNote("Сцена зафиксирована. Сначала создаём один главный кадр для проверки.");
    } catch (e: any) { setErr(e?.message || "Не удалось создать фотосессию"); }
    finally { setBusy(""); }
  }

  async function startMaster() {
    if (!sid) return;
    setBusy("master"); setErr("");
    try {
      await apiPost(`/api/photo/session/${sid}/master`, {});
      setNote("Главный кадр создаётся через OpenAI. После него остальные ракурсы будут опираться на этот же кадр.");
      await refresh(sid);
    } catch (e: any) { setErr(e?.message || "Не удалось запустить генерацию"); }
    finally { setBusy(""); }
  }

  async function approve(ok: boolean) {
    if (!sid) return;
    setBusy(ok ? "approve" : "reject"); setErr("");
    try {
      await apiPost(`/api/photo/session/${sid}/approve_master`, ok ? { approved: true, codes } : { approved: false });
      setNote(ok ? "Главный кадр принят. Создаём остальные ракурсы с жёсткой фиксацией сцены." : "Главный кадр отклонён — можно переснять.");
      await refresh(sid);
    } catch (e: any) { setErr(e?.message || "Не удалось обработать главный кадр"); }
    finally { setBusy(""); }
  }

  const status = session?.status || "";
  return (
    <div style={{ display: "grid", gap: 14 }}>
      {err && <Alert kind="danger">{err}</Alert>}
      {note && <Alert kind="info">{note}</Alert>}
      <Card>
        <div style={{ fontSize: 18, fontWeight: 800 }}>Генерация картинок</div>
        <div style={{ marginTop: 6, color: color.muted, fontSize: 13, lineHeight: 1.5 }}>
          Опишите сцену. BORIS создаёт один главный контрольный кадр через OpenAI, а остальные ракурсы делает из него как из reference, чтобы интерьер не менялся от фото к фото.
        </div>
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={5}
          placeholder="Например: современная кухня, белые матовые фасады, дубовая столешница, светлый линолеум с мелким серым рисунком, окно слева, дневной свет..."
          style={{ width: "100%", boxSizing: "border-box", marginTop: 14, padding: 12, borderRadius: 10, border: `1px solid ${color.line}`, resize: "vertical", font: "inherit" }} />
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
          {[4, 6, 8].map((n) => <Button key={n} kind={count === n ? undefined : "ghost"} onClick={() => setCount(n)}>{n} фото</Button>)}
        </div>
        <div style={{ marginTop: 12, padding: 12, borderRadius: 10, background: "#f5f9ff", fontSize: 12.5, lineHeight: 1.5, color: color.muted }}>
          Контроль согласованности: фиксируются цвет и рисунок пола, направление швов/досок, стены, окна, двери, освещение, мебель, техника, ручки, столешница, декор и положение крупных объектов. Между кадрами меняется только ракурс камеры.
        </div>
        {!session && <div style={{ marginTop: 14 }}><Button disabled={busy !== "" || prompt.trim().length < 12 || !account} onClick={create}>{busy === "create" ? "Подготавливаю…" : "Подготовить фотосессию"}</Button></div>}
      </Card>

      {session && status === "draft" && <Card><div style={{fontWeight:800}}>Сначала — контрольный главный контрольный кадр</div><div style={{marginTop:6,color:color.muted,fontSize:13}}>Вы проверяете его до того, как BORIS потратит деньги на всю серию.</div><div style={{marginTop:12}}><Button disabled={busy!==""} onClick={startMaster}>{busy === "master" ? "Создаю…" : "Создать главный кадр"}</Button></div></Card>}
      {session && status === "running" && <Card><div style={{fontWeight:800}}>Создаём главный кадр…</div><div style={{marginTop:6,color:color.muted,fontSize:13}}>Страница обновится автоматически.</div></Card>}
      {session && status === "master_review" && <Card>
        <div style={{fontWeight:800}}>Проверьте главный кадр</div>
        <div style={{marginTop:6,color:color.muted,fontSize:13}}>Особенно проверьте пол, его рисунок, фасады, технику, окна, свет и геометрию.</div>
        {session.master_url && <img src={session.master_url} alt="Главный кадр" style={{display:"block",width:"100%",maxWidth:760,marginTop:14,borderRadius:12,border:`1px solid ${color.line}`}} />}
        <div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:14}}><Button disabled={busy!==""} onClick={()=>approve(true)}>Всё правильно — сделать остальные</Button><Button kind="ghost" disabled={busy!==""} onClick={()=>approve(false)}>Переснять</Button></div>
      </Card>}
      {session && ["deriving","derived_running"].includes(status) && <Card><div style={{fontWeight:800}}>Создаём остальные ракурсы</div><div style={{marginTop:6,color:color.muted,fontSize:13}}>Готово {session.shots_done || 0} из {session.shots_planned || count}. Все кадры используют master как reference.</div></Card>}
      {session && ["completed","partial"].includes(status) && <Card><div style={{fontWeight:800}}>Серия готова</div><div style={{marginTop:6,color:color.muted,fontSize:13}}>Результаты сохранены в общей Медиатеке. Отклонённые по несогласованные кадры не считаются готовыми.</div><div style={{marginTop:12,display:"flex",gap:8}}><a href="/dashboard/campaign/media"><Button>Открыть Медиатеку</Button></a><Button kind="ghost" onClick={()=>{setSession(null);setSid(null);setNote("");}}>Новая серия</Button></div></Card>}
    </div>
  );
}
