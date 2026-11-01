"""Capability declarations require the actual pinned runner compilation inputs."""
from pathlib import Path
import json,tempfile,unittest
import native_graph
from clean_build import sha
ROOT=Path(__file__).resolve().parents[1]
class RuntimeCapabilities(unittest.TestCase):
 def test_save_service_requires_pin_compiled_unit_and_exact_policy(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();src=root/'runtime.cpp';policy=(ROOT/'runtime/host_environment/storage_paths.hpp').read_text().removeprefix('#pragma once\n')
   src.write_text(policy+'\n'+ '\n'.join(f'{x}.mcRoot = ps2_port::memory_card_root({x}.elfDirectory);' for x in ('defaults','normalized','paths')))
   recipe=root/'recipe.json';data={'files':{'runtime.cpp':sha(src)},'source_contracts':[{'id':'native-separate-save-root','unit':'runtime','file':'runtime.cpp'}]};recipe.write_text(json.dumps(data))
   report={'source_contracts':['native-separate-save-root'],'dependencies':{'runtime':['runtime.cpp']}}
   self.assertEqual(native_graph.runtime_capabilities(recipe,report,['runtime']),{'separate_save_root':True})
   self.assertEqual(native_graph.runtime_capabilities(recipe,{},['runtime']),{})
   for bad,units in ((report,[]),({'source_contracts':['native-separate-save-root'],'dependencies':{}},['runtime'])):
    with self.assertRaises(ValueError):native_graph.runtime_capabilities(recipe,bad,units)
   src.write_text(src.read_text()+'// changed')
   with self.assertRaises(ValueError):native_graph.runtime_capabilities(recipe,report,['runtime'])
