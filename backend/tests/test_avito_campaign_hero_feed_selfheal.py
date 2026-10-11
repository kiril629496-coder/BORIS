import unittest
from types import SimpleNamespace
from scripts.avito_campaign_hero_feed_selfheal import canon, apply_hero_replacements

class HeroFeedSelfHealContractTests(unittest.TestCase):
    def obj(self, ident, images):
        return SimpleNamespace(id=ident, images=list(images))

    def test_canon_normalizes_host_query_and_percent_encoding(self):
        self.assertEqual(canon("https://boris-ai.pro/images/a/My%20Photo.png?v=2"), "/images/a/My Photo.png")
        self.assertEqual(canon("/images/a/My Photo.png"), "/images/a/My Photo.png")

    def test_hero_already_at_zero_is_noop(self):
        a=self.obj("A",["hero.jpg","two.jpg"])
        out=apply_hero_replacements([a],{"A":{"hero":"hero.jpg","item_id":1}})
        self.assertIs(out[0],a); self.assertEqual(a.images,["hero.jpg","two.jpg"])

    def test_existing_hero_moves_to_zero_and_preserves_relative_order(self):
        a=self.obj("A",["one.jpg","two.jpg","hero.jpg","three.jpg"])
        apply_hero_replacements([a],{"A":{"hero":"hero.jpg","item_id":1}})
        self.assertEqual(a.images,["hero.jpg","one.jpg","two.jpg","three.jpg"])

    def test_absent_hero_preserves_replace_index_zero_behavior(self):
        a=self.obj("A",["old.jpg","two.jpg","three.jpg"])
        apply_hero_replacements([a],{"A":{"hero":"new.jpg","item_id":1}})
        self.assertEqual(a.images,["new.jpg","two.jpg","three.jpg"])

    def test_multiple_equivalent_hero_occurrences_collapse_to_one(self):
        a=self.obj("A",["old.jpg","https://boris-ai.pro/images/a/x%20y.jpg?v=1","/images/a/x y.jpg","keep.jpg"])
        apply_hero_replacements([a],{"A":{"hero":"/images/a/x y.jpg","item_id":1}})
        self.assertEqual(a.images,["/images/a/x y.jpg","old.jpg","keep.jpg"])

    def test_other_duplicate_urls_are_deduplicated_in_order(self):
        a=self.obj("A",["hero.jpg","same.jpg","same.jpg","last.jpg"])
        apply_hero_replacements([a],{"A":{"hero":"hero.jpg","item_id":1}})
        self.assertEqual(a.images,["hero.jpg","same.jpg","last.jpg"])

    def test_one_image_gallery(self):
        a=self.obj("A",["only.jpg"])
        apply_hero_replacements([a],{"A":{"hero":"only.jpg","item_id":1}})
        self.assertEqual(a.images,["only.jpg"])

    def test_rejects_empty_gallery(self):
        a=self.obj("A",[])
        with self.assertRaisesRegex(RuntimeError,"empty feed gallery A"):
            apply_hero_replacements([a],{"A":{"hero":"new.jpg","item_id":1}})

    def test_other_identity_and_account_scope_are_untouched(self):
        a=self.obj("A",["old.jpg","hero.jpg"]); b=self.obj("B",["keep.jpg","keep2.jpg"])
        apply_hero_replacements([a,b],{"A":{"hero":"hero.jpg","item_id":1}})
        self.assertEqual(a.images,["hero.jpg","old.jpg"]); self.assertEqual(b.images,["keep.jpg","keep2.jpg"])

    def test_rejects_missing_bound_identity(self):
        a=self.obj("A",["old.jpg"])
        with self.assertRaisesRegex(RuntimeError,"missing identities"):
            apply_hero_replacements([a],{"B":{"hero":"new.jpg","item_id":2}})

if __name__=="__main__": unittest.main()
