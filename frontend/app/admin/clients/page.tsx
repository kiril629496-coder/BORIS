"use client";

/* Администрирование → Клиенты.
   Отдельная страница: свои инлайн-стили и свой fetch, без зависимости от
   оболочки кабинета (её сейчас переписывают) и от app/lib/api.ts.
   Ключ токена тот же, что у всего фронта: boris_token. */

import { useState } from "react";

type Client = { id: number; email: string; role: string; status: string };

const S: any = {
  page: { maxWidth: 1000, margin: "0 auto", padding: 24, fontFamily: "system-ui, sans-serif", color: "#1B2437" },
  h1: { fontSize: 24, fontWeight: 800, margin: "0 0 4px" },
  sub: { color: "#6B7688", fontSize: 14, margin: "0 0 20px" },
  card: { background: "#fff", border: "1px solid #E3E7F0", borderRadius: 16, padding: 20, marginBottom: 16 },
  row: { display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" },
  input: { border: "1px solid #D6DCE8", borderRadius: 10, padding: "10px 12px", fontSize: 14, minWidth: 180 },
  label: { fontSize: 12, color: "#6B7688", display: "block", marginBottom: 4 },
  btn: { background: "#2F6BFF", color: "#fff", border: 0, borderRadius: 10, padding: "10px 18px", fontSize: 14, fontWeight: 600, cursor: "pointer" },
  btnGhost: { background: "#fff", color: "#2F6BFF", border: "1px solid #2F6BFF", borderRadius: 10, padding: "10px 18px", fontSize: 14, fontWeight: 600, cursor: "pointer" },
  btnDanger: { background: "#E5484D", color: "#fff", border: 0, borderRadius: 10, padding: "10px 18px", fontSize: 14, fontWeight: 700, cursor: "pointer" },
  item: { padding: "10px 12px", border: "1px solid #E3E7F0", borderRadius: 10, marginBottom: 8, cursor: "pointer", background: "#fff" },
  tag: { display: "inline-block", fontSize: 12, padding: "2px 8px", borderRadius: 999, background: "#EEF3FF", color: "#2F6BFF", marginLeft: 8 },
  plan: { background: "#FFF8E6", border: "1px solid #F5D98B", borderRadius: 12, padding: 16, marginTop: 12 },
  err: { background: "#FDECEC", border: "1px solid #F3B5B5", borderRadius: 12, padding: 12, marginTop: 12, color: "#9B1C1C" },
  okBox: { background: "#EAF7EE", border: "1px solid #A9DDB9", borderRadius: 12, padding: 12, marginTop: 12, color: "#14663A" },
  th: { textAlign: "left", fontSize: 12, color: "#6B7688", fontWeight: 600, padding: "6px 8px" },
  td: { fontSize: 13, padding: "6px 8px", borderTop: "1px solid #EEF1F6" },
};

async function api(path: string, method = "GET", body?: any) {
  const token = typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "";
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: any = {};
  try { data = await res.json(); } catch { data = {}; }
  return { status: res.status, data };
}

export default function AdminClientsPage() {
  const [q, setQ] = useState("");
  const [list, setList] = useState<Client[]>([]);
  const [card, setCard] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [plan, setPlan] = useState<string[] | null>(null);
  const [result, setResult] = useState<any>(null);

  const [tier, setTier] = useState("");
  const [slots, setSlots] = useState("");
  const [slotDays, setSlotDays] = useState("30");
  const [amount, setAmount] = useState("");
  const [periodDays, setPeriodDays] = useState("30");
  const [paidAt, setPaidAt] = useState("");
  const [paymentId, setPaymentId] = useState("");
  const [comment, setComment] = useState("");
  const [rop, setRop] = useState(false);
  const [ropRenew, setRopRenew] = useState(false);
  const [ropMinutes, setRopMinutes] = useState("1500");
  const [ropChats, setRopChats] = useState("450");
  const [ropRepCalls, setRopRepCalls] = useState("30");
  const [ropRepChats, setRopRepChats] = useState("30");
  const [ropDays, setRopDays] = useState("30");

  function reset() {
    setPlan(null); setResult(null); setError("");
  }

  async function doSearch() {
    setBusy(true); reset(); setCard(null);
    const r = await api("/api/admin/clients/search?q=" + encodeURIComponent(q));
    setBusy(false);
    if (r.status !== 200) { setError("Поиск не удался: " + r.status); return; }
    setList(r.data.clients || []);
  }

  async function openCard(id: number) {
    setBusy(true); reset();
    const r = await api("/api/admin/clients/card?user_id=" + id);
    setBusy(false);
    if (r.status !== 200) { setError("Карточка не открылась: " + r.status); return; }
    setCard(r.data);
  }

  function payload(dry: boolean) {
    return {
      user_id: card.user.id,
      tier, slots: Number(slots) || 0, slot_days: Number(slotDays) || 30,
      amount_rub: Number(amount) || 0, period_days: Number(periodDays) || 30,
      paid_at: paidAt || null, payment_id: paymentId, comment,
      rop, rop_renew: ropRenew,
      rop_minutes: Number(ropMinutes) || 0, rop_chats: Number(ropChats) || 0,
      rop_rep_calls: Number(ropRepCalls) || 0, rop_rep_chats: Number(ropRepChats) || 0,
      rop_days: Number(ropDays) || 30,
      dry_run: dry,
    };
  }

  async function preview() {
    setBusy(true); reset();
    const r = await api("/api/admin/clients/provision", "POST", payload(true));
    setBusy(false);
    if (r.status !== 200) { setError("Предпросмотр не удался: " + r.status); return; }
    if (r.data.status === "error") { setError(r.data.message || "Нечего применять"); return; }
    setPlan(r.data.plan || []);
  }

  async function apply() {
    if (!plan) return;
    if (!confirm("Применить изменения? Слоты и оплата будут записаны.")) return;
    setBusy(true); setError("");
    const r = await api("/api/admin/clients/provision", "POST", payload(false));
    setBusy(false);
    if (r.status !== 200) { setError("Применение не удалось: " + r.status); return; }
    setResult(r.data);
    setPlan(null);
    if (r.data.after) setCard({ status: "ok", ...r.data.after });
  }

  return (
    <div style={S.page}>
      <h1 style={S.h1}>Администрирование · Клиенты</h1>
      <p style={S.sub}>Поиск клиента, тариф, слоты, оплата и комментарий — одним действием.</p>

      <div style={S.card}>
        <div style={S.row}>
          <div>
            <label style={S.label}>Email, название или account_id</label>
            <input style={{ ...S.input, minWidth: 320 }} value={q}
                   onChange={(e) => setQ(e.target.value)}
                   onKeyDown={(e) => { if (e.key === "Enter") doSearch(); }}
                   placeholder="например client@mail.ru" />
          </div>
          <button style={S.btn} onClick={doSearch} disabled={busy}>Найти</button>
        </div>

        {list.length > 0 && (
          <div style={{ marginTop: 16 }}>
            {list.map((c) => (
              <div key={c.id} style={S.item} onClick={() => openCard(c.id)}>
                <b>{c.email}</b>
                <span style={S.tag}>{c.role}</span>
                <span style={S.tag}>{c.status}</span>
                <span style={{ ...S.tag, background: "#F3F4F6", color: "#6B7688" }}>id {c.id}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {error && <div style={S.err}>{error}</div>}

      {card && card.user && (
        <>
          <div style={S.card}>
            <h2 style={{ fontSize: 18, fontWeight: 700, marginTop: 0 }}>{card.user.email}</h2>
            <div style={{ fontSize: 13, color: "#6B7688", marginBottom: 12 }}>
              id {card.user.id} · роль {card.user.role} · статус {card.user.status} ·
              основной аккаунт {card.user.account_id || "—"}
            </div>

            <div style={{ fontSize: 13, fontWeight: 700, margin: "12px 0 4px" }}>
              Аккаунты ({(card.accounts || []).length})
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>
                <th style={S.th}>account_id</th><th style={S.th}>название</th><th style={S.th}>биллинг</th>
              </tr></thead>
              <tbody>
                {(card.accounts || []).map((a: any) => (
                  <tr key={a.account_id}>
                    <td style={S.td}>{a.account_id}</td>
                    <td style={S.td}>{a.name}</td>
                    <td style={S.td}>{a.billing_mode}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div style={{ fontSize: 13, fontWeight: 700, margin: "16px 0 4px" }}>
              Слоты ({(card.slots || []).length})
            </div>
            {(card.slots || []).length === 0
              ? <div style={{ fontSize: 13, color: "#6B7688" }}>слотов нет</div>
              : (
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead><tr>
                    <th style={S.th}>№</th><th style={S.th}>статус</th><th style={S.th}>аккаунт</th>
                    <th style={S.th}>цена</th><th style={S.th}>оплачен до</th>
                  </tr></thead>
                  <tbody>
                    {(card.slots || []).map((s: any) => (
                      <tr key={s.id}>
                        <td style={S.td}>{s.slot_no}</td>
                        <td style={S.td}>{s.status}</td>
                        <td style={S.td}>{s.account_id || "—"}</td>
                        <td style={S.td}>{s.price_rub} ₽</td>
                        <td style={S.td}>{(s.paid_until || "").slice(0, 10) || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
          </div>

          <div style={S.card}>
            <h2 style={{ fontSize: 18, fontWeight: 700, marginTop: 0 }}>Оформление</h2>
            <div style={S.row}>
              <div>
                <label style={S.label}>Тариф</label>
                <select style={S.input} value={tier} onChange={(e) => { setTier(e.target.value); reset(); }}>
                  <option value="">не менять</option>
                  <option value="tariff_1">Автопилот 2.0 (tariff_1)</option>
                  <option value="tariff_2">Автопилот MAX (tariff_2)</option>
                  <option value="none">снять тариф</option>
                </select>
              </div>
              <div>
                <label style={S.label}>Слотов всего должно быть</label>
                <input style={{ ...S.input, minWidth: 110 }} type="number" min={0} max={50}
                       value={slots} onChange={(e) => { setSlots(e.target.value); reset(); }} />
              </div>
              <div>
                <label style={S.label}>Слоты на дней</label>
                <input style={{ ...S.input, minWidth: 110 }} type="number" min={1}
                       value={slotDays} onChange={(e) => { setSlotDays(e.target.value); reset(); }} />
              </div>
            </div>

            <div style={{ ...S.row, marginTop: 12 }}>
              <div>
                <label style={S.label}>Оплачено, ₽</label>
                <input style={{ ...S.input, minWidth: 140 }} type="number" min={0}
                       value={amount} onChange={(e) => { setAmount(e.target.value); reset(); }} />
              </div>
              <div>
                <label style={S.label}>Период, дней</label>
                <input style={{ ...S.input, minWidth: 110 }} type="number" min={1}
                       value={periodDays} onChange={(e) => { setPeriodDays(e.target.value); reset(); }} />
              </div>
              <div>
                <label style={S.label}>Дата оплаты</label>
                <input style={{ ...S.input, minWidth: 150 }} type="date"
                       value={paidAt} onChange={(e) => { setPaidAt(e.target.value); reset(); }} />
              </div>
              <div>
                <label style={S.label}>ID платежа</label>
                <input style={{ ...S.input, minWidth: 150 }} value={paymentId}
                       onChange={(e) => { setPaymentId(e.target.value); reset(); }} />
              </div>
            </div>

            <div style={{ marginTop: 16, padding: 14, border: "1px solid #E3E7F0", borderRadius: 12 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input type="checkbox" checked={rop}
                       onChange={(e) => { setRop(e.target.checked); reset(); }} />
                <b style={{ fontSize: 14 }}>Начислить пакет РОП</b>
              </label>
              {rop && (
                <>
                  <div style={{ fontSize: 13, color: "#8A5A00", background: "#FFF8E6",
                                border: "1px solid #F5D98B", borderRadius: 8,
                                padding: "8px 10px", margin: "10px 0" }}>
                    РОП будет начислен на {(card.accounts || []).length} аккаунт(ов).
                    Каждый аккаунт получит ОТДЕЛЬНЫЙ лимит: {ropMinutes} минут,
                    {" "}{ropChats} разборов, {ropRepCalls} отчётов по звонкам и
                    {" "}{ropRepChats} по перепискам. Лимиты между аккаунтами не делятся.
                  </div>
                  <div style={S.row}>
                    <div>
                      <label style={S.label}>Минут</label>
                      <input style={{ ...S.input, minWidth: 110 }} value={ropMinutes}
                             onChange={(e) => { setRopMinutes(e.target.value); reset(); }} />
                    </div>
                    <div>
                      <label style={S.label}>Разборов переписок</label>
                      <input style={{ ...S.input, minWidth: 110 }} value={ropChats}
                             onChange={(e) => { setRopChats(e.target.value); reset(); }} />
                    </div>
                    <div>
                      <label style={S.label}>Отчётов по звонкам</label>
                      <input style={{ ...S.input, minWidth: 110 }} value={ropRepCalls}
                             onChange={(e) => { setRopRepCalls(e.target.value); reset(); }} />
                    </div>
                    <div>
                      <label style={S.label}>Отчётов по перепискам</label>
                      <input style={{ ...S.input, minWidth: 110 }} value={ropRepChats}
                             onChange={(e) => { setRopRepChats(e.target.value); reset(); }} />
                    </div>
                    <div>
                      <label style={S.label}>Дней</label>
                      <input style={{ ...S.input, minWidth: 90 }} value={ropDays}
                             onChange={(e) => { setRopDays(e.target.value); reset(); }} />
                    </div>
                  </div>
                  <label style={{ display: "flex", alignItems: "center", gap: 8,
                                  marginTop: 10, cursor: "pointer" }}>
                    <input type="checkbox" checked={ropRenew}
                           onChange={(e) => { setRopRenew(e.target.checked); reset(); }} />
                    <span style={{ fontSize: 13, color: "#9B1C1C" }}>
                      Начать новый период РОП (перезапишет активный период, израсходованное обнулится)
                    </span>
                  </label>
                </>
              )}
            </div>

            <div style={{ marginTop: 12 }}>
              <label style={S.label}>Комментарий (уходит в журнал)</label>
              <textarea style={{ ...S.input, width: "100%", minHeight: 70 }} value={comment}
                        onChange={(e) => { setComment(e.target.value); reset(); }}
                        placeholder="например: РОП — индивидуальный пакет на 2 аккаунта, 20 000 ₽" />
            </div>

            <div style={{ ...S.row, marginTop: 16 }}>
              <button style={S.btnGhost} onClick={preview} disabled={busy}>Показать план</button>
              <button style={plan ? S.btnDanger : { ...S.btnDanger, opacity: 0.4, cursor: "not-allowed" }}
                      onClick={apply} disabled={!plan || busy}>Применить</button>
              {!plan && <span style={{ fontSize: 12, color: "#6B7688" }}>
                сначала предпросмотр — без него запись невозможна
              </span>}
            </div>

            {plan && (
              <div style={S.plan}>
                <b>Будет выполнено:</b>
                <ul style={{ margin: "8px 0 0 18px", padding: 0 }}>
                  {plan.map((p, i) => <li key={i} style={{ fontSize: 14, marginBottom: 4 }}>{p}</li>)}
                </ul>
              </div>
            )}

            {result && (
              <div style={(result.errors || []).length ? S.err : S.okBox}>
                <b>Готово.</b>
                <ul style={{ margin: "8px 0 0 18px", padding: 0 }}>
                  {(result.done || []).map((d: string, i: number) => <li key={i}>{d}</li>)}
                </ul>
                {(result.errors || []).length > 0 && (
                  <>
                    <b>Ошибки:</b>
                    <ul style={{ margin: "8px 0 0 18px", padding: 0 }}>
                      {(result.errors || []).map((d: string, i: number) => <li key={i}>{d}</li>)}
                    </ul>
                  </>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
