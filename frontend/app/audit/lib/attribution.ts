export type AuditAttribution = {
  session_id: string | null;
  referral_code: string | null;
  utm_source: string | null;
  utm_medium: string | null;
  utm_campaign: string | null;
  utm_content: string | null;
  utm_term: string | null;
  landing_path: string | null;
  first_seen_at: string | null;
  qa: boolean;
};

type StoredTouch = Record<string, unknown>;

function parseStored(key: string): StoredTouch {
  if (typeof window === "undefined") return {};
  try {
    const value = JSON.parse(localStorage.getItem(key) || "{}");
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

export function auditSessionId(): string {
  if (typeof window === "undefined") return "";
  try {
    let id = localStorage.getItem("boris_audit_session_id") || "";
    if (!id) {
      id =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : "audit_" + Date.now() + "_" + Math.random().toString(36).slice(2);
      localStorage.setItem("boris_audit_session_id", id);
    }
    return id;
  } catch {
    return "";
  }
}

function refCookie(): string {
  if (typeof document === "undefined") return "";
  try {
    const match = document.cookie.match(/(?:^|; )boris_ref=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  } catch {
    return "";
  }
}

export function captureAuditAttribution(): AuditAttribution {
  if (typeof window === "undefined") return auditAttribution();

  try {
    const query = new URLSearchParams(window.location.search);
    const utm: Record<string, string> = {};
    for (const key of ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]) {
      const value = query.get(key);
      if (value) utm[key] = value.slice(0, 180);
    }
    const explicitQa = query.get("qa") === "1";
    const inferredQa = /^qa(?:_|-|$)/i.test(utm.utm_content || "");
    const now = new Date().toISOString();
    const payload = {
      ...utm,
      landing_path: window.location.pathname || "/audit",
      first_seen_at: now,
      captured_at: now,
      qa: explicitQa || inferredQa,
    };
    if (Object.keys(utm).length) {
      if (!localStorage.getItem("boris_first_utm")) {
        localStorage.setItem("boris_first_utm", JSON.stringify(payload));
      }
      localStorage.setItem("boris_last_utm", JSON.stringify(payload));
      if (String(utm.utm_source || "").toLowerCase() === "email") {
        localStorage.setItem("boris_email_utm", JSON.stringify(payload));
      }
    }
    if (payload.qa) localStorage.setItem("boris_audit_qa", "1");
  } catch {}

  return auditAttribution();
}

export function auditAttribution(): AuditAttribution {
  const first = parseStored("boris_first_utm");
  const last = parseStored("boris_last_utm");
  const touch = Object.keys(first).length ? first : last;
  const qaStored =
    typeof window !== "undefined" && localStorage.getItem("boris_audit_qa") === "1";

  return {
    session_id: auditSessionId() || null,
    referral_code: refCookie() || null,
    utm_source: String(touch.utm_source || "") || null,
    utm_medium: String(touch.utm_medium || "") || null,
    utm_campaign: String(touch.utm_campaign || "") || null,
    utm_content: String(touch.utm_content || "") || null,
    utm_term: String(touch.utm_term || "") || null,
    landing_path: String(touch.landing_path || "/audit") || "/audit",
    first_seen_at: String(touch.first_seen_at || touch.captured_at || "") || null,
    qa: Boolean(touch.qa) || qaStored,
  };
}

export function auditEventHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("boris_token") || "";
    if (token && token !== "undefined" && token !== "null") {
      headers.Authorization = "Bearer " + token;
    }
  }
  return headers;
}

export function fireAuditEvent(
  event: string,
  source: string,
  extra: Record<string, unknown> = {},
): void {
  if (typeof window === "undefined") return;
  fetch("/api/viral/events", {
    method: "POST",
    headers: auditEventHeaders(),
    body: JSON.stringify({ event, source, ...auditAttribution(), ...extra }),
    keepalive: true,
  }).catch(() => {});
}

export function emailGoal(goal: string): void {
  const emailTouch = parseStored("boris_email_utm");
  const capturedAt = Date.parse(String(emailTouch.captured_at || ""));
  const recent =
    String(emailTouch.utm_source || "").toLowerCase() === "email" &&
    Number.isFinite(capturedAt) &&
    Date.now() - capturedAt <= 7 * 24 * 60 * 60 * 1000;
  if (!recent) return;
  try {
    const ym = (window as unknown as { ym?: (...args: unknown[]) => void }).ym;
    if (typeof ym === "function") {
      ym(111297252, "reachGoal", goal, {
        utm_campaign: String(emailTouch.utm_campaign || "") || undefined,
        utm_content: String(emailTouch.utm_content || "") || undefined,
        utm_term: String(emailTouch.utm_term || "") || undefined,
      });
    }
  } catch {}
}
