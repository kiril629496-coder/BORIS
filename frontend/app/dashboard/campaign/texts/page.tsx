"use client";
import { Button } from "@/app/ui";


import { FormEvent, useRef, useState } from "react";
import { useAccounts } from "../../../lib/AccountContext";
import { useRouter } from "next/navigation";

type Ad = {
  title?: string;
  description?: string;
  price?: number;
};

export default function TextsPage() {
  const router = useRouter();
  const { selectedAccountId, selectedAccount } = useAccounts();

  const [topic, setTopic] = useState("");
  const [ownText, setOwnText] = useState("");
  const [count, setCount] = useState(5);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Ad[]>([]);
  const [fileName, setFileName] = useState("");
  const generationKeyRef = useRef("");

  async function submit(event: FormEvent) {
    event.preventDefault();

    setError("");
    setResult([]);

    if (!selectedAccountId) {
      setError("Выберите аккаунт в верхней панели.");
      return;
    }

    if (!topic.trim()) {
      setError("Укажите товар или услугу.");
      return;
    }

    if (!ownText.trim()) {
      setError("Добавьте свой текст или факты о бизнесе.");
      return;
    }

    setLoading(true);
    if (!generationKeyRef.current) {
      generationKeyRef.current = globalThis.crypto?.randomUUID?.() || `texts-${Date.now()}-${Math.random().toString(36).slice(2)}`;
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
          count: Math.max(1, Math.min(50, Number(count) || 1)),
          price_from: 0,
          price_to: 0,
          extra: ownText.trim(),
          goal: "message",
          use_my_ads: false,
          length: "medium",
          keywords: "",
          authored_texts: [ownText.trim()],
          bold_level: "medium",
          account_id: selectedAccountId,
          idempotency_key: generationKeyRef.current,
        }),
      });

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(
          data?.detail ||
          data?.message ||
          `Ошибка HTTP ${response.status}`
        );
      }

      const resultData =
        Array.isArray(data) ? data :
        Array.isArray(data?.ads) ? data.ads :
        Array.isArray(data?.result) ? data.result :
        [];

      if (!resultData.length) {
        throw new Error("BORIS не вернул тексты.");
      }

      setResult(resultData);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Не удалось создать тексты."
      );
    } finally {
      if (response) generationKeyRef.current = "";
      setLoading(false);
    }
  }

  return (
    <main data-boris-g34v2="texts" style={page}>
      <div style={wrap}>

        <Button
          onClick={() => router.push("/dashboard/campaign")}
          style={back}
        >
          ← Центр объявлений
        </Button>

        <section style={hero}>
          <div style={eyebrow}>BORIS ИИ · ЕЦО</div>
          <h1 data-boris-g34v2-heading="1" style={title}>Написать тексты</h1>
          <p style={subtitle}>
            Дайте BORIS свой текст — он подготовит варианты объявлений.
          </p>
        </section>

        <form data-boris-g34v2-card="1" onSubmit={submit} style={panel}>

          <Field label="Товар или услуга">
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="Например: кухни на заказ"
              style={input}
            />
          </Field>

          <Field label="Аккаунт">
            <div style={{ ...input, background: "#f8fafc" }}>
              {selectedAccount?.name || selectedAccountId || "Не выбран"}
            </div>
          </Field>

          <Field label="Ваш текст / описание">
            <div style={{fontSize:12,color:"#667085",marginBottom:8}}>Можно вставить текст сюда. Если исходник в Word DOCX — откройте кампанию в Центре объявлений: там Word загружается напрямую и связывается с визуалами.</div>
            <textarea
              value={ownText}
              onChange={(e) => setOwnText(e.target.value)}
              rows={10}
              placeholder="Вставьте свой текст, факты, преимущества, условия, цены..."
              style={textarea}
            />
          </Field>

          <Field label="Количество вариантов">
            <input
              type="number"
              min={1}
              max={50}
              value={Number(count) === 0 ? "" : count}
              onChange={(e) => setCount(Number(e.target.value))}
              style={input}
            />
          </Field>

          {error && (
            <div data-boris-g34v2-card="1" style={errorBox}>
              {error}
            </div>
          )}

          <Button
            type="submit"
            disabled={loading}
            style={{
              ...primary,
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? "BORIS пишет…" : "Создать тексты"}
          </Button>

        </form>

        {result.length > 0 && (
          <section style={{ marginTop: 24 }}>
            <div style={{padding:14,border:"1px solid #dbeafe",borderRadius:14,background:"#f8fbff",marginBottom:16}}>
              <b>Тексты готовы. Следующий шаг — изображения.</b>
              <div style={{fontSize:13,color:"#667085",marginTop:5}}>В кампании можно сразу создать ИИ-баннеры и картинки, собрать релевантные фото со стоков или загрузить свои фотографии и распределить их по объявлениям.</div>
              <Button type="button" onClick={()=>router.push("/dashboard/campaign")} style={{marginTop:10}}>Перейти к объявлениям и визуалам →</Button>
            </div>

            <h2 data-boris-g34v2-heading="1">
              Готовые варианты · {result.length}
            </h2>

            <div style={{ display: "grid", gap: 16 }}>

              {result.map((item, index) => (
                <article data-boris-g34v2-card="1" key={index} style={card}>

                  <div data-boris-g34v2-card="1" style={cardTitle}>
                    {item.title || `Вариант ${index + 1}`}
                  </div>

                  <div style={description}>
                    {item.description || ""}
                  </div>

                </article>
              ))}

            </div>

          </section>
        )}

      </div>
    </main>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "block", marginTop: 18 }}>
      <div style={{ fontWeight: 700, marginBottom: 7 }}>
        {label}
      </div>
      {children}
    </label>
  );
}

const page = {
  minHeight: "100vh",
  background: "#f6f8fc",
  padding: "32px",
  color: "#172033",
};

const wrap = {
  maxWidth: 1180,
  margin: "0 auto",
};

const back = {
  border: 0,
  background: "transparent",
  color: "#356df3",
  cursor: "pointer",
  marginBottom: 18,
};

const hero = {
  background: "#fff",
  borderRadius: 22,
  padding: 28,
  boxShadow: "0 12px 35px rgba(31, 52, 90, 0.08)",
};

const eyebrow = {
  fontSize: 12,
  fontWeight: 700,
  color: "#356df3",
};

const title = {
  margin: "8px 0 0",
  fontSize: 30,
};

const subtitle = {
  color: "#68738a",
};

const panel = {
  background: "#fff",
  borderRadius: 22,
  padding: 28,
  marginTop: 20,
  boxShadow: "0 12px 35px rgba(31, 52, 90, 0.08)",
};

const input = {
  width: "100%",
  boxSizing: "border-box" as const,
  border: "1px solid #dce3f0",
  borderRadius: 12,
  padding: "12px 14px",
  fontSize: 14,
};

const textarea = {
  ...input,
  resize: "vertical" as const,
};

const primary = {
  marginTop: 22,
  border: 0,
  borderRadius: 12,
  padding: "13px 22px",
  background: "#356df3",
  color: "#fff",
  fontWeight: 700,
  cursor: "pointer",
};

const errorBox = {
  marginTop: 18,
  padding: 14,
  borderRadius: 12,
  background: "#fff0f0",
  color: "#b3261e",
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
