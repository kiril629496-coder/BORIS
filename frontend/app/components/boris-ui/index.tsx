import React from "react";
import { IconChip, type IconName } from "../../ui";

type Children = {
  children?: React.ReactNode;
  className?: string;
};

function cx(...parts: Array<string | undefined | false>) {
  return parts.filter(Boolean).join(" ");
}

export function BorisPage({
  children,
  className,
}: Children) {
  return (
    <div className={cx("boris-page", className)}>
      <div className="boris-page-container">{children}</div>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="boris-page-header">
      <div>
        <h1 className="boris-page-title">{title}</h1>
        {description && (
          <p className="boris-page-description">{description}</p>
        )}
      </div>
      {actions && <div>{actions}</div>}
    </header>
  );
}

export function Section({
  title,
  actions,
  children,
  className,
}: Children & {
  title?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <section className={cx("boris-section", className)}>
      {(title || actions) && (
        <div className="boris-section-header">
          {title && <h2 className="boris-section-title">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Card({
  children,
  className,
  hover = true,
}: Children & {
  hover?: boolean;
}) {
  return (
    <div
      className={cx(
        "boris-card",
        hover && "boris-card-hover",
        className
      )}
    >
      {children}
    </div>
  );
}

export function CardBody({
  children,
  className,
}: Children) {
  return (
    <div className={cx("boris-card-body", className)}>
      {children}
    </div>
  );
}

export function Button({
  children,
  variant = "primary",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
}) {
  return (
    <button
      {...props}
      className={cx(
        "boris-btn",
        `boris-btn-${variant}`,
        className
      )}
    >
      {children}
    </button>
  );
}

export function Badge({
  children,
  tone,
  className,
}: Children & {
  tone?: "blue" | "green" | "orange" | "purple";
}) {
  return (
    <span
      className={cx(
        "boris-badge",
        tone && `boris-badge-${tone}`,
        className
      )}
    >
      {children}
    </span>
  );
}

export function Panel({
  title,
  children,
  className,
}: Children & {
  title?: React.ReactNode;
}) {
  return (
    <div className={cx("boris-panel", className)}>
      {title && (
        <div className="boris-panel-header">
          <strong>{title}</strong>
        </div>
      )}
      <div className="boris-panel-body">{children}</div>
    </div>
  );
}

export function BorisHelper({
  title = "BORIS всегда на связи!",
  children,
  className,
}: Children & {
  title?: React.ReactNode;
}) {
  return (
    <div className={cx("boris-helper", className)}>
      <div className="boris-helper-avatar" aria-hidden="true">
        B
      </div>
      <div>
        <p className="boris-helper-title">{title}</p>
        <div className="boris-helper-text">{children}</div>
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
  className,
}: Children & {
  title: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className={cx("boris-state", className)}>
      <div className="boris-helper-avatar" aria-hidden="true">
        B
      </div>
      <div className="boris-state-title">{title}</div>
      {children && <div className="boris-state-text">{children}</div>}
      {action && <div style={{ marginTop: 16 }}>{action}</div>}
    </div>
  );
}

/* BORIS UI — G3.1 COMPONENTS */

export function PageHero({
  icon,
  title,
  description,
  actions,
  children,
}: {
  icon?: IconName;
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="boris-g31-hero">
      <div className="boris-g31-hero-main">
        {icon ? (
          <IconChip name={icon} kind="info" size={44} />
        ) : null}

        <div className="boris-g31-hero-copy">
          <h1 className="boris-g31-hero-title">{title}</h1>

          {description ? (
            <div className="boris-g31-hero-description">
              {description}
            </div>
          ) : null}
        </div>

        {actions ? (
          <div className="boris-g31-hero-actions">
            {actions}
          </div>
        ) : null}
      </div>

      {children ? (
        <div className="boris-g31-hero-extra">
          {children}
        </div>
      ) : null}
    </div>
  );
}

export function SurfaceCard({
  icon,
  title,
  description,
  children,
  actions,
  hover = true,
  className,
}: {
  icon?: IconName;
  title?: React.ReactNode;
  description?: React.ReactNode;
  children?: React.ReactNode;
  actions?: React.ReactNode;
  hover?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "boris-g31-surface",
        hover && "boris-g31-surface-hover",
        className
      )}
    >
      {(icon || title || description || actions) ? (
        <div className="boris-g31-surface-head">
          {icon ? (
            <IconChip name={icon} kind="info" size={40} />
          ) : null}

          <div className="boris-g31-surface-copy">
            {title ? (
              <div className="boris-g31-surface-title">
                {title}
              </div>
            ) : null}

            {description ? (
              <div className="boris-g31-surface-description">
                {description}
              </div>
            ) : null}
          </div>

          {actions ? (
            <div className="boris-g31-surface-actions">
              {actions}
            </div>
          ) : null}
        </div>
      ) : null}

      {children ? (
        <div className="boris-g31-surface-body">
          {children}
        </div>
      ) : null}
    </div>
  );
}

export function ActionCard({
  icon,
  title,
  description,
  meta,
  tone = "blue",
  status,
  onClick,
  children,
  className,
}: {
  icon: IconName;
  title: React.ReactNode;
  description?: React.ReactNode;
  meta?: React.ReactNode;
  tone?: "blue" | "green" | "violet" | "orange" | "pink" | "cyan";
  status?: "idle" | "active" | "working" | "success" | "warning" | "error" | "waiting_external" | "requires_action" | "disabled";
  onClick?: () => void;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      className={cx(
        "boris-g31-action-card",
        className
      )}
      data-tone={tone}
      data-status={status}
      disabled={status === "disabled"}
      onClick={onClick}
    >
      <IconChip name={icon} kind="info" size={42} />

      <span className="boris-g31-action-copy">
        <strong className="boris-g31-action-title">
          {title}
        </strong>

        {description ? (
          <span className="boris-g31-action-description">
            {description}
          </span>
        ) : null}

        {children}
      </span>

      {meta ? (
        <span className="boris-g31-action-meta">
          {meta}
        </span>
      ) : null}
    </button>
  );
}

export function StatCard({
  icon,
  label,
  value,
  hint,
  className,
}: {
  icon?: IconName;
  label: React.ReactNode;
  value: React.ReactNode;
  hint?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("boris-g31-stat", className)}>
      <div className="boris-g31-stat-top">
        {icon ? (
          <IconChip name={icon} kind="info" size={36} />
        ) : null}

        <div className="boris-g31-stat-label">
          {label}
        </div>
      </div>

      <div className="boris-g31-stat-value">
        {value}
      </div>

      {hint ? (
        <div className="boris-g31-stat-hint">
          {hint}
        </div>
      ) : null}
    </div>
  );
}

export function StatGrid({
  children,
  className,
}: Children) {
  return (
    <div className={cx("boris-g31-stat-grid", className)}>
      {children}
    </div>
  );
}
