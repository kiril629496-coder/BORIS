/**
 * Клиент Command Center.
 *
 * Единственное место, где фронт знает про /api/command/*. И страница, и
 * будущий виджет, и PWA ходят через него — второй реализации быть не должно.
 *
 * Правило контракта: answer — текст для человека, data — структурированный
 * payload исполнителя. Интерфейс НИЧЕГО не парсит из answer.
 */

export interface CommandChange {
  entity?: string;
  field?: string;
  before?: any;
  after?: any;
  human?: string;
}

export interface CommandResult {
  command_id: string;
  status: string;
  intent?: string | null;
  operation?: string | null;
  risk_level?: string | null;
  units?: number;
  needs_confirmation?: boolean;
  human_summary?: string | null;
  clarify_question?: string | null;
  answer?: string | null;
  error_code?: string | null;
  message?: string | null;
  data?: Record<string, any> | null;
  proposed_changes?: CommandChange[] | null;
}

export interface HistoryItem {
  command_id: string;
  created_at: string | null;
  source: string;
  intent: string | null;
  operation: string | null;
  status: string;
  units: number;
  summary: string | null;
  error_code: string | null;
  account_id?: string | null;
}

export interface Capabilities {
  status: string;
  error_code?: string;
  features?: Record<string, boolean>;
  pool?: {
    purchased: number;
    used: number;
    left: number;
    active: boolean;
    paid_until: string | null;
  };
  operations?: { operation: string; title: string; risk: string; units: number }[];
}

function headers(): Record<string, string> {
  const t =
    typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return { "Content-Type": "application/json", Authorization: "Bearer " + t };
}

async function call(path: string, init?: RequestInit): Promise<any> {
  try {
    const r = await fetch(path, { ...(init || {}), headers: headers() });
    const text = await r.text();
    let body: any = {};
    try {
      body = text ? JSON.parse(text) : {};
    } catch {
      body = {};
    }
    if (!r.ok && !body.status) {
      return { status: "failed", error_code: "http_" + r.status,
               message: "Сервер ответил " + r.status };
    }
    return body;
  } catch (e: any) {
    return { status: "failed", error_code: "network",
             message: "Нет связи с сервером" };
  }
}

export interface SendOptions {
  source?: "text" | "voice";
  entityType?: string;
  entityId?: string;
  section?: string;
  route?: string;
  commandId?: string;
}

/**
 * В page_context уходит ТОЛЬКО белый список полей. Произвольное состояние
 * страницы отправлять нельзя: контекст читает модель, и всё лишнее — это
 * и утечка, и лишние токены.
 */
function pageContext(opts?: SendOptions): Record<string, string> | null {
  const ctx: Record<string, string> = {};
  if (opts?.route) ctx.route = opts.route;
  if (opts?.section) ctx.section = opts.section;
  if (typeof window !== "undefined") {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    ctx.local_date = `${y}-${m}-${d}`;
    try { ctx.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch {}
  }
  return Object.keys(ctx).length ? ctx : null;
}

export async function sendCommand(
  text: string,
  accountId: string,
  opts?: SendOptions
): Promise<CommandResult> {
  return call("/api/command/parse", {
    method: "POST",
    body: JSON.stringify({
      command_id: opts?.commandId || null,
      source: opts?.source || "text",
      text,
      transcript: opts?.source === "voice" ? text : null,
      account_id: accountId,
      page_context: pageContext(opts),
      entity_type: opts?.entityType || null,
      entity_id: opts?.entityId || null,
    }),
  });
}

export async function confirmCommand(commandId: string): Promise<CommandResult> {
  return call("/api/command/confirm", {
    method: "POST",
    body: JSON.stringify({ command_id: commandId }),
  });
}

export async function cancelCommand(commandId: string): Promise<CommandResult> {
  return call("/api/command/cancel", {
    method: "POST",
    body: JSON.stringify({ command_id: commandId }),
  });
}

export async function commandHistory(limit = 15): Promise<HistoryItem[]> {
  const r = await call("/api/command/history?limit=" + limit);
  return Array.isArray(r.commands) ? r.commands : [];
}

export async function commandCapabilities(accountId: string): Promise<Capabilities> {
  return call("/api/command/capabilities?account_id=" + encodeURIComponent(accountId));
}


export async function transcribeVoice(blob: Blob, accountId: string, durationSeconds: number) {
  const fd = new FormData();
  fd.append("account_id", accountId);
  fd.append("duration_seconds", String(durationSeconds || 0));
  fd.append("audio", blob, "voice.webm");
  const t = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  const r = await fetch("/api/command/voice/transcribe", { method: "POST", headers: { Authorization: "Bearer " + t }, body: fd });
  if (!r.ok) throw new Error("Не удалось распознать голосовое");
  return r.json();
}

export async function speakVoice(text: string, accountId: string, voice: string) {
  const t = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  const r = await fetch("/api/command/voice/speech", { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + t }, body: JSON.stringify({ account_id: accountId, text, voice }) });
  if (!r.ok) throw new Error("Не удалось озвучить ответ");
  return { blob: await r.blob(), costUsd: Number(r.headers.get("X-Boris-Cost-Usd") || 0), chars: Number(r.headers.get("X-Boris-Chars") || 0) };
}
