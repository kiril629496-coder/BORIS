"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/app/components/shell/AppShell";
import { useAccounts } from "@/app/lib/AccountContext";
import { Alert, Button, Card, EmptyState, Field, Input, Page, Textarea } from "@/app/ui";
import { color, font, space } from "@/app/ui/tokens";

function authHeaders(extra: Record<string, string> = {}) {
  const token = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return { ...extra, Authorization: `Bearer ${token}` };
}

export default function SiteBuildPage() {
  const { selectedAccountId, selectedAccount, loading: accountsLoading } = useAccounts();
  const [videos, setVideos] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [niche, setNiche] = useState("");
  const [comment, setComment] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const screens = [
    { img: "beton_zavod.png", title: "Бетонный завод", tag: "Производство" },
    { img: "almaznoe_burenie.png", title: "Алмазное бурение", tag: "Строительные услуги" },
    { img: "gruzoperevozki.png", title: "Грузоперевозки", tag: "Логистика" },
    { img: "arenda_spectehniki.png", title: "Аренда спецтехники", tag: "Аренда" },
    { img: "landshaftnyy_dizayn.png", title: "Ландшафтный дизайн", tag: "Услуги" },
    { img: "lazernaya_epilyaciya.png", title: "Лазерная эпиляция", tag: "Бьюти" },
    { img: "stihi_pesni_na_zakaz.png", title: "Стихи и песни на заказ", tag: "Творчество" },
  ];

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const res = await fetch("/api/sitebuild/videos", { headers: authHeaders(), cache: "no-store" });
      const data = await res.json();
      if (!res.ok || data?.status === "error") throw new Error(data?.message || `HTTP ${res.status}`);
      setVideos(Array.isArray(data?.videos) ? data.videos : []);
    } catch {
      setError("Не удалось получить материалы по созданию сайтов");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function submit() {
    if (!selectedAccountId || !phone.trim()) return;
    setSending(true); setError(""); setDone("");
    try {
      const form = new FormData();
      form.append("name", name);
      form.append("phone", phone);
      form.append("niche", niche);
      form.append("comment", comment);
      form.append("account_id", selectedAccountId);
      const res = await fetch("/api/sitebuild/order", { method: "POST", headers: authHeaders(), body: form });
      const data = await res.json();
      if (!res.ok || data?.status !== "ok") throw new Error(data?.message || `HTTP ${res.status}`);
      setDone("Заявка сохранена");
      setComment("");
    } catch {
      setError("Не удалось сохранить заявку на сайт");
    } finally { setSending(false); }
  }

  return (
    <AppShell active="sitebuild" title="Создание сайтов" subtitle="Заявка и материалы через существующий sitebuild backend">
      <Page className="boris-sitebuild-reference-page">
        {error ? <Alert kind="danger">{error}</Alert> : null}
        {done ? <Alert kind="success">{done}</Alert> : null}
        {!accountsLoading && !selectedAccountId ? <EmptyState title="Не выбран аккаунт" hint="Выберите аккаунт в верхней панели." /> : null}
        {selectedAccountId ? <>
          <Card className="boris-sitebuild-showcase-card">
            <div style={{display:"flex",justifyContent:"space-between",gap:space.md,alignItems:"flex-end",flexWrap:"wrap"}}>
              <div>
                <div style={{...font.small,color:color.muted}}>Примеры готовых проектов</div>
                <h2 style={{...font.h2,margin:"5px 0 4px"}}>Сайты BORIS вживую</h2>
                <div style={{...font.small,color:color.muted}}>Реальные скриншоты и видео из старого рабочего интерфейса — теперь внутри этой вкладки.</div>
              </div>
            </div>
            <div className="boris-sitebuild-marquee" aria-label="Примеры сайтов">
              <div className="boris-sitebuild-marquee-track">
                {[...screens,...screens].map((sc,index)=><article className="boris-sitebuild-slide" key={`${sc.img}-${index}`}>
                  <img src={`/images/_sitebuild/screens/${sc.img}`} alt={sc.title} loading="lazy" />
                  <div className="boris-sitebuild-slide-meta"><b>{sc.title}</b><span>{sc.tag}</span></div>
                </article>)}
              </div>
            </div>
            <div style={{marginTop:space.md}}>
              <h3 style={{...font.h3,margin:"0 0 4px"}}>Видео готовых сайтов</h3>
              <div style={{...font.small,color:color.muted}}>Можно посмотреть реальные записи работы сайтов прямо здесь.</div>
              {videos.length ? <div className="boris-sitebuild-video-grid">{videos.map((video,index)=><article key={String(video.filename || index)} className="boris-sitebuild-video-card"><video src={video.url} controls preload="metadata" /><div><b>{video.title || video.filename || `Видео ${index+1}`}</b>{video.description ? <span>{video.description}</span> : null}</div></article>)}</div> : <div style={{...font.small,color:color.muted,marginTop:10}}>Видео пока не добавлены.</div>}
            </div>
          </Card>
          <div className="boris-sitebuild-reference-grid">
          <Card className="boris-sitebuild-form-card">
            <div style={{ ...font.small, color: color.muted }}>{selectedAccount?.name || selectedAccountId}</div>
            <h2 style={{ ...font.h2, margin: "6px 0 16px" }}>Заказать сайт</h2>
            <div style={{ display: "grid", gap: space.sm, maxWidth: 720 }}>
              <Field label="Ваше имя"><Input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" /></Field>
              <Field label="Телефон" hint="Обязательное поле"><Input value={phone} onChange={(e) => setPhone(e.target.value)} type="tel" autoComplete="tel" required /></Field>
              <Field label="Сфера бизнеса"><Input value={niche} onChange={(e) => setNiche(e.target.value)} /></Field>
              <Field label="Комментарий и пожелания"><Textarea rows={4} value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
              <div><Button onClick={submit} disabled={sending || !phone.trim()} aria-busy={sending}>{sending ? "Сохраняем…" : "Отправить заявку"}</Button></div>
            </div>
          </Card>
          <Card className="boris-sitebuild-materials-card">
            <div style={{ display: "flex", justifyContent: "space-between", gap: space.md, alignItems: "center", flexWrap: "wrap" }}>
              <h2 style={{ ...font.h2, margin: 0 }}>Материалы</h2>
              <Button kind="ghost" onClick={() => void load()} disabled={loading}>{loading ? "Обновляем…" : "Обновить"}</Button>
            </div>
            {videos.length ? <div style={{ display: "grid", gap: space.sm, marginTop: space.md }}>{videos.map((video, index) => <div key={String(video.filename || index)} style={{ padding: 12, border: `1px solid ${color.line}`, borderRadius: 12 }}><b>{video.title || video.filename || `Видео ${index + 1}`}</b></div>)}</div> : <EmptyState title="Материалов пока нет" hint="Backend не вернул опубликованные материалы sitebuild." />}
          </Card>
          <Card className="boris-sitebuild-status-card">
            <div style={{...font.small,color:color.muted}}>Рабочий контур</div>
            <h2 style={{...font.h2,margin:"5px 0 12px"}}>Подготовка сайта</h2>
            <div className="boris-sitebuild-status-list">
              <span className="ok">✓ Аккаунт выбран</span>
              <span className={phone.trim() ? "ok" : "wait"}>{phone.trim() ? "✓" : "○"} Контакт для связи</span>
              <span className={niche.trim() ? "ok" : "wait"}>{niche.trim() ? "✓" : "○"} Сфера бизнеса</span>
              <span className={videos.length ? "ok" : "wait"}>{videos.length ? "✓" : "○"} Материалы: {videos.length}</span>
            </div>
            <div className="boris-sitebuild-note">Заявка уйдёт в существующий sitebuild backend. Никаких данных и статусов BORIS не придумывает.</div>
          </Card>
          </div>
        </> : null}
      </Page>
    </AppShell>
  );
}
