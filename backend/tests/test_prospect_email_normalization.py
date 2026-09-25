import unittest
from unittest.mock import patch

from app.services.prospecting import extract_contacts, normalize_email
from app.services.prospect_campaigns import (
    _canonicalize_member_email_before_send,
    deliverability_decision,
)


class ProspectEmailNormalizationTests(unittest.TestCase):
    def test_percent_encoded_mailto_is_canonicalized_before_scoring(self):
        self.assertEqual(
            normalize_email("mailto:%20Office@Example.RU?subject=Hello"),
            "office@example.ru",
        )
        html = "<html><body><a href=\"mailto:%20office@quarry-invest.ru\">email</a></body></html>"
        emails, _ = extract_contacts("https://quarry-invest.ru/contacts", html)
        self.assertIn("office@quarry-invest.ru", emails)
        self.assertNotIn("%20office@quarry-invest.ru", emails)

    def test_zero_width_and_nbsp_are_removed(self):
        self.assertEqual(
            normalize_email(" sales\u200b@\u00a0example.ru "),
            "sales@example.ru",
        )

    def test_final_send_gate_normalizes_historical_member_before_queue(self):
        class Result:
            def scalar(self):
                return False

        class FakeDb:
            def __init__(self):
                self.calls = []
                self.committed = False
                self.closed = False

            def execute(self, statement, params=None):
                self.calls.append((str(statement), dict(params or {})))
                return Result()

            def commit(self):
                self.committed = True

            def close(self):
                self.closed = True

        fake = FakeDb()
        with patch("app.services.prospect_campaigns.SessionLocal", return_value=fake):
            row, allowed, reason = _canonicalize_member_email_before_send(
                {
                    "id": 77,
                    "email": "%20Office@Example.RU",
                    "email_domain": "example.ru",
                }
            )
        self.assertTrue(allowed)
        self.assertEqual(reason, "normalized_email_before_send")
        self.assertEqual(row["email"], "office@example.ru")
        self.assertTrue(fake.committed)
        self.assertTrue(fake.closed)
        self.assertTrue(
            any(call[1].get("e") == "office@example.ru" for call in fake.calls)
        )

    def test_final_send_gate_allows_valid_public_mailbox_domain(self):
        with patch("app.services.prospect_campaigns.SessionLocal", side_effect=AssertionError("should not open DB on canonical fast path")):
            row, allowed, reason = _canonicalize_member_email_before_send(
                {
                    "id": 800,
                    "email": "sales@yandex.ru",
                    "email_domain": "yandex.ru",
                }
            )
        self.assertTrue(allowed)
        self.assertIsNone(reason)
        self.assertEqual(row["email"], "sales@yandex.ru")

    def test_final_send_gate_blocks_bad_domain_even_when_already_canonical(self):
        class Result:
            def scalar(self):
                return False

        class FakeDb:
            def __init__(self):
                self.calls = []
                self.committed = False
                self.closed = False

            def execute(self, statement, params=None):
                self.calls.append((str(statement), dict(params or {})))
                return Result()

            def commit(self):
                self.committed = True

            def close(self):
                self.closed = True

        fake = FakeDb()
        with patch("app.services.prospect_campaigns.SessionLocal", return_value=fake):
            row, allowed, reason = _canonicalize_member_email_before_send(
                {
                    "id": 801,
                    "email": "info@rutube.ru",
                    "email_domain": "rutube.ru",
                }
            )
        self.assertFalse(allowed)
        self.assertEqual(reason, "blocked_domain_before_send")
        self.assertEqual(row["email_domain"], "rutube.ru")
        self.assertTrue(fake.committed)
        self.assertTrue(fake.closed)
        self.assertTrue(any("blocked_domain_before_send" in call[0] for call in fake.calls))

    def test_final_send_gate_blocks_invalid_address(self):
        class Result:
            def scalar(self):
                return False

        class FakeDb:
            def __init__(self):
                self.calls = []

            def execute(self, statement, params=None):
                self.calls.append((str(statement), dict(params or {})))
                return Result()

            def commit(self):
                pass

            def close(self):
                pass

        fake = FakeDb()
        with patch("app.services.prospect_campaigns.SessionLocal", return_value=fake):
            _, allowed, reason = _canonicalize_member_email_before_send(
                {"id": 78, "email": "not-an-email", "email_domain": ""}
            )
        self.assertFalse(allowed)
        self.assertEqual(reason, "invalid_email_before_send")
        self.assertTrue(any("invalid_email_before_send" in call[0] for call in fake.calls))
    def test_final_send_gate_blocks_canonical_duplicate(self):
        class Result:
            def __init__(self, value=None):
                self.value = value

            def scalar(self):
                return self.value

        class FakeDb:
            def __init__(self):
                self.calls = []
                self.n = 0

            def execute(self, statement, params=None):
                self.n += 1
                self.calls.append((str(statement), dict(params or {})))
                if self.n == 1:
                    return Result(999)
                return Result(False)

            def commit(self):
                pass

            def close(self):
                pass

        fake = FakeDb()
        with patch("app.services.prospect_campaigns.SessionLocal", return_value=fake):
            _, allowed, reason = _canonicalize_member_email_before_send(
                {
                    "id": 79,
                    "campaign_id": 9,
                    "email": "%20dup@example.ru",
                    "email_domain": "example.ru",
                }
            )
        self.assertFalse(allowed)
        self.assertEqual(reason, "duplicate_email_before_send")
        self.assertTrue(any("duplicate_email_before_send" in call[0] for call in fake.calls))

    def test_deliverability_guard_collects_then_throttles_then_blocks(self):
        collecting = deliverability_decision(10, 3)
        self.assertEqual(collecting["state"], "ok")
        self.assertEqual(collecting["reason"], "collecting")

        safe = deliverability_decision(40, 1)
        self.assertEqual(safe["state"], "ok")
        self.assertEqual(safe["hard_failure_rate_pct"], 2.5)

        throttled = deliverability_decision(30, 4)
        self.assertEqual(throttled["state"], "throttled")
        self.assertEqual(throttled["max_enqueue_override"], 2)
        self.assertFalse(throttled["owner_action_required"])

        blocked = deliverability_decision(40, 8)
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["max_enqueue_override"], 0)
        self.assertTrue(blocked["owner_action_required"])


if __name__ == "__main__":
    unittest.main()
