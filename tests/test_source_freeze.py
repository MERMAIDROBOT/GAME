from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pipeline as lab
from source_freeze import compile_arguments,freeze,remap


class SourceFreezeTests(unittest.TestCase):
    def test_reject_arbitrary_driver_and_plugin(self):
        entry=dict(arguments=['/tmp/c++','-o','x.o','-c','/tmp/x.cpp'],file='/tmp/x.cpp')
        with self.assertRaisesRegex(ValueError,'driver'): compile_arguments(entry,'/tmp/y.cpp','/tmp/y.o')
        compiler=shutil.which('clang++') or shutil.which('c++')
        if not compiler: self.skipTest('No compiler installed')
        entry['arguments']=[compiler,'-Xclang','-load','evil','-o','x.o','-c','/tmp/x.cpp']
        with self.assertRaisesRegex(ValueError,'plugin'): compile_arguments(entry,'/tmp/y.cpp','/tmp/y.o')

    def test_rewrite_includes_without_rewriting_outputs(self):
        project=Path('/tmp/work');snapshot=project/'game/run/snapshot'
        args=['-I/tmp/work/include','-iquote','/tmp/work/src','/tmp/work/src/a.cpp','-o','/tmp/work/game/run/probe.o']
        got=remap(args,project,snapshot)
        self.assertEqual(got[0],'-I'+str(snapshot/'include'))
        self.assertEqual(got[-1],args[-1])
        nested=remap(['-I/tmp/work/unused/../include'],project,snapshot)
        self.assertEqual(nested,['-I'+str(snapshot/'include')])

    def test_real_synthetic_compile_closure_and_reference_mismatch(self):
        compiler=shutil.which('clang++') or shutil.which('c++')
        if not compiler or not shutil.which('clang'):
            self.skipTest('Native source controls require Clang')
        with tempfile.TemporaryDirectory() as tmp:
            project=(Path(tmp)/'work').resolve();source=project/'src/a.cpp';source.parent.mkdir(parents=True)
            header=source.parent/'value.h';header.write_text('#define VALUE 42\n')
            source.write_text('#include "value.h"\nint value() { return VALUE; }\n')
            reference=project/'ref.o'
            arguments=[compiler,'-O1','-std=c++20','-o',str(reference),'-c',str(source)]
            subprocess.run(arguments,check=True,capture_output=True)
            db=project/'compile.json';db.write_text(json.dumps([dict(arguments=arguments,file=str(source),directory=str(project))]))
            private=project/'game'
            with patch.object(lab,'WORK',project):
                r=freeze({'id':'synthetic'},private,db,'a.cpp',source,reference,'good')
                self.assertEqual(r['status'],'passed')
                self.assertEqual(r['reached_original_project_files'],[])
                staged=[Path(p['staged']) for p in r['inputs'] if p['kind']=='project']
                self.assertTrue(all(p.is_relative_to(private/'compiler-runs/good/snapshot') for p in staged))
                header.write_text('#define VALUE 43\n')
                r=freeze({'id':'synthetic'},private,db,'a.cpp',source,reference,'changed')
                self.assertEqual(r['status'],'text_mismatch')
                self.assertFalse(r['probe_matches_reference'])
                self.assertTrue(r['text_matches'])
