import datetime
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.ext_api import clients, report


class ClientBusinessTruthContractTest(unittest.TestCase):
    def setUp(self):
        report._TRUTH_CACHE.update(at=0.0, hours=None, data=None)

    def _anton_truth(self, creative):
        spec = {
            "key": "CLIENT_ANTON",
            "scope": {"account": "antonybatono_78732"},
        }
        meter = {
            "total": 5,
            "delta": 0,
            "last": None,
            "measurable": True,
            "unit": "готовых объявлений",
        }
        with (
            patch.object(clients, "CLIENTS", [spec]),
            patch.object(clients, "_existing", return_value=None),
            patch.object(report, "_acct_id", return_value=None),
            patch.object(report, "_measure", return_value=meter),
            patch.object(report, "_feed_item_count", return_value=5),
            patch.object(report, "_current_campaign_creative_stats", return_value=creative),
            patch.object(
                report,
                "_state_of",
                return_value={"state": "STALLED", "proof": "нет движения"},
            ),
        ):
            report._TRUTH_CACHE.update(at=0.0, hours=None, data=None)
            return report.business_truth(2, fresh=True)["clients"][0]

    def test_anton_done_requires_full_creative_contract(self):
        row = self._anton_truth({
            "measurable": True,
            "campaign_id": 226,
            "card_count": 5,
            "image_count": 30,
            "banner_count": 5,
            "hero_media_count": 5,
            "missing_local_refs": [],
        })
        self.assertTrue(row["overall_done"], row)
        self.assertTrue(row["contract_measurable"], row)
        self.assertTrue(row["image_ok"], row)
        self.assertTrue(row["banner_ok"], row)
        self.assertEqual(row["image_count"], 30)
        self.assertEqual(row["banner_count"], 5)
        self.assertEqual(row["state"], "DONE")

    def test_anton_three_banners_is_not_done_even_with_five_cards_and_feed(self):
        row = self._anton_truth({
            "measurable": True,
            "campaign_id": 226,
            "card_count": 5,
            "image_count": 30,
            "banner_count": 3,
            "hero_media_count": 5,
            "missing_local_refs": [],
        })
        self.assertFalse(row["overall_done"], row)
        self.assertTrue(row["contract_measurable"], row)
        self.assertTrue(row["image_ok"], row)
        self.assertFalse(row["banner_ok"], row)
        self.assertEqual(row["state"], "WAITING_DEPLOY")
        self.assertIn("баннеры 3/5", row["proof"])

    def test_unmeasurable_creatives_fail_closed(self):
        row = self._anton_truth({
            "measurable": False,
            "campaign_id": None,
            "card_count": 0,
            "image_count": 0,
            "banner_count": 0,
            "hero_media_count": 0,
            "missing_local_refs": [],
        })
        self.assertFalse(row["overall_done"], row)
        self.assertFalse(row["contract_measurable"], row)

    def test_creative_stats_count_current_package_payload_not_global_media_stock(self):
        items = []
        for idx in range(5):
            banner = f"/images/a/banners/banner-{idx}.png"
            images = [banner] + [
                f"/images/a/photos/{idx}-{photo}.png" for photo in range(6)
            ]
            items.append({
                "id": 100 + idx,
                "status": "published",
                "hero_media_id": 500 + idx,
                "payload_json": json.dumps({
                    "images": images,
                    "banner_url": banner,
                }),
            })

        with (
            patch.object(report, "_acct_id", return_value=None),
            patch.object(
                report.db,
                "one",
                side_effect=[
                    {"id": 226, "ready_n": 5, "viable_n": 5, "effective_n": 5},
                    {"n": 5},
                ],
            ),
            patch.object(report.db, "rows", return_value=items),
            patch.object(report, "_local_asset_present", return_value=True),
        ):
            stats = report._current_campaign_creative_stats("a", 5)

        self.assertTrue(stats["measurable"], stats)
        self.assertEqual(stats["campaign_id"], 226)
        self.assertEqual(stats["card_count"], 5)
        self.assertEqual(stats["image_count"], 30)
        self.assertEqual(stats["banner_count"], 5)
        self.assertEqual(stats["hero_media_count"], 5)

    def test_fresh_measurable_regression_reopens_old_terminal_receipts(self):
        job = {"id": 618, "current_phase": "TESTING"}
        truth = {
            "key": "CLIENT_ANTON",
            "overall_done": False,
            "measurable": True,
            "contract_measurable": True,
            "state": "WAITING_DEPLOY",
            "proof": "баннеры 3/5",
        }
        with (
            patch.object(clients.db, "rows", return_value=[{"id": 10}, {"id": 11}]) as rows,
            patch.object(clients.a2a, "message") as message,
            patch.object(clients.dev, "event") as event,
        ):
            out = clients._reopen_terminal_receipts_if_business_regressed(job, truth)
        self.assertEqual(out, [10, 11])
        self.assertIn("business_goal_already_confirmed", rows.call_args.args[0])
        self.assertIn("business_goal_regressed_after_fresh_truth", rows.call_args.args[0])
        self.assertEqual(message.call_count, 2)
        event.assert_called_once()

    def test_unmeasurable_regression_does_not_reopen_terminal_receipt(self):
        job = {"id": 618, "current_phase": "TESTING"}
        truth = {
            "key": "CLIENT_ANTON",
            "overall_done": False,
            "measurable": True,
            "contract_measurable": False,
        }
        with patch.object(clients.db, "rows") as rows:
            out = clients._reopen_terminal_receipts_if_business_regressed(job, truth)
        self.assertEqual(out, [])
        rows.assert_not_called()

    def test_recovery_job_terminalizes_when_fresh_business_truth_is_done(self):
        job = {
            "id": 1101,
            "request_id": "business_recovery:CLIENT_ANTON:after:618",
            "status": "blocked",
            "current_phase": "PLANNING",
        }
        truth = {
            "key": "CLIENT_ANTON",
            "overall_done": True,
            "measurable": True,
            "contract_measurable": True,
            "state": "DONE",
            "progress": 100,
            "proof": "5/5, фото 30/25, баннеры 5/5, feed ok",
        }
        with (
            patch.object(clients.db, "q") as q,
            patch.object(clients.dev, "event") as event,
            patch.object(clients.dev, "release") as release,
        ):
            out = clients._terminalize_recovery_if_business_done(job, truth)
        self.assertTrue(out)
        self.assertEqual(q.call_count, 2)
        self.assertIn("business_goal_already_confirmed", q.call_args_list[0].args[0])
        self.assertIn("status='cancelled'", q.call_args_list[1].args[0])
        payload = json.loads(q.call_args_list[1].kwargs["result"])
        self.assertEqual(payload["REAL_RESULT"]["progress"], 100)
        self.assertEqual(payload["REAL_RESULT"]["reason"], "business_goal_already_confirmed")
        event.assert_called_once()
        release.assert_called_once_with(1101)

    def test_canonical_non_recovery_job_is_not_terminalized_by_business_truth(self):
        job = {
            "id": 618,
            "request_id": "req_client_anton",
            "status": "blocked",
            "current_phase": "TESTING",
        }
        truth = {
            "key": "CLIENT_ANTON",
            "overall_done": True,
            "measurable": True,
            "contract_measurable": True,
        }
        with patch.object(clients.db, "q") as q:
            out = clients._terminalize_recovery_if_business_done(job, truth)
        self.assertFalse(out)
        q.assert_not_called()

    def test_client_cabinet_contract_is_three_real_workspaces(self):
        conf = report.ARTIFACTS["CLIENT_CABINET"]
        self.assertEqual(conf["kind"], "cabinet")
        self.assertEqual(conf["expected"], 3)
        self.assertEqual(
            conf["accounts"],
            [
                "antonybatono_78732",
                "vasyailchenko_82650",
                "planetazayavki_65985",
            ],
        )

    def test_client_cabinet_measure_counts_only_workspaces_with_dashboard_data(self):
        targets = ["a", "b", "c"]
        rows = [
            {"account_id": "a", "media_total": 5, "catalog_total": 0, "last_t": None, "recent": True},
            {"account_id": "b", "media_total": 0, "catalog_total": 2, "last_t": None, "recent": False},
            {"account_id": "c", "media_total": 0, "catalog_total": 0, "last_t": None, "recent": False},
        ]
        with (
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(report.db, "rows", return_value=rows),
        ):
            measured = report._measure("cabinet", targets, 2)
        self.assertTrue(measured["measurable"], measured)
        self.assertEqual(measured["total"], 2)
        self.assertEqual(measured["delta"], 1)
        self.assertIn("кабинетов", measured["unit"])

    def test_client_cabinet_measure_fails_closed_if_target_account_missing(self):
        targets = ["a", "b", "c"]
        rows = [
            {"account_id": "a", "media_total": 5, "catalog_total": 0, "last_t": None, "recent": False},
            {"account_id": "b", "media_total": 2, "catalog_total": 0, "last_t": None, "recent": False},
        ]
        with (
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(report.db, "rows", return_value=rows),
        ):
            measured = report._measure("cabinet", targets, 2)
        self.assertFalse(measured["measurable"], measured)
        self.assertEqual(measured["total"], 0)

    def test_vasily_current_contract_stays_done(self):
        spec = {
            "key": "CLIENT_VASILY",
            "scope": {"account": "vasyailchenko_82650"},
        }
        meter = {
            "total": 10,
            "delta": 0,
            "last": None,
            "measurable": True,
            "unit": "готовых карточек",
        }
        with (
            patch.object(clients, "CLIENTS", [spec]),
            patch.object(clients, "_existing", return_value=None),
            patch.object(report, "_acct_id", return_value=None),
            patch.object(report, "_measure", return_value=meter),
            patch.object(
                report,
                "_state_of",
                return_value={"state": "STALLED", "proof": "нет движения"},
            ),
        ):
            report._TRUTH_CACHE.update(at=0.0, hours=None, data=None)
            row = report.business_truth(2, fresh=True)["clients"][0]
        self.assertTrue(row["overall_done"], row)
        self.assertTrue(row["contract_measurable"], row)
        self.assertEqual(row["expected_count"], 10)
        self.assertFalse(row["requires_feed"], row)

    def test_penza_current_contract_stays_done_with_full_internal_feed(self):
        spec = {
            "key": "CLIENT_ANDREY_PENZA",
            "scope": {"account": "andrey_launzh_mebel_akkaunt_penza_82435"},
        }
        meter = {
            "total": 151,
            "delta": 0,
            "last": None,
            "measurable": True,
            "unit": "готовых объявлений",
        }
        with (
            patch.object(clients, "CLIENTS", [spec]),
            patch.object(clients, "_existing", return_value=None),
            patch.object(report, "_acct_id", return_value=None),
            patch.object(report, "_measure", return_value=meter),
            patch.object(report, "_feed_item_count", return_value=151),
            patch.object(report, "_live_avito_count", return_value=0),
            patch.object(
                report,
                "_state_of",
                return_value={"state": "STALLED", "proof": "нет движения"},
            ),
        ):
            report._TRUTH_CACHE.update(at=0.0, hours=None, data=None)
            row = report.business_truth(2, fresh=True)["clients"][0]
        self.assertTrue(row["overall_done"], row)
        self.assertTrue(row["contract_measurable"], row)
        self.assertEqual(row["feed_count"], 151)
        self.assertTrue(row["feed_ok"], row)
        self.assertTrue(row["show_live"], row)
        self.assertFalse(row["live_required"], row)
        self.assertEqual(row["live_count"], 0)

    def test_planeta_contract_uses_current_package_identity_not_superseded_144(self):
        self.assertIn("planeta-real", clients.BUSINESS_GOAL["CLIENT_PLANETA_MEBELI"])
        self.assertNotIn("актуальной кампании 144", clients.BUSINESS_GOAL["CLIENT_PLANETA_MEBELI"])
        self.assertIn(
            "planeta-real",
            clients.BUSINESS_CONTRACT["CLIENT_PLANETA_MEBELI"]["method"],
        )
        self.assertEqual(
            report.ARTIFACTS["CLIENT_PLANETA_MEBELI"]["source_ref_prefix"],
            "planeta-real:",
        )


    def test_bytovki_delivery_contract_uses_two_account_receipts(self):
        conf = report.ARTIFACTS["CLIENT_BYTOVKI_REPORTS"]
        self.assertEqual(conf["kind"], "delivery")
        self.assertEqual(conf["expected"], 2)
        self.assertEqual(
            conf["accounts"],
            ["prodazha_bytovok_25677", "3411770_94346"],
        )
        self.assertEqual(conf["delivery_tz"], "Europe/Moscow")
        self.assertEqual(conf["delivery_due_hour"], 20)
        self.assertEqual(conf["delivery_due_minute"], 35)
        spec = next(
            x for x in clients.CLIENTS if x.get("key") == "CLIENT_BYTOVKI_REPORTS"
        )
        self.assertIn("thread_id=0", spec["goal"])
        self.assertNotIn("-1004425842869, тема 1", spec["goal"])
        self.assertTrue(
            any("thread_id=0" in x for x in spec["acceptance"])
        )

    def test_delivery_measure_counts_only_current_success_receipts(self):
        today = datetime.datetime.now(
            ZoneInfo("Europe/Moscow")
        ).date().isoformat()
        good = lambda account, mid: {
            "account_id": account,
            "value": json.dumps({
                "automated_daily": {
                    "account_id": account,
                    "report_date": today,
                    "status": "delivered",
                    "telegram_message_id": mid,
                    "sent_at": f"{today}T17:30:00+00:00",
                }
            }),
        }
        rows = [
            good("prodazha_bytovok_25677", 79),
            good("3411770_94346", 80),
        ]
        conf = report.ARTIFACTS["CLIENT_BYTOVKI_REPORTS"]
        with (
            patch.dict(conf, {"delivery_due_hour": 0, "delivery_due_minute": 0}),
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(report.db, "rows", return_value=rows),
        ):
            measured = report._measure("delivery", conf["accounts"], 24)
        self.assertTrue(measured["measurable"], measured)
        self.assertEqual(measured["total"], 2)
        self.assertEqual(
            measured["delivered_accounts"],
            ["3411770_94346", "prodazha_bytovok_25677"],
        )

    def test_delivery_measure_rejects_failed_and_wrong_date_receipts(self):
        today = datetime.datetime.now(
            ZoneInfo("Europe/Moscow")
        ).date().isoformat()
        yesterday = (
            datetime.datetime.now(ZoneInfo("Europe/Moscow")).date()
            - datetime.timedelta(days=1)
        ).isoformat()
        rows = [
            {
                "account_id": "prodazha_bytovok_25677",
                "value": json.dumps({
                    "automated_daily": {
                        "account_id": "prodazha_bytovok_25677",
                        "report_date": today,
                        "status": "delivered",
                        "telegram_message_id": 79,
                    }
                }),
            },
            {
                "account_id": "3411770_94346",
                "value": json.dumps({
                    "automated_daily": {
                        "account_id": "3411770_94346",
                        "report_date": yesterday,
                        "status": "delivered",
                        "telegram_message_id": 80,
                    }
                }),
            },
            {
                "account_id": "3411770_94346",
                "value": json.dumps({
                    "automated_daily": {
                        "account_id": "3411770_94346",
                        "report_date": today,
                        "status": "failed",
                        "telegram_message_id": 81,
                    }
                }),
            },
        ]
        conf = report.ARTIFACTS["CLIENT_BYTOVKI_REPORTS"]
        with (
            patch.dict(conf, {"delivery_due_hour": 0, "delivery_due_minute": 0}),
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(report.db, "rows", return_value=rows),
        ):
            measured = report._measure("delivery", conf["accounts"], 24)
        self.assertTrue(measured["measurable"], measured)
        self.assertEqual(measured["total"], 1)
        self.assertEqual(
            measured["delivered_accounts"],
            ["prodazha_bytovok_25677"],
        )

    def test_terminal_regression_creates_one_recovery_lifecycle(self):
        terminal = {
            "id": 969,
            "dev_job_id": "dev_969",
            "status": "completed",
            "current_phase": "COMPLETED",
        }
        truth = {
            "key": "CLIENT_BYTOVKI_REPORTS",
            "overall_done": False,
            "measurable": True,
            "contract_measurable": True,
            "state": "STALLED",
            "proof": "1/2 delivery receipts",
        }
        recovery = {
            "id": 1200,
            "dev_job_id": "dev_1200",
            "status": "queued",
            "current_phase": "PLANNING",
        }
        with (
            patch.object(clients.db, "one", side_effect=[None, {"id": 1200}]),
            patch.object(clients.dev, "create", return_value=recovery) as create,
            patch.object(clients.dev, "event") as event,
            patch.object(clients.dev, "get", return_value=recovery),
        ):
            first, first_created = clients._recovery_job_if_business_regressed(
                "CLIENT_BYTOVKI_REPORTS",
                clients.BUSINESS_GOAL["CLIENT_BYTOVKI_REPORTS"],
                terminal,
                truth,
            )
            second, second_created = clients._recovery_job_if_business_regressed(
                "CLIENT_BYTOVKI_REPORTS",
                clients.BUSINESS_GOAL["CLIENT_BYTOVKI_REPORTS"],
                terminal,
                truth,
            )
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["id"], 1200)
        self.assertEqual(second["id"], 1200)
        self.assertEqual(create.call_count, 1)
        event.assert_called_once()

    def test_terminal_unmeasurable_state_does_not_create_recovery(self):
        terminal = {"id": 969, "status": "completed"}
        truth = {
            "overall_done": False,
            "measurable": False,
            "contract_measurable": False,
        }
        with patch.object(clients.dev, "create") as create:
            out, created = clients._recovery_job_if_business_regressed(
                "CLIENT_BYTOVKI_REPORTS",
                clients.BUSINESS_GOAL["CLIENT_BYTOVKI_REPORTS"],
                terminal,
                truth,
            )
        self.assertFalse(created)
        self.assertIs(out, terminal)
        create.assert_not_called()

    def test_existing_uses_latest_lifecycle(self):
        with (
            patch.object(clients.db, "one", return_value={"id": 1200}) as one,
            patch.object(clients.dev, "get", return_value={"id": 1200}),
        ):
            out = clients._existing("CLIENT_BYTOVKI_REPORTS")
        self.assertEqual(out["id"], 1200)
        self.assertIn("ORDER BY id DESC LIMIT 1", one.call_args.args[0])

    def test_calls_business_truth_uses_current_delivered_digest_contract(self):
        today = datetime.datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
        state = {
            "message_id": 81,
            "sha256": "abc",
            "contract": "avito_calls_truth_v2",
            "report_date": today,
            "updated_at": f"{today}T18:00:00+00:00",
            "text": (
                f"📞 Разбор звонков — {today}\n\n"
                "Всего звонков по Avito: 0\n"
                "Отвечено: 0\n"
                "Пропущено: 0\n"
                "Разобрано разговоров: 0\n\n"
                "AI-разбор не выполняется: период РОП завершён."
            ),
        }
        conf = report.ARTIFACTS["DEV_CALLS_REPORT_FIX"]
        with (
            patch.dict(conf, {"delivery_due_hour": 0, "delivery_due_minute": 0}),
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(
                report.db,
                "rows",
                return_value=[{"key": "rop_digest_msg:test", "value": json.dumps(state)}],
            ),
        ):
            measured = report._measure("calls", None, 24)
        self.assertTrue(measured["measurable"], measured)
        self.assertEqual(measured["total"], 1)
        self.assertEqual(measured["delivery_message_id"], 81)
        self.assertEqual(measured["contract"], "avito_calls_truth_v2")

    def test_calls_business_truth_rejects_stale_or_old_contract_digest(self):
        today = datetime.datetime.now(ZoneInfo("Europe/Moscow")).date()
        yesterday = (today - datetime.timedelta(days=1)).isoformat()
        rows = [
            {
                "key": "rop_digest_msg:old-date",
                "value": json.dumps({
                    "message_id": 78,
                    "sha256": "old",
                    "contract": "avito_calls_truth_v2",
                    "report_date": yesterday,
                    "text": (
                        f"📞 Разбор звонков — {yesterday}\n"
                        "Всего звонков по Avito: 1\n"
                        "Разобрано разговоров: 0"
                    ),
                }),
            },
            {
                "key": "rop_digest_msg:old-contract",
                "value": json.dumps({
                    "message_id": 81,
                    "sha256": "old2",
                    "contract": "legacy_v1",
                    "report_date": today.isoformat(),
                    "text": (
                        f"📞 Разбор звонков — {today.isoformat()}\n"
                        "Всего звонков по Avito: 0\n"
                        "Разобрано разговоров: 0"
                    ),
                }),
            },
        ]
        conf = report.ARTIFACTS["DEV_CALLS_REPORT_FIX"]
        with (
            patch.dict(conf, {"delivery_due_hour": 0, "delivery_due_minute": 0}),
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(report.db, "rows", return_value=rows),
        ):
            measured = report._measure("calls", None, 24)
        self.assertTrue(measured["measurable"], measured)
        self.assertEqual(measured["total"], 0)

    def test_calls_business_truth_rejects_answered_calls_waiting_for_analysis(self):
        today = datetime.datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
        state = {
            "message_id": 82,
            "sha256": "pending",
            "contract": "avito_calls_truth_v2",
            "report_date": today,
            "text": (
                f"📞 Разбор звонков — {today}\n"
                "Всего звонков по Avito: 2\n"
                "Отвечено: 1\n"
                "Разобрано разговоров: 0\n"
                "Есть отвеченные звонки без AI-разбора; BORIS должен обработать очередь анализа."
            ),
        }
        conf = report.ARTIFACTS["DEV_CALLS_REPORT_FIX"]
        with (
            patch.dict(conf, {"delivery_due_hour": 0, "delivery_due_minute": 0}),
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(
                report.db,
                "rows",
                return_value=[{"key": "rop_digest_msg:pending", "value": json.dumps(state)}],
            ),
        ):
            measured = report._measure("calls", None, 24)
        self.assertTrue(measured["measurable"], measured)
        self.assertEqual(measured["total"], 0)

    def test_calls_before_sla_falls_back_to_yesterday_only_when_today_missing(self):
        now = datetime.datetime.now(ZoneInfo("Europe/Moscow"))
        today = now.date().isoformat()
        yesterday = (now.date() - datetime.timedelta(days=1)).isoformat()
        yesterday_state = {
            "message_id": 81,
            "sha256": "yesterday",
            "contract": "avito_calls_truth_v2",
            "report_date": yesterday,
            "updated_at": f"{yesterday}T18:00:00+00:00",
            "text": (
                f"📞 Разбор звонков — {yesterday}\n"
                "Всего звонков по Avito: 0\n"
                "Разобрано разговоров: 0\n"
                "AI-разбор не выполняется: период РОП завершён."
            ),
        }
        conf = report.ARTIFACTS["DEV_CALLS_REPORT_FIX"]
        with (
            patch.dict(conf, {"delivery_due_hour": 23, "delivery_due_minute": 59}),
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(
                report.db,
                "rows",
                return_value=[{
                    "key": "rop_digest_msg:yesterday",
                    "value": json.dumps(yesterday_state),
                }],
            ),
        ):
            measured = report._measure("calls", None, 24)
        self.assertFalse(measured["due"], measured)
        self.assertEqual(measured["total"], 1)
        self.assertEqual(measured["accepted_report_date"], yesterday)
        self.assertNotEqual(measured["accepted_report_date"], today)

    def test_calls_before_sla_today_invalid_receipt_blocks_yesterday_fallback(self):
        now = datetime.datetime.now(ZoneInfo("Europe/Moscow"))
        today = now.date().isoformat()
        yesterday = (now.date() - datetime.timedelta(days=1)).isoformat()
        today_bad = {
            "message_id": 82,
            "sha256": "today-bad",
            "contract": "avito_calls_truth_v2",
            "report_date": today,
            "updated_at": f"{today}T10:00:00+00:00",
            "text": (
                f"📞 Разбор звонков — {today}\n"
                "Всего звонков по Avito: 2\n"
                "Отвечено: 1\n"
                "Разобрано разговоров: 0\n"
                "Есть отвеченные звонки без AI-разбора; BORIS должен обработать очередь анализа."
            ),
        }
        yesterday_good = {
            "message_id": 81,
            "sha256": "yesterday-good",
            "contract": "avito_calls_truth_v2",
            "report_date": yesterday,
            "updated_at": f"{yesterday}T18:00:00+00:00",
            "text": (
                f"📞 Разбор звонков — {yesterday}\n"
                "Всего звонков по Avito: 0\n"
                "Разобрано разговоров: 0\n"
                "AI-разбор не выполняется: период РОП завершён."
            ),
        }
        conf = report.ARTIFACTS["DEV_CALLS_REPORT_FIX"]
        with (
            patch.dict(conf, {"delivery_due_hour": 23, "delivery_due_minute": 59}),
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(
                report.db,
                "rows",
                return_value=[
                    {"key": "rop_digest_msg:today", "value": json.dumps(today_bad)},
                    {"key": "rop_digest_msg:yesterday", "value": json.dumps(yesterday_good)},
                ],
            ),
        ):
            measured = report._measure("calls", None, 24)
        self.assertFalse(measured["due"], measured)
        self.assertEqual(measured["accepted_report_date"], today)
        self.assertEqual(measured["total"], 0)
        self.assertIsNone(measured["delivery_message_id"])

    def test_delivery_before_sla_accepts_yesterday_two_of_two(self):
        now = datetime.datetime.now(ZoneInfo("Europe/Moscow"))
        yesterday = (now.date() - datetime.timedelta(days=1)).isoformat()
        rows = []
        for acc, mid in (
            ("prodazha_bytovok_25677", 79),
            ("3411770_94346", 80),
        ):
            rows.append({
                "account_id": acc,
                "value": json.dumps({
                    "automated_daily": {
                        "account_id": acc,
                        "report_date": yesterday,
                        "status": "delivered",
                        "telegram_message_id": mid,
                        "sent_at": f"{yesterday}T17:30:00+00:00",
                    }
                }),
            })
        conf = report.ARTIFACTS["CLIENT_BYTOVKI_REPORTS"]
        with (
            patch.dict(conf, {"delivery_due_hour": 23, "delivery_due_minute": 59}),
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(report.db, "rows", return_value=rows),
        ):
            measured = report._measure("delivery", conf["accounts"], 24)
        self.assertFalse(measured["due"], measured)
        self.assertEqual(measured["total"], 2)
        self.assertEqual(measured["accepted_report_date"], yesterday)

    def test_delivery_after_sla_rejects_yesterday_two_of_two(self):
        now = datetime.datetime.now(ZoneInfo("Europe/Moscow"))
        today = now.date().isoformat()
        yesterday = (now.date() - datetime.timedelta(days=1)).isoformat()
        rows = []
        for acc, mid in (
            ("prodazha_bytovok_25677", 79),
            ("3411770_94346", 80),
        ):
            rows.append({
                "account_id": acc,
                "value": json.dumps({
                    "automated_daily": {
                        "account_id": acc,
                        "report_date": yesterday,
                        "status": "delivered",
                        "telegram_message_id": mid,
                    }
                }),
            })
        conf = report.ARTIFACTS["CLIENT_BYTOVKI_REPORTS"]
        with (
            patch.dict(conf, {"delivery_due_hour": 0, "delivery_due_minute": 0}),
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(report.db, "rows", return_value=rows),
        ):
            measured = report._measure("delivery", conf["accounts"], 24)
        self.assertTrue(measured["due"], measured)
        self.assertEqual(measured["total"], 0)
        self.assertEqual(measured["accepted_report_date"], today)

    def test_delivery_after_sla_mixed_today_yesterday_counts_only_today(self):
        now = datetime.datetime.now(ZoneInfo("Europe/Moscow"))
        today = now.date().isoformat()
        yesterday = (now.date() - datetime.timedelta(days=1)).isoformat()
        rows = [
            {
                "account_id": "prodazha_bytovok_25677",
                "value": json.dumps({
                    "automated_daily": {
                        "account_id": "prodazha_bytovok_25677",
                        "report_date": today,
                        "status": "delivered",
                        "telegram_message_id": 90,
                    }
                }),
            },
            {
                "account_id": "3411770_94346",
                "value": json.dumps({
                    "automated_daily": {
                        "account_id": "3411770_94346",
                        "report_date": yesterday,
                        "status": "delivered",
                        "telegram_message_id": 80,
                    }
                }),
            },
        ]
        conf = report.ARTIFACTS["CLIENT_BYTOVKI_REPORTS"]
        with (
            patch.dict(conf, {"delivery_due_hour": 0, "delivery_due_minute": 0}),
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(report.db, "rows", return_value=rows),
        ):
            measured = report._measure("delivery", conf["accounts"], 24)
        self.assertTrue(measured["due"], measured)
        self.assertEqual(measured["total"], 1)
        self.assertEqual(measured["accepted_report_date"], today)
        self.assertEqual(measured["delivered_accounts"], ["prodazha_bytovok_25677"])

    def test_calls_after_sla_rejects_yesterday_receipt(self):
        now = datetime.datetime.now(ZoneInfo("Europe/Moscow"))
        today = now.date().isoformat()
        yesterday = (now.date() - datetime.timedelta(days=1)).isoformat()
        state = {
            "message_id": 81,
            "sha256": "yesterday",
            "contract": "avito_calls_truth_v2",
            "report_date": yesterday,
            "text": (
                f"📞 Разбор звонков — {yesterday}\n"
                "Всего звонков по Avito: 0\n"
                "Разобрано разговоров: 0\n"
                "AI-разбор не выполняется: период РОП завершён."
            ),
        }
        conf = report.ARTIFACTS["DEV_CALLS_REPORT_FIX"]
        with (
            patch.dict(conf, {"delivery_due_hour": 0, "delivery_due_minute": 0}),
            patch.object(report.db, "columns", return_value={"id": "bigint"}),
            patch.object(
                report.db,
                "rows",
                return_value=[{
                    "key": "rop_digest_msg:yesterday",
                    "value": json.dumps(state),
                }],
            ),
        ):
            measured = report._measure("calls", None, 24)
        self.assertTrue(measured["due"], measured)
        self.assertEqual(measured["accepted_report_date"], today)
        self.assertEqual(measured["total"], 0)
        self.assertIsNone(measured["delivery_message_id"])


if __name__ == "__main__":
    unittest.main()


class ClientPriorityDriftSelfHealTest(unittest.TestCase):
    def test_open_a2a_priority_is_repaired_even_when_parent_priority_is_already_correct(self):
        dev_row = {"id": 894, "title": "DEV_GUIDED_SCENARIOS — BORIS ведёт клиента за руку", "priority": 88}
        drift_row = {"id": 125, "priority": 60}
        with patch.object(clients.db, "rows", side_effect=[[dev_row], [drift_row]]) as rows, \
             patch.object(clients.db, "q") as q:
            out = clients.prioritize()

        self.assertEqual(out["changed"], [])
        self.assertEqual(out["order_priority_repairs"], [{
            "dev_job_id": "dev_894",
            "order_ids": [125],
            "from": [60],
            "now": 88,
        }])
        self.assertEqual(rows.call_count, 2)
        q.assert_called_once()
        sql = q.call_args.args[0]
        self.assertIn("UPDATE ext_a2a_orders", sql)
        self.assertEqual(q.call_args.kwargs["p"], 88)
        self.assertEqual(q.call_args.kwargs["i"], 894)
        self.assertIn(clients.a2a.BLOCKED_INFRA, q.call_args.kwargs["st"])
        self.assertIn(clients.a2a.BLOCKED_HUMAN, q.call_args.kwargs["st"])

    def test_no_priority_drift_causes_no_write(self):
        dev_row = {"id": 894, "title": "DEV_GUIDED_SCENARIOS — BORIS ведёт клиента за руку", "priority": 88}
        with patch.object(clients.db, "rows", side_effect=[[dev_row], []]), \
             patch.object(clients.db, "q") as q:
            out = clients.prioritize()
        self.assertEqual(out["changed"], [])
        self.assertEqual(out["order_priority_repairs"], [])
        q.assert_not_called()


class ReactivationSupervisorQueueTruthTest(unittest.TestCase):
    def test_old_candidates_are_not_stalled_when_ready_queue_exists(self):
        src = open(
            "/root/BORIS/backend/app/services/control_plane_adapters.py",
            encoding="utf-8",
        ).read()
        needle = (
            "str(st.get('mode') or 'recommend')=='auto' and "
            "int(a.get('stale_candidates_45d') or 0)>0 and "
            "int(a.get('ready_messages') or 0)==0"
        )
        self.assertIn(needle, src)
        self.assertIn('"classification":"REACTIVATION_AUTO_BACKLOG_STALLED"', src)


class ClientSupervisorCampaignAttentionContractTest(unittest.TestCase):
    def test_settled_partial_duplicate_campaign_is_not_attention(self):
        from app.services import client_supervisor
        settings={
            "feed_factory": {
                "owner_activation": {
                    "unresolved": 0,
                    "repair_plan": {
                        "state": "no_retry_required",
                        "retryable_count": 0,
                        "external_wait_count": 0,
                        "owner_action_required": False,
                    },
                },
                "post_publish_watch": {
                    "status": "observation_complete",
                    "owner_action_required": False,
                },
            }
        }
        self.assertFalse(
            client_supervisor._campaign_requires_attention("partial", json.dumps(settings))
        )

    def test_retryable_partial_campaign_stays_attention(self):
        from app.services import client_supervisor
        settings={
            "feed_factory": {
                "owner_activation": {
                    "unresolved": 1,
                    "repair_plan": {
                        "state": "repair_required",
                        "retryable_count": 1,
                        "external_wait_count": 0,
                        "owner_action_required": False,
                    },
                },
                "post_publish_watch": {"status": "observation_complete"},
            }
        }
        self.assertTrue(
            client_supervisor._campaign_requires_attention("partial", json.dumps(settings))
        )

    def test_partial_without_terminal_evidence_fails_closed(self):
        from app.services import client_supervisor
        self.assertTrue(client_supervisor._campaign_requires_attention("partial", "{}"))
        self.assertTrue(client_supervisor._campaign_requires_attention("partial", "not-json"))
        self.assertTrue(client_supervisor._campaign_requires_attention("submitted", "{}"))
        self.assertFalse(client_supervisor._campaign_requires_attention("published", "{}"))
