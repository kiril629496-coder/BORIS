"use client";

import { useEffect, useState } from "react";

const S: any = {
  page: { maxWidth: 720, margin: "0 auto", padding: 24, fontFamily: "system-ui, sans-serif", color: "#1B2437" },
  card: { background: "#fff", border: "1px solid #E3E7EF", borderRadius: 18, padding: 22, boxShadow: "0 8px 32px rgba(21,32,56,.06)" },
  h1: { fontSize: 26, fontWeight: 800, margin: "0 0 6px" },
  sub: { color: "#657083", fontSize: 14, lineHeight: 1.55, marginBottom: 20 },
  label: { fontSize: 13, fontWeight: 700, display: "block", marginBottom: 7 },
  input: { width: "100%", boxSizing: "border-box", border: "1px solid #D6DCE8", borderRadius: 12, padding: "12px 14px", fontSize: 15 },
  row: { display: "flex", gap: 10, flexWrap: "wrap", marginTop: 14 },
  btn: { border: 0, borderRadius: 11, padding: "11px 17px", background: "#2F6BFF", color: "#fff", fontWeight: 700, cursor: "pointer" },
  ghost: { border: "1px solid #2F6BFF", borderRadius: 11, padding: "10px 16px", background: "#fff", color: "#2F6BFF", fontWeight: 700, cursor: "pointer" },
  ok: { marginTop: 16, padding: 13, borderRadius: 12, background: "#EAF7EE", color: "#14663A", border: "1px solid #B5E0C1" },
  wait: { marginTop: 16, padding: 13, borderRadius: 12, background: "#FFF7E2", color: "#7A5412", border: "1px solid #F1D38A" },
  err: { marginTop: 16, padding: 13, borderRadius: 12, background: "#FDECEC", color: "#9B1C1C", border: "1px solid #F1B4B4" },
  meta: { marginTop: 18, fontSize: 13, color: "#657083", lineHeight: 1.6 },
};

async function api(path: string, method = "GET", body?: any) {
  const token = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: any = {};
  try { data = await res.json(); } catch {}
  if (res.status === 401 && typeof window !== "undefined") window.location.href = "/login";
  return { code: res.status, data };
}

export default function PlusVibeSetupPage() {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<any>(null);
  const [msg, setMsg] = useState<any>(null);

  async function load() {
    const r = await api("/api/admin/clients/plusvibe/status");
    if (r.code === 200) setStatus(r.data);
  }
  useEffect(() => { load(); }, []);

  async function save() {
    const v = key.trim();
    if (!v) { setMsg({ kind: "err", text: "Вставьте ключ PlusVibe." }); return; }
    setBusy(true); setMsg(null);
    const r = await api("/api/admin/clients/plusvibe/key", "POST", { key: v });
    setKey("");
    setBusy(false);
    if (r.code !== 200) {
      const d = r.data?.detail;
      setMsg({ kind: "err", text: typeof d === "string" ? d : "Не удалось сохранить ключ." });
      return;
    }
    setStatus({ configured: true, model: r.data.model });
    setMsg({
      kind: r.data.live_test === "pass" ? "ok" : "wait",
      text: r.data.live_test === "pass"
        ? "Готово: ключ сохранён, живой запрос MiniMax прошёл."
        : "Ключ сохранён. " + (r.data.message || "Нужно активировать бесплатную модель в PlusVibe."),
    });
  }

  async function test() {
    setBusy(true); setMsg(null);
    const r = await api("/api/admin/clients/plusvibe/test", "POST");
    setBusy(false);
    if (r.code !== 200) {
      setMsg({ kind: "err", text: r.data?.detail || "Проверка не удалась." });
      return;
    }
    setMsg({
      kind: r.data.live_test === "pass" ? "ok" : "wait",
      text: r.data.message || "Проверка завершена.",
    });
  }

  const box = msg?.kind === "ok" ? S.ok : msg?.kind === "wait" ? S.wait : S.err;

  return <main style={S.page}>
    <div style={S.card}>
      <h1 style={S.h1}>PlusVibe для BORIS</h1>
      <div style={S.sub}>
        Защищённая настройка для владельца. Ключ отправляется только на ваш BORIS-сервер по HTTPS,
        записывается в закрытый .env и больше не показывается в интерфейсе. В localStorage ключ не сохраняется.
      </div>

      <label style={S.label}>API-ключ PlusVibe</label>
      <input
        type="password"
        value={key}
        onChange={(e) => setKey(e.target.value)}
        placeholder="sk-pv-…"
        autoComplete="off"
        spellCheck={false}
        style={S.input}
      />

      <div style={S.row}>
        <button style={S.btn} disabled={busy} onClick={save}>
          {busy ? "Проверяю…" : "Сохранить и проверить"}
        </button>
        <button style={S.ghost} disabled={busy || !status?.configured} onClick={test}>
          Повторить живой тест
        </button>
      </div>

      {msg && <div style={box}>{msg.text}</div>}

      <div style={S.meta}>
        <div><b>Состояние:</b> {status?.configured ? "ключ настроен" : "ключ ещё не настроен"}</div>
        <div><b>Модель:</b> {status?.model || "minimax-m2.7:free"}</div>
        <div><b>Роутинг продаж:</b> PlusVibe/MiniMax M2.7 — основной бесплатный маршрут; прямой OpenAI для МОП/РОП временно выключается.</div>
        <div><b>Баннеры и изображения:</b> прямой OpenAI остаётся отдельным контуром и этой настройкой не удаляется.</div>
      </div>
    </div>
  </main>;
}
