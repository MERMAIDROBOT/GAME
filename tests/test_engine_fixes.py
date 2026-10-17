"""Regressions for the 2026-10-17 engine review fixes (H1-H4, M2-M3, L1-L2).

Entry-point and next-game checks run real subprocesses of run.py/pipeline.py on
synthetic, asset-free inputs. No game data, native builds or GPU work.
"""
from pathlib import Path
import contextlib
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pipeline as lab
import next_game
from test_pipeline import minimal_elf

SUMMARY_OK = ('Functions processed: 3, recompiled: 2, stubs: 1, skipped: 0, decode failures: 0\n'
              'Warnings: 4, errors: 0\n')


def cli(*arguments, env=None, cwd=None):
    environment = {k: v for k, v in os.environ.items() if not k.startswith(('PS2_PORT_', 'PS2X_'))}
    environment.update(env or {})
    return subprocess.run([sys.executable, *arguments], cwd=cwd or ROOT, env=environment,
                          capture_output=True, text=True, timeout=120)


class SyntheticDisc:
    def __init__(self, root):
        self.root = Path(root)
        self.source = self.root / 'mounted'; self.source.mkdir()
        (self.source / 'SYSTEM.CNF').write_text('BOOT2 = cdrom0:\\TEST.ELF;1\n')
        (self.source / 'TEST.ELF').write_bytes(minimal_elf())
        self.private = self.root / 'private'
        self.profile = self.root / 'synthetic.json'
        lab.save(self.profile, dict(id='synthetic', title='Synthetic', private_dir=str(self.private),
                                    private_dir_absolute_opt_in=True))


class EntryPointSubprocessTests(unittest.TestCase):
    def test_non_object_profile_reports_a_clean_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.json'
            for value in [[], 'not a profile', None]:
                path.write_text(json.dumps(value))
                result = cli('run.py', '--profile', str(path), 'status')
                self.assertEqual(result.returncode, 1)
                self.assertIn('Profile must be a JSON object', result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_run_py_reports_errors_without_traceback(self):
        result = cli('run.py', 'status')
        self.assertEqual(result.returncode, 1)
        self.assertIn('ERROR:', result.stderr)
        self.assertIn('--profile', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        with tempfile.TemporaryDirectory() as directory:
            result = cli('run.py', '--profile', str(Path(directory) / 'missing.json'), 'status')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Profile not found', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_unknown_target_is_a_clean_cli_error(self):
        result = cli('run.py', '--target', 'no-such-target', 'doctor')
        self.assertEqual(result.returncode, 1)
        self.assertIn('ERROR:', result.stderr)
        self.assertIn('Unknown target', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_direct_pipeline_also_requires_an_explicit_profile(self):
        result = cli('pipeline.py', 'inspect')
        self.assertEqual(result.returncode, 1)
        self.assertIn('explicit per-game --profile', result.stderr)

    def test_help_reaches_the_target_command_list(self):
        result = cli('run.py', '--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ['next-game', 'new-profile', 'translate', '--target']:
            self.assertIn(command, result.stdout)
        result = cli('run.py', 'import', '--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('source', result.stdout)
        if platform.system() == 'Darwin':
            result = cli('run.py', '--target', 'macos', '--help')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('build-native', result.stdout)

    def test_existing_profile_cannot_be_reused_for_another_title(self):
        with tempfile.TemporaryDirectory() as directory:
            disc = SyntheticDisc(directory)
            result = cli('run.py', '--profile', str(disc.profile), 'next-game', '--title', 'Another game',
                         '--source', str(disc.source), '--name', 'first')
        self.assertEqual(result.returncode, 1)
        self.assertIn('belongs to', result.stderr)

    def test_new_profile_pins_revision_at_explicit_private_path(self):
        with tempfile.TemporaryDirectory() as directory:
            disc = SyntheticDisc(directory)
            output = Path(directory) / 'second-title.json'
            result = cli('run.py', 'new-profile', 'second-title', '--title', 'Second', '--source', str(disc.source),
                         '--output', str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads(output.read_text())
            self.assertEqual(profile['expected_sha256'], lab.digest(disc.source / 'TEST.ELF'))
            self.assertNotIn('private_dir', profile)
            result = cli('run.py', 'new-profile', 'other-id', '--title', 'X', '--output', str(output))
            self.assertEqual(result.returncode, 1)
            self.assertIn('<id>.json', result.stderr)

    def test_child_rejects_a_mismatched_work_root(self):
        with tempfile.TemporaryDirectory() as directory:
            disc = SyntheticDisc(directory)
            result = cli('pipeline.py', '--profile', str(disc.profile), 'status',
                         env={'PS2_PORT_LAB_WORK': str(Path(directory) / 'a'),
                              'PS2_PORT_LAB_EXPECTED_WORK': str(Path(directory) / 'b')})
        self.assertEqual(result.returncode, 1)
        self.assertIn('Work directory mismatch', result.stderr)


class NextGameSubprocessTests(unittest.TestCase):
    def test_real_child_stages_use_the_pinned_work_root(self):
        with tempfile.TemporaryDirectory() as directory:
            disc = SyntheticDisc(directory)
            work = Path(directory) / 'custom-work'
            # A different inherited value must not leak into the children either.
            with patch.object(lab, 'WORK', work), patch.dict(os.environ, {'PS2_PORT_LAB_WORK': str(Path(directory) / 'other')}), \
                    contextlib.redirect_stdout(io.StringIO()):
                report = next_game.run(disc.profile, 'real', source=disc.source, through='intake',
                                       execute=True, process_audit=False)
            self.assertEqual(report['status'], 'intake_complete_integration_required', report.get('ticket'))
            self.assertEqual(report['contract']['work'], str(work))
            logs = disc.private / 'next-game-runs/real'
            for command in ['import', 'verify', 'inspect', 'knowledge-plan']:
                text = (logs / f'{command}.log').read_text()
                self.assertIn(f'[pipeline] work={work.resolve()}', text, command)


class ProfilePolicyTests(unittest.TestCase):
    def test_default_and_relative_storage_stay_under_dot_private(self):
        self.assertEqual(lab.private_directory({'id': 'demo'}), (lab.ROOT / '.private/games/demo').resolve())
        self.assertEqual(lab.private_directory({'id': 'demo', 'private_dir': '.private/elsewhere/demo'}),
                         (lab.ROOT / '.private/elsewhere/demo').resolve())
        for value in ['../../work/demo', 'games/demo', '.private/../profiles']:
            with self.assertRaises(ValueError, msg=value):
                lab.private_directory({'id': 'demo', 'private_dir': value})

    def test_absolute_storage_needs_explicit_opt_in_and_cannot_hold_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'opt_in'):
                lab.private_directory({'id': 'demo', 'private_dir': directory})
            self.assertEqual(lab.private_directory({'id': 'demo', 'private_dir': directory,
                                                    'private_dir_absolute_opt_in': True}), Path(directory).resolve())
        for value in [str(lab.ROOT), str(lab.ROOT.parent), str(lab.ROOT / 'profiles'), '/']:
            with self.assertRaises(ValueError, msg=value):
                lab.private_directory({'id': 'demo', 'private_dir': value, 'private_dir_absolute_opt_in': True})

    def test_symlinked_private_root_outside_the_tree_needs_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'checkout'; root.mkdir()
            outside = Path(directory) / 'elsewhere'; (outside / 'games').mkdir(parents=True)
            (root / '.private').symlink_to(outside, target_is_directory=True)
            for profile in [{'id': 'demo'}, {'id': 'demo', 'private_dir': '.private/games/demo'}]:
                with self.assertRaisesRegex(ValueError, 'outside the source tree', msg=profile):
                    lab.private_directory(profile, root=root)
            explicit = {'id': 'demo', 'private_dir': str(outside / 'games/demo'), 'private_dir_absolute_opt_in': True}
            self.assertEqual(lab.private_directory(explicit, root=root), (outside / 'games/demo').resolve())
            # A symlink below a real .private is treated the same way.
            (root / '.private').unlink(); (root / '.private').mkdir()
            (root / '.private/games').symlink_to(outside / 'games', target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'outside the source tree'):
                lab.private_directory({'id': 'demo'}, root=root)
            # Redirecting .private into a public directory inside the checkout
            # is also unsafe: resolved writes would evade the .private ignore.
            (root / '.private/games').unlink(); (root / '.private').rmdir()
            public = root / 'profiles'; public.mkdir()
            (root / '.private').symlink_to(public, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'public source directory'):
                lab.private_directory({'id': 'demo'}, root=root)
            with self.assertRaises(ValueError):
                lab.private_directory({'id': 'demo', 'private_dir': str(public / 'demo'),
                                       'private_dir_absolute_opt_in': True}, root=root)

    def test_public_tree_ships_only_a_template_profile(self):
        self.assertEqual(sorted(p.name for p in (lab.ROOT / 'profiles').glob('*.json')), ['example-game.json'])
        self.assertIn('profiles/example-game.json', lab.SOURCE_FILES)
        self.assertNotIn('private_dir', json.loads((lab.ROOT / 'profiles/example-game.json').read_text()))


class TranslationAcceptanceTests(unittest.TestCase):
    def test_summary_parsing_fails_closed_and_reports_stubs_separately(self):
        ok = lab.translation_acceptance(SUMMARY_OK)
        self.assertTrue(ok['accepted'])
        self.assertEqual(ok['stub_functions'], 1)
        self.assertFalse(ok['stubs_are_implementations'])
        for text, reason in [('', 'function summary'), ('Functions processed: 1, recompiled: 1\n', 'function summary'),
                             ('Warnings: 0, errors: 0\n', 'function summary'),
                             (SUMMARY_OK.replace('decode failures: 0', 'decode failures: 2'), 'decode failures'),
                             (SUMMARY_OK.replace('errors: 0', 'errors: 1'), 'recompiler errors'),
                             (SUMMARY_OK.split('\n')[0], 'warning/error summary')]:
            result = lab.translation_acceptance(text)
            self.assertFalse(result['accepted'], text)
            self.assertTrue(any(reason in b for b in result['blockers']), (text, result['blockers']))

    def private(self, directory, log):
        private = Path(directory) / 'private'
        (private / 'disc-root').mkdir(parents=True)
        (private / 'disc-root/TEST.ELF').write_bytes(minimal_elf())
        (private / 'analysis').mkdir()
        (private / 'analysis/recomp.toml').write_text('[general]\n')
        def recompiler(command, log_path, offline=False, cwd=None):
            (private / 'generated').mkdir()
            (private / 'generated/a.cpp').write_text('void a() {}\n')
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text(log)
        return private, recompiler

    def test_unrecognized_summary_blocks_translate_and_every_downstream_stage(self):
        import native
        profile = {'id': 'synthetic', 'expected_executable': 'TEST.ELF'}
        with tempfile.TemporaryDirectory() as directory:
            private, recompiler = self.private(directory, 'tool finished\n')
            with patch.object(lab, 'run', recompiler), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'not accepted'):
                    lab.translate(profile, private)
            stage = json.loads((private / 'analysis/translation-stage.json').read_text())
            self.assertFalse(stage['accepted']); self.assertFalse(stage['success'])
            with self.assertRaisesRegex(ValueError, 'not accepted'):
                lab.check_generated(profile, private)
            with self.assertRaisesRegex(ValueError, 'not accepted'):
                native.build_native(profile, private)

    def test_accepted_translation_records_stub_count(self):
        profile = {'id': 'synthetic', 'expected_executable': 'TEST.ELF'}
        with tempfile.TemporaryDirectory() as directory:
            private, recompiler = self.private(directory, SUMMARY_OK)
            with patch.object(lab, 'run', recompiler), contextlib.redirect_stdout(io.StringIO()) as output:
                lab.translate(profile, private)
            stage = json.loads((private / 'analysis/translation-stage.json').read_text())
            self.assertTrue(stage['accepted'])
            self.assertEqual(stage['stub_functions'], 1)
            self.assertIn('not implementations', output.getvalue())

    def test_next_game_blocks_a_translation_without_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            disc = SyntheticDisc(directory)
            (disc.private / 'disc-root').mkdir(parents=True)
            for name in ['SYSTEM.CNF', 'TEST.ELF']:
                (disc.private / 'disc-root' / name).write_bytes((disc.source / name).read_bytes())
            def runner(command, private):
                artifact = dict(next_game.STAGES)[command]
                lab.save(private / artifact, {'success': True, 'counts': {}})
                if command == 'analyze': (private / 'analysis/recomp.toml').write_text('[general]\n')
                if command == 'translate':
                    (private / 'generated').mkdir(exist_ok=True); (private / 'generated/a.cpp').write_text('x')
            with patch.object(lab, 'WORK', Path(directory) / 'work'):
                report = next_game.run(disc.profile, 'first', through='translate', execute=True, runner=runner)
            self.assertEqual(report['status'], 'blocked')
            self.assertEqual(report['ticket']['subsystem'], 'translate')


class GeneratedSourceTests(unittest.TestCase):
    def test_nested_or_unsupported_generated_cpp_fails_closed_for_every_consumer(self):
        import knowledge
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / 'generated'
            (generated / 'sub').mkdir(parents=True)
            (generated / 'a.cpp').write_text('void a() {}\n')
            self.assertEqual([p.name for p in lab.generated_sources(generated)], ['a.cpp'])
            (generated / 'sub/b.cpp').write_text('void b() {}\n')
            with self.assertRaisesRegex(ValueError, 'Nested'):
                lab.generated_sources(generated)
            with self.assertRaisesRegex(ValueError, 'Nested'):
                knowledge.apply_translation(generated, write=False)
            (generated / 'sub/b.cpp').unlink()
            (generated / 'c.cc').write_text('void c() {}\n')
            with self.assertRaisesRegex(ValueError, 'non-.cpp'):
                lab.generated_sources(generated)

    def test_attach_game_cmake_matches_the_python_inventory_rule(self):
        text = (lab.ROOT / 'cmake/AttachGame.cmake').read_text()
        self.assertIn('GLOB_RECURSE all_generated_cpp', text)
        self.assertIn('Nested or non-.cpp generated C++ is not supported', text)


if __name__ == '__main__':
    unittest.main()
