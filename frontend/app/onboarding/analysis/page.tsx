"use client";

/**
 * Экран персонального анализа BORIS.
 *
 * Фронт ничего не вычисляет: ниша, доступность возможностей, порядок плана и
 * происхождение каждого блока приходят готовыми из /api/onboarding/analysis.
 * Здесь только отрисовка, выбор ниши при неоднозначности и два события.
 */
import React from "react";
import { useRouter } from "next/navigation";
import {
  fetchAnalysis,
  fetchFactoryReadiness,
  selectNiche,
  sendEvent,
  OnboardingUnauthorized,
  ROUTE_LOGIN,
  ROUTE_SCENARIOS,
} from "../../lib/onboardingClient";

const ROUTE_FIRST_WORK = "/dashboard/scenarios";
const BLUE = "var(--boris-blue)";
const INK = "var(--boris-text)";
const GREY = "var(--boris-text-2)";
const LINE = "var(--boris-border)";

const wrap: React.CSSProperties = {
  minHeight: "100vh",
  background: "linear-gradient(160deg, var(--boris-bg) 0%, var(--boris-blue-soft) 100%)",
  padding: "32px 16px 64px",
  fontFamily: "var(--font-inter), Arial, Helvetica, sans-serif",
  color: INK,
};
const shell: React.CSSProperties = { maxWidth: 880, margin: "0 auto" };
const card: React.CSSProperties = {
  background: "#FFFFFF",
  border: "1px solid " + LINE,
  borderRadius: 16,
  padding: "24px 26px",
  marginBottom: 16,
};
const h2: React.CSSProperties = { fontSize: 18, fontWeight: 700, margin: "0 0 4px" };
const sub: React.CSSProperties = { fontSize: 13, color: GREY, margin: "0 0 16px" };
const primary: React.CSSProperties = {
  background: BLUE, color: "#FFFFFF", border: "none", borderRadius: 10,
  padding: "14px 30px", fontWeight: 700, fontSize: 16, cursor: "pointer",
};

const SOURCE_LABEL: Record<string, string> = {
  confirmed: "Проверенная база BORIS",
  cross_client: "Проверено на нескольких клиентах",
  observed: "Наблюдение по реальным диалогам",
  hypothesis: "Требует подтверждения",
  product: "Возможности BORIS",
  user: "Данные вашей компании",
};

function Chip(props: { text: string; tone?: string }) {
  const tone = props.tone || "grey";
  const palette: Record<string, string[]> = {
    grey: ["var(--boris-border-soft)", GREY],
    blue: ["var(--boris-blue-soft)", BLUE],
    green: ["#E7F6EC", "#067647"],
    amber: ["#FEF6E7", "#B54708"],
    violet: ["#F4EBFF", "#6941C6"],
  };
  const [bg, fg] = palette[tone] || palette.grey;
  return (
    <span style={{
      display: "inline-block", background: bg, color: fg, borderRadius: 999,
      padding: "3px 10px", fontSize: 12, fontWeight: 600, marginRight: 6, marginTop: 4,
    }}>{props.text}</span>
  );
}

function StatusChip(props: { status?: string }) {
  if (props.status === "works") return <Chip text="Работает" tone="green" />;
  if (props.status === "beta") return <Chip text="Beta" tone="amber" />;
  if (props.status === "soon") return <Chip text="Скоро" tone="grey" />;
  return null;
}

function SourceChip(props: { source?: string }) {
  if (!props.source) return null;
  const text = SOURCE_LABEL[props.source];
  if (!text) return null;
  return <Chip text={text} tone={props.source === "product" ? "blue" : "grey"} />;
}

function ReqChips(props: { requirements?: any[] }) {
  const list = props.requirements || [];
  if (!list.length) return null;
  return (
    <span>
      {list.map(function (r: any, i: number) {
        if (r.kind === "license") return <Chip key={i} text="Требуется лицензия" tone="violet" />;
        if (r.kind === "material") return <Chip key={i} text="Нужны материалы" tone="amber" />;
        return <Chip key={i} text="Нужно подключение" tone="blue" />;
      })}
    </span>
  );
}

function CapCard(props: { item: any }) {
  const it = props.item;
  return (
    <div style={{ border: "1px solid " + LINE, borderRadius: 12, padding: "14px 16px", marginBottom: 10 }}>
      <div className="boris-f3-24b23746723671">{it.title}</div>
      <div style={{ color: GREY, fontSize: 14, marginTop: 4 }}>{it.description}</div>
      <div>
        <StatusChip status={it.status} />
        <ReqChips requirements={it.requirements} />
      </div>
      {it.limits ? <div style={{ color: GREY, fontSize: 13, marginTop: 6 }}>{it.limits}</div> : null}
      {it.beta_limits ? <div className="boris-f3-6534ec0ec3510f">{it.beta_limits}</div> : null}
      {it.soon_reason ? <div style={{ color: GREY, fontSize: 13, marginTop: 6 }}>{it.soon_reason}</div> : null}
    </div>
  );
}

export default function AnalysisPage() {
  const [factoryReadiness, setFactoryReadiness] =
    React.useState<any>(null);

  const router = useRouter();
  const [phase, setPhase] = React.useState<"loading" | "ready" | "error">("loading");
  const [data, setData] = React.useState<any>(null);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState("");
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(function () {
    let active = true;

    fetchFactoryReadiness()
      .then(function (value) {
        if (active) setFactoryReadiness(value);
      })
      .catch(function (err) {
        console.error("factory readiness unavailable", err);
      });

    return function () {
      active = false;
    };
  }, []);

  // ZERO_TOUCH_READINESS_POLL_V1
  React.useEffect(function () {
    if (factoryReadiness?.all_ready) return;

    let cancelled = false;

    const timer = window.setInterval(function () {
      fetchFactoryReadiness()
        .then(function (value) {
          if (!cancelled) {
            setFactoryReadiness(value);
          }
        })
        .catch(function (err) {
          console.error("factory readiness poll failed", err);
        });
    }, 5000);

    return function () {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [factoryReadiness?.all_ready]);


  React.useEffect(() => {
    let alive = true;
    setPhase("loading");
    setError("");
    fetchAnalysis()
      .then(function (value) {
        if (!alive) return;
        setData(value);
        setPhase("ready");
      })
      .catch(function (err) {
        if (!alive) return;
        if (err instanceof OnboardingUnauthorized) { router.replace(ROUTE_LOGIN); return; }
        setError("Не удалось собрать анализ. Проверьте связь и попробуйте ещё раз.");
        setPhase("error");
      });
    return function () { alive = false; };
  }, [attempt, router]);

  async function choose(slug: string) {
    setBusy(slug);
    try {
      await selectNiche(slug);
      setAttempt(function (n) { return n + 1; });
    } catch (err) {
      if (err instanceof OnboardingUnauthorized) { router.replace(ROUTE_LOGIN); return; }
      setError("Не удалось сохранить выбор. Попробуйте ещё раз.");
      setPhase("error");
    } finally {
      setBusy("");
    }
  }

  function goScenarios() {
    sendEvent("onboarding_analysis_completed", { status: data && data.status });
    sendEvent("onboarding_scenarios_clicked", {});
    router.push(ROUTE_SCENARIOS);
  }

  if (phase === "loading") {
    return (
      <div style={wrap}><div style={shell}><div style={card}>
        <div style={{ color: GREY, fontSize: 15 }}>Собираю анализ по вашим ответам…</div>
      </div></div></div>
    );
  }

  if (phase === "error") {
    return (
      <div style={wrap}><div style={shell}><div style={card}>
        <div style={h2}>Не получилось собрать анализ</div>
        <div style={{ color: GREY, fontSize: 14, marginBottom: 18 }}>{error}</div>
        <button style={primary} onClick={function () { setAttempt(function (n) { return n + 1; }); }}>
          Попробовать снова
        </button>
      </div></div></div>
    );
  }

  const a = data || {};
  const profile = a.profile || {};

  if (a.status === "ambiguous") {
    return (
      <div style={wrap}><div style={shell}>
        <h1 className="boris-f3-e1d90e14e35bc6">Уточните, что ближе к вашему бизнесу</h1>
        <p style={{ color: GREY, fontSize: 15, margin: "0 0 20px" }}>
          Вы написали «{profile.company_niche}» — под это подходит несколько направлений.
          Выберите одно, и я соберу анализ точнее.
        </p>
        {(a.candidates || []).map(function (c: any) {
          return (
            <div key={c.slug} style={card}>
              <div className="boris-f3-252286067d1d2a">{c.name}</div>
              <div style={{ color: GREY, fontSize: 14, margin: "6px 0 14px" }}>{c.summary}</div>
              <button style={primary} disabled={!!busy} onClick={function () { choose(c.slug); }}>
                {busy === c.slug ? "Сохраняю…" : "Это про меня"}
              </button>
            </div>
          );
        })}
      </div></div>
    );
  }

  return (
    <div style={wrap}><div style={shell}>
      <h1 className="boris-f3-5e90a7cf6bd647">
        {a.status === "matched" ? "Вот что я понял о вашем бизнесе" : "Ваш профиль сохранён"}
      </h1>
      <p style={{ color: GREY, fontSize: 15, margin: "0 0 22px" }}>
        Ниже — только ваши ответы и проверенные знания BORIS. Ничего придуманного.
      </p>

      <div style={card}>
        <div style={h2}>Данные вашей компании</div>
        <div style={sub}>Это то, что вы указали в мастере</div>
        <div className="boris-f3-426f63eb9cbd72">
          <div><b>{profile.company_name}</b></div>
          <div>{profile.company_niche}{profile.city ? ", " + profile.city : ""}</div>
          {profile.website ? <div style={{ color: GREY }}>{profile.website}</div> : null}
          <div className="boris-f3-1d436efc1f885f">
            {(profile.channels || []).map(function (c: any) {
              return <Chip key={c.key} text={c.title} tone="blue" />;
            })}
          </div>
        </div>
      </div>

      {a.status === "matched" && a.niche ? (
        <div style={card}>
          <div style={h2}>Что я знаю о вашей нише</div>
          <div style={sub}>{a.niche.name}</div>
          <div className="boris-f3-339ce3fa422000">{a.niche.summary}</div>
          {a.buyer_questions && a.buyer_questions.intro ? (
            <div style={{ fontSize: 14, color: GREY, marginBottom: 4 }}>{a.buyer_questions.intro}</div>
          ) : null}
          {(((a.buyer_questions || {}).items) || []).map(function (q: any, i: number) {
            return (
              <div key={i} style={{ borderTop: "1px solid " + LINE, padding: "10px 0" }}>
                <div className="boris-f3-e49c04bf11ac05">{q.text}</div>
                <SourceChip source={q.source} />
              </div>
            );
          })}
        </div>
      ) : null}

      {a.status === "not_found" && a.fallback ? (
        <div style={card}>
          <div style={h2}>По вашей нише данных пока мало</div>
          <div style={{ fontSize: 15, color: GREY }}>{a.fallback.text}</div>
        </div>
      ) : null}

      {a.channels_compare && (a.channels_compare.have || []).length ? (
        <div style={card}>
          <div style={h2}>Ваши каналы продаж</div>
          <div style={sub}>Что уже есть и что можно добавить позже</div>
          <div className="boris-f3-355c4cc2a4c7f2">Уже есть:</div>
          <div>{(a.channels_compare.have || []).map(function (c: any) {
            return <Chip key={c.key} text={c.title} tone="green" />;
          })}</div>
          {(a.channels_compare.can_add_later || []).length ? (
            <>
              <div className="boris-f3-5a4a260410682b">Можно подключить позже:</div>
              <div>{(a.channels_compare.can_add_later || []).map(function (c: any) {
                return <Chip key={c.key} text={c.title} tone="grey" />;
              })}</div>
            </>
          ) : null}
        </div>
      ) : null}

      {(a.problems || []).length ? (
        <div style={card}>
          <div style={h2}>Что может мешать росту</div>
          <div style={sub}>Пока у меня нет вашей статистики, поэтому это вопросы, а не выводы</div>
          {(a.problems || []).map(function (p: any) {
            return (
              <div key={p.key} style={{ borderTop: "1px solid " + LINE, padding: "10px 0", fontSize: 15 }}>
                {p.title}
              </div>
            );
          })}
          <div style={{ color: GREY, fontSize: 14, marginTop: 12 }}>
            Что из этого похоже на вашу ситуацию? Подключите Авито — и я проверю это по вашим данным.
          </div>
        </div>
      ) : null}

      {(a.capabilities_now || []).length ? (
        <div style={card}>
          <div style={h2}>Что я могу сделать прямо сейчас</div>
          <div style={sub}>Без подключения Авито и без оплаты</div>
          {(a.capabilities_now || []).map(function (c: any) {
            return <CapCard key={c.key} item={c} />;
          })}
        </div>
      ) : null}

      {(a.plan || []).length ? (
        <div style={card}>
          <div style={h2}>Персональный план запуска BORIS</div>
          <div style={sub}>Собран по целям и задачам вашей ниши</div>
          {(a.plan || []).map(function (p: any) {
            return (
              <div key={p.key} style={{ borderTop: "1px solid " + LINE, padding: "14px 0" }}>
                <div className="boris-f3-24b23746723671">{p.order}. {p.title}</div>
                <div style={{ color: GREY, fontSize: 14, marginTop: 4 }}>{p.description}</div>
                {p.result ? (
                  <div className="boris-f3-f5b8a0e6b837bf">Результат: {p.result.title}</div>
                ) : null}
                <div>
                  <StatusChip status={p.status} />
                  <ReqChips requirements={p.requirements} />
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      <div
        style={{
          ...card,
          padding: 24,
          borderRadius: 18,
          boxShadow: "0 8px 24px rgba(20, 22, 26, 0.05)",
        }}
      >
        <div

         className="boris-f3-6abe06ae75666f">
          <div className="boris-f3-141426cc82e765">
            <div
              style={{
                color: BLUE,
                fontSize: 13,
                fontWeight: 700,
                marginBottom: 7,
              }}
            >
              Готовность BORIS
            </div>

            <div
              style={{
                color: INK,
                fontSize: 24,
                lineHeight: 1.25,
                fontWeight: 700,
              }}
            >
              {factoryReadiness?.all_ready
                ? "BORIS готов к работе"
                : "Осталось подключить Авито"}
            </div>

            <div
              style={{
                color: GREY,
                fontSize: 14,
                lineHeight: 1.55,
                marginTop: 8,
              }}
            >
              {factoryReadiness?.all_ready
                ? "Результат готов. Персональный план сохранён выше, а рабочие сценарии и следующие действия находятся в разделе «Бизнес-сценарии». От вас сейчас ничего не требуется."
                : "Профиль и персональный план уже готовы. От вас сейчас нужно только подключить Авито; после этого BORIS сам проверит аккаунт и подготовит всё остальное."}
            </div>
          </div>

          <div
            style={{
              borderRadius: 999,
              padding: "8px 13px",
              fontSize: 13,
              fontWeight: 700,
              whiteSpace: "nowrap",
              color: factoryReadiness?.all_ready
                ? "#027A48"
                : BLUE,
              background: factoryReadiness?.all_ready
                ? "#ECFDF3"
                : "#EFF4FF",
            }}
          >
            {factoryReadiness?.all_ready
              ? "Готово"
              : "Нужно подключить"}
          </div>
        </div>

        <div

         className="boris-f3-e1fca5dcbd57bf">
          <div
            style={{
              border: "1px solid " + LINE,
              borderRadius: 12,
              padding: "14px 16px",
            }}
          >
            <div className="boris-f3-242fff7d033493">
              ✓ Профиль бизнеса
            </div>
            <div style={{ color: GREY, fontSize: 13, marginTop: 5 }}>
              Сохранён и понятен BORIS
            </div>
          </div>

          <div
            style={{
              border: "1px solid " + LINE,
              borderRadius: 12,
              padding: "14px 16px",
            }}
          >
            <div className="boris-f3-242fff7d033493">
              {factoryReadiness?.all_ready ? "✓" : "○"} Авито
            </div>
            <div style={{ color: GREY, fontSize: 13, marginTop: 5 }}>
              {factoryReadiness?.all_ready
                ? "Подключено"
                : "Нужно подключить аккаунт"}
            </div>
          </div>

          <div
            style={{
              border: "1px solid " + LINE,
              borderRadius: 12,
              padding: "14px 16px",
            }}
          >
            <div className="boris-f3-242fff7d033493">
              {factoryReadiness?.all_ready ? "✓" : "○"} BORIS
            </div>
            <div style={{ color: GREY, fontSize: 13, marginTop: 5 }}>
              {factoryReadiness?.all_ready
                ? "Готов к работе"
                : "Настроится автоматически"}
            </div>
          </div>
        </div>

        {!factoryReadiness?.all_ready ? (
          <div
            style={{
              borderTop: "1px solid " + LINE,
              marginTop: 22,
              paddingTop: 20,
            }}
          >
            <div
              style={{
                color: INK,
                fontSize: 14,
                fontWeight: 700,
              }}
            >
              После подключения BORIS сам:
            </div>

            <div

             className="boris-f3-31b963deea8150">
              проверит объявления • найдёт слабые места •
              подготовит улучшения • соберёт переписки •
              начнёт контролировать результаты
            </div>
          </div>
        ) : null}
      </div>

      {(a.capabilities_paid || []).length ? (
        <div style={card}>
          <div style={h2}>Доступно с лицензией</div>
          {(a.capabilities_paid || []).map(function (c: any) {
            return <CapCard key={c.key} item={c} />;
          })}
        </div>
      ) : null}

      {(a.materials_missing || []).length ? (
        <div style={card}>
          <div style={h2}>Что стоит добавить позже</div>
          <div style={sub}>Чтобы BORIS работал точнее — не срочно, но пригодится</div>
          {(a.materials_missing || []).map(function (m: any) {
            return (
              <div key={m.key} style={{ borderTop: "1px solid " + LINE, padding: "10px 0" }}>
                <div className="boris-f3-e49c04bf11ac05">{m.title}</div>
                <div style={{ color: GREY, fontSize: 14 }}>{m.description}</div>
              </div>
            );
          })}
        </div>
      ) : null}

      <div

       className="boris-f3-fd241d7fc681a4">
        <button
          style={primary}
          onClick={function () {
            if (factoryReadiness?.all_ready) {
              router.push(ROUTE_FIRST_WORK);
              return;
            }

            goScenarios();
          }}
        >
          {factoryReadiness?.all_ready
            ? "Открыть бизнес-сценарии"
            : "Перейти к бизнес-сценариям"}
        </button>

        <div
          style={{
            color: GREY,
            fontSize: 13,
            lineHeight: 1.4,
          }}
        >
          {factoryReadiness?.all_ready
            ? "Результат готов. Здесь начинается рабочий контур: сценарии, первые действия и дальнейший контроль BORIS."
            : "После подключения Авито статус обновится автоматически — возвращаться и запускать проверку вручную не нужно."}
        </div>
      </div>
    </div></div>
  );
}
