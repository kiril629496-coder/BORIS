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
    return await r.json();
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
    return await r.json();
  } catch {
    return { status: "error", message: "Некорректный ответ сервера" };
  }
}
