import os, tempfile, unittest
from PIL import Image
from app.api.avito import uniquify_image

class UniquifyContentIdentityTests(unittest.TestCase):
    def _write(self,path,color): Image.new('RGB',(80,60),color).save(path,'PNG')
    def test_same_source_is_stable_and_reused(self):
        with tempfile.TemporaryDirectory() as d:
            src=os.path.join(d,'hero.png'); self._write(src,(10,20,30))
            a=uniquify_image(src); p=os.path.join(d,a); m=os.stat(p).st_mtime_ns
            b=uniquify_image(src)
            self.assertEqual(a,b); self.assertEqual(m,os.stat(p).st_mtime_ns)
    def test_replaced_content_same_path_gets_new_derivative(self):
        with tempfile.TemporaryDirectory() as d:
            src=os.path.join(d,'hero.png'); self._write(src,(10,20,30)); a=uniquify_image(src)
            self._write(src,(220,30,40)); b=uniquify_image(src)
            self.assertNotEqual(a,b); self.assertTrue(os.path.isfile(os.path.join(d,b)))
    def test_output_is_deterministic_after_cache_loss(self):
        with tempfile.TemporaryDirectory() as d:
            src=os.path.join(d,'hero.png'); self._write(src,(50,100,150)); a=uniquify_image(src); pa=os.path.join(d,a)
            with open(pa,'rb') as f: first=f.read()
            os.remove(pa)
            b=uniquify_image(src)
            with open(os.path.join(d,b),'rb') as f: second=f.read()
            self.assertEqual(a,b); self.assertEqual(first,second)
if __name__=='__main__': unittest.main()
