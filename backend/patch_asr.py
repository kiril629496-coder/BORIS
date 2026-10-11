# -*- coding: utf-8 -*-
"""Словарь терминов для whisper. Без него отраслевые слова слышатся неверно:
на бытовках расшифровка давала «бутылки» вместо «бытовки», и GPT разбирал
разговор про другой бизнес. Термины берутся из базы знаний аккаунта."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/calltracking.py")

HINT_FN = '''def _asr_hint(account_id: str) -> str:
    """Подсказка whisper: ниша клиента + названия фактов из его базы знаний.
    Так отраслевые слова распознаются правильно у КАЖДОГО клиента, а не только
    у того, под кого зашили список."""
    words = []
    try:
        import psycopg2
        u = _db_url()
        if u:
            c = psycopg2.connect(u); cur = c.cursor()
            cur.execute("SELECT company_niche FROM accounts WHERE account_id=%s", (account_id,))
            row = cur.fetchone()
            if row and row[0]:
                words.append(str(row[0]))
            cur.execute("""SELECT DISTINCT name FROM client_facts
                           WHERE account_id=%s AND status <> 'rejected'
                           ORDER BY name LIMIT 60""", (account_id,))
            for r in cur.fetchall():
                if r[0]:
                    words.append(str(r[0]))
            c.close()
    except Exception:
        pass
    txt = ", ".join(w.strip() for w in words if w and len(str(w).strip()) > 2)
    return txt[:900]


'''

A_OLD = 'def _transcribe(audio_bytes: bytes, filename: str = "call.mp3") -> str:'
A_NEW = HINT_FN + 'def _transcribe(audio_bytes: bytes, filename: str = "call.mp3", hint: str = "") -> str:'

B_OLD = '''        data={"model": "whisper-1", "language": "ru"},'''
B_NEW = '''        data={"model": "whisper-1", "language": "ru",
              **({"prompt": hint} if hint else {})},'''

C_OLD = '        transcript = _transcribe(audio, f"call_{call_id}.{ext}")'
C_NEW = '        transcript = _transcribe(audio, f"call_{call_id}.{ext}", _asr_hint(account_id))'

D_OLD = '            tr = _transcribe(audio, f"call_{cid}.{ext}")'
D_NEW = '            tr = _transcribe(audio, f"call_{cid}.{ext}", _asr_hint(account_id))'

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "_asr_hint" in src:
    print("уже пропатчено"); sys.exit(0)
out = src
for old, new, nm in ((A_OLD, A_NEW, "функция словаря"), (B_OLD, B_NEW, "параметр prompt"),
                     (C_OLD, C_NEW, "вызов в разборе"), (D_OLD, D_NEW, "вызов в отчёте")):
    n = out.count(old)
    print("  %-20s якорь: %s" % (nm, n))
    if n != 1:
        print("  СТОП: якорь должен быть один"); sys.exit(1)
    out = out.replace(old, new, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_asr_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
