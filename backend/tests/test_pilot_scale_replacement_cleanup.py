import inspect
import unittest

from app.services import campaign_post_publish_watch as watch


class PilotScaleReplacementCleanupTests(unittest.TestCase):
    def test_cleanup_is_provider_proven_and_fail_closed(self):
        src = inspect.getsource(watch._pilot_scale_replacement_cleanup)
        self.assertIn("PILOT_SCALE_OLD_CAMPAIGN_CLEANUP_V1", src)
        self.assertIn("pilot_scale_backlink_mismatch", src)
        self.assertIn("REPLACEMENT_ACCOUNT_ADDITIVE_SCOPE_V2", src)
        self.assertIn("canonical_feed_new_scope_missing", src)
        self.assertIn("unrelated_preserved", src)
        self.assertIn("_autoload_upload_snapshot", src)
        self.assertIn("new_scope_not_fully_active", src)
        self.assertIn("_mutate_feed_items", src)
        self.assertIn("waiting_newer_avito_upload", src)
        self.assertIn("old_scope_still_present", src)
        self.assertIn("old.status = \"superseded\"", src)

    def test_feed_prune_happens_after_new_scope_success_check(self):
        src = inspect.getsource(watch._pilot_scale_replacement_cleanup)
        proof = src.index("if not bool(new_snap.get(\"full_success\"))")
        prune = src.index("_mutate_feed_items(account_id, _prune)")
        self.assertLess(proof, prune)

    def test_completion_requires_newer_upload_and_exact_old_zero(self):
        src = inspect.getsource(watch._pilot_scale_replacement_cleanup)
        self.assertIn("current_upload <= baseline", src)
        self.assertIn("PILOT_SCALE_OLD_CORE_INVENTORY_ZERO_V1", src)
        self.assertIn("DATEEND_STUCK_OLD_AUTOLOAD_STAGING_V1", src)
        self.assertIn("_mutate_feed_items(account_id, _stage_dateend)", src)
        self.assertIn("DATEEND_STAGING_SCOPE_DRIFT", src)
        self.assertIn("dateend_staging_verify_failed", src)
        self.assertLess(src.index("DATEEND_STUCK_OLD_AUTOLOAD_STAGING_V1"), src.index('old.status = "superseded"'))
        self.assertIn("OLD_SCOPE_EXACT_NOT_ACCOUNT_TOTAL_V1", src)
        self.assertIn("old_core_active", src)
        self.assertIn("old_found > 0 or old_active > 0", src)
        self.assertNotIn("account_total != expected_new", src)

    def test_dateend_residue_cleanup_is_ownerless_and_fail_closed(self):
        src = inspect.getsource(watch._pilot_scale_replacement_cleanup)
        self.assertIn("DATEEND_STUCK_OLD_AUTOLOAD_CLEANUP_V1", src)
        self.assertIn("waiting_dateend_cleanup_upload", src)
        self.assertIn("dateend_cleanup_old_core_still_active", src)
        self.assertIn("dateend_cleanup_new_scope_not_fully_active", src)
        self.assertIn("DATEEND_CLEANUP_SCOPE_DRIFT", src)
        self.assertIn("past_DateEnd_then_core_zero_then_prune", src)
        self.assertIn("DATEEND_REMOVE_PREVIOUS_DAY_V1", src)
        self.assertIn("timedelta(days=1)", src)
        self.assertIn("dateend_cleanup_restaged_previous_day", src)
        self.assertIn("DATEEND_RESTAGE_SCOPE_DRIFT", src)
        self.assertIn("previous_day_DateEnd_then_core_zero_then_prune", src)
        self.assertLess(src.index("dateend_cleanup_old_core_still_active"),
                        src.index("_mutate_feed_items(account_id, _dateend_prune)"))

    def test_completed_cleanup_is_reverified_and_can_rearm(self):
        src = inspect.getsource(watch._pilot_scale_replacement_cleanup)
        self.assertIn("COMPLETED_CLEANUP_CORE_REVERIFY_V1", src)
        self.assertIn("COMPLETED_SCOPE_ADDITIVE_INVENTORY_V2", src)
        self.assertIn("completed_old_scope_staged_dateend", src)
        self.assertIn("completed_scope_DateEnd_preserve_unrelated", src)
        self.assertNotIn('if str(cleanup.get("status") or "") == "completed":\n        return {"status": "completed", "changed": False}', src)

    def test_under_kpi_does_not_interrupt_inflight_dateend_cleanup(self):
        src = inspect.getsource(watch._pilot_scale_replacement_cleanup)
        self.assertIn("UNDER_KPI_PRESERVE_INVENTORY_V2", src)
        self.assertIn("replacement_cleanup_inflight_not_growth_inventory", src)
        self.assertIn('"under_kpi_restore_staged", "waiting_under_kpi_restore_upload"', src)
        self.assertIn("UNDER_KPI_COMPLETED_RESTORE_V3", src)
        self.assertIn("under_kpi_restore_old_inventory_staged", src)
        self.assertIn("under_kpi_preserve_inventory_active", src)
        self.assertIn("UNDER_KPI_RESTORE_PROVIDER_FUNDS_BLOCK_V1", src)
        self.assertIn("waiting_owner_funds", src)
        self.assertIn("2214", src)
        self.assertIn("Пополнить аванс/баланс Avito", src)
        resume = src.index("replacement_cleanup_inflight_not_growth_inventory")
        pause = src.index("and _cleanup_status != \"completed\"")
        dateend = src.index("DATEEND_STUCK_OLD_AUTOLOAD_CLEANUP_V1")
        self.assertLess(resume, pause)
        self.assertLess(pause, dateend)

    def test_budget_release_requires_fresh_spend_before_dateend_release(self):
        src = inspect.getsource(watch._pilot_scale_replacement_cleanup)
        self.assertIn("REPLACEMENT_RELEASE_FRESH_SPEND_GATE_V1", src)
        self.assertIn("money_spend_signal_eligible", src)
        self.assertIn("latest_confirmed_spend", src)
        self.assertIn("waiting_fresh_spend_after_budget_clear", src)
        self.assertIn("wait_fresh_spend_before_budget_hold_release", src)
        self.assertIn("max_age_seconds=900", src)
        spend_gate = src.index("REPLACEMENT_RELEASE_FRESH_SPEND_GATE_V1")
        kpi_gate = src.index("REPLACEMENT_RELEASE_FRESH_KPI_GATE_V1")
        dateend_release = src.index("REPLACEMENT_BUDGET_DATEEND_RELEASE_V1")
        self.assertLess(spend_gate, kpi_gate)
        self.assertLess(kpi_gate, dateend_release)

    def test_run_once_reloads_cleanup_state_and_uses_marketing_day(self):
        src = inspect.getsource(watch.run_once)
        self.assertIn("REPLACEMENT_CLEANUP_RELOAD_AFTER_MUTATION_V1", src)
        self.assertIn("db.refresh(campaign)", src)
        self.assertIn('{"armed", "rearmed", "waiting", "completed", "blocked"}', src)
        self.assertIn("marketing_today_iso()", src)
        self.assertNotIn("date.today().isoformat()", src)
        self.assertIn("REPLACEMENT_CLEANUP_OWNER_ACTION_TRUTH_V1", src)
        self.assertIn('_obs_cleanup["owner_action_required"] = bool(', src)
        self.assertNotIn('_obs_cleanup["owner_action_required"] = False', src)

    def test_replacement_child_counts_as_new_scope(self):
        src = inspect.getsource(watch._pilot_scale_replacement_cleanup)
        self.assertIn("REPLACEMENT_CHILD_SCOPE_V1", src)
        self.assertIn("replacement_campaign_id", src)
        self.assertIn("replacement_rows", src)
        self.assertIn("PILOT_SCALE_OLD_CORE_INVENTORY_ZERO_V1", src)
        self.assertIn('"status": "active"', src)
        self.assertIn("old_core_inventory_still_active", src)
        self.assertIn("old_avito_ids", src)
        self.assertIn("OLD_PROVIDER_IDENTITY_SCOPE_V1", src)
        self.assertIn("old_provider_identity_scope_mismatch", src)
        self.assertIn("CampaignItem.avito_item_id.isnot(None)", src)
        run_src = inspect.getsource(watch.run_once)
        self.assertIn("REPLACEMENT_CLEANUP_DURABLE_OBSERVATION_V1", run_src)
        self.assertIn('["last_observation"]', run_src)
        self.assertLess(src.index("old_core_inventory_still_active"), src.index("if old_found > 0 or old_active > 0"))
        self.assertLess(src.index("old_core_inventory_still_active"), src.index('old.status = "superseded"'))


if __name__ == "__main__":
    unittest.main()
