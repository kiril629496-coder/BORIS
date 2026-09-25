"use client";

import * as React from "react";

/**
 * FactoryReadyBadge
 *
 * A small, self-contained, read-only status badge for BORIS AI, communicating
 * that "BORIS Factory" is ready / connected for autonomous development.
 *
 * IMPORTANT: this component is intentionally standalone. It is NOT imported
 * or rendered anywhere in the application yet. It carries its own scoped
 * styles (no dependency on shared stylesheets), so it can be safely added
 * to a page later without any risk of affecting existing UI.
 */

export type FactoryReadyBadgeProps = {
  /** Optional extra class name for the outer wrapper. */
  className?: string;
  /** Optional inline style overrides for the outer wrapper. */
  style?: React.CSSProperties;
  /** Optional title override. Defaults to "BORIS Factory — готова". */
  title?: string;
  /** Optional caption override. Defaults to "Автономная разработка подключена". */
  caption?: string;
};

const FACTORY_READY_BADGE_CSS = [
  ".boris-ai-factory-ready-badge-v1 {",
  "  display: inline-flex;",
  "  align-items: center;",
  "  gap: 10px;",
  "  padding: 10px 14px;",
  "  background: #ffffff;",
  "  border: 1px solid #dfe6f2;",
  "  border-radius: 14px;",
  "  box-shadow: 0 1px 2px rgba(21, 33, 59, 0.04), 0 4px 12px rgba(21, 33, 59, 0.04);",
  "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;",
  "  line-height: 1.25;",
  "  max-width: 100%;",
  "}",
  ".boris-ai-factory-ready-badge-v1__dot {",
  "  position: relative;",
  "  display: inline-flex;",
  "  align-items: center;",
  "  justify-content: center;",
  "  width: 10px;",
  "  height: 10px;",
  "  flex: 0 0 auto;",
  "}",
  ".boris-ai-factory-ready-badge-v1__dot-core {",
  "  position: relative;",
  "  z-index: 1;",
  "  display: block;",
  "  width: 8px;",
  "  height: 8px;",
  "  border-radius: 50%;",
  "  background: #2878ff;",
  "}",
  ".boris-ai-factory-ready-badge-v1__dot-ping {",
  "  position: absolute;",
  "  inset: 0;",
  "  border-radius: 50%;",
  "  background: #2878ff;",
  "  opacity: 0.5;",
  "  animation: boris-ai-factory-ready-badge-v1-pulse 1.8s ease-out infinite;",
  "}",
  "@keyframes boris-ai-factory-ready-badge-v1-pulse {",
  "  0% { transform: scale(1); opacity: 0.5; }",
  "  70% { transform: scale(2.4); opacity: 0; }",
  "  100% { transform: scale(2.4); opacity: 0; }",
  "}",
  ".boris-ai-factory-ready-badge-v1__text {",
  "  display: flex;",
  "  flex-direction: column;",
  "  gap: 2px;",
  "  min-width: 0;",
  "}",
  ".boris-ai-factory-ready-badge-v1__title {",
  "  font-size: 13px;",
  "  font-weight: 700;",
  "  color: #15213b;",
  "  white-space: nowrap;",
  "}",
  ".boris-ai-factory-ready-badge-v1__caption {",
  "  font-size: 11px;",
  "  font-weight: 500;",
  "  color: #53617a;",
  "  white-space: nowrap;",
  "}",
  "@media (prefers-reduced-motion: reduce) {",
  "  .boris-ai-factory-ready-badge-v1__dot-ping { animation: none; opacity: 0; }",
  "}",
].join("\n");

/**
 * BORIS Factory readiness badge.
 *
 * Usage (not wired anywhere yet):
 *   import { FactoryReadyBadge } from "@/components/factory/FactoryReadyBadge";
 *   <FactoryReadyBadge />
 */
export function FactoryReadyBadge({
  className,
  style,
  title = "BORIS Factory — готова",
  caption = "Автономная разработка подключена",
}: FactoryReadyBadgeProps) {
  const rootClassName = className
    ? "boris-ai-factory-ready-badge-v1 " + className
    : "boris-ai-factory-ready-badge-v1";

  return (
    <div className={rootClassName} style={style}>
      <style>{FACTORY_READY_BADGE_CSS}</style>
      <span className="boris-ai-factory-ready-badge-v1__dot" aria-hidden="true">
        <span className="boris-ai-factory-ready-badge-v1__dot-core" />
        <span className="boris-ai-factory-ready-badge-v1__dot-ping" />
      </span>
      <span className="boris-ai-factory-ready-badge-v1__text">
        <span className="boris-ai-factory-ready-badge-v1__title">{title}</span>
        <span className="boris-ai-factory-ready-badge-v1__caption">{caption}</span>
      </span>
    </div>
  );
}

export default FactoryReadyBadge;
