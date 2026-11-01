from pathlib import Path
import tempfile,unittest
from cadence import analyze_field_trace
class FieldTrace(unittest.TestCase):
 def run_trace(self,rows):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'trace.csv';p.write_text('\n'.join(','.join(map(str,r)) for r in rows))
   return analyze_field_trace(p)
 def test_regular_swaps_stale_fields_are_flagged(self):
  r=self.run_trace([(i*16666667,1+i//6,1,i) for i in range(61)])
  self.assertGreater(r['host_swap_hz'],59);self.assertLess(r['completed_stamp_change_hz'],11)
  self.assertIn('regular_host_loop_does_not_prove_smooth_game_delivery',r['findings'])
  self.assertIsNone(r['unique_pixel_fps']);self.assertFalse(r['gameplay_verified'])
 def test_generation_boundary_not_a_long_hold(self):
  r=self.run_trace([(0,1,1,1),(10000000,2,1,2),(1000000000,1,2,3),(1010000000,2,2,4)])
  self.assertIsNone(r['completed_hold_ms']);self.assertEqual(r['invalid_or_generation_boundaries'],1)
 def test_invalid_rows_not_silently_dropped(self):
  with self.assertRaises(ValueError):self.run_trace([(0,1,1,1),(0,2,1,2)])
  with self.assertRaises(ValueError):self.run_trace([(0,2,1,1),(1,1,1,2)])
