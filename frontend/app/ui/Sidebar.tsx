"use client";

/**
 * Общее левое меню BORIS AI.
 * Поведение перенесено из монолита один в один:
 *   пункт с href  -> переход по адресу;
 *   пункт без href -> onSelect(key) (переключение вкладки внутри кабинета);
 *   guideKey       -> подсветка пункта обучалкой.
 */
import React from "react";
import { Icon } from "./index";
import { color, radius, space, font, layout } from "./tokens";
import { MENU } from "./menu";

export function Sidebar({ activeKey, onSelect, guideKey, mobileOpen, onCloseMobile }:
  { activeKey?: string; onSelect?: (key: string) => void; guideKey?: string;
    mobileOpen?: boolean; onCloseMobile?: () => void }) {

  function go(item: { key: string; href?: string }) {
    if (item.href) { window.location.href = item.href; return; }
    if (onSelect) { onSelect(item.key); }
    else { window.location.href = "/dashboard?tab=" + encodeURIComponent(item.key); }
    if (onCloseMobile) onCloseMobile();
  }

  return (
    <aside style={{
      width: layout.sidebar, flex: "0 0 " + layout.sidebar + "px",
      background: color.surface, borderRight: "1px solid " + color.line,
      padding: space.lg, boxSizing: "border-box", overflowY: "auto",
      position: mobileOpen === undefined ? "relative" : "fixed",
      top: 0, bottom: 0, left: mobileOpen === false ? -layout.sidebar - 8 : 0,
      zIndex: mobileOpen === undefined ? "auto" : 60,
      transition: "left .18s ease",
    }}>
      <div style={{ ...font.h3, marginBottom: space.lg, paddingLeft: 6 }}>
        BORIS <span style={{ color: color.blue }}>AI</span>
      </div>

      {MENU.map((section) => (
        <div key={section.group} style={{ marginBottom: space.lg }}>
          <div style={{ ...font.micro, color: color.muted, letterSpacing: "0.08em",
                        textTransform: "uppercase", marginBottom: space.sm, paddingLeft: 6 }}>
            {section.group}
          </div>
          {section.items.map((item) => {
            const isActive = activeKey === item.key;
            const isGuide = !!guideKey && guideKey === item.key;
            return (
              <button key={item.key} onClick={() => go(item)} title={item.label}
                style={{
                  display: "flex", alignItems: "center", gap: space.md, width: "100%",
                  textAlign: "left", cursor: "pointer",
                  padding: "9px 10px", marginBottom: 2, borderRadius: radius.sm,
                  background: isGuide ? color.greenSoft : isActive ? color.blueSoft : "transparent",
                  border: "1px solid " + (isGuide ? color.green : "transparent"),
                  color: isActive ? color.blue : color.heading,
                  fontSize: 13.5, fontWeight: isActive ? 600 : 500,
                }}>
                <Icon name={item.icon} size={18}
                      tone={isActive ? "accent" : isGuide ? "success" : "muted"} />
                <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                               whiteSpace: "nowrap" }}>{item.label}</span>
              </button>
            );
          })}
        </div>
      ))}
    </aside>
  );
}

/** Обёртка экрана: меню слева, содержимое справа. Для страниц вне монолита. */
export function Shell({ activeKey, children }:
  { activeKey?: string; children: React.ReactNode }) {
  const [open, setOpen] = React.useState(false);
  const [narrow, setNarrow] = React.useState(false);

  React.useEffect(() => {
    const check = () => setNarrow(window.innerWidth < 900);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: color.bg }}>
      {narrow ? (
        <>
          <button onClick={() => setOpen(!open)} aria-label="Меню"
                  style={{ position: "fixed", top: 14, left: 14, zIndex: 70,
                           background: color.surface, border: "1px solid " + color.line,
                           borderRadius: radius.sm, padding: "8px 10px", cursor: "pointer" }}>
            <Icon name={open ? "close" : "layers"} size={18} />
          </button>
          {open ? (
            <div onClick={() => setOpen(false)}
                 style={{ position: "fixed", inset: 0, background: "rgba(11,16,32,0.35)", zIndex: 50 }} />
          ) : null}
          <Sidebar activeKey={activeKey} mobileOpen={open} onCloseMobile={() => setOpen(false)} />
        </>
      ) : (
        <Sidebar activeKey={activeKey} />
      )}
      <div style={{ flex: 1, minWidth: 0 }}>{children}</div>
    </div>
  );
}
