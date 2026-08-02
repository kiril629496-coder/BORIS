// Общий слой для НОВЫХ страниц (/dashboard/home, /dashboard/scenarios).
// Монолит app/dashboard/page.tsx не трогаем - у него свой apiFetch.
// Ключи localStorage те же, поэтому выбор аккаунта сквозной.

export const LS_TOKEN = "boris_token";
export const LS_ACCOUNT = "boris_currentAccount";
export const LS_ROLE = "boris_user_role";
export const LS_TAB = "boris_activeTab";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(LS_TOKEN);
}

/* ПЕРВОПРИЧИНА account_id=null: у владельца поле account_id в базе NULL.
   При входе оно уходит в localStorage, где JS превращает null в СТРОКУ "null" —
   и она проходит любую проверку на непустоту. Отсюда запрос
   /api/home/overview?account_id=null при первом входе.
   Чиним на обоих концах: не записываем мусор и не читаем его. */
function _clean(v: any): string {
  const s = String(v == null ? "" : v).trim();
  if (!s || s === "null" || s === "undefined" || s === "NaN") return "";
  return s;
}

export function getAccount(): string {
  if (typeof window === "undefined") return "";
  const v = _clean(localStorage.getItem(LS_ACCOUNT));
  if (!v && localStorage.getItem(LS_ACCOUNT)) {
    localStorage.removeItem(LS_ACCOUNT);   // подчищаем мусор от прошлых версий
  }
  return v;
}

export function setAccount(id: string) {
  if (typeof window === "undefined") return;
  const v = _clean(id);
  if (!v) { localStorage.removeItem(LS_ACCOUNT); return; }
  localStorage.setItem(LS_ACCOUNT, v);
}

export function getRole(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(LS_ROLE) || "";
}

export function logout() {
  if (typeof window === "undefined") return;
  [LS_TOKEN, "boris_user_email", LS_ROLE, LS_ACCOUNT].forEach((k) =>
    localStorage.removeItem(k)
  );
  window.location.href = "/login";
}

/* Ключ публичного verification_id - тот же, что VERIFY_ID_KEY в app/lib/register.ts.
   Литералом, а не импортом: register.ts импортирует из api.ts, обратный импорт даст цикл. */
const VERIFY_ID_KEY = "boris_verification_id";
export const LS_LOGIN_NOTE = "boris_login_note";

/* Приводит любое тело ошибки к единой форме. В JSX уходит только message;
   сырой объект живёт в raw и никогда не рендерится. */
function _normErr(httpStatus: number, data: any) {
  const d = data && data.detail;
  const code = d && typeof d === "object" && !Array.isArray(d) ? d.code : undefined;
  const message =
    (d && typeof d === "object" && typeof d.message === "string" && d.message) ||
    (typeof d === "string" && d) ||
    (data && typeof data.message === "string" && data.message) ||
    ("Ошибка " + httpStatus);
  return { status: "error", httpStatus, code, message, raw: d };
}

/* Неподтверждённая почта: уводим на экран ввода кода.
   На самих /verify и /login редирект не делаем - иначе зациклимся. */
function _gotoVerify() {
  if (typeof window === "undefined") return;
  const p = window.location.pathname;
  if (p.startsWith("/verify") || p.startsWith("/login")) return;
  if (localStorage.getItem(VERIFY_ID_KEY)) { window.location.href = "/verify"; return; }
  localStorage.setItem(LS_LOGIN_NOTE,
    "Войдите снова, чтобы получить новый код подтверждения");
  logout();
}

export const LS_PENDING = "boris_pendingNavigation";

/** Режимы старого кабинета, которые открываются не вкладкой, а флагом. */
export type OldMode = "director" | "advisor" | "wallet" | "requisites" | "analytics";

/* ЕДИНЫЙ МОСТ между новым интерфейсом и монолитом.
   Пишем цель в boris_pendingNavigation, монолит читает её при монтировании,
   открывает вкладку, включает режим и чистит ключ. Ложных ссылок нет:
   пользователь попадает именно туда, куда нажал. */
export function navigateToOld(tab: string, mode: OldMode | null = null) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(LS_PENDING, JSON.stringify({
      tab, mode: mode || null, accountId: getAccount(),
      timestamp: Date.now(),
    }));
  } catch {}
  localStorage.setItem(LS_TAB, tab);   // запасной путь, если мост ещё не подключён
  window.location.href = "/dashboard";
}

// Штатный переход в старый кабинет: монолит сам поднимает вкладку из
// localStorage при монтировании (page.tsx:1309). Query-параметр не нужен.
export function openTab(tab: string) {
  navigateToOld(tab, null);
}

export function openRoute(route: string) {
  if (typeof window === "undefined") return;
  window.location.href = route;
}

export async function apiFetch(url: string, init: any = {}): Promise<Response> {
  // та же защита, что в монолите: пустой account_id не отправляем
  // Последний рубеж: ни пустого, ни "null"/"undefined" в запрос не выпускаем.
  if (/account_id=(&|$)/.test(url) || /account_id=(null|undefined|NaN)(&|$)/.test(url)) {
    return new Response(JSON.stringify({ status: "skip" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  const token = getToken();
  const headers = new Headers(init.headers || {});
  if (token) headers.set("Authorization", "Bearer " + token);
  const resp = await fetch(url, { ...init, headers });
  if (resp.status === 401 && typeof window !== "undefined") {
    logout();
  }
  return resp;
}

export async function apiGet(url: string): Promise<any> {
  const r = await apiFetch(url);
  try {
    const data = await r.json();
    if (!r.ok) {
      const e = _normErr(r.status, data);
      if (e.code === "email_not_verified") _gotoVerify();
      return e;
    }
    return data;
  } catch {
    return { status: "error", message: "Некорректный ответ сервера" };
  }
}

export async function apiPost(url: string, body: any): Promise<any> {
  const r = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  try {
    const data = await r.json();
    if (!r.ok) {
      const e = _normErr(r.status, data);
      if (e.code === "email_not_verified") _gotoVerify();
      return e;
    }
    return data;
  } catch {
    return { status: "error", message: "Некорректный ответ сервера" };
  }
}
