import contextlib
import io
import unittest
from unittest.mock import patch
import run


class EntryPointTests(unittest.TestCase):
    def test_default_cpp_forwards_profile_and_exit_status(self):
        with patch.object(run, 'dispatch', return_value=3) as dispatch:
            self.assertEqual(run.main(['--profile', 'local.json', 'translate']), 3)
            dispatch.assert_called_once_with('cpp', ['--profile', 'local.json', 'translate'])

    def test_optional_mac_forwarding_and_discovery(self):
        with patch.object(run, 'dispatch', return_value=0) as dispatch:
            run.main(['--target', 'macos', 'doctor'])
            dispatch.assert_called_once_with('macos', ['doctor'])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run.main(['--list-targets']), 0)
        self.assertIn('macos', output.getvalue())
        self.assertIn('cpp', output.getvalue())

    def test_unknown_target_fails_without_launch(self):
        with patch.object(run.subprocess, 'run') as execute:
            with self.assertRaisesRegex(ValueError, 'Unknown'): run.dispatch('unknown', [])
            execute.assert_not_called()

    def test_cpp_cannot_bypass_native_target_gate(self):
        self.assertNotEqual(run.dispatch('cpp', ['package-local']), 0)
