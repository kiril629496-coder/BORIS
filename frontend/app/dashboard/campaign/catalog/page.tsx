"use client";

/**
 * Ассортимент — вкладка ЕЦО.
 *
 * Клиент видит не таблицу с полями, а список товаров и честный ответ, чего
 * каждому не хватает для объявления. Импорт идёт через превью маппинга:
 * сначала показываем, что распознали, и только после подтверждения пишем.
 */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card, Button, Input, Alert, Skeleton } from "../../../ui";
import { EcShell, EcEmpty } from "../../../ui/EcShell";
import { ProductCard, EcKpiRow } from "../../../ui/ec";
import { color, font, radius, space } from "../../../ui/tokens";
import { apiGet, apiPost, apiUpload } from "../../../lib/api";
import { useAccounts } from "../../../lib/AccountContext";

export default function CatalogPage() {
  const router = useRouter();
  const { selectedAccountId } = useAccounts();
  const [account, setAccount] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [sum, setSum] = useState<any>({});
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [open, setOpen] = useState<any>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [file, setFile] = useState<any>(null);

  useEffect(() => { setAccount(selectedAccountId || ""); }, [selectedAccountId]);
  useEffect(() => {
    const t = setTimeout(() => setQuery(q), 400);
    return () => clearTimeout(t);
  }, [q]);

  const load = useCallback(async () => {
    if (!account) { setLoading(false); return; }
    setLoading(true); setErr("");
    const p = new URLSearchParams({ account_id: account, limit: "60" });
    if (query) p.set("q", query);
    const [r, s] = await Promise.all([
      apiGet("/api/catalog/list?" + p.toString()),
      apiGet("/api/catalog/summary?account_id=" + account),
    ]);
    if (r.status === "error") { setErr(r.message || "Не удалось загрузить"); }
    else { setItems(r.items || []); setTotal(r.total || 0); }
    if (s.status === "ok") setSum(s.summary || {});
    setLoading(false);
  }, [account, query]);
  useEffect(() => { load(); }, [load]);

  async function create() {
    if (!name.trim()) return;
    setCreating(true);
    const r = await apiPost("/api/catalog/product",
                            { account_id: account, name: name.trim() });
    setCreating(false);
    if (r.status === "error") { setErr(r.message || "Не удалось создать"); return; }
    setName(""); setNote("Товар создан. Заполните недостающие данные.");
    load();
  }

  async function doPreview(f: any) {
    if (!f) return;
    setFile(f); setErr(""); setNote("");
    const form = new FormData();
    form.append("account_id", account);
    form.append("file", f);
    const r = await apiUpload("/api/catalog/import/preview", form);
    if (r.status === "error") { setErr(r.message || "Файл не разобрался"); return; }
    setPreview(r);
  }

  async function doImport(dry: boolean) {
    if (!file || !preview) return;
    const form = new FormData();
    form.append("account_id", account);
    form.append("file", file);
    form.append("mapping", JSON.stringify(preview.mapping));
    form.append("dry_run", dry ? "1" : "0");
    const r = await apiUpload("/api/catalog/import/apply", form);
    if (r.status === "error") { setErr(r.message || "Импорт не прошёл"); return; }
    setNote(dry
      ? "Будет создано товаров: " + r.created + ", уже есть: " + r.updated_existing +
        ", пропущено строк: " + r.skipped
      : "Импортировано: " + r.created + ". Уже было: " + r.updated_existing);
    if (!dry) { setPreview(null); setFile(null); load(); }
  }

  return (
    <EcShell tab="catalog" title="Ассортимент" crumbs={["Ассортимент"]}
      subtitle="Товары и услуги бизнеса — основа для объявлений и фотосессий">
      {err !== "" && <Alert kind="danger">{err}</Alert>}
      {note !== "" && <Alert kind="info">{note}</Alert>}

      {loading ? (
        <Card><Skeleton lines={5} /></Card>
      ) : !account ? (
        <EcEmpty title="Выберите аккаунт"
          hint="Каталог ведётся отдельно по каждому Avito-аккаунту." />
      ) : (
        <>
          <EcKpiRow items={[
            { label: "Всего товаров", value: sum.total || 0 },
            { label: "Готовы к объявлениям", value: sum.ready || 0,
              tone: (sum.ready || 0) > 0 ? "green" : "neutral" },
            { label: "Без цены", value: sum.no_price || 0,
              tone: (sum.no_price || 0) > 0 ? "orange" : "neutral" },
            { label: "Без фотографий", value: sum.no_photo || 0,
              tone: (sum.no_photo || 0) > 0 ? "orange" : "neutral" },
          ]} />

          <Card>
            <div data-boris-g32b-page="campaign" style={{ ...font.h3, marginBottom: space.md }}>Добавить товар</div>
            <div style={{ display: "flex", gap: space.md, flexWrap: "wrap",
                          alignItems: "center" }}>
              <div className="boris-f3-6fd77f7f6cf1f7">
                <Input value={name} placeholder="Название товара или услуги"
                       onChange={(e: any) => setName(e.target.value)} />
              </div>
              <Button onClick={create} disabled={creating || !name.trim()}>
                {creating ? "Создаю…" : "Создать"}
              </Button>
              <label>
                <input type="file" accept=".csv,.xlsx,.xlsm"
                       onChange={(e: any) => doPreview(e.target.files && e.target.files[0])}  className="boris-f3-d0466aa33fa806"/>
                <span data-boris-g32b-card="1" style={{ display: "inline-block", padding: "9px 16px",
                               border: `1px solid ${color.line}`, borderRadius: radius.md,
                               cursor: "pointer", ...font.body }}>
                  Импорт из Excel или CSV
                </span>
              </label>
            </div>
          </Card>

          {preview && (
            <Card style={{ marginTop: space.lg }}>
              <div style={{ ...font.h3, marginBottom: space.md }}>
                Что я распознал в файле
              </div>
              {(preview.problems || []).length > 0 && (
                <Alert kind="warning">{(preview.problems || []).join("; ")}</Alert>
              )}
              <div style={{ display: "grid", gap: 6, marginBottom: space.lg }}>
                {(preview.mapping || []).map((m: any) => (
                  <div key={m.index} style={{ ...font.body }}>
                    <b style={{ color: color.heading }}>{m.header || "(без названия)"}</b>
                    {" → "}
                    {m.field
                      ? <span style={{ color: m.needs_confirm ? color.orange : color.green }}>
                          {FIELD_RU[m.field] || m.field}
                          {m.needs_confirm ? " (проверьте)" : ""}
                        </span>
                      : <span style={{ color: color.muted }}>не импортируется</span>}
                  </div>
                ))}
              </div>
              <div style={{ ...font.small, marginBottom: space.lg }}>
                Строк с данными в файле: {preview.sample_count}
              </div>
              <div style={{ display: "flex", gap: space.md }}>
                <Button kind="secondary" onClick={() => doImport(true)}>
                  Показать, что будет
                </Button>
                <Button onClick={() => doImport(false)}>Импортировать</Button>
                <Button kind="ghost" onClick={() => { setPreview(null); setFile(null); }}>
                  Отмена
                </Button>
              </div>
            </Card>
          )}

          <div style={{ margin: space.xxl + "px 0 " + space.lg + "px",
                        display: "flex", gap: space.md, alignItems: "center" }}>
            <div className="boris-f3-6fd77f7f6cf1f7">
              <Input value={q} placeholder="Поиск по названию и артикулу"
                     onChange={(e: any) => setQ(e.target.value)} />
            </div>
            <div style={{ ...font.small }}>Показано {items.length} из {total}</div>
          </div>

          {items.length === 0 ? (
            <EcEmpty title="Товаров пока нет"
              hint="Создайте первый товар или импортируйте прайс из Excel — BORIS сам скажет, каких данных не хватает для объявлений." />
          ) : (
            <div style={{ display: "grid", gap: space.md,
                          gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
              {items.map((p: any) => (
                <ProductCard key={p.id} p={p} onOpen={setOpen} />
              ))}
            </div>
          )}

          {open && (
            <div onClick={() => setOpen(null)}
              style={{ position: "fixed", inset: 0, background: "rgba(11,16,32,.45)",
                       display: "flex", alignItems: "center", justifyContent: "center",
                       padding: space.xl, zIndex: 60 }}>
              <div data-boris-g32b-card="1" onClick={(e) => e.stopPropagation()}
                style={{ background: color.surface, borderRadius: radius.lg,
                         padding: space.xxl, maxWidth: 620, width: "100%",
                         maxHeight: "84vh", overflowY: "auto" }}>
                <div style={{ ...font.h2, marginBottom: 6 }}>{open.name}</div>
                <div style={{ ...font.small, marginBottom: space.xl }}>
                  {[open.sku, open.category].filter(Boolean).join(" · ") || "без артикула"}
                </div>
                {(open.missing || []).filter((m: any) => m.blocking).length > 0 && (
                  <Alert kind="warning">
                    Не хватает для объявления:{" "}
                    {(open.missing || []).filter((m: any) => m.blocking)
                      .map((m: any) => m.label).join(", ")}
                  </Alert>
                )}
                <div style={{ ...font.body, margin: space.lg + "px 0" }}>
                  {open.description || "Описание не заполнено"}
                </div>
                <div style={{ ...font.bodyStrong, marginBottom: space.lg }}>
                  {open.price ? open.price.toLocaleString("ru-RU") + " ₽" : "Цена не указана"}
                </div>
                {(open.media || []).length > 0 && (
                  <div style={{ display: "grid", gap: 8, marginBottom: space.lg,
                                gridTemplateColumns: "repeat(auto-fill, minmax(90px, 1fr))" }}>
                    {(open.media || []).slice(0, 8).map((m: any) => (
                      m.thumb_url ? <img key={m.id} src={m.thumb_url} alt=""
                        style={{ width: "100%", aspectRatio: "1/1", objectFit: "cover",
                                 borderRadius: radius.sm }} /> : null
                    ))}
                  </div>
                )}
                <div style={{ display: "flex", gap: space.md, flexWrap: "wrap" }}>
                  <Button onClick={() => router.push("/dashboard/campaign/photo?product=" + open.id)}>
                    Сделать ИИ-фотосессию
                  </Button>
                  <Button kind="secondary"
                    onClick={() => router.push("/dashboard/campaign/media")}>
                    Добавить фотографии
                  </Button>
                  <Button kind="ghost" onClick={() => setOpen(null)}>Закрыть</Button>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </EcShell>
  );
}

const FIELD_RU: Record<string, string> = {
  name: "Название", sku: "Артикул", price: "Цена", price_from: "Цена от",
  price_to: "Цена до", category: "Категория", description: "Описание", geo: "Города",
};
