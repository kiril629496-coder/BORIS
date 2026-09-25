import importlib.util
import inspect
import unittest
from pathlib import Path


def _load_posting_runner():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("posting_runner_slot_test", root / "posting_runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SocialSlotSafetyTests(unittest.TestCase):
    def test_scheduled_slot_plan_is_persisted_before_paid_generation(self):
        pr = _load_posting_runner()
        src = inspect.getsource(pr._autopost_projects_impl)
        plan = inspect.getsource(pr._scheduled_generation_plan)
        self.assertIn("_scheduled_generation_plan", src)
        self.assertIn("_posting_generation_exists", src)
        self.assertIn("SLOT_ALREADY_MATERIALIZED", src)
        self.assertIn("pg_advisory_xact_lock", plan)
        self.assertIn("posting_generation_plan:", plan)

    def test_all_scheduled_materialization_records_carry_generation_key(self):
        pr = _load_posting_runner()
        src = inspect.getsource(pr._autopost_projects_impl)
        self.assertGreaterEqual(src.count("generation_key=_generation_key"), 3)
        self.assertIn('"generation_key": str(generation_key or "")', inspect.getsource(pr._save_post_record))


if __name__ == "__main__":
    unittest.main()
