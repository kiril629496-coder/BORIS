import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("media_contract",ROOT/"app/services/campaign_banner_media_contract.py")
contract=importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class BannerMediaProofTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.file=self.root/"tenant-a"/"hero.png"
        self.file.parent.mkdir()
        self.file.write_bytes(b"exact paid artifact bytes")
        self.sha=hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.asset=NS(id=11,account_id="tenant-a",storage_key="tenant-a/hero.png",sha256=self.sha,
                      media_type="banner",source_provider="openai_full_ai_premium",source_ref="premium:banner_abc:v1")
        self.link=NS(media_id=11,role="hero",position=0)
        self.payload={"banner_provider":"openai_full_ai_premium","banner_url":"/images/tenant-a/hero.png",
                      "images":["/images/tenant-a/hero.png"],"banner_regeneration_key":"job:3:item:9:r0",
                      "banner_qa":{"passed":True,"banner_id":"banner_abc","version":1,"qa_version":"test_v1","artifact_sha256":self.sha,"technical_qa":{"artifact_sha256":self.sha}}}
        self.current={"status":"ok","url":"/images/tenant-a/hero.png","qa_version":"test_v1","qa_terminal":True,"receipt_current":True,"artifact_current":True,"qa_passed":True,
                      "cost_reconciled":True,"cost_sla_pass":True,"artifact_sha256":self.sha}
        self.read=Mock(side_effect=lambda *a:dict(self.current))

    def proof(self,**kwargs):
        return contract.premium_link_proof(self.payload,[(self.link,self.asset)],account_id="tenant-a",
            hero_media_id=11,read_preview=self.read,image_root=self.root,**kwargs)

    def test_exact_current_receipt_and_bytes_pass_without_paid_call(self):
        self.assertTrue(self.proof()["ready"])
        self.read.assert_called_once_with("tenant-a","banner_abc",1)

    def test_same_url_changed_bytes_invalidates_before_receipt_read(self):
        self.file.write_bytes(b"replacement bytes at same path")
        self.assertFalse(self.proof()["ready"])
        self.read.assert_not_called()

    def test_same_bytes_stale_qa_or_unknown_cost_never_passes(self):
        for flag in ["receipt_current","artifact_current","qa_passed","cost_reconciled","cost_sla_pass"]:
            with self.subTest(flag=flag):
                self.current[flag]=False
                self.assertFalse(self.proof()["ready"])
                self.current[flag]=True

    def test_string_true_is_not_a_receipt_boolean(self):
        for flag in ["qa_terminal","receipt_current","artifact_current","qa_passed","cost_reconciled","cost_sla_pass"]:
            old=self.current[flag];self.current[flag]="true"
            self.assertFalse(self.proof()["ready"])
            self.current[flag]=old

    def test_new_force_intent_cannot_be_completed_by_old_banner(self):
        self.assertFalse(self.proof(expected_intent="job:3:item:9:r1")["ready"])
        self.read.assert_not_called()
        self.assertTrue(self.proof(expected_intent="job:3:item:9:r0")["ready"])

    def test_foreign_tenant_cannot_supply_hero(self):
        self.asset.account_id="tenant-b"
        self.assertFalse(self.proof()["ready"])
        self.read.assert_not_called()

    def test_foreign_tenant_cannot_hide_in_gallery(self):
        foreign=NS(id=12,account_id="tenant-b")
        result=contract.premium_link_proof(self.payload,[(self.link,self.asset),(NS(),foreign)],
            account_id="tenant-a",hero_media_id=11,read_preview=self.read,image_root=self.root)
        self.assertFalse(result["ready"])
        self.read.assert_not_called()

    def test_hero_role_and_position_required(self):
        self.link.role="gallery"
        self.assertFalse(self.proof()["ready"])
        self.link.role="hero";self.link.position=1
        self.assertFalse(self.proof()["ready"])

    def test_payload_must_publish_the_actual_linked_hero(self):
        self.payload["images"][0]="/images/wrong.png"
        self.assertFalse(self.proof()["ready"])

    def test_external_same_path_url_is_not_local_proof(self):
        self.payload["banner_url"]="https://unrelated.invalid/images/tenant-a/hero.png"
        self.payload["images"][0]=self.payload["banner_url"]
        self.assertFalse(self.proof()["ready"])

    def test_canonical_absolute_public_url_can_be_proven(self):
        url="https://canonical.example/images/tenant-a/hero.png"
        self.payload["banner_url"]=url;self.payload["images"][0]=url
        self.assertTrue(self.proof(public_url=url)["ready"])

    def test_payload_and_receipt_hashes_must_both_match_registered_bytes(self):
        self.current["artifact_sha256"]="a"*64
        self.assertFalse(self.proof()["ready"])
        self.current["artifact_sha256"]=self.sha
        self.payload["banner_qa"]["technical_qa"]["artifact_sha256"]="b"*64
        self.assertFalse(self.proof()["ready"])

    def test_path_escape_is_never_read_as_an_image(self):
        self.asset.storage_key="../outside.png"
        self.assertFalse(contract.linked_file_current(self.asset,self.root))

    def test_gallery_change_without_version_still_changes_signature(self):
        before=contract.media_signature([(self.link,self.asset)])
        self.link.role="gallery"
        self.assertNotEqual(before,contract.media_signature([(self.link,self.asset)]))

    def test_new_selected_asset_replacement_cannot_inherit_old_qa(self):
        before=contract.asset_signature([self.asset])
        self.file.write_bytes(b"a new object replacing the previously inspected asset")
        self.asset.sha256=hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.assertTrue(contract.linked_file_current(self.asset,self.root))
        self.assertNotEqual(before,contract.asset_signature([self.asset]))

    def test_later_text_edit_invalidates_unchanged_banner(self):
        self.current["provenance"]={"content_fingerprint":"old-copy"}
        self.payload["banner_qa"]["content_fingerprint"]="old-copy"
        self.assertTrue(self.proof(content_fingerprint="old-copy")["ready"])
        self.assertFalse(self.proof(content_fingerprint="new-copy")["ready"])

    def test_receipt_for_another_item_cannot_certify_identical_copy(self):
        self.current["provenance"]={"campaign_id":3,"campaign_item_id":9}
        self.assertTrue(self.proof(campaign_id=3,item_id=9)["ready"])
        self.assertFalse(self.proof(campaign_id=3,item_id=10)["ready"])
        self.assertFalse(self.proof(campaign_id=4,item_id=9)["ready"])

    def test_payload_qa_version_cannot_silently_adopt_new_proof(self):
        self.current["qa_version"]="new_v2"
        self.assertFalse(self.proof()["ready"])

    def test_missing_top_level_hash_or_different_receipt_url_cannot_certify_ready(self):
        self.payload["banner_qa"].pop("artifact_sha256")
        self.assertFalse(self.proof()["ready"])
        self.payload["banner_qa"]["artifact_sha256"]=self.sha
        self.current["url"]="/images/different/hero.png"
        self.assertFalse(self.proof()["ready"])

    def test_unknown_provider_outcome_is_not_lost_in_adapter_error(self):
        error=contract.preview_error(9,"premium_preview_failed",{
            "banner_id":"abc","version":1,"artifact_sha256":self.sha,"image_outcome":"unknown",
            "ambiguous_provider_outcome":True,"provider_request_id":"req-real",
            "retry_action":"reconcile_image","retry_allowed":False})
        self.assertTrue(error["ambiguous_provider_outcome"])
        self.assertEqual(error["retry_action"],"reconcile_image")
        self.assertEqual(error["provider_request_id"],"req-real")
        self.assertFalse(error["retry_allowed"])

    def test_unclassified_error_is_fail_closed(self):
        error=contract.preview_error(9,"unexpected")
        self.assertEqual(error["retry_action"],"blocked")
        self.assertFalse(error["retry_allowed"])


class RequestIdentityTest(unittest.TestCase):
    def test_creative_family_survives_chunk_changes_and_restart(self):
        first=contract.premium_creative_choices("a",3,9,"order:9:r0")
        contract.premium_creative_choices("a",3,10,"order:10:r0")
        self.assertEqual(first,contract.premium_creative_choices("a",3,9,"order:9:r0"))

    def test_history_aware_rotation_uses_all_six_without_mass_repetition(self):
        history=[]; values=[]
        for i in range(100):
            family=contract.premium_creative_choices("a",3,i,f"order:{i}:r0",existing_families=history)[1]
            values.append(family); history.append(family)
        self.assertEqual(set(values),set(contract.V7_COMPOSITION_FAMILIES))
        counts=[values.count(family) for family in contract.V7_COMPOSITION_FAMILIES]
        self.assertLessEqual(max(counts),35)
        self.assertEqual(
            contract.premium_creative_choices("a",3,101,"order:101:r0",existing_families=history),
            contract.premium_creative_choices("a",3,101,"order:101:r0",existing_families=list(history)),
        )

    def test_v7_metadata_persists_actual_visual_signature_and_layout_contract(self):
        meta=contract.premium_v7_metadata("diagonal",{
            "technical_qa":{"perceptual_hash":"abcdef0123456789"},
            "composition_profile":"asymmetry","icon_profile":"minimal",
            "creative_hypothesis":"product_hero","prompt_sha256":"a"*64,
        })
        self.assertEqual(meta["visual_signature"],"abcdef0123456789")
        self.assertEqual(meta["composition_family"],"diagonal")
        self.assertEqual(meta["main_composition_class"],"diagonal")
        self.assertEqual(meta["cta_type"],"message_start")
        for key in ("text_position","object_position","background_family","composition_profile","icon_profile","contract_version"):
            self.assertTrue(meta[key])
        ctas=[contract.premium_v7_cta(family) for family in contract.V7_COMPOSITION_FAMILIES]
        self.assertEqual(len({kind for kind,_ in ctas}),6)
        self.assertTrue(all("Avito" in text for _,text in ctas))

    def test_full_maps_preserve_image_and_budget_identity(self):
        body=NS(item_idempotency_keys={"1":"image1:r2","2":"image2:r0"},
                item_budget_intent_keys={"1":"budget1","2":"budget2"})
        image,budget=contract.request_intents(body,[1,2])
        self.assertEqual(image["1"],"image1:r2")
        self.assertEqual(budget["1"],"budget1")

    def test_partial_foreign_or_duplicate_maps_are_rejected(self):
        from fastapi import HTTPException
        cases=[({"1":"x"},{"1":"b"}),({"1":"x","3":"y"},{"1":"b","3":"c"}),
               ({"1":"x","2":"x"},{"1":"b","2":"c"}),({"1":"x","2":"y"},{})]
        for image,budget in cases:
            with self.subTest(image=image),self.assertRaises(HTTPException):
                contract.request_intents(NS(item_idempotency_keys=image,item_budget_intent_keys=budget),[1,2])


if __name__=="__main__":
    unittest.main()
