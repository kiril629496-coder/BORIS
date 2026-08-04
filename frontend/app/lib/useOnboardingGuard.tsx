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
        background: "#F6F7FB",
        fontFamily: "var(--font-inter), Arial, Helvetica, sans-serif",
      }}
    >
      <div
        style={{
          background: "#FFFFFF",
          border: "1px solid #E3E7F0",
          borderRadius: 14,
          padding: "28px 32px",
          maxWidth: 420,
          textAlign: "center",
          color: "#14161A",
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
      <div style={{ fontSize: 15, color: "#667085" }}>Проверяю ваш профиль…</div>
    </CenterBox>
  );
}

export function OnboardingError(props: { text: string; onRetry: () => void }) {
  return (
    <CenterBox>
      <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>Не получилось проверить профиль</div>
      <div style={{ fontSize: 14, color: "#667085", marginBottom: 18 }}>{props.text}</div>
      <button
        onClick={props.onRetry}
        style={{
          background: "#2F6FED",
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

    fetchOnboardingStatus()
      .then(function (value) {
        if (!alive) return;
        setStatus(value);
        if (needsOnboarding(value) && pathname !== ROUTE_ONBOARDING) {
          router.replace(ROUTE_ONBOARDING);
          return;
        }
        setReady(true);
      })
      .catch(function (err) {
        if (!alive) return;
        if (err instanceof OnboardingUnauthorized) {
          router.replace(ROUTE_LOGIN);
          return;
        }
        setError("Сервер не ответил на проверку статуса мастера.");
      });

    return function () {
      alive = false;
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
