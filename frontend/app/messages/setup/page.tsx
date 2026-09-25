"use client";

import { useEffect, useState } from "react";
import {
  Page, PageHeader, Card, Button, Input, Field, Badge, Alert, EmptyState, IconChip,
} from "../../ui";
import { color, space, font, radius } from "../../ui/tokens";

type Slot = {
  id: number; slot_no: number; status: string; account_id: string; account_name: string;
  avito_user_id: string; display_phone: string; tariff_ok: boolean | null;
  last_check_result: string; paid_until: string; dialogs: number; unanswered: number;
};
type PriceRow = { slots: number; per_slot: number; total: number };

const STATUS: Record<string, { label: string; kind: "neutral" | "info" | "success" | "warning" | "danger" }> = {
  paid_empty: { label: "Оплачен, свободен", kind: "info" },
  connecting: { label: "Проверяем", kind: "warning" },
  connected: { label: "Подключён", kind: "success" },
  error: { label: "Ошибка", kind: "danger" },
  readonly: { label: "Только чтение", kind: "neutral" },
  released: { label: "Освобождён", kind: "neutral" },
};

function api(path: string, init?: RequestInit) {
  const token =
    typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  return fetch(path, {
    ...(init || {}),
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + token,
      ...((init && (init.headers as Record<string, string>)) || {}),
    },
  });
}

export default function SlotsSetupPage() {
  const [slots, setSlots] = useState<Slot[]>([]);
  const [table, setTable] = useState<PriceRow[]>([]);
  const [owned, setOwned] = useState(0);
  const [totalNow, setTotalNow] = useState(0);
  const [addOne, setAddOne] = useState(0);
  const [openId, setOpenId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState("");
  const [err, setErr] = useState(false);
  const [form, setForm] = useState({ cid: "", secret: "", phone: "" });

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    try {
      const [s, p] = await Promise.all([
        api("/api/inbox/slots"), api("/api/inbox/pricing"),
      ]);
      if (!s.ok || !p.ok) {
        throw new Error(s.status === 401 || p.status === 401 ? "Нужна авторизация BORIS" : "Не удалось загрузить подключение аккаунтов");
      }
      const slotsData = await s.json();
      const priceData = await p.json();
      setSlots(slotsData.slots || []);
      setTable(priceData.table || []); setOwned(priceData.owned || 0);
      setTotalNow(priceData.total_now || 0); setAddOne(priceData.add_one || 0);
      setErr(false);
    } catch (e) {
      setSlots([]);
      setErr(true);
      setNote(e instanceof Error ? e.message : "Не удалось загрузить слоты");
    } finally {
      setLoading(false);
    }
  }

  async function post(path: string, body: unknown, ok: string) {
    setBusy(true); setNote(""); setErr(false);
    try {
      const r = await api(path, { method: "POST", body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      setErr(!r.ok);
      setNote(d.message || d.detail || ok);
      if (r.ok && d.status === "ok") { setOpenId(null); setForm({ cid: "", secret: "", phone: "" }); }
      await load();
    } catch { setErr(true); setNote("Ошибка сети"); }
    setBusy(false);
  }

  const connected = slots.filter((s) => s.status === "connected").length;

  return (
    <Page>
      <PageHeader
        title="Подключение аккаунтов"
        subtitle={`Единый центр сообщений — ${connected} из ${slots.length} слотов подключено`}
      />

      {note ? <Alert kind={err ? "danger" : "success"}>{note}</Alert> : null}

      <div style={{ display: "flex", gap: space.lg, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div className="boris-f3-5cbe99176a5b07">
          {loading ? (
            <Card><div style={font.body as React.CSSProperties}>Загружаем подключённые аккаунты…</div></Card>
          ) : !err && !slots.length ? (
            <EmptyState title="Оплаченных слотов пока нет"
                        hint="После подтверждения оплаты слоты появятся здесь автоматически." />
          ) : null}

          {slots.map((s) => {
            const st = STATUS[s.status] || { label: "Неизвестный статус", kind: "neutral" as const };
            const open = openId === s.id;
            return (
              <Card key={s.id} padding={space.lg} style={{ marginBottom: space.md }}>
                <div style={{ display: "flex", alignItems: "center", gap: space.md }}>
                  <IconChip name={s.status === "connected" ? "check" : "plug"}
                            kind={st.kind === "neutral" ? "neutral" : st.kind} size={38} />
                  <div className="boris-f3-6253876a649a63">
                    <div style={font.bodyStrong as React.CSSProperties}>
                      {s.account_name || "Слот " + s.slot_no}
                    </div>
                    <div style={{ ...font.small, marginTop: 2 }}>
                      {s.account_id ? "ID Авито: " + (s.avito_user_id || "—") : "аккаунт не подключён"}
                      {s.paid_until ? " · оплачен до " + s.paid_until.slice(0, 10) : ""}
                    </div>
                  </div>
                  <Badge kind={st.kind}>{st.label}</Badge>
                </div>

                {s.status === "connected" ? (
                  <div style={{ display: "flex", gap: space.xl, marginTop: space.md,
                                flexWrap: "wrap", ...font.small }}>
                    <span>диалогов <b style={{ color: color.heading }}>{s.dialogs}</b></span>
                    <span>не отвечено{" "}
                      <b style={{ color: s.unanswered ? color.red : color.heading }}>
                        {s.unanswered}</b>
                    </span>
                    <span>телефон <b style={{ color: color.heading }}>
                      {s.display_phone || "не указан"}</b></span>
                    <span>автозагрузка{" "}
                      <b style={{ color: s.tariff_ok === false ? color.red : color.heading }}>
                        {s.tariff_ok === true ? "доступна"
                          : s.tariff_ok === false ? "нет тарифа" : "не проверена"}</b>
                    </span>
                  </div>
                ) : null}

                {s.last_check_result ? (
                  <div style={{ ...font.small, marginTop: space.sm }}>
                    {s.last_check_result.split("|")[0]}
                  </div>
                ) : null}

                <div style={{ display: "flex", gap: space.sm, marginTop: space.md, flexWrap: "wrap" }}>
                  <Button kind={s.status === "connected" ? "ghost" : "primary"}
                          disabled={busy || s.status === "readonly"}
                          onClick={() => setOpenId(open ? null : s.id)}>
                    {s.status === "connected" ? "Заменить ключи" : "Подключить аккаунт"}
                  </Button>
                  {s.account_id ? (
                    <Button kind="ghost" disabled={busy}
                            onClick={() => post("/api/inbox/slots/check", { slot_id: s.id }, "Проверено")}>
                      Проверить связь
                    </Button>
                  ) : null}
                  {s.account_id ? (
                    <Button kind="danger" disabled={busy}
                            onClick={() => {
                              if (window.confirm("Освободить этот слот? Аккаунт будет отключён от Единого центра сообщений.")) {
                                void post("/api/inbox/slots/release", { slot_id: s.id }, "Слот освобождён");
                              }
                            }}>
                      Освободить слот
                    </Button>
                  ) : null}
                </div>

                {open ? (
                  <div style={{ marginTop: space.md, padding: space.lg,
                                background: color.surfaceAlt, borderRadius: radius.md }}>
                    <Field label="Client ID">
                      <Input value={form.cid} placeholder="из кабинета Авито"
                             onChange={(e) => setForm({ ...form, cid: e.target.value })} />
                    </Field>
                    <Field label="Client Secret">
                      <Input value={form.secret} placeholder="секретный ключ"
                             onChange={(e) => setForm({ ...form, secret: e.target.value })} />
                    </Field>
                    <Field label="Телефон аккаунта"
                           hint="Необязательно — Авито обычно отдаёт его сам">
                      <Input value={form.phone}
                             onChange={(e) => setForm({ ...form, phone: e.target.value })} />
                    </Field>
                    <div style={{ display: "flex", gap: space.sm, flexWrap: "wrap" }}>
                      <Button disabled={busy || !form.cid || !form.secret}
                              onClick={() => post("/api/inbox/slots/connect", {
                                slot_id: s.id, avito_client_id: form.cid,
                                avito_client_secret: form.secret, display_phone: form.phone,
                              }, "Аккаунт подключён")}>
                        Проверить и сохранить
                      </Button>
                      {s.account_id ? (
                        <Button kind="ghost" disabled={busy}
                                onClick={() => post("/api/inbox/slots/phone",
                                  { slot_id: s.id, display_phone: form.phone }, "Телефон сохранён")}>
                          Сохранить только телефон
                        </Button>
                      ) : null}
                    </div>
                    <div style={{ ...font.small, marginTop: space.md }}>
                      Ключи берутся в кабинете Авито: Профиль → Настройки → Доступ к API.
                      Для автозагрузки нужен тариф «Расширенный» или «Максимальный».
                    </div>
                  </div>
                ) : null}
              </Card>
            );
          })}
        </div>

        <div className="boris-f3-66930e0ce6763e">
          <Card>
            <div style={font.h3 as React.CSSProperties}>Подключение</div>
            <div style={{ ...font.kpi, color: color.blue, marginTop: space.md }}>
              {totalNow.toLocaleString("ru-RU")} ₽
            </div>
            <div style={font.small as React.CSSProperties}>
              за {owned} {owned === 1 ? "слот" : "слотов"} в месяц
            </div>
            <div style={{ ...font.small, marginTop: space.sm }}>
              Следующий слот: +{addOne.toLocaleString("ru-RU")} ₽ / мес
            </div>
          </Card>

          <div style={{ ...font.small, marginTop: space.md, color: color.muted }}>
            Слоты выдаются автоматически после подтверждённой оплаты.
          </div>
        </div>
      </div>
    </Page>
  );
}

// BORIS_HIDE_CLIENT_INBOX_PRICES_V1
