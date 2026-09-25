"use client";

import React from "react";
import { GUIDES } from "../lib/guides";

const BLUE = "var(--boris-blue)";

export default function SectionGuide({ section }: { section: string }) {
  const g = GUIDES[section];
  const lsKey = "boris_guide_seen:" + section;
  const [open, setOpen] = React.useState(false);
  const [tab, setTab] = React.useState<"owner" | "staff">("owner");
  const [ready, setReady] = React.useState(false);

  React.useEffect(() => {
    try {
      setOpen(!localStorage.getItem(lsKey));
    } catch {
      setOpen(false);
    }
    setReady(true);
  }, [lsKey]);

  if (!g || !ready) return null;

  function toggle() {
    const next = !open;
    setOpen(next);
    try {
      if (!next) localStorage.setItem(lsKey, "1");
    } catch {}
  }

  const steps = tab === "owner" ? g.ownerSteps : g.staffSteps;

  return (
    <div style={{
      border: "1px solid var(--boris-border)", borderRadius: 14, background: "#FFFFFF",
      marginBottom: 16, overflow: "hidden",
      boxShadow: open ? "0 8px 28px rgba(52,128,254,0.10)" : "none",
      transition: "box-shadow .2s",
    }}>
      <div onClick={toggle} style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: open ? "16px 18px 8px" : "12px 18px", cursor: "pointer",
      }}>
        <div style={{
          width: 34, height: 34, borderRadius: 10, flexShrink: 0,
          background: "linear-gradient(135deg," + BLUE + ",#7AA9FF)",
          display: "flex", alignItems: "center", justifyContent: "center",
          color: "#fff", fontSize: 17,
        }}>💡</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 15, color: "var(--boris-text)" }}>
            {open ? g.title : "Как это работает"}
          </div>
          <div style={{
            fontSize: 13, color: "var(--boris-text-2)", marginTop: 2,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>{g.lead}</div>
        </div>
        <div style={{ fontSize: 13, color: BLUE, fontWeight: 600, flexShrink: 0 }}>
          {open ? "Свернуть" : "Показать"}
        </div>
      </div>

      {open && (
        <div style={{ padding: "4px 18px 18px 64px" }}>
          <div style={{ fontSize: 14, color: "var(--boris-text-2)", lineHeight: 1.55, marginBottom: 14 }}>
            {g.why}
          </div>

          <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
            {(["owner", "staff"] as const).map(function (k) {
              const on = tab === k;
              return (
                <button key={k} onClick={() => setTab(k)} style={{
                  border: "1px solid " + (on ? BLUE : "var(--boris-border)"),
                  background: on ? BLUE : "#FFFFFF",
                  color: on ? "#FFFFFF" : "var(--boris-text-2)",
                  borderRadius: 8, padding: "6px 14px", fontSize: 13,
                  fontWeight: 600, cursor: "pointer",
                }}>{k === "owner" ? "Владельцу" : "Сотруднику"}</button>
              );
            })}
          </div>

          <ol style={{ margin: "0 0 14px", paddingLeft: 0, listStyle: "none" }}>
            {steps.map(function (s, i) {
              return (
                <li key={i} style={{ display: "flex", gap: 10, marginBottom: 8 }}>
                  <span style={{
                    width: 22, height: 22, borderRadius: 999, flexShrink: 0,
                    background: "var(--boris-blue-soft)", color: BLUE, fontSize: 12, fontWeight: 700,
                    display: "flex", alignItems: "center", justifyContent: "center",
                  }}>{i + 1}</span>
                  <span style={{ fontSize: 14, color: "var(--boris-text-2)", lineHeight: 1.5 }}>{s}</span>
                </li>
              );
            })}
          </ol>

          <div style={{
            background: "#F6FAFF", borderLeft: "3px solid " + BLUE,
            borderRadius: 8, padding: "10px 14px", fontSize: 14,
            color: "var(--boris-text)", marginBottom: 12,
          }}>
            <b>Что получится:</b> {g.result}
          </div>

          {g.mistakes.length > 0 && (
            <div style={{ fontSize: 13, color: "var(--boris-text-2)", lineHeight: 1.6 }}>
              <div style={{ fontWeight: 600, color: "var(--boris-text-2)", marginBottom: 4 }}>
                Чего лучше не делать
              </div>
              {g.mistakes.map(function (m, i) {
                return <div key={i}>— {m}</div>;
              })}
            </div>
          )}

          {g.video && (
            <video src={g.video} controls style={{
              width: "100%", borderRadius: 10, marginTop: 14, background: "#000",
            }} />
          )}
        </div>
      )}
    </div>
  );
}
