"""Тесты каталога: наследование фактов, недостающие данные, порождение объявлений."""
from sqlalchemy import text as _t

from app.db.session import SessionLocal
from app.models.campaign_item import CampaignItem
from app.models.catalog_fact import CatalogFact
from app.models.catalog_product import CatalogProduct
from app.services import catalog_service as C

P, F = [], []


def check(n, c, e=""):
    (P if c else F).append(n)
    print(("  [OK] " if c else "  [!!] ") + n + ((" — " + str(e)) if e else ""))


class U:
    def __init__(s, i, r): s.id, s.role = i, r


def main():
    db = SessionLocal()
    made_p, made_f, made_i = [], [], []
    try:
        row = db.execute(_t("SELECT account_id, owner_user_id FROM accounts"
                            " WHERE owner_user_id IS NOT NULL LIMIT 1")).fetchone()
        acc, owner = row[0], row[1]
        u, alien = U(owner, "client"), U(999999, "client")
        camp = db.execute(_t("SELECT id FROM campaigns LIMIT 1")).fetchone()

        print("\n1. ТОВАР И НЕДОСТАЮЩИЕ ДАННЫЕ")
        p = C.create_product(db, owner, acc, "QA Кухня K-024", category="Кухни")
        db.commit(); made_p.append(p.id)
        check("товар создан", p.id > 0)
        miss = C.missing_fields(db, p)
        blocking = [m["field"] for m in miss if m["blocking"]]
        check("видит нехватку цены/описания/гео", set(blocking) >= {"price", "description", "geo"}, blocking)
        check("не выдумывает гарантию", "garantiya" in [m["field"] for m in miss])

        print("\n2. НАСЛЕДОВАНИЕ BUSINESS -> CATEGORY -> PRODUCT")
        C.set_fact(db, owner, "business", "", "dostavka", "по городу бесплатно")
        C.set_fact(db, owner, "category", "Кухни", "srok", "от 25 дней")
        C.set_fact(db, owner, "product", str(p.id), "dostavka", "для этой кухни платная")
        db.commit()
        made_f += [r.id for r in db.query(CatalogFact).filter(
            CatalogFact.owner_user_id == owner,
            CatalogFact.key.in_(("dostavka", "srok"))).all()]
        f = C.resolve_facts(db, owner, p)
        check("факт бизнеса виден", "dostavka" in f)
        check("факт категории виден", f.get("srok", {}).get("value") == "от 25 дней")
        check("товар ПЕРЕОПРЕДЕЛЯЕТ бизнес",
              f["dostavka"]["value"] == "для этой кухни платная" and f["dostavka"]["level"] == "product")

        print("\n3. ПОРОЖДЕНИЕ ОБЪЯВЛЕНИЙ")
        p.price = 150000; p.description = "Кухня на заказ"; p.geo = ["Пенза"]
        db.commit()
        check("после заполнения блокеров нет",
              not [m for m in C.missing_fields(db, p) if m["blocking"]])
        if camp:
            dry = C.build_items(db, u, p, camp[0], acc, ["novostroyka", "malenkaya"], ["Пенза"], dry_run=True)
            check("сухой прогон показал 2 гипотезы", len(dry["preview"]) == 2 and dry["created"] == 0)
            real = C.build_items(db, u, p, camp[0], acc, ["novostroyka", "malenkaya"], ["Пенза"], dry_run=False)
            db.commit()
            made_i = [i.id for i in db.query(CampaignItem).filter(
                CampaignItem.product_id == p.id).all()]
            check("создано 2 объявления", real["created"] == 2)
            check("hypothesis_id проставлен", all(
                i.hypothesis_id and i.hypothesis_id.startswith("h-") for i in
                db.query(CampaignItem).filter(CampaignItem.product_id == p.id).all()))
            again = C.build_items(db, u, p, camp[0], acc, ["novostroyka", "malenkaya"], ["Пенза"], dry_run=False)
            db.commit()
            check("повтор не создаёт дублей", again["created"] == 0 and again["already"] == 2)
        else:
            print("  (нет кампаний — порождение не проверено)")

        print("\n4. ПРАВА И СТАРЫЕ ДАННЫЕ")
        check("чужой не видит товар", not any(
            i["id"] == p.id for i in C.list_products(db, alien, limit=200)["items"]))
        old = db.execute(_t("select count(*) from campaign_items where product_id is null")).scalar()
        check("старые объявления без product_id живы", old >= 10, "%d шт." % old)
        s = C.catalog_summary(db, u)
        check("сводка «требует внимания» считается", "no_photo" in s, s)
    finally:
        for i in made_i:
            db.query(CampaignItem).filter(CampaignItem.id == i).delete(synchronize_session=False)
        db.query(CatalogFact).filter(CatalogFact.id.in_(made_f or [-1])).delete(synchronize_session=False)
        for i in made_p:
            db.query(CatalogProduct).filter(CatalogProduct.id == i).delete(synchronize_session=False)
        db.commit(); db.close()
    print("\nИТОГ: PASS %d / FAIL %d" % (len(P), len(F)))
    if F: print("ПРОВАЛЫ:", F)


if __name__ == "__main__":
    main()
