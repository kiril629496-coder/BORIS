"""Portable OVF04 tests of actual candidate functions with a read-only fake DB.

Only selected definitions are compiled from app/api/viral.py; application
modules, real authentication, databases, providers and HTTP are never imported
or contacted. The actual _require_owner source and role predicate execute.
FastAPI's HTTPException is used when installed. Without FastAPI, the explicitly
named transport-only exception fixture preserves status_code/detail/headers;
it does not replace the owner guard. Dependency resolution has a local marker.

The first three cases execute the exact pinned baseline owner definition with
the same actual unchanged helpers and the same data, proving before/after.
All paths are relative to this test's backend directory.
"""
from __future__ import annotations

import ast
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
import unittest

if importlib.util.find_spec("fastapi") is not None:
    from fastapi import HTTPException
    HTTP_EXCEPTION_PROVIDER = "fastapi.HTTPException"
else:
    class FixtureHTTPException(Exception):
        """Transport exception boundary only; not an authentication stub."""
        def __init__(self, status_code, detail=None, headers=None):
            super().__init__(detail)
            self.status_code, self.detail, self.headers = status_code, detail, headers

    HTTPException = FixtureHTTPException
    HTTP_EXCEPTION_PROVIDER = "local transport-only FixtureHTTPException"


BACKEND = Path(__file__).resolve().parents[1]
SOURCE = BACKEND / "app/api/viral.py"
BASELINE = BACKEND / "tests/fixtures/ocean_funnel_baseline_fd312.json"
BASE_API_SHA = "fd312de76700ab4bb41bcdff0418bd5bad35e0ffb51b405541d940fc436e5b37"
BASE_OWNER_SHA = "9b10a738815ee3d8123cb0724f04aed3f8e7a31101e9f64f0b77dad2dbb2859b"
HELPER_PINS = {
    "_now": "fb76a36a24bb3010597164061761e2afd8b01e1e2a0b9ff54887d8ba59b91e48",
    "_parse_datetime_utc": "b9c6db64d815a39ad34ef36cbe11a105a79e427f6948c1a40c96f74223d8e595",
    "_require_owner": "891772a941b9d58a3990718d8a9d838667962f50061ddfa3494912649dcfd0b8",
    "_attribution_source_label": "929914b4a010f555ed1eb42375cc3646884e36354ce3851651e08df4edc83ff7",
    "_safe_rate": "ccc944e1e41a773b135084dbbaa01904a16a5f3dce674af4d76431d28793f3c8",
}
COUNTERS = ("visits", "audits", "registrations", "results", "trial_cta", "connected")


def definition(source, tree, name):
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(matches) != 1:
        raise AssertionError("Expected exactly one actual definition: " + name)
    return ast.get_source_segment(source, matches[0])


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return lambda row: getattr(row, self.name, None) == value

    def in_(self, values):
        return lambda row: getattr(row, self.name, None) in values


class Storage:
    account_id, key = Column("account_id"), Column("key")


class User:
    account_id = Column("account_id")


class Account:
    account_id = Column("account_id")


class Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *predicates):
        return Query(row for row in self.rows if all(predicate(row) for predicate in predicates))

    def all(self):
        return list(self.rows)


class ReadOnlyFixtureDB:
    """Only query/close exist: a production write would fail this fixture."""
    def __init__(self, states, events):
        self.storage = [
            SimpleNamespace(account_id=account, key="viral_gateway_state", value=json.dumps(state))
            for account, state in states.items()
        ]
        self.storage += [
            SimpleNamespace(account_id="viral_event", key="event:fixture_" + str(index), value=json.dumps(event))
            for index, event in enumerate(events)
        ]
        account_ids = set(states) | {event["account_id"] for event in events if event.get("account_id")}
        self.users = [SimpleNamespace(account_id=a, email=a + "@fixture.invalid") for a in sorted(account_ids)]
        self.accounts = [SimpleNamespace(account_id=a, name="Fixture " + a) for a in sorted(account_ids)]
        self.closed = False
        self.initial = copy.deepcopy(self.snapshot())

    def snapshot(self):
        return [(row.account_id, row.key, row.value) for row in self.storage]

    def query(self, model):
        if self.closed:
            raise AssertionError("Query after close")
        return Query({Storage: self.storage, User: self.users, Account: self.accounts}[model])

    def close(self):
        self.closed = True


def namespace(code, factory):
    scope = {
        "Any": Any, "Optional": Optional, "json": json,
        "datetime": datetime, "timezone": timezone,
        "Depends": lambda dependency: SimpleNamespace(dependency=dependency),
        "get_current_user": object(),  # Dependency marker, never a callable auth replacement.
        "HTTPException": HTTPException, "SessionLocal": factory,
        "Storage": Storage, "User": User, "Account": Account,
    }
    exec(code, scope)
    return scope


class OwnerOceanFirstTouchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        helpers = []
        for name, expected in HELPER_PINS.items():
            actual = definition(source, tree, name)
            if hashlib.sha256(actual.encode()).hexdigest() != expected:
                raise AssertionError("Unchanged actual helper drifted: " + name)
            helpers.append(actual)
        before = json.loads(BASELINE.read_text(encoding="utf-8"))
        if (before["source_sha256"] != BASE_API_SHA
                or before["definition_sha256"] != BASE_OWNER_SHA
                or hashlib.sha256(before["definition"].encode()).hexdigest() != BASE_OWNER_SHA):
            raise AssertionError("Baseline owner definition is not the exact fd312 fixture")
        prefix = "from __future__ import annotations\n\n" + "\n\n".join(helpers) + "\n\n"
        cls.candidate_code = compile(prefix + definition(source, tree, "owner_ocean_funnel"), str(SOURCE), "exec")
        cls.baseline_code = compile(prefix + before["definition"], str(BASELINE), "exec")

    def setUp(self):
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.owner = SimpleNamespace(role="owner")

    def stamp(self, minutes, offset=0):
        return (self.now + timedelta(minutes=minutes)).astimezone(
            timezone(timedelta(hours=offset))).isoformat()

    def touch(self, *, source="tg", post="post_first_tg", session="session_ocean_fixture", qa=False,
              campaign="ocean", **updates):
        return {
            "utm_source": source, "utm_medium": "social", "utm_campaign": campaign,
            "utm_content": post, "session_id": session, "qa": qa,
            "first_seen_at": self.stamp(-12), "captured_at": self.stamp(-11), **updates,
        }

    def state(self, **touch_options):
        return {
            "first_touch": self.touch(**touch_options), "registered_at": self.stamp(-10),
            "stage": "CONNECTED_AUDIT_COMPLETED", "audit_completed": True,
            "trial_interest": True, "history": [{"mode": "connected"}],
        }

    def event(self, name="audit_view", *, source="tg", post="post_first_tg", minute=-8,
              account=None, session="session_ocean_fixture", event_id="event_fixture", **updates):
        return {
            "event": name, "session_id": session, "account_id": account, "event_id": event_id,
            "utm_source": source, "utm_medium": "social", "utm_campaign": "ocean",
            "utm_content": post, "referral_code": "",
            "first_seen_at": self.stamp(minute), "created_at": self.stamp(minute),
            "qa": False, "is_local": False, "is_bot": False, **updates,
        }

    def run_funnel(self, states, events, *, baseline=False):
        db = ReadOnlyFixtureDB(states, events)
        calls = []

        def factory():
            calls.append(True)
            return db

        scope = namespace(self.baseline_code if baseline else self.candidate_code, factory)
        output = scope["owner_ocean_funnel"](user=self.owner)
        self.assertEqual(calls, [True])
        self.assertTrue(db.closed)
        self.assertEqual(db.snapshot(), db.initial, "The read-only funnel mutated stored fixtures")
        return output

    def only_row(self, output):
        self.assertEqual(len(output["rows"]), 1, output["rows"])
        return output["rows"][0]

    def assert_attribution(self, output, source, post, stamp):
        row = self.only_row(output)
        self.assertEqual((row["source"], row["post"], row["date"]), (source, post, stamp))
        return row

    def assert_empty(self, output):
        self.assertEqual(output["rows"], [])
        self.assertEqual(output["posts"], [])
        self.assertEqual({key: output["summary"][key] for key in COUNTERS}, dict.fromkeys(COUNTERS, 0))

    def test_01_before_after_durable_touch_survives_same_session_anon_merge(self):
        account = "account_commercial_fixture"
        states = {account: self.state()}
        events = [
            self.event("audit_view", minute=-9),
            self.event("connected_audit", source="vk", post="post_later_vk", account=account, minute=-2),
        ]
        old = self.run_funnel(states, events, baseline=True)
        self.assert_attribution(old, "Ocean VK", "post_later_vk", self.stamp(-12))
        self.assertEqual(old["by_source"]["vk"]["registrations"], 1)
        self.assert_attribution(self.run_funnel(states, list(reversed(events)), baseline=True),
                                "Ocean TG", "post_first_tg", self.stamp(-12))
        for ordered in (events, list(reversed(events))):
            output = self.run_funnel(states, ordered)
            row = self.assert_attribution(output, "Ocean TG", "post_first_tg", self.stamp(-12))
            self.assertEqual(row["account_id"], account)
            self.assertEqual({key: output["summary"][key] for key in COUNTERS}, dict.fromkeys(COUNTERS, 1))
            self.assertEqual(output["by_source"]["tg"]["registrations"], 1)
            self.assertEqual(output["by_source"]["vk"]["registrations"], 0)

    def test_02_before_after_anonymous_first_touch_ignores_database_order(self):
        events = [
            self.event("audit_view", minute=-9),
            self.event("audit_start", source="vk", post="post_later_vk", minute=-2),
        ]
        self.assert_attribution(self.run_funnel({}, events, baseline=True),
                                "Ocean VK", "post_later_vk", self.stamp(-9))
        self.assert_attribution(self.run_funnel({}, list(reversed(events)), baseline=True),
                                "Ocean TG", "post_first_tg", self.stamp(-9))
        left = self.run_funnel({}, events)
        right = self.run_funnel({}, list(reversed(events)))
        self.assertEqual(left, right)
        self.assert_attribution(left, "Ocean TG", "post_first_tg", self.stamp(-9))
        self.assertEqual((left["summary"]["visits"], left["summary"]["audits"]), (1, 1))

    def test_03_before_after_qa_state_excludes_linked_unmarked_events(self):
        account, session = "account_qa_fixture", "session_explicit_qa_fixture"
        states = {account: self.state(qa=True, session=session, post="qa_post")}
        events = [
            self.event("audit_view", session=session, post="qa_post", minute=-9),
            self.event("connected_audit", session=session, post="qa_post", account=account, minute=-2),
        ]
        self.assert_empty(self.run_funnel(states, [], baseline=True))
        old = self.run_funnel(states, events, baseline=True)
        self.assertEqual(len(old["rows"]), 1)
        self.assertEqual((old["summary"]["visits"], old["summary"]["connected"]), (1, 1))
        self.assert_empty(self.run_funnel(states, events))
        self.assert_empty(self.run_funnel(states, list(reversed(events))))

    def test_04_durable_source_post_and_date_ignore_later_direct_or_earlier_event_fields(self):
        account = "durable_account"
        events = [
            self.event("audit_view", account=account, source="direct", post="", minute=-30),
            self.event("trial_cta_click", account=account, source="", post="post_later_empty_source", minute=-1),
            self.event("audit_start", account=account, source="vk", post="post_later_vk"),
        ]
        for ordered in (events, list(reversed(events))):
            self.assert_attribution(self.run_funnel({account: self.state()}, ordered),
                                    "Ocean TG", "post_first_tg", self.stamp(-12))

    def test_05_durable_timestamp_uses_first_valid_captured_or_registered_utc(self):
        account = "durable_timestamp_account"
        for captured, expected in ((self.stamp(-11, offset=3), self.stamp(-11)), ("invalid", self.stamp(-10))):
            with self.subTest(captured=captured):
                state = self.state(first_seen_at="invalid", captured_at=captured)
                self.assert_attribution(self.run_funnel({account: state}, []),
                                        "Ocean TG", "post_first_tg", expected)

    def test_06_anonymous_timestamp_order_normalizes_utc_offsets(self):
        early = self.event("audit_view", first_seen_at=self.stamp(-9, offset=3),
                           created_at=self.stamp(-8, offset=3))
        late = self.event("audit_start", source="vk", post="late_vk", minute=-2)
        for ordered in ([early, late], [late, early]):
            self.assert_attribution(self.run_funnel({}, ordered), "Ocean TG", "post_first_tg", self.stamp(-9))

    def test_07_anonymous_invalid_first_seen_falls_back_to_valid_server_time(self):
        early = self.event("audit_view", first_seen_at="invalid", created_at=self.stamp(-9, offset=-4))
        late = self.event("audit_start", source="vk", post="late_vk", minute=-2)
        for ordered in ([early, late], [late, early]):
            self.assert_attribution(self.run_funnel({}, ordered), "Ocean TG", "post_first_tg", self.stamp(-9))

    def test_08_equal_utc_time_ties_use_server_time_event_id_then_touch_content(self):
        shared = {"first_seen_at": self.stamp(-9), "created_at": self.stamp(-8), "event_id": "same"}
        cases = [
            (
                self.event(post="alpha", **{**shared, "created_at": self.stamp(-8)}),
                self.event(post="zeta", **{**shared, "created_at": self.stamp(-7), "event_id": "a"}),
            ),
            (
                self.event(post="alpha", **{**shared, "event_id": "a"}),
                self.event(post="zeta", **{**shared, "event_id": "z"}),
            ),
            (
                self.event(post="alpha", **{**shared, "first_seen_at": self.stamp(-9, offset=3),
                                          "created_at": self.stamp(-8, offset=3)}),
                self.event(post="zeta", **shared),
            ),
        ]
        for index, pair in enumerate(cases):
            with self.subTest(rank_component=index):
                left = self.run_funnel({}, pair)
                right = self.run_funnel({}, list(reversed(pair)))
                self.assertEqual(left, right)
                self.assert_attribution(left, "Ocean TG", "alpha", self.stamp(-9))

    def test_09_unattributed_events_without_any_valid_timestamp_are_excluded(self):
        for first_seen, created in (("invalid", "invalid"), ("", ""), (None, None)):
            with self.subTest(first_seen=first_seen):
                event = self.event(first_seen_at=first_seen, created_at=created)
                self.assert_empty(self.run_funnel({}, [event]))

    def test_10_durable_attribution_survives_missing_dates_and_untimed_milestones(self):
        state = self.state(first_seen_at="invalid", captured_at=None)
        state["registered_at"] = "invalid"
        events = [self.event("audit_view", first_seen_at=None, created_at="invalid")]
        output = self.run_funnel({"account_untimed_durable": state}, events)
        self.assert_attribution(output, "Ocean TG", "post_first_tg", None)
        self.assertEqual((output["summary"]["visits"], output["summary"]["registrations"]), (1, 1))

    def test_11_qa_prefix_first_touch_excludes_account_even_without_qa_flag(self):
        account, session = "account_prefix_fixture", "QA_ocean_fixture"
        states = {account: self.state(session=session, qa=False)}
        events = [
            self.event(session=session),
            self.event("connected_audit", session="session_other_on_qa_account", account=account),
        ]
        self.assert_empty(self.run_funnel(states, events))
        self.assert_empty(self.run_funnel({}, [self.event(session="qa_anonymous_fixture", qa=False)]))

    def test_12_qa_session_does_not_quarantine_commercial_account_or_other_sessions(self):
        account = "account_commercial_fixture"
        states = {account: self.state()}
        events = [
            self.event("audit_view"),
            self.event("audit_view", session="session_legitimate_second", account=account),
            self.event("connected_audit", session="qa_acceptance_session", account=account),
            self.event("audit_view", session="qa_acceptance_session", account=None),
        ]
        for ordered in (events, list(reversed(events))):
            output = self.run_funnel(states, ordered)
            self.assertEqual({row["session_id"] for row in output["rows"]},
                             {"session_ocean_fixture", "session_legitimate_second"})
            self.assertEqual({row["account_id"] for row in output["rows"]}, {account})
            self.assertEqual((output["summary"]["visits"], output["summary"]["registrations"]), (2, 1))

    def test_13_acceptance_names_are_not_a_qa_signal(self):
        account, session = "acceptance-commercial-account", "acceptance-commercial-session"
        output = self.run_funnel(
            {account: self.state(session=session, post="acceptance-commercial-post")},
            [self.event(session=session, account=account, post="acceptance-commercial-post")],
        )
        self.assert_attribution(output, "Ocean TG", "acceptance-commercial-post", self.stamp(-12))
        self.assertEqual((output["summary"]["visits"], output["summary"]["registrations"]), (1, 1))

    def test_14_existing_other_campaign_or_direct_touch_cannot_be_reassigned_to_ocean(self):
        for campaign, source in (("other_campaign", "email"), ("", "direct")):
            with self.subTest(campaign=campaign):
                account = "account_previously_acquired"
                states = {account: self.state(campaign=campaign, source=source)}
                events = [self.event(), self.event(session="session_later_ocean", account=account)]
                self.assert_empty(self.run_funnel(states, events))
                self.assert_empty(self.run_funnel(states, list(reversed(events))))

    def test_15_same_session_anonymous_and_authenticated_events_merge_before_attribution(self):
        account = "account_event_link_fixture"
        events = [
            self.event("audit_view", minute=-9, event_id="early_anon"),
            self.event("registration_completed", account=account, source="vk", post="later_vk", minute=-2,
                       event_id="later_authenticated"),
        ]
        for ordered in (events, list(reversed(events))):
            output = self.run_funnel({}, ordered)
            row = self.assert_attribution(output, "Ocean TG", "post_first_tg", self.stamp(-9))
            self.assertEqual(row["account_id"], account)
            self.assertEqual(row["user"], account + "@fixture.invalid")
            self.assertEqual((output["summary"]["visits"], output["summary"]["registrations"]), (1, 1))

    def test_16_existing_event_local_qa_localhost_and_bot_exclusions_remain(self):
        for flag in ("qa", "is_local", "is_bot"):
            with self.subTest(flag=flag):
                excluded = self.event(session="session_excluded_" + flag, **{flag: True})
                legitimate = self.event(session="session_legitimate")
                output = self.run_funnel({}, [excluded, legitimate])
                self.assertEqual(self.only_row(output)["session_id"], "session_legitimate")
                self.assertEqual(output["summary"]["visits"], 1)

    def test_17_response_contract_and_rate_denominator_29_30_are_unchanged(self):
        for count in (29, 30):
            with self.subTest(count=count):
                states = {"account_rate_" + str(i): self.state(session="session_rate_" + str(i)) for i in range(count)}
                events = [self.event(session="session_rate_" + str(i)) for i in range(count)]
                output = self.run_funnel(states, events)
                self.assertEqual(set(output), {"status", "minimum_rate_denominator", "summary", "by_source",
                                               "posts", "rows", "alerts"})
                self.assertEqual((output["status"], output["minimum_rate_denominator"]), ("ok", 30))
                self.assertEqual(set(output["summary"]), {*COUNTERS, "rates"})
                self.assertEqual(set(output["by_source"]), {"tg", "vk"})
                self.assertEqual(set(output["rows"][0]), {
                    "session_id", "account_id", "source", "campaign", "post", "date", "visit", "audit",
                    "registration", "result", "trial", "connected", "user", "company",
                })
                self.assertEqual(set(output["posts"][0]), {"source", "post", *COUNTERS, "rates"})
                self.assertEqual({key: output["summary"][key] for key in COUNTERS}, dict.fromkeys(COUNTERS, count))
                self.assertEqual(set(output["summary"]["rates"].values()), {None if count == 29 else 100.0})
                self.assertEqual(output["alerts"], [])

    def test_18_actual_owner_guard_rejects_nonowner_before_database_access(self):
        calls = []

        def forbidden_database():
            calls.append(True)
            raise AssertionError("Non-owner reached the database")

        scope = namespace(self.candidate_code, forbidden_database)
        for user in (SimpleNamespace(role="member"), SimpleNamespace(role="admin"), SimpleNamespace(), None):
            with self.subTest(user=user):
                with self.assertRaises(HTTPException) as caught:
                    scope["owner_ocean_funnel"](user=user)
                self.assertEqual(caught.exception.status_code, 403)
                self.assertEqual(caught.exception.detail, "Только владелец BORIS")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    print("HTTP exception boundary: " + HTTP_EXCEPTION_PROVIDER)
    unittest.main(verbosity=2)
