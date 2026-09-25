import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

BASE=Path("/var/lib/boris-social-audit")

def _load_audit():
    sha=(BASE/"audit.sha").read_text().strip()
    path=BASE/f"audit.{sha}.py"
    spec=importlib.util.spec_from_file_location("social_published_at_audit_current",path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

class SocialPublishedAtAuditIdentityTest(unittest.TestCase):
    def test_exact_generation_key_wins_over_multiple_project_logs(self):
        mod=_load_audit()
        target_key="social-slot:u2:f938caa3a944:20260905_09"
        other_key="social-slot:u2:f938caa3a944:20260905_18"
        rows=[
          {
            "__REALTIME_TIMESTAMP":"1788598800000000",
            "MESSAGE":"[due] опубликован НА ВСЕХ площадках: От Души | ждём 3 часа | record_id= abc111 | generation_key= "+target_key,
          },
          {
            "__REALTIME_TIMESTAMP":"1788631200000000",
            "MESSAGE":"[due] опубликован НА ВСЕХ площадках: От Души | ждём 3 часа | record_id= def222 | generation_key= "+other_key,
          },
        ]
        mod.run=lambda *a,**k: SimpleNamespace(
            returncode=0,
            stdout="\n".join(json.dumps(x,ensure_ascii=False) for x in rows),
        )
        got=mod.journal_candidates({
            "project_name":"От Души",
            "created_at":"2026-09-05 06:00",
            "id":"abc111",
            "generation_key":target_key,
        })
        self.assertEqual(len(got),1)
        self.assertIn(target_key,got[0][1])
        self.assertNotIn(other_key,got[0][1])


    def test_external_evidence_uses_later_tg_vk_timestamp(self):
        mod=_load_audit()

        class Resp:
            def __init__(self,status_code=200,text="",payload=None):
                self.status_code=status_code
                self.text=text
                self._payload=payload if payload is not None else {}
            def json(self):
                return self._payload

        def fake_get(url,*args,**kwargs):
            if url.startswith("https://t.me/s/"):
                return Resp(
                    200,
                    '<div data-post="ot_dyshi_2000/114">'
                    '<time datetime="2026-09-04T11:34:17+00:00"></time></div>',
                )
            if "wall.getById" in url:
                return Resp(
                    200,
                    payload={
                        "response":{
                            "items":[{"date":1788521665}]
                        }
                    },
                )
            raise AssertionError(url)

        original=mod.requests.get
        original_token=mod.os.environ.get("VK_TOKEN")
        try:
            mod.requests.get=fake_get
            mod.os.environ["VK_TOKEN"]="qa-token"
            got=mod.external_delivery_evidence({
                "ids":{
                    "tg":{"chat_id":"@ot_dyshi_2000","message_id":114},
                    "vk":{"owner_id":-239695377,"post_id":101},
                }
            })
        finally:
            mod.requests.get=original
            if original_token is None:
                mod.os.environ.pop("VK_TOKEN",None)
            else:
                mod.os.environ["VK_TOKEN"]=original_token

        self.assertTrue(got["ok"],got)
        self.assertEqual(
            got["published_at"].isoformat(),
            "2026-09-04T11:34:25+00:00",
        )
        self.assertEqual(
            got["evidence"]["telegram_at"],
            "2026-09-04T11:34:17+00:00",
        )
        self.assertEqual(
            got["evidence"]["vk_at"],
            "2026-09-04T11:34:25+00:00",
        )

    def test_external_evidence_never_guesses_when_one_platform_missing(self):
        mod=_load_audit()

        class Resp:
            def __init__(self,status_code=200,text="",payload=None):
                self.status_code=status_code
                self.text=text
                self._payload=payload if payload is not None else {}
            def json(self):
                return self._payload

        def fake_get(url,*args,**kwargs):
            if url.startswith("https://t.me/s/"):
                return Resp(
                    200,
                    '<div data-post="ot_dyshi_2000/114">'
                    '<time datetime="2026-09-04T11:34:17+00:00"></time></div>',
                )
            if "wall.getById" in url:
                return Resp(
                    200,
                    payload={
                        "error":{"error_code":6,"error_msg":"Too many requests"}
                    },
                )
            raise AssertionError(url)

        original=mod.requests.get
        original_token=mod.os.environ.get("VK_TOKEN")
        try:
            mod.requests.get=fake_get
            mod.os.environ["VK_TOKEN"]="qa-token"
            got=mod.external_delivery_evidence({
                "ids":{
                    "tg":{"chat_id":"@ot_dyshi_2000","message_id":114},
                    "vk":{"owner_id":-239695377,"post_id":101},
                }
            })
        finally:
            mod.requests.get=original
            if original_token is None:
                mod.os.environ.pop("VK_TOKEN",None)
            else:
                mod.os.environ["VK_TOKEN"]=original_token

        self.assertFalse(got["ok"],got)
        self.assertEqual(got["reason"],"external_evidence_incomplete")
        self.assertEqual(got["required"],["telegram","vk"])
        self.assertEqual(got["present"],["telegram"])

    def test_audit_service_loads_env_and_checks_current_sha(self):
        unit=Path("/etc/systemd/system/boris-social-published-at-audit.service").read_text()
        sha=(BASE/"audit.sha").read_text().strip()
        self.assertIn("EnvironmentFile=/root/BORIS/backend/.env",unit)
        self.assertIn("Environment=BORIS_RUNTIME_ROLE=social_published_at_audit",unit)
        self.assertIn(sha,unit)
        self.assertIn("/usr/bin/sha256sum -c -",unit)

    def test_worker_success_log_contains_lifecycle_identity(self):
        text=Path("posting_runner.py").read_text(encoding="utf-8")
        self.assertIn('"| record_id=", rec.get("id")',text)
        self.assertIn('"| generation_key=", rec.get("generation_key")',text)

    def test_guardian_explicitly_monitors_published_at_audit(self):
        unit=Path("/etc/systemd/system/boris-social-guardian.service").read_text()
        sha=unit.split("/objects/",1)[1].split()[0].strip()
        text=Path("/var/lib/boris-integrity/objects") .joinpath(sha).read_text()
        self.assertIn('"worker_id":"social_published_at_audit"',text)
        self.assertIn('"service":"boris-social-published-at-audit.service"',text)
        self.assertIn('"max_age":360',text)

    def test_generic_reliability_has_audit_sla(self):
        text=Path("app/services/reliability.py").read_text(encoding="utf-8")
        self.assertIn('("runtime", "social_published_at_audit"): 360',text)

if __name__=="__main__":
    unittest.main()
