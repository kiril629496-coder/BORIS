"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import Script from "next/script";

const COUNTER_ID = 111297252;
const OWNER_OPTOUT_KEY = "boris_metrika_owner_optout_v1";

// Счётчик работает ТОЛЬКО на публичных страницах.
// В кабинете клиента Метрики нет — Вебвизор не должен писать данные клиентов.
const PUBLIC_EXACT = ["/", "/login", "/verify", "/privacy", "/oferta", "/audit"];
const PUBLIC_PREFIX = ["/r"];

function isOwnerOptedOut() {
  try {
    return (
      window.localStorage.getItem(OWNER_OPTOUT_KEY) === "1" ||
      (window.localStorage.getItem("boris_user_role") || "").toLowerCase() === "owner"
    );
  } catch {
    return false;
  }
}

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
    if (!isPublic || isOwnerOptedOut()) return;
    const ym = (window as unknown as { ym?: (...args: unknown[]) => void }).ym;
    if (typeof ym === "function") {
      ym(COUNTER_ID, "hit", window.location.href, {
        referer: document.referrer,
      });
    }
  }, [pathname, isPublic]);

  useEffect(() => {
    if (!isPublic || isOwnerOptedOut()) return;

    const sendGoal = (goal: string, params: Record<string, unknown> = {}) => {
      const ym = (window as unknown as { ym?: (...args: unknown[]) => void }).ym;
      if (typeof ym !== "function") return;
      ym(COUNTER_ID, "params", {
        retargeting: {
          audience: "boris_public",
          page: pathname,
          [goal]: true,
        },
      });
      ym(COUNTER_ID, "reachGoal", goal, {
        audience: "boris_public",
        page: pathname,
        ...params,
      });
    };

    const pageTimer = window.setTimeout(
      () => sendGoal("retarget_boris_page_view"),
      900,
    );
    const engagedTimer = window.setTimeout(
      () => sendGoal("retarget_boris_engaged_30s"),
      30000,
    );

    let sentScroll50 = false;
    let sentScroll75 = false;

    const onScroll = () => {
      const doc = document.documentElement;
      const max = Math.max(1, doc.scrollHeight - window.innerHeight);
      const percent = Math.round((window.scrollY / max) * 100);
      if (percent >= 50 && !sentScroll50) {
        sentScroll50 = true;
        sendGoal("retarget_boris_scroll_50");
      }
      if (percent >= 75 && !sentScroll75) {
        sentScroll75 = true;
        sendGoal("retarget_boris_scroll_75");
      }
    };

    const onClick = (event: MouseEvent) => {
      const target = event.target as Element | null;
      const el = target?.closest("a,button");
      if (!el) return;

      const href = el instanceof HTMLAnchorElement ? el.getAttribute("href") || "" : "";
      const label = (el.textContent || el.getAttribute("aria-label") || "")
        .trim()
        .slice(0, 120);
      const haystack = `${href} ${label}`;

      if (/wa\.me|whatsapp/i.test(haystack)) {
        sendGoal("retarget_boris_contact", { channel: "whatsapp", label });
        return;
      }
      if (/max\.ru|\bMAX\b/i.test(haystack)) {
        sendGoal("retarget_boris_contact", { channel: "max", label });
        return;
      }
      if (/^tel:/i.test(href)) {
        sendGoal("retarget_boris_contact", { channel: "phone", label });
        return;
      }

      if (
        /^\/(login|onboarding)/.test(href) ||
        /начать|попробовать|демо|консультац|подключ|регистрац|заявк|тариф|купить|получить|рассчитать/i.test(
          label,
        )
      ) {
        sendGoal("retarget_boris_cta_interest", { target: href || "button", label });
      }
    };

    const onSubmit = (event: SubmitEvent) => {
      const form = event.target as HTMLFormElement | null;
      sendGoal("retarget_boris_lead_submit", {
        form: form?.id || form?.getAttribute("name") || "form",
      });
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("click", onClick, true);
    document.addEventListener("submit", onSubmit, true);

    return () => {
      window.clearTimeout(pageTimer);
      window.clearTimeout(engagedTimer);
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("submit", onSubmit, true);
    };
  }, [pathname, isPublic]);

  if (!isPublic) return null;

  return (
    <>
      <Script id="yandex-metrika" strategy="afterInteractive">
        {`
(function(){
    var ownerKey='${OWNER_OPTOUT_KEY}';
    try {
        var url=new URL(window.location.href);
        var ownerMode=url.searchParams.get('owner_metrika');
        if(ownerMode==='off'){
            localStorage.setItem(ownerKey,'1');
            url.searchParams.delete('owner_metrika');
            history.replaceState(null,'',url.pathname+url.search+url.hash);
        } else if(ownerMode==='on'){
            localStorage.removeItem(ownerKey);
            url.searchParams.delete('owner_metrika');
            history.replaceState(null,'',url.pathname+url.search+url.hash);
        }
        if(localStorage.getItem(ownerKey)==='1' || (localStorage.getItem('boris_user_role') || '').toLowerCase()==='owner') return;
    } catch(e) {}

    (function(m,e,t,r,i,k,a){
        m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
        m[i].l=1*new Date();
        for (var j = 0; j < document.scripts.length; j++) {if (document.scripts[j].src === r) { return; }}
        k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)
    })(window, document,'script','https://mc.yandex.ru/metrika/tag.js?id=${COUNTER_ID}', 'ym');

    ym(${COUNTER_ID}, 'init', {ssr:true, webvisor:(location.pathname!='/audit'), clickmap:true, referrer: document.referrer, url: location.href, accurateTrackBounce:true, trackLinks:true});
})();
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
