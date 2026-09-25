"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/app/components/shell/AppShell";
import { useAccounts } from "@/app/lib/AccountContext";
import { Alert, Button, Card, EmptyState, Icon, Page } from "@/app/ui";
import avitoStyles from "./avitoProfile.module.css";

type Form = {
  website: string;
  niche: string;
  description: string;
  advantages: string;
  tone: string;
  clientDescription: string;
};

const EMPTY: Form = { website: "", niche: "", description: "", advantages: "", tone: "", clientDescription: "" };

function authHeaders(extra: Record<string, string> = {}) {
  const token = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return { ...extra, Authorization: `Bearer ${token}` };
}

function FieldCard({ icon, tone, label, children }: { icon: "globe" | "briefcase" | "messages"; tone: string; label: string; children: React.ReactNode }) {
  return <div className={`boris-company-mini-card tone-${tone}`}>
    <span className="boris-company-mini-icon"><Icon name={icon} size={19}/></span>
    <div className="boris-company-mini-content"><span>{label}</span>{children}</div>
  </div>;
}

function LongField({ icon, tone, label, hint, value, onChange, wide = false }: { icon: "file" | "star" | "user"; tone: string; label: string; hint?: string; value: string; onChange: (value: string) => void; wide?: boolean }) {
  return <div className={`boris-company-long-card tone-${tone}${wide ? " is-wide" : ""}`}>
    <div className="boris-company-long-head">
      <span className="boris-company-long-icon"><Icon name={icon} size={19}/></span>
      <div><b>{label}</b>{hint ? <small>{hint}</small> : null}</div>
      <span className="boris-company-counter">{value.length} / 1200</span>
    </div>
    <textarea value={value} maxLength={1200} rows={4} onChange={(e)=>onChange(e.target.value)} />
  </div>;
}


type AvitoPrepForm = {
  about: string; experience: string; guarantees: string; contracts: string;
  address: string; work_hours: string; payment_methods: string;
  photo_ids: number[]; logo_media_id: number | null;
};
const AVITO_EMPTY: AvitoPrepForm = { about:"", experience:"", guarantees:"", contracts:"", address:"", work_hours:"", payment_methods:"", photo_ids:[], logo_media_id:null };

function AvitoProfileSection({ accountId, companyName }: { accountId: string; companyName: string }) {
  const [form, setForm] = useState<AvitoPrepForm>(AVITO_EMPTY);
  const [suggested, setSuggested] = useState<Partial<AvitoPrepForm>>({});
  const [media, setMedia] = useState<any[]>([]);
  const [completion, setCompletion] = useState(0);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  const loadProfile = useCallback(async () => {
    if (!accountId) return;
    setLoading(true); setError(""); setNote("");
    try {
      const encoded = encodeURIComponent(accountId);
      const [profileRes, mediaRes] = await Promise.all([
        fetch(`/api/accounts/${encoded}/avito-profile-prep`, { headers: authHeaders(), cache:"no-store" }),
        fetch(`/api/media/list?account_id=${encoded}&source=mine&limit=24&sort=new`, { headers: authHeaders(), cache:"no-store" }),
      ]);
      const profile = await profileRes.json();
      const mediaData = await mediaRes.json();
      if (!profileRes.ok || profile?.status !== "ok") throw new Error(profile?.detail || profile?.message || "Не удалось загрузить профиль Avito");
      const f = profile.fields || {};
      setForm({
        about:f.about||"", experience:f.experience||"", guarantees:f.guarantees||"", contracts:f.contracts||"",
        address:f.address||"", work_hours:f.work_hours||"", payment_methods:f.payment_methods||"",
        photo_ids:Array.isArray(profile.photo_ids)?profile.photo_ids:[], logo_media_id:profile.logo_media_id||null,
      });
      setSuggested(profile.suggested || {});
      setCompletion(Number(profile.completion || 0));
      setConnected(Boolean(profile.avito_connected));
      const own = (mediaData?.items || []).filter((m:any)=>m.account_id===accountId && m.file_state==="present");
      setMedia(own);
    } catch (e:any) { setError(e?.message || "Не удалось загрузить подготовку профиля Avito"); }
    finally { setLoading(false); }
  }, [accountId]);

  useEffect(() => { setForm(AVITO_EMPTY); setMedia([]); setCompletion(0); void loadProfile(); }, [loadProfile]);

  function fillKnown() {
    setForm(prev => ({
      ...prev,
      about: prev.about || String(suggested.about || ""),
      experience: prev.experience || String(suggested.experience || ""),
      guarantees: prev.guarantees || String(suggested.guarantees || ""),
      contracts: prev.contracts || String(suggested.contracts || ""),
      address: prev.address || String(suggested.address || ""),
      work_hours: prev.work_hours || String(suggested.work_hours || ""),
      payment_methods: prev.payment_methods || String(suggested.payment_methods || ""),
    }));
    setNote("BORIS подставил только уже подтверждённые данные этого аккаунта. Проверьте их перед переносом в Avito.");
  }

  async function savePrep() {
    setSaving(true); setError(""); setNote("");
    try {
      const res = await fetch(`/api/accounts/${encodeURIComponent(accountId)}/avito-profile-prep`, {
        method:"POST", headers:authHeaders({"Content-Type":"application/json"}), body:JSON.stringify(form),
      });
      const data = await res.json();
      if (!res.ok || data?.status !== "ok") throw new Error(data?.detail || data?.message || "Не удалось сохранить");
      setCompletion(Number(data.completion || 0));
      setNote(data.transfer_ready ? "Профиль полностью подготовлен. Можно перенести данные и фотографии в Avito." : "Подготовка сохранена. BORIS продолжит показывать, чего не хватает.");
      await loadProfile();
    } catch (e:any) { setError(e?.message || "Не удалось сохранить подготовку профиля"); }
    finally { setSaving(false); }
  }

  function togglePhoto(id:number) {
    setForm(prev => {
      const selected = prev.photo_ids.includes(id);
      const next = selected ? prev.photo_ids.filter(x=>x!==id) : [...prev.photo_ids, id].slice(0,10);
      return {...prev, photo_ids:next};
    });
  }

  async function copyAbout() {
    if (!form.about) return;
    try { await navigator.clipboard.writeText(form.about); setNote("Текст «Об исполнителе» скопирован."); }
    catch { setError("Не удалось скопировать текст. Выделите его вручную."); }
  }

  return <Card className={avitoStyles.wrap}>
    <div className={avitoStyles.head}>
      <div className={avitoStyles.headTitle}>
        <span className={avitoStyles.mainIcon}><Icon name="building" size={22}/></span>
        <div><span>ПРОФИЛЬ НА AVITO</span><h2>Оформление профиля компании</h2><p>BORIS собирает данные и фотографии только выбранного аккаунта и готовит всё для раздела «Управление профилем» Avito.</p></div>
      </div>
      <div className={avitoStyles.progressCard}>
        <div><b>{completion}%</b><span>готово</span></div>
        <div className={avitoStyles.progress}><i style={{width:`${completion}%`}}/></div>
        <small>{connected ? "Avito подключён" : "Сначала подключите Avito API"}</small>
      </div>
    </div>

    <div className={avitoStyles.apiNotice}><Icon name="alert" size={17}/><span><b>BORIS подготовит профиль полностью.</b><small>Публичный API Avito не даёт BORIS права менять эти поля профиля напрямую. Поэтому сохранение здесь не выдаётся за публикацию: готовые данные и выбранные фото переносятся в «Управление профилем» Avito.</small></span></div>
    {error ? <Alert kind="danger">{error}</Alert> : null}
    {note ? <Alert kind="success">{note}</Alert> : null}

    {loading ? <div className={avitoStyles.loading}>Загружаю данные профиля…</div> : <>
      <div className={avitoStyles.toolbar}>
        <Button kind="ghost" onClick={fillKnown}><Icon name="memory" size={16}/>Заполнить известными данными</Button>
        <span>BORIS использует только подтверждённые сведения компании «{companyName}».</span>
      </div>

      <div className={avitoStyles.fields}>
        <label className={avitoStyles.wide}><span>Об исполнителе / о компании</span><textarea rows={6} value={form.about} onChange={e=>setForm(p=>({...p,about:e.target.value}))} placeholder="Почему клиенту стоит выбрать именно вас: опыт, подход, преимущества, гарантии"/><small>{form.about.length} символов</small></label>
        <label><span>Опыт</span><input value={form.experience} onChange={e=>setForm(p=>({...p,experience:e.target.value}))} placeholder="Например: 9 лет на рынке"/></label>
        <label><span>Адрес / место работы</span><input value={form.address} onChange={e=>setForm(p=>({...p,address:e.target.value}))} placeholder="Город, адрес офиса, шоурума или точки"/></label>
        <label><span>Гарантии</span><textarea rows={3} value={form.guarantees} onChange={e=>setForm(p=>({...p,guarantees:e.target.value}))} placeholder="Какие гарантии получает клиент"/></label>
        <label><span>Договоры и документы</span><textarea rows={3} value={form.contracts} onChange={e=>setForm(p=>({...p,contracts:e.target.value}))} placeholder="Работа по договору, акт, расписка, документы"/></label>
        <label><span>График работы</span><input value={form.work_hours} onChange={e=>setForm(p=>({...p,work_hours:e.target.value}))} placeholder="Например: ежедневно 09:00–20:00"/></label>
        <label><span>Способы оплаты</span><input value={form.payment_methods} onChange={e=>setForm(p=>({...p,payment_methods:e.target.value}))} placeholder="Наличные, карта, перевод, безналичный расчёт"/></label>
      </div>

      <div className={avitoStyles.photoHead}><div><h3>Фотографии профиля</h3><p>Выберите до 10 материалов. Здесь показываются только фотографии текущего аккаунта.</p></div><span>{form.photo_ids.length} выбрано</span></div>
      {media.length ? <div className={avitoStyles.mediaGrid}>{media.map((m:any)=>{
        const selected=form.photo_ids.includes(Number(m.id)); const logo=form.logo_media_id===Number(m.id); const src=m.thumb_url||m.preview_url||m.original_url;
        return <div key={m.id} className={`${avitoStyles.mediaCard} ${selected?avitoStyles.selected:""}`}>
          {src ? <img src={src} alt={m.filename||"Фото компании"}/> : <div className={avitoStyles.noImage}>Фото</div>}
          <button type="button" className={avitoStyles.pick} onClick={()=>togglePhoto(Number(m.id))}>{selected?"✓ Выбрано":"Выбрать"}</button>
          <button type="button" className={`${avitoStyles.logo} ${logo?avitoStyles.logoOn:""}`} onClick={()=>setForm(p=>({...p,logo_media_id:logo?null:Number(m.id)}))}>{logo?"Логотип ✓":"Как логотип"}</button>
        </div>;
      })}</div> : <div className={avitoStyles.emptyMedia}><Icon name="file" size={20}/><div><b>В медиатеке пока нет фотографий</b><span>Загрузите фото компании, сотрудников, офиса или работ в «Объявления → Медиатека».</span></div></div>}

      <div className={avitoStyles.actions}>
        <Button onClick={savePrep} disabled={saving}>{saving?"Сохраняю…":"Сохранить подготовку"}</Button>
        <Button kind="ghost" onClick={copyAbout} disabled={!form.about}>Копировать текст</Button>
        <a href="https://www.avito.ru/profile/extended" target="_blank" rel="noopener noreferrer">Открыть управление профилем Avito →</a>
        <span>Другие аккаунты BORIS не изменяются.</span>
      </div>
    </>}
  </Card>;
}

export default function CompanyPage() {
  const { selectedAccountId, selectedAccount, loading: accountsLoading } = useAccounts();
  const [section, setSection] = useState<"company" | "avito" | "boris">("company");
  const [form, setForm] = useState<Form>(EMPTY);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  const load = useCallback(async () => {
    if (!selectedAccountId) return;
    setLoading(true); setError(""); setSaved("");
    try {
      const res = await fetch(`/api/accounts/${encodeURIComponent(selectedAccountId)}`, { headers: authHeaders(), cache: "no-store" });
      const data = await res.json();
      if (!res.ok || data?.status !== "ok" || !data?.account) throw new Error(data?.message || `HTTP ${res.status}`);
      const account = data.account;
      setForm({
        website: account.company_website || "",
        niche: account.company_niche || "",
        description: account.company_description || "",
        advantages: account.company_advantages || "",
        tone: account.company_tone || "",
        clientDescription: account.company_client_description || "",
      });
    } catch {
      setForm(EMPTY);
      setError("Не удалось получить реальные данные компании");
    } finally { setLoading(false); }
  }, [selectedAccountId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setSection("company"); }, [selectedAccountId]);

  async function save() {
    if (!selectedAccountId) return;
    setSaving(true); setError(""); setSaved("");
    try {
      const res = await fetch(`/api/accounts/${encodeURIComponent(selectedAccountId)}/update`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          company_website: form.website,
          company_niche: form.niche,
          company_description: form.description,
          company_advantages: form.advantages,
          company_tone: form.tone,
          company_client_description: form.clientDescription,
        }),
      });
      const data = await res.json();
      if (!res.ok || data?.status !== "ok") throw new Error(data?.message || `HTTP ${res.status}`);
      setSaved("Профиль клиента сохранён и сразу доступен памяти BORIS и рабочим модулям");
      await load();
    } catch {
      setError("Не удалось сохранить данные компании");
    } finally { setSaving(false); }
  }

  const profileRows = useMemo(() => [
    { icon:"briefcase" as const, label:"Сфера деятельности", value:form.niche, direct:true },
    { icon:"globe" as const, label:"Сайт", value:form.website, direct:true },
    { icon:"messages" as const, label:"Тон общения", value:form.tone, direct:true },
    { icon:"file" as const, label:"Описание компании", value:form.description },
    { icon:"star" as const, label:"Преимущества компании", value:form.advantages },
    { icon:"user" as const, label:"Описание клиента", value:form.clientDescription },
  ], [form]);

  return (
    <AppShell active="company" title="О компании" subtitle="Профиль клиента и единая память выбранного аккаунта для рабочих модулей BORIS">
      <Page className="boris-company-reference-page">
        {error ? <Alert kind="danger">{error}</Alert> : null}
        {saved ? <Alert kind="success">{saved}</Alert> : null}
        {!accountsLoading && !selectedAccountId ? <EmptyState title="Не выбран аккаунт" hint="Выберите аккаунт в верхней панели." /> : null}
        {selectedAccountId ? <>
          <div className="boris-company-toolbar">
            <div><span className="boris-company-kicker">ПРОФИЛЬ КЛИЕНТА</span><b>{selectedAccount?.name || selectedAccountId}</b><small>Факты ниже используются BORIS при создании рекламы, ответах и анализе.</small></div>
            <Button kind="ghost" onClick={() => void load()} disabled={loading}><Icon name="refresh" size={16}/>{loading ? "Обновляем…" : "Обновить данные"}</Button>
          </div>

          <nav className={avitoStyles.sectionTabs} aria-label="Разделы данных компании">
            <button type="button" className={section === "company" ? avitoStyles.tabActive : ""} onClick={()=>setSection("company")}><Icon name="building" size={17}/><span><b>Данные компании</b><small>Основные сведения</small></span></button>
            <button type="button" className={section === "avito" ? avitoStyles.tabActive : ""} onClick={()=>setSection("avito")}><Icon name="globe" size={17}/><span><b>Профиль на Avito</b><small>Текст, условия и фото</small></span></button>
            <button type="button" className={section === "boris" ? avitoStyles.tabActive : ""} onClick={()=>setSection("boris")}><Icon name="ai" size={17}/><span><b>Что знает BORIS</b><small>Контекст рабочих модулей</small></span></button>
          </nav>

          {section === "company" ? <div className={avitoStyles.singlePane}>
            <Card className="boris-company-form-card">
              <div className="boris-company-card-head">
                <span className="boris-company-head-icon"><Icon name="building" size={22}/></span>
                <div><span>{selectedAccount?.name || selectedAccountId}</span><h2>Данные компании</h2><p>Редактируйте профиль прямо здесь — после сохранения BORIS использует его как авторитетный контекст аккаунта.</p></div>
              </div>

              {loading ? <div className="boris-company-loading">Загрузка фактических данных…</div> : <div className="boris-company-form-grid">
                <div className="boris-company-section-label"><b>Основные сведения</b><span>Короткие факты, которые BORIS использует во всех рабочих модулях.</span></div>
                <FieldCard icon="globe" tone="blue" label="Сайт">
                  <input value={form.website} onChange={(e)=>setForm(p=>({...p,website:e.target.value}))} placeholder="https://..." inputMode="url" />
                </FieldCard>
                <FieldCard icon="briefcase" tone="violet" label="Сфера деятельности">
                  <input value={form.niche} onChange={(e)=>setForm(p=>({...p,niche:e.target.value}))} placeholder="Что продаёт или делает компания" />
                </FieldCard>
                <FieldCard icon="messages" tone="cyan" label="Тон общения">
                  <input value={form.tone} onChange={(e)=>setForm(p=>({...p,tone:e.target.value}))} placeholder="Например: доброжелательно, профессионально" />
                </FieldCard>
                <div className="boris-company-memory-chip"><Icon name="memory" size={17}/><span><b>Память BORIS</b><small>Все поля сохраняются отдельно по выбранному аккаунту</small></span></div>

                <div className="boris-company-section-label detailed"><b>Подробный контекст</b><span>Описание бизнеса, сильных сторон и клиента — без смешивания разных аккаунтов.</span></div>
                <LongField icon="file" tone="violet" label="Описание компании" value={form.description} onChange={(v)=>setForm(p=>({...p,description:v}))}/>
                <LongField icon="star" tone="green" label="Преимущества компании" value={form.advantages} onChange={(v)=>setForm(p=>({...p,advantages:v}))}/>
                <LongField icon="user" tone="blue" label="Описание клиента" hint="Кто покупает, что для него важно, боли, критерии выбора и мотивация" value={form.clientDescription} onChange={(v)=>setForm(p=>({...p,clientDescription:v}))} wide/>

                <div className="boris-company-save-row">
                  <Button onClick={save} disabled={saving} aria-busy={saving}>{saving ? "Сохраняем…" : "Сохранить изменения"}</Button>
                  <span><Icon name="check" size={15}/> После сохранения новые данные сразу видят МОП, генератор объявлений, кампании, аналитика и ИИ-фотостудия.</span>
                </div>
              </div>}
            </Card>
          </div> : null}

          {section === "boris" ? <div className={avitoStyles.singlePane}>
            <Card className="boris-company-summary-card">
              <div className="boris-company-card-head compact">
                <span className="boris-company-head-icon bot"><Icon name="ai" size={22}/></span>
                <div><span>ПРОФИЛЬ АККАУНТА</span><h2>Что видят рабочие модули</h2><p>Единый контекст выбранного клиента без смешивания аккаунтов.</p></div>
              </div>
              <div className="boris-company-summary-list">
                {profileRows.map((row)=><div key={row.label}>
                  <span className="boris-company-summary-icon"><Icon name={row.icon} size={17}/></span>
                  <span className="boris-company-summary-label">{row.label}</span>
                  <b title={row.value || ""}>{row.direct ? (row.value || "Не указано") : (row.value ? "Заполнено" : "Не заполнено")}</b>
                  <span className={`boris-company-status ${row.value ? "ok" : "empty"}`}><Icon name={row.value ? "check" : "alert"} size={14}/>{row.value ? "Готово" : "Нужно заполнить"}</span>
                </div>)}
              </div>
              <div className="boris-company-summary-note"><Icon name="zap" size={18}/><span><b>Чем полнее профиль, тем точнее BORIS.</b><small>Описание клиента теперь участвует в объявлениях, ответах МОПа, сценариях продаж, кампаниях и визуальном контенте.</small></span></div>
            </Card>
          </div> : null}
          {section === "avito" ? <AvitoProfileSection accountId={selectedAccountId} companyName={selectedAccount?.name || selectedAccountId}/> : null}
        </> : null}
      </Page>
    </AppShell>
  );
}
