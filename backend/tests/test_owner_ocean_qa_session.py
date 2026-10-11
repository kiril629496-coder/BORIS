"""Explicit QA-event session propagation, with exact frozen-0ab comparisons.

The existing actual-function harness supplies only read-only fake DB and
transport boundaries. Its actual owner guard and all helper pins still run.
The original eighteen tests are imported as a module, not inherited/recounted.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

_support_spec = importlib.util.spec_from_file_location(
    __name__ + "._first_touch_support",
    Path(__file__).resolve().with_name("test_owner_ocean_first_touch.py"),
)
if _support_spec is None or _support_spec.loader is None:
    raise ImportError("Cannot load the exact sibling OWNER first-touch test support")
support = importlib.util.module_from_spec(_support_spec)
_support_spec.loader.exec_module(support)

BEFORE = Path(__file__).resolve().parent / "fixtures/ocean_funnel_before_event_qa_0ab.json"
BEFORE_API_SHA = "0ab800b078f7adce9275fbd5cdd47325e1b83599bf605bf98dd6dc8244068701"
BEFORE_OWNER_SHA = "202a79c3ec61b9244d58bf3420b4c5632cf997f5c22da0750517cbe21d3ad642"


class OwnerOceanQASessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.OwnerOceanFirstTouchTests.setUpClass()
        source = support.SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        helpers = [support.definition(source, tree, name) for name in support.HELPER_PINS]
        before = json.loads(BEFORE.read_text(encoding="utf-8"))
        if (before["source_sha256"] != BEFORE_API_SHA
                or before["definition_sha256"] != BEFORE_OWNER_SHA
                or hashlib.sha256(before["definition"].encode()).hexdigest() != BEFORE_OWNER_SHA):
            raise AssertionError("QA-session before fixture is not the exact frozen 0ab definition")
        prefix = "from __future__ import annotations\n\n" + "\n\n".join(helpers) + "\n\n"
        cls.before_code = compile(prefix + before["definition"], str(BEFORE), "exec")

    def setUp(self):
        self.flow = support.OwnerOceanFirstTouchTests()
        self.flow.setUp()
        # Only this local fixture instance selects the reviewed before source.
        # No application function, guard or support-module class is patched.
        self.flow.baseline_code = self.before_code

    def run_funnel(self, states, events, *, before=False):
        return self.flow.run_funnel(states, events, baseline=before)

    def test_01_explicit_qa_event_excludes_ordinary_session_unmarked_followup(self):
        session = "ordinary_test_session"
        events = [self.flow.event(session=session, qa=True, minute=-8),
                  self.flow.event(session=session, minute=-2)]
        self.assertFalse(session.startswith("qa_"))
        old = self.run_funnel({}, events, before=True)
        self.assertEqual(len(old["rows"]), 1)
        self.assertEqual(old["summary"]["visits"], 1)
        for ordered in (events, list(reversed(events))):
            self.flow.assert_empty(self.run_funnel({}, ordered))

    def test_02_qa_marker_without_ocean_campaign_still_excludes_linked_ocean_session(self):
        session = "ordinary_test_session"
        for campaign in (None, "", "another_campaign"):
            with self.subTest(marked_campaign=campaign):
                events = [self.flow.event(session=session, qa=True, utm_campaign=campaign, minute=-8),
                          self.flow.event(session=session, minute=-2)]
                self.assertEqual(self.run_funnel({}, events, before=True)["summary"]["visits"], 1)
                self.flow.assert_empty(self.run_funnel({}, events))
                self.flow.assert_empty(self.run_funnel({}, list(reversed(events))))

    def test_03_event_session_taint_preserves_real_account_first_touch_and_other_sessions(self):
        account = "commercial_fixture"
        first, qa_session, legitimate = "commercial_first_session", "ordinary_test_session", "unrelated_commercial_session"
        states = {account: self.flow.state(session=first)}
        events = [self.flow.event(session=qa_session, account=account, qa=True, utm_campaign=None, minute=-8),
                  self.flow.event("connected_audit", session=qa_session, account=account, minute=-2),
                  self.flow.event(session=first, account=account),
                  self.flow.event(session=legitimate, account=account)]
        old = self.run_funnel(states, events, before=True)
        self.assertEqual(len(old["rows"]), 3)
        self.assertEqual(old["summary"]["connected"], 2)
        for ordered in (events, list(reversed(events))):
            current = self.run_funnel(states, ordered)
            self.assertEqual({row["session_id"] for row in current["rows"]}, {first, legitimate})
            self.assertEqual(len(current["rows"]), 2)
            self.assertEqual(current["summary"]["visits"], 2)
            self.assertEqual(current["summary"]["registrations"], 1)
            self.assertEqual(current["summary"]["connected"], 1)
            for row in current["rows"]:
                self.assertEqual(row["account_id"], account)
                self.assertEqual((row["source"], row["post"]), ("Ocean TG", "post_first_tg"))

    def test_04_qa_event_without_session_does_not_taint_its_account(self):
        account = "commercial_fixture"
        first, legitimate = "commercial_first_session", "unrelated_commercial_session"
        states = {account: self.flow.state(session=first)}
        for missing_session in ("", "   "):
            with self.subTest(session=missing_session):
                events = [self.flow.event(session=missing_session, account=account, qa=True, utm_campaign=None),
                          self.flow.event(session=first, account=account),
                          self.flow.event(session=legitimate, account=account)]
                before = self.run_funnel(states, events, before=True)
                current = self.run_funnel(states, events)
                self.assertEqual(current, before)
                self.assertEqual({row["session_id"] for row in current["rows"]}, {first, legitimate})
                self.assertEqual(current["summary"]["registrations"], 1)

    def test_05_bot_and_local_flags_remain_event_local_and_do_not_taint_session(self):
        session = "ordinary_commercial_session"
        for flag in ("is_local", "is_bot"):
            with self.subTest(flag=flag):
                events = [self.flow.event(session=session, minute=-8, **{flag: True}),
                          self.flow.event(session=session, minute=-2)]
                before = self.run_funnel({}, events, before=True)
                current = self.run_funnel({}, events)
                self.assertEqual(current, before)
                self.assertEqual(len(current["rows"]), 1)
                self.assertEqual(current["summary"]["visits"], 1)


if __name__ == "__main__":
    print("HTTP exception boundary: " + support.HTTP_EXCEPTION_PROVIDER)
    unittest.main(verbosity=2)
