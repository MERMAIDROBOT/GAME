"""An app opened from Finder is its own privacy subject, so reading or writing ~/Documents waits on a permission
prompt (an unseen one leaves the launcher hanging before the game starts). With application_support_storage the
packaged app keeps its disc, log, graphics history and GPU cache under Application Support; the staged disc is an
APFS clone where possible, verified against the manifest either way."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

import native
import pipeline

ROOT = Path(__file__).resolve().parents[1]


class Content(unittest.TestCase):
    def test_package_paths_follow_storage(self):
        source = (ROOT / 'native.py').read_text()
        for fragment in ("'LogFile': str(storage / 'logs/finder-launch.log')",
                         "'GraphicsHistory': str(storage / 'logs/window-graphics-history.jsonl')",
                         "launch['Environment']['PS2_PORT_HW_GS_PIPELINE_CACHE_DIR'] = str(storage / 'cache/gpu-pipelines')",
                         "['/bin/cp', '-c', str(original), str(temporary)]"):
            self.assertIn(fragment, source)
        self.assertIn('tests/test_app_storage.py', pipeline.SOURCE_FILES)


class Behaviour(unittest.TestCase):
    def test_cd_image_staged_beside_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'private/cd-image/disc.iso'; image.parent.mkdir(parents=True)
            image.write_bytes(bytes(range(256)) * 32)
            staged = native.stage_cd_image(image, Path(tmp) / 'support/cd-image')
            self.assertEqual(staged.read_bytes(), image.read_bytes())
            self.assertTrue(staged.is_relative_to(Path(tmp).resolve() / 'support'))
            staged.write_bytes(b'short')                                       # wrong size: re-staged
            self.assertEqual(native.stage_cd_image(image, Path(tmp) / 'support/cd-image').read_bytes(), image.read_bytes())
        self.assertIn("image = stage_cd_image(image, storage / 'cd-image')", (ROOT / 'native.py').read_text())

    def test_staged_disc_matches_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp) / 'private'; disc = private / 'disc-root'; (disc / 'DATA').mkdir(parents=True)
            files = {'SYSTEM.CNF': b'BOOT2 = cdrom0:\\TEST_000.00;1\n', 'DATA/BLOB.BIN': bytes(range(256)) * 64}
            records = []
            for name, data in files.items():
                (disc / name).write_bytes(data)
                records.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), status='copied'))
            (private / 'analysis').mkdir()
            (private / 'disc-manifest.json').write_text(json.dumps(dict(files=records, complete=True, expected_file_count=len(records))))
            staged = native.stage_runtime_disc(private, Path(tmp) / 'support/disc-root')
            for name, data in files.items():
                self.assertEqual((staged / name).read_bytes(), data)
            self.assertEqual(staged.stat().st_mode & 0o777, 0o700)
            # A second staging keeps verified files and copies nothing.
            native.stage_runtime_disc(private, Path(tmp) / 'support/disc-root')
            record = json.loads((private / 'analysis/runtime-storage-stage.json').read_text())
            self.assertEqual(record['files_copied'], 0)
            # A corrupted staged file is replaced from the archive.
            (staged / 'DATA/BLOB.BIN').write_bytes(b'x')
            native.stage_runtime_disc(private, Path(tmp) / 'support/disc-root')
            self.assertEqual((staged / 'DATA/BLOB.BIN').read_bytes(), files['DATA/BLOB.BIN'])


if __name__ == '__main__':
    unittest.main()
