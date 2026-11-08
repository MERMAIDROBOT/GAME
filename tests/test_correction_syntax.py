from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import json,unittest,pipeline

class CorrectionSyntax(unittest.TestCase):
    def test_every_declared_edit_is_included_beyond_the_representative_sample(self):
        with TemporaryDirectory() as tmp:
            private=Path(tmp);generated=private/'generated';generated.mkdir();(private/'analysis').mkdir()
            for k in range(100):(generated/f'f{k:03d}.cpp').write_text('void f() {}\n')
            (private/'analysis/translation-stage.json').write_text(json.dumps(dict(accepted=True)))
            (private/'analysis/generated-correction-stage.json').write_text(json.dumps(dict(files=[dict(file='f003.cpp')])))
            visited=[]
            def syntax(command,**kwargs):
                visited.append(Path(command[-1]).name);return SimpleNamespace(returncode=0)
            with patch('generated_corrections.verify') as verify,patch('pipeline.syntax_check_command',return_value=['synthetic-clang']),patch('pipeline.subprocess.run',side_effect=syntax):
                pipeline.check_generated(dict(generated_source_corrections={'declared':True}),private)
            verify.assert_called_once();self.assertIn('f003.cpp',visited)
    def test_unapplied_corrections_block_before_compiler_execution(self):
        with TemporaryDirectory() as tmp:
            private=Path(tmp);(private/'generated').mkdir();(private/'generated/f.cpp').write_text('void f(){}')
            (private/'analysis').mkdir();(private/'analysis/translation-stage.json').write_text(json.dumps(dict(accepted=True)))
            with patch('pipeline.subprocess.run') as compiler:
                with self.assertRaisesRegex(ValueError,'Apply pinned title corrections'):
                    pipeline.check_generated(dict(generated_source_corrections={'declared':True}),private)
            compiler.assert_not_called()
