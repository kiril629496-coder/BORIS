import unittest
from pathlib import Path
class T(unittest.TestCase):
 def test_external_403_guard_present(self):
  s=Path('app/services/control_plane_adapters.py').read_text()
  self.assertIn('MOP_AVITO_CHAT_ACCESS_EXTERNAL',s)
  self.assertIn('"blind_retry":False',s)
  self.assertIn("'403' in str(x.get('send_error')",s)
if __name__=='__main__': unittest.main()
