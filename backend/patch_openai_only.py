import ast, re, shutil, sys, time

APPLY = "--apply" in sys.argv
TS = int(time.time())

def load(p):
    return open(p, encoding="utf-8").read()

def finish(path, before, after, notes):
    if before == after:
        print("!!! %s: НИЧЕГО НЕ ИЗМЕНЕНО" % path)
        return
    try:
        ast.parse(after)
    except SyntaxError as e:
        print("!!! %s: СИНТАКСИС СЛОМАН (%s) — файл не тронут" % (path, e)); return
    print("=== %s: %+d символов | %s" % (path, len(after) - len(before), "; ".join(notes)))
    if APPLY:
        shutil.copy(path, "%s.bak_openai_%d" % (path, TS))
        open(path, "w", encoding="utf-8").write(after)
        print("    записано, бэкап %s.bak_openai_%d" % (path, TS))

# --- 1. avito.py: Kandinsky -> OpenAI, потоки 1 -> 4
p = "app/api/avito.py"; s0 = load(p); s = s0; n = []
m = re.search(r"[ \t]*with GigaChat\(.*?img_b64 = image\.content\n", s, re.S)
n.append("блок GigaChat найден: %s" % bool(m))
if m:
    s = s[:m.start()] + s[m.end():]
old = '                with open(fpath, "wb") as imgf:\n                    imgf.write(_b64.b64decode(img_b64))'
new = ('                from banner_generator import generate_ai_image\n'
       '                if not generate_ai_image(full_prompt, fpath, size="1024x1024", quality="medium",\n'
       '                                         model="gpt-image-2", account_id=req.account_id,\n'
       '                                         operation="generate_banner"):\n'
       '                    return (i, None, f"Вариант {i+1}: OpenAI не вернул изображение")')
n.append("блок сохранения найден: %s" % (old in s))
s = s.replace(old, new, 1)
n.append("потоков 1->4: %s" % ("with ThreadPoolExecutor(max_workers=1) as executor:" in s))
s = s.replace("with ThreadPoolExecutor(max_workers=1) as executor:  # тариф Freemium GigaChat = 1 поток одновременных запросов; поднять до 10 при переходе на Business",
              "with ThreadPoolExecutor(max_workers=4) as executor:  # OpenAI: 4 параллельных запроса", 1)
s = s.replace("with ThreadPoolExecutor(max_workers=1) as executor:",
              "with ThreadPoolExecutor(max_workers=4) as executor:", 1)
finish(p, s0, s, n)

# --- 2. banners.py: GigaChat-копирайтер возвращает текст дословно
p = "app/api/banners.py"; s0 = load(p); s = s0; n = []
anchor = 'для баннера."""'
n.append("якорь докстроки: %s" % (anchor in s))
s = s.replace(anchor, anchor +
              '\n    # GigaChat отключён от баннеров (решение 05.08): текст клиента идёт дословно\n'
              '    return {"title": raw_description, "subtitle": "", "price_text": "", "advantages": []}', 1)
finish(p, s0, s, n)

# --- 3. banner_generator.py: mini -> gpt-image-2
p = "banner_generator.py"; s0 = load(p); s = s0
c = s.count("gpt-image-1-mini")
s = s.replace('model: str = "gpt-image-1-mini"', 'model: str = "gpt-image-2"')
s = s.replace('ai_model: str = "gpt-image-1-mini"', 'ai_model: str = "gpt-image-2"')
s = s.replace('model="gpt-image-1-mini"', 'model="gpt-image-2"')
finish(p, s0, s, ["упоминаний mini было %d, осталось %d" % (c, s.count("gpt-image-1-mini"))])

# --- 4. chat.py: описание студии картинок
p = "app/api/chat.py"; s0 = load(p); s = s0
s = s.replace("через Kandinsky", "через OpenAI (gpt-image-2)")
s = s.replace("картинках Kandinsky", "картинках OpenAI")
s = s.replace("Картинка Kandinsky ≈ 5-6 ₽.", "Картинка OpenAI — по тарифу OpenAI.")
finish(p, s0, s, ["упоминаний Kandinsky осталось %d" % s.count("Kandinsky")])

print("=== РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР (dry-run), для записи добавьте --apply")
