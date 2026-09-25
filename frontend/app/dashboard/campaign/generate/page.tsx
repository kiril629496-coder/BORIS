"use client";

import { FormEvent, useRef, useState } from "react";
import { useAccounts } from "../../../lib/AccountContext";
import { useRouter } from "next/navigation";

type Ad = {
  title?: string;
  description?: string;
  price?: number;
};

export default function GenerateAdsPage() {
  const router = useRouter();
  const { selectedAccountId, selectedAccount } = useAccounts();
  const [topic, setTopic] = useState("");
  const [count, setCount] = useState(10);
  const [priceFrom, setPriceFrom] = useState("");
  const [priceTo, setPriceTo] = useState("");
  const [extra, setExtra] = useState("");
  const [keywords, setKeywords] = useState("");
  const [goal, setGoal] = useState("call");
  const [length, setLength] = useState("medium");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [ads, setAds] = useState<Ad[]>([]);
  const generationKeyRef = useRef("");

  async function generate(event: FormEvent) {
    event.preventDefault();

    setError("");
    setAds([]);

    if (!selectedAccountId) {
      setError("Выберите аккаунт в верхней панели.");
      return;
    }

    if (!topic.trim()) {
      setError("Укажите товар или услугу.");
      return;
    }

    setLoading(true);
    if (!generationKeyRef.current) {
      generationKeyRef.current = globalThis.crypto?.randomUUID?.() || `ads-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    let response: Response | null = null;

    try {
      response = await fetch("/api/avito/generate_ads", {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${localStorage.getItem("boris_token") || ""}`,
        },
        body: JSON.stringify({
          topic: topic.trim(),
          sample: "",
          count: Math.max(1, Math.min(50, Number(count) || 10)),
          price_from: Number(priceFrom) || 0,
          price_to: Number(priceTo) || 0,
          extra: extra.trim(),
          goal,
          use_my_ads: false,
          length,
          keywords: keywords.trim(),
          authored_texts: [],
          bold_level: "medium",
          account_id: selectedAccountId,
          idempotency_key: generationKeyRef.current,
        }),
      });

      const data = await response.json().catch(() => null);

      if (!response.ok || data?.status === "error") {
        throw new Error(
          data?.message ||
          `Ошибка генерации: HTTP ${response.status}`
        );
      }

      const generated = Array.isArray(data)
        ? data
        : Array.isArray(data?.ads)
          ? data.ads
          : Array.isArray(data?.result)
            ? data.result
            : [];

      if (!generated.length) {
        throw new Error("BORIS не вернул объявления.");
      }

      setAds(generated);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Ошибка генерации."
      );
    } finally {
      // If the server answered, the attempt is resolved and the next explicit
      // generation may use a new key. On a network-level ambiguous failure keep
      // the key so the user's retry cannot pay for the same generation twice.
      if (response) generationKeyRef.current = "";
      setLoading(false);
    }
  }

  return (
    <main
      style={{
        minHeight: "100vh",
        background: "#f6f8fc",
        padding: 32,
        color: "#172033",
      }}
    >
      <div style={{ maxWidth: 1180, margin: "0 auto" }}>
        <button
          onClick={() => router.push("/dashboard/campaign")}
          style={backButton}
        >
          ← Назад в центр объявлений
        </button>

        <section style={hero}>
          <div style={eyebrow}>BORIS ИИ · ЕЦО</div>
          <h1 style={{ margin: 0, fontSize: 30 }}>
            Создать объявления
          </h1>
          <p style={subtitle}>
            Один ввод — BORIS собирает серию уникальных объявлений.
          </p>
        </section>

        <form onSubmit={generate} style={panel}>
          <div style={grid}>
            <label>
              <b>Товар / услуга</b>
              <input
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="Например: кухня на заказ"
                style={input}
              />
            </label>

            <label>
              <b>Аккаунт</b>
              <div style={{ ...input, background: "#f8fafc" }}>
                {selectedAccount?.name || selectedAccountId || "Не выбран"}
              </div>
            </label>

            <label>
              <b>Количество</b>
              <input
                type="number"
                min={1}
                max={50}
                value={Number(count) === 0 ? "" : count}
                onChange={(e) => setCount(Number(e.target.value))}
                style={input}
              />
            </label>

            <label>
              <b>Цель</b>
              <select
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                style={input}
              >
                <option value="call">Звонок</option>
                <option value="message">Сообщение</option>
                <option value="order">Заказ</option>
                <option value="visit">Заявка / визит</option>
              </select>
            </label>

            <label>
              <b>Цена от</b>
              <input
                type="number"
                value={Number(priceFrom) === 0 ? "" : priceFrom}
                onChange={(e) => setPriceFrom(e.target.value)}
                placeholder="Введите значение"
                style={input}
              />
            </label>

            <label>
              <b>Цена до</b>
              <input
                type="number"
                value={Number(priceTo) === 0 ? "" : priceTo}
                onChange={(e) => setPriceTo(e.target.value)}
                placeholder="Введите значение"
                style={input}
              />
            </label>
          </div>

          <label style={wideLabel}>
            <b>Факты о компании</b>
            <textarea
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
              rows={5}
              placeholder="Производство, доставка, материалы, реальные преимущества..."
              style={textarea}
            />
          </label>

          <label style={wideLabel}>
            <b>Ключевые фразы</b>
            <textarea
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
              rows={3}
              placeholder="кухня на заказ, кухня по размерам, кухня с установкой..."
              style={textarea}
            />
          </label>

          <label style={wideLabel}>
            <b>Длина</b>
            <select
              value={length}
              onChange={(e) => setLength(e.target.value)}
              style={input}
            >
              <option value="short">Короткие</option>
              <option value="medium">Средние</option>
              <option value="long">Длинные</option>
            </select>
          </label>

          {error && (
            <div style={errorBox}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              ...primaryButton,
              opacity: loading ? 0.65 : 1,
            }}
          >
            {loading ? "BORIS генерирует…" : "Создать объявления"}
          </button>
        </form>

        {ads.length > 0 && (
          <section style={{ marginTop: 24 }}>
            <h2 style={{ fontSize: 22 }}>
              Результат · {ads.length} объявлений
            </h2>

            <div style={{ display: "grid", gap: 16 }}>
              {ads.map((ad, index) => (
                <article key={index} style={card}>
                  <div style={cardTitle}>
                    {ad.title || `Объявление ${index + 1}`}
                  </div>

                  <div style={description}>
                    {ad.description || ""}
                  </div>

                  {ad.price !== undefined && (
                    <div style={price}>
                      {Number(ad.price).toLocaleString("ru-RU")} ₽
                    </div>
                  )}
                </article>
              ))}
            </div>
          </section>
        )}
      </div>
    </main>
  );
}

const backButton = {
  border: 0,
  background: "transparent",
  color: "#356df3",
  cursor: "pointer",
  marginBottom: 18,
  fontSize: 14,
};

const hero = {
  background: "#fff",
  borderRadius: 22,
  padding: 28,
  boxShadow: "0 12px 35px rgba(31, 52, 90, 0.08)",
  marginBottom: 20,
};

const eyebrow = {
  fontSize: 12,
  fontWeight: 700,
  color: "#356df3",
  textTransform: "uppercase" as const,
  letterSpacing: ".08em",
  marginBottom: 8,
};

const subtitle = {
  margin: "10px 0 0",
  color: "#68738a",
  fontSize: 15,
};

const panel = {
  background: "#fff",
  borderRadius: 22,
  padding: 28,
  boxShadow: "0 12px 35px rgba(31, 52, 90, 0.08)",
};

const grid = {
  display: "grid",
  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
  gap: 18,
};

const wideLabel = {
  display: "block",
  marginTop: 18,
};

const input = {
  display: "block",
  width: "100%",
  boxSizing: "border-box" as const,
  marginTop: 7,
  border: "1px solid #dce3f0",
  borderRadius: 12,
  padding: "12px 14px",
  fontSize: 14,
  background: "#fff",
};

const textarea = {
  ...input,
  resize: "vertical" as const,
};

const primaryButton = {
  marginTop: 22,
  border: 0,
  borderRadius: 12,
  padding: "13px 22px",
  background: "#356df3",
  color: "#fff",
  fontWeight: 700,
  cursor: "pointer",
  fontSize: 15,
};

const errorBox = {
  marginTop: 18,
  padding: 14,
  borderRadius: 12,
  background: "#fff0f0",
  color: "#bd3030",
};

const card = {
  background: "#fff",
  borderRadius: 18,
  padding: 22,
  boxShadow: "0 8px 25px rgba(31, 52, 90, 0.07)",
};

const cardTitle = {
  fontWeight: 800,
  fontSize: 18,
  marginBottom: 12,
};

const description = {
  whiteSpace: "pre-wrap" as const,
  lineHeight: 1.55,
  color: "#465169",
};

const price = {
  marginTop: 14,
  fontWeight: 800,
};
