import unittest
from unittest.mock import patch
import production_contract_runner as c
class Resp:
 def __init__(self,status): self.status=status
 def __enter__(self): return self
 def __exit__(self,*a): return False
class T(unittest.TestCase):
 def test_all(self):
  with patch("urllib.request.urlopen",side_effect=[Resp(200),Resp(200),Resp(200)]): h=c._live_runtime_health_state()
  self.assertTrue(h["all_ok"])
 def test_502(self):
  from urllib.error import HTTPError
  e=HTTPError("https://boris-ai.pro/health",502,"bad",None,None)
  with patch("urllib.request.urlopen",side_effect=[Resp(200),Resp(200),e]): h=c._live_runtime_health_state()
  self.assertFalse(h["all_ok"]); self.assertEqual(h["public"]["status"],502)
 def test_replica_down(self):
  from urllib.error import URLError
  with patch("urllib.request.urlopen",side_effect=[Resp(200),URLError("down"),Resp(200)]): h=c._live_runtime_health_state()
  self.assertFalse(h["all_ok"])
