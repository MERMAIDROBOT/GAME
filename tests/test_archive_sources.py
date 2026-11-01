from pathlib import Path
import hashlib,json,tempfile,unittest
from archive_sources import members,audit,compiler_records,discover_records

def archive_member(name,data):
 n=name.encode();payload=n+data
 h=(('#1/'+str(len(n))).ljust(16)+'0'.ljust(12)+'0'.ljust(6)+'0'.ljust(6)+'100644'.ljust(8)+str(len(payload)).ljust(10)+'`\n').encode()
 return h+payload+(b'\n' if len(payload)%2 else b'')
class Archives(unittest.TestCase):
 def test_duplicates_preserved_and_only_exact_bytes_match(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);a=p/'lib.a';a.write_bytes(b'!<arch>\n'+archive_member('a.o',b'first')+archive_member('a.o',b'second'))
   src=p/'a.cpp';src.write_text('int a;');obj=p/'a.o';obj.write_bytes(b'second')
   r=audit(a,[dict(object=str(obj),source=str(src))]);self.assertEqual(r['member_count'],2);self.assertEqual(r['exact_object_matches'],1)
   self.assertFalse(r['source_equivalence_verified']);self.assertFalse(r['clean_rebuild_ready'])
 def test_thin_truncated_and_path_members_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'a.a'
   for b in [b'!<thin>\n',b'!<arch>\nBAD',b'!<arch>\n'+archive_member('../a.o',b'x')]:
    p.write_bytes(b)
    with self.assertRaises(ValueError):members(p)
 def test_structured_compile_record_no_execution(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'a.cpp').write_text('int a;');(p/'a.o').write_bytes(b'object')
   record=p/'cmd.json';record.write_text(json.dumps(dict(cmd=['clang++','-c','a.cpp','-o','a.o'],cwd=t)))
   self.assertEqual(len(compiler_records([record])),1)

 def test_discovery_keeps_source_relationship_unverified(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'retained.o').write_bytes(b'object');(p/'a.cpp').write_text('int a;')
   archive=p/'lib.a';archive.write_bytes(b'!<arch>\n'+archive_member('a.cpp.o',b'object'))
   candidates=discover_records(archive,[p]);self.assertEqual(len(candidates),1)
   self.assertFalse(candidates[0]['equivalence_verified'])
   self.assertEqual(audit(archive,candidates)['exact_object_matches'],1)

 def test_conflicting_sources_are_flagged_not_arbitrarily_chosen(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);a=p/'lib.a';a.write_bytes(b'!<arch>\n'+archive_member('a.o',b'object'))
   obj=p/'a.o';obj.write_bytes(b'object');s1=p/'one.cpp';s1.write_text('int one;');s2=p/'two.cpp';s2.write_text('int two;')
   r=audit(a,[dict(object=str(obj),source=str(s)) for s in [s1,s2]])
   self.assertEqual(r['ambiguous_members'],['a.o']);self.assertFalse(r['source_equivalence_verified'])
