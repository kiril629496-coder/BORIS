"use client";

/**
 * Поддержка глазами клиента.
 *
 * Бэкенд это умел с самого начала: /api/support/my отдаёт свои обращения
 * с перепиской, /api/support/message создаёт новое, /api/support/hours —
 * график работы. Не хватало только экрана: страница поддержки была написана
 * под админские ручки, и клиент видел «Доступ запрещён».
 */
import React from "react";

const BLUE = "var(--boris-blue)";
const GREY = "var(--boris-text-2)";
const LINE = "var(--boris-border)";

const card: React.CSSProperties = {
  background: "#FFFFFF", border: "1px solid " + LINE, borderRadius: 14,
  padding: "20px 22px", marginBottom: 16,
};

function auth(): Record<string, string> | null {
  if (typeof window === "undefined") return null;
  const t = localStorage.getItem("boris_token");
  return t ? { Authorization: "Bearer " + t } : null;
}

export default function ClientSupport() {
  const [hours, setHours] = React.useState<any>(null);
  const [tickets, setTickets] = React.useState<any[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [text, setText] = React.useState("");
  const [contact, setContact] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [sent, setSent] = React.useState("");
  const [installPrompt, setInstallPrompt] = React.useState<any>(null);
  const [installed, setInstalled] = React.useState(false);

  const load = React.useCallback(function () {
    const h = auth();
    if (!h) { setLoading(false); return; }
    setLoading(true);
    setError("");
    Promise.all([
      fetch("/api/support/hours", { headers: h }).then(function (r) { return r.json(); }),
      fetch("/api/support/my", { headers: h, cache: "no-store" }).then(function (r) { return r.json(); }),
    ])
      .then(function (res) {
        setHours(res[0] || null);
        setTickets((res[1] && res[1]["обращения"]) || []);
      })
      .catch(function () { setError("Не удалось загрузить обращения"); })
      .finally(function () { setLoading(false); });
  }, []);

  React.useEffect(function () { load(); }, [load]);

  React.useEffect(function () {
    const standalone = window.matchMedia("(display-mode: standalone)").matches || (window.navigator as any).standalone === true;
    setInstalled(standalone);
    const onPrompt = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event);
    };
    const onInstalled = () => { setInstalled(true); setInstallPrompt(null); };
    window.addEventListener("beforeinstallprompt", onPrompt as EventListener);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt as EventListener);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  async function send() {
    if (!text.trim()) { setError("Напишите, что случилось"); return; }
    const h = auth();
    if (!h) return;
    setBusy(true);
    setError("");
    try {
      const r = await fetch("/api/support/message", {
        method: "POST",
        headers: { ...h, "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.trim(), contact: contact.trim() }),
      });
      const d = await r.json();
      if (d && d.status === "ok") {
        setText("");
        setSent("Обращение отправлено. Ответ придёт сюда и на вашу почту.");
        load();
      } else {
        setError((d && d.message) || "Не удалось отправить обращение");
      }
    } catch (e) {
      setError("Не удалось связаться с сервером");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ maxWidth: 820, margin: "0 auto", padding: "24px 16px 60px",
                  fontFamily: "var(--font-inter), Arial, Helvetica, sans-serif", color: "var(--boris-text)" }}>
      <h1 style={{ fontSize: 24, margin: "0 0 6px" }}>Поддержка BORIS</h1>
      <p style={{ color: GREY, fontSize: 15, margin: "0 0 20px" }}>
        Опишите вопрос — мы ответим здесь же и продублируем на почту.
        {hours && hours["часы"] ? " Работаем " + hours["часы"] + "." : ""}
        {hours && hours["рабочее_время"] === false ? " Сейчас нерабочее время, ответим утром." : ""}
      </p>

      <div style={{ ...card, background: "linear-gradient(135deg,#f7faff,#ffffff)", borderColor: "#d7e4fb" }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <div style={{ minWidth: 0, flex: "1 1 420px" }}>
            <div style={{ fontWeight: 800, fontSize: 17, marginBottom: 6 }}>Добавить BORIS на экран телефона</div>
            <div style={{ color: GREY, fontSize: 14, lineHeight: 1.55 }}>
              BORIS можно открыть как отдельное приложение без поиска сайта в браузере. Иконка появится на рабочем столе телефона.
            </div>
          </div>
          {installed ? (
            <span style={{ borderRadius: 999, padding: "8px 12px", background: "#ecfdf3", color: "#067647", fontSize: 13, fontWeight: 700 }}>Уже установлено</span>
          ) : installPrompt ? (
            <button onClick={async function () { const p = installPrompt as any; await p.prompt(); try { await p.userChoice; } catch {} setInstallPrompt(null); }}
                    style={{ border: 0, borderRadius: 10, padding: "10px 16px", background: BLUE, color: "#fff", fontWeight: 750, fontSize: 14, cursor: "pointer" }}>
              Установить BORIS
            </button>
          ) : null}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: 12, marginTop: 16 }}>
          <div style={{ border: "1px solid " + LINE, borderRadius: 12, padding: 14, background: "#fff" }}>
            <div style={{ fontWeight: 750, marginBottom: 7 }}>iPhone / iPad</div>
            <div style={{ color: GREY, fontSize: 13.5, lineHeight: 1.55 }}>Откройте BORIS в Safari → нажмите «Поделиться» → «На экран Домой» → «Добавить».</div>
          </div>
          <div style={{ border: "1px solid " + LINE, borderRadius: 12, padding: 14, background: "#fff" }}>
            <div style={{ fontWeight: 750, marginBottom: 7 }}>Android</div>
            <div style={{ color: GREY, fontSize: 13.5, lineHeight: 1.55 }}>Откройте BORIS в Chrome → меню ⋮ → «Установить приложение» или «Добавить на главный экран» → подтвердите.</div>
          </div>
        </div>
      </div>

      <div style={card}>
        <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 12 }}>Новое обращение</div>
        <textarea value={text} onChange={function (e) { setText(e.target.value); }}
                  rows={5} placeholder="Что случилось? Чем подробнее, тем быстрее разберёмся."
                  style={{ width: "100%", border: "1px solid " + LINE, borderRadius: 10,
                           padding: "11px 13px", fontSize: 15, boxSizing: "border-box",
                           resize: "vertical", marginBottom: 12 }} />
        <input value={contact} onChange={function (e) { setContact(e.target.value); }}
               placeholder="Телефон или Telegram — необязательно"
               style={{ width: "100%", border: "1px solid " + LINE, borderRadius: 10,
                        padding: "11px 13px", fontSize: 15, boxSizing: "border-box", marginBottom: 14 }} />
        {error ? <div style={{ color: "#D92D20", fontSize: 14, marginBottom: 12 }}>{error}</div> : null}
        {sent ? <div style={{ color: "#067647", fontSize: 14, marginBottom: 12 }}>{sent}</div> : null}
        <button onClick={send} disabled={busy}
                style={{ background: busy ? "var(--boris-border)" : BLUE, color: busy ? GREY : "#FFFFFF",
                         border: "none", borderRadius: 10, padding: "12px 26px",
                         fontWeight: 700, fontSize: 15, cursor: busy ? "wait" : "pointer" }}>
          {busy ? "Отправляю…" : "Отправить"}
        </button>
      </div>

      <div style={{ fontWeight: 700, fontSize: 16, margin: "24px 0 12px" }}>Мои обращения</div>

      {loading ? <div style={{ color: GREY, fontSize: 15 }}>Загружаю…</div> : null}

      {!loading && tickets.length === 0 ? (
        <div style={{ ...card, color: GREY, fontSize: 15 }}>
          Обращений пока нет. Напишите первое — мы на связи.
        </div>
      ) : null}

      {tickets.map(function (t: any) {
        return (
          <div key={t["номер"]} style={card}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                          marginBottom: 8, gap: 12, flexWrap: "wrap" }}>
              <div style={{ fontWeight: 700, fontSize: 15 }}>Обращение №{t["номер"]}</div>
              <span style={{ background: "var(--boris-border-soft)", color: GREY, borderRadius: 999,
                             padding: "3px 12px", fontSize: 13 }}>{t["статус"]}</span>
            </div>
            <div style={{ fontSize: 15, whiteSpace: "pre-wrap", marginBottom: 10 }}>{t["текст"]}</div>
            {(t["ответы"] || []).map(function (r: any, i: number) {
              const mine = r["кто"] === "вы";
              return (
                <div key={i} style={{ borderTop: "1px solid " + LINE, paddingTop: 10, marginTop: 10 }}>
                  <div style={{ fontSize: 13, color: mine ? GREY : BLUE, fontWeight: 600, marginBottom: 4 }}>
                    {mine ? "Вы" : "Поддержка BORIS"}
                  </div>
                  <div style={{ fontSize: 15, whiteSpace: "pre-wrap" }}>{r["текст"]}</div>
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
