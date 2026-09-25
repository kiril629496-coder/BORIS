import pathlib
import unittest


class BackgroundRuntimeGenerationScopeTests(unittest.TestCase):
    def test_worker_direct_import_roots_are_generation_tracked(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / 'production_contract_runner.py').read_text(encoding='utf-8')
        required = (
            "BACKEND_ACTIVE_ROOT/'app/telegram_bot.py'",
            "BACKEND_ACTIVE_ROOT/'app/api/messenger.py'",
            "BACKEND_ACTIVE_ROOT/'app/mop_core.py'",
            "BACKEND_ACTIVE_ROOT/'app/api/client_memory.py'",
            "BACKEND_ACTIVE_ROOT/'app/crm/bridge.py'",
            "BACKEND_ACTIVE_ROOT/'app/api/mass_editor.py'",
            "BACKEND_ACTIVE_ROOT/'app/api/cpx_advisor.py'",
            "BACKEND_ACTIVE_ROOT/'app/services/reliability.py'",
            "BACKEND_ACTIVE_ROOT/'app/services/sales_ai_router.py'",
            "BACKEND_ACTIVE_ROOT/'app/db/session.py'",
            "BACKEND_ACTIVE_ROOT/'app/db/base.py'",
            "BACKEND_ACTIVE_ROOT/'app/main.py'",
        )
        for item in required:
            self.assertIn(item, src)


if __name__ == '__main__':
    unittest.main()
