"use client";

import type { ReactNode } from "react";

import AppShell from "./AppShell";
import { navigationItem } from "../../ui/navigation";

type Back = {
  label?: string;
  go?: () => void;
};

type Props = {
  children: ReactNode;
  activeKey?: string;
  back?: Back;
};

const FALLBACK_TITLES: Record<string, string> = {
  support: "Поддержка",
  agency: "Агентство",
  reactivation: "Возврат клиентов",
  sales: "Продажи",
  listings: "Объявления",
  memory: "База знаний",
  aisetup: "ИИ-сотрудники",
  journal: "Что делал BORIS",
  team: "Пользователи и доступы",
  social: "Социальные сети",
  command: "Спросите BORIS",
  slots: "Подключение аккаунтов",
};

function titleFor(key?: string): string {
  if (!key) return "BORIS AI";

  const fromMenu = navigationItem(key)?.label;

  return (
    fromMenu ||
    FALLBACK_TITLES[key] ||
    "BORIS AI"
  );
}

export function Shell({
  children,
  activeKey,
  back,
}: Props) {
  const headerExtra =
    back && typeof back.go === "function"
      ? (
          <button
            type="button"
            className="boris-btn boris-btn-secondary"
            onClick={back.go}
            data-testid="nav-back"
          >
            {back.label || "Назад"}
          </button>
        )
      : undefined;

  return (
    <AppShell
      active={activeKey}
      title={titleFor(activeKey)}
      oldTab={activeKey || "listings"}
      headerExtra={headerExtra}
    >
      {children}
    </AppShell>
  );
}

export default Shell;
