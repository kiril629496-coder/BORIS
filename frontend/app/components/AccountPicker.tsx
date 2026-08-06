"use client";

/**
 * Выбор аккаунта в общей панели.
 *
 * На агрегированных разделах (сообщения, РОП, агентский) показывает не
 * выпадающий список, а спокойную пометку «Все аккаунты»: эти экраны и так
 * собирают данные по всем аккаунтам, и переводить их на один нельзя.
 */
import React from "react";
import { usePathname } from "next/navigation";
import { useAccounts, isAggregatedRoute } from "../lib/AccountContext";

export default function AccountPicker() {
  const pathname = usePathname() || "";
  const acc = useAccounts();

  if (isAggregatedRoute(pathname)) {
    return (
      <span style={{ marginLeft: "auto", fontSize: "13px", color: "#475467",
                     background: "#F2F4F7", borderRadius: "8px", padding: "6px 12px" }}
            data-testid="account-all">
        Все аккаунты{acc.accounts.length ? " · " + acc.accounts.length : ""}
      </span>
    );
  }

  if (acc.loading) {
    return <span style={{ marginLeft: "auto", fontSize: "13px", color: "#98A2B3" }}>Загружаю аккаунты…</span>;
  }

  if (acc.error) {
    return (
      <span style={{ marginLeft: "auto", fontSize: "13px", color: "#B42318" }}>
        {acc.error}
      </span>
    );
  }

  if (!acc.accounts.length) {
    return (
      <span style={{ marginLeft: "auto", fontSize: "13px", color: "#98A2B3" }} data-testid="account-none">
        Аккаунт не подключён
      </span>
    );
  }

  return (
    <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "8px" }}>
      <span style={{ fontSize: "13px", color: "#98A2B3" }}>Аккаунт</span>
      <select
        data-testid="account-picker"
        value={acc.selectedAccountId || ""}
        onChange={function (e) { acc.setSelectedAccount(e.target.value); }}
        style={{ border: "1px solid #E3E7F0", borderRadius: "8px", padding: "7px 10px",
                 fontSize: "14px", background: "#FFFFFF", maxWidth: "260px", cursor: "pointer" }}>
        {acc.accounts.map(function (a) {
          return <option key={a.account_id} value={a.account_id}>{a.name || a.account_id}</option>;
        })}
      </select>
    </span>
  );
}
