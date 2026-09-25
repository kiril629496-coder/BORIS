"use client";
import { useEffect, useMemo, useState } from "react";
import { Card, Button, Badge, Alert, Input } from "../ui";
import { color, font, space } from "../ui/tokens";

function api(path: string, init: RequestInit = {}) {
  const t = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  const headers = { "Content-Type": "application/json", Authorization: "Bearer " + t, ...(init.headers || {}) };
  return fetch(path, { ...init, headers });
}

type Planned = { id:number; title:string; category?:string; status:string; changes:Record<string,string>; run_status?:string; error?:string };
type MemoryField = { label:string; type?:string; options?:string[]; seen_count?:number; last_seen?:string; source?:string };

const fieldNames: Record<string,string> = {
  brand: "Бренд",
  product_subtype: "Подтип товара",
  glass_type: "Тип стекла",
  door_type: "Тип дверей",
};
const terminal = ["completed","completed_with_errors","blocked_credentials","blocked_transport","blocked_auth_or_ui","blocked_gateway","paused_for_fix","superseded_by_new_gateway"];
const REQUIRED_GATEWAY_VERSION = "2.1.9";
function versionAtLeast(actual:string, required:string){ const a=actual.split(".").map(x=>Number(x)||0), r=required.split(".").map(x=>Number(x)||0); for(let i=0;i<Math.max(a.length,r.length);i++){ if((a[i]||0)>(r[i]||0)) return true; if((a[i]||0)<(r[i]||0)) return false; } return true; }

const friendlyLabels: Record<string,string> = {
  ListingFee: "Способ размещения / тариф",
  ProductType: "Категория товара",
  GoodsType: "Тип товара",
  GoodsSubType: "Подтип товара",
  ServiceType: "Вид услуги",
  ServiceSubtype: "Подвид услуги",
  ServiceSubspecies: "Направление услуги",
  "Название объявления": "Заголовок объявления",
  "Звонки и сообщения": "Способ связи",
  "Звонки через Avito": "Звонки через Avito",
  ShowerEnclosureBrand: "Бренд душевого ограждения",
  ShowerEnclosureDoorType: "Тип дверей",
  ShowerEnclosureGlassType: "Тип стекла",
  ShowerEnclosureHeight: "Высота, см",
  ShowerEnclosureType: "Тип душевого ограждения",
  ShowerEnclosureWidth: "Ширина, см",
  Category: "Категория",
  Subcategory: "Подкатегория",
};

function humanLabel(raw:string){
  if(friendlyLabels[raw]) return friendlyLabels[raw];
  if(/[A-Z].*[A-Z]/.test(raw) && !raw.includes(" ")) return raw.replace(/([a-zа-я0-9])([A-ZА-Я])/g,"$1 $2").replace(/^./,m=>m.toUpperCase());
  return raw;
}
function fieldGroup(raw:string){
  const v=(raw||"").toLowerCase();
  if(["цена","описание","фотографии"].includes(v)) return "main";
  if(v.includes("category")||v.includes("категор")||v.includes("subcategory")||v.includes("подкатегор")||v.includes("producttype")||v.includes("listingfee")) return "category";
  return "params";
}
function sourceText(source?:string){
  if(source === "browser_gateway") return "получено из Avito";
  if(source === "local_seed") return "предварительные данные";
  if(source === "boris_standard") return "доступно для изменения";
  return "ещё не проверено в Avito";
}
function clientVisibleField(label:string){
  const v=String(label||"").trim();
  if(!v) return false;
  if(/^params?\[/i.test(v) || /^iac/i.test(v) || /\[\]$/.test(v)) return false;
  if(/^(id|itemid|sellerid|profileid|addressid)$/i.test(v.replace(/[^a-z]/gi,""))) return false;
  return true;
}
const cardShell={position:"relative" as const,minWidth:0,border:"1px solid #dfe7f3",borderTop:"3px solid #2f6fff",borderRadius:16,background:"linear-gradient(180deg,#fff,#fbfdff)",boxShadow:"0 5px 16px rgba(31,58,103,.035)",padding:14};
function displayFieldName(key:string){
  const raw=key.startsWith("custom:") ? key.slice(7) : key;
  return fieldNames[key] || humanLabel(raw);
}
function statusText(status?:string){
  const map:Record<string,string>={
    waiting_gateway:"Готово к запуску", queued_gateway:"Готово к запуску", claimed_gateway:"Подготавливаем", running_gateway:"Выполняем изменения",
    completed:"Готово", completed_with_errors:"Готово, есть замечания", blocked_gateway:"Нужно действие", blocked_auth_or_ui:"Нужно войти в Avito",
    blocked_credentials:"Нужно проверить доступ", blocked_transport:"Не удалось связаться", blocked_network_route:"Ждём чистый маршрут Avito", waiting_avito_code:"Нужен код Avito", waiting_external_verification:"Проверяем результат в Avito", blocked_captcha:"Avito запросил проверку", blocked_identity:"Профиль не совпал", paused_for_fix:"Приостановлено для проверки", superseded_by_new_gateway:"Закрыто после обновления", scanned:"Параметры проверены", changed:"Изменено", verified:"Проверено", verification_failed:"Значение не подтвердилось",
    skipped:"Пропущено", failed:"Ошибка"
  };
  return map[String(status||"")] || (status ? "В работе" : "Ожидает");
}

export default function MassEditor({ account }: { account: string }) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<Planned[]>([]);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<any>(null);
  const [error, setError] = useState("");
  const [memory, setMemory] = useState<any>(null);
  const [gateway, setGateway] = useState<any>(null);
  const [isolated, setIsolated] = useState<any>(null);
  const [pairCode, setPairCode] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [limit, setLimit] = useState(1);
  const [scopeMode, setScopeMode] = useState<"all"|"count">("count");
  const [showPreviewItems, setShowPreviewItems] = useState(false);
  const [showJobItems, setShowJobItems] = useState(false);
  const [segmentKey, setSegmentKey] = useState("");
  const [fieldRules, setFieldRules] = useState<Record<string,{enabled:boolean;mode:"fixed"|"random";value:string;values:string}>>({});
  const [marketQuery, setMarketQuery] = useState("");
  const [marketCity, setMarketCity] = useState("");
  const [marketLimit, setMarketLimit] = useState(10);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketResult, setMarketResult] = useState<any>(null);
  const [useMarketPatterns, setUseMarketPatterns] = useState(true);
  const [blueOcean, setBlueOcean] = useState(true);
  const gatewayVersion = String(gateway?.gateway?.extension_version || "");
  const gatewayMutationReady = Boolean(gateway?.connected && versionAtLeast(gatewayVersion, REQUIRED_GATEWAY_VERSION));

  async function loadMemory() {
    if (!account) return;
    try {
      const r = await api(`/api/avito/mass-editor/characteristics?account_id=${encodeURIComponent(account)}${segmentKey ? `&segment_key=${encodeURIComponent(segmentKey)}` : ""}`);
      const d = await r.json(); if (r.ok) setMemory(d);
    } catch {}
  }
  async function loadGateway() {
    if(!account) return;
    try { const r=await api(`/api/avito/mass-editor/gateway/status?account_id=${encodeURIComponent(account)}`); const d=await r.json(); if(r.ok) setGateway(d); } catch {}
  }
  async function loadIsolated() {
    if(!account) return;
    try { const r=await api(`/api/avito/mass-editor/isolated/status?account_id=${encodeURIComponent(account)}`); const d=await r.json(); if(r.ok) setIsolated(d); } catch {}
  }
  async function submitVerificationCode() {
    const code=verificationCode.replace(/\D/g,"");
    if(code.length<4){setError("Введите код Avito");return;}
    setBusy(true);setError("");
    try{
      const r=await api("/api/avito/mass-editor/isolated/verification-code",{method:"POST",body:JSON.stringify({account_id:account,code})});
      const d=await r.json(); if(!r.ok||d.status!=="ok") throw new Error(d.message||"Не удалось передать код");
      setVerificationCode(""); await loadIsolated();
    }catch(e:any){setError(e.message||"Ошибка");}finally{setBusy(false);}
  }
  useEffect(() => { setJob(null); setPreview([]); setOpen(false); setFieldRules({}); loadMemory(); loadGateway(); loadIsolated(); const t=setInterval(()=>{loadGateway();loadIsolated();},10000); return ()=>clearInterval(t); }, [account, segmentKey]);

  // Progress belongs to the server job, not to this React component. Reattach to
  // the newest job whenever the user returns to Ads, refreshes the page, or opens
  // BORIS in another tab/device.
  useEffect(() => {
    if(!account) { setJob(null); return; }
    let cancelled=false;
    const restoreCurrentJob=async()=>{
      try{
        const r=await api(`/api/avito/mass-editor/jobs/current?account_id=${encodeURIComponent(account)}`);
        const d=await r.json();
        if(!cancelled && r.ok) setJob(d.job || null);
      }catch{}
    };
    restoreCurrentJob();
    const onVisible=()=>{ if(document.visibilityState === "visible") restoreCurrentJob(); };
    const onFocus=()=>restoreCurrentJob();
    document.addEventListener("visibilitychange",onVisible);
    window.addEventListener("focus",onFocus);
    return ()=>{cancelled=true;document.removeEventListener("visibilitychange",onVisible);window.removeEventListener("focus",onFocus);};
  },[account]);

  async function createPairCode() {
    setBusy(true); setError("");
    try { const r=await api(`/api/avito/mass-editor/gateway/pair-code?account_id=${encodeURIComponent(account)}`,{method:"POST"}); const d=await r.json(); if(!r.ok) throw new Error(d.message||"Не удалось создать код"); setPairCode(d.code||""); }
    catch(e:any){ setError(e.message||"Ошибка"); } finally { setBusy(false); }
  }

  function selectedFieldsPayload() {
    const out:Record<string,any> = {};
    for (const [label, r] of Object.entries(fieldRules)) {
      if (!r.enabled) continue;
      out[`custom:${label}`] = r.mode === "fixed"
        ? { enabled:true, mode:"fixed", value:r.value || null, values:[] }
        : { enabled:true, mode:"random", value:null, values:r.values.split(",").map(x=>x.trim()).filter(Boolean) };
    }
    return out;
  }

  async function loadPreview() {
    if (!account) return;
    setJob(null); setBusy(true); setError("");
    try {
      const fields = selectedFieldsPayload();
      const r = await api("/api/avito/mass-editor/jobs", { method:"POST", body:JSON.stringify({account_id:account,limit:scopeMode === "all" ? 100000 : limit,newest:true,segment_key:segmentKey || null,confirmed:false,fields}) });
      const d = await r.json();
      if (!r.ok || !["needs_confirmation","ok"].includes(d.status)) throw new Error(d.message || "Не удалось подготовить объявления");
      const items = d.items || [];
      if (!items.length) throw new Error("Не найдено объявлений для выбранного режима.");
      if (!items.some((it:any)=>Object.keys(it.changes || {}).length)) throw new Error("Выберите хотя бы один параметр и задайте новое значение перед массовой правкой.");
      setPreview(items); setOpen(true);
    } catch (e:any) { setError(e.message || "Ошибка"); }
    finally { setBusy(false); }
  }

  async function startTest() {
    if(gateway?.connected && !gatewayMutationReady){ setError(`Обновите BORIS Browser Gateway до версии ${REQUIRED_GATEWAY_VERSION} перед правкой объявлений. Старая версия не допускается к изменениям.`); return; }
    setJob(null); setBusy(true); setError("");
    try {
      // A correct account-bound Gateway is preferred. If there is no such session,
      // backend automatically uses the account-isolated persistent browser instead.
      if(gateway?.connected){
        const ir=await api(`/api/avito/mass-editor/gateway/identity-check?account_id=${encodeURIComponent(account)}`,{method:"POST"});
        const idata=await ir.json();
        if(!ir.ok || !idata.job_id) throw new Error(idata.message||"Не удалось проверить профиль Avito");
        const deadline=Date.now()+50000; let identity:any=null;
        while(Date.now()<deadline){
          await new Promise(resolve=>setTimeout(resolve,1800));
          const sr=await api(`/api/avito/mass-editor/gateway/identity-check/status?account_id=${encodeURIComponent(account)}&job_id=${encodeURIComponent(idata.job_id)}`);
          const sd=await sr.json();
          if(sd.status==="ok"){ identity=sd; break; }
          if(sd.status==="mismatch") { await loadGateway(); break; }
          if(sd.status==="error") throw new Error(sd.message||"Не удалось проверить профиль Avito");
        }
        // On mismatch the account-bound Gateway stays blocked and is never moved
        // to another client. create_job will use this account's isolated browser.
      }
      const r = await api("/api/avito/mass-editor/jobs", {
        method: "POST",
        body: JSON.stringify({ account_id: account, limit:scopeMode === "all" ? 100000 : limit, newest: true, segment_key:segmentKey || null, confirmed: true, fields:selectedFieldsPayload() }),
      });
      const d = await r.json();
      if (!r.ok || !d.job_id) throw new Error(d.message || "Не удалось запустить правку");
      setPreview([]); setOpen(false); setJob({ job_id:d.job_id, status:d.status || (d.executor==="isolated_browser"?"queued":"waiting_gateway"), total:d.total, processed:0, changed:Number(d.changed||0), verified:Number(d.verified||0), failed:0, items:d.items, executor:d.executor, verification_job_id:d.verification_job_id });
      await loadIsolated();
    } catch (e:any) { setError(e.message || "Ошибка"); }
    finally { setBusy(false); }
  }

  async function scanCharacteristics() {
    setJob(null); setBusy(true); setError("");
    try {
      const r=await api("/api/avito/mass-editor/scan-jobs",{method:"POST",body:JSON.stringify({account_id:account,limit,newest:true,segment_key:segmentKey || null})});
      const d=await r.json(); if(!r.ok||!d.job_id) throw new Error(d.message||"Не удалось запустить сканирование");
      setJob({job_id:d.job_id,status:d.executor==="isolated_browser"?"queued":"waiting_gateway",total:d.total,processed:0,changed:0,verified:0,failed:0,items:d.items||[],executor:d.executor});
    } catch(e:any){ setError(e.message||"Ошибка"); } finally { setBusy(false); }
  }

  async function runMarketAnalysis() {
    if(!account) return;
    if(!gateway?.connected){ setError("Для анализа выдачи подключите Browser Gateway этого Avito-аккаунта."); return; }
    if(!marketQuery.trim() || !marketCity.trim()){ setError("Укажите запрос и город для анализа выдачи Avito."); return; }
    setMarketLoading(true); setMarketResult(null); setError("");
    try {
      const r=await api("/api/parser/city_analysis_gateway",{method:"POST",body:JSON.stringify({
        account_id:account,
        query:marketQuery.trim(),
        cities:[marketCity.trim()],
        top_n:Math.max(1,Math.min(10,Number(marketLimit)||10)),
        use_market_patterns:useMarketPatterns,
        blue_ocean:blueOcean,
      })});
      const d=await r.json();
      const jobId=d?.jobs?.[0]?.job_id;
      if(!r.ok || !jobId) throw new Error(d.message||"Не удалось запустить анализ выдачи");
      const deadline=Date.now()+90000;
      while(Date.now()<deadline){
        await new Promise(resolve=>setTimeout(resolve,2500));
        const sr=await api(`/api/parser/city_analysis_gateway/status?account_id=${encodeURIComponent(account)}&job_id=${encodeURIComponent(jobId)}`);
        const sd=await sr.json();
        if(sd.status==="ok"){ setMarketResult(sd); return; }
        if(sd.status==="error") throw new Error(sd.error||sd.message||"Анализ выдачи завершился с ошибкой");
      }
      throw new Error("Avito отвечает дольше обычного. Результат сохранён на сервере — запустите проверку ещё раз через минуту.");
    } catch(e:any){ setError(e.message||"Ошибка анализа выдачи"); }
    finally { setMarketLoading(false); }
  }

  useEffect(() => {
    if (!job?.job_id || !account || terminal.includes(job.status)) return;
    const timer = setInterval(async () => {
      try {
        const r = await api(`/api/avito/mass-editor/jobs/${job.job_id}?account_id=${encodeURIComponent(account)}`);
        const d = await r.json(); if (d.job) { setJob(d.job); if(["completed","completed_with_errors"].includes(d.job.status)) loadMemory(); } else if (r.ok) { setJob(null); }
      } catch {}
    }, 2500);
    return () => clearInterval(timer);
  }, [job?.job_id, job?.status, account]);

  const progress = useMemo(() => job?.total ? Math.round((job.processed || 0) / job.total * 100) : 0, [job]);
  const clientBrandSuggestion = useMemo(() => {
    const learned:MemoryField[] = Array.isArray(memory?.fields) ? memory.fields : [];
    const company = learned.find(f => String(f.label||"").trim().toLowerCase() === "название компании")?.options?.[0] || "";
    const email = learned.find(f => String(f.label||"").trim().toLowerCase() === "электронная почта")?.options?.[0] || "";
    const companyParts = String(company).replace(/[«»"']/g,"").split(/\s+/).filter(Boolean).filter(x=>!["ооо","ип","ао","пао","зао"].includes(x.toLowerCase()));
    const candidate = companyParts[0] || "";
    const domain = String(email).split("@")[1]?.toLowerCase() || "";
    if(candidate.length >= 2 && candidate.length <= 24 && domain.includes(candidate.toLowerCase())) return candidate;
    return "";
  }, [memory]);
  const fields:MemoryField[] = useMemo(() => {
    const learned:MemoryField[] = Array.isArray(memory?.fields) ? memory.fields : [];
    const standard:MemoryField[] = [
      { label:"Цена", type:"number", source:"boris_standard" },
      { label:"Описание", type:"textarea", source:"boris_standard" },
      { label:"Фотографии", type:"files", source:"boris_standard" },
    ];
    const seen = new Set<string>();
    return [...standard, ...learned].filter(f => {
      const k=String(f.label||"").trim().toLowerCase();
      // Старый сканер Avito мог принять отдельный вариант radio за самостоятельный
      // параметр (например «Распашные» или «Только звонки»). Такие псевдополя
      // клиенту не показываем. Настоящая radio-группа всегда приходит с вариантами.
      if(f.type === "radio" && (!Array.isArray(f.options) || f.options.length < 2)) return false;
      if(!k || seen.has(k) || !clientVisibleField(f.label)) return false;
      seen.add(k); return true;
    });
  }, [memory]);

  return <div style={{display:"grid",gap:space.lg,width:"100%"}}>
    <Card style={{ width:"100%", boxSizing:"border-box" }}>
      <div style={{ display:"flex", gap:space.md, alignItems:"flex-start", flexWrap:"wrap" }}>
        <div style={{ flex:"1 1 520px", minWidth:0 }}>
          <div style={{ ...font.h3, color:color.heading }}>Массовая правка объявлений</div>
          <div style={{ ...font.body, marginTop:6 }}>
            BORIS работает со всеми активными объявлениями выбранного Avito-аккаунта — и созданными в BORIS, и размещёнными раньше вручную. Можно массово менять цену, описание, фотографии и любые найденные характеристики; остальные поля не затрагиваются.
          </div>
        </div>
        <Badge kind={gateway?.connected ? "success" : "neutral"}>{gateway?.connected ? "Avito подключён" : "Требуется подключение"}</Badge>
      </div>
    </Card>

    <Card style={{width:"100%",boxSizing:"border-box"}}>
      <div className="gatewayTopGrid">
        <div className="gatewayTopCard">
          <div className="gatewayEyebrow">Шаг 1</div>
          <div style={{...font.h3,color:color.heading}}>Подключить Chrome</div>
          <div style={{...font.small,marginTop:6}}>Установите модуль один раз. Он использует уже открытую сессию Avito и не передаёт пароль.</div>
          <a href="/boris-browser-gateway.zip" style={{display:"inline-block",marginTop:12,textDecoration:"none"}}><Button>Скачать модуль подключения</Button></a>
        </div>
        <div className="gatewayTopCard">
          <div className="gatewayEyebrow">Шаг 2</div>
          <div style={{...font.h3,color:color.heading}}>Получить код подключения</div>
          <div style={{...font.small,marginTop:6}}>Код действует 10 минут и используется один раз.</div>
          <Button onClick={createPairCode} disabled={busy} style={{marginTop:12}}>{pairCode ? "Обновить код" : "Получить код"}</Button>
          {pairCode ? <div className="gatewayCode">{pairCode}</div> : null}
        </div>
        <div className="gatewayTopCard">
          <div className="gatewayEyebrow">Шаг 3</div>
          <div style={{...font.h3,color:color.heading}}>Статус подключения</div>
          <div style={{...font.body,marginTop:6}}>{gateway?.connected ? "Связь через Browser Gateway активна" : isolated?.session?.ready ? "Отдельная сессия BORIS готова" : "BORIS использует отдельную сессию для этого аккаунта"}</div>
          {gateway?.gateway ? <div style={{...font.small,marginTop:8}}>{gateway.gateway.avito_logged_in === false ? "Нужно войти в Avito в этом Chrome" : gateway.gateway.avito_logged_in === true ? "Avito готов к работе" : "Подключение готово, Avito проверится при первом запуске"}</div> : null}
          {!gateway?.connected && isolated?.route?.state === "network_route_required" ? <Alert kind="warning" style={{marginTop:10}}>Отдельная сессия создана, но текущий сетевой маршрут Avito заблокирован. BORIS не использует чужой профиль Chrome и не запускает изменения до чистого маршрута.</Alert> : null}
          {!gateway?.connected && isolated?.session?.state === "waiting_avito_code" ? <div style={{marginTop:10,display:"flex",gap:8,flexWrap:"wrap"}}><Input value={verificationCode} onChange={(e:any)=>setVerificationCode(String(e.target.value||"").replace(/\D/g,"").slice(0,12))} placeholder="Код Avito" /><Button onClick={submitVerificationCode} disabled={busy}>Подтвердить код</Button></div> : null}
          {gateway?.gateway?.services ? <div style={{display:"flex",gap:6,flexWrap:"wrap",marginTop:10}}>{Object.entries(gateway.gateway.services as Record<string,boolean>).map(([k,v])=><Badge key={k} kind={v?"success":"neutral"}>{v?"✓ ":"○ "}{({avito:"Avito",yandex:"Яндекс",google:"Google",gmail:"Gmail",youtube:"YouTube",vk:"VK",telegram:"Telegram",mailru:"Mail.ru"} as Record<string,string>)[k]||k}</Badge>)}</div> : null}
          {gateway?.gateway?.extension_version && !gatewayMutationReady ? <Alert kind="warning" style={{marginTop:10}}>Установлена версия {gatewayVersion}. Для правки объявлений нужна версия не ниже {REQUIRED_GATEWAY_VERSION}.</Alert> : null}
        </div>
      </div>

      <details className="gatewayGuide">
        <summary className="gatewayGuideSummary">
          <span>Инструкция по установке</span>
          <span className="gatewayGuideHint">Mac · Windows · куда вводить код</span>
        </summary>
        <div className="gatewayGuideBody">
          <div className="gatewayOsGrid">
            <div className="gatewayStepCard">
              <div className="gatewayStepTitle">Mac</div>
              <ol>
                <li>Откройте «Загрузки» и распакуйте ZIP.</li>
                <li>В Chrome откройте <b>chrome://extensions</b>.</li>
                <li>Включите «Режим разработчика».</li>
                <li>Нажмите «Загрузить распакованное расширение».</li>
                <li>Выберите папку <b>boris-browser-gateway</b>.</li>
              </ol>
            </div>
            <div className="gatewayStepCard">
              <div className="gatewayStepTitle">Windows</div>
              <ol>
                <li>Нажмите по ZIP правой кнопкой → «Извлечь все».</li>
                <li>В Chrome откройте <b>chrome://extensions</b>.</li>
                <li>Включите «Режим разработчика».</li>
                <li>Нажмите «Загрузить распакованное расширение».</li>
                <li>Выберите распакованную папку.</li>
              </ol>
            </div>
            <div className="gatewayStepCard gatewayCodeCard">
              <div className="gatewayStepTitle">Куда вводить код</div>
              <div className="gatewayCodeSteps">
                <span>1</span><p>Закрепите BORIS Browser Gateway на панели Chrome.</p>
                <span>2</span><p>Нажмите серую иконку <b>B</b> справа от адресной строки.</p>
                <span>3</span><p>Вставьте код из шага 2 и нажмите <b>«Подключить»</b>.</p>
                <span>4</span><p>Нажмите <b>«Проверить связь»</b>.</p>
              </div>
            </div>
          </div>
        </div>
      </details>
    </Card>

    {error ? <Alert kind="danger">{error}</Alert> : null}

    <Card style={{width:"100%",boxSizing:"border-box"}}>
      <div style={{display:"flex",gap:space.md,alignItems:"center",flexWrap:"wrap"}}>
        <div style={{flex:"1 1 420px",minWidth:0}}>
          <div style={{...font.h3,color:color.heading}}>Параметры объявлений этого аккаунта</div>
          <div style={{...font.small,marginTop:5}}>Изучено активных объявлений: {memory?.active_items_scanned || 0}. {sourceText(memory?.source)}</div>
          <div style={{...font.small,marginTop:3}}>BORIS запоминает структуру именно выбранного Avito-аккаунта. У другого клиента набор категорий, подкатегорий и характеристик будет свой.</div>
          {!!memory?.segments?.length && <div style={{marginTop:12,maxWidth:620}}>
            <div style={{...font.small,fontWeight:800,color:color.heading,marginBottom:6}}>Какие объявления правим</div>
            <select value={segmentKey} onChange={e=>setSegmentKey(e.target.value)} style={{width:"100%",padding:"11px 12px",border:`1px solid ${color.line}`,borderRadius:11,background:"#fff",fontWeight:700,color:color.heading}}>
              <option value="">Все категории аккаунта</option>
              {memory.segments.map((s:any)=><option key={s.key} value={s.key}>{s.title} · {s.seen_count || 0} объявлений</option>)}
            </select>
            <div style={{...font.small,marginTop:5}}>Для смешанного аккаунта сначала выберите нужную категорию или подкатегорию. BORIS покажет только её параметры и не применит их к другим типам объявлений.</div>
          </div>}
        </div>
        <Button onClick={scanCharacteristics} disabled={busy || !account}>Обновить параметры из Avito</Button>
      </div>

      {[{key:"main",title:"Основное",hint:"Цена, описание и фотографии"},{key:"category",title:"Категория и размещение",hint:"Категория, подкатегория, тип товара и параметры размещения"},{key:"params",title:"Характеристики объявления",hint:"Бренд, материал, размеры, типы и другие параметры именно этого аккаунта"}].map(group=>{
        const groupFields=fields.filter(f=>fieldGroup(f.label)===group.key);
        if(!groupFields.length) return null;
        return <details key={group.key} open={group.key==="main"} style={{marginTop:space.md,border:"1px solid #dfe7f3",borderTop:`3px solid ${group.key === "main" ? "#2f6fff" : group.key === "category" ? "#10b981" : "#7c5cff"}`,borderRadius:16,overflow:"hidden",background:"linear-gradient(180deg,#fff,#fbfdff)",boxShadow:"0 5px 16px rgba(31,58,103,.035)"}}>
          <summary style={{cursor:"pointer",listStyle:"none",padding:"14px 16px",display:"flex",alignItems:"center",justifyContent:"space-between",gap:12,flexWrap:"wrap"}}>
            <span><b style={{...font.body,color:color.heading}}>{group.title}</b><span style={{...font.small,display:"block",marginTop:2}}>{group.hint}</span></span>
            <Badge kind="neutral">{groupFields.length} параметров</Badge>
          </summary>
          <div className="accountFieldsGrid" style={{padding:"0 14px 14px"}}>
            {groupFields.slice(0,80).map((f,idx)=>{ const r=fieldRules[f.label] || {enabled:false,mode:"fixed" as const,value:"",values:""}; const set=(patch:any)=>setFieldRules(prev=>({...prev,[f.label]:{...r,...patch}})); const pretty=humanLabel(f.label); const isBrand=String(f.label||"").trim().toLowerCase()==="бренд"; const avitoOptions=Array.from(new Set(f.options||[])); const approvedClientBrand=isBrand && clientBrandSuggestion && avitoOptions.includes(clientBrandSuggestion) ? clientBrandSuggestion : ""; const optionValues=avitoOptions; return <div key={`${f.label}-${idx}`} style={{border:`1px solid ${r.enabled ? "#8EB8FF" : "#dfe7f3"}`,borderTop:`3px solid ${r.enabled ? "#2f6fff" : group.key === "main" ? "#2f6fff" : group.key === "category" ? "#10b981" : "#7c5cff"}`,borderRadius:16,padding:14,minWidth:0,minHeight:92,background:r.enabled?"#F8FBFF":"linear-gradient(180deg,#fff,#fbfdff)",boxShadow:"0 5px 16px rgba(31,58,103,.035)"}}>
              <label style={{display:"flex",gap:8,alignItems:"flex-start",cursor:"pointer"}}><input type="checkbox" checked={r.enabled} onChange={e=>set({enabled:e.target.checked})} style={{marginTop:3}}/><span style={{minWidth:0}}><span style={{...font.body,color:color.heading,fontWeight:800,wordBreak:"break-word"}}>{pretty}</span></span></label>
              <div style={{...font.small,marginTop:7}}>{f.seen_count ? `Найдено в ${f.seen_count} объявлениях` : sourceText(f.source)}</div>
              {approvedClientBrand ? <div style={{marginTop:9}}><div style={{...font.small,fontWeight:800,color:color.heading,marginBottom:6}}>Бренд клиента</div><button type="button" onClick={()=>set({enabled:true,mode:"fixed",value:approvedClientBrand})} style={{border:0,background:"transparent",padding:0,cursor:"pointer"}}><Badge kind="success">{approvedClientBrand}</Badge></button><div style={{...font.small,marginTop:5}}>Бренд есть в официальном справочнике Avito. Нажмите, чтобы выбрать для правки.</div></div> : isBrand && clientBrandSuggestion ? <div style={{...font.small,marginTop:8}}>Бренд компании «{clientBrandSuggestion}» не найден в справочнике Avito для этой категории, поэтому BORIS не предлагает его для массовой правки.</div> : null}
              {!!optionValues.length && (optionValues.length > 6 ? <div style={{marginTop:9}}><div style={{...font.small,fontWeight:700,color:color.heading,marginBottom:6}}>Доступные значения · {optionValues.length}</div><details className="compactValueSelect"><summary>{r.enabled && r.mode==="fixed" && r.value ? r.value : "Выберите значение"}<span>⌄</span></summary><div className="compactValueMenu">{optionValues.map(v=><button type="button" key={v} className={isBrand && v===clientBrandSuggestion ? "clientBrandOption" : ""} onClick={e=>{set({enabled:true,mode:"fixed",value:v}); (e.currentTarget.closest("details") as HTMLDetailsElement | null)?.removeAttribute("open");}}>{isBrand && v===clientBrandSuggestion ? <><b>{v}</b><small>Бренд клиента</small></> : v}</button>)}</div></details></div> : <div style={{marginTop:9}}><div style={{...font.small,fontWeight:700,color:color.heading,marginBottom:6}}>Доступные значения</div><div style={{display:"flex",gap:5,flexWrap:"wrap"}}>{optionValues.map(v=><button type="button" key={v} onClick={()=>set({enabled:true,mode:"fixed",value:v})} style={{border:0,background:"transparent",padding:0,cursor:"pointer"}}><Badge kind={isBrand && v===clientBrandSuggestion ? "success" : "neutral"}>{v}</Badge></button>)}</div></div>)}
              {r.enabled ? <div style={{display:"grid",gap:8,marginTop:11}}>
                {f.type !== "files" && !isBrand ? <div><div style={{...font.small,fontWeight:700,color:color.heading,marginBottom:5}}>Как применить</div><select value={r.mode} onChange={e=>set({mode:e.target.value as "fixed"|"random"})} style={{width:"100%",padding:"10px 11px",border:`1px solid ${color.line}`,borderRadius:10,background:"white"}}><option value="fixed">Одинаковое значение для выбранных объявлений</option><option value="random">Распределить разные значения случайно</option></select></div> : null}
                {isBrand ? <div style={{...font.small,padding:"9px 10px",border:`1px solid ${color.line}`,borderRadius:10,background:"#fff"}}>{r.value ? `Выбрано: ${r.value}` : "Выберите бренд из справочника Avito выше."}</div> : r.mode === "fixed" ? <div><div style={{...font.small,fontWeight:700,color:color.heading,marginBottom:5}}>{f.type === "files" ? "Новые фотографии" : "Новое значение"}</div>{(f.type === "textarea" || f.type === "files") ? <textarea value={r.value} onChange={e=>set({value:e.target.value,mode:"fixed"})} rows={f.type === "files" ? 4 : 6} placeholder={f.type === "files" ? "Ссылки на фотографии — по одной на строку" : `Введите новое значение: ${pretty.toLowerCase()}`} style={{width:"100%",boxSizing:"border-box",padding:"10px 11px",border:`1px solid ${color.line}`,borderRadius:10,resize:"vertical"}}/> : <input value={r.value} onChange={e=>set({value:e.target.value})} placeholder={f.label === "Цена" ? "Например: 15000" : `Введите: ${pretty.toLowerCase()}`} style={{width:"100%",boxSizing:"border-box",padding:"10px 11px",border:`1px solid ${color.line}`,borderRadius:10}}/>}</div> : <div><div style={{...font.small,fontWeight:700,color:color.heading,marginBottom:5}}>Варианты значений</div><input value={r.values} onChange={e=>set({values:e.target.value})} placeholder="Например: вариант 1, вариант 2, вариант 3" style={{width:"100%",boxSizing:"border-box",padding:"10px 11px",border:`1px solid ${color.line}`,borderRadius:10}}/><div style={{...font.small,marginTop:4}}>BORIS распределит эти значения между объявлениями и покажет план до сохранения.</div></div>}
                {f.type === "files" ? <div style={font.small}>До 10 фотографий на объявление. Старые фотографии заменяются только после подтверждения задания.</div> : null}
              </div> : null}
            </div>})}
          </div>
        </details>
      })}
    </Card>

    <Card style={{width:"100%",boxSizing:"border-box"}}>
      <div style={{display:"flex",gap:space.md,alignItems:"flex-start",justifyContent:"space-between",flexWrap:"wrap"}}>
        <div style={{flex:"1 1 520px",minWidth:0}}>
          <div style={{...font.h3,color:color.heading}}>Анализ топ-конкурентов Avito</div>
          <div style={{...font.small,marginTop:5}}>BORIS открывает реальную выдачу Avito в Browser Gateway, читает первые позиции и сравнивает их с активными объявлениями этого аккаунта. Анализ ничего не меняет на Avito.</div>
        </div>
        <Badge kind="neutral">Только чтение</Badge>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(min(100%,220px),1fr))",gap:10,marginTop:14}}>
        <div><div style={{...font.small,fontWeight:800,color:color.heading,marginBottom:5}}>Что ищем</div><Input value={marketQuery} onChange={(e:any)=>setMarketQuery(e.target.value)} placeholder="Например: душевые перегородки" /></div>
        <div><div style={{...font.small,fontWeight:800,color:color.heading,marginBottom:5}}>Город</div><Input value={marketCity} onChange={(e:any)=>setMarketCity(e.target.value)} placeholder="Санкт-Петербург" /></div>
        <div><div style={{...font.small,fontWeight:800,color:color.heading,marginBottom:5}}>Сколько конкурентов</div><select value={marketLimit} onChange={e=>setMarketLimit(Math.max(1,Math.min(10,Number(e.target.value)||10)))} style={{width:"100%",padding:"10px 11px",border:`1px solid ${color.line}`,borderRadius:10,background:"white"}}>{[3,5,7,10].map(n=><option key={n} value={n}>Топ {n}</option>)}</select></div>
      </div>
      <div style={{display:"grid",gap:8,marginTop:12}}>
        <label style={{display:"flex",gap:9,alignItems:"flex-start",cursor:"pointer"}}><input type="checkbox" checked={useMarketPatterns} onChange={e=>setUseMarketPatterns(e.target.checked)} style={{marginTop:3}}/><span><b style={{color:color.heading}}>Учитывать сильные паттерны конкурентов</b><span style={{...font.small,display:"block",marginTop:2}}>BORIS запомнит удачные подходы к заголовкам, цене и подаче как ориентир, без копирования чужих фактов.</span></span></label>
        <label style={{display:"flex",gap:9,alignItems:"flex-start",cursor:"pointer"}}><input type="checkbox" checked={blueOcean} onChange={e=>setBlueOcean(e.target.checked)} style={{marginTop:3}}/><span><b style={{color:color.heading}}>Отстраиваться от конкурентов</b><span style={{...font.small,display:"block",marginTop:2}}>Искать отличия и свободные смысловые зоны, а не делать объявления-клоны.</span></span></label>
      </div>
      <div style={{marginTop:14}}><Button onClick={runMarketAnalysis} disabled={marketLoading || !account}>{marketLoading ? "Анализирую выдачу…" : "Проанализировать топ выдачи"}</Button></div>
      {marketResult ? <div style={{marginTop:16,display:"grid",gap:12}}>
        <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:10}}>
          <div style={cardShell}><div style={font.small}>Проверено позиций</div><div style={{...font.h3,color:color.heading,marginTop:3}}>{marketResult.count_on_page || 0}</div></div>
          <div style={cardShell}><div style={font.small}>Средняя цена</div><div style={{...font.h3,color:color.heading,marginTop:3}}>{marketResult.avg_price ? `${Number(marketResult.avg_price).toLocaleString("ru-RU")} ₽` : "—"}</div></div>
          <div style={cardShell}><div style={font.small}>Продвигаются</div><div style={{...font.h3,color:color.heading,marginTop:3}}>{marketResult.promoted_count ?? 0}</div></div>
          <div style={cardShell}><div style={font.small}>Наши в топе</div><div style={{...font.h3,color:color.heading,marginTop:3}}>{marketResult.own_positions?.length || 0}</div></div>
        </div>
        {marketResult.own_positions?.length ? <Alert kind="success">Наши позиции: {marketResult.own_positions.map((x:any)=>`№${x.position} ${x.title}`).join(" · ")}</Alert> : <Alert kind="warning">В проверенном топе собственные активные объявления не найдены.</Alert>}
        <details open style={{border:`1px solid ${color.line}`,borderRadius:14,overflow:"hidden"}}><summary style={{cursor:"pointer",padding:"12px 14px",fontWeight:800,color:color.heading}}>Топ конкурентов · {marketResult.top_competitors?.length || 0}</summary><div style={{display:"grid",gap:8,padding:12}}>{(marketResult.top_competitors||[]).slice(0,marketLimit).map((x:any)=><div key={`${x.position}-${x.item_id||x.url}`} style={cardShell}><div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"flex-start"}}><div style={{minWidth:0}}><div style={{...font.small,fontWeight:800}}>№{x.position}{x.promoted ? " · Продвижение" : ""}</div><div style={{...font.body,color:color.heading,fontWeight:800,marginTop:3}}>{x.title}</div>{x.seller ? <div style={{...font.small,marginTop:4}}>Продавец: {x.seller}</div> : null}</div><div style={{fontWeight:900,color:color.heading,whiteSpace:"nowrap"}}>{x.price ? `${Number(x.price).toLocaleString("ru-RU")} ₽` : "—"}</div></div>{x.url ? <a href={x.url} target="_blank" rel="noreferrer" style={{display:"inline-block",marginTop:7,fontSize:13,fontWeight:700}}>Открыть объявление</a> : null}</div>)}</div></details>
        {marketResult.recommendations?.length ? <div style={cardShell}><div style={{...font.body,fontWeight:900,color:color.heading}}>Что делать по результату</div><div style={{display:"grid",gap:6,marginTop:8}}>{marketResult.recommendations.map((x:string,i:number)=><div key={i} style={font.small}>• {x}</div>)}</div></div> : null}
      </div> : null}
    </Card>

    <Card style={{width:"100%",boxSizing:"border-box"}}>
      <div style={{display:"flex",gap:space.sm,alignItems:"center",flexWrap:"wrap"}}>
        <div style={{...font.h3,color:color.heading,flex:"1 1 320px"}}>Массовая правка</div>
        <select value={scopeMode} onChange={e=>{ setJob(null); setPreview([]); setOpen(false); const next=e.target.value as "all"|"count"; setScopeMode(next); if(next === "count") setLimit(1); }} style={{padding:"10px 12px",border:`1px solid ${color.line}`,borderRadius:11,background:"#fff",fontWeight:700,color:color.heading}}>
          <option value="all">Все активные объявления</option>
          <option value="count">Определённое количество</option>
        </select>
        {scopeMode === "count" ? <div style={{width:130}}><Input value={String(limit)} onChange={(e:any)=>{ setJob(null); setPreview([]); setOpen(false); const raw=String(e.target.value ?? '').replace(/\D/g,''); setLimit(raw === '' ? 1 : Math.max(1,Number(raw))); }} /></div> : null}
        <Button onClick={loadPreview} disabled={busy || !account}>{busy ? "Подготавливаю…" : scopeMode === "all" ? "Подготовить все активные" : `Подготовить ${limit} объявлений`}</Button>
      </div>

      {open && preview.length ? <div style={{ marginTop:space.lg }}>
        <div style={{ ...font.small, marginBottom:space.sm }}>Перед запуском · остальные поля не изменяются</div>
        <details open={showPreviewItems} onToggle={(e:any)=>setShowPreviewItems(e.currentTarget.open)} style={{border:`1px solid ${color.line}`,borderRadius:14,overflow:"hidden"}}>
          <summary style={{cursor:"pointer",padding:"12px 14px",fontWeight:800,color:color.heading}}>Показать список объявлений · {preview.length}</summary>
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(min(100%,360px),1fr))",gap:space.sm,padding:12,maxHeight:520,overflowY:"auto"}}>
            {preview.map((it, idx) => <div key={it.id} style={{padding:14,border:"1px solid #dfe7f3",borderTop:"3px solid #2f6fff",borderRadius:16,minWidth:0,background:"linear-gradient(180deg,#fff,#fbfdff)",boxShadow:"0 5px 16px rgba(31,58,103,.035)"}}>
              <div style={{...font.small}}>{idx+1}</div><div style={{...font.body,color:color.heading,marginTop:4,fontWeight:700}}>{it.title}</div>
              <div style={{display:"flex",gap:6,flexWrap:"wrap",marginTop:8}}>{Object.entries(it.changes || {}).map(([k,v]) => <Badge key={k} kind="neutral">{displayFieldName(k)}: {v}</Badge>)}</div>
            </div>)}
          </div>
        </details>
        {!job ? <div style={{ marginTop:space.md, display:"flex", gap:space.sm, alignItems:"center", flexWrap:"wrap" }}>
          <Button onClick={startTest} disabled={busy}>Запустить правку</Button>
        </div> : null}
      </div> : null}

      {job ? <div style={{ marginTop:space.lg, paddingTop:space.md, borderTop:`1px solid ${color.line}` }}>
        <div style={{ display:"flex", justifyContent:"space-between", gap:space.md, flexWrap:"wrap" }}>
          <div><b>{statusText(job.status)}</b></div><div>{progress}% · обработано {job.processed || 0} из {job.total || 0}</div>
        </div>
        <div style={{ ...font.small, marginTop:8 }}>Изменено: {job.changed || 0} · Проверено: {job.verified || 0} · Пропущено: {job.skipped || 0} · Ошибок: {job.failed || 0}</div>
        {job.error ? <div style={{ marginTop:space.sm }}><Alert kind="danger">{job.error}</Alert></div> : null}
        <details open={showJobItems} onToggle={(e:any)=>setShowJobItems(e.currentTarget.open)} style={{marginTop:space.sm,border:`1px solid ${color.line}`,borderRadius:14,overflow:"hidden"}}>
          <summary style={{cursor:"pointer",padding:"12px 14px",fontWeight:800,color:color.heading}}>Подробности по объявлениям · {job.total || (job.items || []).length}</summary>
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(min(100%,320px),1fr))",gap:space.sm,padding:12,maxHeight:520,overflowY:"auto"}}>
            {(job.items || []).map((it:Planned) => <div key={it.id} style={{ ...font.small, padding:14,border:"1px solid #dfe7f3",borderTop:`3px solid ${it.run_status === "failed" ? "#ef476f" : it.run_status === "verified" || it.run_status === "changed" ? "#10b981" : "#2f6fff"}`,borderRadius:16,minWidth:0,background:"linear-gradient(180deg,#fff,#fbfdff)",boxShadow:"0 5px 16px rgba(31,58,103,.035)" }}>
              {it.title}<br/><b>{statusText(it.run_status)}</b>{it.error ? ` · ${it.error}` : ""}
            </div>)}
          </div>
        </details>
      </div> : null}
    </Card>
    <style jsx>{`
      .accountFieldsGrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;align-items:start}
      .accountFieldsGrid>div{height:max-content}
      .compactValueSelect{position:relative;border:1px solid #d9e2ef;border-radius:11px;background:#fff;box-shadow:0 4px 12px rgba(31,58,103,.04)}
      .compactValueSelect>summary{list-style:none;cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;font-size:14px;font-weight:800;color:#24324a;min-height:20px}
      .compactValueSelect>summary::-webkit-details-marker{display:none}
      .compactValueSelect[open]>summary{border-bottom:1px solid #e7edf5}
      .compactValueMenu{max-height:220px;overflow:auto;padding:6px;background:#fff;border-radius:0 0 11px 11px}
      .compactValueMenu button{width:100%;border:0;background:transparent;text-align:left;padding:8px 10px;border-radius:8px;cursor:pointer;color:#34425a;font-size:13px;display:flex;align-items:center;justify-content:space-between;gap:10px}
      .compactValueMenu button:hover{background:#f2f6ff;color:#1f5fe0}
      .compactValueMenu .clientBrandOption{background:#eefbf4;color:#148a55;font-weight:800}
      .compactValueMenu .clientBrandOption:hover{background:#e2f7eb;color:#0f7648}
      .compactValueMenu small{font-size:11px;font-weight:700;color:#4f9f77;white-space:nowrap}
      .gatewayTopGrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;align-items:stretch}
      .gatewayTopCard{min-width:0;padding:16px;border:1px solid #dfe7f3;border-top:3px solid #2f6fff;border-radius:16px;background:linear-gradient(180deg,#fff,#fbfdff);box-shadow:0 5px 16px rgba(31,58,103,.035);box-sizing:border-box}
      .gatewayEyebrow{font-size:12px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:#2f6fff;margin-bottom:6px}
      .gatewayCode{margin-top:10px;padding:10px 12px;border-radius:10px;background:#eef4ff;border:1px solid #cfe0ff;font-size:18px;font-weight:800;letter-spacing:1px;color:#172033;word-break:break-all}
      .gatewayGuide{margin-top:14px;border:1px solid #dfe7f3;border-radius:16px;background:#f8fafc;overflow:hidden}
      .gatewayGuideSummary{cursor:pointer;padding:14px 16px;font-weight:800;color:#172033;list-style:none;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
      .gatewayGuideSummary::-webkit-details-marker{display:none}
      .gatewayGuideHint{font-size:12px;font-weight:600;color:#7a879d}
      .gatewayGuideBody{padding:0 14px 14px}
      .gatewayOsGrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
      .gatewayStepCard{padding:14px;border:1px solid #e2e8f0;border-radius:14px;background:#fff;min-width:0}
      .gatewayStepTitle{font-size:15px;font-weight:800;color:#172033;margin-bottom:8px}
      .gatewayStepCard ol{margin:0;padding-left:20px;color:#526079;font-size:14px;line-height:1.55}
      .gatewayStepCard li+li{margin-top:5px}
      .gatewayCodeSteps{display:grid;grid-template-columns:28px minmax(0,1fr);gap:8px 10px;align-items:start}
      .gatewayCodeSteps span{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:#eef4ff;color:#2f6fff;font-size:12px;font-weight:800}
      .gatewayCodeSteps p{margin:2px 0 0;color:#526079;font-size:14px;line-height:1.45}
      @media (max-width:1100px){
        .gatewayTopGrid{grid-template-columns:repeat(2,minmax(0,1fr))}
        .gatewayTopCard:first-child{grid-column:1/-1}
        .gatewayOsGrid{grid-template-columns:repeat(2,minmax(0,1fr))}
        .gatewayCodeCard{grid-column:1/-1}
      }
      @media (max-width:720px){
        .accountFieldsGrid{grid-template-columns:repeat(auto-fill,minmax(min(100%,200px),1fr))}
        .gatewayTopGrid,.gatewayOsGrid{grid-template-columns:1fr}
        .gatewayTopCard:first-child,.gatewayCodeCard{grid-column:auto}
        .gatewayTopCard{padding:14px}
        .gatewayGuideSummary{align-items:flex-start;flex-direction:column}
      }
      @media (max-width:480px){
        .accountFieldsGrid{grid-template-columns:1fr}
        .gatewayTopCard{padding:12px;border-radius:14px}
        .gatewayGuide{border-radius:14px}
        .gatewayGuideBody{padding:0 10px 10px}
        .gatewayStepCard{padding:12px}
        .gatewayStepCard ol,.gatewayCodeSteps p{font-size:13px}
      }
    `}</style>
  </div>;
}
