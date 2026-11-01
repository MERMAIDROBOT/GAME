"""A/B regression harness: synthetic labs, discs and runners only, never game data."""
from pathlib import Path
from unittest import mock
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ab_check
import pipeline


def tree_digests(root):
    root = Path(root)
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'dir')
            for p in sorted(root.rglob('*'))}


def ppm(width, height, value):
    return f'P6\n{width} {height}\n255\n'.encode() + bytes([value]) * (width * height * 3)


def make_title(root):
    """Synthetic title private dir: disc with ELF, data, memory cards and a recomp.toml."""
    private = Path(root) / 'title-private'
    disc = private / 'disc-root'
    (disc / 'DATA').mkdir(parents=True)
    (disc / 'SYSTEM.CNF').write_text('BOOT2 = cdrom0:\\GAME.ELF;1\n')
    (disc / 'GAME.ELF').write_bytes(b'\x7fELF synthetic')
    (disc / 'DATA/level.bin').write_bytes(b'level')
    (disc / 'mc0/BASAVE').mkdir(parents=True)
    (disc / 'mc0/BASAVE/save.bin').write_bytes(b'real save')
    (disc / 'mc1').mkdir()
    (private / 'analysis').mkdir()
    (private / 'analysis/recomp.toml').write_text(
        '[general]\n'
        f'input = "{disc / "GAME.ELF"}"\n'
        f'output = "{private / "generated"}"\n'
        f'ghidra_output = "{private / "ghidra.csv"}"\n'
        'single_file_output = false\n')
    (private / 'generated').mkdir()
    (private / 'generated/a.cpp').write_text('// title translation\n')
    (private / 'profile').mkdir()
    (private / 'profile/title.patch').write_text('--- a/x\n+++ b/x\n')
    profile = {'id': 'synthetic', 'title': 'Synthetic', 'expected_executable': 'GAME.ELF',
               'private_dir': str(private), 'private_dir_absolute_opt_in': True,
               'private_source_patches': [{'file': 'profile/title.patch', 'sha256': 'x', 'reason': 'synthetic'}]}
    path = Path(root) / 'synthetic.json'
    path.write_text(json.dumps(profile))
    return profile, private, path


class StagingTests(unittest.TestCase):
    def test_disc_root_copies_elf_and_saves_and_links_only_game_data(self):
        with tempfile.TemporaryDirectory() as directory:
            _, private, _ = make_title(directory)
            destination = Path(directory) / 'side/disc-root'
            record = ab_check.stage_disc_root(private / 'disc-root', destination, 'GAME.ELF')
            self.assertFalse((destination / 'GAME.ELF').is_symlink())
            self.assertEqual((destination / 'GAME.ELF').read_bytes(), b'\x7fELF synthetic')
            self.assertTrue((destination / 'DATA').is_symlink())
            self.assertEqual((destination / 'DATA/level.bin').read_bytes(), b'level')
            for card in ('mc0', 'mc1'):
                self.assertFalse((destination / card).is_symlink())
            (destination / 'mc0/BASAVE/save.bin').write_bytes(b'overwritten by a run')
            (destination / 'mc0/new.bin').write_bytes(b'new save')
            self.assertEqual((private / 'disc-root/mc0/BASAVE/save.bin').read_bytes(), b'real save')
            self.assertFalse((private / 'disc-root/mc0/new.bin').exists())
            self.assertEqual(sorted(record['memory_cards']), ['mc0', 'mc1'])

    def test_nested_elf_gets_real_directories_and_fresh_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'disc'
            (source / 'BIN').mkdir(parents=True)
            (source / 'BIN/GAME.ELF').write_bytes(b'elf')
            (source / 'BIN/OTHER.IRX').write_bytes(b'irx')
            (source / 'MOVIES').mkdir()
            destination = Path(directory) / 'side'
            ab_check.stage_disc_root(source, destination, 'BIN/GAME.ELF')
            self.assertFalse((destination / 'BIN').is_symlink())
            self.assertTrue((destination / 'BIN/OTHER.IRX').is_symlink())
            self.assertTrue((destination / 'MOVIES').is_symlink())
            for card in ('mc0', 'mc1'):
                self.assertTrue((destination / 'BIN' / card).is_dir())
                self.assertFalse((destination / 'BIN' / card).is_symlink())
            self.assertFalse((source / 'BIN/mc0').exists())

    def test_disc_root_rejects_unsafe_elf_path(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                ab_check.stage_disc_root(directory, Path(directory) / 'x', '../GAME.ELF')

    def test_recomp_config_points_only_at_the_side(self):
        title, side = Path('/title/private'), Path('/ab/candidate/private')
        text = ('[general]\ninput = "/title/private/disc-root/GAME.ELF"\n'
                'ghidra_output = "/title/private/ghidra.csv"\n')
        result = ab_check.rewrite_recomp_config(text, title, side, side / 'disc-root/GAME.ELF', side / 'generated')
        self.assertNotIn('/title/private', result)
        self.assertIn('input = "/ab/candidate/private/disc-root/GAME.ELF"', result)
        self.assertIn('output = "/ab/candidate/private/generated"', result)
        self.assertIn('ghidra_output = "/ab/candidate/private/ghidra.csv"', result)
        with self.assertRaisesRegex(ValueError, 'general'):
            ab_check.rewrite_recomp_config('x = 1\n', title, side, side / 'e', side / 'g')

    def test_stage_private_isolates_and_leaves_title_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, private, _ = make_title(directory)
            references = private / 'logs/scenarios/ref'
            references.mkdir(parents=True)
            (references / 'frame.ppm').write_bytes(ppm(2, 2, 200))
            scenario = private / 'scene.json'
            scenario.write_text(json.dumps({'visual_steps': [{'reference': 'logs/scenarios/ref/frame.ppm',
                                                              'reference_alternatives': []}]}))
            before = tree_digests(private)
            side = Path(directory) / 'ab/candidate'
            side.mkdir(parents=True)
            ab_check.stage_private(profile, private, side, 'candidate', Path('GAME.ELF'), scenario)
            self.assertEqual(tree_digests(private), before)
            staged = side / 'private'
            self.assertEqual(ab_check.isolation_problems(staged, private, 'GAME.ELF'), [])
            self.assertTrue((staged / 'profile/title.patch').is_file())
            self.assertTrue((staged / 'logs/scenarios/ref/frame.ppm').is_file())
            self.assertTrue((staged / 'ab-scenario.json').is_file())
            self.assertFalse((staged / 'generated').exists())
            side_profile, side_private = pipeline.load_profile(side / 'profile.json')
            self.assertEqual(side_private, staged.resolve())
            self.assertEqual(side_profile['id'], 'synthetic-ab-candidate')

    def test_isolation_check_catches_linked_memory_card_and_title_output(self):
        with tempfile.TemporaryDirectory() as directory:
            profile, private, _ = make_title(directory)
            side = Path(directory) / 'ab/baseline'
            side.mkdir(parents=True)
            ab_check.stage_private(profile, private, side, 'baseline', Path('GAME.ELF'))
            staged = side / 'private'
            import shutil
            shutil.rmtree(staged / 'disc-root/mc0')
            (staged / 'disc-root/mc0').symlink_to(private / 'disc-root/mc0')
            (staged / 'analysis/recomp.toml').write_text(f'[general]\noutput = "{private / "generated"}"\n')
            problems = ab_check.isolation_problems(staged, private, 'GAME.ELF')
            self.assertTrue(any('mc0' in p for p in problems))
            self.assertTrue(any('output' in p for p in problems))

    def test_copy_lab_skips_private_data_and_overlays_stack(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = Path(directory) / 'lab'
            (lab / 'patches').mkdir(parents=True)
            (lab / '.private/games').mkdir(parents=True)
            (lab / '__pycache__').mkdir()
            for name in ('pipeline.py', 'native.py'):
                (lab / name).write_text('')
            (lab / 'knowledge.json').write_text('{"revision": 2}')
            (lab / 'patches/runtime-a.patch').write_text('new a')
            (lab / 'patches/runtime-b.patch').write_text('new b')
            recorded = Path(directory) / 'applied-stack'
            recorded.mkdir()
            (recorded / '00-runtime-a.patch').write_text('old a')
            old_bank = Path(directory) / 'old-knowledge.json'
            old_bank.write_text('{"revision": 1}')
            record = ab_check.copy_lab(lab, Path(directory) / 'copy', recorded, old_bank)
            copy = Path(directory) / 'copy'
            self.assertFalse((copy / '.private').exists())
            self.assertFalse((copy / '__pycache__').exists())
            self.assertEqual((copy / 'patches/runtime-a.patch').read_text(), 'old a')
            self.assertEqual((copy / 'patches/runtime-b.patch').read_text(), 'new b')
            self.assertEqual((copy / 'knowledge.json').read_text(), '{"revision": 1}')
            self.assertEqual(set(record['patches']), {'runtime-a.patch', 'runtime-b.patch'})
            self.assertEqual((lab / 'patches/runtime-a.patch').read_text(), 'new a')
            with self.assertRaisesRegex(ValueError, 'Not a PS2 Port Lab'):
                ab_check.copy_lab(Path(directory) / 'applied-stack', Path(directory) / 'copy2')

    def test_work_root_clones_pinned_revision_and_links_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory) / 'shared'
            source = shared / 'dependencies/PS2Recomp'
            source.mkdir(parents=True)
            git = ['git', '-C', str(source), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid']
            subprocess.run(['git', 'init', '--quiet', str(source)], check=True)
            (source / 'file.txt').write_text('pinned')
            subprocess.run([*git, 'add', '.'], check=True)
            subprocess.run([*git, 'commit', '--quiet', '-m', 'pinned'], check=True)
            revision = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
            (source / 'file.txt').write_text('shared checkout has applied patches')
            (shared / 'toolchain/bin').mkdir(parents=True)
            work = Path(directory) / 'side-work'
            record = ab_check.prepare_work(work, shared, source, revision)
            self.assertEqual(record['ps2recomp_revision'], revision)
            self.assertEqual((work / 'dependencies/PS2Recomp/file.txt').read_text(), 'pinned')
            self.assertTrue((work / 'toolchain').is_symlink())
            self.assertFalse((work / 'runtime-deps').exists())


class EvidenceTests(unittest.TestCase):
    def test_frame_image_detection(self):
        self.assertTrue(ab_check.frame_has_image(ppm(4, 4, 120)))
        self.assertFalse(ab_check.frame_has_image(ppm(4, 4, 0)))
        self.assertFalse(ab_check.frame_has_image(b'P6\n4 4\n255\nshort'))
        self.assertFalse(ab_check.frame_has_image(b'not a frame'))

    def test_boot_gate_flags_out_of_memory_exit(self):
        gate = ab_check.classify_boot('[ee] SetupHeap\nout of memory\n', 1, 30, 2.4, 0)
        self.assertEqual(gate['status'], 'early_exit')
        self.assertEqual(gate['fatal_marker'], 'out of memory')
        self.assertFalse(gate['passed'])

    def test_boot_gate_states(self):
        benign = '[ps2xIOP] IOP MEMORY   max allocatable   %12lu\n'
        self.assertEqual(ab_check.classify_boot(benign, None, 30, 30, 3)['status'], 'passed')
        self.assertEqual(ab_check.classify_boot(benign, None, 30, 30, 0)['status'], 'no_image')
        self.assertEqual(ab_check.classify_boot('std::bad_alloc', None, 30, 30, 3)['status'], 'fatal_marker')
        self.assertEqual(ab_check.classify_boot(benign, 0, 30, 4, 3)['status'], 'early_exit')

    def test_comparison_verdicts(self):
        results = {'baseline': {'finished': True, 'checks': {'build': {'status': 'passed'}, 'boot': {'status': 'passed'},
                                                            'vu-test': {'status': 'failed'}}},
                   'candidate': {'finished': True, 'checks': {'build': {'status': 'passed'}, 'boot': {'status': 'early_exit'},
                                                             'vu-test': {'status': 'passed'}}}}
        report = ab_check.compare(results)
        self.assertEqual(report['status'], 'regression')
        self.assertEqual(report['regressions'], ['boot'])
        self.assertEqual(report['improvements'], ['vu-test'])
        results['candidate']['checks']['boot'] = {'status': 'passed'}
        self.assertEqual(ab_check.compare(results)['status'], 'no_regression')
        self.assertEqual(ab_check.compare({'baseline': results['baseline'], 'candidate': None})['status'], 'regression')
        results['candidate'] = {'finished': False, 'checks': {}}
        results['baseline'] = {'finished': True, 'checks': {}}
        self.assertEqual(ab_check.compare(results)['status'], 'incomplete')


FAKE_PIPELINE = '''
from pathlib import Path
import hashlib, json, os
ROOT = Path(__file__).resolve().parent
WORK = Path(os.environ['PS2_PORT_LAB_WORK']).resolve()
SOURCE = WORK / 'dependencies/PS2Recomp'
BUILD = WORK / 'build/ps2recomp'
ENV = WORK / 'toolchain'
LOCK = json.loads((ROOT / 'toolchain.lock.json').read_text())
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True); Path(path).write_text(json.dumps(value))
def load_profile(path):
    profile = json.loads(Path(path).read_text()); return profile, Path(profile['private_dir']).resolve()
def boot_executable(root, profile): return Path(root) / profile['expected_executable']
def translate(profile, private):
    import re
    config = (private / 'analysis/recomp.toml').read_text()
    output = Path(re.search(r'output = "([^"]*)"', config)[1])
    output.mkdir(); (output / 'a.cpp').write_text('// side translation')
    save(private / 'analysis/translation-stage.json', {'counts': {'stubs': 0}})
'''

FAKE_NATIVE = '''
import contextlib, os, sys
from pathlib import Path
import pipeline as lab
RUNTIME_PATCHES = ('patches/stack.patch',)
MODE = (lab.ROOT / 'mode.txt').read_text().strip()
RUNNER = {
  'good': "import os, pathlib, time\\npathlib.Path(os.environ['PS2_PORT_FRAME']).write_bytes(b'P6\\\\n2 2\\\\n255\\\\n' + bytes([200]) * 12)\\npathlib.Path('mc0/written-by-run.bin').write_bytes(b'x')\\nprint('[ps2xIOP] IOP MEMORY max allocatable', flush=True)\\ntime.sleep(60)\\n",
  'bad': "import sys\\nprint('out of memory', flush=True)\\nsys.exit(1)\\n"}
def runtime_environment(): return dict(os.environ)
def apply_runtime_patch(): (lab.SOURCE / 'APPLIED').write_text(MODE)
def common_configure(build): return ['cmake', '-S', lab.SOURCE, '-B', build]
def logged(command, log, cwd=None, timeout=None, allow_failure=False, env_patch=None):
    command = [str(c) for c in command]
    Path(log).parent.mkdir(parents=True, exist_ok=True); Path(log).write_text(' '.join(command))
    if '--build' in command:
        runner = Path(command[command.index('--build') + 1]) / 'ps2xRuntime/ps2EntryRunner'
        runner.parent.mkdir(parents=True, exist_ok=True)
        runner.write_text('#!' + sys.executable + '\\n' + RUNNER[MODE]); runner.chmod(0o755)
    return 'success'
@contextlib.contextmanager
def private_patches_applied(profile, private): yield []
'''


class EndToEndTests(unittest.TestCase):
    def test_candidate_out_of_memory_exit_is_a_regression_and_title_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, private, profile_path = make_title(root)
            shared = root / 'shared-work'
            source = shared / 'dependencies/PS2Recomp'
            source.mkdir(parents=True)
            git = ['git', '-C', str(source), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid']
            subprocess.run(['git', 'init', '--quiet', str(source)], check=True)
            (source / 'README').write_text('tool')
            subprocess.run([*git, 'add', '.'], check=True)
            subprocess.run([*git, 'commit', '--quiet', '-m', 'pin'], check=True)
            revision = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
            labs = {}
            for mode in ('good', 'bad'):
                lab = root / f'lab-{mode}'
                (lab / 'patches').mkdir(parents=True)
                (lab / 'pipeline.py').write_text(textwrap.dedent(FAKE_PIPELINE))
                (lab / 'native.py').write_text(textwrap.dedent(FAKE_NATIVE))
                (lab / 'mode.txt').write_text(mode)
                (lab / 'knowledge.json').write_text('{}')
                (lab / 'patches/stack.patch').write_text(mode)
                (lab / 'toolchain.lock.json').write_text(json.dumps({'ps2recomp': {'revision': revision},
                                                                     'resolved_dependencies': {}}))
                labs[mode] = lab
            before = tree_digests(private)
            with mock.patch.object(pipeline, 'WORK', shared), mock.patch.object(pipeline, 'SOURCE', source), \
                    mock.patch.object(pipeline, 'BUILD', shared / 'build/ps2recomp'), \
                    mock.patch.object(ab_check, 'busy_runners', return_value=[]), \
                    mock.patch('builtins.print'):
                report = ab_check.run_ab_check(profile_path, 'synthetic-ab', labs['good'], labs['bad'],
                                               boot_seconds=5, output=root / 'out')
            out = root / 'out'
            self.assertEqual(report['comparison']['status'], 'regression', (out / 'baseline/worker.log').read_text()
                             + (out / 'candidate/worker.log').read_text())
            self.assertEqual(report['comparison']['regressions'], ['boot'])
            baseline, candidate = report['results']['baseline'], report['results']['candidate']
            self.assertEqual(baseline['checks']['boot']['status'], 'passed')
            self.assertEqual(candidate['checks']['boot']['status'], 'early_exit')
            self.assertEqual(candidate['checks']['boot']['fatal_marker'], 'out of memory')
            self.assertEqual(baseline['lab_root'], str((out / 'baseline/lab').resolve()))
            self.assertEqual((out / 'candidate/work/dependencies/PS2Recomp/APPLIED').read_text(), 'bad')
            self.assertTrue((out / 'baseline/private/generated/a.cpp').is_file())
            self.assertTrue((out / 'baseline/private/disc-root/mc0/written-by-run.bin').is_file())
            self.assertEqual(tree_digests(private), before)
            self.assertFalse((source / 'APPLIED').exists())
            self.assertIn('| boot | passed | early_exit | regression |', (out / 'report.md').read_text())

    def test_refuses_identical_stacks_busy_runner_and_output_in_title(self):
        with tempfile.TemporaryDirectory() as directory:
            _, private, profile_path = make_title(directory)
            with mock.patch.object(ab_check, 'busy_runners', return_value=[]):
                with self.assertRaisesRegex(ValueError, 'same stack'):
                    ab_check.run_ab_check(profile_path, 'same', output=Path(directory) / 'out')
                with self.assertRaisesRegex(ValueError, 'outside'):
                    ab_check.run_ab_check(profile_path, 'inside', baseline_patches=directory,
                                          output=private / 'ab')
            with mock.patch.object(ab_check, 'busy_runners', return_value=[4242]):
                with self.assertRaisesRegex(RuntimeError, 'benchmarking'):
                    ab_check.run_ab_check(profile_path, 'busy', baseline_patches=directory)

    def test_registered_as_public_source_and_macos_command(self):
        self.assertIn('ab_check.py', pipeline.SOURCE_FILES)
        self.assertIn('tests/test_ab_check.py', pipeline.SOURCE_FILES)
        from target_cli import check_target
        with self.assertRaises(ValueError):
            check_target('cpp', 'ab-check')
        check_target('macos', 'ab-check', 'Darwin')


if __name__ == '__main__':
    unittest.main()
