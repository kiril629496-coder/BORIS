"use client";
import { Alert as BorisAlert } from "@/app/ui";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

import { Button } from "@/app/ui";


import React, {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import { apiGet } from "../../lib/api";
import { Shell } from "../../components/shell/LegacyShellAdapter";
import styles from "./page.module.css";

const money = (v: any) =>
  new Intl.NumberFormat(
    "ru-RU",
    {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }
  ).format(Number(v || 0)) + " ₽";

const n = (v: any) =>
  new Intl.NumberFormat(
    "ru-RU"
  ).format(Number(v || 0));

export default function AiCostsPage() {
  /* AI_COSTS_KIRILL_ONLY_V1
     UX guard only; backend is the real security boundary. */
  if (typeof window !== "undefined") {
    const _email = (
      localStorage.getItem("boris_user_email") || ""
    ).toLowerCase();

    if (_email !== "ostapenko-kirill-86@yandex.ru") {
      window.location.replace("/dashboard/home");
      return null;
    }
  }


  const [days, setDays] = useState(7);

  const [overview, setOverview] =
    useState<any>(null);

  const [operations, setOperations] =
    useState<any[]>([]);

  const [accounts, setAccounts] =
    useState<any[]>([]);

  const [images, setImages] =
    useState<any[]>([]);

  const [whisper, setWhisper] =
    useState<any[]>([]);

  const [guards, setGuards] =
    useState<any[]>([]);

  const [alerts, setAlerts] =
    useState<any[]>([]);

  const [guardEvents, setGuardEvents] =
    useState<any[]>([]);

  const [loading, setLoading] =
    useState(true);

  const load = useCallback(
    async () => {
      setLoading(true);

      const [
        a,b,c,d,e,f,g,h
      ] = await Promise.all([
        apiGet(`/api/ai-costs/overview?days=${days}`),
        apiGet(`/api/ai-costs/operations?days=${days}`),
        apiGet(`/api/ai-costs/accounts?days=${days}`),
        apiGet(`/api/ai-costs/images?days=${days}`),
        apiGet(`/api/ai-costs/whisper?days=${days}`),
        apiGet("/api/ai-costs/guards"),
        apiGet("/api/ai-costs/alerts"),
        apiGet(`/api/ai-costs/guard-events?days=${days}&limit=100`),
      ]);

      setOverview(a);
      setOperations(b?.items || []);
      setAccounts(c?.items || []);
      setImages(d?.items || []);
      setWhisper(e?.items || []);
      setGuards(f?.items || []);
      setAlerts(g?.items || []);
      setGuardEvents(h?.items || []);

      setLoading(false);
    },
    [days]
  );

  useEffect(() => {
    load();
  }, [load]);

  const clientCost = useMemo(
    () =>
      accounts
        .filter(x => !!x.owner_user_id)
        .reduce(
          (sum,x) =>
            sum + Number(x.cost_rub || 0),
          0
        ),
    [accounts]
  );

  const systemCost = useMemo(
    () =>
      accounts
        .filter(x => !x.owner_user_id)
        .reduce(
          (sum,x) =>
            sum + Number(x.cost_rub || 0),
          0
        ),
    [accounts]
  );

  return (
    <Shell activeKey="ai-costs">
      <main className={styles.page}>

        <header className={styles.header}>
          <div>
            <div className={styles.kicker}>
              AI CONTROL CENTER
            </div>

            <h1>Расходы ИИ</h1>

            <p>
              Реальная себестоимость BORIS:
              клиенты, модели, изображения,
              звонки, QA и защитные лимиты.
            </p>
          </div>

          <div className={styles.controls}>
            <select
              value={days}
              onChange={
                e => setDays(
                  Number(e.target.value)
                )
              }
            >
              <option value={1}>Сегодня</option>
              <option value={7}>7 дней</option>
              <option value={30}>30 дней</option>
              <option value={90}>90 дней</option>
            </select>

            <Button onClick={load}>
              Обновить
            </Button>
          </div>
        </header>

        <section className={styles.metrics}>
          <Metric
            name="Сегодня"
            value={money(overview?.today?.cost_rub)}
            note={`${n(overview?.today?.calls)} вызовов`}
          />

          <Metric
            name={`${days} дней`}
            value={money(overview?.totals?.cost_rub)}
            note={`${n(overview?.totals?.calls)} ИИ-вызовов`}
          />

          <Metric
            name="Месяц"
            value={money(overview?.month?.cost_rub)}
            note={`${n(overview?.month?.calls)} вызовов`}
          />

          <Metric
            name="Клиенты"
            value={money(clientCost)}
            note="с доказанным владельцем"
          />

          <Metric
            name="Система / QA"
            value={money(systemCost)}
            note={`${money(overview?.qa?.cost_rub)} QA`}
          />

          <Metric
            name="Предупреждения"
            value={n(overview?.alerts?.alerts)}
            note={`${n(overview?.alerts?.critical)} критических`}
          />
        </section>

        <Block title="По операциям">
          <Table
            heads={[
              "Операция",
              "Модель",
              "Вызовы",
              "Картинки",
              "Минуты",
              "Стоимость",
            ]}
            rows={operations.map(x => [
              x.operation,
              x.model,
              n(x.calls),
              n(x.images),
              Number(x.audio_minutes || 0).toFixed(2),
              money(x.cost_rub),
            ])}
          />
        </Block>

        <Block title="По клиентам">
          <Table
            heads={[
              "Аккаунт",
              "Владелец",
              "Вызовы",
              "Без user_id",
              "Стоимость",
            ]}
            rows={accounts.map(x => [
              x.account_id || "Системный",
              x.owner_user_id || "—",
              n(x.calls),
              n(x.missing_user_rows),
              money(x.cost_rub),
            ])}
          />
        </Block>

        <Block title="Изображения и баннеры">
          <Table
            heads={[
              "Модель",
              "Операция",
              "Кол-во",
              "Цена / шт.",
              "Всего",
            ]}
            rows={images.map(x => [
              x.model,
              x.operation,
              n(x.images),
              money(x.rub_per_image),
              money(x.cost_rub),
            ])}
          />
        </Block>

        <div className={styles.two}>
          <Block title="Whisper">
            <Table
              heads={[
                "Аккаунт",
                "Минуты",
                "Вызовы",
                "Стоимость",
              ]}
              rows={whisper.map(x => [
                x.account_id || "—",
                Number(
                  x.audio_minutes || 0
                ).toFixed(2),
                n(x.calls),
                money(x.cost_rub),
              ])}
            />
          </Block>

          <Block title="AI Guard">
            <div className={styles.guards}>
              {guards.length ? (
                guards.map((x,i) => (
                  <div
                    className={styles.guard}
                    key={i}
                  >
                    <div className={styles.guardTop}>
                      <strong>
                        {x.scope_key}
                      </strong>

                      <b>
                        {Number(
                          x.used_percent || 0
                        ).toFixed(2)}%
                      </b>
                    </div>

                    <small>
                      {x.module}
                    </small>

                    <div className={styles.bar}>
                      <div
                        style={{
                          width:
                            Math.min(
                              100,
                              Number(
                                x.used_percent || 0
                              )
                            ) + "%"
                        }}
                      />
                    </div>

                    <small>
                      {money(x.guard_spent_rub)}
                      {" / "}
                      {money(x.limit_rub)}
                    </small>
                  </div>
                ))
              ) : (
                <Empty
                  text="Сегодня guard ещё не использовался"
                />
              )}
            </div>
          </Block>
        </div>

        <Block title="Почему BORIS остановил платные операции">
          <Table
            heads={["Время", "Аккаунт", "Операция", "Решение BORIS", "Статус"]}
            rows={guardEvents.map(x => [
              x.created_at ? new Date(x.created_at).toLocaleString("ru-RU") : "—",
              x.account_id || "Системный",
              humanizeBusinessText(x.operation || x.source,"Операция BORIS"),
              humanizeBusinessText(x.user_message,"Защита BORIS остановила или изменила платную операцию"),
              x.status === "quarantined" ? "Повтор запрещён" : x.status === "blocked" ? "Остановлено" : humanizeBusinessText(x.status,"Состояние уточняется"),
            ])}
          />
          <div style={{fontSize:12,color:"#667085",marginTop:8}}>
            Здесь показываются именно защитные решения BORIS: лимит, запрет повторной покупки после неоднозначного ответа, остановка бюджета и другие причины. Тексты запросов, клиентские данные и ключи сюда не сохраняются.
          </div>
        </Block>

        <Block title="Предупреждения">
          <div className={styles.alerts}>
            {alerts.length ? (
              alerts.map((x,i) => (
                <BorisAlert
                  className={styles.alert}
                  key={i}
                >
                  <div>
                    <strong>
                      {x.subject}
                    </strong>
                    <span>
                      {humanizeBusinessText(x.reason,"Причина уточняется")}
                    </span>
                  </div>

                  <b>{money(x.rub)}</b>
                </BorisAlert>
              ))
            ) : (
              <Empty
                text="Активных предупреждений нет"
              />
            )}
          </div>
        </Block>

        {loading ? (
          <div className={styles.loading}>
            Обновляю данные…
          </div>
        ) : null}

      </main>
    </Shell>
  );
}

function Metric(
  p: {
    name: string;
    value: string;
    note: string;
  }
) {
  return (
    <div className={styles.metric}>
      <span>{p.name}</span>
      <strong>{p.value}</strong>
      <small>{p.note}</small>
    </div>
  );
}

function Block(
  p: {
    title: string;
    children: React.ReactNode;
  }
) {
  return (
    <section className={styles.block}>
      <header>
        <h2>{p.title}</h2>
      </header>
      {p.children}
    </section>
  );
}

function Table(
  p: {
    heads: string[];
    rows: any[][];
  }
) {
  if (!p.rows.length) {
    return (
      <Empty
        text="Нет данных за выбранный период"
      />
    );
  }

  return (
    <div className={styles.table}>
      <table>
        <thead>
          <tr>
            {p.heads.map(x => (
              <th key={x}>
                {x}
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {p.rows.map((r,i) => (
            <tr key={i}>
              {r.map((v,j) => (
                <td key={j}>
                  {String(v ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Empty(
  {text}: {text:string}
) {
  return (
    <div className={styles.empty}>
      {text}
    </div>
  );
}
