"use client";

/**
 * Компоненты Единого центра объявлений — по эталонному макету BORIS.
 *
 * Карточка задачи: иконка в скруглённом квадрате своего цвета, заголовок,
 * описание от первого лица, теги-капсулы и кнопка во всю ширину В ЦВЕТ
 * КАРТОЧКИ. Цвет задачи ведёт человека по экрану, поэтому он одинаковый
 * у иконки и кнопки.
 */
import React from "react";
import { color, font, radius, shadow, space } from "./tokens";

export const TONES: Record<string, { solid: string; soft: string }> = {
  blue: { solid: color.blue, soft: color.blueSoft },
  green: { solid: color.green, soft: color.greenSoft },
  orange: { solid: color.orange, soft: color.orangeSoft },
  purple: { solid: color.purple, soft: color.purpleSoft },
  pink: { solid: "#E11D8F", soft: "#FDECF6" },
  indigo: { solid: "#4F5BD5", soft: "#EDEEFB" },
  cyan: { solid: "#0EA5E9", soft: "#E6F5FD" },
  amber: { solid: "#F0A500", soft: "#FFF6E0" },
  red: { solid: color.red, soft: color.redSoft },
};

export function EcActionCard({ icon, tone = "blue", title, text, tags, cta,
                               onClick, state, variant = "default" }: any) {
  const t = TONES[tone] || TONES.blue;
  const soon = state === "soon";
  if (variant === "work") {
    return (
      <button type="button" disabled={soon} onClick={soon ? undefined : onClick}
        data-boris-g32b-ec="action" className="boris-ec-action-card boris-ec-action-card-work"
        style={{ ["--ec-accent" as any]: t.solid, ["--ec-soft" as any]: t.soft, opacity: soon ? .6 : 1 }}>
        <span className="boris-ec-work-icon">{icon}</span>
        <span className="boris-ec-work-copy">
          <strong>{title}</strong>
          <small>{text}</small>
          {tags && tags.length > 0 ? <span className="boris-ec-work-tags">{tags.slice(0,2).map((x:string)=><em key={x}>{x}</em>)}</span> : null}
        </span>
        <span className="boris-ec-work-arrow" aria-hidden="true">›</span>
      </button>
    );
  }
  return (
    <div data-boris-g32b-ec="action" className="boris-ec-action-card" style={{ display: "flex", flexDirection: "column", height: "100%",
                  border: `1px solid ${color.line}`, borderRadius: radius.lg,
                  background: color.surface, padding: space.xl,
                  opacity: soon ? 0.6 : 1 }}>
      <div className="boris-ec-action-icon" style={{ width: 46, height: 46, borderRadius: 14,
                    background: soon ? color.surfaceAlt : t.solid,
                    display: "flex", alignItems: "center",
                    justifyContent: "center", fontSize: 21,
                    marginBottom: space.lg }}>{icon}</div>
      <div className="boris-ec-action-title" style={{ ...font.h3, marginBottom: 8, minHeight: 40 }}>{title}</div>
      <div className="boris-ec-action-text" style={{ ...font.small, flex: 1, lineHeight: 1.5 }}>{text}</div>
      {tags && tags.length > 0 && <div className="boris-f3-8695b1095b27a4 boris-ec-action-tags">{tags.map((x: string) => <span key={x} style={{ fontSize: 11.5, padding: "3px 10px", borderRadius: radius.pill, color: color.text, background: color.surfaceAlt }}>{x}</span>)}</div>}
      <button className="boris-ec-action-button" onClick={soon ? undefined : onClick} disabled={soon}
        style={{ marginTop: space.lg, width: "100%", padding: "11px 0", border: "none", borderRadius: radius.md, background: soon ? color.surfaceAlt : t.solid, color: soon ? color.muted : "#fff", fontSize: 13.5, fontWeight: 600, cursor: soon ? "default" : "pointer" }}>
        {soon ? "Скоро" : cta}
      </button>
    </div>
  );
}

/** Пункт правой колонки: круглая иконка в мягком цвете + текст. */
export function HowRow({ icon, text, tone = "blue" }: any) {
  const t = TONES[tone] || TONES.blue;
  return (
    <div data-boris-g32b-ec="how" className="boris-f3-4df66bd7cd1d10">
      <div style={{ width: 34, height: 34, borderRadius: radius.pill, flexShrink: 0,
                    background: t.soft, color: t.solid, display: "flex",
                    alignItems: "center", justifyContent: "center", fontSize: 15 }}>
        {icon}
      </div>
      <span style={{ ...font.body, lineHeight: 1.4 }}>{text}</span>
    </div>
  );
}

export function SourceChip({ kind }: { kind: "rule" | "stats" | "ai" | "integration" }) {
  const map: any = {
    rule: ["Правило BORIS", color.green, color.greenSoft],
    stats: ["Статистика", color.orange, color.orangeSoft],
    ai: ["AI BORIS", color.purple, color.purpleSoft],
    integration: ["Интеграция", color.blue, color.blueSoft],
  };
  const [label, c, bg] = map[kind] || map.rule;
  return (
    <span style={{ fontSize: 11.5, fontWeight: 700, padding: "2px 8px",
                   borderRadius: radius.pill, color: c, background: bg }}>
      {label}
    </span>
  );
}

export function EcKpiRow({ items }: { items: { label: string; value: any; tone?: string }[] }) {
  const tones: Record<string, string> = {
    red: color.red, green: color.green, orange: color.orange, neutral: color.heading,
  };
  return (
    <div className="boris-ec-kpi-row" style={{ display: "grid", gap: space.md, marginBottom: space.xxl,
                  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
      {items.map((k) => (
        <div className="boris-ec-kpi-card" key={k.label} style={{ background: color.surface, padding: space.xl,
                                    border: `1px solid ${color.line}`,
                                    borderRadius: radius.lg }}>
          <div style={{ ...font.small, marginBottom: 8 }}>{k.label}</div>
          <div style={{ ...font.kpi, color: tones[k.tone || "neutral"] }}>
            {typeof k.value === "number" ? k.value.toLocaleString("ru-RU") : k.value}
          </div>
        </div>
      ))}
    </div>
  );
}

export function EcTabs({ items, active, onSelect }: any) {
  return (
    <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${color.line}`,
                  marginBottom: space.xl, overflowX: "auto" }}>
      {items.map((it: any) => {
        const on = it.key === active;
        return (
          <button key={it.key} onClick={() => onSelect(it.key)}
            style={{ background: "none", border: "none", cursor: "pointer",
                     padding: "10px 14px", whiteSpace: "nowrap",
                     borderBottom: `2px solid ${on ? color.blue : "transparent"}`,
                     color: on ? color.heading : color.text,
                     fontWeight: on ? 700 : 400, fontSize: 13.5 }}>
            {it.label}
            {typeof it.count === "number" && (
              <span style={{ marginLeft: 6, color: color.muted, fontSize: 12 }}>
                {it.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export const TYPE_RU: Record<string, string> = {
  real_photo: "Фото", ai_generated: "AI", banner: "Баннер", stock: "Сток",
  render: "Рендер", drawing: "Схема", other: "Прочее",
};

export function MediaCard({ item, selected, onToggle, onOpen }: any) {
  const missing = item.file_state === "missing";
  const imageUrl = item.thumb_url || item.preview_url || item.original_url || "";
  return (
    <div data-boris-g32b-ec="media"
      style={{ position: "relative", border: `1px solid ${selected ? color.blue : color.line}`,
               borderRadius: radius.md, overflow: "hidden",
               background: color.surface, boxShadow: selected ? shadow.card : "none" }}>
      <button type="button" aria-label={`Открыть ${item.filename || "изображение"}`}
        onClick={() => onOpen && onOpen(item)}
        style={{ width: "100%", padding: 0, border: 0, background: "transparent", cursor: "zoom-in", display: "block" }}>
        <div style={{ aspectRatio: "1 / 1", background: color.surfaceAlt,
                      display: "flex", alignItems: "center", justifyContent: "center" }}>
          {missing ? (
            <div style={{ ...font.small, textAlign: "center", padding: space.md }}>
              Файл недоступен
            </div>
          ) : imageUrl ? (
            <img src={imageUrl} alt={item.filename || ""} loading="lazy"
              className="boris-f3-5f3c4a87923439" />
          ) : (
            <div style={{ width: "100%", height: "100%",
                          background: `linear-gradient(90deg, ${color.surfaceAlt}, ${color.line}, ${color.surfaceAlt})` }} />
          )}
        </div>
      </button>
      <div className="boris-f3-3450ea9893e0c3">
        <div title={item.display_name || item.filename || ""} style={{ ...font.small, color: color.heading, fontWeight:700, overflow: "hidden",
                      textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {item.display_name || item.filename || "Материал"}
        </div>
        <div style={{ ...font.small, fontSize: 11.5, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>
          {item.display_context || TYPE_RU[item.media_type] || item.media_type}
        </div>
      </div>
      <button type="button" aria-label={selected ? "Убрать из выбранных" : "Выбрать изображение"}
        aria-pressed={selected}
        onClick={(e) => { e.stopPropagation(); onToggle && onToggle(item.id); }}
        style={{ position: "absolute", top: 8, left: 8, width: 30, height: 30,
                 border: `1px solid ${selected ? color.blue : "rgba(255,255,255,.82)"}`,
                 borderRadius: 9, background: selected ? color.blue : "rgba(255,255,255,.94)",
                 color: selected ? "#fff" : color.heading, cursor: "pointer", fontWeight: 800,
                 boxShadow: "0 4px 12px rgba(15,23,42,.12)" }}>
        {selected ? "✓" : "+"}
      </button>
    </div>
  );
}

export function FloatingActionBar({ count, actions, onClear }: any) {
  if (!count) return null;
  return (
    <div style={{ position: "fixed", left: "50%", bottom: 24, transform: "translateX(-50%)",
                  background: color.heading, color: "#fff", borderRadius: radius.pill,
                  padding: "10px 18px", display: "flex", gap: space.lg,
                  alignItems: "center", boxShadow: shadow.pop, zIndex: 50 }}>
      <span className="boris-f3-5fe67f35064d23">Выбрано: {count}</span>
      {actions.map((a: any) => (
        <button key={a.label} onClick={a.onClick}
          style={{ background: "none", border: "none", color: a.danger ? "#FF9AA6" : "#fff",
                   cursor: "pointer", fontSize: 13.5 }}>{a.label}</button>
      ))}
      <button onClick={onClear} style={{ background: "none", border: "none",
              color: color.muted, cursor: "pointer", fontSize: 13.5 }}>Снять</button>
    </div>
  );
}

export function ProductCard({ p, onOpen }: any) {
  return (
    <div data-boris-g32b-ec="product" onClick={() => onOpen && onOpen(p)}
      style={{ border: `1px solid ${color.line}`, borderRadius: radius.md,
               padding: space.lg, background: color.surface, cursor: "pointer" }}>
      <div style={{ ...font.bodyStrong }}>{p.name}</div>
      <div style={{ ...font.small, marginTop: 2 }}>
        {[p.sku, p.category].filter(Boolean).join(" · ") || "без артикула"}
      </div>
      <div style={{ display: "flex", gap: space.md, marginTop: space.md,
                    alignItems: "center" }}>
        <span style={{ ...font.bodyStrong }}>
          {p.price ? p.price.toLocaleString("ru-RU") + " ₽" : "цена не указана"}
        </span>
        <span style={{ fontSize: 11.5, fontWeight: 700, padding: "2px 8px",
                       borderRadius: radius.pill,
                       color: p.ready_for_ads ? color.green : color.orange,
                       background: p.ready_for_ads ? color.greenSoft : color.orangeSoft }}>
          {p.ready_for_ads ? "готов к объявлениям" : "нужны данные"}
        </span>
      </div>
    </div>
  );
}
