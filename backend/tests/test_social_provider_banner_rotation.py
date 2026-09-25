import json, os, tempfile, unittest
from unittest.mock import patch
import posting_runner as p

class _Q:
    def __init__(self,row): self.row=row
    def filter(self,*a,**k): return self
    def first(self): return self.row
class _DB:
    def __init__(self,row): self.row=row
    def query(self,*a,**k): return _Q(self.row)
    def close(self): pass
class _R:
    def __init__(self,value): self.value=value

class SocialProviderBannerRotationTests(unittest.TestCase):
    def test_same_theme_is_idempotent_and_different_themes_rotate(self):
        with tempfile.TemporaryDirectory() as td:
            posts=[]
            for i in range(8):
                path=os.path.join(td,f'b{i}.png')
                with open(path,'wb') as f: f.write(b'x'*50001)
                posts.append({'project_id':'p','status':'published','banner':path,'created_at':f'2026-09-{i+1:02d}','id':f'r{i}'})
            row=_R(json.dumps(posts))
            with patch.object(p,'SessionLocal',return_value=_DB(row)):
                a=p._social_reuse_published_banner('u','p','theme-a')
                b=p._social_reuse_published_banner('u','p','theme-a')
                self.assertEqual(a['path'],b['path'])
                self.assertEqual(a['rotation_pool_size'],8)
                picks={p._social_reuse_published_banner('u','p',f'theme-{i}')['path'] for i in range(20)}
                self.assertGreater(len(picks),1)

    def test_deduplicates_same_asset_path(self):
        with tempfile.TemporaryDirectory() as td:
            path=os.path.join(td,'same.png')
            with open(path,'wb') as f: f.write(b'x'*50001)
            posts=[{'project_id':'p','status':'published','banner':path,'created_at':f'2026-09-0{i+1}','id':f'r{i}'} for i in range(3)]
            with patch.object(p,'SessionLocal',return_value=_DB(_R(json.dumps(posts)))):
                out=p._social_reuse_published_banner('u','p','x')
                self.assertEqual(out['rotation_pool_size'],1)
