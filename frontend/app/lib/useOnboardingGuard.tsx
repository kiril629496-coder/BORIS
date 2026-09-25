"use client";

/**
 * Guard мастера первого запуска.
 *
 * Режим "protect" — для защищённых разделов: пока идёт проверка, детей не
 * рендерим и отдаём fallback, чтобы кабинет не мигал. Режим
 * "redirect-after-auth" — без overlay, только увод.
 *
 * В login, verify и на лендинге хук НЕ используется: там обычная функция
 * resolvePostAuthRoute из onboardingClient.
 */
import React from "react";
import { useRouter, usePathname } from "next/navigation";
import {
  fetchOnboardingStatus,
  needsOnboarding,
  OnboardingStatus,
  OnboardingUnauthorized,
  ROUTE_LOGIN,
  ROUTE_ONBOARDING,
} from "./onboardingClient";

export type GuardMode = "protect" | "redirect-after-auth";

export interface GuardResult {
  ready: boolean;
  status: OnboardingStatus | null;
  error: string;
  retry: () => void;
  fallback: React.ReactElement | null;
}

function CenterBox(props: { children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--boris-bg)",
        fontFamily: "var(--font-inter), Arial, Helvetica, sans-serif",
      }}
    >
      <div
        style={{
          background: "#FFFFFF",
          border: "1px solid var(--boris-border)",
          borderRadius: 14,
          padding: "28px 32px",
          maxWidth: 420,
          textAlign: "center",
          color: "var(--boris-text)",
        }}
      >
        {props.children}
      </div>
    </div>
  );
}

export function OnboardingLoading() {
  return (
    <CenterBox>
      <div style={{ fontSize: 15, color: "var(--boris-text-2)" }}>Проверяю ваш профиль…</div>
    </CenterBox>
  );
}

export function OnboardingError(props: { text: string; onRetry: () => void }) {
  return (
    <CenterBox>
      <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>Не получилось проверить профиль</div>
      <div style={{ fontSize: 14, color: "var(--boris-text-2)", marginBottom: 18 }}>{props.text}</div>
      <button
        onClick={props.onRetry}
        style={{
          background: "var(--boris-blue)",
          color: "#FFFFFF",
          border: "none",
          borderRadius: 10,
          padding: "10px 22px",
          fontWeight: 700,
          cursor: "pointer",
        }}
      >
        Попробовать снова
      </button>
    </CenterBox>
  );
}

export function useOnboardingGuard(options?: { mode?: GuardMode }): GuardResult {
  const mode: GuardMode = (options && options.mode) || "protect";
  const router = useRouter();
  const pathname = usePathname();

  const [status, setStatus] = React.useState<OnboardingStatus | null>(null);
  const [error, setError] = React.useState("");
  const [ready, setReady] = React.useState(false);
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    let alive = true;
    setError("");
    setReady(false);

    // Never let a slow profile check freeze the whole cabinet indefinitely.
    // Onboarding is an entry-flow concern; an already authenticated user must
    // keep access to dashboard even if this auxiliary check is temporarily slow.
    const failOpenTimer = window.setTimeout(function () {
      if (!alive) return;
      setReady(true);
    }, 2500);

    fetchOnboardingStatus()
      .then(function (value) {
        window.clearTimeout(failOpenTimer);
        if (!alive) return;
        setStatus(value);
        if (needsOnboarding(value) && pathname !== ROUTE_ONBOARDING) {
          router.replace(ROUTE_ONBOARDING);
          return;
        }
        setReady(true);
      })
      .catch(function (err) {
        window.clearTimeout(failOpenTimer);
        if (!alive) return;
        if (err instanceof OnboardingUnauthorized) {
          router.replace(ROUTE_LOGIN);
          return;
        }
        // A transient onboarding-status failure must not lock the entire product.
        setError("Сервер не ответил на проверку статуса мастера.");
        setReady(true);
      });

    return function () {
      alive = false;
      window.clearTimeout(failOpenTimer);
    };
  }, [attempt, pathname, router]);

  const retry = React.useCallback(function () {
    setAttempt(function (n) {
      return n + 1;
    });
  }, []);

  let fallback: React.ReactElement | null = null;
  if (mode === "protect" && !ready) {
    fallback = error ? <OnboardingError text={error} onRetry={retry} /> : <OnboardingLoading />;
  }

  return { ready: ready, status: status, error: error, retry: retry, fallback: fallback };
}

/** Обёртка для layout и Shell: детей показывает только после проверки. */
export default function OnboardingGate(props: { children: React.ReactNode }) {
  const gate = useOnboardingGuard({ mode: "protect" });
  if (!gate.ready) return gate.fallback;
  return <>{props.children}</>;
}
