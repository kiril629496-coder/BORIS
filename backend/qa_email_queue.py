"""
Автотесты очереди писем BORIS AI.

Запуск:  cd /root/BORIS/backend && venv/bin/python3 qa_email_queue.py
С реальной отправкой одного письма:  ... qa_email_queue.py --real

Почти все сценарии подменяют транспорт заглушкой, поэтому тесты быстрые,
детерминированные и не шлют почту наружу. Все созданные строки помечены
source='qa_auto' и удаляются в конце — боевые письма не затрагиваются.
"""

import sys
import threading
import time

sys.path.insert(0, "/root/BORIS/backend")

from dotenv import load_dotenv

load_dotenv("/root/BORIS/backend/.env")

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import email_queue as Q
from app.services import email_queue_ref as R
from app.services import email_service as ES

SOURCE = "qa_auto"
TO = "qa-queue@borisqa.ru"
RESULTS = []
_real_send = ES.send_email


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print("  %s  %-46s %s" % ("PASS" if condition else "FAIL", name, detail))


def stub(ok=True, reason="ok", message_id="<qa@test>"):
    """Подменяет транспорт: очередь работает, наружу ничего не уходит."""
    def _fake(to, subject, body, **kwargs):
        return ok, reason, message_id
    ES.send_email = _fake
    Q._es.send_email = _fake


def unstub():
    ES.send_email = _real_send
    Q._es.send_email = _real_send


def row(queue_id):
    db = SessionLocal()
    try:
        return db.execute(text(
            "SELECT status, attempts, next_attempt_at, last_error, provider_message_id "
            "FROM email_queue WHERE id=:id"), {"id": queue_id}).fetchone()
    finally:
        db.close()


def events(queue_id):
    db = SessionLocal()
    try:
        return [r[0] for r in db.execute(text(
            "SELECT event_type FROM email_delivery_events WHERE email_queue_id=:id "
            "ORDER BY id"), {"id": queue_id}).fetchall()]
    finally:
        db.close()


def cleanup():
    db = SessionLocal()
    try:
        ids = [r[0] for r in db.execute(text(
            "SELECT id FROM email_queue WHERE source=:s "
            "OR ref_id LIKE 'qaauto-%'"), {"s": SOURCE}).fetchall()]
        if ids:
            db.execute(text("DELETE FROM email_delivery_events WHERE email_queue_id = ANY(:ids)"),
                       {"ids": ids})
            db.execute(text("DELETE FROM email_queue WHERE id = ANY(:ids)"), {"ids": ids})
            db.commit()
        return len(ids)
    finally:
        db.close()


def t1_success():
    stub(True, "ok", "<qa-1@test>")
    res = Q.enqueue_email(TO, "QA успешная отправка", "тело", source=SOURCE)
    st = row(res["id"])
    check("1. успешная отправка", res["status"] == "sent" and st[0] == "sent",
          "статус %s, попыток %s" % (st[0], st[1]))
    check("1a. событие sent в журнале", "sent" in events(res["id"]), str(events(res["id"])))


def t2_duplicate():
    stub(True)
    key = "qaauto-dup-%d" % int(time.time())
    first = Q.enqueue_email(TO, "QA дубль", "раз", source=SOURCE, idempotency_key=key)
    second = Q.enqueue_email(TO, "QA дубль", "два", source=SOURCE, idempotency_key=key)
    db = SessionLocal()
    try:
        count = db.execute(text("SELECT count(*) FROM email_queue WHERE idempotency_key=:k"),
                           {"k": key}).scalar()
    finally:
        db.close()
    check("2. дубль не создаёт второе письмо",
          second["duplicate"] and second["id"] == first["id"] and count == 1,
          "строк с ключом: %s" % count)
    check("2a. событие duplicate", "duplicate" in events(first["id"]), str(events(first["id"])))


def t3_temporary():
    stub(False, "SMTPServerDisconnected")
    res = Q.enqueue_email(TO, "QA временная ошибка", "тело", source=SOURCE)
    st = row(res["id"])
    check("3. временная ошибка уходит в retry",
          res["status"] == "retrying" and st[0] == "queued" and st[1] == 1,
          "статус %s, попыток %s, повтор в %s" % (st[0], st[1], st[2]))
    check("3a. время следующей попытки задано", st[2] is not None, str(st[2]))


def t4_permanent():
    stub(False, "SMTPRecipientsRefused")
    res = Q.enqueue_email(TO, "QA постоянная ошибка", "тело", source=SOURCE)
    st = row(res["id"])
    check("4. постоянная ошибка сразу в dead",
          res["status"] == "dead" and st[0] == "dead" and st[1] == 1,
          "статус %s, попыток %s" % (st[0], st[1]))


def t5_retry_limit():
    stub(False, "SMTPServerDisconnected")
    res = Q.enqueue_email(TO, "QA лимит попыток", "тело", source=SOURCE,
                          max_attempts=2, send_now=False)
    db = SessionLocal()
    try:
        db.execute(text("UPDATE email_queue SET next_attempt_at=now() WHERE id=:id"),
                   {"id": res["id"]})
        db.commit()
    finally:
        db.close()
    first = Q.process_one(res["id"])
    db = SessionLocal()
    try:
        db.execute(text("UPDATE email_queue SET next_attempt_at=now() WHERE id=:id"),
                   {"id": res["id"]})
        db.commit()
    finally:
        db.close()
    second = Q.process_one(res["id"])
    st = row(res["id"])
    check("5. превышение попыток даёт dead",
          first == "retrying" and second == "dead" and st[0] == "dead",
          "%s -> %s, попыток %s" % (first, second, st[1]))


def t6_dead_not_picked():
    stub(True)
    res = Q.enqueue_email(TO, "QA dead не подбирается", "тело", source=SOURCE, send_now=False)
    db = SessionLocal()
    try:
        db.execute(text("UPDATE email_queue SET status='dead' WHERE id=:id"), {"id": res["id"]})
        db.commit()
    finally:
        db.close()
    Q.process_batch()
    st = row(res["id"])
    check("6. воркер не трогает dead", st[0] == "dead" and st[1] == 0,
          "статус %s, попыток %s" % (st[0], st[1]))


def t7_two_workers():
    """Два воркера разом: каждое письмо должно достаться ровно одному."""
    stub(True)
    ids = []
    for i in range(6):
        res = Q.enqueue_email(TO, "QA параллель %d" % i, "тело", source=SOURCE, send_now=False)
        ids.append(res["id"])
    taken = []
    lock = threading.Lock()

    def worker():
        db = SessionLocal()
        try:
            rows = Q._claim(db, limit=6)
            with lock:
                taken.extend(r[0] for r in rows)
        finally:
            db.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    check("7. два воркера не берут одно письмо",
          len(taken) == len(set(taken)),
          "захвачено %d, уникальных %d" % (len(taken), len(set(taken))))
    db = SessionLocal()
    try:
        db.execute(text("UPDATE email_queue SET status='queued' WHERE id = ANY(:ids)"),
                   {"ids": ids})
        db.commit()
    finally:
        db.close()


def t8_restart_recovery():
    """После рестарта письмо, ждавшее повтора, подхватывается новым процессом."""
    stub(False, "SMTPServerDisconnected")
    res = Q.enqueue_email(TO, "QA восстановление", "тело", source=SOURCE)
    db = SessionLocal()
    try:
        db.execute(text("UPDATE email_queue SET next_attempt_at=now() WHERE id=:id"),
                   {"id": res["id"]})
        db.commit()
    finally:
        db.close()
    stub(True)
    result = Q.process_batch()
    st = row(res["id"])
    check("8. письмо после паузы дожимается воркером",
          st[0] == "sent" and st[1] == 2,
          "статус %s, попыток %s, итог %s" % (st[0], st[1], result))


def t9_real_send():
    unstub()
    res = Q.enqueue_email("ostapenko-kirill-86@yandex.ru", "BORIS — QA реальная отправка",
                          "Проверка боевого транспорта из автотестов.", source=SOURCE)
    st = row(res["id"])
    check("9. реальная отправка через SMTP", st[0] == "sent",
          "статус %s, ошибка %s" % (st[0], st[3]))


def main():
    print("QA очереди писем")
    print("-" * 66)
    try:
        for test in (t1_success, t2_duplicate, t3_temporary, t4_permanent,
                     t5_retry_limit, t6_dead_not_picked, t7_two_workers,
                     t8_restart_recovery, t10_cancel_queued, t11_cancel_retrying,
                     t12_expired, t13_used_code, t14_valid_sent, t15_two_resends):
            try:
                test()
            except Exception as exc:
                check(test.__name__, False, "исключение %s: %s" % (type(exc).__name__, exc))
        if "--real" in sys.argv:
            try:
                t9_real_send()
            except Exception as exc:
                check("9. реальная отправка", False, str(exc))
    finally:
        unstub()
        removed = cleanup()
        drop_verifications()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("-" * 66)
    print("ИТОГ: %d/%d PASS, удалено тестовых строк: %d" % (passed, len(RESULTS), removed))
    return 0 if passed == len(RESULTS) else 1




# ---------- тесты 10-15: привязка письма к verification-записи ----------

SMTP_CALLS = {"n": 0}


def stub_counting():
    """Заглушка со счётчиком: если счётчик не вырос, SMTP действительно не вызывали."""
    def _fake(to, subject, body, **kwargs):
        SMTP_CALLS["n"] += 1
        return True, "ok", "<qa@test>"
    ES.send_email = _fake
    Q._es.send_email = _fake


def make_verification(minutes=10):
    """Создаёт запись email_verifications напрямую. Возвращает verification_id."""
    vid = "qaauto-%d-%d" % (int(time.time() * 1000), SMTP_CALLS["n"])
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO email_verifications (verification_id, user_id, email, code_hash, "
            "expires_at, attempts_count, max_attempts, sent_at, created_at) "
            "VALUES (:v, :uid, :e, 'qa', now() + (:m || ' minutes')::interval, 0, 5, now(), now())"),
            {"v": vid, "e": TO, "m": str(minutes),
             "uid": db.execute(text("SELECT id FROM users ORDER BY id LIMIT 1")).scalar()})
        db.commit()
    finally:
        db.close()
    return vid


def drop_verifications():
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM email_verifications WHERE verification_id LIKE 'qaauto-%'"))
        db.commit()
    finally:
        db.close()


def set_used(vid):
    db = SessionLocal()
    try:
        db.execute(text("UPDATE email_verifications SET used_at=now() WHERE verification_id=:v"),
                   {"v": vid})
        db.commit()
    finally:
        db.close()


def set_queued(queue_id, attempts=0):
    db = SessionLocal()
    try:
        db.execute(text("UPDATE email_queue SET status='queued', attempts=:a WHERE id=:i"),
                   {"i": queue_id, "a": attempts})
        db.commit()
    finally:
        db.close()


def t10_cancel_queued():
    stub_counting()
    vid = make_verification()
    res = R.enqueue_verification(TO, "QA отмена queued", "тело", vid)
    set_queued(res["id"])
    cancelled = R.cancel_pending_verification(TO)
    st = row(res["id"])
    check("10. старый queued отменён", st[0] == "cancelled" and cancelled >= 1,
          "статус %s, отменено %s" % (st[0], cancelled))
    check("10a. событие cancelled", "cancelled" in events(res["id"]), str(events(res["id"])))


def t11_cancel_retrying():
    stub_counting()
    vid = make_verification()
    res = R.enqueue_verification(TO, "QA отмена retry", "тело", vid)
    set_queued(res["id"], attempts=2)
    R.cancel_pending_verification(TO)
    st = row(res["id"])
    check("11. ждущий повтора отменён", st[0] == "cancelled" and st[1] == 2,
          "статус %s, попыток %s" % (st[0], st[1]))


def t12_expired():
    stub_counting()
    vid = make_verification()
    res = R.enqueue_verification(TO, "QA просрочка", "тело", vid)
    before = SMTP_CALLS["n"]
    db = SessionLocal()
    try:
        db.execute(text("UPDATE email_verifications SET expires_at = now() - interval '1 minute' "
                        "WHERE verification_id=:v"), {"v": vid})
        db.execute(text("UPDATE email_queue SET status='queued', expires_at = now() - "
                        "interval '1 minute' WHERE id=:i"), {"i": res["id"]})
        db.commit()
    finally:
        db.close()
    Q.process_one(res["id"])
    st = row(res["id"])
    check("12. просроченный код даёт expired, не dead",
          st[0] == "expired" and SMTP_CALLS["n"] == before,
          "статус %s, вызовов SMTP %d" % (st[0], SMTP_CALLS["n"] - before))
    check("12a. событие expired", "expired" in events(res["id"]), str(events(res["id"])))


def t13_used_code():
    stub_counting()
    vid = make_verification()
    res = R.enqueue_verification(TO, "QA использованный код", "тело", vid)
    before = SMTP_CALLS["n"]
    set_used(vid)
    set_queued(res["id"])
    Q.process_one(res["id"])
    st = row(res["id"])
    check("13. использованный код не отправляется",
          st[0] == "cancelled" and SMTP_CALLS["n"] == before,
          "статус %s, вызовов SMTP %d" % (st[0], SMTP_CALLS["n"] - before))


def t14_valid_sent():
    stub_counting()
    vid = make_verification()
    before = SMTP_CALLS["n"]
    res = R.enqueue_verification(TO, "QA актуальный код", "тело", vid)
    st = row(res["id"])
    check("14. актуальный код отправляется",
          st[0] == "sent" and SMTP_CALLS["n"] == before + 1,
          "статус %s, вызовов SMTP %d" % (st[0], SMTP_CALLS["n"] - before))


def t15_two_resends():
    """Два resend подряд: уходит только последний код, прежнее письмо гасится."""
    stub_counting()
    vid1 = make_verification()
    first = R.enqueue_verification(TO, "QA resend первый", "тело", vid1)
    set_queued(first["id"])
    set_used(vid1)
    R.cancel_pending_verification(TO)
    vid2 = make_verification()
    before = SMTP_CALLS["n"]
    second = R.enqueue_verification(TO, "QA resend второй", "тело", vid2)
    st1, st2 = row(first["id"]), row(second["id"])
    check("15. два resend: уходит только актуальный",
          st1[0] == "cancelled" and st2[0] == "sent" and SMTP_CALLS["n"] == before + 1,
          "первое %s, второе %s, вызовов SMTP %d" % (st1[0], st2[0], SMTP_CALLS["n"] - before))


if __name__ == "__main__":
    sys.exit(main())
