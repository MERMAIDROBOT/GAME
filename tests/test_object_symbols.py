from pathlib import Path
import struct,tempfile,unittest
from object_symbols import fingerprints,differences

def macho(code=b'AAAABBBB'):
 header=struct.pack('<IiiIIIII',0xfeedfacf,0x100000c,0,1,2,176,0,0)
 segment=struct.pack('<II16sQQQQiiII',0x19,152,b'__TEXT',0,8,208,8,7,7,1,0)
 section=struct.pack('<16s16sQQIIIIIIII',b'__text',b'__TEXT',0,8,208,2,0,0,0x80000400,0,0,0)
 table=struct.pack('<IIIIII',2,24,216,2,248,7)
 symbols=struct.pack('<IBBHQ',1,0xf,1,0,0)+struct.pack('<IBBHQ',4,0xf,1,0,4)
 return header+segment+section+table+code+symbols+b'\0_a\0_b\0'
class ObjectSymbols(unittest.TestCase):
 def test_changed_function_is_identified_without_claiming_runtime_equivalence(self):
  with tempfile.TemporaryDirectory() as d:
   a=Path(d)/'a.o';b=Path(d)/'b.o';a.write_bytes(macho());b.write_bytes(macho(b'CCCCBBBB'))
   self.assertEqual(fingerprints(a)['_a']['bytes'],4)
   r=differences(a,b);self.assertEqual(r['matched_symbols'],1);self.assertEqual(r['changed'][0]['symbol'],'_a');self.assertEqual(r['added'],[])
 def test_bad_bounds_and_non_macho_input_fail(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a.o'
   for data in [b'BC\xc0\xde',macho()[:60],macho()[:-3]]:
    p.write_bytes(data)
    with self.assertRaises(ValueError):fingerprints(p)
