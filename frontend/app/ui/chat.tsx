"use client";

/**
 * Компоненты рабочего центра сообщений.
 * Добавлены в библиотеку, потому что нужны и в инбоксе, и в будущих
 * экранах МОПа и РОПа: списки, переписка, поле ответа.
 */
import React from "react";
import { Icon, Badge, Card } from "./index";
import { color, radius, space, font, shadow } from "./tokens";

/** Три колонки рабочего центра. На узком экране — по одной с возвратом. */
export function SplitLayout({ left, middle, right, narrow, step, onBack }:
  { left: React.ReactNode; middle: React.ReactNode; right: React.ReactNode;
    narrow?: boolean; step?: 0 | 1 | 2; onBack?: () => void }) {
  if (narrow) {
    const pane = step === 2 ? right : step === 1 ? middle : left;
    return (
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        {step ? (
          <button onClick={onBack} style={{
            display: "flex", alignItems: "center", gap: space.sm, cursor: "pointer",
            background: "transparent", border: "none", padding: space.md,
            ...font.bodyStrong, color: color.blue,
          }}>
            <Icon name="arrow" size={16} tone="accent" style={{ transform: "rotate(180deg)" }} />
            Назад
          </button>
        ) : null}
        <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>{pane}</div>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", gap: space.md, alignItems: "stretch",
                  height: "calc(100vh - 190px)", minHeight: 460 }}>
      <div style={{ flex: "0 0 268px", minWidth: 0, display: "flex" }}>{left}</div>
      <div style={{ flex: "0 0 340px", minWidth: 0, display: "flex" }}>{middle}</div>
      <div style={{ flex: 1, minWidth: 0, display: "flex" }}>{right}</div>
    </div>
  );
}

/** Колонка со своим заголовком и прокруткой содержимого. */
export function Pane({ title, action, children }:
  { title?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <Card padding={0} style={{ display: "flex", flexDirection: "column",
                               width: "100%", overflow: "hidden" }}>
      {title ? (
        <div style={{ display: "flex", alignItems: "center", gap: space.sm,
                      padding: "13px 16px", borderBottom: "1px solid " + color.line }}>
          <div style={{ ...font.h3, flex: 1, minWidth: 0, overflow: "hidden",
                        textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</div>
          {action}
        </div>
      ) : null}
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: space.sm }}>
        {children}
      </div>
    </Card>
  );
}

/** Строка списка: аккаунт или диалог. */
export function ListItem({ title, subtitle, meta, count, active, danger, dot, onClick }:
  { title: React.ReactNode; subtitle?: React.ReactNode; meta?: string;
    count?: number; active?: boolean; danger?: boolean; dot?: string;
    onClick?: () => void }) {
  return (
    <button onClick={onClick} title={typeof title === "string" ? title : undefined}
      style={{
        display: "flex", alignItems: "center", gap: space.md, width: "100%",
        textAlign: "left", cursor: "pointer", marginBottom: 2,
        padding: "10px 12px", borderRadius: radius.sm,
        background: active ? color.blueSoft : "transparent",
        border: "1px solid " + (active ? color.blue : "transparent"),
      }}>
      {dot ? (
        <span style={{ width: 8, height: 8, borderRadius: radius.pill,
                       background: dot, flex: "0 0 8px" }} />
      ) : null}
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ ...font.bodyStrong, color: active ? color.blue : color.heading,
                       display: "block", overflow: "hidden",
                       textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
        {subtitle ? (
          <span style={{ ...font.small, display: "block", overflow: "hidden",
                         textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{subtitle}</span>
        ) : null}
      </span>
      {meta ? <span style={{ ...font.small, flex: "0 0 auto" }}>{meta}</span> : null}
      {count ? <Badge kind={danger ? "danger" : "info"}>{count}</Badge> : null}
    </button>
  );
}

/** Пузырь сообщения. */
export function Bubble({ text, outgoing, time, fresh, system }:
  { text: React.ReactNode; outgoing?: boolean; time?: string;
    fresh?: boolean; system?: boolean }) {
  if (system) {
    return (
      <div style={{ ...font.small, textAlign: "center", padding: "6px 0" }}>{text}</div>
    );
  }
  return (
    <div style={{ display: "flex", justifyContent: outgoing ? "flex-end" : "flex-start",
                  marginBottom: space.sm }}>
      <div style={{
        maxWidth: "76%", padding: "10px 14px", borderRadius: radius.md,
        background: outgoing ? color.blueSoft : color.surfaceAlt,
        color: color.heading, fontSize: 13.5, lineHeight: 1.5,
        whiteSpace: "pre-wrap", wordBreak: "break-word",
        boxShadow: shadow.none,
      }}>
        {text}
        {(time || fresh) ? (
          <div style={{ ...font.small, marginTop: 4, display: "flex",
                        gap: space.sm, justifyContent: "flex-end" }}>
            {fresh ? <span style={{ color: color.red, fontWeight: 700 }}>новое</span> : null}
            {time ? <span>{time}</span> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** Поле ответа с кнопкой отправки. */
export function Composer({ value, onChange, onSend, sending, disabled, placeholder }:
  { value: string; onChange: (v: string) => void; onSend: () => void;
    sending?: boolean; disabled?: boolean; placeholder?: string }) {
  return (
    <div style={{ borderTop: "1px solid " + color.line, padding: space.md,
                  display: "flex", gap: space.sm, alignItems: "flex-end" }}>
      <textarea value={value} disabled={disabled || sending}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          // как в старом экране: Enter отправляет, Shift+Enter переносит строку
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); }
        }}
        placeholder={placeholder || "Введите сообщение…"}
        style={{
          flex: 1, minHeight: 44, maxHeight: 140, resize: "vertical",
          border: "1px solid " + color.lineStrong, borderRadius: radius.sm,
          padding: "11px 13px", fontSize: 13.5, color: color.heading,
          background: color.surface, outline: "none", boxSizing: "border-box",
          fontFamily: "inherit",
        }} />
      <button onClick={onSend} disabled={disabled || sending || !value.trim()}
        style={{
          border: "none", borderRadius: radius.sm, cursor: "pointer",
          background: (disabled || sending || !value.trim()) ? color.lineStrong : color.blue,
          color: color.onAccent, padding: "12px 16px", fontSize: 13.5, fontWeight: 600,
          display: "flex", alignItems: "center", gap: 6,
        }}>
        <Icon name="send" size={16} tone="default"
              style={{ filter: "brightness(0) invert(1)" }} />
        {sending ? "Отправляю" : "Отправить"}
      </button>
    </div>
  );
}
