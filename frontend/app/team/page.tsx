"use client";

import React from "react";
import { Page, Card, Button } from "../ui";
import AppShell from "../components/shell/AppShell";
import { useAccounts } from "../lib/AccountContext";

const BLUE = "var(--boris-blue)";

type Acc = { account_id: string; can_reply: boolean; can_manage_ads: boolean; can_view_reports: boolean };
type Emp = { user_id: number; email: string; is_active: boolean; created_at: string | null; accounts: Acc[] };

export default function TeamPage() {
  const ctx = useAccounts();
  const [list, setList] = React.useState<Emp[]>([]);
  const [err, setErr] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [open, setOpen] = React.useState(false);
  const [created, setCreated] = React.useState<any>(null);
  const [email, setEmail] = React.useState("");
  const [picked, setPicked] = React.useState<string[]>([]);
  const [reports, setReports] = React.useState(false);
  const [ads, setAds] = React.useState(false);

  const tok = () => (typeof window === "undefined" ? "" : localStorage.getItem("boris_token") || "");

  const load = React.useCallback(async () => {
    try {
      const r = await fetch("/api/team/list", { headers: { Authorization: "Bearer " + tok() } });
      const d = await r.json();
      if (d && d.status === "ok") { setList(d.employees || []); setErr(""); }
      else setErr("Не удалось загрузить список");
    } catch { setErr("Сервер не ответил"); }
  }, []);

  React.useEffect(() => { load(); }, [load]);

  const add = async () => {
    if (!email.trim() || picked.length === 0) return;
    setBusy(true);
    try {
      const r = await fetch("/api/team/create", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + tok() },
        body: JSON.stringify({ email: email.trim(), account_ids: picked, can_reply: true, can_manage_ads: ads, can_view_reports: reports }),
      });
      const d = await r.json();
      if (d && d.status === "ok") {
        setCreated(d); setOpen(false); setEmail(""); setPicked([]); setAds(false); setReports(false); load();
      } else setErr(d?.detail || "Не удалось создать сотрудника");
    } catch { setErr("Сервер не ответил"); }
    setBusy(false);
  };

  const revoke = async (uid: number, acc?: string) => {
    if (!confirm(acc ? "Убрать доступ к этому аккаунту?" : "Убрать сотрудника полностью? Он потеряет доступ, но история ответов сохранится.")) return;
    await fetch("/api/team/revoke", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + tok() },
      body: JSON.stringify(acc ? { user_id: uid, account_id: acc } : { user_id: uid }),
    });
    load();
  };

  const nameOf = (id: string) => ctx.accounts.find((a: any) => a.account_id === id)?.name || id;

  return (
    <AppShell
      active="team"
      title="Пользователи и доступы"
      subtitle="Сотрудники, которые работают в кабинете под своими логинами"
      headerExtra={
        <Button onClick={() => setOpen(true)}>
          Добавить сотрудника
        </Button>
      }
    >
      <Page>

      {err && <div data-boris-g33="team" className="boris-f3-58172e0ff0c521">{err}</div>}

      {created && (
        <Card>
          <div className="boris-f3-3b2cdac893f750">
            <div className="boris-f3-35bf9da77b9aed">Сотрудник создан</div>
            <div className="boris-f3-55d0601d1e44ae">
              Пароль показывается один раз — сохраните и передайте лично.
            </div>
            <div className="boris-f3-5fa1a763790fe5">
              <code className="boris-f3-1b2233da4c81e4">
                {created.email} · {created.password}
              </code>
              <Button onClick={() => { navigator.clipboard.writeText(created.email + " / " + created.password); }}>Скопировать</Button>
              <button onClick={() => setCreated(null)} className="boris-f3-2f1f05ff531034">Скрыть</button>
            </div>
          </div>
        </Card>
      )}

      {open && (
        <Card>
          <div className="boris-f3-3b2cdac893f750">
            <div className="boris-f3-04349470938de6">Новый сотрудник</div>
            <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Рабочая почта"
  className="boris-f3-4b0e2b0d3ee898"/>
            <div className="boris-f3-eca52be4e7695e">К каким аккаунтам открыть доступ</div>
            <div className="boris-f3-08382589a7eba8">
              {ctx.accounts.map((a: any) => (
                <label key={a.account_id} className="boris-f3-b5c38db291c1a7">
                  <input type="checkbox" checked={picked.includes(a.account_id)}
                    onChange={(e) => setPicked(e.target.checked ? [...picked, a.account_id] : picked.filter((x) => x !== a.account_id))}
  className="boris-f3-d802a6f8c3a28f"/>
                  {a.name}
                </label>
              ))}
            </div>
            <div className="boris-f3-eca52be4e7695e">Что разрешить сверх переписки</div>
            <label className="boris-f3-ed2d114a8870cd">
              <input type="checkbox" checked={reports} onChange={(e) => setReports(e.target.checked)}  className="boris-f3-d802a6f8c3a28f"/>
              Видеть отчёты
            </label>
            <label className="boris-f3-b5c38db291c1a7">
              <input type="checkbox" checked={ads} onChange={(e) => setAds(e.target.checked)}  className="boris-f3-d802a6f8c3a28f"/>
              Управлять объявлениями
            </label>
            <div className="boris-f3-a4c1c4f74b2de9">
              <Button onClick={add} disabled={busy || !email.trim() || picked.length === 0}>
                {busy ? "Создаю…" : "Создать и выдать пароль"}
              </Button>
              <button onClick={() => setOpen(false)} className="boris-f3-2f1f05ff531034">Отмена</button>
            </div>
          </div>
        </Card>
      )}

      {list.length === 0 && !open && (
        <Card>
          <div className="boris-f3-44757431b39312">
            <div className="boris-f3-54b2e2af0579e3">Сотрудников пока нет</div>
            <div className="boris-f3-905228b6d52d18">Добавьте людей, которые будут отвечать клиентам — каждый под своим логином.</div>
          </div>
        </Card>
      )}

      {list.map((e) => (
        <Card key={e.user_id}>
          <div className="boris-f3-3b2cdac893f750">
            <div className="boris-f3-c7149680de3f70">
              <div>
                <div className="boris-f3-ed5b5b926caafd">{e.email}</div>
                <div style={{ fontSize: 13, color: e.is_active ? "#12B76A" : "#B42318" }}>
                  {e.is_active ? "Работает" : "Доступ закрыт"}
                </div>
              </div>
              <button onClick={() => revoke(e.user_id)} className="boris-f3-975f113af95524">
                Убрать сотрудника
              </button>
            </div>
            <div className="boris-f3-26cc1cde1da030">
              {e.accounts.map((a) => (
                <div key={a.account_id} className="boris-f3-1e20e12b64ee62">
                  <span data-boris-g33-card="1" style={{ background: "var(--boris-blue-soft)", color: BLUE, padding: "3px 9px", borderRadius: 6, fontSize: 13 }}>{nameOf(a.account_id)}</span>
                  <span className="boris-f3-498e792763208b">
                    отвечает{a.can_view_reports ? " · отчёты" : ""}{a.can_manage_ads ? " · объявления" : ""}
                  </span>
                  <button onClick={() => revoke(e.user_id, a.account_id)} className="boris-f3-d95c37e06698f1">убрать</button>
                </div>
              ))}
            </div>
          </div>
        </Card>
      ))}
    </Page>
    </AppShell>
  );
}
