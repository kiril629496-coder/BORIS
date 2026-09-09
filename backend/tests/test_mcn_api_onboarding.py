# -*- coding: utf-8 -*-
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import text

from app.api import mcn_phone as API
from app.db.session import SessionLocal
from app.services import mcn_core
from app.services import telephony_core as T


class MCNApiOnboardingTests(unittest.TestCase):
    def setUp(self):
        mcn_core.ensure_schema()
        T.ensure_schema()
        self.account="__qa_mcn_api_"+uuid.uuid4().hex[:16]
        self._phone_entitlement_patch=patch(
            "app.services.telephony_core.phone_entitlement_status",
            return_value={"status":"active","active":True},
        )
        self._phone_entitlement_patch.start()

    def tearDown(self):
        db=SessionLocal()
        try:
            for table in (
                "telephony_dids",
                "telephony_trunks",
                "telephony_provider_configs",
            ):
                db.execute(text(f"DELETE FROM {table} WHERE account_id=:a"),{"a":self.account})
            db.commit()
        finally:
            db.close()
        self._phone_entitlement_patch.stop()

    def test_unpaid_phone_blocks_atomic_mcn_onboard_before_storage(self):
        payload={
            "account_id":"real-unpaid",
            "trunk":{"provider":"mcn","username":"u","password":"p"},
            "did":{"provider":"mcn","number":"+74951234567"},
        }
        with patch(
            "app.services.telephony_core.phone_entitlement_status",
            return_value={"status":"not_entitled","active":False},
        ), patch.object(API,"onboarding_upsert") as upsert:
            with self.assertRaises(HTTPException) as ctx:
                API.onboard(payload,current_user=None)
        self.assertEqual(ctx.exception.status_code,409)
        self.assertEqual(ctx.exception.detail["code"],"phone_entitlement_required")
        upsert.assert_not_called()

    def test_unpaid_phone_blocks_mcn_trunk_before_secret_storage(self):
        with patch(
            "app.services.telephony_core.phone_entitlement_status",
            return_value={"status":"expired","active":False},
        ), patch.object(API,"trunk_upsert") as upsert:
            with self.assertRaises(HTTPException) as ctx:
                API.save_trunk({
                    "account_id":"real-expired",
                    "provider":"mcn",
                    "username":"u",
                    "password":"p",
                },current_user=None)
        self.assertEqual(ctx.exception.status_code,409)
        self.assertEqual(ctx.exception.detail["code"],"phone_entitlement_required")
        upsert.assert_not_called()

    def test_unpaid_phone_blocks_mcn_did_before_storage_or_guardian(self):
        with patch(
            "app.services.telephony_core.phone_entitlement_status",
            return_value={"status":"not_entitled","active":False},
        ), patch.object(API,"did_upsert") as upsert:
            with self.assertRaises(HTTPException) as ctx:
                API.save_did({
                    "account_id":"real-unpaid",
                    "provider":"mcn",
                    "number":"+74951234567",
                },current_user=None)
        self.assertEqual(ctx.exception.status_code,409)
        self.assertEqual(ctx.exception.detail["code"],"phone_entitlement_required")
        upsert.assert_not_called()

    def _save_trunk(self):
        with patch("app.services.asterisk_gateway.mcn_pjsip_guardian", return_value={
            "status":"ok","trunks_verified":0,"trunks_degraded":1,
            "owner_action_required":False,
        }), patch("app.services.telephony_core.verify_provider_connection", return_value={
            "status":"configured_unverified","connected":False,
        }):
            return API.save_trunk({
                "account_id":self.account,
                "provider":"mcn",
                "name":"MCN QA",
                "auth_mode":"registration",
                "registrar":"sip.mcn.example:5060",
                "username":"qa-login",
                "password":"qa-secret-password",
                "source_ips":[],
                "codecs":["alaw","ulaw"],
                "enabled":True,
            }, current_user=None)

    def test_one_mcn_trunk_save_selects_provider_automatically(self):
        out=self._save_trunk()
        self.assertEqual(out["status"],"ok")
        self.assertEqual(out["provider_config"]["provider"],"mcn")
        self.assertEqual(out["pjsip_apply"],"automatic_guardian")

        db=SessionLocal()
        try:
            row=db.execute(text("""SELECT provider,status,(credentials_enc IS NOT NULL AND credentials_enc<>'') has_credentials
                                   FROM telephony_provider_configs WHERE account_id=:a"""),
                           {"a":self.account}).mappings().one()
        finally:
            db.close()
        self.assertEqual(row["provider"],"mcn")
        self.assertTrue(row["has_credentials"])

    def test_default_mcn_registrar_is_applied_when_owner_omits_it(self):
        out=mcn_core.trunk_upsert(self.account,{
            "provider":"mcn",
            "name":"MCN default registrar QA",
            "auth_mode":"registration",
            "username":"qa-login",
            "password":"qa-secret-password",
            "source_ips":[],
            "enabled":True,
        })
        self.assertEqual(out["status"],"ok")
        self.assertEqual(out["trunk"]["registrar"],"sip.mcn.ru")
        self.assertEqual(out["trunk"]["status"],"configured_unverified")

    def test_default_mcn_media_profile_is_alaw_rfc4733(self):
        out=mcn_core.trunk_upsert(self.account,{
            "provider":"mcn",
            "name":"MCN default media QA",
            "auth_mode":"registration",
            "username":"qa-login",
            "password":"qa-secret-password",
            "enabled":True,
        })
        self.assertEqual(out["status"],"ok")
        self.assertEqual(out["trunk"]["codecs"],["alaw"])
        self.assertEqual(out["trunk"]["dtmf_mode"],"rfc4733")

    def test_mcn_media_payload_is_normalized_to_safe_profile(self):
        out=mcn_core.trunk_upsert(self.account,{
            "provider":"mcn",
            "name":"MCN media normalize QA",
            "auth_mode":"registration",
            "username":"qa-login",
            "password":"qa-secret-password",
            "codecs":["ulaw","g729"],
            "dtmf_mode":"inband",
            "enabled":True,
        })
        self.assertEqual(out["status"],"ok")
        self.assertEqual(out["trunk"]["codecs"],["alaw"])
        self.assertEqual(out["trunk"]["dtmf_mode"],"rfc4733")

    def test_partial_repeat_save_preserves_existing_mcn_transport_fields(self):
        first=mcn_core.trunk_upsert(self.account,{
            "provider":"mcn",
            "name":"MCN patch QA",
            "auth_mode":"registration",
            "registrar":"sip2.mcn.ru:5070",
            "outbound_proxy":"sip3.mcn.ru:5071",
            "username":"qa-login",
            "password":"qa-old-password",
            "source_ips":["85.94.32.92/32"],
            "codecs":["alaw","ulaw"],
            "dtmf_mode":"rfc4733",
            "max_channels":7,
            "max_cps":"3",
            "allowed_cli":["+74951234567"],
            "priority":321,
            "enabled":True,
            "metadata":{"qa":"keep"},
        })
        self.assertEqual(first["status"],"ok")
        second=mcn_core.trunk_upsert(self.account,{
            "provider":"mcn",
            "name":"MCN patch QA",
            "password":"qa-new-password",
        })
        self.assertEqual(second["status"],"ok")
        tr=second["trunk"]
        self.assertEqual(tr["auth_mode"],"registration")
        self.assertEqual(tr["registrar"],"sip2.mcn.ru:5070")
        self.assertEqual(tr["outbound_proxy"],"sip3.mcn.ru:5071")
        self.assertEqual(tr["source_ips"],["85.94.32.92/32"])
        self.assertEqual(tr["codecs"],["alaw"])
        self.assertEqual(tr["dtmf_mode"],"rfc4733")
        self.assertEqual(tr["max_channels"],7)
        self.assertEqual(float(tr["max_cps"]),3.0)
        self.assertEqual(tr["allowed_cli"],["+74951234567"])
        self.assertEqual(tr["priority"],321)
        self.assertTrue(tr["enabled"])
        self.assertEqual(tr["status"],"configured_unverified")
        secret=mcn_core.trunk_credentials(self.account,int(tr["id"]))
        self.assertEqual(secret["username"],"qa-login")
        self.assertEqual(secret["password"],"qa-new-password")

    def test_owner_api_never_returns_sip_password(self):
        self._save_trunk()
        listed=API.trunks(self.account,current_user=None)
        body=repr(listed)
        self.assertNotIn("qa-secret-password",body)
        self.assertNotIn("qa-login",body)
        self.assertTrue(listed["items"])
        self.assertTrue(listed["items"][0].get("has_username"))
        self.assertTrue(listed["items"][0].get("has_password"))

    def test_partial_did_update_preserves_trunk_and_number_policy(self):
        tr=mcn_core.trunk_upsert(self.account,{
            "provider":"mcn",
            "name":"MCN DID patch QA",
            "auth_mode":"registration",
            "username":"qa-login",
            "password":"qa-password",
        })
        self.assertEqual(tr["status"],"ok")
        tid=int(tr["trunk"]["id"])
        first=mcn_core.did_upsert(self.account,{
            "provider":"mcn",
            "number":"+74951234568",
            "region":"Москва",
            "number_type":"local",
            "trunk_id":tid,
            "purpose":"campaign",
            "inbound_enabled":True,
            "outbound_cli_enabled":True,
            "monthly_cost":"450.50",
            "connection_cost":"100.00",
            "status":"active",
            "metadata":{"qa":"keep"},
        })
        self.assertEqual(first["status"],"ok")
        second=mcn_core.did_upsert(self.account,{
            "provider":"mcn",
            "number":"+74951234568",
            "region":"Москва и МО",
        })
        self.assertEqual(second["status"],"ok")
        did=second["did"]
        self.assertEqual(did["region"],"Москва и МО")
        self.assertEqual(did["number_type"],"local")
        self.assertEqual(did["trunk_id"],tid)
        self.assertEqual(did["purpose"],"campaign")
        self.assertTrue(did["inbound_enabled"])
        self.assertTrue(did["outbound_cli_enabled"])
        self.assertEqual(float(did["monthly_cost"]),450.5)
        self.assertEqual(float(did["connection_cost"]),100.0)
        self.assertEqual(did["status"],"active")
        self.assertEqual(did["metadata_json"],{"qa":"keep"})

        explicit=mcn_core.did_upsert(self.account,{
            "provider":"mcn",
            "number":"+74951234568",
            "outbound_cli_enabled":False,
        })
        self.assertEqual(explicit["status"],"ok")
        self.assertFalse(explicit["did"]["outbound_cli_enabled"])
        self.assertEqual(explicit["did"]["trunk_id"],tid)

    def test_did_is_bound_to_saved_trunk_without_second_provider_setup(self):
        tr=self._save_trunk()
        tid=int(tr["trunk"]["id"])
        did=API.save_did({
            "account_id":self.account,
            "provider":"mcn",
            "number":"+74951234567",
            "region":"Москва",
            "trunk_id":tid,
            "purpose":"main",
            "status":"active",
            "inbound_enabled":True,
            "outbound_cli_enabled":True,
        },current_user=None)
        self.assertEqual(did["status"],"ok")
        rows=API.dids(self.account,current_user=None)["items"]
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["trunk_id"],tid)
        self.assertEqual(rows[0]["number_e164"],"+74951234567")

    def test_saving_last_did_autofinalizes_without_second_owner_action(self):
        tr=self._save_trunk()
        tid=int(tr["trunk"]["id"])
        with patch("app.services.asterisk_gateway.mcn_pjsip_guardian",return_value={
            "status":"ok","owner_action_required":False
        }) as pjsip, patch("app.services.telephony_core.verify_provider_connection",return_value={
            "status":"ok","connected":True
        }) as verify, patch("app.api.mcn_phone.mcn_readiness",return_value={
            "ready":True,"steps_completed":3,"steps_total":3
        }) as readiness:
            out=API.save_did({
                "account_id":self.account,
                "provider":"mcn",
                "number":"+74951234570",
                "trunk_id":tid,
                "purpose":"main",
                "status":"active",
                "inbound_enabled":True,
                "outbound_cli_enabled":True,
            },current_user=None)
        self.assertEqual(out["status"],"ok")
        self.assertTrue(out["autofinalize"])
        self.assertEqual(out["provider_verification"]["connected"],True)
        self.assertTrue(out["readiness"]["ready"])
        pjsip.assert_called_once()
        verify.assert_called_once_with(self.account,None)
        readiness.assert_called_once_with(self.account)

    def test_atomic_onboarding_rolls_back_new_trunk_when_did_is_invalid(self):
        out=mcn_core.onboarding_upsert(self.account,{
            "name":"MCN atomic new QA",
            "auth_mode":"registration",
            "registrar":"sip.mcn.ru",
            "username":"qa-atomic-user",
            "password":"qa-atomic-secret",
            "enabled":True,
        },{
            "number":"not-a-phone",
            "purpose":"main",
            "status":"active",
            "inbound_enabled":True,
            "outbound_cli_enabled":True,
        })
        self.assertEqual(out["status"],"onboarding_failed")
        self.assertEqual(out["step"],"did")
        self.assertTrue(out["atomic"])
        db=SessionLocal()
        try:
            trunks=int(db.execute(text("SELECT count(*) FROM telephony_trunks WHERE account_id=:a"),
                                  {"a":self.account}).scalar() or 0)
            dids=int(db.execute(text("SELECT count(*) FROM telephony_dids WHERE account_id=:a"),
                                {"a":self.account}).scalar() or 0)
        finally:
            db.close()
        self.assertEqual(trunks,0)
        self.assertEqual(dids,0)

    def test_atomic_onboarding_rolls_back_existing_trunk_update_when_did_fails(self):
        first=mcn_core.trunk_upsert(self.account,{
            "name":"MCN atomic existing QA",
            "auth_mode":"registration",
            "registrar":"sip.mcn.ru",
            "username":"qa-old-user",
            "password":"qa-old-secret",
            "enabled":True,
        })
        self.assertEqual(first["status"],"ok")
        tid=int(first["trunk"]["id"])

        out=mcn_core.onboarding_upsert(self.account,{
            "name":"MCN atomic existing QA",
            "password":"qa-new-secret",
        },{
            "number":"bad",
            "purpose":"main",
            "status":"active",
        })
        self.assertEqual(out["status"],"onboarding_failed")
        self.assertEqual(out["step"],"did")
        secret=mcn_core.trunk_credentials(self.account,tid)
        self.assertEqual(secret["username"],"qa-old-user")
        self.assertEqual(secret["password"],"qa-old-secret")
        self.assertEqual(len(mcn_core.did_list(self.account)),0)

    def test_atomic_onboarding_commits_trunk_and_did_together(self):
        out=mcn_core.onboarding_upsert(self.account,{
            "name":"MCN atomic success QA",
            "auth_mode":"registration",
            "registrar":"sip.mcn.ru",
            "username":"qa-success-user",
            "password":"qa-success-secret",
            "allowed_cli":["+74957770011"],
            "enabled":True,
        },{
            "number":"+7 (495) 777-00-11",
            "purpose":"main",
            "status":"active",
            "inbound_enabled":True,
            "outbound_cli_enabled":True,
        })
        self.assertEqual(out["status"],"ok")
        self.assertTrue(out["atomic"])
        self.assertEqual(out["did"]["number_e164"],"+74957770011")
        self.assertEqual(int(out["did"]["trunk_id"]),int(out["trunk"]["id"]))
        self.assertEqual(len(mcn_core.trunk_list(self.account)),1)
        self.assertEqual(len(mcn_core.did_list(self.account)),1)

    def test_atomic_onboarding_repeat_is_duplicate_safe(self):
        trunk={
            "name":"MCN atomic repeat QA",
            "auth_mode":"registration",
            "registrar":"sip.mcn.ru",
            "username":"qa-repeat-user",
            "password":"qa-repeat-secret",
            "allowed_cli":["+74959990033"],
            "enabled":True,
        }
        did={
            "number":"+7 (495) 999-00-33",
            "purpose":"main",
            "status":"active",
            "inbound_enabled":True,
            "outbound_cli_enabled":True,
        }
        first=mcn_core.onboarding_upsert(self.account,trunk,did)
        second=mcn_core.onboarding_upsert(self.account,trunk,did)
        self.assertEqual(first["status"],"ok")
        self.assertEqual(second["status"],"ok")
        self.assertEqual(int(first["trunk"]["id"]),int(second["trunk"]["id"]))
        self.assertEqual(int(first["did"]["id"]),int(second["did"]["id"]))
        db=SessionLocal()
        try:
            trunks=int(db.execute(text("SELECT count(*) FROM telephony_trunks WHERE account_id=:a"),{"a":self.account}).scalar() or 0)
            dids=int(db.execute(text("SELECT count(*) FROM telephony_dids WHERE account_id=:a"),{"a":self.account}).scalar() or 0)
        finally:
            db.close()
        self.assertEqual(trunks,1)
        self.assertEqual(dids,1)
        secret=mcn_core.trunk_credentials(self.account,int(second["trunk"]["id"]))
        self.assertEqual(secret["username"],"qa-repeat-user")
        self.assertEqual(secret["password"],"qa-repeat-secret")

    def test_atomic_onboarding_concurrent_repeat_is_duplicate_safe(self):
        trunk={
            "name":"MCN concurrent QA",
            "auth_mode":"ip",
            "registrar":"sip.mcn.ru",
            "source_ips":["192.0.2.10/32"],
            "allowed_cli":["+74951110044"],
            "enabled":True,
        }
        did={
            "number":"+7 (495) 111-00-44",
            "purpose":"main",
            "status":"active",
            "inbound_enabled":True,
            "outbound_cli_enabled":True,
        }
        def one(_):
            return mcn_core.onboarding_upsert(self.account,trunk,did)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(one,range(4)))
        self.assertTrue(all(x.get("status")=="ok" for x in results))
        trunk_ids={int(x["trunk"]["id"]) for x in results}
        did_ids={int(x["did"]["id"]) for x in results}
        self.assertEqual(len(trunk_ids),1)
        self.assertEqual(len(did_ids),1)
        db=SessionLocal()
        try:
            trunks=int(db.execute(text("SELECT count(*) FROM telephony_trunks WHERE account_id=:a"),{"a":self.account}).scalar() or 0)
            dids=int(db.execute(text("SELECT count(*) FROM telephony_dids WHERE account_id=:a"),{"a":self.account}).scalar() or 0)
        finally:
            db.close()
        self.assertEqual(trunks,1)
        self.assertEqual(dids,1)

    def test_atomic_onboard_api_saves_pair_and_autofinalizes(self):
        with patch("app.services.asterisk_gateway.mcn_pjsip_guardian",return_value={
            "status":"ok","owner_action_required":False
        }) as pjsip, patch("app.services.telephony_core.verify_provider_connection",return_value={
            "status":"ok","connected":False
        }) as verify, patch("app.api.mcn_phone.mcn_readiness",return_value={
            "ready":False,"steps_completed":1,"steps_total":3,
            "primary_next_action":{"code":"mcn_registration_self_heal"}
        }):
            out=API.onboard({
                "account_id":self.account,
                "trunk":{
                    "name":"MCN atomic API QA",
                    "auth_mode":"registration",
                    "registrar":"sip.mcn.ru",
                    "username":"qa-api-user",
                    "password":"qa-api-secret",
                    "allowed_cli":["+74958880022"],
                    "enabled":True,
                },
                "did":{
                    "number":"+7 (495) 888-00-22",
                    "purpose":"main",
                    "status":"active",
                    "inbound_enabled":True,
                    "outbound_cli_enabled":True,
                },
            },current_user=None)
        self.assertEqual(out["status"],"ok")
        self.assertTrue(out["atomic"])
        self.assertTrue(out["autofinalize"])
        self.assertEqual(out["provider_config"]["provider"],"mcn")
        self.assertEqual(out["pjsip_apply"],"automatic_guardian")
        self.assertEqual(out["did"]["number_e164"],"+74958880022")
        self.assertEqual(int(out["did"]["trunk_id"]),int(out["trunk"]["id"]))
        pjsip.assert_called_once()
        verify.assert_called_once_with(self.account,None)

    def test_mcn_provider_config_guardian_heals_only_missing_config(self):
        tr=mcn_core.trunk_upsert(self.account,{
            "name":"MCN provider guardian QA",
            "auth_mode":"registration",
            "registrar":"sip.mcn.ru",
            "username":"qa-provider-user",
            "password":"qa-provider-secret",
            "enabled":True,
        })
        self.assertEqual(tr["status"],"ok")
        before=T.provider_config_public(self.account)
        self.assertIsNone(before["config"])

        healed=T.mcn_provider_config_guardian(include_synthetic=True)
        self.assertEqual(healed["status"],"ok")
        self.assertEqual(healed["found"],1)
        self.assertEqual(healed["healed"],1)
        cfg=T.provider_config_public(self.account)["config"]
        self.assertEqual(cfg["provider"],"mcn")

    def test_mcn_provider_config_guardian_does_not_override_existing_provider(self):
        tr=mcn_core.trunk_upsert(self.account,{
            "name":"MCN provider preserve QA",
            "auth_mode":"registration",
            "registrar":"sip.mcn.ru",
            "username":"qa-preserve-user",
            "password":"qa-preserve-secret",
            "enabled":True,
        })
        self.assertEqual(tr["status"],"ok")
        saved=T.save_provider_config(
            self.account,"zadarma",
            credentials={"user_key":"qa-user-key","secret_key":"qa-secret-key"},
            public_config={},
            actor_user_id=None,
        )
        self.assertEqual(saved["status"],"ok")
        self.assertEqual(saved["config"]["provider"],"zadarma")

        healed=T.mcn_provider_config_guardian(include_synthetic=True)
        self.assertEqual(healed["found"],0)
        cfg=T.provider_config_public(self.account)["config"]
        self.assertEqual(cfg["provider"],"zadarma")

    def test_mcn_readiness_reports_real_three_step_onboarding_progress(self):
        initial=mcn_core.readiness(self.account)
        self.assertEqual(initial["steps_completed"],0)
        self.assertEqual(initial["steps_total"],3)
        self.assertEqual(initial["primary_next_action"]["code"],"mcn_trunk_credentials")

        tr=mcn_core.trunk_upsert(self.account,{
            "provider":"mcn",
            "name":"MCN progress QA",
            "auth_mode":"registration",
            "username":"qa-login",
            "password":"qa-password",
            "enabled":True,
        })
        self.assertEqual(tr["status"],"ok")
        tid=int(tr["trunk"]["id"])

        saved=mcn_core.readiness(self.account)
        self.assertEqual(saved["steps_completed"],1)
        self.assertEqual(saved["primary_next_action"]["code"],"mcn_registration_self_heal")
        self.assertFalse(saved["primary_next_action"]["owner_action_required"])

        mcn_core.trunk_mark_health(self.account,tid,True,"registration_registered")
        registered=mcn_core.readiness(self.account)
        self.assertEqual(registered["steps_completed"],2)
        self.assertEqual(registered["primary_next_action"]["code"],"mcn_did_required")
        self.assertTrue(registered["primary_next_action"]["owner_action_required"])

        did=mcn_core.did_upsert(self.account,{
            "provider":"mcn",
            "number":"+74951234569",
            "trunk_id":tid,
            "status":"active",
            "inbound_enabled":True,
        })
        self.assertEqual(did["status"],"ok")
        ready=mcn_core.readiness(self.account)
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["steps_completed"],3)
        self.assertEqual(ready["primary_next_action"]["code"],"mcn_ready")
        self.assertFalse(ready["primary_next_action"]["owner_action_required"])


if __name__=="__main__":
    unittest.main()
