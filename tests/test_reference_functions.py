from pathlib import Path
import hashlib,json,tempfile,unittest
from reference_functions import load,apply,audit


class ReferenceFunctions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'source.bin').write_bytes(b'own original bytes')
        self.rows=self.root/'functions.tsv';self.rows.write_text('# metadata\nf\tshared\t16\tfingerprint\t1\t00:00100000\tunit\n')
        self.module=self.root/'module.json';self.module.write_text(json.dumps(dict(index=0,entry=0x100000,
            sections=[dict(addr=0x100000,size=64,type=1,flags=6)])))
        pin=lambda p:dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        self.profile={'reference_functions':dict(format='module-placements-tsv-v1',
            source=dict(path='source.bin',sha256=hashlib.sha256((self.root/'source.bin').read_bytes()).hexdigest()),
            placements=pin(self.rows),modules=[pin(self.module)])}
        self.image=dict(base=0x100000,spans=[(0,64)],layout=dict(entry=0x100000,
            segments=[dict(address=0x100000,bytes=64,zero_fill=False,executable=True)]))
        self.config=self.root/'config.toml';self.config.write_text('[general]\nghidra_output = ""\ninput="untouched"\n')

    def test_verified_map_drives_compiler_configuration_and_entry_gate(self):
        reference=load(self.profile,self.root);record=apply(reference,self.image,self.config,self.root)
        self.assertTrue(record['matched']);self.assertEqual(record['functions'],1)
        self.assertIn('reference_00100000,0x100000,0x100010,16',(self.root/'reference-functions.csv').read_text())
        self.assertIn('input="untouched"',self.config.read_text())
        table=self.root/'register_functions.cpp';table.write_text('// fake entry 0x100000\n')
        self.assertFalse(audit(reference,self.image,self.root)['passed'])
        table.write_text('table[0] = reference_00100000; // 0x100000\n')
        self.assertTrue(audit(reference,self.image,self.root)['passed'])

    def test_foreign_source_cannot_supply_function_addresses(self):
        (self.root/'source.bin').write_bytes(b'other bytes')
        with self.assertRaisesRegex(ValueError,'source image hash'):load(self.profile,self.root)

    def test_bad_reference_does_not_erase_an_existing_translation(self):
        import overlays
        (self.root/'overlays').mkdir();old=self.root/'overlays/retained.cpp';old.write_text('existing native source')
        (self.root/'analysis').mkdir();(self.root/'analysis/code-census.json').write_text('{"regions":[]}')
        (self.root/'source.bin').write_bytes(b'other bytes');self.profile['id']='synthetic'
        with self.assertRaisesRegex(ValueError,'source image hash'):
            overlays.translate_overlays(self.profile,self.root)
        self.assertEqual(old.read_text(),'existing native source')

    def test_changed_reference_metadata_is_rejected(self):
        self.rows.write_text('changed')
        with self.assertRaisesRegex(ValueError,'metadata hash'):load(self.profile,self.root)

    def test_an_unmatched_module_is_reported_without_injecting_a_map(self):
        reference=load(self.profile,self.root);self.image['layout']['entry']+=4
        self.assertFalse(apply(reference,self.image,self.config,self.root)['matched'])
        self.assertFalse((self.root/'reference-functions.csv').exists())

    def test_reference_boundaries_cannot_escape_actual_code(self):
        reference=load(self.profile,self.root);reference['functions'][0][0x100000]=128
        with self.assertRaisesRegex(ValueError,'outside verified'):apply(reference,self.image,self.config,self.root)

    def test_existing_external_map_is_preserved(self):
        reference=load(self.profile,self.root);self.config.write_text('[general]\nghidra_output="existing.csv"\n')
        with self.assertRaisesRegex(ValueError,'existing function map'):apply(reference,self.image,self.config,self.root)

    def test_missing_whole_module_cannot_report_complete_reference_coverage(self):
        from reference_functions import audit_port
        from unittest.mock import patch
        (self.root/'analysis').mkdir();(self.root/'analysis/code-census.json').write_text('{}')
        with patch('overlays.candidates',return_value=([],[])):
            report=audit_port(self.profile,self.root)
        self.assertFalse(report['passed']);self.assertEqual(report['missing_reference_modules'],[0])

    def test_conflicting_sizes_for_one_entry_are_not_guessed(self):
        self.rows.write_text(self.rows.read_text()+'other\tshared\t32\tfingerprint\t1\t00:00100000\tunit\n')
        self.profile['reference_functions']['placements']['sha256']=hashlib.sha256(self.rows.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'Conflicting reference'):load(self.profile,self.root)
