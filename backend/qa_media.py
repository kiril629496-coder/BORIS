"""Тесты Медиатеки. Прогон: venv/bin/python3 qa_media.py

Проверяют то, что реально может сломаться: изоляцию доступа, file_state,
корзину, связи, дедупликацию, классификацию и производительность списка.
Все созданные объекты удаляются в конце — боевые данные не остаются.
"""
import time

from sqlalchemy import text as _t

from app.db.session import SessionLocal
from app.models.media_asset import MediaAsset
from app.models.media_link import MediaLink
from app.services import media_service as M

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK] " if cond else "  [!!] ") + name + ((" — " + str(extra)) if extra else ""))


class U:
    def __init__(self, uid, role):
        self.id, self.role = uid, role


def main():
    db = SessionLocal()
    made = []
    try:
        row = db.execute(_t("SELECT account_id, owner_user_id FROM accounts"
                            " WHERE owner_user_id IS NOT NULL LIMIT 1")).fetchone()
        if not row:
            print("нет аккаунта с владельцем — тест невозможен")
            return
        acc, owner = row[0], row[1]
        other = db.execute(_t("SELECT id FROM users WHERE id <> :o AND role <> 'owner'"
                              " LIMIT 1"), {"o": owner}).fetchone()
        owner_u, alien = U(owner, "client"), U(other[0] if other else 999999, "client")
        print("тестовый аккаунт: %s, владелец %s, чужой пользователь %s"
              % (acc, owner, alien.id))

        print("\n1. КЛАССИФИКАЦИЯ ФАЙЛОВ")
        check("up_ -> real_photo", M.classify("up_1.jpg") == ("real_photo", "upload"))
        check("stock_ -> stock", M.classify("stock_9.jpg") == ("stock", "stock"))
        check("fullai_ -> banner", M.classify("fullai_x.png") == ("banner", "banner"))
        check("product_ -> real_photo/site_parser",
              M.classify("product_3_ab.jpg") == ("real_photo", "site_parser"))
        check("gen_ -> ai_generated", M.classify("gen_2.png") == ("ai_generated", "ai"))
        check("uq_ не импортируется", M.classify("uq_1.jpg") == (None, None))
        check("shot_ (QA) не импортируется", M.classify("shot_1.png") == (None, None))
        check("не картинка не импортируется", M.classify("report.pdf") == (None, None))

        print("\n2. РЕГИСТРАЦИЯ И ИДЕМПОТЕНТНОСТЬ")
        key = "%s/qa_media_test/up_qa_media_test.jpg" % acc
        a, created = M.register_asset(db, owner, acc, key, "up_qa_media_test.jpg",
                                      media_type="real_photo", source_type="upload",
                                      sha256="deadbeef" * 8, file_size=123)
        db.commit()
        made.append(a.id)
        check("asset создан", created and a.id > 0)
        a2, created2 = M.register_asset(db, owner, acc, key, "up_qa_media_test.jpg")
        db.commit()
        check("повтор по storage_key не создаёт дубль", (not created2) and a2.id == a.id)
        twin = M.find_duplicate(db, owner, "deadbeef" * 8)
        check("дедуп по sha256 находит близнеца", twin is not None and twin.id == a.id)

        print("\n3. ПРАВА И ИЗОЛЯЦИЯ")
        check("владелец читает свой файл", M.can_read_asset(db, owner_u, a))
        check("чужой не читает", not M.can_read_asset(db, alien, a))
        check("чужой не пишет", not M.can_write_asset(db, alien, a))
        check("платформенный owner читает всё", M.can_read_asset(db, U(1, "owner"), a))
        alien_list = M.list_assets(db, alien, limit=200)
        check("в выдаче чужого нашего файла нет",
              all(i["id"] != a.id for i in alien_list["items"]))

        print("\n4. FILE_STATE (файла на диске нет)")
        check("по умолчанию present", a.file_state == "present")
        rec = M.reconcile(db, limit=5000)
        db.refresh(a)
        check("сверка пометила missing", a.file_state == "missing", rec)
        check("статус пользователя не подменён", a.status == "approved")
        check("deleted_at не выставлен", a.deleted_at is None)
        pub = M.asset_public(a)
        check("API не отдаёт битые ссылки",
              pub["thumb_url"] is None and pub["original_url"] is None)
        check("есть человеческая причина", bool(pub["unavailable_reason"]))
        rec2 = M.reconcile(db, limit=5000)
        check("повторная сверка не пишет событие снова", rec2["became_missing"] == 0)

        print("\n5. СВЯЗИ И USAGES")
        ci = db.execute(_t("SELECT id FROM campaign_items LIMIT 1")).fetchone()
        if ci:
            M.link(db, a, "campaign_item", str(ci[0]), "hero")
            db.commit()
            u = M.usages(db, a.id)
            check("usages показывает объявление", len(u) == 1 and u[0]["role"] == "hero")
            M.link(db, a, "campaign_item", str(ci[0]), "hero")
            db.commit()
            check("повторная связь не дублируется",
                  db.query(MediaLink).filter(MediaLink.media_id == a.id).count() == 1)
        else:
            print("  (нет campaign_items — связь не проверена)")

        print("\n6. КОРЗИНА")
        M.soft_delete(db, a, owner_u)
        db.commit()
        check("файл ушёл в корзину", a.deleted_at is not None)
        live = M.list_assets(db, owner_u, limit=200)
        check("из активной выдачи исчез", all(i["id"] != a.id for i in live["items"]))
        trash = M.list_assets(db, owner_u, trashed=True, limit=200)
        check("в корзине виден", any(i["id"] == a.id for i in trash["items"]))
        M.restore(db, a, owner_u)
        db.commit()
        check("восстановлен", a.deleted_at is None)

        print("\n7. ПРОИЗВОДИТЕЛЬНОСТЬ СПИСКА")
        t0 = time.time()
        r = M.list_assets(db, owner_u, limit=60)
        ms = int((time.time() - t0) * 1000)
        check("/list первая страница < 400 мс", ms < 400,
              "%d мс, всего %d" % (ms, r["total"]))
        t0 = time.time()
        M.list_assets(db, owner_u, source="mine", limit=60)
        ms3 = int((time.time() - t0) * 1000)
        check("фильтр «мои материалы» < 500 мс", ms3 < 500, "%d мс" % ms3)

    finally:
        for i in made:
            db.query(MediaLink).filter(MediaLink.media_id == i).delete(synchronize_session=False)
            obj = db.query(MediaAsset).get(i)
            if obj:
                db.delete(obj)
        db.commit()
        db.close()
    print("\nИТОГ: PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("ПРОВАЛЫ:", FAIL)


if __name__ == "__main__":
    main()
