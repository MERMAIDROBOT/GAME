"""Known native SDK entries survive late discovery; interior guest code stays real."""
import json
from pathlib import Path
import re
import struct
import tempfile
import unittest

import behaviour_check
import overlays
import pipeline


class DiscoveredSDKBindings(unittest.TestCase):
    @unittest.skipUnless((pipeline.BUILD / 'ps2xRecomp/ps2_recomp').is_file(), 'Local compiler required')
    def test_native_handler_and_guest_exceptions_at_late_entries(self):
        base = behaviour_check.CODE_BASE
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code = bytearray(0xc0)
            for offset, words in ((0, (0x03e00008, 0)),
                                  (0x40, (0x03e00008, 0)),
                                  (0x50, (0x24020007, 0x03e00008, 0)),
                                  (0x80, (0x24020009, 0x03e00008, 0)),
                                  (0xa0, (0x2402000b, 0x03e00008, 0))):
                struct.pack_into('<'+'I'*len(words), code, offset, *words)
            elf = root/'synthetic.elf';elf.write_bytes(overlays.wrap_elf(code,base))
            output=root/'generated';config=root/'config.toml'
            text=behaviour_check.CONFIG.format(input=json.dumps(str(elf)),output=json.dumps(str(output)))
            stubs=[f'sceGsExecStoreImage@0x{base+0x40:x}',f'sceSdRemote@0x{base+0x80:x}',
                   f'unrecognized_native_handler@0x{base+0xa0:x}']
            text=text.replace('stubs = []','stubs = '+json.dumps(stubs))
            text+='entry_points = '+json.dumps([f'late_{n}@0x{base+n:x}' for n in (0x40,0x50,0x80,0xa0)])+'\n'
            config.write_text(text)
            pipeline.run([pipeline.BUILD/'ps2xRecomp/ps2_recomp',config],root/'compiler.log',offline=True,cwd=root)
            table=(output/'register_functions.cpp').read_text()
            def body(offset):
                name=re.search(r'= (\w+); // 0x'+f'{base+offset:x}'+r'\b',table)
                self.assertIsNotNone(name,f'Unregistered late entry {offset:x}')
                return (output/(name[1]+'.cpp')).read_text()
            self.assertIn('ps2_stubs::sceGsExecStoreImage(',body(0x40))
            self.assertIn(f'// 0x{base+0x50:x}: 0x24020007',body(0x50))
            self.assertNotIn('ps2_stubs::sceGsExecStoreImage(',body(0x50))
            self.assertNotIn('ps2_stubs::sceSdRemote(',body(0x80))
            self.assertIn('0x24020009',body(0x80))
            self.assertIn('0x2402000b',body(0xa0))
