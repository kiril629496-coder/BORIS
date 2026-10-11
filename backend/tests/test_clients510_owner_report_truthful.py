from __future__ import annotations
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))
from app.api import inbox_daily as report
DAY = dt.date(2026, 10, 8)
UTC = dt.timezone.utc
REAL_DATETIME = dt.datetime


class FixedDateTime(REAL_DATETIME):
    @classmethod
    def now(cls, tz=None):
        value = REAL_DATETIME(2026, 10, 8, 21, 30, tzinfo=UTC)
        return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)


class Rows:
    def __init__(self, rows=()): self.rows = list(rows)
    def scalar(self): return self.rows[0] if self.rows else None
    def scalars(self): return self
    def mappings(self): return self
    def all(self): return self.rows


class DB:
    def __init__(self, *, daily=None, settings=None, receipts=(), measurements=(), payloads=()):
        self.daily = daily
        self.settings = settings or {}
        self.receipts = list(receipts)
        self.measurements = list(measurements)
        self.payloads = list(payloads)
        self.queries = []
    def execute(self, statement, params=None):
        sql = str(statement); params = params or {}; self.queries.append((sql, params))
        if "FROM cpx_execution_receipts" in sql: return Rows(copy.deepcopy(self.receipts))
        if "FROM action_log" in sql: return Rows(copy.deepcopy(self.measurements))
        if "owner_daily_report_snapshots" in sql: return Rows(copy.deepcopy(self.payloads))
        if "FROM storage" in sql:
            if "key='kpi_settings'" in sql: return Rows([json.dumps(self.settings)])
            if str(params.get("k") or "").startswith("daily_stats:"): return Rows([json.dumps(self.daily)] if self.daily is not None else [])
        raise AssertionError("Unexpected query: " + sql)
    def commit(self): pass
    def close(self): pass


def rights(state="active", *, rop=False, trial=False):
    return {
        "marketing": {"state": state}, "rop": {"state": "known", "allowed": rop},
        "marketing_trial": {"entitlement_conflict": trial, "trial_until": "2026-10-10T10:23:08+00:00"} if trial else {},
    }


def payload(aid="client", *, budget=1000, leads=8, target=4, service=None, marker=True):
    return {
        "account_id": aid, "account_name": aid,
        "generated_at": "2026-10-08T21:30:00+00:00",
        "kpi": {"actual_leads": leads, "target_leads": target, "completion_pct": leads * 100 / target if leads is not None and target else None,
                "actual_leads_complete": leads is not None, "actual_leads_source": "avito_contacts" if leads is not None else "unavailable",
                "actual_cpl": 100 if leads else None, "target_cpl": 450, "actual_spend_rub": 800, "daily_budget_limit_rub": budget},
        "avito": {"status": "ok" if leads is not None else "not_proven", "stats_date": DAY.isoformat() if leads is not None else None,
                  "stats_date_proven": marker and leads is not None, "collected_at": "2026-10-08T20:06:00+00:00",
                  "views": 100 if leads is not None else None, "contacts": leads, "view_to_contact_pct": leads if leads is not None else None,
                  "spending_status": "ok", "spending_date": DAY.isoformat(), "ad_spend_rub": 700, "all_spend_rub": 800,
                  "spending_confirmed_at": "2026-10-08T20:06:00+00:00"},
        "messages": {}, "qualification": {}, "calls_crm": {},
        "rop_quality": {"calls_total": 0, "chats_total": 0, "calls_analyzed": 0, "chats_analyzed": 0},
        "ai_marketer": {"receipt_source": "cpx_execution_receipts", "actions": [], "count": 0, "bid_changes": 0,
                        "unique_bid_items": 0, "measurement_count": 0, "measurements": []},
        "current_service_rights": service or rights(),
    }


def render(rows):
    db = DB(payloads=rows)
    accounts = [{"account_id": p["account_id"], "rights": p.get("current_service_rights") or {}} for p in rows]
    with patch.object(report, "SessionLocal", return_value=db), patch.object(report, "_owner_report_schema"), patch.object(report, "_active_owner_accounts", return_value=accounts):
        return report.render_owner_daily_report(DAY)


class ProviderFactsTests(unittest.TestCase):
    def test_cache_day_without_provider_day_never_becomes_zero_statistics(self):
        data = {
            "date": DAY.isoformat(), "stats_date": None, "items": [], "collected_at": "2026-10-08T20:06:00+00:00",
            "completeness": {"complete": True, "stats_batches_total": 0},
            "spending": {"status": "ok", "date": DAY.isoformat(), "ad_spend_rub": 0, "all_spend_rub": 0,
                         "confirmed_at": "2026-10-08T20:06:00+00:00"},
        }
        db = DB(daily=data)
        avito = report._daily_avito_snapshot(db, "timofey", DAY)
        self.assertEqual(avito["status"], "not_proven")
        self.assertIsNone(avito["stats_date"])
        self.assertIsNone(avito["views"]); self.assertIsNone(avito["contacts"])
        with patch.object(report.datetime, "datetime", FixedDateTime):
            kpi = report._kpi_snapshot(db, "timofey", DAY, avito)
        self.assertIsNone(kpi["actual_leads"]); self.assertIsNone(kpi["actual_cpl"])
        # Independent same-day API-spending response remains distinct evidence.
        self.assertEqual(kpi["actual_spend_rub"], 0)
        self.assertEqual(avito["spending_confirmed_at"], "2026-10-08T20:06:00+00:00")

    def test_provider_other_day_not_relabelled_as_requested_day(self):
        db = DB(daily={"date": DAY.isoformat(), "stats_date": "2026-10-07", "items": [{"views": 200, "contacts": 8}]})
        avito = report._daily_avito_snapshot(db, "x", DAY)
        self.assertEqual(avito["status"], "stale")
        self.assertEqual(avito["stats_date"], "2026-10-07")
        self.assertIsNone(avito["views"]); self.assertIsNone(avito["contacts"])

    def test_complete_provider_zero_is_retained_as_real_zero(self):
        db = DB(daily={"date": DAY.isoformat(), "stats_date": DAY.isoformat(),
                       "completeness": {"complete": True, "stats_batches_total": 1},
                       "items": [{"views": 0, "contacts": 0, "status": "active"}]})
        avito = report._daily_avito_snapshot(db, "x", DAY)
        self.assertEqual(avito["status"], "ok"); self.assertTrue(avito["stats_date_proven"])
        self.assertEqual(avito["views"], 0); self.assertEqual(avito["contacts"], 0)

    def test_missing_counter_not_defaulted_to_zero(self):
        db = DB(daily={"stats_date": DAY.isoformat(), "items": [{"views": None, "contacts": 1}]})
        avito = report._daily_avito_snapshot(db, "x", DAY)
        self.assertNotEqual(avito["status"], "ok")
        self.assertIsNone(avito["views"])

    def test_other_day_spending_not_used_for_report_cpl(self):
        db = DB(settings={"target_leads_per_day": 4, "daily_budget_limit_rub": 1000})
        avito = {"status": "ok", "contacts": 4, "spending_status": "ok", "spending_date": "2026-10-09", "all_spend_rub": 400}
        with patch.object(report.datetime, "datetime", FixedDateTime):
            kpi = report._kpi_snapshot(db, "x", DAY, avito)
        self.assertEqual(kpi["actual_leads"], 4)
        self.assertIsNone(kpi["actual_cpl"]); self.assertIsNone(kpi["actual_spend_rub"])

    def test_inbound_floor_does_not_claim_final_plan_outcome(self):
        p = payload(leads=2, target=4)
        p["kpi"].update(actual_leads_complete=False, actual_leads_source="verified_inbound_floor", actual_cpl=None)
        text = render([p])["text"]
        self.assertIn("полный факт лидов не установлен", text)
        self.assertIn("план/факт не определён — 1", text)
        self.assertNotIn("🔴 Ниже плана", text)
        self.assertNotIn("выполнено 50", text)


class ReceiptTruthTests(unittest.TestCase):
    def receipt(self, rid=1, **changes):
        row = {"id": rid, "account_id": "garik", "request_key": f"request-{rid}", "item_id": 1000 + rid,
               "action": "raise", "source": "account_low_views_ramp", "status": "succeeded",
               "old_bid_penny": 0, "intended_bid_penny": 700, "observed_bid_penny": 700,
               "provider_http_status": 200, "finished_at": REAL_DATETIME(2026, 10, 8, 3, 9, tzinfo=UTC)}
        row.update(changes); return row

    def test_three_confirmed_bids_and_two_measurements_are_separate(self):
        receipts = [self.receipt(i) for i in (1968, 1977, 1983)]
        measurements = [{"id": i, "ts": REAL_DATETIME(2026, 10, 7, 21, 10), "source": "cpx_daily_learning",
                         "object_name": str(5000 + i), "after_val": json.dumps({"эффект": "worsened"})} for i in (1, 2)]
        db = DB(receipts=receipts, measurements=measurements)
        result = report._marketer_actions_snapshot(db, "garik", DAY)
        self.assertEqual(result["bid_changes"], 3)
        self.assertEqual(result["measurement_count"], 2)
        self.assertEqual([a["receipt_id"] for a in result["actions"]], [1968, 1977, 1983])
        self.assertEqual(result["receipt_source"], "cpx_execution_receipts")
        self.assertEqual(report._owner_report_time(result["actions"][0]["finished_at"]), "08.10.2026 06:09 МСК")
        self.assertEqual(report._owner_report_time(result["measurements"][0]["ts"]), "08.10.2026 00:10 МСК")

    def test_only_matching_success_readback_changed_bid_in_account_and_day_counts(self):
        invalid = [
            self.receipt(2, status="failed"), self.receipt(3, provider_http_status=402),
            self.receipt(4, observed_bid_penny=600), self.receipt(5, old_bid_penny=700),
            self.receipt(6, observed_bid_penny=None), self.receipt(7, account_id="other"),
            self.receipt(8, finished_at=REAL_DATETIME(2026, 10, 8, 21, 0, tzinfo=UTC)),
            self.receipt(9, finished_at=None), self.receipt(10, intended_bid_penny="NaN"),
        ]
        db = DB(receipts=[self.receipt(1), *invalid])
        result = report._marketer_actions_snapshot(db, "garik", DAY)
        self.assertEqual(result["bid_changes"], 1)
        self.assertEqual(result["actions"][0]["receipt_id"], 1)
        self.assertEqual(result["business_date"], "2026-10-08")

    def test_source_time_always_has_explicit_msk_date(self):
        self.assertEqual(report._owner_report_time("2026-10-08T20:10:38+00:00"), "08.10.2026 23:10 МСК")
        self.assertEqual(report._owner_report_time("2026-10-08T21:01:00+00:00"), "09.10.2026 00:01 МСК")
        self.assertEqual(report._owner_report_time("not a timestamp"), "время не подтверждено")

    def test_legacy_five_action_substring_count_not_presented_as_execution(self):
        p = payload()
        p["ai_marketer"] = {"count": 5, "bid_changes": 5, "actions": [{"action": "Оценил результат изменения ставки"}]}
        body = render([p])["text"]
        self.assertIn("число выполненных изменений ставок не подтверждено", body)
        self.assertNotIn("изменений ставок 5", body)


class RecommendationAndRightsTests(unittest.TestCase):
    def test_budget_zero_not_presented_as_scaling_or_execution(self):
        p = payload(budget=0, leads=2, target=4)
        body = render([p])["text"]
        self.assertIn("дневной рекламный лимит 0 ₽", body)
        self.assertIn("Рекомендации по данным дня — выполнение не подтверждено", body)
        self.assertNotIn("BORIS увеличивает", body); self.assertNotIn("BORIS масштабирует", body)
        self.assertNotIn("Рассмотреть усиление", body)

    def test_expired_service_with_goal_met_keeps_numbers_and_current_gate(self):
        p = payload(aid="evgeniy", service=rights("expired"))
        body = render([p])["text"]
        self.assertIn("План выполнен: факт 8 · план 4", body)
        self.assertIn("Текущий доступ маркетинга: срок услуги завершён", body)
        self.assertNotIn("Рассмотреть усиление", body)

    def test_trial_conflict_is_adjacent_to_numeric_goal_result(self):
        p = payload(aid="garik", service=rights("expired", trial=True))
        body = render([p])["text"]
        result_pos = body.index("План выполнен: факт 8 · план 4")
        gate_pos = body.index("Текущий доступ маркетинга: тест записан")
        self.assertLess(gate_pos - result_pos, 200)
        self.assertIn("расхождение не устранено", body)
        self.assertNotIn("BORIS масштабирует", body)

    def test_rop_without_rights_does_not_claim_connected_zero_over_zero(self):
        body = render([payload(service=rights(rop=False))])["text"]
        self.assertIn("РОП: текущий пакет не подтверждён", body)
        self.assertNotIn("РОП: звонков разобрано 0/0", body)

    def test_rop_active_with_no_events_is_not_success(self):
        body = render([payload(service=rights(rop=True))])["text"]
        self.assertIn("РОП: пакет действует; за отчётный день нет", body)
        self.assertIn("Работа модуля по 0/0 не подтверждается", body)

    def test_legacy_provider_date_source_requires_new_confirmation(self):
        p = payload(marker=False)
        body = render([p])["text"]
        self.assertIn("просмотры и контакты не подтверждены", body)
        self.assertIn("Факт лидов не подтверждён", body)
        self.assertNotIn("План выполнен: факт 8", body)

    def test_unknown_stats_and_independent_zero_spending_explicitly_separated(self):
        p = payload(aid="timofey", leads=None, target=None)
        p["avito"].update(ad_spend_rub=0, all_spend_rub=0)
        p["kpi"].update(actual_spend_rub=0)
        body = render([p])["text"]
        self.assertIn("просмотры и контакты не подтверждены", body)
        self.assertIn("API списаний за 08.10.2026", body)
        self.assertIn("расход Avito всего 0 ₽", body)
        self.assertNotIn("просмотры 0", body); self.assertNotIn("контакты 0", body)
        self.assertIn("полный факт контактов не подтверждён", body)

    def test_channel_replies_are_not_attributed_to_mop(self):
        p = payload()
        p["messages"] = {"dialogs": 7, "replied_dialogs": 7}
        body = render([p])["text"]
        self.assertIn("ответы канала за день (включая ручные)", body)
        self.assertIn("автор ответа здесь не классифицирован", body)
        self.assertNotIn("МОП: диалогов 7", body)
        self.assertIn("Отправки МОПа за последние 24 часа", body)

    def test_report_day_and_generated_timestamp_are_distinct(self):
        body = render([payload()])["text"]
        self.assertIn("отчёты по аккаунтам · 08.10.2026", body)
        self.assertIn("Отчёт сформирован: <b>09.10.2026 00:30 МСК", body)
        self.assertIn("дата провайдера 2026-10-08", body)
        self.assertIn("кэш получен 08.10.2026 23:06 МСК", body)


if __name__ == "__main__":
    unittest.main()
