# -*- coding: utf-8 -*-
"""Проверки сборки анализа: matched / ambiguous / not_found."""
import copy, json, os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import niches
from app import analysis_onboarding as analysis_core

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "knowledge")
niches.load(SRC)
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    print(("ok     " if cond else "ПРОВАЛ ") + name + " " + str(detail))
    globals().__setitem__("ok", ok + 1) if cond else globals().__setitem__("bad", bad + 1)


FORM = {"company_name": "Альфа Мебель", "company_niche": "кухни на заказ",
        "city": "Пенза", "website": "https://example.ru", "phone": "+7 900 000",
        "channels": ["avito", "vk"], "channel_links": {"vk": "https://vk.com/x"}}

print("--- MATCHED ---")
r = niches.resolve(FORM["company_niche"])
a = analysis_core.build(FORM, niches, r)
check("статус matched", a["status"] == "matched", a["status"])
check("ниша названа", a["niche"]["name"] == "Корпусная мебель на заказ", a["niche"]["slug"])
check("профиль — эхо введённого", a["profile"]["city"] == "Пенза" and len(a["profile"]["channels"]) == 2)
check("вопросы покупателей есть", len(a["buyer_questions"]) == 7, len(a["buyer_questions"]))
check("observed подан мягко",
      a["buyer_questions"][0]["title"].startswith("По имеющимся данным"),
      a["buyer_questions"][0]["title"][:45])
check("у вопроса есть source", a["buyer_questions"][0]["source"] == "observed")
check("проблемы поданы вопросом", all(p["as_question"] for p in a["problems"]), len(a["problems"]))
check("возможности разложены по трём корзинам",
      len(a["capabilities_now"]) and len(a["capabilities_after_connection"]) and len(a["capabilities_paid"]),
      "now=%d after=%d paid=%d" % (len(a["capabilities_now"]), len(a["capabilities_after_connection"]),
                                   len(a["capabilities_paid"])))
check("в 'сейчас' нет ничего, что требует Avito или лицензии",
      all(not (i["requirements"]) or all(rq["kind"] == "material" for rq in i["requirements"])
          for i in a["capabilities_now"]),
      [i["key"] for i in a["capabilities_now"]])
check("answer_clients ушёл в платные",
      any(i["key"] == "answer_clients" for i in a["capabilities_paid"]),
      [i["key"] for i in a["capabilities_paid"]])
check("make_banners доступен сейчас и с ограничением",
      any(i["key"] == "make_banners" and i["limits"] for i in a["capabilities_now"]))
check("план 3-5 пунктов", 3 <= len(a["plan"]) <= 5, len(a["plan"]))
check("план отсортирован: доступное первым",
      a["plan"][0]["status"] == "works"
      and all(rq["kind"] == "material" for rq in a["plan"][0]["requirements"]),
      [(p["key"], p["status"], [rq["kind"] for rq in p["requirements"]]) for p in a["plan"]])
check("у пункта плана есть обоснование", all(p["why"] for p in a["plan"]))
check("время не выдумано", all(p["time"] is None for p in a["plan"]))
check("каналы разложены",
      len(a["channels_compare"]["have"]) == 2 and a["channels_compare"]["can_add_later"],
      json.dumps({k: [i["key"] for i in v] for k, v in a["channels_compare"].items()}, ensure_ascii=False))
check("материалы не утверждают отсутствие", all(m["asked"] is False for m in a["materials_missing"]))
check("после Avito — только требующее Avito",
      all("avito" in [rq["key"] for rq in i["requirements"]] for i in a["automatic_after_avito"]),
      [i["key"] for i in a["automatic_after_avito"]])

print()
print("--- NOT_FOUND ---")
form2 = dict(FORM, company_niche="ремонт квадрокоптеров")
r2 = niches.resolve(form2["company_niche"])
a2 = analysis_core.build(form2, niches, r2)
check("статус not_found", a2["status"] == "not_found", a2["status"])
check("честный текст есть", a2["fallback"]["text"].startswith("По вашей нише"))
check("ниша не выдумана", a2["niche"] is None)
check("типовых вопросов нет", a2["buyer_questions"] == [])
check("проблем ниши нет", a2["problems"] == [])
check("базовые возможности показаны", len(a2["capabilities_now"]) > 0, len(a2["capabilities_now"]))
check("профиль на месте", a2["profile"]["company_name"] == "Альфа Мебель")
check("материалы показаны чек-листом", len(a2["materials_missing"]) > 0)

print()
print("--- AMBIGUOUS ---")
tmp = tempfile.mkdtemp(); dst = os.path.join(tmp, "knowledge")
shutil.copytree(SRC, dst)
second = json.load(open(os.path.join(SRC, "niches", "korpusnaya-mebel.json"), encoding="utf-8"))
second["slug"] = "kuhni-premium"; second["name"] = "Кухни премиум"
second["aliases"] = ["кухни премиум", "премиальные кухни"]
json.dump(second, open(os.path.join(dst, "niches", "kuhni-premium.json"), "w", encoding="utf-8"),
          ensure_ascii=False)
niches.load(dst)
r3 = niches.resolve("кухни")
a3 = analysis_core.build(FORM, niches, r3)
check("статус ambiguous", a3["status"] == "ambiguous", a3["status"])
check("кандидаты названы", len(a3["candidates"]) == 2 and all(c["name"] for c in a3["candidates"]),
      [c["slug"] for c in a3["candidates"]])
check("автоматически ничего не выбрано", a3["niche"] is None)
check("анализ по нише не построен", a3["plan"] == [] and a3["buyer_questions"] == [])

niches.load(SRC)
print()
print("ИТОГО: ok = %d, провалов = %d" % (ok, bad))
sys.exit(1 if bad else 0)
