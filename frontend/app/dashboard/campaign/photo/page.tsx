"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { EcShell } from "../../../ui/EcShell";
import { Card, Button, Alert, Skeleton } from "../../../ui";
import { color } from "../../../ui/tokens";
import { apiGet, apiPost, apiUpload } from "../../../lib/api";
import { useAccounts } from "../../../lib/AccountContext";

type InteriorPreset = {
  code: string;
  about: string;
};

type Session = any;

type SetKey = "minimal" | "optimal" | "extended" | "manual";

const INTERIORS: Record<
  string,
  { title: string; description: string }
> = {
  client_auto: {
    title: "Авто под бизнес",
    description: "BORIS сам подберёт коммерчески уместное окружение из данных выбранного клиента",
  },
  sovremennyy: {
    title: "Современный",
    description: "Светлый интерьер, дерево, естественный дневной свет",
  },
  minimalizm: {
    title: "Минимализм",
    description: "Чистое пространство, спокойные оттенки, минимум деталей",
  },
  premium: {
    title: "Премиум",
    description: "Тёплые глубокие оттенки, камень и выразительные детали",
  },
  neoklassika: {
    title: "Неоклассика",
    description: "Светлые стены, декоративные панели и паркет",
  },
  novostroyka: {
    title: "Новостройка",
    description: "Светлая новая квартира с нейтральной отделкой",
  },
  loft: {
    title: "Лофт",
    description: "Кирпич, бетон и тёмные металлические детали",
  },
  semeynyy: {
    title: "Семейный",
    description: "Тёплый уютный интерьер для повседневной жизни",
  },
};

const SHOTS: Record<
  string,
  { title: string; description: string }
> = {
  HERO_45_LEFT: {
    title: "Ракурс слева",
    description: "Товар под углом слева",
  },
  HERO_45_RIGHT: {
    title: "Ракурс справа",
    description: "Товар под углом справа",
  },
  FRONT: {
    title: "Вид спереди",
    description: "Прямой фронтальный ракурс",
  },
  WIDE: {
    title: "Общий план",
    description: "Товар вместе с окружающим интерьером",
  },
  DETAIL_FACADE: {
    title: "Деталь фасада",
    description: "Материал, поверхность и фактура",
  },
  DETAIL_HARDWARE: {
    title: "Фурнитура",
    description: "Ручки, петли и другие элементы",
  },
  DETAIL_COUNTERTOP: {
    title: "Столешница",
    description: "Материал и край столешницы",
  },
  OPEN_STORAGE: {
    title: "Открытое хранение",
    description: "Внутреннее наполнение секции",
  },
  LIFESTYLE: {
    title: "Интерьерный кадр",
    description: "Товар в естественной обстановке",
  },
};

const SETS: Record<
  Exclude<SetKey, "manual">,
  { title: string; description: string; codes: string[] }
> = {
  minimal: {
    title: "Минимальный",
    description: "3 фотографии",
    codes: ["HERO_45_LEFT", "FRONT"],
  },
  optimal: {
    title: "Оптимальный",
    description: "5 фотографий",
    codes: [
      "HERO_45_LEFT",
      "HERO_45_RIGHT",
      "FRONT",
      "DETAIL_FACADE",
    ],
  },
  extended: {
    title: "Расширенный",
    description: "8 фотографий",
    codes: [
      "HERO_45_LEFT",
      "HERO_45_RIGHT",
      "FRONT",
      "WIDE",
      "DETAIL_FACADE",
      "DETAIL_HARDWARE",
      "OPEN_STORAGE",
    ],
  },
};

function statusRu(value: string) {
  const map: Record<string, string> = {
    draft: "Готово к запуску",
    running: "Создаётся главный кадр",
    master_review: "Нужно проверить главный кадр",
    derived_running: "Создаются остальные фотографии",
    deriving: "Создаются остальные фотографии",
    completed: "Фотосессия завершена",
    failed: "Произошла ошибка",
    paused_limit: "Создание временно остановлено",
  };

  return map[value] || "Фотосессия";
}

export default function PhotoStudioPage() {
  const { selectedAccountId } = useAccounts();
  const search = useSearchParams();
  const productId = Number(search.get("product") || 0);

  const [account, setAccount] = useState("");
  const [selectedProductId, setSelectedProductId] = useState(productId);
  const [catalogItems, setCatalogItems] = useState<any[]>([]);
  const [creationMode, setCreationMode] = useState<"product" | "prompt" | "reference">(
    productId > 0 ? "product" : "prompt"
  );
  const [prompt, setPrompt] = useState(search.get("prompt") || "");
  const [batchProjects, setBatchProjects] = useState(1);
  const [batchPhotos, setBatchPhotos] = useState(10);
  const [batchResult, setBatchResult] = useState<any>(null);
  const [referenceFile, setReferenceFile] = useState<File | null>(null);
  const [referenceMediaId, setReferenceMediaId] = useState(0);
  const [interiors, setInteriors] = useState<InteriorPreset[]>([]);
  const [interior, setInterior] = useState("client_auto");
  const [clientContext, setClientContext] = useState<any>(null);

  const [setKey, setSetKey] = useState<SetKey>("optimal");
  const [manualShots, setManualShots] = useState<string[]>(
    SETS.optimal.codes
  );

  const [session, setSession] = useState<Session | null>(null);
  const [history, setHistory] = useState<Session[]>([]);
  const [sid, setSid] = useState<number | null>(null);

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");

  const selectedShots = useMemo(() => {
    if (setKey === "manual") return manualShots;
    return SETS[setKey].codes;
  }, [setKey, manualShots]);

  const totalPhotos = selectedShots.length + 1;

  const loadSession = useCallback(async (id: number) => {
    const r = await apiGet("/api/photo/session/" + id);

    if (r?.session) {
      setSession(r.session);
    }

    return r?.session;
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");

    try {
      const currentAccount = selectedAccountId || "";
      setAccount(currentAccount);

      const p = await apiGet("/api/photo/presets");
      setInteriors(p?.interiors || []);
      if (currentAccount) {
        const [context, catalog] = await Promise.all([
          apiGet("/api/photo/client_context?account_id=" + encodeURIComponent(currentAccount)),
          apiGet("/api/catalog/list?account_id=" + encodeURIComponent(currentAccount) + "&limit=60"),
        ]);
        setClientContext(context || null);
        setCatalogItems(Array.isArray(catalog?.items) ? catalog.items : []);
      } else {
        setClientContext(null);
        setCatalogItems([]);
      }

      if (currentAccount && selectedProductId > 0) {
        const r = await apiGet(
          "/api/photo/sessions?account_id=" +
            encodeURIComponent(currentAccount) +
            "&product_id=" +
            selectedProductId +
            "&limit=20"
        );

        const items = r?.items || [];
        setHistory(items);

        if (items.length) {
          setSid(items[0].id);
          setSession(items[0]);
        }
      }
    } catch (e: any) {
      setErr(e?.message || "Не удалось открыть фотостудию");
    } finally {
      setLoading(false);
    }
  }, [selectedProductId, selectedAccountId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!sid) return;

    if (
      !["running", "derived_running", "deriving"].includes(
        session?.status || ""
      )
    ) {
      return;
    }

    const timer = window.setInterval(() => {
      loadSession(sid).catch(() => {});
    }, 4000);

    return () => window.clearInterval(timer);
  }, [sid, session?.status, loadSession]);

  const startNew = () => {
    setSid(null);
    setSession(null);
    setErr("");
    setNote("");
    setInterior("client_auto");
    setPrompt("");
    setReferenceFile(null); setReferenceMediaId(0);
    setCreationMode(selectedProductId > 0 ? "product" : "prompt");
    setSetKey("optimal");
    setManualShots(SETS.optimal.codes);
  };

  const createSession = async () => {
    if (!account || selectedShots.length === 0) return;
    if (creationMode === "product" && !selectedProductId) {
      setErr("Для режима товара сначала выберите товар в Ассортименте.");
      return;
    }
    if (creationMode === "prompt" && prompt.trim().length < 12) {
      setErr("Опишите сцену подробнее — хотя бы 12 символов.");
      return;
    }
    if (creationMode === "reference" && !referenceFile && !referenceMediaId) {
      setErr("Загрузите исходную фотографию объекта.");
      return;
    }

    setBusy("create");
    setErr("");
    setNote("");

    try {
      let refId=referenceMediaId;
      if (creationMode === "reference" && referenceFile) {
        const form=new FormData(); form.append("account_id",account); form.append("folder_name","photo_source_references"); form.append("files",referenceFile);
        const up=await apiUpload("/api/media/upload",form);
        if (up?.status === "error") throw new Error(up?.message || "Не удалось загрузить исходную фотографию");
        refId=Number(up?.created?.[0]?.id || up?.duplicates?.[0]?.duplicate_of || 0);
        if (!refId) throw new Error("Исходная фотография не сохранена");
        setReferenceMediaId(refId); setReferenceFile(null);
      }
      const r = await apiPost("/api/photo/session", {
        account_id: account,
        product_id: creationMode === "product" ? selectedProductId : 0,
        reference_media_id: creationMode === "reference" ? refId : 0,
        preset: interior,
        shots: selectedShots,
        mode: creationMode === "prompt" ? "free_concept" : creationMode === "reference" ? "preserve_reference" : "preserve_product",
        prompt: creationMode === "prompt" || creationMode === "reference" ? prompt.trim() : "",
        batch_confirmed: false,
      });

      setSid(r.session_id);

      setNote(
        "Настройки сохранены. Сначала BORIS создаст один главный кадр."
      );

      await loadSession(r.session_id);
    } catch (e: any) {
      setErr(e?.message || "Не удалось создать фотосессию");
    } finally {
      setBusy("");
    }
  };

  const createBatch = async () => {
    if (!account || prompt.trim().length < 12) {
      setErr("Опишите серию проектов подробнее — хотя бы 12 символов.");
      return;
    }
    setBusy("batch"); setErr(""); setNote(""); setBatchResult(null);
    try {
      const r=await apiPost("/api/photo/sessions/batch",{
        account_id:account,prompt:prompt.trim(),projects:batchProjects,
        photos_per_project:batchPhotos,preset:interior,
      });
      setBatchResult(r);
      setNote(`Создано ${r.project_count || 0} проектов · ${r.total_photos || 0} фото запланировано. Генерация ещё не запускалась.`);
    } catch(e:any){ setErr(e?.message || "Не удалось создать пакет фотопроектов"); }
    finally{ setBusy(""); }
  };

  const startMaster = async () => {
    if (!sid) return;

    setBusy("master");
    setErr("");
    setNote("");

    try {
      const r = await apiPost(
        "/api/photo/session/" + sid + "/master",
        {}
      );

      setNote(
        r?.message ||
          "Главный кадр создаётся. Обычно это занимает около минуты."
      );

      await loadSession(sid);
    } catch (e: any) {
      setErr(e?.message || "Не удалось создать главный кадр");
    } finally {
      setBusy("");
    }
  };

  const approveMaster = async (approved: boolean) => {
    if (!sid) return;

    setBusy(approved ? "approve" : "reject");
    setErr("");
    setNote("");

    try {
      const r = await apiPost(
        "/api/photo/session/" + sid + "/approve_master",
        approved
          ? {
              approved: true,
              codes: selectedShots,
            }
          : {
              approved: false,
            }
      );

      setNote(
        r?.message ||
          (approved
            ? "Главный кадр принят. Создаём остальные фотографии."
            : "Главный кадр отклонён. Можно создать новый.")
      );

      await loadSession(sid);
    } catch (e: any) {
      setErr(
        e?.message || "Не удалось обработать главный кадр"
      );
    } finally {
      setBusy("");
    }
  };

  const toggleManual = (code: string) => {
    setManualShots((old) =>
      old.includes(code)
        ? old.filter((x) => x !== code)
        : [...old, code]
    );
  };

  const currentStatus = session?.status || "";

  const showSetup =
    !session ||
    currentStatus === "completed" ||
    currentStatus === "failed";

  return (
    <EcShell
      tab="photo"
      title="ИИ-фотостудия"
      subtitle="Создавайте согласованные серии изображений товара или целой сцены по промту"
    >
      {loading ? (
        <div data-boris-g32b-page="campaign" className="boris-f3-be6250838f2f7c">
          <Card>
            <Skeleton lines={4} />
          </Card>
          <Card>
            <Skeleton lines={8} />
          </Card>
        </div>
      ) : (
        <div className="boris-f3-e748dc1b52f360">
          {err && <Alert kind="danger">{err}</Alert>}
          {note && <Alert kind="info">{note}</Alert>}

          {clientContext?.context_ready && (
            <Card>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
                <div style={{ flex: "1 1 560px" }}>
                  <div style={{ fontSize: 12, fontWeight: 800, color: color.blue, marginBottom: 5 }}>РЕЗИНОВАЯ АДАПТАЦИЯ ВКЛЮЧЕНА</div>
                  <div style={{ fontSize: 17, fontWeight: 800, color: color.heading }}>
                    Фотостудия настроена под {clientContext?.company?.name || "выбранный бизнес"}
                  </div>
                  <div style={{ marginTop: 7, color: color.muted, fontSize: 13, lineHeight: 1.55 }}>
                    {clientContext?.company?.company_niche || "BORIS использует подтверждённый коммерческий контекст выбранного аккаунта."}
                    {clientContext?.company?.client_goal_text ? ` Цель: ${clientContext.company.client_goal_text}` : ""}
                  </div>
                  <div style={{ marginTop: 7, color: color.muted, fontSize: 12 }}>
                    BORIS автоматически учитывает профиль компании, ассортимент, позиционирование и подтверждённые правила. Промт задаёт конкретную сцену, а не начинает работу с нуля.
                  </div>
                </div>
                <div style={{ padding: "7px 10px", borderRadius: 999, background: "#ecfdf5", color: "#047857", fontSize: 12, fontWeight: 800 }}>
                  {Number(clientContext?.confirmed_facts_count || 0)} подтверждённых фактов
                </div>
              </div>
            </Card>
          )}

          {session && (
            <Card>
              <div

               className="boris-f3-9b18e913563f1f">
                <div>
                  <div
                    style={{
                      fontSize: 12,
                      color: color.muted,
                      marginBottom: 6,
                    }}
                  >
                    {session.mode === "free_concept" ? "СЦЕНА ПО ПРОМТУ" : session.mode === "preserve_reference" ? "ИСХОДНАЯ ФОТОГРАФИЯ" : `ТОВАР №${selectedProductId}`}
                  </div>

                  <div

                   className="boris-f3-64126e3e96bc1e">
                    {statusRu(currentStatus)}
                  </div>

                  <div
                    style={{
                      marginTop: 6,
                      color: color.muted,
                      fontSize: 13,
                    }}
                  >
                    Готово {session.shots_done || 0} из{" "}
                    {session.shots_planned || 0} фотографий
                  </div>
                </div>

                {currentStatus === "completed" && (
                  <Button kind="ghost" onClick={startNew}>
                    Создать новую фотосессию
                  </Button>
                )}
              </div>
            </Card>
          )}

          {showSetup && (
            <>
              <Card>
                <div className="boris-f3-93269fbb36437a">
                  1. Откуда взять сцену?
                </div>
                <div style={{ color: color.muted, fontSize: 13, marginBottom: 14 }}>
                  Можно работать с товаром из Ассортимента или создать целый интерьер по вашему промту.
                </div>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
                  <Button type="button" kind={creationMode === "prompt" ? undefined : "ghost"}
                    onClick={() => setCreationMode("prompt")}>По промту</Button>
                  <Button type="button" kind={creationMode === "product" ? undefined : "ghost"}
                    onClick={() => setCreationMode("product")}>Из товара</Button>
                  <Button type="button" kind={creationMode === "reference" ? undefined : "ghost"}
                    onClick={() => setCreationMode("reference")}>Из исходной фотографии</Button>
                </div>
                {creationMode === "prompt" ? (
                  <div>
                    <label style={{ display: "block", fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
                      Опишите, что нужно создать
                    </label>
                    <textarea
                      value={prompt}
                      onChange={(e) => setPrompt(e.target.value)}
                      rows={5}
                      placeholder={clientContext?.company?.company_niche ? `Например: создайте продающую реалистичную сцену для бизнеса «${clientContext.company.name || "клиент"}». Конкретный товар, стиль, материалы и пожелания к кадру...` : "Опишите конкретную сцену, товар, стиль, материалы и пожелания к кадру..."}
                      style={{ width: "100%", border: "1px solid " + color.line, borderRadius: 12, padding: 12, resize: "vertical", font: "inherit" }}
                    />
                    <div style={{ marginTop: 10, padding: 12, borderRadius: 10, background: "#f5f9ff", color: color.muted, fontSize: 12.5, lineHeight: 1.5 }}>
                      BORIS сначала создаст один главный кадр. После вашей проверки все остальные ракурсы будут делаться из него как из reference. Пол, его цвет и рисунок, стены, окна, свет, мебель, техника, ручки, столешница и декор фиксируются для всей серии.
                    </div>
                    <div style={{marginTop:12,padding:14,border:"1px solid "+color.line,borderRadius:12,background:"#fff"}}>
                      <div style={{fontSize:14,fontWeight:800,color:color.heading}}>Массовые фотопроекты</div>
                      <div style={{fontSize:12,color:color.muted,marginTop:4}}>Например: 10 разных проектов кухонь × 10 фотографий. Каждый проект получает свой мастер-кадр и сохраняет один и тот же дизайн во всех ракурсах.</div>
                      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:8,alignItems:"end",marginTop:10}}>
                        <label style={{display:"grid",gap:5,fontSize:12,fontWeight:700}}>Проектов<input type="number" min={1} max={20} value={batchProjects} onChange={(e)=>setBatchProjects(Math.max(1,Math.min(20,Number(e.target.value)||1)))} style={{minHeight:42,border:"1px solid "+color.line,borderRadius:9,padding:"0 10px"}} /></label>
                        <label style={{display:"grid",gap:5,fontSize:12,fontWeight:700}}>Фото в проекте<input type="number" min={2} max={10} value={batchPhotos} onChange={(e)=>setBatchPhotos(Math.max(2,Math.min(10,Number(e.target.value)||10)))} style={{minHeight:42,border:"1px solid "+color.line,borderRadius:9,padding:"0 10px"}} /></label>
                        <Button kind="ghost" onClick={createBatch} disabled={busy!=="" || prompt.trim().length<12}>{busy==="batch"?"Создаю проекты…":"Создать пакет проектов"}</Button>
                      </div>
                      {batchResult?.projects?.length?<div style={{marginTop:10,fontSize:12,color:color.muted}}>Создано черновиков: <b>{batchResult.project_count}</b> · запланировано фото: <b>{batchResult.total_photos}</b> · генерация не запущена.</div>:null}
                    </div>
                  </div>
                ) : creationMode === "reference" ? (
                  <div>
                    <label style={{display:"grid",gap:7,fontSize:13,fontWeight:700}}>
                      Исходная фотография объекта
                      <input type="file" accept="image/*" onChange={(e)=>{setReferenceFile(e.target.files?.[0] || null);setReferenceMediaId(0);}} style={{minHeight:42,padding:"8px 0"}} />
                    </label>
                    <div style={{marginTop:8,fontSize:12.5,color:color.muted,lineHeight:1.5}}>{referenceFile?`Выбрано: ${referenceFile.name}`:referenceMediaId?"Исходная фотография уже сохранена.":"Загрузите фото автомобиля, мебели или другого реального объекта, который нужно сохранить между ракурсами."}</div>
                    <label style={{display:"block",fontSize:13,fontWeight:700,marginTop:12,marginBottom:6}}>Что изменить или какие ракурсы нужны</label>
                    <textarea value={prompt} onChange={(e)=>setPrompt(e.target.value)} rows={4} placeholder="Например: Toyota Camry — сохранить точно этот автомобиль, цвет, диски и госномер. Сделать продающую серию с разных ракурсов на нейтральной современной площадке." style={{width:"100%",border:"1px solid "+color.line,borderRadius:12,padding:12,resize:"vertical",font:"inherit"}} />
                    <div style={{marginTop:10,padding:12,borderRadius:10,background:"#f5f9ff",color:color.muted,fontSize:12.5,lineHeight:1.5}}>Первый кадр создаётся через исходную фотографию как reference. BORIS фиксирует идентичность объекта, цвет, материалы, уникальные элементы и видимые идентификаторы/госномер; после вашей проверки остальные ракурсы строятся уже от утверждённого мастер-кадра.</div>
                  </div>
                ) : selectedProductId > 0 ? (
                  <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", gap:10, flexWrap:"wrap" }}>
                    <div>
                      <div style={{ color: color.heading, fontSize: 13, fontWeight: 800 }}>
                        {catalogItems.find((p:any) => Number(p.id) === selectedProductId)?.name || `Товар №${selectedProductId}`}
                      </div>
                      <div style={{ color: color.muted, fontSize: 12, marginTop: 3 }}>Товар выбран для этой фотосессии.</div>
                    </div>
                    <Button kind="ghost" type="button" onClick={() => { setSelectedProductId(0); setSession(null); setSid(null); setHistory([]); }}>Выбрать другой</Button>
                  </div>
                ) : (
                  <div>
                    <div style={{ color: color.heading, fontSize: 13, fontWeight: 800, marginBottom: 8 }}>Выберите товар здесь</div>
                    {catalogItems.length ? (
                      <select
                        value={selectedProductId || ""}
                        onChange={(e) => { const id = Number(e.target.value || 0); setSelectedProductId(id); setSession(null); setSid(null); setHistory([]); }}
                        style={{ width:"100%", minHeight:44, border:"1px solid "+color.line, borderRadius:10, padding:"0 12px", background:"#fff", color:color.heading, font:"inherit" }}
                      >
                        <option value="">Выберите товар из ассортимента</option>
                        {catalogItems.map((p:any) => <option key={p.id} value={p.id}>{p.name || `Товар №${p.id}`}{p.sku ? ` · ${p.sku}` : ""}</option>)}
                      </select>
                    ) : <Alert kind="info">В ассортименте этого аккаунта пока нет товаров. Их можно добавить отдельно, не покидая фотостудию до необходимости редактирования каталога.</Alert>}
                    <div style={{ marginTop:8, color:color.muted, fontSize:12 }}>Обычный выбор товара теперь выполняется прямо здесь. Ассортимент нужен только если требуется добавить или изменить сам товар.</div>
                    {!catalogItems.length ? <a href="/dashboard/campaign/catalog" style={{display:"inline-block",marginTop:8}}>Добавить товар в ассортимент</a> : null}
                  </div>
                )}
              </Card>

              <Card>
                <div

                 className="boris-f3-93269fbb36437a">
                  2. Что хотите сделать?
                </div>

                <div
                  style={{
                    color: color.muted,
                    fontSize: 13,
                    marginBottom: 16,
                  }}
                >
                  Выберите задачу — остальные настройки BORIS
                  предложит автоматически.
                </div>

                <div

                 className="boris-f3-226f68bcdf3e8f">
                  <div data-boris-g32b-card="1"
                    style={{
                      border: "2px solid " + color.blue,
                      background: "#f5f9ff",
                      borderRadius: 14,
                      padding: 18,
                    }}
                  >
                    <div

                     className="boris-f3-eef13f8516fe87">
                      Сделать красивые фото товара
                    </div>

                    <div
                      style={{
                        marginTop: 7,
                        color: color.muted,
                        fontSize: 13,
                        lineHeight: 1.45,
                      }}
                    >
                      BORIS создаст товар в подходящем интерьере
                      и сделает несколько полезных ракурсов.
                    </div>

                    <div
                      style={{
                        marginTop: 12,
                        color: color.blue,
                        fontSize: 12,
                        fontWeight: 700,
                      }}
                    >
                      Выбрано
                    </div>
                  </div>

                  <button data-boris-g32b-card="1" type="button"
                    onClick={() => {
                      setCreationMode("reference");
                      setPrompt("Сохранить текущий интерьер, расположение предметов, материалы, свет и сам объект без изменений. Сделать новые ракурсы той же сцены.");
                    }}
                    style={{
                      border: creationMode === "reference" && prompt.startsWith("Сохранить текущий интерьер") ? "2px solid " + color.blue : "1px solid " + color.line,
                      background: creationMode === "reference" && prompt.startsWith("Сохранить текущий интерьер") ? "#f5f9ff" : "#fff",
                      borderRadius: 14,
                      padding: 18,
                      textAlign: "left",
                      cursor: "pointer",
                      font: "inherit",
                    }}
                  >
                    <div className="boris-f3-eef13f8516fe87">
                      Сохранить текущий интерьер
                    </div>
                    <div style={{ marginTop: 7, color: color.muted, fontSize: 13, lineHeight: 1.45 }}>
                      Загрузите исходное фото — BORIS сохранит помещение и объект и сделает новые ракурсы этой же сцены.
                    </div>
                    <div style={{ marginTop: 12, color: color.blue, fontSize: 12, fontWeight: 700 }}>
                      Доступно по исходной фотографии
                    </div>
                  </button>

                  <button data-boris-g32b-card="1" type="button"
                    onClick={() => {
                      setCreationMode("reference");
                      setPrompt("Сохранить сам объект, его геометрию, цвет, материалы и уникальные детали без изменений. Полностью заменить окружение на выбранный ниже интерьер.");
                    }}
                    style={{
                      border: creationMode === "reference" && prompt.startsWith("Сохранить сам объект") ? "2px solid " + color.blue : "1px solid " + color.line,
                      background: creationMode === "reference" && prompt.startsWith("Сохранить сам объект") ? "#f5f9ff" : "#fff",
                      borderRadius: 14,
                      padding: 18,
                      textAlign: "left",
                      cursor: "pointer",
                      font: "inherit",
                    }}
                  >
                    <div className="boris-f3-eef13f8516fe87">
                      Перенести товар в другой интерьер
                    </div>
                    <div style={{ marginTop: 7, color: color.muted, fontSize: 13, lineHeight: 1.45 }}>
                      Оставить сам товар без изменений, но полностью заменить окружение.
                    </div>
                    <div style={{ marginTop: 12, color: color.blue, fontSize: 12, fontWeight: 700 }}>
                      Доступно по исходной фотографии
                    </div>
                  </button>
                </div>
              </Card>

              <Card>
                <div

                 className="boris-f3-93269fbb36437a">
                  3. Выберите интерьер
                </div>

                <div
                  style={{
                    color: color.muted,
                    fontSize: 13,
                    marginBottom: 16,
                  }}
                >
                  BORIS сохранит сам товар без изменений.
                </div>

                <div

                 className="boris-f3-5e07cf1b565997"
                 style={{ gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: 12 }}>
                  {interiors.map((item) => {
                    const active = interior === item.code;
                    const info =
                      INTERIORS[item.code] || {
                        title: "Интерьер",
                        description:
                          "Подходящее окружение для товара",
                      };

                    return (
                      <Button
                        kind="ghost"
                        type="button"
                        key={item.code}
                        onClick={() => setInterior(item.code)}
                        style={{
                          width: "100%",
                          minHeight: 92,
                          display: "block",
                          textAlign: "left",
                          whiteSpace: "normal",
                          lineHeight: 1.35,
                          color: color.heading,
                          cursor: "pointer",
                          borderRadius: 12,
                          padding: 14,
                          border: active
                            ? "2px solid " + color.blue
                            : "1px solid " + color.line,
                          background: active
                            ? "#f5f9ff"
                            : "#fff",
                        }}
                      >
                        <div

                         className="boris-f3-e41690c10dea51">
                          {info.title}
                        </div>

                        <div
                          style={{
                            marginTop: 5,
                            fontSize: 12,
                            lineHeight: 1.5,
                            fontWeight: 500,
                            color: color.muted,
                          }}
                        >
                          {info.description}
                        </div>
                      </Button>
                    );
                  })}
                </div>
              </Card>

              <Card>
                <div

                 className="boris-f3-93269fbb36437a">
                  4. Сколько фотографий сделать?
                </div>

                <div
                  style={{
                    color: color.muted,
                    fontSize: 13,
                    marginBottom: 16,
                  }}
                >
                  Для большинства объявлений рекомендуем
                  оптимальный набор.
                </div>

                <div

                 className="boris-f3-5e07cf1b565997"
                 style={{ gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 12 }}>
                  {(
                    [
                      "minimal",
                      "optimal",
                      "extended",
                    ] as const
                  ).map((key) => {
                    const item = SETS[key];
                    const active = setKey === key;

                    return (
                      <Button
                        kind="ghost"
                        key={key}
                        type="button"
                        onClick={() => setSetKey(key)}
                        style={{
                          width: "100%",
                          minHeight: 92,
                          display: "block",
                          textAlign: "left",
                          whiteSpace: "normal",
                          lineHeight: 1.35,
                          color: color.heading,
                          cursor: "pointer",
                          padding: 16,
                          borderRadius: 12,
                          border: active
                            ? "2px solid " + color.blue
                            : "1px solid " + color.line,
                          background: active
                            ? "#f5f9ff"
                            : "#fff",
                        }}
                      >
                        <div

                         className="boris-f3-a588e2661aaeb8">
                          {item.title}
                        </div>

                        <div
                          style={{
                            marginTop: 5,
                            fontSize: 12.5,
                            lineHeight: 1.45,
                            fontWeight: 500,
                            color: color.muted,
                          }}
                        >
                          {item.description}
                        </div>

                        {key === "optimal" && (
                          <div
                            style={{
                              marginTop: 9,
                              color: color.blue,
                              fontSize: 11,
                              fontWeight: 700,
                            }}
                          >
                            Рекомендуем
                          </div>
                        )}
                      </Button>
                    );
                  })}

                  <Button
                    kind="ghost"
                    type="button"
                    onClick={() => setSetKey("manual")}
                    style={{
                      width: "100%",
                      minHeight: 92,
                      display: "block",
                      textAlign: "left",
                      whiteSpace: "normal",
                      lineHeight: 1.35,
                      color: color.heading,
                      cursor: "pointer",
                      padding: 16,
                      borderRadius: 12,
                      border:
                        setKey === "manual"
                          ? "2px solid " + color.blue
                          : "1px solid " + color.line,
                      background:
                        setKey === "manual"
                          ? "#f5f9ff"
                          : "#fff",
                    }}
                  >
                    <div

                     className="boris-f3-a588e2661aaeb8">
                      Настроить вручную
                    </div>

                    <div
                      style={{
                        marginTop: 5,
                        fontSize: 12.5,
                        lineHeight: 1.45,
                        fontWeight: 500,
                        color: color.muted,
                      }}
                    >
                      Выбрать нужные ракурсы самостоятельно
                    </div>
                  </Button>
                </div>

                {setKey === "manual" && (
                  <div

                   className="boris-f3-219d0d07d884aa">
                    {Object.entries(SHOTS).map(
                      ([code, item]) => {
                        const active =
                          manualShots.includes(code);

                        return (
                          <label data-boris-g32b-card="1"
                            key={code}
                            style={{
                              display: "flex",
                              gap: 10,
                              padding: 12,
                              cursor: "pointer",
                              borderRadius: 11,
                              border:
                                "1px solid " +
                                (active
                                  ? color.blue
                                  : color.line),
                              background: active
                                ? "#f5f9ff"
                                : "#fff",
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={active}
                              onChange={() =>
                                toggleManual(code)
                              }
                            />

                            <span>
                              <b

                               className="boris-f3-cdcef186c815d7">
                                {item.title}
                              </b>

                              <span
                                style={{
                                  display: "block",
                                  marginTop: 3,
                                  fontSize: 11.5,
                                  color: color.muted,
                                }}
                              >
                                {item.description}
                              </span>
                            </span>
                          </label>
                        );
                      }
                    )}
                  </div>
                )}
              </Card>

              <Card>
                <div

                 className="boris-f3-9dac6c84178fce">
                  <div>
                    <div

                     className="boris-f3-bb65a9b6767687">
                      Всё готово
                    </div>

                    <div
                      style={{
                        marginTop: 5,
                        color: color.muted,
                        fontSize: 13,
                      }}
                    >
                      Будет создано {totalPhotos} фотографий.
                      Сначала только один главный кадр —
                      остальные после вашей проверки.
                    </div>
                  </div>

                  <Button
                    disabled={
                      busy !== "" ||
                      selectedShots.length === 0 ||
                      (creationMode === "product" && !selectedProductId) ||
                      (creationMode === "prompt" && prompt.trim().length < 12) ||
                      (creationMode === "reference" && !referenceFile && !referenceMediaId)
                    }
                    onClick={createSession}
                  >
                    {busy === "create"
                      ? "Подготавливаю…"
                      : "Создать фотосессию"}
                  </Button>
                </div>
              </Card>
            </>
          )}

          {session && currentStatus === "draft" && (
            <Card>
              <div

               className="boris-f3-f9ab603104b127">
                Сначала создадим главный кадр
              </div>

              <div
                style={{
                  marginTop: 7,
                  color: color.muted,
                  fontSize: 13,
                  lineHeight: 1.5,
                }}
              >
                BORIS создаст одну фотографию.
                Вы посмотрите результат и решите,
                продолжать фотосессию или переснять кадр.
              </div>

              <div className="boris-f3-76977cc9f24231">
                <Button
                  disabled={busy !== ""}
                  onClick={startMaster}
                >
                  {busy === "master"
                    ? "Создаю…"
                    : "Создать главный кадр"}
                </Button>
              </div>
            </Card>
          )}

          {session && currentStatus === "running" && (
            <Card>
              <div

               className="boris-f3-f9ab603104b127">
                Создаём главный кадр
              </div>

              <div
                style={{
                  marginTop: 7,
                  color: color.muted,
                  fontSize: 13,
                }}
              >
                Страница обновится автоматически,
                когда фотография будет готова.
              </div>
            </Card>
          )}

          {session &&
            currentStatus === "master_review" && (
              <Card>
                <div

                 className="boris-f3-f9ab603104b127">
                  Проверьте главный кадр
                </div>

                <div
                  style={{
                    marginTop: 7,
                    color: color.muted,
                    fontSize: 13,
                    lineHeight: 1.5,
                  }}
                >
                  Посмотрите, правильно ли сохранены
                  форма товара, цвет, материалы и детали.
                </div>

                {session.master_url && (
                  <img
                    src={session.master_url}
                    alt="Главный кадр"
                    style={{
                      display: "block",
                      width: "100%",
                      maxWidth: 650,
                      marginTop: 16,
                      borderRadius: 14,
                      border:
                        "1px solid " + color.line,
                    }}
                  />
                )}

                <div

                 className="boris-f3-f8f1c5c3c9e27c">
                  <Button
                    disabled={busy !== ""}
                    onClick={() =>
                      approveMaster(true)
                    }
                  >
                    {busy === "approve"
                      ? "Продолжаю…"
                      : "Всё правильно — продолжить"}
                  </Button>

                  <Button
                    kind="ghost"
                    disabled={busy !== ""}
                    onClick={() =>
                      approveMaster(false)
                    }
                  >
                    {busy === "reject"
                      ? "Отменяю…"
                      : "Не подходит — переснять"}
                  </Button>
                </div>
              </Card>
            )}

          {session &&
            ["derived_running", "deriving"].includes(currentStatus) && (
              <Card>
                <div

                 className="boris-f3-f9ab603104b127">
                  Создаём остальные фотографии
                </div>

                <div
                  style={{
                    marginTop: 7,
                    color: color.muted,
                    fontSize: 13,
                  }}
                >
                  Уже готово {session.shots_done || 0} из{" "}
                  {session.shots_planned || totalPhotos}.
                </div>
              </Card>
            )}

          {session &&
            currentStatus === "completed" && (
              <Card>
                <div

                 className="boris-f3-f9ab603104b127">
                  Фотосессия готова
                </div>

                <div
                  style={{
                    marginTop: 7,
                    color: color.muted,
                    fontSize: 13,
                  }}
                >
                  Все фотографии сохранены
                  в Медиатеке и привязаны к товару.
                </div>

                <div

                 className="boris-f3-f8f1c5c3c9e27c">
                  <a href="/dashboard/campaign/media">
                    <Button>Открыть Медиатеку</Button>
                  </a>

                  <Button
                    kind="ghost"
                    onClick={startNew}
                  >
                    Создать новую фотосессию
                  </Button>
                </div>
              </Card>
            )}

          {history.length > 0 && (
            <Card>
              <div

               className="boris-f3-d6994f1d02ba76">
                Предыдущие фотосессии
              </div>

              <div

               className="boris-f3-7e79b1884980df">
                {history.slice(0, 8).map((item: any) => (
                  <Button data-boris-g32b-card="1"
                    key={item.id}
                    type="button"
                    onClick={async () => {
                      setSid(item.id);
                      await loadSession(item.id);
                    }}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      gap: 12,
                      width: "100%",
                      textAlign: "left",
                      border:
                        "1px solid " + color.line,
                      background: "#fff",
                      borderRadius: 10,
                      padding: "11px 12px",
                      cursor: "pointer",
                    }}
                  >
                    <span>
                      <b>
                        Фотосессия №{item.id}
                      </b>

                      <span
                        style={{
                          display: "block",
                          marginTop: 3,
                          color: color.muted,
                          fontSize: 12,
                        }}
                      >
                        {statusRu(item.status)}
                      </span>
                    </span>

                    <span
                      style={{
                        fontSize: 12,
                        color: color.muted,
                        whiteSpace: "nowrap",
                      }}
                    >
                      {item.shots_done || 0} из{" "}
                      {item.shots_planned || 0}
                    </span>
                  </Button>
                ))}
              </div>
            </Card>
          )}
        </div>
      )}
    </EcShell>
  );
}
