import os
import unittest
from unittest.mock import patch

import posting_runner as pr


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class VkEditCleanupContractTest(unittest.TestCase):
    def setUp(self):
        self.prev = os.environ.get("VK_TOKEN")
        os.environ["VK_TOKEN"] = "test-token"

    def tearDown(self):
        if self.prev is None:
            os.environ.pop("VK_TOKEN", None)
        else:
            os.environ["VK_TOKEN"] = self.prev

    def _current(self):
        return _Resp({
            "response": {
                "items": [{
                    "id": 7301,
                    "text": "old",
                    "attachments": [
                        {"type": "photo", "photo": {"owner_id": -1, "id": 11}},
                        {"type": "photo", "photo": {"owner_id": -1, "id": 12, "access_key": "abc"}},
                    ],
                }]
            }
        })

    def _edited(self):
        return _Resp({"response": 1})

    def _verified(self, photo_count=2):
        photos = [
            {"type": "photo", "photo": {"owner_id": -1, "id": 11 + i}}
            for i in range(photo_count)
        ]
        return _Resp({
            "response": {
                "items": [{
                    "id": 7301,
                    "text": "clean text",
                    "attachments": photos,
                }]
            }
        })

    def test_edit_vk_post_preserves_and_verifies_photos(self):
        calls = [self._current(), self._edited(), self._verified(2)]
        with patch("requests.get", side_effect=calls) as get:
            result = pr.edit_vk_post(-121265252, 7301, "clean text")

        self.assertTrue(result["ok"])
        self.assertTrue(result["text_ok"])
        self.assertTrue(result["attachments_ok"])
        self.assertEqual(result["attachments_expected"], 2)
        self.assertEqual(result["attachments_actual"], 2)

        edit_params = get.call_args_list[1].kwargs["params"]
        self.assertEqual(
            edit_params["attachments"],
            "photo-1_11,photo-1_12_abc",
        )

    def test_edit_vk_post_fails_closed_if_photo_is_lost(self):
        calls = [self._current(), self._edited(), self._verified(1)]
        with patch("requests.get", side_effect=calls):
            result = pr.edit_vk_post(-121265252, 7301, "clean text")

        self.assertFalse(result["ok"])
        self.assertTrue(result["text_ok"])
        self.assertFalse(result["attachments_ok"])
        self.assertEqual(result["attachments_expected"], 2)
        self.assertEqual(result["attachments_actual"], 1)

    def test_repost_uneditable_creates_verified_copy_before_delete(self):
        old_item = {
            "id": 7301,
            "text": "old with link",
            "likes": {"count": 0},
            "comments": {"count": 0},
            "reposts": {"count": 0},
            "attachments": [
                {"type": "photo", "photo": {"owner_id": -1, "id": 11}},
                {"type": "photo", "photo": {"owner_id": -1, "id": 12, "access_key": "abc"}},
            ],
        }
        new_item = {
            "id": 7400,
            "text": "clean text",
            "attachments": [
                {"type": "photo", "photo": {"owner_id": -1, "id": 11}},
                {"type": "photo", "photo": {"owner_id": -1, "id": 12, "access_key": "abc"}},
            ],
        }
        snapshots = [
            {"ok": True, "item": old_item},
            {"ok": True, "item": new_item},
            {"ok": False, "missing": True},
        ]
        transport = [_Resp({"response": {"post_id": 7400}}), _Resp({"response": 1})]
        with patch.object(pr, "_vk_wall_post_snapshot", side_effect=snapshots), \
             patch("requests.get", side_effect=transport) as get:
            result = pr.repost_vk_post_preserving_photos(-121265252, 7301, "clean text")

        self.assertTrue(result["ok"])
        self.assertEqual(result["old_post_id"], 7301)
        self.assertEqual(result["new_post_id"], 7400)
        self.assertEqual(result["attachments"], 2)
        self.assertIn("wall.post", get.call_args_list[0].args[0])
        self.assertEqual(
            get.call_args_list[0].kwargs["params"]["attachments"],
            "photo-1_11,photo-1_12_abc",
        )
        self.assertIn("wall.delete", get.call_args_list[1].args[0])

    def test_repost_uneditable_stops_when_engagement_exists(self):
        old_item = {
            "id": 7301,
            "text": "old",
            "likes": {"count": 1},
            "comments": {"count": 0},
            "reposts": {"count": 0},
            "attachments": [],
        }
        with patch.object(pr, "_vk_wall_post_snapshot", return_value={"ok": True, "item": old_item}), \
             patch("requests.get") as get:
            result = pr.repost_vk_post_preserving_photos(-121265252, 7301, "clean text")

        self.assertFalse(result["ok"])
        self.assertTrue(result["unsafe_engagement"])
        self.assertEqual(result["stage"], "engagement_guard")
        get.assert_not_called()

    def test_ocean_pending_priority_wins_over_other_projects(self):
        # Make the non-Ocean item deliberately older and with a larger post id;
        # the test must pass because priority is explicit, not by accidental date order.
        ocean = (0, "u2", "ocean", -121265252, 7301, "x", "Океан Клиентов и Борис", "2026-09-04")
        other = (1, "u2", "other", -239695377, 9999, "y", "От Души", "2026-08-01")
        ordered = sorted([other, ocean], key=pr._social_vk_pending_sort_key)
        self.assertEqual(ordered[0][6], "Океан Клиентов и Борис")
        import inspect
        src = inspect.getsource(pr.social_vk_pending_cleanup_once)
        self.assertIn("pending.sort(key=_social_vk_pending_sort_key)", src)
        self.assertNotIn("pending.sort(key=_social_vk_pending_sort_key, reverse=True)", src)

    def test_pending_cleanup_enforces_current_public_policy_last_mile(self):
        import inspect
        src = inspect.getsource(pr.social_vk_pending_cleanup_once)
        self.assertIn("render_public_text as _render_social_public_text", src)
        self.assertIn("VK_PENDING_LAST_MILE_POLICY", src)
        self.assertIn("PUBLIC_COPY_EMPTY_AFTER_SANITIZE", src)
        self.assertLess(src.index("_render_social_public_text"), src.index("result = edit_vk_post"))
        self.assertLess(src.index("result = edit_vk_post"), src.index("repost_vk_post_preserving_photos"))


if __name__ == "__main__":
    unittest.main()
