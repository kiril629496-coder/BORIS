"""Удаляет старые уникализированные копии картинок (uq_*), созданные при публикации.
Оригиналы (gen_*, up_*, dl_*) НИКОГДА не трогает — только временные технические копии.
Запускать раз в сутки через cron.
"""
import os
import time

IMAGES_DIR = "/root/BORIS/backend/images"
MAX_AGE_DAYS = 45
MAX_AGE_SECONDS = MAX_AGE_DAYS * 24 * 60 * 60

def cleanup():
    now = time.time()
    removed = 0
    freed_bytes = 0
    for root, dirs, files in os.walk(IMAGES_DIR):
        for fname in files:
            if not fname.startswith("uq_"):
                continue  # трогаем только технические копии-уникализации
            fpath = os.path.join(root, fname)
            try:
                age = now - os.path.getmtime(fpath)
                if age > MAX_AGE_SECONDS:
                    freed_bytes += os.path.getsize(fpath)
                    os.remove(fpath)
                    removed += 1
            except FileNotFoundError:
                pass
    print(f"Автоочистка: удалено {removed} файлов, освобождено {freed_bytes/1024/1024:.1f} МБ")

if __name__ == "__main__":
    cleanup()
