import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
FRONTEND=ROOT/"frontend"
BACKEND=ROOT/"backend"


class AdminSurfaceNoOverlapTests(unittest.TestCase):
    def read(self, rel):
        return (FRONTEND/rel).read_text(encoding="utf-8")

    def test_clients_uses_client_memory_without_global_runtime_materialize(self):
        src=self.read("app/dashboard/clients-control/page.tsx")
        self.assertIn("thread_type=client&sync_runtime=false&refresh_executor=false",src)
        self.assertIn("/api/control-plane/workspace/client-supervisor",src)
        self.assertIn("/api/control-plane/action-plan?account_id=",src)
        self.assertIn("/dashboard/control-plane",src)

    def test_development_uses_server_projection_and_second_defense_filter(self):
        src=self.read("app/dashboard/development/page.tsx")
        self.assertIn("projection=development",src)
        self.assertNotIn("/workspace/materialize",src)
        self.assertIn("isDevelopmentThread",src)
        self.assertIn('if(t.thread_type==="client")return false;',src)
        self.assertIn('if(surface==="control-plane"||source==="system-brain")return false;',src)

    def test_brain_has_global_prompt_but_not_programming_journal_copy(self):
        src=self.read("app/dashboard/control-plane/page.tsx")
        self.assertIn("system-brain:owner-global",src)
        self.assertIn("Команда Центральному Мозгу",src)
        self.assertIn("Дать команду Мозгу",src)
        self.assertNotIn("Журнал программирования",src)
        self.assertNotIn("workspace/programming-journal",src)

    def test_three_owner_surfaces_share_one_prompt_executor_contract(self):
        for rel in (
            "app/dashboard/control-plane/page.tsx",
            "app/dashboard/clients-control/page.tsx",
            "app/dashboard/development/page.tsx",
        ):
            src=self.read(rel)
            self.assertIn("/api/control-plane/workspace/threads/",src,rel)
            self.assertIn("/prompt",src,rel)
        backend=(BACKEND/"app/api/control_plane.py").read_text(encoding="utf-8")
        self.assertIn('@router.post("/workspace/threads/{thread_id}/prompt")',backend)
        self.assertIn("capture_owner_instruction(",backend)
        self.assertIn("_workspace_enqueue_executor(row",backend)

    def test_command_center_is_summary_not_second_client_controller(self):
        src=self.read("app/dashboard/command-center/page.tsx")
        self.assertIn("/api/control-plane/workspace/client-supervisor?account_id=",src)
        self.assertIn("/dashboard/clients-control",src)
        self.assertNotIn("/workspace/threads/",src)
        self.assertNotIn("/prompt",src)

    def test_journal_is_alias_of_development_not_fourth_surface(self):
        src=self.read("app/journal/page.tsx").strip()
        self.assertEqual(src,'export {default} from "../dashboard/development/page";')


if __name__=="__main__":
    unittest.main()
