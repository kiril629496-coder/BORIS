"use client";

/**
 * Общий контекст текущего аккаунта BORIS.
 *
 * Список аккаунтов приходит с бэкенда (/api/accounts/list уже отдаёт все
 * аккаунты владельца), выбор хранится в localStorage под тем же ключом, что
 * использовался раньше, — чтобы старые экраны не потеряли своё значение.
 *
 * Режим "all" — состояние ИНТЕРФЕЙСА для агрегированных разделов
 * (сообщения, РОП, агентский). В backend он не уходит: фиктивного
 * account_id=all не существует, такие разделы и так собирают все аккаунты.
 */
import React from "react";
import { resolveAccountSelection } from "./accountSelection";

export const LS_ACCOUNT = "boris_currentAccount";

export interface AccountSummary {
  account_id: string;
  name: string;
  role?: string;
  company_website?: string;
  company_niche?: string;
  company_description?: string;
  company_advantages?: string;
  company_client_description?: string;
  company_tone?: string;
  client_goal?: string;
  client_goal_text?: string;
}

export type AccountMode = "single" | "all";

export interface AccountContextValue {
  accounts: AccountSummary[];
  selectedAccountId: string | null;
  selectedAccount: AccountSummary | null;
  mode: AccountMode;
  setSelectedAccount: (id: string) => void;
  setAllAccounts: () => void;
  reload: () => void;
  loading: boolean;
  error: string | null;
}

const EMPTY: AccountContextValue = {
  accounts: [],
  selectedAccountId: null,
  selectedAccount: null,
  mode: "single",
  setSelectedAccount: function () { /* провайдера нет — молча ничего */ },
  setAllAccounts: function () { /* провайдера нет — молча ничего */ },
  reload: function () { /* провайдера нет — молча ничего */ },
  loading: false,
  error: null,
};

const Ctx = React.createContext<AccountContextValue>(EMPTY);

function readToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return localStorage.getItem("boris_token") || "";
  } catch (e) {
    return "";
  }
}

function readRole(): string {
  if (typeof window === "undefined") return "";
  try {
    return (localStorage.getItem("boris_user_role") || "").toLowerCase();
  } catch (e) {
    return "";
  }
}

function readSaved(): string {
  if (typeof window === "undefined") return "";
  try {
    return localStorage.getItem(LS_ACCOUNT) || "";
  } catch (e) {
    return "";
  }
}

function readLinked(): string {
  if (typeof window === "undefined") return "";
  try {
    const params = new URLSearchParams(window.location.search);
    return params.get("account_id") || params.get("accountId") || "";
  } catch (e) {
    return "";
  }
}

function writeSaved(id: string): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(LS_ACCOUNT, id);
  } catch (e) {
    // приватный режим — выбор просто не переживёт перезагрузку
  }
}

export function AccountProvider(props: { children: React.ReactNode }) {
  const [accounts, setAccounts] = React.useState<AccountSummary[]>([]);
  const [selectedAccountId, setSelected] = React.useState<string | null>(null);
  const [mode, setMode] = React.useState<AccountMode>("single");
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(function () {
    const token = readToken();
    if (!token) return;                 // на публичных страницах не ходим

    let alive = true;
    setLoading(true);
    setError(null);

    fetch("/api/accounts/list", {
      headers: { Authorization: "Bearer " + token },
      cache: "no-store",
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!alive) return;
        const rawList: AccountSummary[] = (data && data.accounts) || [];
        const role = readRole();
        // INTERNAL_ACCOUNT_FILTER_V2:
        // QA/test/demo fixtures must never leak into the owner/admin product
        // selector.  A dedicated QA client must still see its own workspace, so
        // filtering is intentionally disabled for role=client.
        const isInternalAccount = function (a: AccountSummary): boolean {
          const id = String(a && a.account_id || "").trim().toLowerCase();
          return /^(?:qa|test|demo|fixture|sandbox|mock)(?:[_-]|$)/i.test(id);
        };
        const visible = role === "client"
          ? rawList
          : rawList.filter(function (a: AccountSummary) { return !isInternalAccount(a); });
        // Backend account_id is the canonical identity.  Defensively collapse
        // duplicate rows so one account can never appear twice in shared selectors.
        const seen = new Set<string>();
        const list: AccountSummary[] = visible.filter(function (a: AccountSummary) {
          const id = String(a && a.account_id || "").trim();
          if (!id || seen.has(id)) return false;
          seen.add(id);
          return true;
        });
        setAccounts(list);

        // Единственный приоритет выбора для ссылок, reload и первого входа:
        // link -> current -> saved -> first. Каждый кандидат обязан входить
        // в доступный backend-список, поэтому query string не расширяет доступ.
        const saved = readSaved();
        const next = resolveAccountSelection(
          list,
          readLinked(),
          selectedAccountId,
          saved,
        );
        setSelected(next);
        if (next && next !== saved) writeSaved(next);
        setLoading(false);
      })
      .catch(function () {
        if (!alive) return;
        setError("Не удалось получить список аккаунтов");
        setLoading(false);
      });

    return function () { alive = false; };
  }, [attempt]);

  const setSelectedAccount = React.useCallback(function (id: string) {
    setSelected(id);
    setMode("single");
    writeSaved(id);
  }, []);

  const setAllAccounts = React.useCallback(function () {
    setMode("all");
  }, []);

  const reload = React.useCallback(function () {
    setAttempt(function (n) { return n + 1; });
  }, []);

  const selectedAccount = React.useMemo(function () {
    for (let i = 0; i < accounts.length; i++) {
      if (accounts[i].account_id === selectedAccountId) return accounts[i];
    }
    return null;
  }, [accounts, selectedAccountId]);

  const value: AccountContextValue = {
    accounts: accounts,
    selectedAccountId: selectedAccountId,
    selectedAccount: selectedAccount,
    mode: mode,
    setSelectedAccount: setSelectedAccount,
    setAllAccounts: setAllAccounts,
    reload: reload,
    loading: loading,
    error: error,
  };

  return <Ctx.Provider value={value}>{props.children}</Ctx.Provider>;
}

export function useAccounts(): AccountContextValue {
  return React.useContext(Ctx);
}

/** Разделы, которые собирают данные по всем аккаунтам сразу. */
const AGGREGATED = ["/messages", "/rop", "/agency"];

export function isAggregatedRoute(pathname: string): boolean {
  if (!pathname) return false;
  for (let i = 0; i < AGGREGATED.length; i++) {
    if (pathname.startsWith(AGGREGATED[i])) return true;
  }
  return false;
}
