"""A recognized table is an optimization, not proof of all runtime JR targets."""
from pathlib import Path
import json
import re
import struct
import tempfile
import unittest

import behaviour_check
import overlays
import pipeline


class MutableJumpTable(unittest.TestCase):
    @unittest.skipUnless((pipeline.BUILD/'ps2xRecomp/ps2_recomp').is_file(), 'Local compiler required')
    def test_recognized_table_keeps_other_decoded_cases_callable(self):
        base = behaviour_check.CODE_BASE
        # Both initial entries name A. A writable table may later select B.
        # B is real decoded code, but is absent from the static table snapshot.
        words = [0x27bdfff0, 0x2c880002, 0x1100000d, 0x3c090010,
                 0x00044080, 0x25290080, 0x01094021, 0x8d0a0000,
                 0x01400008, 0, 0x2402000b, 0x08040010, 0,
                 0x24020016, 0x08040010, 0, 0x27bd0010, 0x03e00008, 0]
        raw = bytearray(0xa0)
        struct.pack_into('<'+str(len(words))+'I', raw, 0, *words)
        struct.pack_into('<2I', raw, 0x80, base+0x28, base+0x28)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); elf=root/'synthetic.elf'; output=root/'generated'; config=root/'config.toml'
            elf.write_bytes(overlays.wrap_elf(raw,base,[(0,0x4c)]))
            # Pin the synthetic function boundary, so the pointer to A does not
            # split this one function into inferred subfunctions at its cases.
            functions=root/'functions.csv'
            functions.write_text(f'Name,Start,End,Size\nfixture,0x{base:x},0x{base+0x4c:x},76\n')
            text=behaviour_check.CONFIG.format(input=json.dumps(str(elf)),output=json.dumps(str(output)))
            config.write_text(text.replace('ghidra_output = ""','ghidra_output = '+json.dumps(str(functions))))
            pipeline.run([pipeline.BUILD/'ps2xRecomp/ps2_recomp',config],root/'compiler.log',offline=True,cwd=root)
            table=(output/'register_functions.cpp').read_text()
            at=base+0x34
            owner=re.search(r'= (\w+); // 0x'+f'{at:x}'+r'\b',table)
            self.assertIsNotNone(owner,'Recognized table omitted a real runtime-selectable case')
            body=(output/(owner[1]+'.cpp')).read_text()
            self.assertIn(f'case 0x{at:x}u: goto label_{at:x};',body)
            self.assertIn('0x24020016',body)
            # Table storage is data, not a fallback instruction entry.
            self.assertNotRegex(table,r'// 0x'+f'{base+0x80:x}'+r'\b')
