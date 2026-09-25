"use client";

import { usePathname, useRouter } from "next/navigation";

const NO_BACK = new Set([
  "/",
  "/login",
  "/dashboard",
  "/dashboard/home",
]);

type BackMeta = {
  label: string;
  fallback: string;
};

function backMeta(pathname: string): BackMeta {
  if (pathname.startsWith("/dashboard/campaign/")) {
    return { label: "К объявлениям", fallback: "/dashboard/campaign" };
  }
  if (pathname.startsWith("/messages/")) {
    return { label: "К сообщениям", fallback: "/messages" };
  }
  if (pathname.startsWith("/reactivation")) {
    return { label: "К сообщениям", fallback: "/messages" };
  }
  if (pathname.startsWith("/dashboard/crm/")) {
    return { label: "К клиентам", fallback: "/dashboard/crm" };
  }
  if (pathname.startsWith("/dashboard/scenarios/")) {
    return { label: "К сценариям", fallback: "/dashboard/scenarios" };
  }
  if (pathname.startsWith("/dashboard/")) {
    return { label: "На главный экран", fallback: "/dashboard/command-center" };
  }
  return { label: "Назад", fallback: "/dashboard/command-center" };
}

export default function GlobalBackButton() {
  const router = useRouter();
  const pathname = usePathname();

  if (!pathname || NO_BACK.has(pathname)) {
    return null;
  }

  const meta = backMeta(pathname);
  const compactMain = pathname === "/dashboard/command-center";

  function goBack() {
    /*
     * BORIS_GLOBAL_BACK_V2
     *
     * 1) Если есть реальная история внутри приложения — возвращаемся по ней.
     * 2) При прямом открытии вложенного URL ведём в родительский рабочий раздел.
     * Это не оставляет пользователя в тупике после ссылки из уведомления/Telegram.
     */
    if (typeof window !== "undefined") {
      const ref = document.referrer;
      const refState = ref ? (() => {
        try {
          const u = new URL(ref);
          const current = window.location.pathname + window.location.search;
          const previous = u.pathname + u.search;
          return {
            sameOrigin: u.origin === window.location.origin,
            differentScreen: previous !== current,
          };
        } catch {
          return { sameOrigin: false, differentScreen: false };
        }
      })() : { sameOrigin: false, differentScreen: false };

      if (refState.sameOrigin && refState.differentScreen && window.history.length > 1) {
        router.back();
        return;
      }
    }

    router.push(meta.fallback);
  }

  return (
    <button
      type="button"
      onClick={goBack}
      aria-label={meta.label}
      title={meta.label}
      data-testid="nav-back"
      className={compactMain ? "boris-global-back boris-global-back-compact" : "boris-global-back"}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: compactMain ? 0 : 8,
        width: compactMain ? 44 : "fit-content",
        minWidth: compactMain ? 44 : "auto",
        maxWidth: "max-content",
        minHeight: 44,
        padding: compactMain ? 0 : "9px 13px",
        marginBottom: compactMain ? 10 : 14,
        border: "1px solid #e1e8f3",
        borderRadius: 11,
        background: "#ffffff",
        color: "#334155",
        fontSize: 13.5,
        fontWeight: 700,
        cursor: "pointer",
        boxShadow: "0 4px 12px rgba(31,58,103,.05)",
        alignSelf: "flex-start",
      }}
    >
      <span aria-hidden="true" style={{ fontSize: 18, lineHeight: 1 }}>←</span>
      {!compactMain ? <span>{meta.label}</span> : null}
    </button>
  );
}
