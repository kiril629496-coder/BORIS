"""Проверка согласованности фото одного проекта.

Ловит без ИИ-зрения: битые и пустые файлы, дубликаты, кадры
выбивающиеся по палитре (значит нарисована другая мебель),
пересветы и провалы в тень.
"""
import os, sys, hashlib
from collections import Counter
from PIL import Image

MIN_BYTES   = 120_000    # меньше — почти наверняка брак
MIN_SIDE    = 900
PALETTE_MAX = 90         # порог расхождения палитры с медианой проекта
BRIGHT_LO, BRIGHT_HI = 35, 225


def palette(path, n=6):
    im = Image.open(path).convert("RGB").resize((160, 160))
    q = im.quantize(colors=n, method=Image.MEDIANCUT).convert("RGB")
    cnt = Counter(q.getdata())
    top = [c for c, _ in cnt.most_common(n)]
    while len(top) < n:
        top.append(top[-1] if top else (128, 128, 128))
    return top


def dist(p1, p2):
    return sum(((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2) ** 0.5
               for a, b in zip(p1, p2)) / len(p1)


def brightness(path):
    im = Image.open(path).convert("L").resize((100, 100))
    px = list(im.getdata())
    return sum(px) / len(px)


def check(folder, prefix):
    files = sorted(f for f in os.listdir(folder)
                   if f.startswith(prefix) and f.lower().endswith((".png", ".jpg")))
    if not files:
        print("  нет файлов по префиксу", prefix); return []
    problems, pals, hashes = [], {}, {}
    for f in files:
        p = os.path.join(folder, f)
        size = os.path.getsize(p)
        if size < MIN_BYTES:
            problems.append((f, "файл подозрительно мал: %d КБ" % (size // 1024)))
        try:
            im = Image.open(p); im.verify()
            im = Image.open(p)
            w, h = im.size
            if min(w, h) < MIN_SIDE:
                problems.append((f, "низкое разрешение %dx%d" % (w, h)))
        except Exception as e:
            problems.append((f, "битый файл: %s" % str(e)[:50])); continue
        h5 = hashlib.md5(open(p, "rb").read()).hexdigest()
        if h5 in hashes:
            problems.append((f, "дубликат кадра %s" % hashes[h5]))
        hashes[h5] = f
        b = brightness(p)
        if b < BRIGHT_LO:
            problems.append((f, "слишком тёмный кадр (%.0f)" % b))
        elif b > BRIGHT_HI:
            problems.append((f, "пересвет (%.0f)" % b))
        pals[f] = palette(p)

    if len(pals) >= 3:
        names = list(pals)
        avg = {}
        for a in names:
            avg[a] = sum(dist(pals[a], pals[b]) for b in names if b != a) / (len(names) - 1)
        med = sorted(avg.values())[len(avg) // 2]
        for f, d in avg.items():
            if d > med * 1.6 and d > PALETTE_MAX:
                problems.append((f, "палитра расходится с проектом (%.0f против %.0f) "
                                    "— вероятно другая мебель" % (d, med)))
    return problems


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    prefixes = sys.argv[2:] or sorted({f.rsplit("_", 2)[0] for f in os.listdir(folder)
                                       if f.startswith("proj_")})
    total = 0
    for pref in prefixes:
        print("\n=== %s ===" % pref)
        pr = check(folder, pref)
        if not pr:
            print("  ✅ отклонений нет")
        for f, msg in pr:
            print("  ⚠️  %-26s %s" % (f, msg))
            total += 1
    print("\nИТОГО замечаний: %d" % total)
