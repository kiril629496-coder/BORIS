import unittest
from types import SimpleNamespace
from scripts.avito_campaign_hero_feed_selfheal import canon, apply_hero_replacements

class HeroFeedSelfHealContractTests(unittest.TestCase):
    def test_canon_accepts_absolute_and_relative_same_image(self):
        self.assertEqual(canon('https://boris-ai.pro/images/a/x.png'), '/images/a/x.png')
        self.assertEqual(canon('/images/a/x.png'), '/images/a/x.png')

    def test_changes_only_hero_and_preserves_gallery_and_other_identity(self):
        a=SimpleNamespace(id='A', images=['old.jpg','two.jpg','three.jpg'])
        b=SimpleNamespace(id='B', images=['keep.jpg','keep2.jpg'])
        out=apply_hero_replacements([a,b], {'A':{'hero':'new.jpg','item_id':1}})
        self.assertIs(out[0],a)
        self.assertEqual(a.images,['new.jpg','two.jpg','three.jpg'])
        self.assertEqual(b.images,['keep.jpg','keep2.jpg'])

    def test_rejects_empty_gallery(self):
        a=SimpleNamespace(id='A', images=[])
        with self.assertRaisesRegex(RuntimeError,'empty feed gallery A'):
            apply_hero_replacements([a], {'A':{'hero':'new.jpg','item_id':1}})

    def test_rejects_missing_bound_identity(self):
        a=SimpleNamespace(id='A', images=['old.jpg'])
        with self.assertRaisesRegex(RuntimeError,'missing identities'):
            apply_hero_replacements([a], {'B':{'hero':'new.jpg','item_id':2}})

if __name__=='__main__': unittest.main()
