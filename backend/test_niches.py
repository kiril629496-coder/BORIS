# -*- coding: utf-8 -*-
"""Обязательные проверки Базы знаний BORIS до подключения к интерфейсу."""
import copy, json, os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import niches

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "knowledge")
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    if cond:
        print("ok     %s %s" % (name, detail)); ok += 1
    else:
        print("ПРОВАЛ %s %s" % (name, detail)); bad += 1


def sandbox(mutate_niche=None, mutate_common=None, extra_niche=None):
    tmp = tempfile.mkdtemp()
    dst = os.path.join(tmp, "knowledge")
    shutil.copytree(SRC, dst)
    p = os.path.join(dst, "niches", "korpusnaya-mebel.json")
    if mutate_niche:
        d = json.load(open(p, encoding="utf-8"))
        mutate_niche(d)
        json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    if mutate_common:
        cp = os.path.join(dst, "_common.json")
        d = json.load(open(cp, encoding="utf-8"))
        mutate_common(d)
        json.dump(d, open(cp, "w", encoding="utf-8"), ensure_ascii=False)
    if extra_niche:
        name, data = extra_niche
        json.dump(data, open(os.path.join(dst, "niches", name), "w", encoding="utf-8"),
                  ensure_ascii=False)
    return niches.load(dst)


print("--- 1. базовая загрузка ---")
h = niches.load(SRC)
check("эталонная ниша загружается", h["niches_ok"] == 1 and not h["failed"], str(h["ok"]))

print()
print("--- 2. разбор текста ---")
r = niches.resolve("шкафы-купе")
check("точный алиас", r.status == "matched" and r.slug == "korpusnaya-mebel", r.status)
r = niches.resolve("делаем кухни на заказ в Пензе")
check("русский падеж и лишние слова", r.status == "matched", r.status)
r = niches.resolve("корпусной мебели")
check("падеж через огрубление", r.status == "matched", r.status)
r = niches.resolve("на заказ под ключ недорого")
check("общие слова не дают совпадения", r.status == "not_found", r.status)
r = niches.resolve("автосервис и шиномонтаж")
check("неизвестная ниша", r.status == "not_found", r.status)

print()
print("--- 3. неоднозначность ---")
second = copy.deepcopy(json.load(open(os.path.join(SRC, "niches", "korpusnaya-mebel.json"), encoding="utf-8")))
second["slug"] = "kuhni-na-zakaz"
second["name"] = "Кухни на заказ отдельно"
second["aliases"] = ["кухонная мебель", "кухни премиум"]
h = sandbox(extra_niche=("kuhni-na-zakaz.json", second))
check("две ниши загрузились", h["niches_ok"] == 2, str(h["niches_ok"]))
r = niches.resolve("кухни")
check("два кандидата → ambiguous", r.status == "ambiguous" and len(r.candidates) == 2, str(r.candidates))

print()
print("--- 4. контракт блокирует битое ---")
h = sandbox(mutate_niche=lambda d: d["goals"].append("nesushchestvuyushchaya_cel"))
check("несуществующий ключ словаря блокирует файл", h["niches_failed"] == 1,
      h["failed"][0]["error"] if h["failed"] else "")
h = sandbox(mutate_niche=lambda d: d["thresholds"].pop("max_zero_view_ratio"))
check("отсутствующий порог блокирует", h["niches_failed"] == 1,
      h["failed"][0]["error"] if h["failed"] else "")
h = sandbox(mutate_niche=lambda d: d["thresholds"]["min_views"].__setitem__("confidence", "точно"))
check("неверный confidence блокирует", h["niches_failed"] == 1,
      h["failed"][0]["error"] if h["failed"] else "")
h = sandbox(mutate_niche=lambda d: d["thresholds"]["min_views"].__setitem__("confidence", 80))
check("числовой confidence блокируется", h["niches_failed"] == 1,
      h["failed"][0]["error"] if h["failed"] else "")
h = sandbox(mutate_niche=lambda d: d.__setitem__("schema_version", 99))
check("чужая schema_version блокирует", h["niches_failed"] == 1,
      h["failed"][0]["error"] if h["failed"] else "")

print()
print("--- 5. битая ниша не кладёт модуль ---")
broken = {"slug": "bitaya", "schema_version": 4}
h = sandbox(extra_niche=("bitaya.json", broken))
check("рабочая ниша осталась", h["niches_ok"] == 1 and h["niches_failed"] == 1,
      "ok=%s failed=%s" % (h["niches_ok"], h["niches_failed"]))
check("health показывает файл и путь",
      h["failed"][0]["file"] == "bitaya.json" and h["failed"][0]["path"],
      json.dumps(h["failed"][0], ensure_ascii=False))
check("resolve по рабочей нише не сломан", niches.resolve("шкафы-купе").status == "matched")

print()
print("--- 6. forbidden_wording ---")
def same_wording(d):
    d["problems"]["few_ads"]["forbidden_wording"] = [d["problems"]["few_ads"]["message_template"]]
try:
    sandbox(mutate_common=same_wording)
    check("совпадение forbidden_wording и message_template блокирует", False, "ошибки не было")
except niches.ContractError as e:
    check("совпадение forbidden_wording и message_template блокирует", True, e.path)

print()
print("--- 7. срез по модулям ---")
niches.load(SRC)
sl = niches.for_module("korpusnaya-mebel", "mop")
check("МОП получает только своё",
      set(sl) == {"slug", "name", "content_hash", "buyer_questions", "collect_from_client", "limits"},
      str(sorted(sl)))
sl = niches.for_module("korpusnaya-mebel", "categories")
check("резолвер категорий получает только avito",
      set(sl) == {"slug", "name", "content_hash", "avito"}, str(sorted(sl)))
try:
    niches.for_module("korpusnaya-mebel", "nesushchestvuyushchij")
    check("неизвестный модуль отвергается", False)
except KeyError:
    check("неизвестный модуль отвергается", True)

print()
print("--- 8. content_hash ---")
h1 = niches.get("korpusnaya-mebel")["content_hash"]
niches.load(SRC)
h2 = niches.get("korpusnaya-mebel")["content_hash"]
check("стабилен при том же содержимом", h1 == h2, h1[:16])
sandbox(mutate_niche=lambda d: d.__setitem__("summary", d["summary"] + " правка"))
h3 = niches.get("korpusnaya-mebel")["content_hash"]
check("меняется при правке", h3 != h1, h3[:16])

niches.load(SRC)
print()
print("ИТОГО: ok = %d, провалов = %d" % (ok, bad))
sys.exit(1 if bad else 0)
