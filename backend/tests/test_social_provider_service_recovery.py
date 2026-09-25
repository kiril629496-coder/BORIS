import unittest
from unittest.mock import patch
import posting_runner as p
class T(unittest.TestCase):
 def test_dushi(self):
  with patch.object(p,'_openai_text',side_effect=RuntimeError('CAMPAIGN_AI_PROVIDER_COOLDOWN:500')),patch.object(p,'_social_reuse_published_banner',return_value={'path':'/tmp/a.png','source_record_id':'r'}): o=p.generate_post('u2','слова для прощания, когда трудно говорить',image_source='ai',project_id='f938caa3a944')
  self.assertEqual(o['text_engine'],'deterministic_provider_recovery'); self.assertEqual(o['banner_engine'],'reuse_published_project_asset')
 def test_manicure(self):
  with patch.object(p,'_openai_text',side_effect=RuntimeError('CAMPAIGN_AI_RELEASED_RETRY_CAP:9')),patch.object(p,'_social_reuse_published_banner',return_value={'path':'/tmp/a.png','source_record_id':'r'}): o=p.generate_post('u2','кошачий глаз. Услуга: уход за стопами.',image_source='ai',project_id='270dbf472f7f')
  self.assertEqual(o['text_engine'],'deterministic_provider_recovery')
 def test_other_closed(self):
  with patch.object(p,'_openai_text',side_effect=RuntimeError('CAMPAIGN_AI_PROVIDER_COOLDOWN:500')):
   with self.assertRaises(RuntimeError): p.generate_post('x','t',image_source='ai',project_id='other')
class PartialAssetReuseTest(unittest.TestCase):
 def test_valid_partial_asset_breaks_vk_only_recovery_deadlock(self):
  import json
  from types import SimpleNamespace
  class Q:
   def filter(self,*a,**k): return self
   def first(self):
    return SimpleNamespace(value=json.dumps([
     {'id':'partial-good','project_id':'270dbf472f7f','status':'partial','banner':'partial.png','created_at':'2026-09-14 01:00'},
     {'id':'error-bad','project_id':'270dbf472f7f','status':'error','banner':'error.png','created_at':'2026-09-14 02:00'},
    ]))
  class DB:
   def query(self,*a,**k): return Q()
   def close(self): pass
  with patch.object(p,'SessionLocal',return_value=DB()), \
       patch('os.path.isfile',side_effect=lambda x: str(x).endswith('partial.png')), \
       patch('os.path.getsize',return_value=100000), \
       patch('os.path.realpath',side_effect=lambda x: str(x)):
   out=p._social_reuse_published_banner('u2','270dbf472f7f','theme')
  self.assertTrue(out['path'].endswith('partial.png'))
  self.assertEqual(out['source_record_id'],'partial-good')

if __name__=='__main__': unittest.main()
