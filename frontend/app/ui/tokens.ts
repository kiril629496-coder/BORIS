/**
 * BORIS AI — токены дизайн-системы.
 * Эталон: Главная страница BORIS AI. Значения сняты пипеткой с макета.
 * Правило: цвета, отступы и радиусы берутся ТОЛЬКО отсюда.
 */

export const color = {
  /* поверхности */
  bg: "#F7F8FC",
  surface: "#FFFFFF",
  surfaceAlt: "#F1F3F8",
  line: "#E8ECF4",
  lineStrong: "#DDE3EE",

  /* текст */
  heading: "#0B1020",
  text: "#5E667D",
  muted: "#9AA1B8",
  onAccent: "#FFFFFF",

  /* фирменный синий — единственный на весь продукт */
  blue: "#3480FE",
  blueHover: "#2A6FE0",
  blueActive: "#2461C4",
  blueSoft: "#ECF1FD",

  /* статусы */
  green: "#1BB385",
  greenSoft: "#E9F6EF",
  orange: "#FE961C",
  orangeSoft: "#FFF4E6",
  red: "#F3364E",
  redSoft: "#FEEEEF",
  purple: "#7C5CFC",
  purpleSoft: "#F0EDFE",
} as const;

/** Палитра для аватарок и цветных меток списков. Только для них. */
export const avatarPalette = [
  color.blue, color.green, color.purple, color.orange,
  color.muted, color.red, "#0EA5E9", "#E11D8F",
] as const;

export const radius = { sm: 10, md: 12, lg: 16, xl: 20, pill: 999 } as const;

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24, xxxl: 32 } as const;

export const shadow = {
  card: "0 1px 2px rgba(11,16,32,0.04), 0 8px 24px rgba(11,16,32,0.05)",
  pop: "0 8px 32px rgba(11,16,32,0.12)",
  none: "none",
} as const;

export const font = {
  family: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  h1: { fontSize: 24, fontWeight: 700, color: color.heading, lineHeight: 1.25 },
  h2: { fontSize: 18, fontWeight: 700, color: color.heading, lineHeight: 1.3 },
  h3: { fontSize: 15, fontWeight: 700, color: color.heading, lineHeight: 1.35 },
  body: { fontSize: 13.5, fontWeight: 400, color: color.text, lineHeight: 1.5 },
  bodyStrong: { fontSize: 13.5, fontWeight: 600, color: color.heading, lineHeight: 1.5 },
  small: { fontSize: 12.5, fontWeight: 400, color: color.muted, lineHeight: 1.45 },
  micro: { fontSize: 11.5, fontWeight: 700, lineHeight: 1.3 },
  kpi: { fontSize: 26, fontWeight: 700, color: color.heading, lineHeight: 1.1 },
} as const;

/** Ширина рабочей области и колонок — единая сетка */
export const layout = {
  page: { maxWidth: 1180, padding: "28px 32px" },
  gap: space.lg,
  sidebar: 260,
} as const;

export type StatusKind = "neutral" | "info" | "success" | "warning" | "danger";

export const statusStyle: Record<StatusKind, { color: string; bg: string }> = {
  neutral: { color: color.muted, bg: color.surfaceAlt },
  info: { color: color.blue, bg: color.blueSoft },
  success: { color: color.green, bg: color.greenSoft },
  warning: { color: color.orange, bg: color.orangeSoft },
  danger: { color: color.red, bg: color.redSoft },
};
