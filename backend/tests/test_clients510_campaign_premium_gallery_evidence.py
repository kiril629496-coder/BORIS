"""Offline regression for the real Premium gallery binding helper.

Only AST-extracted production code and the real semantic validator execute.
Media bytes live in TemporaryDirectory; no DB, provider or queue is used.
"""
import ast
import copy
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from app.services import media_semantic_validator as V
from app.services import media_storage

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("BORIS_TEST_CAMPAIGNS_SOURCE", str(ROOT / "app/api/campaigns.py")))


def load_rebind():
    tree = ast.parse(SOURCE.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "_ff_rebind_premium_gallery_evidence")
    scope = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), scope)
    return scope[node.name]


class PremiumGalleryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rebind = load_rebind()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = {
            "feed_factory": True,
            "title": "Профнастил для кровли",
            "description": "Профилированный стальной лист для крыши",
            "direction": "Профнастил",
            "category": "Ремонт и строительство",
            "media_semantic_guard": {"mode": "universal_text_image", "min_confidence": 0.8},
        }
        self.pairs = []
        for pos in range(8):
            key = f"gallery-{pos}.jpg"
            data = f"exact stored artifact {pos}".encode()
            (self.root / key).write_bytes(data)
            self.pairs.append((NS(id=pos + 100, position=pos, role="gallery"),
                NS(id=pos + 10, storage_key=key, sha256=hashlib.sha256(data).hexdigest(),
                   source_provider="local_derivative", source_ref="source-product", account_id="test-account")))
        fp = V.semantic_content_fingerprint(self.payload)
        self.payload["media_semantic_evidence"] = [
            {"media_id":a.id,"position":l.position,"content_fingerprint":fp,
             "match":True,"confidence":0.95,"subject":"профнастил для кровли",
             "checked_at":"2026-10-08T09:00:00+00:00","checker":"stored_real_vision"}
            for l,a in self.pairs]
        self.storage = patch.object(media_storage,"abs_path",side_effect=lambda key: self.root/key)
        self.storage.start()
        self.addCleanup(self.storage.stop)

    def bind(self,payload=None,pairs=None):
        return self.rebind(payload or self.payload,pairs if pairs is not None else self.pairs)

    def projected_errors(self,evidence,payload=None):
        p=copy.deepcopy(payload or self.payload)
        p["media_semantic_evidence"]=evidence
        banner=NS(id=999,storage_key="premium/banner.jpg",source_provider="openai_full_ai_premium",
                  source_ref="premium:known:v1",account_id="test-account")
        linked=[(NS(position=0,role="hero"),banner)]
        linked += [(NS(position=i,role="gallery"),a) for i,(_l,a) in enumerate(self.pairs,1)]
        p["images"]=["/images/"+a.storage_key for l,a in linked]
        p["banner_url"]=p["images"][0]
        p["banner_qa"]={"passed":True}
        with patch.object(V,"_linked_media",return_value=linked):
            return V.validate_linked_media_semantics(None,NS(id=1),p)

    def test_new_hero_rebinds_all_eight_gallery_positions(self):
        got=self.bind()
        self.assertEqual([e["position"] for e in got],list(range(1,9)))
        self.assertEqual(self.projected_errors(got),[])

    def test_preserves_original_qa_time_fingerprint_and_input(self):
        old=copy.deepcopy(self.payload)
        got=self.bind()
        self.assertEqual(self.payload,old)
        for before,after in zip(old["media_semantic_evidence"],got):
            self.assertEqual(before["checked_at"],after["checked_at"])
            self.assertEqual(before["content_fingerprint"],after["content_fingerprint"])
            self.assertEqual(before["checker"],after["checker"])
            self.assertEqual(after["placement_rebinding"]["artifact_sha256"],
                             self.pairs[before["position"]][1].sha256)

    def test_changed_title_does_not_relabel_stale_proof(self):
        self.payload["title"]="Другая услуга"
        old=copy.deepcopy(self.payload["media_semantic_evidence"])
        got=self.bind()
        self.assertEqual(got,old)
        self.assertTrue(self.projected_errors(got))

    def test_changed_description_does_not_relabel_stale_proof(self):
        self.payload["description"]="Другой объект объявления"
        old=copy.deepcopy(self.payload["media_semantic_evidence"])
        self.assertEqual(self.bind(),old)

    def test_position_must_match_old_link(self):
        self.payload["media_semantic_evidence"][0]["position"]=7
        self.assertEqual(self.bind()[0],self.payload["media_semantic_evidence"][0])

    def test_different_media_id_cannot_borrow_proof(self):
        self.payload["media_semantic_evidence"][0]["media_id"]=888
        self.assertEqual(self.bind()[0],self.payload["media_semantic_evidence"][0])

    def test_negative_low_confidence_and_unchecked_are_not_rebound(self):
        for update in [{"match":False},{"confidence":0.79},{"confidence":float("nan")},
                       {"confidence":2},{"checked_at":None}]:
            with self.subTest(update=update):
                p=copy.deepcopy(self.payload);p["media_semantic_evidence"][0].update(update)
                got=self.bind(p)
                self.assertEqual(got[0]["position"],0)
                self.assertNotIn("placement_rebinding",got[0])

    def test_changed_bytes_fail_closed(self):
        (self.root/"gallery-0.jpg").write_bytes(b"different artifact")
        self.assertEqual(self.bind()[0],self.payload["media_semantic_evidence"][0])
        self.assertTrue(self.projected_errors(self.bind()))

    def test_missing_bytes_fail_closed(self):
        (self.root/"gallery-0.jpg").unlink()
        self.assertEqual(self.bind()[0],self.payload["media_semantic_evidence"][0])

    def test_missing_asset_sha_fail_closed(self):
        self.pairs[0][1].sha256=None
        self.assertEqual(self.bind()[0],self.payload["media_semantic_evidence"][0])

    def test_existing_proof_sha_must_match(self):
        self.payload["media_semantic_evidence"][0]["artifact_sha256"]="0"*64
        self.assertEqual(self.bind()[0],self.payload["media_semantic_evidence"][0])

    def test_duplicate_asset_positions_are_ambiguous(self):
        pairs=[self.pairs[0],(NS(position=8),self.pairs[0][1])]+self.pairs[1:]
        self.assertEqual(self.bind(pairs=pairs)[0],self.payload["media_semantic_evidence"][0])

    def test_unchanged_layout_keeps_proof_byte_for_byte(self):
        for l,_ in self.pairs:l.position+=1
        for e in self.payload["media_semantic_evidence"]:e["position"]+=1
        self.assertEqual(self.bind(),self.payload["media_semantic_evidence"])

    def test_removed_banner_proof_is_not_reused_for_new_banner(self):
        old={"media_id":777,"position":0,"match":True,"confidence":1,
             "content_fingerprint":V.semantic_content_fingerprint(self.payload),
             "checked_at":"2026-10-07T00:00:00Z"}
        self.payload["media_semantic_evidence"].append(old)
        got=self.bind()
        self.assertEqual(got[-1],old)
        self.assertEqual(self.projected_errors(got),[])


if __name__=="__main__":
    unittest.main()
