import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import numpy as np
import sr_batch
import texture_seams as seams
from local_sr import digest


def texture():
    a=np.zeros((8,8,4),dtype='uint8');a[:,:,3]=255
    a[:,:,:3]=np.array([0,60,120,180,240,180,120,60])[None,:,None]
    return Image.fromarray(a)


class SeamTests(unittest.TestCase):
    def test_wrapped_context_and_crop_restore_exact_source(self):
        image=texture(); image.putpixel((0,0),(10,20,30,0))
        padded=seams.pad(image,['x','y'],4)
        self.assertEqual(padded.size,(16,16))
        self.assertEqual(padded.getpixel((3,4)),image.getpixel((7,0)))
        prediction=padded.resize((64,64),Image.Resampling.NEAREST)
        enhanced=seams.finish(prediction,image,['x','y'],4)
        self.assertEqual(enhanced.size,(32,32))
        self.assertEqual(enhanced.getchannel('A').tobytes(),seams.baseline(image,['x','y'],4).getchannel('A').tobytes())
        with self.assertRaises(ValueError): seams.finish(padded,image,['x'],4)

    def test_periodic_baseline_improves_clamped_seam_without_flattening_detail(self):
        image=texture()
        good=seams.baseline(image,['x'],16)
        bad=image.resize((32,32),Image.Resampling.LANCZOS)
        control=seams.check_pair(image,'right',image,'left',bad,bad)
        result=seams.check_pair(image,'right',image,'left',good,good)
        self.assertFalse(control['passed'])
        self.assertTrue(result['passed'])
        self.assertGreater(np.asarray(good)[:,:,:3].std(),40)

    def test_hidden_rgb_does_not_create_false_seam(self):
        a=Image.new('RGBA',(4,4),(255,0,0,0));b=Image.new('RGBA',(4,4),(0,255,255,0))
        self.assertEqual(seams.boundary(a,'left',b,'right')['boundary_step'],0)
        with self.assertRaises(ValueError): seams.boundary(a,'left',Image.new('RGBA',(4,5)),'right')

    def test_explicit_reversed_adjacency(self):
        a=texture();b=a.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        a.putpixel((7,0),(200,50,30,255));b.putpixel((0,7),(200,50,30,255))
        self.assertLess(seams.boundary(a,'right',b,'left',True)['boundary_step'],seams.boundary(a,'right',b,'left')['boundary_step'])

    def test_policy_and_single_tile_bounds(self):
        for entry in [dict(repeat_axes=['z']),dict(repeat_axes=['x','x']),dict(wrap_padding=1)]:
            with self.assertRaises(ValueError): seams.policy(entry)
        self.assertEqual(seams.tile_size([Image.new('RGBA',(288,288))]),512)
        with self.assertRaises(ValueError): seams.tile_size([Image.new('RGBA',(1025,1))])

    def fixture(self,root,modes=('full','detail-sensitive','preserve')):
        image=texture();image.save(root/'source.png')
        image.resize((32,32),Image.Resampling.LANCZOS).save(root/'base.png')
        entries=[dict(id=str(i)*64,source='source.png',source_sha256=digest(root/'source.png'),
                     baseline='base.png',baseline_sha256=digest(root/'base.png'),mode=mode,
                     reason='Synthetic periodic fixture',animation_family='water',
                     repeat_axes=['x'] if i==1 else []) for i,mode in enumerate(modes,1)]
        (root/'manifest.json').write_text(json.dumps(dict(schema=1,textures=entries)))
        (root/'lock.json').write_text('{}')
        return entries

    def test_family_wrap_blend_alpha_resume_and_unchanged_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); entries=self.fixture(root)
            hashes=[digest(root/'source.png'),digest(root/'base.png')]
            calls=[]
            def infer(lock,inputs,outputs,log,**kw):
                calls.append(kw)
                for p in inputs.iterdir():
                    with Image.open(p) as image:
                        self.assertEqual(image.size,(40,8))
                        image.resize((160,32),Image.Resampling.LANCZOS).save(outputs/p.name)
            with patch.object(sr_batch,'gpu_idle'),patch.object(sr_batch,'infer_directory',side_effect=infer),patch.object(sr_batch.shutil,'disk_usage',return_value=type('Space',(),{'free':10*1024**3})()):
                args=(root,root/'manifest.json',root/'lock.json',root/'pack')
                report=sr_batch.run(*args)
                self.assertEqual(report['seam_validation']['status'],'passed_declared_edges')
                self.assertEqual(len(report['seam_validation']['checks']),3)
                self.assertEqual(calls,[dict(tile=64)])
                for entry in report['textures']:
                    self.assertEqual(entry['sampling']['axes'],['x'])
                    with Image.open(root/entry['file']) as output:
                        self.assertEqual(output.size,(32,32))
                        self.assertEqual(output.getchannel('A').tobytes(),bytes([255])*1024)
                sr_batch.run(*args);self.assertEqual(len(calls),1)
            self.assertEqual([digest(root/'source.png'),digest(root/'base.png')],hashes)

    def test_bad_ai_seam_blocks_pack_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root,modes=('full',))
            def infer(lock,inputs,outputs,log,**kw):
                for p in inputs.iterdir():
                    image=Image.new('RGB',(160,32),(50,50,50))
                    # Crop begins at source padding*4 = 64.
                    for y in range(32): image.putpixel((64,y),(255,255,255))
                    image.save(outputs/p.name)
            with patch.object(sr_batch,'gpu_idle'),patch.object(sr_batch,'infer_directory',side_effect=infer),patch.object(sr_batch.shutil,'disk_usage',return_value=type('Space',(),{'free':10*1024**3})()):
                report=sr_batch.run(root,root/'manifest.json',root/'lock.json',root/'pack')
            self.assertEqual(report['status'],'blocked_seam_review')
            self.assertEqual(report['runtime_integration'],'pending')
            self.assertFalse(report['seam_validation']['undeclared_adjacencies_verified'])

    def test_undeclared_pairs_and_unknown_ids_rejected_before_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            path=root/'manifest.json';manifest=json.loads(path.read_text())
            manifest['seam_pairs']=[dict(a='missing',b='1'*64,edge_a='left',edge_b='right',evidence='synthetic')]
            path.write_text(json.dumps(manifest))
            with patch.object(sr_batch,'infer_directory') as infer:
                with self.assertRaises(ValueError): sr_batch.run(root,path,root/'lock.json',root/'pack')
                infer.assert_not_called()
            self.assertFalse((root/'pack').exists())

    def test_preserve_repeat_is_cpu_only_and_declared_adjacency_failure_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root,modes=('preserve',))
            with patch.object(sr_batch,'gpu_idle') as idle,patch.object(sr_batch,'infer_directory') as infer,patch.object(sr_batch.shutil,'disk_usage',return_value=type('Space',(),{'free':10*1024**3})()):
                report=sr_batch.run(root,root/'manifest.json',root/'lock.json',root/'pack')
                infer.assert_not_called();idle.assert_not_called()
                self.assertEqual(report['seam_validation']['status'],'passed_declared_edges')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entries=self.fixture(root,modes=('full','full'))
            for entry in entries: entry.pop('animation_family');entry['repeat_axes']=[]
            spec=dict(schema=1,textures=entries,seam_pairs=[dict(a=entries[0]['id'],b=entries[1]['id'],edge_a='bottom',edge_b='top',evidence='Synthetic adjacency fixture')])
            (root/'manifest.json').write_text(json.dumps(spec))
            def infer(lock,inputs,outputs,log):
                for p in inputs.iterdir():
                    shade=40 if p.name.startswith('1') else 160
                    Image.new('RGB',(32,32),(shade,shade,shade)).save(outputs/p.name)
            with patch.object(sr_batch,'gpu_idle'),patch.object(sr_batch,'infer_directory',side_effect=infer),patch.object(sr_batch.shutil,'disk_usage',return_value=type('Space',(),{'free':10*1024**3})()):
                report=sr_batch.run(root,root/'manifest.json',root/'lock.json',root/'pack')
            self.assertEqual(report['status'],'blocked_seam_review')
            self.assertEqual(report['seam_validation']['checks'][0]['kind'],'adjacency')
