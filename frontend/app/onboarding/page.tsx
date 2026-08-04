"use client";

/**
 * Мастер первого запуска BORIS — два обязательных экрана.
 *
 * Экран 1: о компании. Экран 2: каналы продаж и ссылки.
 * Незавершённый мастер восстанавливается: current_step и form_data приходят
 * из /api/onboarding/status. Экрана «анализа» здесь пока нет — он появится,
 * когда будет утверждён справочник ниш.
 */
import React from "react";
import { useRouter } from "next/navigation";
import {
  completeOnboarding,
  describeOnboardingError,
  fetchOnboardingStatus,
  OnboardingStatus,
  OnboardingUnauthorized,
  ROUTE_CABINET,
  ROUTE_LOGIN,
  ROUTE_SCENARIOS,
  saveOnboardingStep,
} from "../lib/onboardingClient";

const CHANNELS: { key: string; label: string }[] = [
  { key: "avito", label: "Авито" },
  { key: "telegram", label: "Telegram" },
  { key: "vk", label: "VK" },
  { key: "website", label: "Сайт" },
  { key: "ozon", label: "Ozon" },
  { key: "wildberries", label: "Wildberries" },
  { key: "yandex_maps", label: "Яндекс Карты" },
  { key: "2gis", label: "2ГИС" },
  { key: "youtube", label: "YouTube" },
  { key: "other", label: "Другое" },
];

const wrap: React.CSSProperties = {
  minHeight: "100vh",
  background: "linear-gradient(160deg, #F6F7FB 0%, #E7EFFE 100%)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: "32px 16px",
  fontFamily: "var(--font-inter), Arial, Helvetica, sans-serif",
  color: "#14161A",
};
const card: React.CSSProperties = {
  background: "#FFFFFF",
  border: "1px solid #E3E7F0",
  borderRadius: 16,
  padding: "32px 34px",
  width: "100%",
  maxWidth: 560,
};
const label: React.CSSProperties = { display: "block", fontSize: 13, color: "#667085", marginBottom: 6 };
const input: React.CSSProperties = {
  width: "100%",
  border: "1px solid #E3E7F0",
  borderRadius: 10,
  padding: "11px 13px",
  fontSize: 15,
  marginBottom: 16,
  boxSizing: "border-box",
};
const primary: React.CSSProperties = {
  background: "#2F6FED",
  color: "#FFFFFF",
  border: "none",
  borderRadius: 10,
  padding: "12px 26px",
  fontWeight: 700,
  fontSize: 15,
  cursor: "pointer",
};
const ghost: React.CSSProperties = {
  background: "#FFFFFF",
  color: "#2F6FED",
  border: "1.5px solid #2F6FED",
  borderRadius: 10,
  padding: "12px 22px",
  fontWeight: 700,
  fontSize: 15,
  cursor: "pointer",
};

export default function OnboardingPage() {
  const router = useRouter();

  const [phase, setPhase] = React.useState<"loading" | "form" | "done" | "failed">("loading");
  const [step, setStep] = React.useState(1);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [attempt, setAttempt] = React.useState(0);

  const [companyName, setCompanyName] = React.useState("");
  const [niche, setNiche] = React.useState("");
  const [city, setCity] = React.useState("");
  const [website, setWebsite] = React.useState("");
  const [phone, setPhone] = React.useState("");
  const [channels, setChannels] = React.useState<string[]>([]);
  const [links, setLinks] = React.useState<Record<string, string>>({});

  function applyStatus(value: OnboardingStatus) {
    const form: any = value.form_data || {};
    setCompanyName(form.company_name || "");
    setNiche(form.company_niche || "");
    setCity(form.city || "");
    setWebsite(form.website || "");
    setPhone(form.phone || "");
    setChannels(Array.isArray(form.channels) ? form.channels : []);
    setLinks(form.channel_links && typeof form.channel_links === "object" ? form.channel_links : {});
  }

  React.useEffect(() => {
    let alive = true;
    setPhase("loading");
    setError("");

    fetchOnboardingStatus(true)
      .then(function (value) {
        if (!alive) return;
        if (value.status === "completed") {
          router.replace(ROUTE_SCENARIOS);
          return;
        }
        if (value.status === "skipped_existing_user") {
          router.replace(ROUTE_CABINET);
          return;
        }
        applyStatus(value);
        setStep(value.current_step >= 1 ? 2 : 1);
        setPhase("form");
      })
      .catch(function (err) {
        if (!alive) return;
        if (err instanceof OnboardingUnauthorized) {
          router.replace(ROUTE_LOGIN);
          return;
        }
        setError("Сервер не ответил. Проверьте связь и попробуйте ещё раз.");
        setPhase("failed");
      });

    return function () {
      alive = false;
    };
  }, [attempt, router]);

  function toggleChannel(key: string) {
    setChannels(function (prev) {
      if (prev.indexOf(key) >= 0) return prev.filter(function (k) { return k !== key; });
      return prev.concat([key]);
    });
  }

  async function submitStep1() {
    setError("");
    if (!companyName.trim() || !niche.trim() || !city.trim()) {
      setError("Заполните название компании, нишу и город.");
      return;
    }
    setBusy(true);
    try {
      await saveOnboardingStep(1, {
        company_name: companyName.trim(),
        company_niche: niche.trim(),
        city: city.trim(),
        website: website.trim(),
        phone: phone.trim(),
      });
      setStep(2);
    } catch (err) {
      if (err instanceof OnboardingUnauthorized) { router.replace(ROUTE_LOGIN); return; }
      setError(describeOnboardingError(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitStep2() {
    setError("");
    if (channels.length === 0) {
      setError("Отметьте хотя бы один канал продаж.");
      return;
    }
    setBusy(true);
    try {
      const cleanLinks: Record<string, string> = {};
      channels.forEach(function (key) {
        const v = (links[key] || "").trim();
        if (v) cleanLinks[key] = v;
      });
      await saveOnboardingStep(2, { channels: channels, channel_links: cleanLinks });
      await completeOnboarding();
      setPhase("done");
      setTimeout(function () { router.replace(ROUTE_SCENARIOS); }, 1400);
    } catch (err) {
      if (err instanceof OnboardingUnauthorized) { router.replace(ROUTE_LOGIN); return; }
      setError(describeOnboardingError(err));
    } finally {
      setBusy(false);
    }
  }

  if (phase === "loading") {
    return (
      <div style={wrap}>
        <div style={card}>
          <div style={{ color: "#667085", fontSize: 15 }}>Загружаю мастер…</div>
        </div>
      </div>
    );
  }

  if (phase === "failed") {
    return (
      <div style={wrap}>
        <div style={card}>
          <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>Не удалось открыть мастер</div>
          <div style={{ color: "#667085", fontSize: 14, marginBottom: 20 }}>{error}</div>
          <button style={primary} onClick={function () { setAttempt(function (n) { return n + 1; }); }}>
            Попробовать снова
          </button>
        </div>
      </div>
    );
  }

  if (phase === "done") {
    return (
      <div style={wrap}>
        <div style={card}>
          <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 10 }}>Профиль бизнеса сохранён</div>
          <div style={{ color: "#667085", fontSize: 15 }}>Перехожу к подходящим сценариям…</div>
        </div>
      </div>
    );
  }

  return (
    <div style={wrap}>
      <div style={card}>
        <div style={{ fontSize: 13, color: "#667085", marginBottom: 6 }}>Шаг {step} из 2</div>
        <div
          style={{ height: 4, background: "#E3E7F0", borderRadius: 4, marginBottom: 22 }}
          aria-hidden="true"
        >
          <div style={{ height: 4, width: step === 1 ? "50%" : "100%", background: "#2F6FED", borderRadius: 4 }} />
        </div>

        {step === 1 ? (
          <div>
            <h1 style={{ fontSize: 22, margin: "0 0 6px" }}>Расскажите о компании</h1>
            <p style={{ color: "#667085", fontSize: 14, margin: "0 0 22px" }}>
              Это займёт минуту. По этим данным BORIS подберёт подходящие сценарии.
            </p>

            <label style={label}>Название компании</label>
            <input style={input} value={companyName} onChange={function (e) { setCompanyName(e.target.value); }} placeholder="Например: Бытовки План Б" />

            <label style={label}>Чем занимаетесь</label>
            <input style={input} value={niche} onChange={function (e) { setNiche(e.target.value); }} placeholder="Например: аренда бытовок" />

            <label style={label}>Город</label>
            <input style={input} value={city} onChange={function (e) { setCity(e.target.value); }} placeholder="Например: Санкт-Петербург" />

            <label style={label}>Сайт — необязательно</label>
            <input style={input} value={website} onChange={function (e) { setWebsite(e.target.value); }} placeholder="example.ru" />

            <label style={label}>Телефон — необязательно</label>
            <input style={input} value={phone} onChange={function (e) { setPhone(e.target.value); }} placeholder="+7 900 000-00-00" />

            {error ? <div style={{ color: "#D92D20", fontSize: 14, marginBottom: 14 }}>{error}</div> : null}

            <button style={primary} disabled={busy} onClick={submitStep1}>
              {busy ? "Сохраняю…" : "Далее"}
            </button>
          </div>
        ) : (
          <div>
            <h1 style={{ fontSize: 22, margin: "0 0 6px" }}>Где вы уже продаёте</h1>
            <p style={{ color: "#667085", fontSize: 14, margin: "0 0 20px" }}>
              Отметьте площадки. Ссылки можно не заполнять — добавите позже.
            </p>

            {CHANNELS.map(function (ch) {
              const on = channels.indexOf(ch.key) >= 0;
              return (
                <div key={ch.key} style={{ marginBottom: 10 }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer", fontSize: 15 }}>
                    <input type="checkbox" checked={on} onChange={function () { toggleChannel(ch.key); }} />
                    <span>{ch.label}</span>
                  </label>
                  {on ? (
                    <input
                      style={{ ...input, marginTop: 8, marginBottom: 4 }}
                      value={links[ch.key] || ""}
                      onChange={function (e) {
                        const v = e.target.value;
                        setLinks(function (prev) {
                          const next: Record<string, string> = { ...prev };
                          next[ch.key] = v;
                          return next;
                        });
                      }}
                      placeholder="Ссылка — необязательно"
                    />
                  ) : null}
                </div>
              );
            })}

            {error ? <div style={{ color: "#D92D20", fontSize: 14, margin: "14px 0" }}>{error}</div> : null}

            <div style={{ display: "flex", gap: 12, marginTop: 18 }}>
              <button style={ghost} disabled={busy} onClick={function () { setError(""); setStep(1); }}>
                Назад
              </button>
              <button style={primary} disabled={busy} onClick={submitStep2}>
                {busy ? "Сохраняю…" : "Готово"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
