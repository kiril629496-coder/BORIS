"use client";

import { useEffect, useMemo, useState } from "react";
import { useAccounts } from "../../lib/AccountContext";

type MaxStatus = {
  status?: string;
  token_configured?: boolean;
  token_valid?: boolean | null;
  webhook_registered?: boolean;
  webhook_remote_confirmed?: boolean | null;
  chat_connected?: boolean;
  bot_in_chat?: boolean | null;
  leadership_chat_connected?: boolean;
  leadership_bot_in_chat?: boolean | null;
  bot?: { first_name?: string; username?: string; user_id?: number | string } | null;
};

type Settings = {
  account_id?: string;
  web_push_enabled: boolean;
  web_push_ready?: boolean;
  active_push_subscriptions?: number;
  max_enabled: boolean;
  max_chat_id?: string;
  max_token_configured?: boolean;
  email_enabled: boolean;
  email_to?: string;
  email_mode: "critical_only" | "all";
};

const EMPTY: Settings = {
  web_push_enabled: true,
  max_enabled: false,
  max_chat_id: "",
  max_token_configured: false,
  email_enabled: false,
  email_to: "",
  email_mode: "critical_only",
};

function authHeaders(json=false): Record<string,string> {
  const token = typeof window !== "undefined" ? (localStorage.getItem("boris_token") || "") : "";
  const h: Record<string,string> = { Authorization: "Bearer " + token };
  if (json) h["Content-Type"] = "application/json";
  return h;
}

async function api(url:string, init?:RequestInit) {
  const r = await fetch(url, { cache:"no-store", ...init, headers:{...authHeaders(Boolean(init?.body)), ...(init?.headers || {})} });
  const raw = await r.text();
  let data:any = null;
  try { data = raw ? JSON.parse(raw) : null; } catch {}
  if (!r.ok) {
    const detail=data?.detail;
    if (detail && typeof detail==="object") throw new Error(String(detail.message || detail.status || JSON.stringify(detail)));
    throw new Error(String(detail || data?.message || raw || ("HTTP " + r.status)));
  }
  return data;
}

function base64ToUint8Array(value:string): Uint8Array {
  const padding = "=".repeat((4 - value.length % 4) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let i=0;i<raw.length;i++) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

function isIos(): boolean {
  if (typeof navigator === "undefined") return false;
  return /iphone|ipad|ipod/i.test(navigator.userAgent);
}

function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia?.("(display-mode: standalone)")?.matches === true
    || (navigator as any).standalone === true;
}

const card: React.CSSProperties = {
  background:"#fff", border:"1px solid #e4eaf2", borderRadius:18, padding:20,
  boxShadow:"0 8px 28px rgba(31,49,78,.055)",
};
const input: React.CSSProperties = {
  width:"100%", boxSizing:"border-box", border:"1px solid #d9e1ec", borderRadius:10,
  minHeight:42, padding:"9px 11px", fontSize:14, outline:"none", background:"#fff",
};
const btn: React.CSSProperties = {
  border:0, borderRadius:10, minHeight:42, padding:"0 15px", cursor:"pointer",
  fontWeight:800, fontSize:14,
};

export default function NotificationSettingsPage() {
  const {accounts, selectedAccountId, selectedAccount, setSelectedAccount} = useAccounts();
  const [accountId,setAccountId] = useState("");
  const [cfg,setCfg] = useState<Settings>(EMPTY);
  const [maxToken,setMaxToken] = useState("");
  const [maxState,setMaxState] = useState<MaxStatus>({status:"not_configured"});
  const [pushState,setPushState] = useState<"unknown"|"unsupported"|"off"|"on"|"denied">("unknown");
  const [busy,setBusy] = useState(false);
  const [notice,setNotice] = useState("");
  const [error,setError] = useState("");

  useEffect(()=>{
    const qs = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("account_id") : "";
    const fallback = typeof window !== "undefined" ? localStorage.getItem("boris_currentAccount") || "" : "";
    const next = qs || selectedAccountId || fallback;
    if (next) setAccountId(next);
  },[selectedAccountId]);

  useEffect(()=>{
    if (!accountId) return;
    let alive=true;
    setBusy(true); setError("");
    Promise.all([
      api("/api/lead-notifications/settings?account_id="+encodeURIComponent(accountId)),
      api("/api/lead-notifications/max/status?live=false&account_id="+encodeURIComponent(accountId)),
    ])
      .then(([x,m]:any[])=>{ if(alive){ setCfg({...EMPTY,...(x?.settings||{})}); setMaxState(m||{status:"not_configured"}); } })
      .catch((e:any)=>alive&&setError(e.message))
      .finally(()=>alive&&setBusy(false));
    void detectPush();
    return ()=>{alive=false};
  },[accountId]);

  const requestedAccount = useMemo(
    ()=>accounts.find(a=>a.account_id===accountId) || null,
    [accounts,accountId]
  );
  const accountName = requestedAccount?.name || accountId;
  const accountAccessible = !accountId || Boolean(requestedAccount);

  useEffect(()=>{
    if (!accountId || !requestedAccount) return;
    if (selectedAccountId !== accountId) setSelectedAccount(accountId);
  },[accountId,requestedAccount,selectedAccountId,setSelectedAccount]);

  async function detectPush() {
    if (typeof window === "undefined" || !("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      setPushState("unsupported"); return;
    }
    if (Notification.permission === "denied") { setPushState("denied"); return; }
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      setPushState(sub ? "on" : "off");
    } catch { setPushState("off"); }
  }

  async function enablePush() {
    if (!accountId) return;
    setBusy(true); setError(""); setNotice("");
    try {
      if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
        throw new Error("Этот браузер не поддерживает push-уведомления.");
      }
      if (isIos() && !isStandalone()) {
        throw new Error("На iPhone сначала добавьте BORIS на экран «Домой», откройте его с иконки и затем включите уведомления.");
      }
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setPushState(permission === "denied" ? "denied" : "off");
        throw new Error("Разрешение на уведомления не выдано.");
      }
      const k = await api("/api/lead-notifications/vapid-public-key?account_id="+encodeURIComponent(accountId));
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly:true,
          applicationServerKey: base64ToUint8Array(String(k.public_key)) as BufferSource,
        });
      }
      await api("/api/lead-notifications/subscribe", {
        method:"POST", body:JSON.stringify({account_id:accountId, subscription:sub.toJSON()}),
      });
      setCfg((x)=>({...x,web_push_enabled:true,active_push_subscriptions:Math.max(1,Number(x.active_push_subscriptions||0))}));
      setPushState("on");
      setNotice("Push включён на этом устройстве. Теперь BORIS сможет присылать статусы лидов прямо на телефон.");
    } catch(e:any) { setError(e.message || "Не удалось включить push"); }
    finally { setBusy(false); }
  }

  async function disablePush() {
    setBusy(true); setError(""); setNotice("");
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await api("/api/lead-notifications/unsubscribe", {
          method:"POST", body:JSON.stringify({account_id:accountId,endpoint:sub.endpoint}),
        });
        await sub.unsubscribe();
      }
      setPushState("off");
      setNotice("Push на этом устройстве выключен.");
    } catch(e:any) { setError(e.message || "Не удалось отключить push"); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!accountId) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const payload:any = {
        account_id:accountId,
        web_push_enabled:cfg.web_push_enabled,
        max_enabled:cfg.max_enabled,
        max_chat_id:cfg.max_chat_id || "",
        email_enabled:cfg.email_enabled,
        email_to:cfg.email_to || "",
        email_mode:cfg.email_mode || "critical_only",
      };
      if (maxToken.trim()) payload.max_bot_token=maxToken.trim();
      const x=await api("/api/lead-notifications/settings",{method:"POST",body:JSON.stringify(payload)});
      setCfg({...EMPTY,...(x?.settings||{})});
      setMaxToken("");
      setNotice("Настройки уведомлений сохранены.");
    } catch(e:any) { setError(e.message || "Не удалось сохранить настройки"); }
    finally { setBusy(false); }
  }

  async function refreshMax(live=true) {
    if (!accountId) return null;
    const x=await api("/api/lead-notifications/max/status?live="+String(live)+"&account_id="+encodeURIComponent(accountId));
    setMaxState(x||{status:"not_configured"});
    return x;
  }

  async function connectMax() {
    if (!accountId) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const payload:any={account_id:accountId};
      if(maxToken.trim()) payload.max_bot_token=maxToken.trim();
      const x=await api("/api/lead-notifications/max/connect",{method:"POST",body:JSON.stringify(payload)});
      setMaxToken("");
      const fresh=await api("/api/lead-notifications/settings?account_id="+encodeURIComponent(accountId));
      setCfg({...EMPTY,...(fresh?.settings||{})});
      await refreshMax(true);
      setNotice(x?.chat_id
        ? "MAX уже подключён к группе. Можно отправлять тест."
        : "Webhook MAX подключён. Теперь добавьте BORIS-бота в нужную MAX-группу — BORIS сам запомнит группу.");
    } catch(e:any) { setError(typeof e?.message==="string"?e.message:"Не удалось подключить MAX"); }
    finally { setBusy(false); }
  }

  async function checkMax() {
    if (!accountId) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const x=await refreshMax(true);
      const labels:any={
        ready:"MAX готов: токен, webhook и группа подтверждены.",
        waiting_for_group:"MAX-бот готов. Добавьте его в нужную группу — BORIS поймает группу автоматически.",
        token_required:"Нужен токен MAX-бота.",
        invalid_token:"Токен MAX недействителен.",
        webhook_missing:"Токен работает, но webhook MAX не подтверждён. Нажмите «Подключить MAX».",
        bot_not_in_chat:"Группа сохранена, но бот сейчас в ней не состоит.",
        max_unavailable:"MAX API сейчас недоступен с сервера.",
      };
      setNotice(labels[String(x?.status||"")] || ("Статус MAX: "+String(x?.status||"неизвестно")));
    } catch(e:any) { setError(e.message || "Не удалось проверить MAX"); }
    finally { setBusy(false); }
  }

  async function testMax() {
    if (!accountId) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const x=await api("/api/lead-notifications/max/test",{method:"POST",body:JSON.stringify({account_id:accountId})});
      if (x?.status==="sent") {
        setNotice("Тест MAX отправлен. В группе должно появиться сообщение «BORIS ↔ MAX: тест связи».");
      } else {
        setMaxState(x?.diagnostics||maxState);
        throw new Error("MAX ещё не готов к тестовой доставке: "+String(x?.diagnostics?.status||x?.status||"unknown"));
      }
    } catch(e:any) { setError(e.message || "Не удалось отправить тест MAX"); }
    finally { setBusy(false); }
  }

  async function test() {
    if (!accountId) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await api("/api/lead-notifications/test",{method:"POST",body:JSON.stringify({account_id:accountId})});
      setNotice("Тест поставлен в доставку. Push/MAX обычно приходит почти сразу; email — только если он включён.");
    } catch(e:any) { setError(e.message || "Не удалось отправить тест"); }
    finally { setBusy(false); }
  }

  const set=(k:keyof Settings,v:any)=>setCfg((x)=>({...x,[k]:v}));
  const pushLabel = pushState==="on" ? "Включён на этом устройстве" : pushState==="denied" ? "Заблокирован в браузере" : pushState==="unsupported" ? "Не поддерживается" : "Не включён";

  return <main style={{minHeight:"100vh",background:"#f5f8fc",padding:"clamp(14px,3vw,34px)"}}>
    <div style={{maxWidth:1050,margin:"0 auto"}}>
      <header style={{marginBottom:18}}>
        <div style={{fontSize:12,fontWeight:900,letterSpacing:".08em",color:"#3971db"}}>BORIS · ОПОВЕЩЕНИЯ О ЛИДАХ</div>
        <h1 style={{fontSize:"clamp(28px,4vw,42px)",lineHeight:1.05,margin:"8px 0",color:"#17233a"}}>Клиент узнаёт о лиде сразу</h1>
        <p style={{margin:0,color:"#69778d",fontSize:15,maxWidth:760,lineHeight:1.55}}>
          Push на телефон — персонально сотруднику. MAX — общая группа отдела. Почта — только для важных событий, чтобы не засыпать ящик.
        </p>
      </header>

      <div style={{...card,marginBottom:14,display:"flex",gap:12,alignItems:"center",flexWrap:"wrap"}}>
        <div style={{fontWeight:800}}>Аккаунт</div>
        <select value={accountId} onChange={e=>{setAccountId(e.target.value);setSelectedAccount(e.target.value)}} style={{...input,width:"min(100%,420px)"}}>
          {!accountAccessible && accountId ? <option value={accountId}>{accountId} · нет доступа</option> : null}
          {accounts.map(a=><option value={a.account_id} key={a.account_id}>{a.name || a.account_id}</option>)}
        </select>
        <span style={{fontSize:13,color:"#7b8798"}}>{accountName}</span>
      </div>

      {!accountAccessible ? <div style={{marginBottom:14,padding:"12px 14px",borderRadius:12,background:"#fff7ed",color:"#9a3412",fontWeight:700}}>Этот вход BORIS не имеет доступа к запрошенному аккаунту. Войдите под владельцем BORIS или под пользователем этого клиента.</div> : null}
      {error ? <div style={{marginBottom:14,padding:"12px 14px",borderRadius:12,background:"#fff1f2",color:"#b42318",fontWeight:700}}>{error}</div> : null}
      {notice ? <div style={{marginBottom:14,padding:"12px 14px",borderRadius:12,background:"#ecfdf3",color:"#087443",fontWeight:700}}>{notice}</div> : null}

      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(280px,1fr))",gap:14}}>
        <section style={card}>
          <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"start"}}>
            <div><div style={{fontSize:12,fontWeight:900,color:"#3971db"}}>1 · ОСНОВНОЙ КАНАЛ</div><h2 style={{fontSize:21,margin:"6px 0"}}>Push в BORIS</h2></div>
            <span style={{fontSize:11,fontWeight:900,padding:"5px 8px",borderRadius:999,background:pushState==="on"?"#ecfdf3":"#f1f5f9",color:pushState==="on"?"#087443":"#64748b"}}>{pushLabel}</span>
          </div>
          <p style={{color:"#667085",fontSize:14,lineHeight:1.5}}>Системное уведомление на экран телефона даже когда BORIS не открыт. Telegram для этого не нужен.</p>
          <label style={{display:"flex",gap:9,alignItems:"center",fontSize:14,fontWeight:700,margin:"14px 0"}}>
            <input type="checkbox" checked={cfg.web_push_enabled} onChange={e=>set("web_push_enabled",e.target.checked)}/> Использовать push для этого аккаунта
          </label>
          <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
            {pushState==="on"
              ? <button disabled={busy} onClick={disablePush} style={{...btn,background:"#eef2f7",color:"#344054"}}>Выключить на этом устройстве</button>
              : <button disabled={busy} onClick={enablePush} style={{...btn,background:"#2563eb",color:"#fff"}}>Включить push</button>}
          </div>
          <small style={{display:"block",color:"#7d8899",marginTop:12,lineHeight:1.45}}>
            iPhone: BORIS нужно добавить на экран «Домой» и открыть с иконки. Android/ПК: достаточно разрешить уведомления в браузере.
          </small>
        </section>

        <section style={card}>
          <div style={{fontSize:12,fontWeight:900,color:"#7c3aed"}}>2 · ОБЩАЯ ГРУППА</div>
          <h2 style={{fontSize:21,margin:"6px 0"}}>MAX</h2>
          <p style={{color:"#667085",fontSize:14,lineHeight:1.5}}>Подходит для группы клиента: BORIS-бот + руководитель + сотрудники. Горячий лид и смена статуса видны всей команде.</p>
          <label style={{display:"flex",gap:9,alignItems:"center",fontSize:14,fontWeight:700,margin:"12px 0"}}>
            <input type="checkbox" checked={cfg.max_enabled} onChange={e=>set("max_enabled",e.target.checked)}/> Отправлять в MAX
          </label>
          <label style={{display:"block",fontSize:12,fontWeight:800,color:"#475467"}}>Токен BORIS-бота
            <input type="password" value={maxToken} onChange={e=>setMaxToken(e.target.value)} placeholder={cfg.max_token_configured?"Токен уже сохранён · оставьте пустым":"Вставьте токен бота"} style={{...input,marginTop:5}}/>
          </label>
          <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap",marginTop:10}}>
            <button disabled={busy||(!maxToken.trim()&&!cfg.max_token_configured)} onClick={connectMax} style={{...btn,background:"#f1ebff",color:"#6d28d9"}}>
              {cfg.max_chat_id?"Переподключить MAX":"Подключить MAX"}
            </button>
            <button disabled={busy||(!cfg.max_token_configured&&!maxToken.trim())} onClick={checkMax} style={{...btn,background:"#f1f5f9",color:"#344054"}}>Проверить MAX</button>
            <button disabled={busy||maxState.status!=="ready"} onClick={testMax} style={{...btn,background:maxState.status==="ready"?"#ecfdf3":"#f1f5f9",color:maxState.status==="ready"?"#087443":"#98a2b3"}}>Тест в группу</button>
          </div>
          <div style={{display:"grid",gap:6,marginTop:12,fontSize:12}}>
            {[
              ["Токен",maxState.token_valid===true?"✓ действителен":maxState.token_configured?"• сохранён":"— нет"],
              ["Webhook",maxState.webhook_remote_confirmed===true?"✓ подтверждён":maxState.webhook_registered?"• зарегистрирован локально":"— нет"],
              ["Рабочий чат Альбины",maxState.chat_connected?"✓ найден":"— ждём добавления бота"],
              ["Бот в рабочем чате",maxState.bot_in_chat===true?"✓ да":maxState.chat_connected?"• не подтверждён":"— ещё нет чата"],
              ["Чат руководства",maxState.leadership_chat_connected?"✓ найден":"— подключим отдельно"],
            ].map(([k,v])=><div key={k} style={{display:"flex",justifyContent:"space-between",gap:10,padding:"6px 8px",background:"#f8fafc",borderRadius:8}}><b>{k}</b><span>{v}</span></div>)}
          </div>
          {maxState.bot?.username ? <small style={{display:"block",color:"#596579",marginTop:8}}>Бот: @{maxState.bot.username}</small> : null}
          <small style={{display:"block",color:"#7d8899",marginTop:10}}>BORIS сам получает ID группы через защищённый webhook после добавления бота. Токен хранится на сервере зашифрованным и обратно в интерфейс не показывается.</small>
        </section>

        <section style={card}>
          <div style={{fontSize:12,fontWeight:900,color:"#0f8a65"}}>3 · РЕЗЕРВ</div>
          <h2 style={{fontSize:21,margin:"6px 0"}}>Почта без спама</h2>
          <p style={{color:"#667085",fontSize:14,lineHeight:1.5}}>По умолчанию отправляем только горячие лиды, передачу сотруднику, выигранные и потерянные сделки.</p>
          <label style={{display:"flex",gap:9,alignItems:"center",fontSize:14,fontWeight:700,margin:"12px 0"}}>
            <input type="checkbox" checked={cfg.email_enabled} onChange={e=>set("email_enabled",e.target.checked)}/> Включить email
          </label>
          <input type="email" value={cfg.email_to||""} onChange={e=>set("email_to",e.target.value)} placeholder="manager@company.ru" style={input}/>
          <select value={cfg.email_mode} onChange={e=>set("email_mode",e.target.value as any)} style={{...input,marginTop:8}}>
            <option value="critical_only">Только важное — рекомендовано</option>
            <option value="all">Все события — много писем</option>
          </select>
        </section>
      </div>

      <section style={{...card,marginTop:14}}>
        <h2 style={{fontSize:20,margin:"0 0 8px"}}>Что будет прилетать</h2>
        <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:8,fontSize:14}}>
          {["🆕 Новый лид","🔥 Получен телефон","📞 Сегодня / скоро звонок","⏰ Рабочая задача","⚠️ Просрочка","♻️ Реактивация базы","👤 Нужен сотрудник","🔄 Этап CRM изменился","✅ Сделка выиграна","❌ Лид потерян"].map(x=><div key={x} style={{padding:"10px 11px",background:"#f8fafc",borderRadius:10,fontWeight:700}}>{x}</div>)}
        </div>
        <div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:16}}>
          <button disabled={busy||!accountId} onClick={save} style={{...btn,background:"#17233a",color:"#fff"}}>{busy?"Сохраняем…":"Сохранить"}</button>
          <button disabled={busy||!accountId} onClick={test} style={{...btn,background:"#eef4ff",color:"#2458c5"}}>Отправить тест</button>
          <a href={"/messages?account_id="+encodeURIComponent(accountId)} style={{...btn,display:"inline-flex",alignItems:"center",textDecoration:"none",background:"#f1f5f9",color:"#344054"}}>К сообщениям</a>
        </div>
      </section>
    </div>
  </main>;
}
