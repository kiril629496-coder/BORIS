"use client";
import { Button } from "@/app/ui";


import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAccounts } from "../../lib/AccountContext";

type StatusData = {
  status: string;
  account?: {
    account_id?: string;
    name?: string;
    company_niche?: string | null;
    company_description?: string | null;
  };
  resolved?: {
    niche?: string;
    source?: string;
    confidence?: number | null;
    status?: string;
  };
};

function token(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("boris_token") || "";
}

async function api(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers || {});

  const t = token();

  if (t) {
    headers.set("Authorization", "Bearer " + t);
  }

  if (
    init.body &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  const r = await fetch(path, {
    ...init,
    headers,
  });

  const data = await r
    .json()
    .catch(() => ({}));

  if (!r.ok) {
    throw new Error(
      data?.detail ||
      `HTTP ${r.status}`
    );
  }

  return data;
}


export default function CategoryPage() {
  const router = useRouter();

  const {
    selectedAccountId,
  } = useAccounts();

  const [loading, setLoading] = useState(true);
  const [detecting, setDetecting] = useState(false);
  const [saving, setSaving] = useState(false);

  const [accountName, setAccountName] = useState("");
  const [businessText, setBusinessText] = useState("");
  const [niche, setNiche] = useState("");

  const [source, setSource] = useState("");
  const [confidence, setConfidence] = useState<number | null>(null);

  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");

  const accountId = selectedAccountId || "";

  useEffect(() => {
    let alive = true;

    async function load() {
      setLoading(true);
      setErr("");

      try {
        const qs = accountId
          ? "?account_id=" + encodeURIComponent(accountId)
          : "";

        const d: StatusData =
          await api("/api/category-ui/status" + qs);

        if (!alive) return;

        setAccountName(
          d.account?.name || ""
        );

        setBusinessText(
          d.account?.company_description || ""
        );

        setNiche(
          d.account?.company_niche ||
          d.resolved?.niche ||
          ""
        );

        setSource(
          d.resolved?.source || ""
        );

        setConfidence(
          typeof d.resolved?.confidence === "number"
            ? d.resolved.confidence
            : null
        );

      } catch (e: any) {
        if (alive) {
          setErr(
            e?.message ||
            "Не удалось загрузить данные"
          );
        }
      } finally {
        if (alive) setLoading(false);
      }
    }

    load();

    return () => {
      alive = false;
    };
  }, [accountId]);


  async function detect() {
    setDetecting(true);
    setErr("");
    setOk("");

    try {
      const d = await api(
        "/api/category-ui/detect",
        {
          method: "POST",
          body: JSON.stringify({
            account_id: accountId || null,
            business_text: businessText,
          }),
        }
      );

      setNiche(d.suggestion || "");
      setSource(d.source || "");

      setConfidence(
        typeof d.confidence === "number"
          ? d.confidence
          : null
      );

      setOk(
        "BORIS предложил категорию. Проверьте её и подтвердите."
      );

    } catch (e: any) {
      setErr(
        e?.message ||
        "Не удалось определить категорию"
      );
    } finally {
      setDetecting(false);
    }
  }


  async function confirm() {
    if (!niche.trim()) {
      setErr("Укажите основную категорию");
      return;
    }

    setSaving(true);
    setErr("");
    setOk("");

    try {
      await api(
        "/api/category-ui/confirm",
        {
          method: "POST",
          body: JSON.stringify({
            account_id: accountId || null,
            niche: niche.trim(),
            business_text: businessText.trim(),
          }),
        }
      );

      setOk("Категория сохранена.");

      setTimeout(() => {
        router.push("/dashboard/home");
        router.refresh();
      }, 500);

    } catch (e: any) {
      setErr(
        e?.message ||
        "Не удалось сохранить категорию"
      );
    } finally {
      setSaving(false);
    }
  }


  return (
    <div data-boris-g34v2="category"
      style={{
        maxWidth: 980,
        margin: "0 auto",
        padding: "34px 26px 60px",
      }}
    >
      <div
        style={{
          marginBottom: 24,
        }}
      >
        <div
          style={{
            fontSize: 13,
            color: "#246BFD",
            fontWeight: 800,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            marginBottom: 8,
          }}
        >
          BORIS · профиль бизнеса
        </div>

        <h1 data-boris-g34v2-heading="1"
          style={{
            fontSize: 32,
            lineHeight: 1.15,
            margin: 0,
            color: "#15213B",
          }}
        >
          Определение категории
        </h1>

        <p
          style={{
            marginTop: 10,
            color: "#667085",
            fontSize: 15,
            lineHeight: 1.55,
          }}
        >
          BORIS использует категорию для объявлений,
          баннеров, сценариев, аналитики и ответов клиентам.
        </p>
      </div>

      {err && (
        <div
          style={{
            background: "#FFF1F0",
            border: "1px solid #FFD4D1",
            borderRadius: 12,
            padding: "14px 16px",
            color: "#C2362B",
            marginBottom: 18,
          }}
        >
          {err}
        </div>
      )}

      {ok && (
        <div
          style={{
            background: "#ECFDF3",
            border: "1px solid #C6F0D5",
            borderRadius: 12,
            padding: "14px 16px",
            color: "#087A45",
            marginBottom: 18,
          }}
        >
          {ok}
        </div>
      )}

      <div data-boris-g34v2-card="1"
        style={{
          background: "#fff",
          border: "1px solid #E6EAF0",
          borderRadius: 18,
          padding: 24,
          boxShadow: "0 8px 28px rgba(21,33,59,.06)",
        }}
      >
        <div
          style={{
            fontSize: 13,
            color: "#667085",
            marginBottom: 5,
          }}
        >
          Аккаунт
        </div>

        <div
          style={{
            fontSize: 18,
            fontWeight: 700,
            color: "#15213B",
            marginBottom: 24,
          }}
        >
          {loading
            ? "Загрузка…"
            : accountName || accountId || "Текущий аккаунт"}
        </div>

        <label
          style={{
            display: "block",
            fontSize: 14,
            fontWeight: 700,
            color: "#344054",
            marginBottom: 8,
          }}
        >
          Чем занимается компания
        </label>

        <textarea data-boris-g34v2-card="1"
          value={businessText}
          onChange={(e) =>
            setBusinessText(e.target.value)
          }
          placeholder={
            "Например: кухни на заказ, шкафы-купе, " +
            "диваны, кровати, матрасы, двери, зеркала..."
          }
          style={{
            width: "100%",
            minHeight: 130,
            resize: "vertical",
            boxSizing: "border-box",
            border: "1px solid #D7DEE9",
            borderRadius: 12,
            padding: 14,
            fontSize: 15,
            lineHeight: 1.5,
            outline: "none",
            color: "#15213B",
            background: "#fff",
          }}
        />

        <Button
          onClick={detect}
          disabled={detecting || loading}
          style={{
            marginTop: 14,
            border: 0,
            borderRadius: 10,
            background: "#246BFD",
            color: "#fff",
            padding: "12px 18px",
            fontWeight: 700,
            cursor: "pointer",
            opacity: detecting ? .7 : 1,
          }}
        >
          {detecting
            ? "BORIS определяет…"
            : "Определить категорию"}
        </Button>

        <div
          style={{
            height: 1,
            background: "#EEF1F5",
            margin: "26px 0",
          }}
        />

        <label
          style={{
            display: "block",
            fontSize: 14,
            fontWeight: 700,
            color: "#344054",
            marginBottom: 8,
          }}
        >
          Основная категория
        </label>

        <input data-boris-g34v2-card="1"
          value={niche}
          onChange={(e) =>
            setNiche(e.target.value)
          }
          placeholder="Например: Мебель на заказ"
          style={{
            width: "100%",
            boxSizing: "border-box",
            border: "1px solid #D7DEE9",
            borderRadius: 12,
            padding: "13px 14px",
            fontSize: 15,
            outline: "none",
            color: "#15213B",
          }}
        />

        {(source || confidence !== null) && (
          <div
            style={{
              marginTop: 10,
              fontSize: 13,
              color: "#8993A4",
            }}
          >
            Источник: {source || "BORIS"}
            {confidence !== null
              ? ` · уверенность ${Math.round(confidence * 100)}%`
              : ""}
          </div>
        )}

        <div
          style={{
            display: "flex",
            gap: 10,
            marginTop: 22,
            flexWrap: "wrap",
          }}
        >
          <Button
            onClick={confirm}
            disabled={saving || loading}
            style={{
              border: 0,
              borderRadius: 10,
              background: "#246BFD",
              color: "#fff",
              padding: "12px 20px",
              fontWeight: 700,
              cursor: "pointer",
              opacity: saving ? .7 : 1,
            }}
          >
            {saving
              ? "Сохраняю…"
              : "Подтвердить категорию"}
          </Button>

          <Button
            onClick={() =>
              router.push("/dashboard/home")
            }
            style={{
              border: "1px solid #D7DEE9",
              borderRadius: 10,
              background: "#fff",
              color: "#344054",
              padding: "12px 20px",
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            Вернуться
          </Button>
        </div>
      </div>

      <div
        style={{
          background: "#fff",
          border: "1px solid #E6EAF0",
          borderRadius: 18,
          padding: 20,
          marginTop: 18,
          color: "#667085",
          lineHeight: 1.5,
          fontSize: 14,
        }}
      >
        <strong style={{ color: "#344054" }}>
          Если у бизнеса несколько направлений
        </strong>
        <div style={{ marginTop: 6 }}>
          Здесь укажите основное направление.
          Дополнительные направления BORIS сможет использовать
          отдельно для объявлений и контента.
        </div>
      </div>
    </div>
  );
}
