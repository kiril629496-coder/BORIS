"use client";

import { useEffect, useState } from "react";

import {
  apiGet,
  getAccount,
  setAccount,
} from "../../lib/api";

import css from "./shell.module.css";

type Props = {
  account: string;
  onAccountChange: (id: string) => void;
};

function getId(a: any): string {
  return String(
    a?.account_id ??
    a?.id ??
    "",
  );
}

function getName(a: any): string {
  return String(
    a?.name ??
    a?.account_name ??
    a?.title ??
    getId(a),
  );
}

export default function AccountSelector({
  account,
  onAccountChange,
}: Props) {
  const [accounts, setAccounts] = useState<any[]>([]);

  useEffect(() => {
    let alive = true;

    apiGet("/api/accounts/list")
      .then((d: any) => {
        if (!alive) return;

        const valid =
          Array.isArray(d) ||
          Boolean(
            d &&
            Array.isArray(d.accounts),
          );

        if (!valid) return;

        const list = Array.isArray(d)
          ? d
          : d.accounts;

        setAccounts(list);

        /*
         * Preserve old HomeShell behaviour:
         * if page has no selected account yet,
         * restore the stored one or select the first.
         */
        if (!account && list.length > 0) {
          const stored = getAccount();

          const storedExists =
            Boolean(stored) &&
            list.some(
              (a: any) =>
                getId(a) === String(stored),
            );

          const selected = storedExists
            ? String(stored)
            : getId(list[0]);

          if (selected) {
            setAccount(selected);
            onAccountChange(selected);
          }
        }
      })
      .catch(() => {
        /*
         * No visual crash on account API failure.
         * Scenario page keeps its own existing error handling.
         */
      });

    return () => {
      alive = false;
    };
  }, [account, onAccountChange]);

  if (accounts.length <= 1) {
    return null;
  }

  return (
    <label className={css.accountControl}>
      <span className={css.accountLabel}>
        Аккаунт
      </span>

      <select
        className={css.accountSelect}
        value={account}
        aria-label="Аккаунт"
        onChange={(e) => {
          const next = e.target.value;

          setAccount(next);
          onAccountChange(next);
        }}
      >
        {accounts.map((a: any) => {
          const id = getId(a);

          return (
            <option key={id} value={id}>
              {getName(a)}
            </option>
          );
        })}
      </select>
    </label>
  );
}
