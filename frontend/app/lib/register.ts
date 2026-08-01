// Единый источник логики регистрации и подтверждения email.
// Используется формой лендинга (app/page.tsx), формой /login и экраном /verify.

export type RegisterInput = {
  email: string;
  password: string;
  accountName?: string;
  plannedAccounts?: string;
  turnstileToken?: string;
};

export type RegisterResult =
  | { ok: true; data: any }
  | { ok: false; error: string; code?: string; retryAfter?: number };

// Экран /verify восстанавливается по этим ключам после перезагрузки.
// САМ КОД НЕ ХРАНИТСЯ НИГДЕ — только публичный verification_id и маска адреса.
export const VERIFY_ID_KEY = "boris_verification_id";
export const VERIFY_MASK_KEY = "boris_verification_email";

/** Читает cookie boris_ref В МОМЕНТ ВЫЗОВА, а не при монтировании страницы. */
export function readRefCookie(): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/(?:^|; )boris_ref=([^;]*)/);
  return m ? decodeURIComponent(m[1]) : "";
}

/** Человеческие тексты для машинных кодов backend. */
export function errorText(code: string, fallback: string, retryAfter?: number): string {
  switch (code) {
    case "captcha_required":
      return "Подтвердите, что вы не робот.";
    case "captcha_invalid":
      return "Проверка не пройдена. Попробуйте ещё раз.";
    case "captcha_unavailable":
      return "Регистрация временно недоступна. Попробуйте позже.";
    case "rate_limited":
      return "Слишком много попыток. Повторите позже.";
    case "cooldown":
      return retryAfter
        ? "Новый код можно запросить через " + retryAfter + " с"
        : "Новый код можно запросить чуть позже.";
    case "code_invalid":
      return "Неверный код.";
    case "code_expired":
      return "Срок действия кода истёк. Запросите новый.";
    case "code_used":
      return "Этот код уже использован. Запросите новый.";
    case "code_locked":
      return "Слишком много попыток. Запросите новый код.";
    case "already_verified":
      return "Почта уже подтверждена.";
    case "bad_email":
      return "Некорректный адрес.";
    default:
      return fallback;
  }
}

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (typeof localStorage !== "undefined") {
    const t = localStorage.getItem("boris_token");
    if (t && t !== "undefined" && t !== "null") h["Authorization"] = "Bearer " + t;
  }
  return h;
}

async function post(url: string, body: any, withAuth: boolean): Promise<RegisterResult> {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: withAuth ? authHeaders() : { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    const code = data.code || (data.detail && data.detail.code) || "";
    if (!res.ok || data.status === "error") {
      const fallback =
        data.message ||
        (data.detail && data.detail.message) ||
        (typeof data.detail === "string" ? data.detail : "") ||
        "Не удалось выполнить запрос";
      return { ok: false, error: errorText(code, fallback, data.retry_after), code,
               retryAfter: data.retry_after };
    }
    return { ok: true, data };
  } catch {
    return { ok: false, error: "Не удалось связаться с сервером" };
  }
}

export async function registerUser(input: RegisterInput): Promise<RegisterResult> {
  return post("/api/auth/register", {
    email: input.email,
    password: input.password,
    account_name: input.accountName || "",
    planned_accounts: input.plannedAccounts || "",
    ref: readRefCookie(),
    turnstile_token: input.turnstileToken || "",
  }, false);
}

export async function verifyEmail(verificationId: string, code: string): Promise<RegisterResult> {
  return post("/api/auth/verify-email", {
    verification_id: verificationId,
    code: code,
  }, false);
}

export async function resendCode(turnstileToken: string): Promise<RegisterResult> {
  return post("/api/auth/resend-verification", { turnstile_token: turnstileToken }, true);
}

export async function changeVerificationEmail(email: string, turnstileToken: string): Promise<RegisterResult> {
  return post("/api/auth/change-verification-email",
              { email: email, turnstile_token: turnstileToken }, true);
}

/** Запоминает, какое подтверждение сейчас идёт, чтобы экран пережил перезагрузку. */
export function rememberVerification(data: any) {
  if (typeof localStorage === "undefined") return;
  if (data && data.verification_id) localStorage.setItem(VERIFY_ID_KEY, data.verification_id);
  if (data && data.email_masked) localStorage.setItem(VERIFY_MASK_KEY, data.email_masked);
}

export function forgetVerification() {
  if (typeof localStorage === "undefined") return;
  localStorage.removeItem(VERIFY_ID_KEY);
  localStorage.removeItem(VERIFY_MASK_KEY);
}

/** Единый набор ключей сессии — одинаковый для всех точек входа.
 *  ЗАЩИТА: ветка «email уже занят» приходит БЕЗ access_token, и раньше в
 *  localStorage ложилась строка "undefined" — та же болезнь, что была с account_id=null. */
export function persistSession(data: any, justRegistered: boolean) {
  if (!data || !data.access_token) return false;
  localStorage.setItem("boris_token", data.access_token);
  if (data.user) {
    if (data.user.email) localStorage.setItem("boris_user_email", data.user.email);
    if (data.user.role) localStorage.setItem("boris_user_role", data.user.role);
    if (data.user.account_id) localStorage.setItem("boris_currentAccount", data.user.account_id);
  }
  if (justRegistered) localStorage.setItem("boris_just_registered", "1");
  return true;
}
