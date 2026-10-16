import hashlib
from pathlib import Path
import tempfile
import unittest

from PIL import Image
import local_sr


class LocalSRTests(unittest.TestCase):
    def test_selected_blend_and_endpoints_preserve_alpha(self):
        base = Image.new('RGBA', (2, 2), (100, 100, 100, 127))
        inferred = Image.new('RGBA', (2, 2), (200, 0, 100, 127))
        self.assertEqual(local_sr.blend_upscale(base, inferred).getpixel((0, 0)), (130, 70, 100, 127))
        self.assertEqual(local_sr.blend_upscale(base, inferred, 0).tobytes(), base.tobytes())
        self.assertEqual(local_sr.blend_upscale(base, inferred, 1).tobytes(), inferred.tobytes())
        for invalid in (-1, 2, float('nan'), float('inf'), True, '0.3'):
            with self.assertRaises(ValueError):
                local_sr.blend_upscale(base, inferred, invalid)
        with self.assertRaises(ValueError):
            local_sr.blend_upscale(base, Image.new('RGBA', (1, 1)))
        with self.assertRaises(ValueError):
            local_sr.blend_upscale(base, Image.new('RGBA', (2, 2)))

    def test_model_does_not_receive_hidden_rgb(self):
        a = Image.new('RGBA', (2, 2), (255, 0, 0, 0))
        b = Image.new('RGBA', (2, 2), (0, 255, 0, 0))
        self.assertEqual(local_sr.prepare(a).tobytes(), local_sr.prepare(b).tobytes())

    def test_alpha_is_source_resampling_not_model_prediction(self):
        original = Image.new('RGBA', (2, 2), (12, 34, 56, 0))
        original.putpixel((0, 0), (12, 34, 56, 255))
        predicted = Image.new('RGB', (8, 8), (200, 100, 50))
        result = local_sr.combine(predicted, original)
        self.assertEqual(result.getchannel('A').tobytes(),
                         original.getchannel('A').resize((8, 8), Image.Resampling.LANCZOS).tobytes())
        with self.assertRaises(ValueError):
            local_sr.combine(Image.new('RGB', (4, 4)), original)

    def test_tool_and_model_pins_reject_mutation_and_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'tool'
            path.write_bytes(b'original')
            item = {'file': 'tool', 'sha256': hashlib.sha256(b'original').hexdigest()}
            self.assertEqual(local_sr.pinned_file(root, item), path.resolve())
            path.write_bytes(b'modified')
            with self.assertRaises(ValueError):
                local_sr.pinned_file(root, item)
            item['file'] = '../tool'
            with self.assertRaises(ValueError):
                local_sr.pinned_file(root, item)


if __name__ == '__main__':
    unittest.main()
