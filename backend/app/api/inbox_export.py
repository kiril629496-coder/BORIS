"""Экспорт переписок «Единого окна» — PDF и CSV.
Движок PDF переиспользуется из проекта (WeasyPrint + DejaVu Sans, как в
calltracking.py/wallet.py). Свой генератор не создаётся.
"""
import csv
import datetime
import io

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.db.session import SessionLocal
from app.api.inbox_slots import _connected_account_ids
try:
    from app.api.auth import get_current_user
except ImportError:
    from app.auth import get_current_user

router = APIRouter(prefix="/api/inbox", tags=["inbox-export"])

MSK = datetime.timezone(datetime.timedelta(hours=3))
KIND = {"voice": "голосовое", "image": "фото", "video": "видео",
        "file": "файл", "link": "ссылка", "appCall": "звонок"}


def _ts(sec, fmt="%d.%m.%Y %H:%M"):
    if not sec:
        return ""
    try:
        return datetime.datetime.fromtimestamp(int(sec), tz=MSK).strftime(fmt)
    except Exception:
        return ""


def _esc(v):
    return (str(v or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\n", "<br>"))


def _guard(user, account_id):
    db = SessionLocal()
    try:
        conn = dict(_connected_account_ids(db, user))
    finally:
        db.close()
    return account_id in conn, conn.get(account_id, account_id)


def _period_sql(date_from, date_to):
    cond, params = "", {}
    if date_from:
        cond += " AND avito_created_at >= :df"
        params["df"] = int(datetime.datetime.strptime(date_from[:10], "%Y-%m-%d")
                           .replace(tzinfo=MSK).timestamp())
    if date_to:
        cond += " AND avito_created_at < :dt"
        params["dt"] = int((datetime.datetime.strptime(date_to[:10], "%Y-%m-%d")
                            .replace(tzinfo=MSK) + datetime.timedelta(days=1)).timestamp())
    return cond, params


def _fetch_messages(db, account_id, chat_id, date_from="", date_to=""):
    cond, params = _period_sql(date_from, date_to)
    params.update({"a": account_id, "c": chat_id})
    rows = db.execute(text(
        "SELECT direction, text, avito_created_at, item_title, msg_type, content_type, media_ref "
        "  FROM messenger_messages WHERE account_id=:a AND avito_chat_id=:c" + cond +
        " ORDER BY avito_created_at ASC, id ASC"), params).all()
    out = []
    for d, t, c, title, mt, ct, mr in rows:
        is_out = str(d or "").lower().startswith("out")
        out.append({
            "at": _ts(c), "ts": c or 0,
            "who": "Мы" if is_out else ("Avito" if mt == "system" else "Клиент"),
            "dir": "исходящее" if is_out else "входящее",
            "kind": KIND.get(ct or "text", "текст"),
            "text": t or "", "item": title or "", "media": mr or "",
            "system": mt == "system",
        })
    return out


def _stats(msgs):
    inc = sum(1 for m in msgs if m["dir"] == "входящее" and not m["system"])
    out = sum(1 for m in msgs if m["dir"] == "исходящее")
    return {
        "total": len(msgs), "in": inc, "out": out,
        "voice": sum(1 for m in msgs if m["kind"] == "голосовое"),
        "image": sum(1 for m in msgs if m["kind"] == "фото"),
        "system": sum(1 for m in msgs if m["system"]),
        "first": msgs[0]["at"] if msgs else "", "last": msgs[-1]["at"] if msgs else "",
        "answered": bool(msgs) and msgs[-1]["dir"] == "исходящее",
    }


_CSS = """
* { font-family: 'DejaVu Sans', sans-serif; box-sizing: border-box; color:#1D2939; }
body { margin:24px; font-size:11px; }
h1 { font-size:17px; margin:0 0 4px; }
.sub { color:#667085; font-size:11px; margin-bottom:14px; }
.card { border:1px solid #E3E7F0; border-radius:8px; padding:10px 12px; margin-bottom:12px; }
.card b { color:#667085; font-weight:normal; }
.stat { display:inline-block; margin-right:16px; }
.stat i { font-style:normal; font-weight:bold; font-size:13px; }
table { width:100%; border-collapse:collapse; margin-top:6px; }
th { background:#F5F7FB; text-align:left; padding:6px 8px; font-size:10px; color:#667085; border-bottom:1px solid #E3E7F0; }
td { padding:6px 8px; border-bottom:1px solid #EEF1F6; vertical-align:top; }
td.t { width:88px; color:#98A2B3; white-space:nowrap; }
td.w { width:78px; }
.out td.w { color:#2F6FED; } .in td.w { color:#12B76A; } .sys td { color:#98A2B3; font-size:10px; }
.kind { font-size:9px; color:#B54708; background:#FFFAEB; border-radius:4px; padding:1px 5px; }
.sec { font-size:13px; font-weight:bold; margin:16px 0 6px; }
.foot { margin-top:18px; color:#98A2B3; font-size:9px; border-top:1px solid #EEF1F6; padding-top:8px; }
"""


def _pdf(html_body, title):
    from weasyprint import HTML as _WHTML
    html = f"""<html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>
    {html_body}
    <div class="foot">Сформировано в БОРИС · boris-ai.pro · {datetime.datetime.now(MSK).strftime("%d.%m.%Y %H:%M")}</div>
    </body></html>"""
    out = io.BytesIO()
    _WHTML(string=html).write_pdf(out)
    out.seek(0)
    return out


def _msgs_table(msgs):
    rows = []
    for m in msgs:
        cls = "sys" if m["system"] else ("out" if m["dir"] == "исходящее" else "in")
        kind = "" if m["kind"] == "текст" else f'<span class="kind">{m["kind"]}</span> '
        body = _esc(m["text"]) or ("<i>(без текста)</i>" if m["kind"] != "текст" else "")
        rows.append(f'<tr class="{cls}"><td class="t">{m["at"]}</td>'
                    f'<td class="w">{m["who"]}</td><td>{kind}{body}</td></tr>')
    return ('<table><tr><th>Время (МСК)</th><th>Кто</th><th>Сообщение</th></tr>'
            + "".join(rows) + '</table>')


def _csv_response(rows, header, fname):
    buf = io.StringIO()
    buf.write("\ufeff")                      # BOM — чтобы Excel открыл кириллицу
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    w.writerow(header)
    w.writerows(rows)
    data = io.BytesIO(buf.getvalue().encode("utf-8"))
    return StreamingResponse(data, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ------------------------------------------------ экспорт ОДНОГО диалога
@router.get("/export/dialog")
def export_dialog(account_id: str, avito_chat_id: str, format: str = "pdf",
                  date_from: str = "", date_to: str = "", user=Depends(get_current_user)):
    okd, acc_name = _guard(user, account_id)
    if not okd:
        return {"status": "error", "message": "Аккаунт не найден или не подключён"}
    db = SessionLocal()
    try:
        msgs = _fetch_messages(db, account_id, avito_chat_id, date_from, date_to)
    finally:
        db.close()
    if not msgs:
        return {"status": "error", "message": "В этом диалоге нет сообщений за период"}
    item = next((m["item"] for m in msgs if m["item"]), "")
    st = _stats(msgs)
    stamp = datetime.datetime.now(MSK).strftime("%Y%m%d")

    if format.lower() == "json":
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "ok", "account_id": account_id, "account_name": acc_name,
                             "avito_chat_id": avito_chat_id, "item_title": item,
                             "stats": st, "messages": msgs})
    if format.lower() == "csv":
        rows = [[m["at"], m["dir"], m["who"], m["kind"], m["text"], m["media"]] for m in msgs]
        return _csv_response(rows,
                             ["Время (МСК)", "Направление", "Кто", "Тип", "Текст", "Медиа"],
                             f"dialog_{avito_chat_id[:16]}_{stamp}.csv")

    body = f"""
    <h1>Переписка Avito</h1>
    <div class="sub">Аккаунт: {_esc(acc_name)} · ID диалога: {_esc(avito_chat_id)}</div>
    <div class="card">
      <div><b>Объявление:</b> {_esc(item) or '—'}</div>
      <div><b>Период:</b> {st['first']} — {st['last']}</div>
      <div style="margin-top:8px">
        <span class="stat"><i>{st['total']}</i><br>всего</span>
        <span class="stat"><i>{st['in']}</i><br>от клиента</span>
        <span class="stat"><i>{st['out']}</i><br>наших</span>
        <span class="stat"><i>{st['voice']}</i><br>голосовых</span>
        <span class="stat"><i>{st['image']}</i><br>фото</span>
        <span class="stat"><i>{'да' if st['answered'] else 'нет'}</i><br>отвечен</span>
      </div>
    </div>
    <div class="sec">Сообщения</div>
    {_msgs_table(msgs)}"""
    return StreamingResponse(_pdf(body, "Переписка"), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="dialog_{avito_chat_id[:16]}_{stamp}.pdf"'})


# ------------------------------------------------ МАССОВЫЙ экспорт
@router.get("/export/dialogs")
def export_dialogs(account_id: str, format: str = "pdf", date_from: str = "", date_to: str = "",
                   answered: str = "", item: str = "", q: str = "", limit: int = 200,
                   user=Depends(get_current_user)):
    """answered: '' | 'yes' | 'no'. item — подстрока объявления. q — поиск по тексту."""
    okd, acc_name = _guard(user, account_id)
    if not okd:
        return {"status": "error", "message": "Аккаунт не найден или не подключён"}
    cond, params = _period_sql(date_from, date_to)
    params["a"] = account_id
    db = SessionLocal()
    try:
        chats = [r[0] for r in db.execute(text(
            "SELECT DISTINCT avito_chat_id FROM messenger_messages WHERE account_id=:a" + cond),
            params).all()]
        blocks = []
        for cid in chats:
            msgs = _fetch_messages(db, account_id, cid, date_from, date_to)
            if not msgs:
                continue
            st = _stats(msgs)
            it = next((m["item"] for m in msgs if m["item"]), "")
            if item and item.lower() not in it.lower():
                continue
            if answered == "yes" and not st["answered"]:
                continue
            if answered == "no" and st["answered"]:
                continue
            if q and not any(q.lower() in (m["text"] or "").lower() for m in msgs):
                continue
            blocks.append({"cid": cid, "item": it, "st": st, "msgs": msgs})
    finally:
        db.close()
    if not blocks:
        return {"status": "error", "message": "Под фильтры не попал ни один диалог"}
    blocks.sort(key=lambda b: b["msgs"][-1]["ts"], reverse=True)
    blocks = blocks[:max(1, min(int(limit or 200), 500))]
    stamp = datetime.datetime.now(MSK).strftime("%Y%m%d")

    if format.lower() == "json":
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "ok", "account_id": account_id, "account_name": acc_name,
                             "filters": {"date_from": date_from, "date_to": date_to,
                                         "answered": answered, "item": item, "q": q},
                             "totals": {"dialogs": len(blocks),
                                        "messages": sum(x["st"]["total"] for x in blocks)},
                             "dialogs": [{"avito_chat_id": x["cid"], "item_title": x["item"],
                                          "stats": x["st"], "messages": x["msgs"]} for x in blocks]})
    if format.lower() == "csv":
        rows = []
        for b in blocks:
            for m in b["msgs"]:
                rows.append([b["cid"], b["item"], m["at"], m["dir"], m["who"],
                             m["kind"], m["text"], m["media"]])
        return _csv_response(rows,
            ["ID диалога", "Объявление", "Время (МСК)", "Направление", "Кто", "Тип", "Текст", "Медиа"],
            f"dialogs_{account_id[:20]}_{stamp}.csv")

    tot = {"d": len(blocks), "m": sum(b["st"]["total"] for b in blocks),
           "a": sum(1 for b in blocks if b["st"]["answered"]),
           "v": sum(b["st"]["voice"] for b in blocks),
           "i": sum(b["st"]["image"] for b in blocks)}
    flt = []
    if date_from or date_to: flt.append(f"период {date_from or '…'} — {date_to or '…'}")
    if answered == "yes": flt.append("только отвеченные")
    if answered == "no": flt.append("только неотвеченные")
    if item: flt.append(f"объявление содержит «{_esc(item)}»")
    if q: flt.append(f"поиск «{_esc(q)}»")

    parts = [f"""
    <h1>Переписки Avito — выгрузка</h1>
    <div class="sub">Аккаунт: {_esc(acc_name)}{' · ' + ' · '.join(flt) if flt else ''}</div>
    <div class="card">
      <span class="stat"><i>{tot['d']}</i><br>диалогов</span>
      <span class="stat"><i>{tot['m']}</i><br>сообщений</span>
      <span class="stat"><i>{tot['a']}</i><br>отвечено</span>
      <span class="stat"><i>{tot['d']-tot['a']}</i><br>без ответа</span>
      <span class="stat"><i>{tot['v']}</i><br>голосовых</span>
      <span class="stat"><i>{tot['i']}</i><br>фото</span>
    </div>"""]
    for b in blocks:
        st = b["st"]
        parts.append(f"""<div class="sec">{_esc(b['item']) or 'Без объявления'}</div>
        <div class="card"><b>ID:</b> {_esc(b['cid'])} · <b>сообщений:</b> {st['total']} ·
        <b>период:</b> {st['first']} — {st['last']} ·
        <b>статус:</b> {'отвечен' if st['answered'] else 'ждёт ответа'}</div>
        {_msgs_table(b['msgs'])}""")
    return StreamingResponse(_pdf("".join(parts), "Выгрузка"), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="dialogs_{account_id[:20]}_{stamp}.pdf"'})
