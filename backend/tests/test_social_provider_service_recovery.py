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

class ManicureQualityRegressionTest(unittest.TestCase):
 def test_manicure_recovery_never_leaks_internal_service_metadata_or_sea(self):
  theme='педикюр осенью: зачем он нужен, когда открытая обувь уже не актуальна. Услуга: маникюр. Для ИЗОБРАЖЕНИЯ: море'
  text=p._social_provider_text_recovery('270dbf472f7f',theme)
  self.assertIn('Педикюр не заканчивается',text)
  self.assertNotIn('Услуга:',text)
  self.assertNotIn('море',text.lower())

 def test_nail_traits_follow_pedicure_topic_not_random_hand_traits(self):
  base='педикюр осенью: зачем он нужен, когда открытая обувь уже не актуальна'
  traits={
   'service_type':'маникюр','content_type':'юмор','design_type':'неоновый',
   'primary_color':'неоновый','nail_shape':'миндаль','nail_length':'длинные',
   'visual_style':'яркий','tone':'лёгкий','cta_type':'вопрос аудитории',
   'composition':'одна рука крупным планом',
  }
  with patch.object(p,'_traits_history',return_value=[]), patch.object(p,'_least_used',side_effect=lambda values,*a,**k: values[0]):
   out=p._align_nail_traits_to_base('u2',base,traits)
  self.assertEqual(out['service_type'],'педикюр')
  self.assertIn(out['composition'],p._FOOT_COMPS)

 def test_owner_policy_never_adds_sea_hashtag_fallback(self):
  project={'id':'270dbf472f7f','name':'Маникюр Евпатория'}
  text=p._project_delivery_text('Летний дизайн без привязки к купанию',project,'vk')
  self.assertNotIn('#маникюрнаморе',text.lower())

class SocialCanonicalPaidContourTest(unittest.TestCase):
 def test_canonical_paid_contour_is_primary(self):
  with patch('app.api.campaigns._ff_guarded_openai_response',return_value={'text':'Сильный текст'}) as paid, \
       patch.object(p,'_social_secondary_ai_text') as secondary:
   out=p._openai_text('u2','prompt','social_test_primary')
  self.assertEqual(out,'Сильный текст')
  self.assertEqual(paid.call_count,1)
  secondary.assert_not_called()

 def test_proven_403_uses_secondary_without_second_openai(self):
  import requests
  response=requests.Response(); response.status_code=403
  err=requests.HTTPError('403'); err.response=response
  with patch('app.api.campaigns._ff_guarded_openai_response',side_effect=err), \
       patch.object(p,'_social_secondary_ai_text',return_value='Вторичный сильный текст') as secondary:
   out=p._openai_text('u2','prompt','social_test_403')
  self.assertEqual(out,'Вторичный сильный текст')
  self.assertEqual(secondary.call_count,1)

 def test_ambiguous_primary_outcome_never_buys_secondary(self):
  with patch('app.api.campaigns._ff_guarded_openai_response',side_effect=RuntimeError('CAMPAIGN_AI_OUTCOME_UNCERTAIN:network')), \
       patch.object(p,'_social_secondary_ai_text') as secondary:
   with self.assertRaisesRegex(RuntimeError,'CAMPAIGN_AI_OUTCOME_UNCERTAIN'):
    p._openai_text('u2','prompt','social_test_ambiguous')
  secondary.assert_not_called()
