/**
 * BORIS AI — токены дизайн-системы.
 * Эталон: Главная страница BORIS AI. Значения сняты пипеткой с макета.
 * Правило: цвета, отступы и радиусы берутся ТОЛЬКО отсюда.
 */

export const color = {
  /* canonical CSS variables: one visual source of truth */
  bg: "var(--boris-background)",
  surface: "var(--boris-surface)",
  surfaceAlt: "var(--boris-surface-soft)",
  line: "var(--boris-border)",
  lineStrong: "var(--boris-border-hover)",

  heading: "var(--boris-text-primary)",
  text: "var(--boris-text-secondary)",
  muted: "var(--boris-text-muted)",
  onAccent: "#FFFFFF",

  blue: "var(--boris-primary)",
  blueHover: "var(--boris-primary-hover)",
  blueActive: "var(--boris-primary-hover)",
  blueSoft: "var(--boris-blue-soft)",

  green: "var(--boris-success)",
  greenSoft: "var(--boris-green-soft)",
  orange: "var(--boris-warning)",
  orangeSoft: "var(--boris-orange-soft)",
  red: "var(--boris-danger)",
  redSoft: "var(--boris-danger-soft)",
  purple: "var(--boris-purple)",
  purpleSoft: "var(--boris-purple-soft)",
} as const;

/** Палитра для аватарок и цветных меток списков. Только для них. */
export const avatarPalette = [
  color.blue, color.green, color.purple, color.orange,
  color.muted, color.red, "#0EA5E9", "#E11D8F",
] as const;

export const radius = { sm: 10, md: 14, lg: 18, xl: 24, pill: 999 } as const;

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24, xxxl: 32 } as const;

export const shadow = {
  card: "var(--boris-shadow-card)",
  pop: "var(--boris-shadow-modal)",
  none: "none",
} as const;

export const font = {
  family: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  /* BORIS semantic type scale: Large / Medium / Small (+ display KPI). */
  h1: { fontSize: 24, fontWeight: 700, color: color.heading, lineHeight: 1.25 },
  h2: { fontSize: 24, fontWeight: 700, color: color.heading, lineHeight: 1.25 },
  h3: { fontSize: 15, fontWeight: 700, color: color.heading, lineHeight: 1.4 },
  body: { fontSize: 15, fontWeight: 400, color: color.text, lineHeight: 1.55 },
  bodyStrong: { fontSize: 15, fontWeight: 600, color: color.heading, lineHeight: 1.5 },
  small: { fontSize: 13.5, fontWeight: 400, color: color.muted, lineHeight: 1.5 },
  micro: { fontSize: 13.5, fontWeight: 700, lineHeight: 1.4 },
  kpi: { fontSize: 28, fontWeight: 700, color: color.heading, lineHeight: 1.1 },
} as const;

/** Ширина рабочей области и колонок — единая сетка */
export const layout = {
  page: { maxWidth: 1180, padding: "28px 32px" },
  gap: space.lg,
  sidebar: 286,
} as const;

export type StatusKind = "neutral" | "info" | "success" | "warning" | "danger";

export const statusStyle: Record<StatusKind, { color: string; bg: string }> = {
  neutral: { color: color.muted, bg: color.surfaceAlt },
  info: { color: color.blue, bg: color.blueSoft },
  success: { color: color.green, bg: color.greenSoft },
  warning: { color: color.orange, bg: color.orangeSoft },
  danger: { color: color.red, bg: color.redSoft },
};
