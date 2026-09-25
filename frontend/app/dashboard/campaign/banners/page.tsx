"use client";

import { useEffect, useRef, useState } from "react";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

import {
  EcShell,
  EcEmpty,
} from "../../../ui/EcShell";

import {
  Card,
  Button,
  Alert,
  Skeleton,
  Input,
} from "../../../ui";

import { color } from "../../../ui/tokens";
import PromptImageGenerator from "../_components/PromptImageGenerator";

import {
  apiGet,
  apiPost,
} from "../../../lib/api";
import { useAccounts } from "../../../lib/AccountContext";

type Step =
  | "brief"
  | "copy"
  | "ready";

const GOALS = [
  {
    key: "offer",
    title: "Показать предложение",
    hint: "Что именно вы предлагаете клиенту",
  },
  {
    key: "price",
    title: "Сделать акцент на цене",
    hint: "Цена, акция или специальное условие",
  },
  {
    key: "benefit",
    title: "Показать преимущество",
    hint: "Почему стоит выбрать именно вас",
  },
];

export default function BannersPage() {
  const { selectedAccountId, selectedAccount } = useAccounts();
  const [account, setAccount] =
    useState("");

  const [loading, setLoading] =
    useState(true);

  const [busy, setBusy] =
    useState("");

  const [err, setErr] =
    useState("");

  const [step, setStep] =
    useState<Step>("brief");

  const [goal, setGoal] =
    useState("offer");

  const [description, setDescription] =
    useState("");

  const [title, setTitle] =
    useState("");

  const [subtitle, setSubtitle] =
    useState("");

  const [price, setPrice] =
    useState("");

  const [advantages, setAdvantages] =
    useState<string[]>(["", "", ""]);

  const [resultUrl, setResultUrl] =
    useState("");
  const bannerGenerationKeyRef = useRef("");

  const [socialAssets, setSocialAssets] =
    useState<any[]>([]);
  const [workspaceMode, setWorkspaceMode] =
    useState<"banners" | "images">("banners");

  const clientExample = (() => {
    const niche = String(selectedAccount?.company_niche || "").trim();
    const company = String(selectedAccount?.name || "").trim();
    const desc = String(selectedAccount?.company_description || "").trim().replace(/\s+/g, " ");
    const base = niche || company || "предложение выбранного клиента";
    const facts = desc ? ` ${desc.slice(0, 150)}${desc.length > 150 ? "…" : ""}` : "";
    return `Например для этого аккаунта: ${base}.${facts}`;
  })();

  useEffect(() => {
    setAccount(selectedAccountId || "");
    setStep("brief");
    setResultUrl("");
    bannerGenerationKeyRef.current = "";
    setLoading(false);
  }, [selectedAccountId]);

  useEffect(() => {
    if (!account) {
      setSocialAssets([]);
      return;
    }
    let alive = true;
    apiGet("/api/posting/social-assets?account_id=" + encodeURIComponent(account)).then((r: any) => {
      if (alive) setSocialAssets(Array.isArray(r?.assets) ? r.assets : []);
    });
    return () => { alive = false; };
  }, [account]);

  async function prepareCopy() {
    if (!description.trim()) {
      setErr(
        "Коротко расскажите о предложении."
      );
      return;
    }

    setBusy("copy");
    setErr("");

    try {
      const r = await apiPost(
        "/api/banners/v4/copy",
        {
          account_id: account,
          goal,
          description:
            description.trim(),
        }
      );

      if (r?.status !== "ok") {
        throw new Error(
          r?.detail ||
            "Не удалось подготовить текст"
        );
      }

      setTitle(
        r.title || ""
      );

      setSubtitle(
        r.subtitle || ""
      );

      setPrice(
        r.price_text || ""
      );

      const adv =
        Array.isArray(
          r.advantages
        )
          ? r.advantages
          : [];

      setAdvantages([
        adv[0] || "",
        adv[1] || "",
        adv[2] || "",
      ]);

      setStep("copy");

    } catch (e: any) {
      setErr(
        e?.message ||
          "Не удалось подготовить текст"
      );

    } finally {
      setBusy("");
    }
  }

  function changeAdvantage(
    index: number,
    value: string
  ) {
    setAdvantages(
      (old) =>
        old.map(
          (item, i) =>
            i === index
              ? value
              : item
        )
    );
  }

  async function createBanner() {
    if (!title.trim()) {
      setErr(
        "Проверьте заголовок."
      );
      return;
    }

    setBusy("image");
    setErr("");
    if (!bannerGenerationKeyRef.current) {
      bannerGenerationKeyRef.current =
        globalThis.crypto?.randomUUID?.()
        || `banner-v4-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    let serverAnswered = false;

    try {
      const r = await apiPost(
        "/api/banners/v4/design-one",
        {
          account_id: account,
          goal,
          description:
            description.trim(),

          title:
            title.trim(),

          subtitle:
            subtitle.trim(),

          advantages:
            advantages
              .map((x) => x.trim())
              .filter(Boolean),

          price_text:
            price.trim(),

          accent_color:
            "var(--boris-blue)",

          idempotency_key:
            bannerGenerationKeyRef.current,
        }
      );
      serverAnswered = true;

      if (
        r?.status !== "ok"
        || !r?.url
      ) {
        throw new Error(
          r?.detail ||
            "Не удалось создать баннер"
        );
      }

      setResultUrl(
        r.url
      );

      setStep("ready");

    } catch (e: any) {
      setErr(
        e?.message ||
          "Не удалось создать баннер"
      );

    } finally {
      // A real server response finishes this economic intent. On an ambiguous
      // network failure keep the same key so an explicit retry cannot be billed twice.
      if (serverAnswered) bannerGenerationKeyRef.current = "";
      setBusy("");
    }
  }

  return (
    <EcShell
      tab="banners"
      title="Баннеры"
      subtitle="Рекламные изображения для объявлений"
    >
      <div className="boris-banners-mode-tabs">
        <Button kind={workspaceMode === "banners" ? undefined : "ghost"} onClick={() => setWorkspaceMode("banners")}>Галерея и баннеры</Button>
        <Button kind={workspaceMode === "images" ? undefined : "ghost"} onClick={() => setWorkspaceMode("images")}>Генерация картинок</Button>
      </div>
      {workspaceMode === "images" && account ? <PromptImageGenerator account={account} /> : null}
      <div style={{ display: workspaceMode === "banners" ? "block" : "none" }}>
      {err && (
        <Alert kind="danger">
          {err}
        </Alert>
      )}

      {loading ? (
        <Card>
          <Skeleton lines={6} />
        </Card>
      ) : !account ? (
        <EcEmpty
          title="Выберите аккаунт"
          hint="После выбора аккаунта здесь можно создавать баннеры."
        />
      ) : (
        <div data-boris-g32b-page="campaign" className="boris-banners-workspace">
          {socialAssets.length > 0 && (
            <Card>
              <div style={{ fontSize:16, fontWeight:800 }}>Баннеры из социальных сетей</div>
              <div style={{ marginTop:5, marginBottom:14, color:color.muted, fontSize:13 }}>
                Реальные сохранённые изображения, связанные с черновиками и опубликованными постами.
              </div>
              <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(180px,1fr))", gap:12 }}>
                {socialAssets.slice(0, 12).map((a: any) => (
                  <div key={a.post_id + ":" + a.banner} style={{ border:"1px solid " + color.line, borderRadius:12, padding:10, minWidth:0 }}>
                    <img src={a.url} alt={a.project_name || "Баннер поста"} style={{ width:"100%", aspectRatio:"1 / 1", objectFit:"cover", borderRadius:9, display:"block" }} />
                    <div style={{ marginTop:8, fontSize:13, fontWeight:700, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>{a.project_name || "Социальный пост"}</div>
                    <div style={{ marginTop:3, fontSize:12, color:color.muted }}>
                      {a.status === "published" ? "Опубликован" : a.status === "draft" ? "Черновик" : humanizeBusinessText(a.status,"Пост")}
                      {a.publish_at ? " · " + a.publish_at : ""}
                    </div>
                    <div style={{ marginTop:3, fontSize:12, color:color.muted }}>{Math.max(1, Math.round((a.bytes || 0) / 1024))} КБ · {a.created_at || ""}</div>
                  </div>
                ))}
              </div>
              <div style={{ marginTop:14 }}>
                <a href="/social"><Button kind="ghost">Открыть связанные посты</Button></a>
              </div>
            </Card>
          )}

          {step === "brief" && (
            <>
              <Card className="boris-banners-step-card boris-banners-goal-step">
                <div className="boris-banners-step-head"><span className="boris-banners-step-number">1</span><div><div className="boris-banners-step-title">Что должен показать баннер?</div><div className="boris-banners-step-subtitle">BORIS сам подберёт подходящую композицию и оформление.</div></div></div>

                <div className="boris-banners-goal-grid">
                  {GOALS.map(
                    (item) => {
                      const active =
                        goal === item.key;

                      return (
                        <button className={`boris-banners-goal-card ${active ? "is-active" : ""}`} data-boris-g32b-card="1"
                          key={item.key}
                          type="button"
                          onClick={() =>
                            setGoal(
                              item.key
                            )
                          }
                        >
                          <div

                           className="boris-banners-goal-title">
                            {item.title}
                          </div>

                          <div
                            className="boris-banners-goal-hint"
                          >
                            {item.hint}
                          </div>
                        </button>
                      );
                    }
                  )}
                </div>
              </Card>

              <Card className="boris-banners-step-card boris-banners-offer-step">
                <div className="boris-banners-step-head"><span className="boris-banners-step-number is-green">2</span><div><div className="boris-banners-step-title">Расскажите о предложении</div><div className="boris-banners-step-subtitle">Достаточно 2–4 предложений: что предлагаете, кому и в чём основная ценность.</div></div></div>

                <textarea className="boris-banners-description" data-boris-g32b-card="1"
                  value={description}
                  onChange={(e) =>
                    setDescription(
                      e.target.value
                    )
                  }
                  placeholder={clientExample}
                  rows={5}
                  style={{
                    width: "100%",
                    boxSizing: "border-box",
                    resize: "vertical",
                    padding: 12,
                    border:
                      "1px solid " +
                      color.line,
                    borderRadius: 10,
                    font: "inherit",
                  }}
                />

                <div

                 className="boris-banners-primary-action">
                  <Button
                    disabled={
                      busy !== ""
                      || !description.trim()
                    }
                    onClick={
                      prepareCopy
                    }
                  >
                    {busy === "copy"
                      ? "Подготавливаю…"
                      : "Продолжить"}
                  </Button>
                </div>
              </Card>
            </>
          )}

          {step === "copy" && (
            <>
              <Card>
                <div

                 className="boris-f3-f9ab603104b127">
                  Проверьте содержание
                </div>

                <div
                  style={{
                    marginTop: 5,
                    marginBottom: 14,
                    color: color.muted,
                    fontSize: 13,
                  }}
                >
                  Короткие тексты лучше работают в рекламном изображении.
                </div>

                <div

                 className="boris-f3-1e6913f0bd912f">
                  <Input
                    value={title}
                    onChange={(e: any) =>
                      setTitle(
                        e.target.value
                      )
                    }
                    placeholder="Главный заголовок"
                  />

                  <Input
                    value={subtitle}
                    onChange={(e: any) =>
                      setSubtitle(
                        e.target.value
                      )
                    }
                    placeholder="Короткое уточнение"
                  />

                  <div

                   className="boris-f3-11a0a4549c4c07">
                    Что ещё показать
                  </div>

                  {advantages.map(
                    (item, index) => (
                      <Input
                        key={index}
                        value={item}
                        onChange={(e: any) =>
                          changeAdvantage(
                            index,
                            e.target.value
                          )
                        }
                        placeholder={
                          "Преимущество " +
                          (index + 1) +
                          " — необязательно"
                        }
                      />
                    )
                  )}

                  <Input
                    value={price}
                    onChange={(e: any) =>
                      setPrice(
                        e.target.value
                      )
                    }
                    placeholder="Цена или акция — только если она реально указана"
                  />
                </div>
              </Card>

              <Card>
                <div

                 className="boris-f3-d3a3167164ac43">
                  <div>
                    <div

                     className="boris-f3-bb65a9b6767687">
                      Создадим один контрольный вариант
                    </div>

                    <div
                      style={{
                        marginTop: 5,
                        fontSize: 13,
                        color: color.muted,
                      }}
                    >
                      BORIS сам подберёт композицию, оформление и подходящие пиктограммы.
                    </div>
                  </div>

                  <div

                   className="boris-f3-ccecc9abc4e3d9">
                    <Button
                      kind="ghost"
                      onClick={() =>
                        setStep(
                          "brief"
                        )
                      }
                    >
                      Назад
                    </Button>

                    <Button
                      disabled={
                        busy !== ""
                        || !title.trim()
                      }
                      onClick={
                        createBanner
                      }
                    >
                      {busy === "image"
                        ? "Создаю…"
                        : "Создать баннер"}
                    </Button>
                  </div>
                </div>
              </Card>
            </>
          )}

          {step === "ready" && (
            <Card>
              <div

               className="boris-f3-64126e3e96bc1e">
                Баннер готов
              </div>

              <div
                style={{
                  marginTop: 6,
                  color: color.muted,
                  fontSize: 13,
                }}
              >
                Проверьте визуал, текст, иконки и общую композицию.
              </div>

              {resultUrl && (
                <img
                  src={resultUrl}
                  alt="Готовый баннер"
                  style={{
                    display: "block",
                    width: "100%",
                    maxWidth: 680,
                    marginTop: 16,
                    borderRadius: 14,
                    border:
                      "1px solid " +
                      color.line,
                  }}
                />
              )}

              <div

               className="boris-f3-f8f1c5c3c9e27c">
                <a href="/dashboard/campaign/media">
                  <Button>
                    Открыть в Медиатеке
                  </Button>
                </a>

                <Button
                  kind="ghost"
                  onClick={() => {
                    setResultUrl("");
                    setStep("copy");
                  }}
                >
                  Изменить и сделать заново
                </Button>
              </div>
            </Card>
          )}
        </div>
      )}
      </div>
    </EcShell>
  );
}
