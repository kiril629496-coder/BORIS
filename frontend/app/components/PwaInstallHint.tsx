"use client";

import { useEffect, useMemo, useState } from "react";

type InstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
};

declare global {
  interface Navigator { standalone?: boolean; }
}

const DISMISS_KEY = "boris_pwa_hint_dismissed_at";
const WEEK = 7 * 24 * 60 * 60 * 1000;

function isStandalone() {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
}

export default function PwaInstallHint() {
  const [visible, setVisible] = useState(false);
  const [promptEvent, setPromptEvent] = useState<InstallPromptEvent | null>(null);
  const [iosHelp, setIosHelp] = useState(false);
  const isIos = useMemo(() => typeof navigator !== "undefined" && /iphone|ipad|ipod/i.test(navigator.userAgent), []);

  useEffect(() => {
    if (isStandalone()) return;
    const mobile = window.matchMedia("(max-width: 900px)").matches;
    if (!mobile) return;
    const appPath = /^(\/dashboard|\/messages|\/command|\/memory|\/ai|\/rop|\/social|\/support|\/team|\/reactivation)/.test(window.location.pathname);
    if (!appPath) return;
    const dismissed = Number(localStorage.getItem(DISMISS_KEY) || 0);
    if (dismissed && Date.now() - dismissed < WEEK) return;

    const onPrompt = (event: Event) => {
      event.preventDefault();
      setPromptEvent(event as InstallPromptEvent);
      setVisible(true);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    const timer = window.setTimeout(() => setVisible(true), 5000);
    return () => { window.removeEventListener("beforeinstallprompt", onPrompt); window.clearTimeout(timer); };
  }, []);

  if (!visible || isStandalone()) return null;

  const close = () => {
    try { localStorage.setItem(DISMISS_KEY, String(Date.now())); } catch {}
    setVisible(false);
    setIosHelp(false);
  };

  const install = async () => {
    if (promptEvent) {
      await promptEvent.prompt();
      const choice = await promptEvent.userChoice.catch(() => null);
      if (choice?.outcome === "accepted") setVisible(false);
      return;
    }
    if (isIos) { setIosHelp(true); return; }
    setIosHelp(true);
  };

  return (
    <aside className="boris-pwa-install" aria-label="Установить BORIS на телефон">
      <button className="boris-pwa-install-close" type="button" onClick={close} aria-label="Закрыть">×</button>
      <div className="boris-pwa-install-mark" aria-hidden="true">B</div>
      <div className="boris-pwa-install-copy">
        <strong>BORIS на экране телефона</strong>
        {iosHelp ? <span>Нажмите «Поделиться» в Safari → «На экран Домой» → «Добавить».</span> : <span>Открывайте BORIS как приложение — отдельной иконкой, без поиска сайта.</span>}
      </div>
      {!iosHelp ? <button className="boris-pwa-install-action" type="button" onClick={install}>Добавить</button> : null}
    </aside>
  );
}
