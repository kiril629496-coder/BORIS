/*
 * BORIS_CRM_SHARED_AUTH_V1
 *
 * Единственный frontend transport для CRM.
 * CRM использует тот же boris_token, что и основной кабинет.
 */

export async function crmApi<T = any>(
  url: string,
  options: RequestInit = {}
): Promise<T> {
  const token =
    typeof window !== "undefined"
      ? localStorage.getItem("boris_token")
      : null;

  if (!token || token === "undefined" || token === "null") {
    throw new Error("AUTH_REQUIRED");
  }

  const headers = new Headers(options.headers || {});

  headers.set("Authorization", "Bearer " + token);

  if (
    options.body &&
    !(options.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(url, {
    ...options,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });

  if (response.status === 401) {
    throw new Error("AUTH_REQUIRED");
  }

  if (response.status === 403) {
    throw new Error("ACCESS_DENIED");
  }

  if (!response.ok) {
    let detail = "";

    try {
      detail = await response.text();
    } catch {}

    throw new Error(
      `CRM_HTTP_${response.status}${detail ? ": " + detail.slice(0, 300) : ""}`
    );
  }

  return (await response.json()) as T;
}


// BORIS_CRM_HUMAN_ERROR_V1: keep technical evidence available for support,
// but never force a client to interpret raw HTTP/JSON errors.
export function crmHumanError(error: unknown, entity: "client" | "deal") {
  const raw = error instanceof Error ? error.message : String(error || "");
  const noun = entity === "client" ? "Карточка клиента" : "Сделка";

  if (raw === "AUTH_REQUIRED") {
    return {
      title: "Нужно снова войти в BORIS",
      message: "Сессия закончилась. После входа выбранный аккаунт и CRM можно открыть снова.",
      technical: raw,
    };
  }

  if (raw === "ACCESS_DENIED") {
    return {
      title: `${noun} недоступна для этого пользователя`,
      message: "BORIS не получил право открыть CRM-данные выбранного аккаунта. Это не ошибка ваших действий.",
      technical: raw,
    };
  }

  if (/CONTACT_NOT_FOUND|DEAL_NOT_FOUND|CRM_HTTP_404/.test(raw)) {
    return {
      title: `${noun} не открылась`,
      message: "BORIS не смог связать эту CRM-карточку с выбранным аккаунтом. Повторите загрузку или вернитесь в CRM — техническая причина сохранена для диагностики.",
      technical: raw,
    };
  }

  return {
    title: `Не удалось открыть: ${noun.toLowerCase()}`,
    message: "BORIS получил техническую ошибку. Повторите загрузку; если ошибка сохраняется, владелец увидит техническую причину ниже.",
    technical: raw || "CRM_UNKNOWN_ERROR",
  };
}
