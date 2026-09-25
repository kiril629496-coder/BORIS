import pathlib
import unittest


class NoLegacyDirectPaidApiTests(unittest.TestCase):
    def test_active_api_has_no_direct_chat_completion_boundary(self):
        root=pathlib.Path('/root/BORIS/backend/app/api')
        offenders=[]
        url='https://api.openai.com/v1/chat/completions'
        for p in root.rglob('*.py'):
            name=p.name
            rel=str(p.relative_to(root))
            if '/_backups/' in '/'+rel or name.startswith('.') or '.bak' in name or '.before_' in name or '.backup_' in name:
                continue
            src=p.read_text(encoding='utf-8',errors='ignore')
            if url in src:
                offenders.append((rel,src.count(url)))
        self.assertEqual(offenders,[],offenders)

    def test_audio_provider_urls_live_only_inside_shared_audio_guard(self):
        backend=pathlib.Path('/root/BORIS/backend/app')
        needles=('https://api.openai.com/v1/audio/transcriptions','https://api.openai.com/v1/audio/speech')
        hits=[]
        for p in backend.rglob('*.py'):
            name=p.name
            rel=str(p.relative_to(backend))
            if '/_backups/' in '/'+rel or '.bak' in name or '.before_' in name or '.backup_' in name:
                continue
            src=p.read_text(encoding='utf-8',errors='ignore')
            for n in needles:
                if n in src: hits.append((rel,n.rsplit('/',1)[-1]))
        self.assertEqual(sorted(hits),sorted([
            ('services/openai_audio_guard.py','transcriptions'),
            ('services/openai_audio_guard.py','speech'),
        ]),hits)

    def test_responses_and_image_provider_urls_stay_on_canonical_paid_boundaries(self):
        backend=pathlib.Path('/root/BORIS/backend/app')
        expected={
            'https://api.openai.com/v1/responses': [('api/campaigns.py',1), ('services/sales_ai_router.py',1), ('ext_api/aiprov.py',1)],
            'https://api.openai.com/v1/images/generations': [('services/openai_image_transport.py',1)],
            'https://api.openai.com/v1/images/edits': [('services/image_service.py',1)],
        }
        for url,want in expected.items():
            hits=[]
            for p in backend.rglob('*.py'):
                name=p.name
                rel=str(p.relative_to(backend))
                if '/_backups/' in '/'+rel or '.bak' in name or '.before_' in name or '.backup_' in name:
                    continue
                src=p.read_text(encoding='utf-8',errors='ignore')
                count=src.count(url)
                if count:
                    hits.append((rel,count))
            self.assertEqual(hits,want,(url,hits))

        campaigns=(backend/'api/campaigns.py').read_text(encoding='utf-8',errors='ignore')
        pos=campaigns.index('https://api.openai.com/v1/responses')
        window=campaigns[max(0,pos-1800):pos+1800]
        self.assertIn('Idempotency-Key',window)
        self.assertIn('ambiguous-text:campaign:',campaigns)
        self.assertIn('safe_external_ai_payload',campaigns)

        image_transport=(backend/'services/openai_image_transport.py').read_text(encoding='utf-8',errors='ignore')
        self.assertIn('Idempotency-Key',image_transport)
        image_service=(backend/'services/image_service.py').read_text(encoding='utf-8',errors='ignore')
        self.assertIn('boris-image-edit:',image_service)

    def test_all_active_chat_completion_boundaries_are_known_and_guarded(self):
        backend=pathlib.Path('/root/BORIS/backend/app')
        url='https://api.openai.com/v1/chat/completions'
        hits=[]
        for p in backend.rglob('*.py'):
            name=p.name
            rel=str(p.relative_to(backend))
            if '/_backups/' in '/'+rel or '.bak' in name or '.before_' in name or '.backup_' in name:
                continue
            src=p.read_text(encoding='utf-8',errors='ignore')
            count=src.count(url)
            if count:
                hits.append((rel,count))
        self.assertEqual(sorted(hits),sorted([
            ('services/category_resolver.py',1),
            ('services/media_semantic_validator.py',1),
            ('services/realtime_ai_guard.py',1),
        ]),hits)

        category=(backend/'services/category_resolver.py').read_text(encoding='utf-8',errors='ignore')
        self.assertIn('Idempotency-Key',category)
        self.assertIn('category_transport_outcome_unknown',category)
        self.assertIn('ambiguous-text:category:',category)

        vision=(backend/'services/media_semantic_validator.py').read_text(encoding='utf-8',errors='ignore')
        self.assertIn('Idempotency-Key',vision)
        self.assertIn('vision_transport_outcome_unknown',vision)
        self.assertIn('VISION_OUTCOME_UNCERTAIN',vision)

        realtime=(backend/'services/realtime_ai_guard.py').read_text(encoding='utf-8',errors='ignore')
        self.assertIn('guarded_post',realtime)
        self.assertIn('OPENAI_CHAT_URL',realtime)
        self.assertIn('safe_external_ai_payload',realtime)


if __name__=='__main__':
    unittest.main()
