from pathlib import Path
import struct,tempfile,unittest,wave
import audio_evidence,port_checks
class AudioEvidence(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def wav(self,name='clip.wav',samples=None):
  p=self.root/name;samples=[0]*8000 if samples is None else samples
  with wave.open(str(p),'wb') as w:w.setparams((1,2,8000,0,'NONE','not compressed'));w.writeframes(struct.pack('<'+'h'*len(samples),*samples))
  return p
 def test_expected_silent_scene_fails_qualification(self):
  p=self.wav();r=port_checks.audit(expected_audio_wavs=[p]);self.assertFalse(r['structural_checks_passed']);self.assertEqual(r['findings'][0]['code'],'expected_audio_entirely_silent')
 def test_nonzero_capture_is_not_speech_music_or_gameplay_proof(self):
  p=self.wav(samples=[100,-100]*4000);r=port_checks.audit(expected_audio_wavs=[p]);self.assertTrue(r['structural_checks_passed']);self.assertFalse(r['gameplay_verified']);a=r['metrics']['captured_audio'][0];self.assertEqual(a['rms'],100);self.assertFalse(a['quality_verified']);self.assertFalse(a['speech_verified']);self.assertFalse(a['music_verified'])
 def test_truncated_pcm_is_rejected(self):
  p=self.wav();p.write_bytes(p.read_bytes()[:-4])
  with self.assertRaisesRegex(ValueError,'Truncated'):audio_evidence.inspect(p)
 def test_symlink_is_rejected(self):
  p=self.wav();link=self.root/'link.wav';link.symlink_to(p)
  with self.assertRaises(ValueError):audio_evidence.inspect(link)
 def test_expected_capture_list_is_explicit_and_bounded(self):
  for value in ([],['x']*17,'x'):
   with self.assertRaises(ValueError):port_checks.audit(expected_audio_wavs=value)
 def test_short_clip_and_unsupported_width_rejected(self):
  with self.assertRaises(ValueError):audio_evidence.inspect(self.wav(samples=[0]*10))
  p=self.root/'8bit.wav'
  with wave.open(str(p),'wb') as w:w.setparams((1,1,8000,0,'NONE','not compressed'));w.writeframes(b'\x80'*8000)
  with self.assertRaises(ValueError):audio_evidence.inspect(p)
