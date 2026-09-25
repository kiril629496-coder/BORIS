"use client";

import React, { ReactNode } from "react";
import AppShell from "./shell/AppShell";
import AccountSelector from "./shell/AccountSelector";

/**
 * E4 V3.
 *
 * HomeShell remains a compatibility adapter for scenario pages.
 * Existing scenario API stays unchanged.
 *
 * Visual chrome is delegated to AppShell.
 * No scenario business logic lives here.
 */
type Props = {
  active?: string;
  account: any;
  onAccountChange: (account: any) => void;
  children: ReactNode;
};

export default function HomeShell({
  active,
  account,
  onAccountChange,
  children,
}: Props) {
  return (
    <AppShell
      active={active || "scenarios"}
      title="Сценарии"
      subtitle="Управление сценариями работы BORIS"
      oldTab="listings"
      headerExtra={(
        <div data-boris-home-release="1" data-testid="homeshell-account-selector">
          <AccountSelector
          account={account}
          onAccountChange={onAccountChange}
          />
        </div>
      )}
    >
      {children}
    </AppShell>
  );
}
