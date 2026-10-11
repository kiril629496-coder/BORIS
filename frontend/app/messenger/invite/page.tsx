"use client";

import { useEffect, useState } from "react";

export default function MessengerInvitePage() {
  const [token, setToken] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState("Проверяем ссылку приглашения…");
  const [loading, setLoading] = useState(false);
  const [invalid, setInvalid] = useState(false);

  useEffect(() => {
    const value = new URLSearchParams(window.location.search).get("token") || "";
    setToken(value);
    if (!value) {
      setInvalid(true);
      setStatus("В ссылке нет токена приглашения.");
    } else {
      setStatus("Укажите данные для входа в BORIS Messenger.");
    }
  }, []);

  const acceptInvite = async () => {
    if (!token || loading) return;
    if (!email.trim() || !email.includes("@")) {
      setStatus("Укажите рабочий email.");
      return;
    }
    if (password.length < 8) {
      setStatus("Пароль должен содержать не менее 8 символов.");
      return;
    }
    setLoading(true);
    try {
      const response = await fetch("/api/team/invite/accept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, email: email.trim(), password }),
      });
      const raw = await response.text();
      let data: any = {};
      try { data = raw ? JSON.parse(raw) : {}; } catch {}
      if (!response.ok || data.status !== "ok") {
        throw new Error(data?.detail?.message || data?.detail || data?.message || "Приглашение не принято");
      }
      const accountId = String(data.account_id || "");
      localStorage.setItem("boris_post_login_return", accountId ? "/messenger?account_id=" + encodeURIComponent(accountId) + "&onboarding=1" : "/messenger?onboarding=1");
      localStorage.setItem("boris_login_note", "Приглашение принято. Войдите с указанным email и паролем — BORIS поможет установить приложение и включить уведомления.");
      window.location.replace("/login");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setInvalid(/ист[её]к|недейств|использован|revok/i.test(message));
      setStatus(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24, background: "linear-gradient(180deg,#eef5ff,#f8fbff)", color: "#0f172a", fontFamily: "Inter,system-ui,sans-serif" }}>
      <section style={{ width: "min(480px,100%)", background: "#fff", border: "1px solid #dbe5f1", borderRadius: 24, padding: 30, boxShadow: "0 24px 64px rgba(15,23,42,.09)" }}>
        <div style={{ width: 54, height: 54, display: "grid", placeItems: "center", borderRadius: 17, color: "#fff", background: "linear-gradient(145deg,#2563eb,#1d4ed8)", fontWeight: 900, fontSize: 24 }}>B</div>
        <h1 style={{ margin: "22px 0 8px", fontSize: 28, letterSpacing: "-.03em" }}>Вход в команду BORIS</h1>
        <p style={{ margin: "0 0 22px", color: "#64748b", lineHeight: 1.55 }}>{status}</p>
        {!invalid && token ? (
          <div style={{ display: "grid", gap: 12 }}>
            <label style={{ display: "grid", gap: 6, fontWeight: 700 }}>Email
              <input value={email} onChange={event => setEmail(event.target.value)} type="email" autoComplete="email" placeholder="name@company.ru" style={{ minHeight: 46, border: "1px solid #d7e2ef", borderRadius: 11, padding: "0 12px", fontSize: 16 }} />
            </label>
            <label style={{ display: "grid", gap: 6, fontWeight: 700 }}>Пароль
              <input value={password} onChange={event => setPassword(event.target.value)} type="password" autoComplete="new-password" placeholder="Не менее 8 символов" style={{ minHeight: 46, border: "1px solid #d7e2ef", borderRadius: 11, padding: "0 12px", fontSize: 16 }} />
            </label>
            <button type="button" disabled={loading} onClick={() => void acceptInvite()} style={{ minHeight: 48, marginTop: 6, border: 0, borderRadius: 12, color: "#fff", background: "#1d4ed8", fontWeight: 800, fontSize: 15, cursor: loading ? "wait" : "pointer", opacity: loading ? .65 : 1 }}>
              {loading ? "Принимаем приглашение…" : "Принять приглашение"}
            </button>
          </div>
        ) : (
          <button type="button" onClick={() => window.location.replace("/login")} style={{ minHeight: 48, width: "100%", border: 0, borderRadius: 12, color: "#fff", background: "#1d4ed8", fontWeight: 800, fontSize: 15, cursor: "pointer" }}>Перейти ко входу</button>
        )}
      </section>
    </main>
  );
}
