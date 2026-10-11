"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "./legal.module.css";

type LegalDocument = {
  document_type: "terms" | "privacy" | string;
  version: string;
  title: string;
  hash: string;
  public_url: string;
  text: string;
  accepted?: boolean;
  accepted_at?: string | null;
};

function authHeaders(): HeadersInit {
  const token = localStorage.getItem("boris_messenger_token") || localStorage.getItem("boris_token") || "";
  return token ? { Authorization: "Bearer " + token, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
}

export default function MessengerLegalPage() {
  const [documents, setDocuments] = useState<LegalDocument[]>([]);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(true);
  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState("");
  const [openDoc, setOpenDoc] = useState<string>("terms");

  const returnTo = useMemo(() => {
    if (typeof window === "undefined") return "/messenger";
    const stored = localStorage.getItem("boris_messenger_legal_return") || "";
    return stored.startsWith("/messenger") && !stored.startsWith("/messenger/legal") ? stored : "/messenger";
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const docsResponse = await fetch("/api/legal/messenger/documents", { cache: "no-store" });
        if (!docsResponse.ok) throw new Error("Не удалось загрузить документы.");
        const docsData = await docsResponse.json();
        const statusResponse = await fetch("/api/legal/messenger/status", { cache: "no-store", headers: authHeaders() });
        if (statusResponse.status === 401) {
          localStorage.setItem("boris_post_login_return", "/messenger/legal");
          window.location.replace("/messenger/login");
          return;
        }
        if (!statusResponse.ok) throw new Error("Не удалось проверить принятие документов.");
        const statusData = await statusResponse.json();
        if (statusData.accepted) {
          localStorage.removeItem("boris_messenger_legal_return");
          window.location.replace(returnTo);
          return;
        }
        if (!cancelled) setDocuments(docsData.documents || []);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Не удалось открыть документы.");
      } finally {
        if (!cancelled) setBusy(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [returnTo]);

  const accept = async () => {
    if (!checked || accepting || !documents.length) return;
    setAccepting(true);
    setError("");
    try {
      const response = await fetch("/api/legal/messenger/accept", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          accepted: true,
          documents: documents.map(doc => ({
            document_type: doc.document_type,
            version: doc.version,
            hash: doc.hash,
          })),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (response.status === 401) {
        localStorage.setItem("boris_post_login_return", "/messenger/legal");
        window.location.replace("/messenger/login");
        return;
      }
      if (!response.ok || !data.accepted) {
        throw new Error(String(data?.detail?.message || data?.detail || "Не удалось зафиксировать принятие документов."));
      }
      localStorage.removeItem("boris_messenger_legal_return");
      window.location.replace(returnTo);
    } catch (e) {
      setChecked(false);
      setError(e instanceof Error ? e.message : "Не удалось принять документы.");
    } finally {
      setAccepting(false);
    }
  };

  return <main className={styles.page}>
    <section className={styles.card}>
      <header className={styles.header}>
        <span className={styles.logo}>B</span>
        <div>
          <strong>BORIS Messenger</strong>
          <small>Первый вход · обязательные документы</small>
        </div>
      </header>

      <div className={styles.intro}>
        <span className={styles.kicker}>УСЛОВИЯ И КОНФИДЕНЦИАЛЬНОСТЬ</span>
        <h1>Перед началом работы</h1>
        <p>Каждый пользователь принимает документы лично. Владелец компании не принимает их за сотрудников.</p>
      </div>

      {busy ? <div className={styles.loading}>Загружаем актуальную редакцию…</div> : error && !documents.length ? <div className={styles.error}>{error}</div> : <>
        <div className={styles.tabs}>
          {documents.map(doc => <button type="button" key={doc.document_type} className={openDoc === doc.document_type ? styles.active : ""} onClick={() => setOpenDoc(doc.document_type)}>
            <span>{doc.document_type === "terms" ? "Соглашение" : "Конфиденциальность"}</span>
            <small>версия {doc.version}</small>
          </button>)}
        </div>

        {documents.filter(doc => doc.document_type === openDoc).map(doc => <article className={styles.document} key={doc.document_type}>
          <div className={styles.documentHead}>
            <div><strong>{doc.title}</strong><small>Редакция {doc.version}</small></div>
            {doc.document_type === "privacy" && <a href="/privacy" target="_blank" rel="noreferrer">Открыть полную политику ↗</a>}
          </div>
          <pre>{doc.text}</pre>
        </article>)}

        <label className={styles.consent}>
          <input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} />
          <span>Я прочитал(а) и принимаю Пользовательское соглашение BORIS Messenger и Политику конфиденциальности.</span>
        </label>

        {error && <div className={styles.error}>{error}</div>}
        <button type="button" className={styles.accept} disabled={!checked || accepting || !documents.length} onClick={() => void accept()}>
          {accepting ? "Фиксируем согласие…" : "Принять и продолжить"}
        </button>
        <p className={styles.evidence}>Фиксируются версия, криптографический хеш документа, дата принятия и технические данные сессии для подтверждения согласия и безопасности.</p>
      </>}
    </section>
  </main>;
}
