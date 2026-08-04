/**
 * Единый клиент онбординга BORIS.
 *
 * Одна точка, через которую и guard-хук, и обработчики логина узнают статус
 * мастера. Результат кешируется в переменной модуля: React Context не шарится
 * между app/dashboard/layout.tsx и Shell (это разные поддеревья), а дёргать
 * /status на каждом переходе между разделами не нужно.
 *
 * Запросы идут по относительному адресу /api — так же, как login/page.tsx.
 */
export type OnboardingStatusValue =
  | "not_started"
  | "in_progress"
  | "completed"
  | "skipped_existing_user";

export interface OnboardingStatus {
  status: OnboardingStatusValue;
  current_step: number;
  form_version: number;
  required_steps: number;
  skipped_blocks: string[];
  form_data: Record<string, any>;
  completed_at: string | null;
}

export const TOKEN_KEY = "boris_token";
export const ROUTE_LOGIN = "/login";
export const ROUTE_ONBOARDING = "/onboarding";
export const ROUTE_MANAGER = "/manager";
export const ROUTE_CABINET = "/dashboard/home";
export const ROUTE_SCENARIOS = "/dashboard/scenarios";

const CACHE_TTL_MS = 60000;

let cached: { at: number; token: string; value: OnboardingStatus } | null = null;

export class OnboardingUnauthorized extends Error {}

export function getToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch (e) {
    return "";
  }
}

export function clearOnboardingCache(): void {
  cached = null;
}

export async function fetchOnboardingStatus(force?: boolean): Promise<OnboardingStatus> {
  const token = getToken();
  if (!token) throw new OnboardingUnauthorized("Нет токена");

  if (!force && cached && cached.token === token && Date.now() - cached.at < CACHE_TTL_MS) {
    return cached.value;
  }

  const res = await fetch("/api/onboarding/status", {
    headers: { Authorization: "Bearer " + token },
    cache: "no-store",
  });
  if (res.status === 401 || res.status === 403) {
    cached = null;
    throw new OnboardingUnauthorized("Сессия недействительна");
  }
  if (!res.ok) throw new Error("Не удалось получить статус мастера");

  const value = (await res.json()) as OnboardingStatus;
  cached = { at: Date.now(), token, value };
  return value;
}

/** Нужно ли этому пользователю сейчас проходить мастер. */
export function needsOnboarding(value: OnboardingStatus): boolean {
  return value.status === "not_started" || value.status === "in_progress";
}

/**
 * Куда вести сразу после успешной авторизации.
 * Вызывается обычной функцией из login, verify и лендинга — хук там не годится.
 */
export async function resolvePostAuthRoute(user?: { role?: string }): Promise<string> {
  const role = ((user && user.role) || "").toLowerCase();
  if (role === "manager") return ROUTE_MANAGER;
  try {
    const value = await fetchOnboardingStatus(true);
    return needsOnboarding(value) ? ROUTE_ONBOARDING : ROUTE_CABINET;
  } catch (e) {
    if (e instanceof OnboardingUnauthorized) return ROUTE_LOGIN;
    // статус недоступен — не запираем клиента снаружи кабинета
    return ROUTE_CABINET;
  }
}

/** Сохранить один шаг мастера. Возвращает обновлённый статус. */
export async function saveOnboardingStep(
  step: number,
  data: Record<string, any>
): Promise<OnboardingStatus> {
  const token = getToken();
  if (!token) throw new OnboardingUnauthorized("Нет токена");

  const res = await fetch("/api/onboarding/step", {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
    body: JSON.stringify({ step: step, data: data }),
  });
  if (res.status === 401 || res.status === 403) throw new OnboardingUnauthorized("Сессия недействительна");

  const body = await res.json();
  if (!res.ok) throw new OnboardingApiError(body && body.detail);

  clearOnboardingCache();
  return body as OnboardingStatus;
}

/** Завершить мастер. */
export async function completeOnboarding(): Promise<OnboardingStatus> {
  const token = getToken();
  if (!token) throw new OnboardingUnauthorized("Нет токена");

  const res = await fetch("/api/onboarding/complete", {
    method: "POST",
    headers: { Authorization: "Bearer " + token },
  });
  if (res.status === 401 || res.status === 403) throw new OnboardingUnauthorized("Сессия недействительна");

  const body = await res.json();
  if (!res.ok) throw new OnboardingApiError(body && body.detail);

  clearOnboardingCache();
  return body as OnboardingStatus;
}

/** Ошибка бэкенда с кодом и списком полей: {code, fields}. */
export class OnboardingApiError extends Error {
  code: string;
  fields: string[];
  constructor(detail: any) {
    const code = (detail && detail.code) || "onboarding_error";
    super(code);
    this.code = code;
    this.fields = (detail && detail.fields) || [];
  }
}

const FIELD_TITLES: Record<string, string> = {
  company_name: "Название компании",
  company_niche: "Ниша",
  city: "Город",
  website: "Сайт",
  phone: "Телефон",
  channels: "Каналы продаж",
  channel_links: "Ссылки на каналы",
  step: "Шаг мастера",
};

/** Человеческий текст ошибки вместо кода. */
export function describeOnboardingError(err: any): string {
  if (err instanceof OnboardingApiError) {
    const names = err.fields.map(function (f) {
      return FIELD_TITLES[String(f).split(".")[0]] || f;
    });
    const list = names.join(", ");
    switch (err.code) {
      case "onboarding_required_fields_missing":
        return "Заполните обязательные поля: " + list;
      case "onboarding_field_too_long":
        return "Слишком длинное значение: " + list;
      case "onboarding_bad_url":
        return "Проверьте ссылку: " + list;
      case "onboarding_unknown_channel":
        return "Неизвестный канал: " + list;
      case "onboarding_link_without_channel":
        return "Сначала отметьте канал, потом добавляйте ссылку: " + list;
      case "onboarding_too_many_channels":
        return "Слишком много каналов";
      case "onboarding_already_finished":
        return "Мастер уже завершён";
      case "onboarding_bad_type":
        return "Некорректное значение: " + list;
      default:
        return "Не удалось сохранить данные. Попробуйте ещё раз.";
    }
  }
  return "Не удалось связаться с сервером. Попробуйте ещё раз.";
}
