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

const PREMIUM_FORMATS = [
  { key:"1:1", label:"1:1" },
  { key:"4:5", label:"4:5" },
  { key:"16:9", label:"16:9" },
  { key:"avito_pc", label:"Avito PC" },
  { key:"avito_mobile", label:"Avito Mobile" },
  { key:"vk", label:"VK" },
  { key:"telegram", label:"Telegram" },
];

const PREMIUM_HYPOTHESES = [
  { key:"product_hero", label:"Продукт крупно" },
  { key:"pain_solution", label:"Проблема → решение" },
  { key:"result_analytics", label:"Результат / цифры" },
  { key:"dashboard_control", label:"Dashboard / управление" },
  { key:"people_product", label:"Человек + продукт" },
];

function premiumStatusLabel(meta: any) {
  const status = String(meta?.preview_status || "DRAFT_PREVIEW");
  if (status === "AVAILABLE_FOR_USE") return "Одобрено";
  if (status === "REJECTED") return "Отклонено";
  if (status === "QA_FAILED") return "Нужна правка";
  if (status === "BLOCKED_COST") return "Остановлено по лимиту";
  if (status === "COST_RECONCILIATION_REQUIRED") return "Проверяем стоимость";
  return meta?.qa_passed === false ? "Нужна правка" : "Проверки пройдены";
}

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
  const [resultMeta, setResultMeta] =
    useState<any>(null);
  const [engineMode, setEngineMode] =
    useState<"premium" | "standard">("premium");
  const [premiumStatus, setPremiumStatus] =
    useState<any>(null);
  const [premiumFormat, setPremiumFormat] =
    useState("1:1");
  const [premiumHypothesis, setPremiumHypothesis] =
    useState("product_hero");
  const [premiumReferenceUrl, setPremiumReferenceUrl] =
    useState("");
  const [premiumCta, setPremiumCta] =
    useState("");
  const [premiumEdit, setPremiumEdit] =
    useState("");
  const [premiumVersions, setPremiumVersions] =
    useState<any[]>([]);
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
    setResultMeta(null);
    setPremiumVersions([]);
    setPremiumEdit("");
    setEngineMode("premium");
    bannerGenerationKeyRef.current = "";
    setLoading(false);
  }, [selectedAccountId]);

  useEffect(() => {
    if (!account) {
      setSocialAssets([]);
      setPremiumStatus(null);
      return;
    }
    let alive = true;
    apiGet("/api/posting/social-assets?account_id=" + encodeURIComponent(account)).then((r: any) => {
      if (alive) setSocialAssets(Array.isArray(r?.assets) ? r.assets : []);
    });
    apiGet("/api/banners/premium-ai/status?account_id=" + encodeURIComponent(account))
      .then((r: any) => {
        if (alive) setPremiumStatus(r?.status === "ok" ? r : null);
      })
      .catch(() => {
        if (alive) setPremiumStatus(null);
      });
    return () => { alive = false; };
  }, [account]);

  async function loadPremiumVersions(meta: any) {
    const bannerId = String(meta?.banner_id || "").trim();
    if (!account || !bannerId) {
      setPremiumVersions([]);
      return;
    }
    try {
      const r = await apiGet(
        "/api/banners/premium-ai/versions?account_id="
        + encodeURIComponent(account)
        + "&banner_id="
        + encodeURIComponent(bannerId)
      );
      setPremiumVersions(Array.isArray(r?.versions) ? r.versions : []);
    } catch {
      setPremiumVersions([]);
    }
  }

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
      setErr("Проверьте заголовок.");
      return;
    }

    if (
      engineMode === "premium"
      && premiumStatus
      && premiumStatus.available === false
    ) {
      setErr(
        premiumStatus?.subscription?.state === "expired"
          ? "Премиум AI недоступен: оплаченный период аккаунта завершён."
          : "Премиум AI сейчас недоступен для этого аккаунта."
      );
      return;
    }

    setBusy("image");
    setErr("");
    if (!bannerGenerationKeyRef.current) {
      bannerGenerationKeyRef.current =
        globalThis.crypto?.randomUUID?.()
        || `banner-${engineMode}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    let serverAnswered = false;

    try {
      const endpoint =
        engineMode === "premium"
          ? "/api/banners/premium-ai/create-one"
          : "/api/banners/v4/design-one";

      const body =
        engineMode === "premium"
          ? {
              account_id: account,
              description: description.trim(),
              exact_title: title.trim(),
              exact_subtitle: subtitle.trim(),
              exact_cta: premiumCta.trim(),
              exact_price: price.trim(),
              advantages: advantages.map((x) => x.trim()).filter(Boolean),
              accent_color: "#F97316",
              format: premiumFormat,
              hypothesis: premiumHypothesis,
              reference_image_urls: premiumReferenceUrl.trim()
                ? [premiumReferenceUrl.trim()]
                : [],
              idempotency_key: bannerGenerationKeyRef.current,
            }
          : {
              account_id: account,
              goal,
              description: description.trim(),
              title: title.trim(),
              subtitle: subtitle.trim(),
              advantages: advantages.map((x) => x.trim()).filter(Boolean),
              price_text: price.trim(),
              accent_color: "#2F6FED",
              idempotency_key: bannerGenerationKeyRef.current,
            };

      const r = await apiPost(endpoint, body);
      serverAnswered = true;

      if (r?.status !== "ok" || !r?.url) {
        throw new Error(
          r?.detail
          || r?.code
          || "Не удалось создать баннер"
        );
      }

      setResultUrl(r.url);
      setResultMeta(r);
      setStep("ready");

      if (engineMode === "premium") {
        await loadPremiumVersions(r);
      } else {
        setPremiumVersions([]);
      }

    } catch (e: any) {
      setErr(
        e?.message
        || "Не удалось создать баннер"
      );

    } finally {
      // Keep a key only for an ambiguous network failure. Any server response
      // finishes this paid economic intent.
      if (serverAnswered) bannerGenerationKeyRef.current = "";
      setBusy("");
    }
  }

  async function regeneratePremium() {
    if (!resultMeta?.banner_id || !premiumEdit.trim()) return;
    setBusy("image");
    setErr("");
    const idem =
      globalThis.crypto?.randomUUID?.()
      || `banner-premium-edit-${Date.now()}-${Math.random().toString(36).slice(2)}`;

    try {
      const r = await apiPost(
        "/api/banners/premium-ai/create-one",
        {
          account_id: account,
          description: description.trim(),
          exact_title: title.trim(),
          exact_subtitle: subtitle.trim(),
          exact_cta: premiumCta.trim(),
          exact_price: price.trim(),
          advantages: advantages.map((x) => x.trim()).filter(Boolean),
          accent_color: "#F97316",
          format: premiumFormat,
          hypothesis: premiumHypothesis,
          reference_image_urls: premiumReferenceUrl.trim()
            ? [premiumReferenceUrl.trim()]
            : [],
          idempotency_key: idem,
          parent_banner_id: resultMeta.banner_id,
          edit_instruction: premiumEdit.trim(),
        }
      );

      if (r?.status !== "ok" || !r?.url) {
        throw new Error(r?.detail || r?.code || "Не удалось сделать новую версию");
      }

      setResultUrl(r.url);
      setResultMeta(r);
      setPremiumEdit("");
      await loadPremiumVersions(r);
    } catch (e: any) {
      setErr(e?.message || "Не удалось сделать новую версию");
    } finally {
      setBusy("");
    }
  }

  async function approvePremium() {
    if (!resultMeta?.banner_id) return;
    if (resultMeta?.qa_passed !== true || resultMeta?.cost_sla_pass !== true) {
      setErr("Этот вариант нельзя одобрить: контроль качества или лимит стоимости не пройден.");
      return;
    }
    setBusy("approve");
    setErr("");
    try {
      const r = await apiPost(
        "/api/banners/premium-ai/approve",
        {
          account_id: account,
          banner_id: resultMeta.banner_id,
          version: resultMeta.version,
        }
      );
      if (r?.status !== "ok") {
        throw new Error(r?.detail || r?.code || "Не удалось одобрить баннер");
      }
      setResultMeta((old: any) => ({
        ...(old || {}),
        approved: true,
        rejected: false,
        preview_status: "AVAILABLE_FOR_USE",
        media: r.media,
      }));
      await loadPremiumVersions(resultMeta);
    } catch (e: any) {
      setErr(e?.message || "Не удалось одобрить баннер");
    } finally {
      setBusy("");
    }
  }

  async function rejectPremium() {
    if (!resultMeta?.banner_id || resultMeta?.approved === true) return;
    setBusy("reject");
    setErr("");
    try {
      const r = await apiPost(
        "/api/banners/premium-ai/reject",
        {
          account_id: account,
          banner_id: resultMeta.banner_id,
          version: resultMeta.version,
          reason: premiumEdit.trim() || "Отклонено пользователем после просмотра",
        }
      );
      if (r?.status !== "ok") {
        throw new Error(r?.detail || r?.code || "Не удалось отклонить баннер");
      }
      setResultMeta((old: any) => ({
        ...(old || {}),
        approved: false,
        rejected: true,
        preview_status: "REJECTED",
        rejected_at: r.rejected_at,
        rejection_reason: premiumEdit.trim(),
      }));
      setPremiumEdit("");
      await loadPremiumVersions(resultMeta);
    } catch (e: any) {
      setErr(e?.message || "Не удалось отклонить баннер");
    } finally {
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

          <Card>
            <div style={{ fontSize:16, fontWeight:800 }}>Режим генерации</div>
            <div style={{ display:"flex", gap:10, flexWrap:"wrap", marginTop:12 }}>
              <Button kind={engineMode === "premium" ? undefined : "ghost"} onClick={() => setEngineMode("premium")}>
                Премиум AI · до 4 ₽
              </Button>
              <Button kind={engineMode === "standard" ? undefined : "ghost"} onClick={() => setEngineMode("standard")}>
                Стандартный
              </Button>
            </div>
            <div style={{ marginTop:8, color:color.muted, fontSize:13 }}>
              {engineMode === "premium"
                ? `Максимальное качество · ориентир ${Number(premiumStatus?.estimated_cost_rub || 2.6).toFixed(2)} ₽ за баннер · сначала покажем черновик`
                : "Стандартный режим · AI-фон + аккуратная типографика"}
            </div>
          </Card>

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

                  {engineMode === "premium" ? (
                    <>
                      <Input
                        value={premiumCta}
                        onChange={(e: any) => setPremiumCta(e.target.value)}
                        placeholder="CTA — например: Запуск за 30 минут"
                      />

                      <div style={{ display:"grid", gap:14 }}>
                        <div>
                          <div style={{ fontSize:13, fontWeight:800, marginBottom:8 }}>Формат</div>
                          <div style={{ display:"flex", gap:8, flexWrap:"wrap" }}>
                            {PREMIUM_FORMATS.map((item) => (
                              <Button
                                key={item.key}
                                kind={premiumFormat === item.key ? undefined : "ghost"}
                                onClick={() => setPremiumFormat(item.key)}
                              >
                                {item.label}
                              </Button>
                            ))}
                          </div>
                        </div>

                        <div>
                          <div style={{ fontSize:13, fontWeight:800, marginBottom:8 }}>Визуальная гипотеза</div>
                          <div style={{ display:"flex", gap:8, flexWrap:"wrap" }}>
                            {PREMIUM_HYPOTHESES.map((item) => (
                              <Button
                                key={item.key}
                                kind={premiumHypothesis === item.key ? undefined : "ghost"}
                                onClick={() => setPremiumHypothesis(item.key)}
                              >
                                {item.label}
                              </Button>
                            ))}
                          </div>
                        </div>
                      </div>

                      <Input
                        value={premiumReferenceUrl}
                        onChange={(e: any) => setPremiumReferenceUrl(e.target.value)}
                        placeholder="Референс — ссылка на сохранённую картинку BORIS (необязательно)"
                      />
                    </>
                  ) : null}
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
              <div className="boris-f3-64126e3e96bc1e">
                {engineMode === "premium" ? "Премиум AI — черновик готов" : "Баннер готов"}
              </div>

              <div style={{ marginTop:6, color:color.muted, fontSize:13 }}>
                {engineMode === "premium"
                  ? "Проверьте текст и композицию. Пока вы не нажмёте «Одобрить», баннер не попадёт в медиатеку и не используется в публикации."
                  : "Проверьте визуал, текст, иконки и общую композицию."}
              </div>

              {engineMode === "premium" && resultMeta ? (
                <div style={{ display:"flex", gap:8, flexWrap:"wrap", marginTop:12, fontSize:12 }}>
                  <span style={{ padding:"6px 9px", border:"1px solid " + color.line, borderRadius:999 }}>
                    Версия {Number(resultMeta.version || 1)}
                  </span>
                  <span style={{ padding:"6px 9px", border:"1px solid " + color.line, borderRadius:999 }}>
                    {Number(resultMeta.total_cost_rub || resultMeta.actual_cost_rub || resultMeta.estimated_cost_rub || 0).toFixed(2)} ₽
                  </span>
                  <span style={{ padding:"6px 9px", border:"1px solid " + color.line, borderRadius:999 }}>
                    {premiumStatusLabel(resultMeta)}
                  </span>
                </div>
              ) : null}

              {resultUrl && (
                <img
                  src={resultUrl}
                  alt="Готовый баннер"
                  style={{
                    display:"block",
                    width:"100%",
                    maxWidth:680,
                    marginTop:16,
                    borderRadius:14,
                    border:"1px solid " + color.line,
                  }}
                />
              )}

              {engineMode === "premium" ? (
                <>
                  <div style={{ marginTop:16, display:"grid", gap:10, maxWidth:680 }}>
                    <Input
                      value={premiumEdit}
                      onChange={(e: any) => setPremiumEdit(e.target.value)}
                      placeholder="Правка словами — например: сделай CTA крупнее и убери лишние элементы"
                    />
                    <div style={{ display:"flex", gap:10, flexWrap:"wrap" }}>
                      <Button
                        kind="ghost"
                        disabled={busy !== "" || !premiumEdit.trim()}
                        onClick={regeneratePremium}
                      >
                        {busy === "image" ? "Создаю новую версию…" : "Сделать новую версию"}
                      </Button>
                      <Button
                        kind="ghost"
                        disabled={busy !== "" || resultMeta?.approved === true || resultMeta?.rejected === true}
                        onClick={rejectPremium}
                      >
                        {resultMeta?.rejected === true
                          ? "Отклонено"
                          : busy === "reject"
                            ? "Отклоняю…"
                            : "Отклонить"}
                      </Button>
                      <Button
                        disabled={
                          busy !== ""
                          || resultMeta?.approved === true
                          || resultMeta?.rejected === true
                          || resultMeta?.qa_passed !== true
                          || resultMeta?.cost_sla_pass !== true
                          || resultMeta?.cost_reconciled !== true
                        }
                        onClick={approvePremium}
                      >
                        {resultMeta?.approved === true
                          ? "Одобрено"
                          : busy === "approve"
                            ? "Одобряю…"
                            : "Одобрить в медиатеку"}
                      </Button>
                    </div>
                  </div>

                  {premiumVersions.length > 0 ? (
                    <div style={{ marginTop:18 }}>
                      <div style={{ fontSize:13, fontWeight:800 }}>История версий</div>
                      <div style={{ display:"flex", gap:8, flexWrap:"wrap", marginTop:8 }}>
                        {premiumVersions.map((v: any) => (
                          <button
                            type="button"
                            key={String(v.version)}
                            onClick={() => {
                              if (v?.url) setResultUrl(v.url);
                              setResultMeta(v);
                            }}
                            style={{
                              border:"1px solid " + color.line,
                              borderRadius:10,
                              background:Number(v.version) === Number(resultMeta?.version) ? "#fff7ed" : "white",
                              padding:"8px 10px",
                              cursor:"pointer",
                              font:"inherit",
                              fontSize:12,
                            }}
                          >
                            v{v.version} · {Number(v.total_cost_rub || v.actual_cost_rub || v.estimated_cost_rub || 0).toFixed(2)} ₽
                            {v.approved ? " · одобрено" : v.rejected ? " · отклонено" : ""}
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  <div style={{ marginTop:16, display:"flex", gap:10, flexWrap:"wrap" }}>
                    {resultMeta?.approved === true ? (
                      <a href="/dashboard/campaign/media">
                        <Button>Открыть в Медиатеке</Button>
                      </a>
                    ) : null}
                    <Button
                      kind="ghost"
                      onClick={() => {
                        setResultUrl("");
                        setResultMeta(null);
                        setPremiumVersions([]);
                        setStep("copy");
                      }}
                    >
                      Назад к настройкам
                    </Button>
                  </div>
                </>
              ) : (
                <div className="boris-f3-f8f1c5c3c9e27c">
                  <a href="/dashboard/campaign/media">
                    <Button>Открыть в Медиатеке</Button>
                  </a>
                  <Button
                    kind="ghost"
                    onClick={() => {
                      setResultUrl("");
                      setResultMeta(null);
                      setStep("copy");
                    }}
                  >
                    Изменить и сделать заново
                  </Button>
                </div>
              )}
            </Card>
          )}
        </div>
      )}
      </div>
    </EcShell>
  );
}
