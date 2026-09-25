"use client";

import React, { useEffect, useMemo, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "";

type Props = { project?: any };

function bearer() {
  const token = typeof window !== "undefined" ? localStorage.getItem("boris_token") || "" : "";
  return { Authorization: `Bearer ${token}` };
}

export default function SocialPublishingPanel({ project }: Props) {
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [instagram, setInstagram] = useState(true);
  const [youtube, setYoutube] = useState(true);
  const [scheduleAt, setScheduleAt] = useState("");
  const [caption, setCaption] = useState("");

  const connectedPlatforms = useMemo(() => {
    const out: string[] = [];
    if (instagram && data?.accounts?.instagram?.connected) out.push("instagram");
    if (youtube && data?.accounts?.youtube?.connected) out.push("youtube");
    return out;
  }, [data, instagram, youtube]);

  const canPublish = Boolean(project?.id && project?.status === "ready" && project?.outputs?.length);

  async function load() {
    try {
      const response = await fetch(`${API}/api/video-factory/social/status`, { headers: bearer(), cache: "no-store" });
      if (!response.ok) throw new Error(await response.text());
      setData(await response.json());
    } catch (error: any) {
      setMessage(error?.message || "Не удалось получить состояние подключений");
    }
  }

  async function connect(platform: "instagram" | "youtube") {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${API}/api/video-factory/social/connect/${platform}`, { method: "POST", headers: bearer() });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || "Не удалось начать подключение");
      if (!body?.ok) {
        const missing = Array.isArray(body?.missing) ? body.missing.join(", ") : "OAuth-настройки";
        setMessage(`${platform === "instagram" ? "Instagram" : "YouTube"}: на сервере ещё не заданы ${missing}. Код модуля готов, но авторизация пока заблокирована настройками приложения.`);
        return;
      }
      if (!body?.authorization_url) throw new Error("Не получена ссылка авторизации");
      window.location.href = body.authorization_url;
    } catch (error: any) {
      setMessage(error?.message || "Ошибка подключения");
    } finally { setBusy(false); }
  }

  async function disconnect(platform: "instagram" | "youtube") {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${API}/api/video-factory/social/disconnect/${platform}`, { method: "POST", headers: bearer() });
      if (!response.ok) throw new Error(await response.text());
      await load();
    } catch (error: any) { setMessage(error?.message || "Не удалось отключить аккаунт"); }
    finally { setBusy(false); }
  }

  async function submit(mode: "publish" | "schedule") {
    if (!project?.id) return;
    if (!connectedPlatforms.length) { setMessage("Сначала подключите хотя бы одну площадку и отметьте её для публикации."); return; }
    if (mode === "schedule" && !scheduleAt) { setMessage("Укажите дату и время публикации."); return; }
    setBusy(true); setMessage("");
    try {
      const payload: any = { platforms: connectedPlatforms, caption, privacy: "private" };
      if (mode === "schedule") payload.scheduled_at = new Date(scheduleAt).toISOString();
      const response = await fetch(`${API}/api/video-factory/projects/${project.id}/${mode === "publish" ? "publish" : "schedule"}`, {
        method: "POST", headers: { ...bearer(), "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || "Публикация не выполнена");
      const items = body?.items || [];
      const result = items.map((x: any) => `${x.platform}: ${x.status}${x.external_url ? ` — ${x.external_url}` : ""}`).join("; ");
      setMessage(mode === "publish" ? `Результат публикации: ${result || "готово"}` : `Добавлено в расписание: ${result || "готово"}`);
      await load();
    } catch (error: any) { setMessage(error?.message || "Ошибка публикации"); }
    finally { setBusy(false); }
  }

  useEffect(() => {
    load();
    if (typeof window !== "undefined") {
      const oauth = new URLSearchParams(window.location.search).get("oauth") || "";
      if (oauth.endsWith("_connected")) setMessage("Соцсеть успешно подключена к BORIS.");
      else if (oauth.endsWith("_error")) setMessage("Провайдер не завершил OAuth. Проверьте настройки приложения и повторите подключение.");
      else if (oauth.endsWith("_cancelled")) setMessage("Подключение отменено.");
    }
  }, []);

  const ig = data?.accounts?.instagram;
  const yt = data?.accounts?.youtube;
  const config = data?.config;
  const recent = Array.isArray(data?.publications) ? data.publications.slice(0, 5) : [];

  return (
    <section style={{ margin: "0 0 22px", border: "1px solid var(--boris-border)", borderRadius: 18, background: "#fff", padding: 20, boxShadow: "0 8px 30px rgba(15,23,42,.05)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div><div style={{ fontSize: 12, fontWeight: 800, letterSpacing: ".06em", color: "var(--boris-text-secondary)" }}>ПУБЛИКАЦИЯ</div><h2 style={{ margin: "5px 0 5px" }}>Instagram Reels + YouTube Shorts</h2><p style={{ margin: 0, color: "var(--boris-text-secondary)" }}>Прямые официальные интеграции BORIS. Metricool для работы этого контура не требуется.</p></div>
        {config?.test_mode && <span style={{ background: "#fff4e5", color: "#a15c00", borderRadius: 999, padding: "7px 11px", fontSize: 12, fontWeight: 800 }}>TEST MODE</span>}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: 12, marginTop: 16 }}>
        <div style={{ border: "1px solid var(--boris-border)", borderRadius: 14, padding: 14 }}>
          <b>Instagram</b><div style={{ marginTop: 6, color: ig?.connected ? "#087f5b" : "var(--boris-text-secondary)" }}>{ig?.connected ? `Подключён${ig?.username ? ` · @${ig.username}` : ""}` : "Не подключён"}</div>
          {!config?.instagram?.configured && <div style={{ fontSize: 12, color: "#b54708", marginTop: 6 }}>Нужны Meta App ID + Secret</div>}
          <button disabled={busy || (!ig?.connected && !config?.instagram?.configured)} onClick={() => ig?.connected ? disconnect("instagram") : connect("instagram")} style={{ marginTop: 10, padding: "8px 12px", borderRadius: 9, border: "1px solid var(--boris-border)", background: "#fff", fontWeight: 700, cursor: "pointer" }}>{ig?.connected ? "Отключить" : "Подключить Instagram"}</button>
        </div>
        <div style={{ border: "1px solid var(--boris-border)", borderRadius: 14, padding: 14 }}>
          <b>YouTube</b><div style={{ marginTop: 6, color: yt?.connected ? "#087f5b" : "var(--boris-text-secondary)" }}>{yt?.connected ? `Подключён${yt?.channel_name ? ` · ${yt.channel_name}` : ""}` : "Не подключён"}</div>
          {!config?.youtube?.configured && <div style={{ fontSize: 12, color: "#b54708", marginTop: 6 }}>Нужны Google OAuth Client ID + Secret</div>}
          <button disabled={busy || (!yt?.connected && !config?.youtube?.configured)} onClick={() => yt?.connected ? disconnect("youtube") : connect("youtube")} style={{ marginTop: 10, padding: "8px 12px", borderRadius: 9, border: "1px solid var(--boris-border)", background: "#fff", fontWeight: 700, cursor: "pointer" }}>{yt?.connected ? "Отключить" : "Подключить YouTube"}</button>
        </div>
      </div>

      {project && <div style={{ marginTop: 16, borderTop: "1px solid var(--boris-border)", paddingTop: 16 }}>
        <div style={{ fontWeight: 800 }}>Текущий ролик: {project.title || project.id}</div>
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 10 }}>
          <label><input type="checkbox" checked={instagram} onChange={e => setInstagram(e.target.checked)} /> Instagram</label>
          <label><input type="checkbox" checked={youtube} onChange={e => setYoutube(e.target.checked)} /> YouTube</label>
        </div>
        <textarea value={caption} onChange={e => setCaption(e.target.value)} placeholder="Необязательно: своя подпись. Если оставить пустым, BORIS возьмёт hook + CTA из проекта." style={{ width: "100%", minHeight: 72, marginTop: 10, border: "1px solid var(--boris-border)", borderRadius: 10, padding: 10, font: "inherit" }} />
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
          <button disabled={busy || !canPublish || !connectedPlatforms.length} onClick={() => submit("publish")} style={{ padding: "10px 15px", border: 0, borderRadius: 10, background: "var(--boris-primary)", color: "#fff", fontWeight: 800, cursor: "pointer" }}>Опубликовать сейчас</button>
          <input type="datetime-local" value={scheduleAt} onChange={e => setScheduleAt(e.target.value)} style={{ padding: "9px 10px", border: "1px solid var(--boris-border)", borderRadius: 10 }} />
          <button disabled={busy || !canPublish || !connectedPlatforms.length || !scheduleAt} onClick={() => submit("schedule")} style={{ padding: "10px 15px", borderRadius: 10, border: "1px solid var(--boris-border)", background: "#fff", fontWeight: 800, cursor: "pointer" }}>В расписание</button>
          {!canPublish && <span style={{ fontSize: 13, color: "var(--boris-text-secondary)" }}>Публикация станет доступна после финального render + QA PASS.</span>}
        </div>
      </div>}

      {message && <div style={{ marginTop: 12, borderRadius: 10, background: "#f7f9fc", padding: 10, fontSize: 13 }}>{message}</div>}
      {recent.length > 0 && <div style={{ marginTop: 14 }}><b style={{ fontSize: 13 }}>Последние публикации</b>{recent.map((row: any) => <div key={row.id} style={{ fontSize: 12, color: "var(--boris-text-secondary)", marginTop: 5 }}>{row.platform} · {row.status}{row.external_url ? ` · ${row.external_url}` : ""}</div>)}</div>}
    </section>
  );
}
