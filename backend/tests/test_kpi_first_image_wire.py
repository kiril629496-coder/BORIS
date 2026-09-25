import unittest
from pathlib import Path
SRC=Path('/root/BORIS/backend/app/api/avito.py').read_text()
class FirstImageWire(unittest.TestCase):
 def test_planner_action_is_wired_to_prepare_only(self):
  self.assertIn('KPI_GOAL_FIRST_IMAGE_PREPARE_WIRE_V1',SRC)
  self.assertIn('x.get("action") == "test_first_image"',SRC)
  self.assertIn('"stage": "first_image_prepare"',SRC)
  self.assertIn('result = kpi_prepare_cycle(',SRC)
if __name__=='__main__': unittest.main()
