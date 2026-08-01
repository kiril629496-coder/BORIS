from fastapi import APIRouter, UploadFile, File, Form, Depends
import os, json, time
from app.api.auth import get_current_user

router = APIRouter(prefix="/api/sitebuild", tags=["sitebuild"])

VIDEO_DIR = "/root/BORIS/backend/images/_sitebuild/videos"
os.makedirs(VIDEO_DIR, exist_ok=True)
SCREENS_DIR = "/root/BORIS/backend/images/_sitebuild/screens"
os.makedirs(SCREENS_DIR, exist_ok=True)
ALLOWED = {".mp4", ".webm", ".mov"}

@router.post("/order")
async def site_order(name: str = Form(""), phone: str = Form(...), niche: str = Form(""), comment: str = Form(""), account_id: str = Form("")):
    import os
    text = (
        "\U0001F310 <b>Новая заявка на сайт!</b>\n\n"
        f"\U0001F464 Имя: {name or '—'}\n"
        f"\U0001F4DE Телефон: {phone}\n"
        f"\U0001F3E2 Сфера: {niche or '—'}\n"
        f"\U0001F4AC Комментарий: {comment or '—'}\n"
        f"\U0001F517 Аккаунт: {account_id or '—'}"
    )
    # 1) Telegram — сразу
    try:
        from app.telegram_bot import send_telegram_message
        chat_id = os.environ.get("DIRECTOR_CHAT_ID")
        if chat_id:
            send_telegram_message(chat_id, text)
    except Exception as e:
        print("site_order telegram failed:", e)

    # 2) Email на 2 почты — работает, если в .env заданы SMTP_HOST/SMTP_USER/SMTP_PASS
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    if smtp_host and smtp_user and smtp_pass:
        try:
            import smtplib
            from email.mime.text import MIMEText
            recipients = ["ostapenko-kirill-86@yandex.ru", "eliseev-ko@mail.ru"]
            body = text.replace("<b>", "").replace("</b>", "")
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = "Новая заявка на сайт — БОРИС"
            msg["From"] = smtp_user
            msg["To"] = ", ".join(recipients)
            port = int(os.environ.get("SMTP_PORT", "465"))
            with smtplib.SMTP_SSL(smtp_host, port) as srv:
                srv.login(smtp_user, smtp_pass)
                srv.sendmail(smtp_user, recipients, msg.as_string())
        except Exception as e:
            print("site_order email failed:", e)

    return {"status": "ok"}

@router.post("/upload_video")
async def upload_video(title: str = Form(""), file: UploadFile = File(...), user=Depends(get_current_user)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED:
        return {"status": "error", "message": f"Формат {ext} не поддерживается. Разрешены: mp4, webm, mov"}
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_а-яёА-ЯЁ").strip()[:60] or "video"
    fname = f"{int(time.time())}_{safe_title}{ext}"
    path = os.path.join(VIDEO_DIR, fname)
    content = await file.read()
    with open(path, "wb") as f:
        f.write(content)
    # метаданные
    meta_path = os.path.join(VIDEO_DIR, "_meta.json")
    meta = []
    if os.path.exists(meta_path):
        try: meta = json.load(open(meta_path, encoding="utf-8"))
        except Exception: meta = []
    meta.append({"filename": fname, "title": title or safe_title, "uploaded_at": time.time()})
    json.dump(meta, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False)
    return {"status": "ok", "filename": fname, "url": f"/images/_sitebuild/videos/{fname}"}

@router.post("/upload_screen")
async def upload_screen(filename: str = Form(...), file: UploadFile = File(...), user=Depends(get_current_user)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        return {"status": "error", "message": "Только PNG/JPG/WEBP"}
    safe = "".join(c for c in filename if c.isalnum() or c in "._-")[:80]
    if not safe.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        safe += ext
    path = os.path.join(SCREENS_DIR, safe)
    content = await file.read()
    with open(path, "wb") as f:
        f.write(content)
    return {"status": "ok", "filename": safe, "url": f"/images/_sitebuild/screens/{safe}"}

@router.get("/screens")
def list_screens():
    if not os.path.exists(SCREENS_DIR):
        return {"status": "ok", "screens": []}
    files = [f for f in sorted(os.listdir(SCREENS_DIR)) if f.lower().endswith((".png",".jpg",".jpeg",".webp"))]
    return {"status": "ok", "screens": files}

@router.get("/videos")
def list_videos():
    meta_path = os.path.join(VIDEO_DIR, "_meta.json")
    if not os.path.exists(meta_path):
        return {"status": "ok", "videos": []}
    try:
        meta = json.load(open(meta_path, encoding="utf-8"))
    except Exception:
        meta = []
    # только реально существующие файлы
    videos = [{"title": m["title"], "url": f"/images/_sitebuild/videos/{m['filename']}", "filename": m["filename"]}
              for m in meta if os.path.exists(os.path.join(VIDEO_DIR, m["filename"]))]
    return {"status": "ok", "videos": videos}

@router.post("/rename_video")
def rename_video(filename: str = Form(...), title: str = Form(...), user=Depends(get_current_user)):
    meta_path = os.path.join(VIDEO_DIR, "_meta.json")
    if not os.path.exists(meta_path):
        return {"status": "error", "message": "нет метаданных"}
    try:
        meta = json.load(open(meta_path, encoding="utf-8"))
    except Exception:
        meta = []
    changed = False
    for m in meta:
        if m.get("filename") == filename:
            m["title"] = title
            changed = True
    if changed:
        json.dump(meta, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False)
    return {"status": "ok" if changed else "error"}

@router.post("/delete_video")
def delete_video(filename: str = Form(...), user=Depends(get_current_user)):
    path = os.path.join(VIDEO_DIR, filename)
    if os.path.exists(path):
        os.remove(path)
    return {"status": "ok"}
