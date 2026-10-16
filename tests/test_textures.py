import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from textures import enhance, resize, refine_detail, postblend_detail

class Textures(unittest.TestCase):
    def fixture(self, folder, role='world', wrap='repeat', alpha='smooth'):
        root=Path(folder);(root/'disc-root').mkdir();(root/'disc-root/TEST.ELF').write_bytes(b'synthetic')
        identity=hashlib.sha256(b'synthetic').hexdigest()
        profile=dict(id='synthetic',expected_executable='TEST.ELF',expected_sha256=identity)
        image=Image.new('RGBA',(4,4),(200,50,20,255));image.putpixel((0,0),(0,255,255,0));image.save(root/'input.png')
        spec=dict(schema=1,title_id='synthetic',executable_sha256=identity,textures=[dict(id='tile',file='input.png',sha256=hashlib.sha256((root/'input.png').read_bytes()).hexdigest(),role=role,scale=2,wrap=wrap,alpha=alpha)])
        (root/'textures.json').write_text(json.dumps(spec));return root,profile,spec
    def test_refinement_preserves_alpha_and_flat_color(self):
        image=Image.new('RGBA',(8,8),(60,90,120,128))
        self.assertEqual(refine_detail(image).tobytes(),image.tobytes())
        image.putpixel((3,3),(180,180,180,255))
        self.assertEqual(refine_detail(image).getchannel('A').tobytes(),image.getchannel('A').tobytes())
    def test_refinement_ignores_hidden_color(self):
        a=Image.new('RGBA',(8,8),(60,80,100,255));b=a.copy()
        for y in range(8):
            a.putpixel((0,y),(255,0,0,0));b.putpixel((0,y),(0,255,255,0))
        ra,rb=refine_detail(a),refine_detail(b)
        for y in range(8):
            for x in range(1,8):self.assertEqual(ra.getpixel((x,y)),rb.getpixel((x,y)))
    def test_periodic_refinement_is_shift_invariant(self):
        import numpy as np
        source=np.random.default_rng(5).integers(0,256,(8,8,4),dtype=np.uint8);source[:,:,3]=255
        a=np.asarray(refine_detail(Image.fromarray(source),'repeat'))
        b=np.asarray(refine_detail(Image.fromarray(np.roll(source,2,axis=1)),'repeat'))
        self.assertTrue(np.array_equal(np.roll(a,2,axis=1),b))
        self.assertLessEqual(int(np.abs(a.astype(int)-source.astype(int)).max()),18)
    def test_detail_profile_has_separate_verified_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder);first=enhance(profile,root,'textures.json','one')['textures'][0]
            spec['textures'][0]['detail']='adaptive';(root/'textures.json').write_text(json.dumps(spec))
            second=enhance(profile,root,'textures.json','two')['textures'][0]
            self.assertNotEqual(first['cache_key'],second['cache_key'])
            self.assertEqual(second['policy']['detail'],'adaptive')
    def test_postblend_preserves_alpha_flat_color_and_zero_amount(self):
        image=Image.new('RGBA',(12,12),(70,100,130,140))
        self.assertEqual(postblend_detail(image).tobytes(),image.tobytes())
        image.putpixel((5,5),(180,180,180,255))
        self.assertEqual(postblend_detail(image,amount=0).tobytes(),image.tobytes())
        self.assertEqual(postblend_detail(image).getchannel('A').tobytes(),image.getchannel('A').tobytes())
    def test_postblend_ignores_hidden_rgb_and_bounds_changes(self):
        import numpy as np
        source=np.random.default_rng(9).integers(0,256,(12,12,4),dtype=np.uint8);source[:,:,3]=255
        other=source.copy();source[:,0,:]=[255,0,0,0];other[:,0,:]=[0,255,0,0]
        a=np.asarray(postblend_detail(Image.fromarray(source)));b=np.asarray(postblend_detail(Image.fromarray(other)))
        self.assertTrue(np.array_equal(a[:,1:],b[:,1:]))
        self.assertLessEqual(int(np.abs(a.astype(int)-source.astype(int)).max()),12)
    def test_postblend_policy_is_reproducible(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder);spec['textures'][0]['detail']='postblend';(root/'textures.json').write_text(json.dumps(spec))
            first=enhance(profile,root,'textures.json','one')['textures'][0]
            second=enhance(profile,root,'textures.json','two')['textures'][0]
            self.assertEqual(first['levels'],second['levels']);self.assertTrue(second['cache_hit'])
    def test_explicit_unknown_resampling_keeps_review_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder,role='unclassified')
            spec['textures'][0]['unclassified_filter']='lanczos';(root/'textures.json').write_text(json.dumps(spec))
            entry=enhance(profile,root,'textures.json','one')['textures'][0]
            self.assertEqual(entry['levels'][0]['width'],8)
            self.assertEqual(entry['review'],'classification_required')
            self.assertEqual(entry['policy']['detail'],'none')
    def test_storage_reserve_stops_before_cache_write(self):
        from unittest.mock import patch
        from collections import namedtuple
        usage=namedtuple('usage','total used free')
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder)
            with patch('textures.shutil.disk_usage',return_value=usage(1024,1024,0)):
                with self.assertRaisesRegex(ValueError,'reserve'):enhance(profile,root,'textures.json','one')
            self.assertEqual(list((root/'texture-cache').iterdir()),[])
    def test_batch_mipmaps_and_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder); original=(root/'input.png').read_bytes()
            a=enhance(profile,root,'textures.json','one');b=enhance(profile,root,'textures.json','two')
            self.assertFalse(a['textures'][0]['cache_hit']);self.assertTrue(b['textures'][0]['cache_hit'])
            self.assertEqual([(x['width'],x['height']) for x in a['textures'][0]['levels']],[(8,8),(4,4),(2,2),(1,1)])
            self.assertEqual((root/'input.png').read_bytes(),original)
            self.assertEqual(a['runtime_integration'],'pending')
            with self.assertRaises(FileExistsError):enhance(profile,root,'textures.json','one')
    def test_transparent_color_does_not_bleed(self):
        image=Image.new('RGBA',(2,1));image.putdata([(255,0,0,255),(0,255,0,0)])
        result=resize(image,(8,4),'clamp','smooth')
        for r,g,b,a in result.getdata():
            if a: self.assertEqual(g,0)
    def test_cutout_alpha_stays_binary(self):
        image=Image.new('RGBA',(2,1));image.putdata([(255,0,0,255),(0,0,0,0)])
        self.assertEqual(set(resize(image,(8,4),'repeat','cutout').getchannel('A').getdata()),{0,255})
    def test_periodic_filter_uses_opposite_edge(self):
        image=Image.new('RGBA',(3,1));image.putdata([(255,0,0,255),(0,0,0,255),(0,0,255,255)])
        repeated=resize(image,(12,4),'repeat','smooth');clamped=resize(image,(12,4),'clamp','smooth')
        self.assertGreater(repeated.getpixel((0,0))[2],clamped.getpixel((0,0))[2])
    def test_text_and_ui_are_preserved(self):
        for role in ['text','ui','unclassified']:
            with tempfile.TemporaryDirectory() as folder:
                root,profile,spec=self.fixture(folder,role=role,wrap='clamp')
                entry=enhance(profile,root,'textures.json','one')['textures'][0]
                self.assertEqual(entry['policy']['scale'],1);self.assertEqual(len(entry['levels']),1)
                with Image.open(root/'texture-cache'/entry['cache_key']/'mip-00.png') as result:
                    with Image.open(root/'input.png') as original:self.assertEqual(result.tobytes(),original.tobytes())
    def test_ui_crisp_scaling_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder,role='text',wrap='clamp')
            spec['textures'][0]['ui_filter']='nearest';(root/'textures.json').write_text(json.dumps(spec))
            entry=enhance(profile,root,'textures.json','one')['textures'][0]
            with Image.open(root/'texture-cache'/entry['cache_key']/'mip-00.png') as result:
                with Image.open(root/'input.png') as original:
                    self.assertEqual(result.size,(8,8))
                    self.assertTrue(set(result.getdata()).issubset(set(original.getdata())))
            self.assertEqual(len(entry['levels']),1)
    def test_cache_missing_levels_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder);entry=enhance(profile,root,'textures.json','one')['textures'][0]
            path=root/'texture-cache'/entry['cache_key']/'entry.json';data=json.loads(path.read_text());data['levels']=[];path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'layout'):enhance(profile,root,'textures.json','two')
    def test_corrupt_cache_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder);entry=enhance(profile,root,'textures.json','one')['textures'][0]
            (root/'texture-cache'/entry['cache_key']/'mip-00.png').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'corruption'):enhance(profile,root,'textures.json','two')
            self.assertEqual(json.loads((root/'texture-runs/two/report.json').read_text())['status'],'failed')
    def test_wrong_revision_and_path_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder);spec['executable_sha256']='0'*64;(root/'textures.json').write_text(json.dumps(spec))
            with self.assertRaisesRegex(ValueError,'revision'):enhance(profile,root,'textures.json','one')
            spec['executable_sha256']=profile['expected_sha256'];spec['textures'][0]['file']='../outside.png';(root/'textures.json').write_text(json.dumps(spec))
            with self.assertRaises(ValueError):enhance(profile,root,'textures.json','two')
            self.assertFalse((root/'texture-runs').exists())
    def test_source_change_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder);(root/'input.png').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'hash'):enhance(profile,root,'textures.json','one')
            (root/'input.png').unlink();(root/'input.png').symlink_to(root/'disc-root/TEST.ELF')
            with self.assertRaises(ValueError):enhance(profile,root,'textures.json','two')
    def test_duplicate_identity_reuses_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root,profile,spec=self.fixture(folder);spec['textures'].append(dict(spec['textures'][0],id='second'));(root/'textures.json').write_text(json.dumps(spec))
            report=enhance(profile,root,'textures.json','one')
            self.assertEqual(report['textures'][0]['cache_key'],report['textures'][1]['cache_key'])
            self.assertTrue(report['textures'][1]['cache_hit'])
if __name__=='__main__':unittest.main()
