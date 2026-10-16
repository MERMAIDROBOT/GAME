from pathlib import Path
import tempfile
import unittest
import pipeline as lab
import enhancements as e
from test_pipeline import minimal_elf


class EnhancementEngineTests(unittest.TestCase):
    def test_graph_includes_every_requested_graphics_and_completion_stage(self):
        spec=e.load();ids={s['id'] for s in spec['stages']}
        self.assertTrue({'surfers-boards-and-skin-variants','fsr1-easu-presentation',
            'adaptive-supersampling','local-ai-and-detail-blending',
            'source-and-rebuild-preservation','approved-duplicate-cleanup'}.issubset(ids))
        self.assertEqual(spec['policy']['simulation_speed_multiplier'],1.0)
        self.assertFalse(spec['policy']['frame_generation'])
    def test_old_artifact_is_not_a_pass_and_changed_input_invalidates_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'disc-root').mkdir()
            (root/'disc-root/TEST.ELF').write_bytes(minimal_elf())
            profile=dict(id='test',expected_executable='TEST.ELF')
            lab.save(root/'verification.json',{'success':True})
            self.assertEqual(e.schedule(profile,root)['completed'],0)
            e.record_stage(profile,root,'disc-integrity','verification.json')
            self.assertEqual(e.schedule(profile,root)['completed'],1)
            lab.save(root/'verification.json',{'success':True,'changed':True})
            self.assertEqual(e.schedule(profile,root)['completed'],0)
    def test_dependency_evidence_change_blocks_descendants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'disc-root').mkdir()
            (root/'disc-root/TEST.ELF').write_bytes(minimal_elf())
            profile=dict(id='test',expected_executable='TEST.ELF')
            lab.save(root/'verification.json',{'passed':1})
            lab.save(root/'analysis/elf.json',{'passed':1})
            e.record_stage(profile,root,'disc-integrity','verification.json')
            e.record_stage(profile,root,'binary-inspection','analysis/elf.json')
            self.assertEqual(e.schedule(profile,root)['completed'],2)
            lab.save(root/'verification.json',{'passed':2})
            report=e.schedule(profile,root)
            self.assertEqual(report['completed'],0)
            self.assertFalse(report['all_steps_qualified'])
