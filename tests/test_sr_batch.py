import json
from pathlib import Path
import tempfile
import unittest
import os
from unittest.mock import patch
from PIL import Image
from local_sr import digest
import sr_batch


class BatchTests(unittest.TestCase):
    def test_abrupt_exit_after_png_before_index_recovers_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            Image.new('RGBA', (4, 4), (100, 100, 100, 255)).save(root / 'source.png')
            Image.new('RGBA', (16, 16), (100, 100, 100, 255)).save(root / 'base.png')
            entry = dict(id='a'*64, source='source.png', source_sha256=digest(root/'source.png'),
                         baseline='base.png', baseline_sha256=digest(root/'base.png'),
                         mode='full', reason='Synthetic crash fixture')
            (root/'manifest.json').write_text(json.dumps(dict(schema=1,textures=[entry])))
            (root/'lock.json').write_text('{}')
            def inference(lock, inputs, outputs, log):
                log.write_text('Synthetic inference')
                for file in inputs.iterdir():
                    Image.new('RGB',(16,16),(200,0,100)).save(outputs/file.name)
            args=(root,root/'manifest.json',root/'lock.json',root/'pack')
            real_save=sr_batch.save_json
            def crash(path,value):
                if value['textures']:
                    os._exit(77)
                real_save(path,value)
            with patch.object(sr_batch,'gpu_idle'), patch.object(sr_batch,'infer_directory',side_effect=inference), patch.object(sr_batch.shutil,'disk_usage',return_value=type('Space',(),{'free':10*1024**3})()):
                pid=os.fork()
                if pid==0:
                    with patch.object(sr_batch,'save_json',side_effect=crash):
                        sr_batch.run(*args)
                    os._exit(78)
                _,status=os.waitpid(pid,0)
                self.assertEqual(os.waitstatus_to_exitcode(status),77)
                file=root/'pack'/('a'*64+'.png')
                orphan_hash=digest(file)
                self.assertEqual(json.loads((root/'pack/pack-index.json').read_text())['textures'],[])
                report=sr_batch.run(*args)
                self.assertEqual(report['status'],'processed_requires_visual_review')
                saved=list((root/'pack/orphaned').glob('*.png'))
                self.assertEqual(len(saved),1)
                self.assertEqual(digest(saved[0]),orphan_hash)
                self.assertEqual(digest(file),orphan_hash)

    def test_conditional_policy(self):
        self.assertEqual(sr_batch.policy_weight('full'), 1)
        self.assertEqual(sr_batch.policy_weight('detail-sensitive'), .3)
        self.assertEqual(sr_batch.policy_weight('preserve'), 0)
        with self.assertRaises(ValueError):
            sr_batch.policy_weight('unknown')

    def test_pack_resume_and_corruption_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            Image.new('RGBA', (4, 4), (100, 100, 100, 255)).save(root / 'source.png')
            Image.new('RGBA', (16, 16), (100, 100, 100, 255)).save(root / 'base.png')
            entries = [{'id': str(i) * 64, 'source': 'source.png', 'source_sha256': digest(root / 'source.png'),
                        'baseline': 'base.png', 'baseline_sha256': digest(root / 'base.png'),
                        'mode': mode, 'reason': 'Synthetic fixture'}
                       for i, mode in enumerate(('full', 'detail-sensitive', 'preserve'), 1)]
            (root / 'manifest.json').write_text(json.dumps({'schema': 1, 'textures': entries}))
            (root / 'lock.json').write_text('{}')
            def inference(lock, inputs, outputs, log):
                log.write_text('Synthetic test; no GPU execution')
                for file in inputs.iterdir():
                    Image.new('RGB', (16, 16), (200, 0, 100)).save(outputs / file.name)
            args = (root, root / 'manifest.json', root / 'lock.json', root / 'pack')
            with patch.object(sr_batch, 'gpu_idle'), patch.object(sr_batch, 'infer_directory', side_effect=inference) as infer, patch.object(sr_batch.shutil, 'disk_usage', return_value=type('Space', (), {'free': 10 * 1024**3})()):
                report = sr_batch.run(*args)
                self.assertEqual(len(report['textures']), 3)
                self.assertEqual(infer.call_count, 1)
                with Image.open(root / 'pack' / ('2' * 64 + '.png')) as result:
                    self.assertEqual(result.getpixel((0, 0)), (130, 70, 100, 255))
                sr_batch.run(*args)
                self.assertEqual(infer.call_count, 1)
                (root / 'pack' / ('2' * 64 + '.png')).write_bytes(b'corrupt')
                with self.assertRaises(ValueError):
                    sr_batch.run(*args)


if __name__ == '__main__':
    unittest.main()
