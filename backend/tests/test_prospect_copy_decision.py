import unittest

from app.api.prospecting_scale import current_copy_decision


class ProspectCopyDecisionTests(unittest.TestCase):
    def test_collects_first_twenty_sends(self):
        d=current_copy_decision(
            {"version":"new","sent":0,"positive_replied":0,"opt_out":0},
            [{"version":"legacy","sent":37,"positive_replied":0,"opt_out":0}],
        )
        self.assertEqual(d["state"],"collecting")
        self.assertEqual(d["sample_remaining"],20)
        self.assertFalse(d["owner_action_required"])

    def test_promising_copy_is_kept(self):
        d=current_copy_decision(
            {"version":"new","sent":24,"positive_replied":2,"opt_out":1},
            [{"version":"legacy","sent":37,"positive_replied":0,"opt_out":0}],
        )
        self.assertEqual(d["state"],"promising")
        self.assertEqual(d["auto_action"],"keep_current")

    def test_zero_positive_after_thirty_requires_next_copy_not_owner_operator(self):
        d=current_copy_decision(
            {"version":"new","sent":30,"positive_replied":0,"opt_out":1},
            [{"version":"legacy","sent":37,"positive_replied":0,"opt_out":0}],
        )
        self.assertEqual(d["state"],"needs_revision")
        self.assertEqual(d["auto_action"],"keep_current_until_safe_replacement")
        self.assertFalse(d["owner_action_required"])


if __name__=="__main__":
    unittest.main()
