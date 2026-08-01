"use client";

/**
 * BORIS AI — библиотека компонентов.
 * Новые экраны собираются ТОЛЬКО из этих деталей.
 * Нужен новый компонент — сначала добавить сюда, потом использовать.
 */
import React from "react";
import { Mascot } from '../components/Mascot';
import {
  LuHouse,
  LuMegaphone,
  LuMessageSquare,
  LuUsers,
  LuUser,
  LuListTodo,
  LuListChecks,
  LuChartLine,
  LuTrendingUp,
  LuWallet,
  LuCreditCard,
  LuSettings,
  LuBrain,
  LuBot,
  LuHandshake,
  LuCheck,
  LuX,
  LuPencil,
  LuPen,
  LuTrash2,
  LuTrash,
  LuPlus,
  LuRefreshCw,
  LuSearch,
  LuCircleAlert,
  LuInfo,
  LuClock,
  LuShield,
  LuPlug,
  LuFileText,
  LuStar,
  LuSend,
  LuArrowRight,
  LuEye,
  LuLock,
  LuZap,
  LuTarget,
  LuImage,
  LuCalendar,
  LuBell,
  LuAward,
  LuLink,
  LuHeadphones,
  LuMonitor,
  LuLayers,
  LuPenTool,
  LuList,
  LuBriefcase,
  LuCoins,
  LuBanknote,
  LuUpload,
  LuPalette,
  LuGlobe,
  LuChartColumn,
  LuChartBar,
  LuBuilding2,
  LuBuilding,
} from "react-icons/lu";
import { color, radius, space, shadow, font, statusStyle, StatusKind, layout } from "./tokens";

type Div = React.HTMLAttributes<HTMLDivElement>;

/* ------------------------------------------------------------------ каркас */
export function Page({ children, style, ...r }: Div) {
  return (
    <div style={{ background: color.bg, minHeight: "100vh",
                  padding: layout.page.padding, ...style }} {...r}>
      <div style={{ maxWidth: layout.page.maxWidth, margin: "0 auto" }}>{children}</div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }:
  { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: space.lg,
                  marginBottom: space.xl, flexWrap: "wrap" }}>
      <div style={{ flex: 1, minWidth: 260 }}>
        <h1 style={{ ...font.h1, margin: 0 }}>{title}</h1>
        {subtitle ? <div style={{ ...font.body, marginTop: 6 }}>{subtitle}</div> : null}
      </div>
      {actions ? <div style={{ display: "flex", gap: space.sm }}>{actions}</div> : null}
    </div>
  );
}

export function Card({ children, padding = space.xxl, style, ...r }:
  Div & { padding?: number }) {
  return (
    <div style={{ background: color.surface, border: "1px solid " + color.line,
                  borderRadius: radius.lg, boxShadow: shadow.card, padding, ...style }} {...r}>
      {children}
    </div>
  );
}

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: space.xl }}>
      <div style={{ ...font.h3, marginBottom: space.md }}>{title}</div>
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ кнопки */
type BtnKind = "primary" | "secondary" | "ghost" | "danger";

export function Button({ kind = "primary", disabled, children, style, ...r }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: BtnKind }) {
  const [hover, setHover] = React.useState(false);
  const [active, setActive] = React.useState(false);
  const base: React.CSSProperties = {
    borderRadius: radius.md, padding: "11px 18px", fontSize: 13.5, fontWeight: 600,
    cursor: disabled ? "not-allowed" : "pointer", border: "1px solid transparent",
    transition: "background .12s, border-color .12s", opacity: disabled ? 0.5 : 1,
  };
  const map: Record<BtnKind, React.CSSProperties> = {
    primary: { background: active ? color.blueActive : hover ? color.blueHover : color.blue,
               color: color.onAccent },
    secondary: { background: color.blueSoft, color: color.blue,
                 borderColor: hover ? color.blue : color.blueSoft },
    ghost: { background: color.surface, color: color.heading,
             borderColor: hover ? color.lineStrong : color.line },
    danger: { background: color.redSoft, color: color.red,
              borderColor: hover ? color.red : color.redSoft },
  };
  return (
    <button disabled={disabled} style={{ ...base, ...map[kind], ...style }}
            onMouseEnter={() => setHover(true)} onMouseLeave={() => { setHover(false); setActive(false); }}
            onMouseDown={() => setActive(true)} onMouseUp={() => setActive(false)} {...r}>
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------- формы */
const fieldBase: React.CSSProperties = {
  width: "100%", border: "1px solid " + color.lineStrong, borderRadius: radius.sm,
  padding: "10px 13px", fontSize: 13.5, color: color.heading,
  background: color.surface, outline: "none", boxSizing: "border-box",
};

export function Field({ label, hint, children }:
  { label?: string; hint?: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "block", marginBottom: space.md }}>
      {label ? <div style={{ ...font.bodyStrong, marginBottom: 6 }}>{label}</div> : null}
      {children}
      {hint ? <div style={{ ...font.small, marginTop: 5 }}>{hint}</div> : null}
    </label>
  );
}

export function Input(p: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...p} style={{ ...fieldBase, ...p.style }} />;
}

export function Textarea(p: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...p} style={{ ...fieldBase, minHeight: 96, resize: "vertical", ...p.style }} />;
}

export function Select(p: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...p} style={{ ...fieldBase, ...p.style }} />;
}

export function Choice({ type = "checkbox", checked, onToggle, title, hint, right }:
  { type?: "checkbox" | "radio"; checked: boolean; onToggle: () => void;
    title: React.ReactNode; hint?: string; right?: React.ReactNode }) {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: space.md, cursor: "pointer",
                    border: "1px solid " + (checked ? color.blue : color.line),
                    background: checked ? color.blueSoft : color.surface,
                    borderRadius: radius.md, padding: "11px 14px", marginBottom: space.sm }}>
      <input type={type} checked={checked} onChange={onToggle}
             style={{ accentColor: color.blue, width: 16, height: 16 }} />
      <span style={{ flex: 1 }}>
        <span style={font.bodyStrong as React.CSSProperties}>{title}</span>
        {hint ? <span style={{ ...font.small, display: "block", marginTop: 2 }}>{hint}</span> : null}
      </span>
      {right}
    </label>
  );
}

/* ------------------------------------------------------------- индикаторы */
export function Badge({ kind = "neutral", children }:
  { kind?: StatusKind; children: React.ReactNode }) {
  const s = statusStyle[kind];
  return (
    <span style={{ ...font.micro, color: s.color, background: s.bg,
                   padding: "4px 10px", borderRadius: radius.pill, display: "inline-block" }}>
      {children}
    </span>
  );
}

export function Kpi({ value, label, kind = "neutral" }:
  { value: React.ReactNode; label: string; kind?: StatusKind }) {
  const c = kind === "neutral" ? color.heading : statusStyle[kind].color;
  return (
    <Card padding={space.xl} style={{ minWidth: 168, flex: "1 1 168px" }}>
      <div style={{ ...font.small, marginBottom: 6 }}>{label}</div>
      <div style={{ ...font.kpi, color: c }}>{value}</div>
    </Card>
  );
}

export function KpiRow({ children }: { children: React.ReactNode }) {
  return <div style={{ display: "flex", gap: space.md, flexWrap: "wrap" }}>{children}</div>;
}

/* ----------------------------------------------------------------- таблица */
export function Table({ head, rows, onRowClick }:
  { head: string[]; rows: React.ReactNode[][]; onRowClick?: (i: number) => void }) {
  return (
    <Card padding={0} style={{ overflow: "hidden" }}>
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${head.length}, minmax(0,1fr))`,
                    background: color.surfaceAlt, padding: "11px 16px", gap: space.md }}>
        {head.map((h, i) => (
          <div key={i} style={{ ...font.micro, color: color.muted, letterSpacing: 0.3 }}>
            {h.toUpperCase()}
          </div>
        ))}
      </div>
      {rows.map((r, i) => (
        <div key={i} onClick={onRowClick ? () => onRowClick(i) : undefined}
             style={{ display: "grid", gap: space.md,
                      gridTemplateColumns: `repeat(${head.length}, minmax(0,1fr))`,
                      padding: "13px 16px", cursor: onRowClick ? "pointer" : "default",
                      borderTop: "1px solid " + color.line, ...font.body }}>
          {r.map((cell, j) => <div key={j}>{cell}</div>)}
        </div>
      ))}
      {!rows.length ? (
        <div style={{ padding: space.xxl, ...font.small, textAlign: "center" }}>Пока пусто</div>
      ) : null}
    </Card>
  );
}

/* --------------------------------------------------- состояния и сообщения */
export function EmptyState({ title, hint, action }:
  { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <Card style={{ textAlign: "center", padding: 40 }}>
      <Mascot size={64} interactive={false} />
      <div style={{ ...font.h3, marginTop: space.md }}>{title}</div>
      {hint ? <div style={{ ...font.body, marginTop: 6 }}>{hint}</div> : null}
      {action ? <div style={{ marginTop: space.lg }}>{action}</div> : null}
    </Card>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <Card>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} style={{ height: 12, borderRadius: radius.pill,
                              background: color.surfaceAlt, marginBottom: space.md,
                              width: `${100 - i * 12}%` }} />
      ))}
    </Card>
  );
}

export function Alert({ kind = "info", children }:
  { kind?: StatusKind; children: React.ReactNode }) {
  const s = statusStyle[kind];
  return (
    <div style={{ background: s.bg, color: color.heading, borderRadius: radius.md,
                  padding: "11px 15px", fontSize: 13.5, marginBottom: space.lg,
                  borderLeft: "3px solid " + s.color }}>
      {children}
    </div>
  );
}


/* -------------------------------------------------------------- иконки
 * Одна библиотека — Lucide. Импортировать иконки по проекту НЕЛЬЗЯ,
 * только через <Icon name="..." />. Нужна новая — добавить в карту ниже.
 * Имена экспортов Lucide меняются между версиями, поэтому берём первое
 * существующее из списка кандидатов.
 */
type LuComp = React.ComponentType<{ size?: number; color?: string; strokeWidth?: number }>;

const LIB: Record<string, LuComp> = { LuHouse, LuMegaphone, LuMessageSquare, LuUsers, LuUser, LuListTodo, LuListChecks, LuChartLine, LuTrendingUp, LuWallet, LuCreditCard, LuSettings, LuBrain, LuBot, LuHandshake, LuCheck, LuX, LuPencil, LuPen, LuTrash2, LuTrash, LuPlus, LuRefreshCw, LuSearch, LuCircleAlert, LuInfo, LuClock, LuShield, LuPlug, LuFileText, LuStar, LuSend, LuArrowRight, LuEye, LuLock, LuZap, LuTarget, LuImage, LuCalendar, LuBell, LuAward, LuLink, LuHeadphones, LuMonitor, LuLayers, LuPenTool, LuList, LuBriefcase, LuCoins, LuBanknote, LuUpload, LuPalette, LuGlobe, LuChartColumn, LuChartBar, LuBuilding2, LuBuilding };

function pick(...names: string[]): LuComp {
  for (const n of names) if (LIB[n]) return LIB[n];
  return LIB.LuCircle;
}

export const icons = {
  home: pick("LuHouse", "LuHome"),
  ads: pick("LuMegaphone"),
  messages: pick("LuMessageSquare"),
  clients: pick("LuUsers"),
  user: pick("LuUser"),
  tasks: pick("LuListTodo", "LuListChecks"),
  analytics: pick("LuChartLine", "LuTrendingUp"),
  finance: pick("LuWallet"),
  card: pick("LuCreditCard"),
  settings: pick("LuSettings"),
  memory: pick("LuBrain"),
  ai: pick("LuBot"),
  handshake: pick("LuHandshake", "LuUsers"),
  check: pick("LuCheck"),
  close: pick("LuX"),
  edit: pick("LuPencil", "LuPen"),
  trash: pick("LuTrash2", "LuTrash"),
  plus: pick("LuPlus"),
  refresh: pick("LuRefreshCw"),
  search: pick("LuSearch"),
  alert: pick("LuCircleAlert", "LuAlertCircle"),
  info: pick("LuInfo"),
  clock: pick("LuClock"),
  shield: pick("LuShield"),
  plug: pick("LuPlug"),
  file: pick("LuFileText"),
  star: pick("LuStar"),
  send: pick("LuSend"),
  arrow: pick("LuArrowRight"),
  eye: pick("LuEye"),
  lock: pick("LuLock"),
  zap: pick("LuZap"),
  target: pick("LuTarget"),
  image: pick("LuImage"),
  calendar: pick("LuCalendar"),
  bell: pick("LuBell"),
  award: pick("LuAward"),
  link: pick("LuLink"),
  support: pick("LuHeadphones"),
  monitor: pick("LuMonitor"),
  layers: pick("LuLayers"),
  pen: pick("LuPenTool", "LuPen"),
  list: pick("LuList", "LuListTodo"),
  briefcase: pick("LuBriefcase"),
  coins: pick("LuCoins", "LuBanknote"),
  upload: pick("LuUpload"),
  palette: pick("LuPalette"),
  globe: pick("LuGlobe"),
  chart: pick("LuChartColumn", "LuBarChart3", "LuChartBar"),
  building: pick("LuBuilding2", "LuBuilding"),
};

export type IconName = keyof typeof icons;
export type IconTone = "default" | "muted" | "accent" | "success" | "warning" | "danger" | "disabled";

const ICON_TONE: Record<IconTone, string> = {
  default: color.heading,
  muted: color.muted,
  accent: color.blue,
  success: color.green,
  warning: color.orange,
  danger: color.red,
  disabled: color.lineStrong,
};

export const ICON_SIZE = 20;

export function Icon({ name, size = ICON_SIZE, tone = "default", style }:
  { name: IconName; size?: number; tone?: IconTone; style?: React.CSSProperties }) {
  const C = icons[name];
  return (
    <span style={{ display: "inline-flex", alignItems: "center",
                   justifyContent: "center", lineHeight: 0, ...style }}>
      <C size={size} color={ICON_TONE[tone]} strokeWidth={2} />
    </span>
  );
}

/** Круглый чип с иконкой — единая замена цветным кружкам по экранам */
export function IconChip({ name, kind = "info", size = 40 }:
  { name: IconName; kind?: StatusKind; size?: number }) {
  const s = statusStyle[kind];
  return (
    <span style={{ width: size, height: size, borderRadius: radius.pill,
                   background: s.bg, display: "inline-flex",
                   alignItems: "center", justifyContent: "center" }}>
      <Icon name={name} size={Math.round(size * 0.5)}
            tone={kind === "neutral" ? "muted" : kind === "info" ? "accent" :
                  kind === "success" ? "success" : kind === "warning" ? "warning" : "danger"} />
    </span>
  );
}
