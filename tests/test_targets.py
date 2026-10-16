import unittest
from target_cli import check_target


class Targets(unittest.TestCase):
    def test_cpp_export_is_platform_independent(self):
        for system in ['Linux','Windows','Darwin']:
            check_target('cpp','translate',system)
    def test_cpp_target_cannot_accidentally_package_or_launch_mac_game(self):
        for command in ['build-native','package-accepted-app','boot-probe']:
            with self.assertRaises(ValueError): check_target('cpp',command,'Linux')
    def test_mac_target_platform_gate(self):
        check_target('macos','build-native','Darwin')
        with self.assertRaises(ValueError): check_target('macos','build-native','Linux')
