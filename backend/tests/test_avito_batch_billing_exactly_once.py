import unittest
from unittest.mock import patch

from app.api import avito as A


class AvitoBatchBillingExactlyOnceTest(unittest.TestCase):
    def _drafts(self):
        return [{
            "id": "d1", "batch_label": "qa-batch", "title": "Ремонт фар",
            "description": "Проверка", "images": ["/images/original.jpg"],
        }]

    def _banner(self, **kw):
        key = kw.get("idempotency_key") or ""
        return {"status": "ok", "url": "/images/" + key.replace(":", "_") + ".png",
                "idempotency_replay": False}

    def test_unsettled_child_never_mutates_drafts(self):
        charges = []
        def consume(account, unit, amount, idempotency_key=""):
            charges.append((amount, idempotency_key))
            if len(charges) == 2:
                return {"allowed": False, "blocked_reason": "billing_busy"}
            return {"allowed": True}
        with patch.object(A, "_load_drafts", return_value=self._drafts()), \
             patch.object(A, "_mutate_drafts") as mutate_mock, \
             patch.object(A, "images_list", return_value={"folders": {}}), \
             patch("app.api.banners.get_banner_showcase", return_value={"showcase": []}), \
             patch("app.api.banners.avito_safe_banner_replay_available", return_value=False), \
             patch("app.api.banners.create_avito_safe_banner", side_effect=self._banner), \
             patch("app.api.billing.get_status", return_value={"unlimited": False, "usage": {"banners": {"remaining": 2}}}), \
             patch("app.api.billing.check_and_consume", side_effect=consume):
            out = A.apply_banner_to_batch_impl("qa", "qa-batch", banner_count=2,
                                               photos_per_ad=1, idempotency_key="intent-1")
        self.assertEqual(out["status"], "billing_pending")
        mutate_mock.assert_not_called()
        self.assertEqual(charges, [(1, "intent-1:billing:banner:0"),
                                   (1, "intent-1:billing:banner:1")])

    def test_all_children_settle_before_one_atomic_draft_save(self):
        charges = []
        def consume(account, unit, amount, idempotency_key=""):
            charges.append((amount, idempotency_key))
            return {"allowed": True}
        with patch.object(A, "_load_drafts", return_value=self._drafts()), \
             patch.object(A, "_mutate_drafts") as mutate_mock, \
             patch.object(A, "images_list", return_value={"folders": {}}), \
             patch("app.api.banners.get_banner_showcase", return_value={"showcase": []}), \
             patch("app.api.banners.avito_safe_banner_replay_available", return_value=False), \
             patch("app.api.banners.create_avito_safe_banner", side_effect=self._banner), \
             patch("app.api.billing.get_status", return_value={"unlimited": False, "usage": {"banners": {"remaining": 2}}}), \
             patch("app.api.billing.check_and_consume", side_effect=consume), \
             patch("app.services.action_log.log_action", return_value=None):
            out = A.apply_banner_to_batch_impl("qa", "qa-batch", banner_count=2,
                                               photos_per_ad=1, idempotency_key="intent-2")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["banners_new"], 2)
        mutate_mock.assert_called_once()
        self.assertEqual(charges, [(1, "intent-2:billing:banner:0"),
                                   (1, "intent-2:billing:banner:1")])

    def test_impl_rejects_missing_business_idempotency_key(self):
        with patch.object(A, "_load_drafts", return_value=self._drafts()):
            out = A.apply_banner_to_batch_impl("qa", "qa-batch", banner_count=1,
                                               photos_per_ad=1, idempotency_key="")
        self.assertEqual(out["code"], "paid_idempotency_required")


if __name__ == "__main__":
    unittest.main()
