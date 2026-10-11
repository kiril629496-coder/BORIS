export interface AccountIdentity {
  account_id: string;
}

function clean(value: string | null | undefined): string {
  const normalized = String(value || "").trim();
  return normalized === "null" || normalized === "undefined" ? "" : normalized;
}

export function resolveAccountSelection(
  accounts: AccountIdentity[],
  linkedAccountId: string | null | undefined,
  currentAccountId: string | null | undefined,
  savedAccountId: string | null | undefined,
): string | null {
  const allowed = new Set(
    accounts.map((account) => clean(account && account.account_id)).filter(Boolean),
  );
  const candidates = [linkedAccountId, currentAccountId, savedAccountId];
  for (const candidate of candidates) {
    const id = clean(candidate);
    if (id && allowed.has(id)) return id;
  }
  return accounts.length ? clean(accounts[0].account_id) || null : null;
}
