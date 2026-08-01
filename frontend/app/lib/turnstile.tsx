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

  useEffect(() => {
    let cancelled = false;
    if (!SITE_KEY) { setFailed(true); return; }

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
          "error-callback": () => { setFailed(true); onToken(""); },
          "expired-callback": () => onToken(""),
          theme: "light",
        });
      })
      .catch(() => { if (!cancelled) setFailed(true); });

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  if (!SITE_KEY) {
    return (
      <div style={{fontSize:"13px", color:"#B42318", marginBottom:"12px"}}>
        Проверка «я не робот» не настроена. Регистрация временно недоступна.
      </div>
    );
  }

  return (
    <div style={{marginBottom:"16px"}}>
      <div ref={boxRef} />
      {failed && (
        <div style={{fontSize:"13px", color:"#B42318", marginTop:"6px"}}>
          Не удалось загрузить проверку. Обновите страницу.
        </div>
      )}
    </div>
  );
}
