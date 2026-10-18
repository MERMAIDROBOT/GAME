"""Synthetic checks for private vector-source boundaries and failed builds."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pipeline
from vectors import compile_vu,validated_sources

class VectorWorkflowTests(unittest.TestCase):
    def test_modified_extra_and_symlink_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            private=Path(folder);output=private/'generated-vu';output.mkdir()
            source=output/'vu1_programs.cpp';source.write_text('synthetic source')
            (output/'manifest.json').write_text(json.dumps({'sources':{source.name:pipeline.digest(source)}}))
            self.assertEqual(validated_sources(private)[0],output)
            source.write_text('altered source')
            with self.assertRaises(ValueError): validated_sources(private)
            source.write_text('synthetic source');extra=output/'extra.cpp';extra.write_text('extra')
            with self.assertRaises(ValueError): validated_sources(private)
            extra.unlink();outside=private/'outside.cpp';outside.write_text(source.read_text());source.unlink();source.symlink_to(outside)
            with self.assertRaises(ValueError): validated_sources(private)

    def test_successful_compile_is_private_and_resumes_without_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            private=Path(folder);capture=private/'capture';capture.mkdir()
            image=capture/'vu1_synthetic.bin';image.write_bytes(bytes(16384))
            compiler=private/'native-build/ps2PortVuRecompiler';compiler.parent.mkdir();compiler.write_text('synthetic compiler')
            def generate(command,log):
                target=Path(command[-1])
                (target/'vu1_programs.cpp').write_text('synthetic registry')
                (target/'vu1_synthetic.cpp').write_text('synthetic program')
                return 'success'
            with patch('vectors.native.logged',side_effect=generate) as runner:
                first=compile_vu({},private,capture)
                stamp=(private/'generated-vu/manifest.json').stat().st_mtime_ns
                second=compile_vu({},private,capture)
                self.assertEqual(first,second)
                self.assertEqual(runner.call_count,1)
                self.assertEqual((private/'generated-vu').stat().st_mode & 0o777,0o700)
                self.assertEqual((private/'generated-vu/manifest.json').stat().st_mtime_ns,stamp)
                self.assertFalse(first['analysis_implementation']['runtime_optimization_eligible'])
                report=private/'generated-vu/vu-dependencies.json'
                saved=report.read_bytes();report.write_text('{}')
                with self.assertRaisesRegex(ValueError,'dependency report'):
                    validated_sources(private)
                report.write_bytes(saved)
                with patch('vectors.deps.implementation',return_value={'revision':'changed'}):
                    with self.assertRaisesRegex(ValueError,'Archive'):
                        compile_vu({},private,capture)
                image.write_bytes(bytes([1])*16384)
                with self.assertRaisesRegex(ValueError,'Archive'):
                    compile_vu({},private,capture)
                self.assertEqual(runner.call_count,1)
                self.assertEqual(validated_sources(private)[1],first)

    def test_source_drift_during_compile_does_not_promote_output(self):
        with tempfile.TemporaryDirectory() as folder:
            private=Path(folder);capture=private/'capture';capture.mkdir()
            image=capture/'vu1_synthetic.bin';image.write_bytes(bytes(16384))
            compiler=private/'native-build/ps2PortVuRecompiler';compiler.parent.mkdir();compiler.write_text('synthetic compiler')
            def generate(command,log):
                target=Path(command[-1])
                (target/'vu1_programs.cpp').write_text('synthetic registry')
                (target/'vu1_synthetic.cpp').write_text('synthetic program')
                image.write_bytes(bytes([1])*16384)
                return 'success'
            with patch('vectors.native.logged',side_effect=generate):
                with self.assertRaisesRegex(ValueError,'changed during'):
                    compile_vu({},private,capture)
            self.assertFalse((private/'generated-vu').exists())
            self.assertEqual(list(private.glob('vu-compile-*')),[])

    def test_failed_compile_keeps_capture_and_does_not_promote_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            private=Path(folder);capture=private/'capture';capture.mkdir()
            image=capture/'vu1_synthetic.bin';image.write_bytes(bytes(16384));before=pipeline.digest(image)
            compiler=private/'native-build/ps2PortVuRecompiler';compiler.parent.mkdir();compiler.write_text('synthetic compiler')
            with patch('vectors.native.logged',side_effect=RuntimeError('compiler failed')):
                with self.assertRaisesRegex(RuntimeError,'compiler failed'):
                    compile_vu({},private,capture)
            self.assertFalse((private/'generated-vu').exists())
            self.assertEqual(list(private.glob('vu-compile-*')),[])
            self.assertEqual(pipeline.digest(image),before)
            with self.assertRaisesRegex(ValueError,'private game storage'):
                compile_vu({},private,private.parent)

if __name__=='__main__': unittest.main()
