"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import Script from "next/script";

const COUNTER_ID = 111297252;

// Счётчик работает ТОЛЬКО на публичных страницах.
// В кабинете клиента Метрики нет — Вебвизор не должен писать данные клиентов.
const PUBLIC_EXACT = ["/", "/login", "/verify", "/privacy", "/oferta"];
const PUBLIC_PREFIX = ["/r"];

export default function Metrika() {
  const pathname = usePathname() || "/";
  const isPublic =
    PUBLIC_EXACT.includes(pathname) ||
    PUBLIC_PREFIX.some((p) => pathname === p || pathname.startsWith(p + "/"));

  const firstRender = useRef(true);

  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    if (!isPublic) return;
    const ym = (window as unknown as { ym?: (...args: unknown[]) => void }).ym;
    if (typeof ym === "function") {
      ym(COUNTER_ID, "hit", window.location.href, {
        referer: document.referrer,
      });
    }
  }, [pathname, isPublic]);

  if (!isPublic) return null;

  return (
    <>
      <Script id="yandex-metrika" strategy="afterInteractive">
        {`
(function(m,e,t,r,i,k,a){
    m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
    m[i].l=1*new Date();
    for (var j = 0; j < document.scripts.length; j++) {if (document.scripts[j].src === r) { return; }}
    k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)
})(window, document,'script','https://mc.yandex.ru/metrika/tag.js?id=${COUNTER_ID}', 'ym');

ym(${COUNTER_ID}, 'init', {ssr:true, webvisor:true, clickmap:true, referrer: document.referrer, url: location.href, accurateTrackBounce:true, trackLinks:true});
        `}
      </Script>
      <noscript>
        <div>
          <img
            src={`https://mc.yandex.ru/watch/${COUNTER_ID}`}
            style={{ position: "absolute", left: "-9999px" }}
            alt=""
          />
        </div>
      </noscript>
    </>
  );
}
