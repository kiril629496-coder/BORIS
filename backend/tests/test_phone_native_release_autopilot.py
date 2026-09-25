# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import telephony_guardian_runner as G


class PhoneNativeReleaseAutopilotTests(unittest.TestCase):
    def test_no_external_signing_inputs_create_no_job(self):
        with patch.object(G, "_release_candidates", return_value=[]):
            out = G.native_release_autopilot_once()
        self.assertEqual(out["status"], "waiting_external_inputs")
        self.assertEqual(out["created"], [])
        self.assertEqual(out["existing"], [])

    def test_windows_signing_input_requires_local_file(self):
        with patch.dict(G.os.environ, {}, clear=True):
            self.assertFalse(G._windows_signing_input_ready())
        with tempfile.TemporaryDirectory() as td:
            cert = Path(td) / "signing.pfx"
            cert.write_bytes(b"qa")
            with patch.dict(G.os.environ, {"CSC_LINK": str(cert)}, clear=True):
                self.assertTrue(G._windows_signing_input_ready())
            with patch.dict(G.os.environ, {"CSC_LINK": str(cert) + ".missing"}, clear=True):
                self.assertFalse(G._windows_signing_input_ready())

    def test_ready_generation_enqueues_exactly_one_scoped_job(self):
        candidate = {
            "platform": "android",
            "root": Path("/qa/android"),
            "paths": [
                "../clients/boris-phone/android",
                "../clients/boris-phone/scripts/artifact-manifest.py",
                "../clients/boris-phone/native-release-evidence.json",
            ],
            "goal": "signed release",
            "acceptance": ["signed", "manifest", "device qa truth"],
        }
        created_job = {"dev_job_id": "dev_222"}
        with patch.object(G, "_release_candidates", return_value=[candidate]),              patch.object(G, "_latest_source_generation", return_value="abcdef12345678"),              patch("app.ext_api.db.one", return_value=None) as one,              patch("app.ext_api.dev.create", return_value=created_job) as create,              patch("app.ext_api.dev.schedule", return_value={"status": "ok"}) as schedule:
            out = G.native_release_autopilot_once()
        self.assertEqual(out["status"], "queued")
        self.assertEqual(out["created"], ["dev_222"])
        self.assertEqual(out["ready_platforms"], ["android"])
        one.assert_called_once()
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["scope"]["workstream_id"], "telephony_core")
        self.assertEqual(kwargs["scope"]["paths"], candidate["paths"])
        self.assertEqual(kwargs["locks"], candidate["paths"])
        schedule.assert_called_once()

    def test_existing_generation_is_idempotent_and_not_rescheduled(self):
        candidate = {
            "platform": "windows",
            "root": Path("/qa/desktop"),
            "paths": ["../clients/boris-phone/desktop"],
            "goal": "signed release",
            "acceptance": ["signed"],
        }
        with patch.object(G, "_release_candidates", return_value=[candidate]),              patch.object(G, "_latest_source_generation", return_value="1234567890abcd"),              patch("app.ext_api.db.one", return_value={"id": 333, "status": "waiting"}),              patch("app.ext_api.dev.create") as create,              patch("app.ext_api.dev.schedule") as schedule:
            out = G.native_release_autopilot_once()
        self.assertEqual(out["status"], "already_queued")
        self.assertEqual(out["existing"], ["dev_333"])
        create.assert_not_called()
        schedule.assert_not_called()

    def test_one_android_device_enqueues_dependent_qa_after_release(self):
        candidate = {
            "platform": "android",
            "root": Path("/qa/android"),
            "paths": [
                "../clients/boris-phone/android",
                "../clients/boris-phone/scripts/artifact-manifest.py",
                "../clients/boris-phone/native-release-evidence.json",
            ],
            "goal": "signed release",
            "acceptance": ["signed"],
        }
        with patch.object(G, "_release_candidates", return_value=[candidate]), \
             patch.object(G, "_android_connected_device_state", return_value={
                 "status": "one_connected", "count": 1, "device_fingerprint": "a1b2c3d4"
             }), \
             patch.object(G, "_latest_source_generation", return_value="abcdef12345678"), \
             patch("app.ext_api.db.one", side_effect=[None, None]), \
             patch("app.ext_api.dev.create", side_effect=[
                 {"dev_job_id": "dev_222"}, {"dev_job_id": "dev_223"}
             ]) as create, \
             patch("app.ext_api.dev.schedule", return_value={"status": "ok"}) as schedule:
            out = G.native_release_autopilot_once()
        self.assertEqual(out["created"], ["dev_222"])
        self.assertEqual(out["device_qa_created"], ["dev_223"])
        self.assertEqual(create.call_count, 2)
        qa_call = create.call_args_list[1]
        self.assertEqual(qa_call.kwargs["scope"]["workstream_id"], "telephony_core")
        self.assertEqual(qa_call.kwargs["depends_on"], ["dev_222"])
        self.assertEqual(qa_call.kwargs["locks"], candidate["paths"])
        self.assertNotIn("serial", repr(out).lower())
        schedule.assert_called_once()

    def test_multiple_android_devices_do_not_enqueue_device_qa(self):
        candidate = {
            "platform": "android",
            "root": Path("/qa/android"),
            "paths": ["../clients/boris-phone/android"],
            "goal": "signed release",
            "acceptance": ["signed"],
        }
        with patch.object(G, "_release_candidates", return_value=[candidate]), \
             patch.object(G, "_android_connected_device_state", return_value={
                 "status": "multiple_connected", "count": 2
             }), \
             patch.object(G, "_latest_source_generation", return_value="abcdef12345678"), \
             patch("app.ext_api.db.one", return_value=None), \
             patch("app.ext_api.dev.create", return_value={"dev_job_id": "dev_222"}) as create, \
             patch("app.ext_api.dev.schedule", return_value={"status": "ok"}):
            out = G.native_release_autopilot_once()
        self.assertEqual(create.call_count, 1)
        self.assertEqual(out["device_qa_created"], [])
        self.assertEqual(out["device_state"]["status"], "multiple_connected")

    def test_android_device_detector_does_not_expose_serial(self):
        fake = type("CP", (), {
            "returncode": 0,
            "stdout": "List of devices attached\nSERIAL-SECRET-123\tdevice\n",
            "stderr": "",
        })()
        with patch.object(G.shutil, "which", return_value="/qa/adb"), \
             patch.object(G.subprocess, "run", return_value=fake):
            out = G._android_connected_device_state()
        self.assertEqual(out["status"], "one_connected")
        self.assertEqual(out["count"], 1)
        self.assertNotIn("SERIAL-SECRET-123", repr(out))
        self.assertRegex(out["device_fingerprint"], r"^[0-9a-f]{8}$")


if __name__ == "__main__":
    unittest.main()
