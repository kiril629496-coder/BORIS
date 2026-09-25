/** Local recovery copy, never a substitute for a successful server save. */
function key(scope: string): string | null {
  if (typeof window === "undefined" || !scope) return null;
  const user = window.localStorage.getItem("boris_user_email");
  return user ? `boris:campaign-draft:v1:${encodeURIComponent(user)}:${scope}` : null;
}

export function readCampaignDraft<T>(scope: string): T | undefined {
  try {
    const k = key(scope);
    if (!k) return undefined;
    const raw = window.localStorage.getItem(k);
    if (!raw) return undefined;
    const record = JSON.parse(raw);
    return record.version === 1 ? record.value as T : undefined;
  } catch { return undefined; }
}

export function writeCampaignDraft(scope: string, value: unknown): boolean {
  try {
    const k = key(scope);
    if (!k) return false;
    window.localStorage.setItem(k, JSON.stringify({ version: 1, value, savedAt: Date.now() }));
    return true;
  } catch { return false; }
}

export function clearCampaignDraft(scope: string): void {
  try { const k = key(scope); if (k) window.localStorage.removeItem(k); } catch {}
}
