"use client";

/**
 * Канонический доменный каркас Единого центра объявлений.
 *
 * ВАЖНО:
 * - глобальный chrome предоставляет /dashboard/campaign layout;
 * - EcShell отвечает только за локальную навигацию ЕЦО;
 * - бизнес-логики страниц здесь нет.
 */

import { useRouter } from "next/navigation";
import css from "./EcShell.module.css";

export const EC_TABS = [
  {
    key: "overview",
    label: "Обзор",
    href: "/dashboard/campaign",
  },
  {
    key: "catalog",
    label: "Ассортимент",
    href: "/dashboard/campaign/catalog",
  },
  {
    key: "media",
    label: "Медиатека",
    href: "/dashboard/campaign/media",
  },
  {
    key: "photo",
    label: "AI-фотостудия",
    href: "/dashboard/campaign/photo",
  },
  {
    key: "banners",
    label: "Баннеры",
    href: "/dashboard/campaign/banners",
  },
  {
    key: "ab-tests",
    label: "АБ тесты",
    href: "/dashboard/campaign/ab-tests",
  },
];

type EcShellProps = {
  tab: string;
  title?: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;

  /**
   * Legacy-compatible extra props are intentionally allowed.
   * Example: crumbs on catalog.
   * They do not participate in global shell rendering.
   */
  [key: string]: unknown;
};

export function EcShell({
  tab,
  title,
  subtitle,
  actions,
  children,
}: EcShellProps) {
  const router = useRouter();

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ margin: 0, fontSize: 28, lineHeight: 1.2 }}>
          {title || "Центр объявлений"}
        </h1>
        <div style={{ marginTop: 6, color: "var(--boris-text-2)", fontSize: 14 }}>
          {subtitle || "Ассортимент, фотографии, тексты и публикация в одном окне"}
        </div>
      </div>
      <nav
        className={css.tabs}
        aria-label="Разделы центра объявлений"
      >
        {EC_TABS.map((item) => {
          const active = item.key === tab;

          return (
            <button
              key={item.key}
              type="button"
              className={[
                css.tab,
                active ? css.tabActive : "",
              ]
                .filter(Boolean)
                .join(" ")}
              aria-current={active ? "page" : undefined}
              onClick={() => router.push(item.href)}
            >
              {item.label}
            </button>
          );
        })}

        {actions ? (
          <div className={css.actions}>
            {actions}
          </div>
        ) : null}
      </nav>

      <div className={tab === "overview" ? undefined : css.unifiedWorkspace} data-ec-workspace={tab}>
        {children}
      </div>
    </>
  );
}

type EcEmptyProps = {
  title: React.ReactNode;
  hint?: React.ReactNode;
  action?: React.ReactNode;
};

export function EcEmpty({
  title,
  hint,
  action,
}: EcEmptyProps) {
  return (
    <div className={css.empty}>
      <div className={css.emptyTitle}>
        {title}
      </div>

      {hint ? (
        <div className={css.emptyHint}>
          {hint}
        </div>
      ) : null}

      {action ? (
        <div className={css.emptyAction}>
          {action}
        </div>
      ) : null}
    </div>
  );
}
