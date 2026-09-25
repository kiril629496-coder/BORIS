import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# -*- coding: utf-8 -*-
import unittest

from app.services import social_owner_policy_v3 as owner


class SocialVkQueuePriorityTest(unittest.TestCase):
    def test_newest_pending_wins_across_projects(self):
        older_ocean = (
            "2026-09-01 10:00", 7301, "u2", "ocean", -121265252,
            "text", "Океан Клиентов и Борис",
        )
        newer_dushi = (
            "2026-09-04 12:00", 93, "u2", "dushi", -239695377,
            "text", "От Души",
        )
        queue = [older_ocean, newer_dushi]
        queue.sort(key=owner._vk_pending_sort_key, reverse=True)
        self.assertEqual(queue[0][3], "dushi")
        self.assertEqual(queue[0][6], "От Души")

    def test_newest_post_wins_inside_same_project(self):
        older = (
            "2026-09-01 10:00", 7301, "u2", "older", -121265252,
            "text", "Океан Клиентов и Борис",
        )
        newer = (
            "2026-09-02 10:00", 7306, "u2", "newer", -121265252,
            "text", "Океан Клиентов и Борис",
        )
        queue = [older, newer]
        queue.sort(key=owner._vk_pending_sort_key, reverse=True)
        self.assertEqual(queue[0][3], "newer")
        self.assertEqual(queue[0][1], 7306)


if __name__ == "__main__":
    unittest.main(verbosity=2)
