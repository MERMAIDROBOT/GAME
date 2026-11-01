from pathlib import Path
import json,shutil,tempfile,unittest
from abi_layouts import audit,parse_layout
from clean_build import sha

class AbiLayouts(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name);self.config=self.p/'abi.json';self.probes=[]
  for name,field in [('one','int'),('two','long long')]:
   root=self.p/name;root.mkdir();(root/'packet.h').write_text('struct Packet {'+field+' value;};\n');(root/'main.cpp').write_text('#include "packet.h"\n')
   recipe=root/'recipe.json';recipe.write_text(json.dumps(dict(schema=1,source_only=True,files={p:sha(root/p) for p in ['main.cpp','packet.h']},units=[dict(id='main',source='main.cpp',includes=[],include_paths=[dict(kind='-I',path='include')])],targets=[dict(id='core',kind='static',units=['main'])])))
   # Keep include search isolated from the recipe/receipt files themselves.
   inc=root/'include';inc.mkdir();(inc/'packet.h').write_bytes((root/'packet.h').read_bytes());r=json.loads(recipe.read_text());r['files']['include/packet.h']=sha(inc/'packet.h');recipe.write_text(json.dumps(r))
   self.probes.append(dict(id=name,recipe=str(recipe),recipe_sha256=sha(recipe),unit='main',headers=['packet.h']))
  self.save()
 def save(self):self.config.write_text(json.dumps(dict(schema=1,records=[dict(record='Packet',probes=self.probes)])))
 def test_layout_parser_requires_exact_named_record_and_completed_dump(self):
  output='*** Dumping AST Record Layout\n 0 | struct Packet\n 0 |   int value\n   | [sizeof=4, dsize=4, align=4,\n   |  nvsize=4, nvalign=4]\n'
  self.assertIn('sizeof=4',parse_layout(output,'Packet'))
  with self.assertRaises(ValueError):parse_layout(output,'Other')
 @unittest.skipUnless(shutil.which('clang++'),'Clang required')
 def test_actual_compiler_catches_cross_component_layout_drift(self):
  r=audit(self.config,self.p/'out',slot_check=False);self.assertEqual(r['status'],'abi_layouts_mismatch');self.assertFalse(r['equivalence_verified']);self.assertEqual(len(r['probes']),2)
 def test_header_injection_and_same_probe_not_accepted(self):
  self.probes[0]['headers']=['../outside.h'];self.save()
  with self.assertRaisesRegex(ValueError,'safe source header'):audit(self.config,self.p/'out',slot_check=False)
  self.probes[0]['headers']=['packet.h'];self.probes[1]=dict(self.probes[0],id='two');self.save()
  with self.assertRaisesRegex(ValueError,'independent source'):audit(self.config,self.p/'out',slot_check=False)

class SignatureSource(unittest.TestCase):
 def test_pointer_vs_value_and_noexcept_are_part_of_the_contract(self):
  from abi_layouts import probe_source
  spec=dict(function='gpu::Wait',returns='void*',parameters=['std::uint64_t*','int'],noexcept=True)
  text=probe_source(spec,['surface.h'])
  self.assertIn('void* (*)(std::uint64_t*, int) noexcept',text)
  self.assertIn('decltype(&::gpu::Wait)',text)
  self.assertIn('std::is_same_v',text)
  spec['parameters'][0]='std::uint64_t'
  self.assertNotEqual(text,probe_source(spec,['surface.h']))
 def test_signature_type_injection_is_rejected(self):
  from abi_layouts import probe_source
  for bad in ['int); system("bad"); //','decltype(execute())','int[3]','void\n#include "outside.h"']:
   with self.assertRaises(ValueError):probe_source(dict(function='Wait',returns=bad,parameters=[]),['surface.h'])
  with self.assertRaises(ValueError):probe_source(dict(function='Wait',returns='void',parameters=['void']),['surface.h'])

class FunctionAbiCompiler(unittest.TestCase):
 setUp=AbiLayouts.setUp
 save=AbiLayouts.save
 @unittest.skipUnless(shutil.which('clang++'),'Clang required')
 def test_actual_compiler_rejects_pointer_value_signature_drift(self):
  for index,probe in enumerate(self.probes):
   root=Path(probe['recipe']).parent
   (root/'include/packet.h').write_text('namespace gpu { void* Wait(unsigned '+('int*' if index==0 else 'int')+'); }\n')
   recipe=json.loads(Path(probe['recipe']).read_text());recipe['files']['include/packet.h']=sha(root/'include/packet.h')
   Path(probe['recipe']).write_text(json.dumps(recipe));probe['recipe_sha256']=sha(Path(probe['recipe']))
  self.config.write_text(json.dumps(dict(schema=1,functions=[dict(function='gpu::Wait',returns='void*',parameters=['unsigned int*'],probes=self.probes)])))
  import subprocess
  with self.assertRaises(subprocess.CalledProcessError):audit(self.config,self.p/'function-out',slot_check=False)
  receipt=json.loads((self.p/'function-out/receipt.json').read_text())
  self.assertEqual(receipt['status'],'failed')
  self.assertIn('Shared function signature differs',(self.p/'function-out/two.log').read_text())
