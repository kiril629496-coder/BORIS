"use client";
import { useEffect, useRef, useState } from "react";

// Виджет Cloudflare Turnstile. Sitekey публичный и безопасен на фронте —
// решение принимает backend через Siteverify. Здесь только получение токена.

declare global {
  interface Window { turnstile?: any }
}

const SCRIPT_SRC =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

export const SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY || "";

function loadScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (typeof window === "undefined") return reject(new Error("no window"));
    if (window.turnstile) return resolve();
    const existing = document.querySelector<HTMLScriptElement>(
      'script[data-boris-turnstile="1"]'
    );
    if (existing) {
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () => reject(new Error("script error")));
      return;
    }
    const s = document.createElement("script");
    s.src = SCRIPT_SRC;
    s.async = true;
    s.defer = true;
    s.dataset.borisTurnstile = "1";
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("script error"));
    document.head.appendChild(s);
  });
}

type Props = {
  onToken: (token: string) => void;
  resetKey?: number;
};

export default function TurnstileBox({ onToken, resetKey = 0 }: Props) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const widgetId = useRef<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [fallbackQuestion,setFallbackQuestion]=useState("");
  const [fallbackToken,setFallbackToken]=useState("");
  const [fallbackAnswer,setFallbackAnswer]=useState("");

  async function loadFallback(){
    try{
      const r=await fetch("/api/auth/captcha-fallback",{cache:"no-store"});
      const d=await r.json();
      if(r.ok&&d?.challenge_token&&d?.question){
        setFallbackQuestion(String(d.question));
        setFallbackToken(String(d.challenge_token));
      }
    }catch{}
  }

  useEffect(() => {
    let cancelled = false;
    if (!SITE_KEY) { setFailed(true); loadFallback(); return; }

    loadScript()
      .then(() => {
        if (cancelled || !boxRef.current || !window.turnstile) return;
        if (widgetId.current !== null) {
          try { window.turnstile.reset(widgetId.current); } catch {}
          return;
        }
        widgetId.current = window.turnstile.render(boxRef.current, {
          sitekey: SITE_KEY,
          callback: (token: string) => onToken(token),
          "error-callback": () => { setFailed(true); onToken(""); loadFallback(); },
          "expired-callback": () => onToken(""),
          theme: "light",
        });
      })
      .catch(() => { if (!cancelled) { setFailed(true); loadFallback(); } });

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  return (
    <div style={{marginBottom:"16px"}}>
      {!failed && SITE_KEY ? <div ref={boxRef} /> : null}
      {failed && fallbackQuestion && fallbackToken ? (
        <div style={{border:"1px solid #dfe6f2",borderRadius:"12px",padding:"12px",background:"#f8faff"}}>
          <div style={{fontSize:"12px",fontWeight:800,color:"#53617a",marginBottom:"8px"}}>Проверка BORIS · работает даже если внешний виджет недоступен</div>
          <div style={{display:"flex",gap:"8px",alignItems:"center"}}>
            <div style={{fontWeight:800,color:"#15213b",minWidth:"84px"}}>{fallbackQuestion}</div>
            <input
              inputMode="numeric"
              value={fallbackAnswer}
              onChange={(e)=>{
                const v=e.target.value.replace(/[^0-9-]/g,"").slice(0,4);
                setFallbackAnswer(v);
                onToken(v ? "boris-fallback:"+fallbackToken+":"+v : "");
              }}
              placeholder="Ответ"
              style={{height:"42px",width:"110px",border:"1px solid #dfe6f2",borderRadius:"10px",padding:"0 12px",fontSize:"16px"}}
            />
            <button type="button" onClick={()=>{setFallbackAnswer("");onToken("");loadFallback();}} style={{border:"0",background:"transparent",fontWeight:800,color:"#2878ff",cursor:"pointer"}}>Другая</button>
          </div>
        </div>
      ) : failed ? (
        <div style={{fontSize:"13px", color:"#8a5a00", marginTop:"6px"}}>
          Загружаем резервную проверку BORIS…
        </div>
      ) : null}
    </div>
  );
}
