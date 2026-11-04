"""The patch that makes every R5900 link branch write $ra whether or not it is taken; no game needed."""
from pathlib import Path
import unittest

import behaviour_check as bc

ROOT = Path(__file__).resolve().parents[1]


class LinkBranchPatch(unittest.TestCase):
    def test_likely_link_branches_link_unconditionally(self):
        patch = (ROOT / 'patches/link-branch-reference.patch').read_text()
        added = [line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++')]
        removed = [line[1:] for line in patch.splitlines() if line.startswith('-') and not line.startswith('---')]
        self.assertIn('                m_branchInst.rt == REGIMM_BLTZALL || m_branchInst.rt == REGIMM_BGEZALL)', added)
        self.assertTrue(any('conditionalLinkCode = fmt::format' in line for line in removed))
        self.assertFalse(any('conditionalLinkCode = fmt::format' in line for line in added))
        import native
        self.assertIn('patches/link-branch-reference.patch', native.RUNTIME_PATCHES)

    def test_model_links_a_not_taken_likely_branch(self):
        s = bc.State([[0, 0] for _ in range(32)], 0, 0, 0, 0, bytes(bc.DATA_BYTES))
        s.gpr[1][0] = 1
        out = bc.expected('bltzall', dict(rs=1, start=0x100000), s)
        self.assertEqual(out.get(bc.LINK_COPY), 0x10000C)


if __name__ == '__main__':
    unittest.main()
