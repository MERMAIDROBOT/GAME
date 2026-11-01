from pathlib import Path
import hashlib,os,tempfile,unittest
from unittest.mock import patch
import source_inventory as inventory

class SourceInventory(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.inc=self.root/'inc';self.inc.mkdir();(self.inc/'nested').mkdir();(self.inc/'main.cpp').write_text('int main(){return 0;}\n');(self.inc/'nested/space name.h').write_text('#define VALUE 7\n');self.files={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.inc.rglob('*') if p.is_file()};self.companions=set(self.files)|{'inc','inc/nested'}
 def verify(self):inventory.verify_files(self.root,self.files);inventory.audit_include(self.root,'inc',self.files,self.companions)
 def test_descriptor_and_fallback_validate_identical_contents(self):
  self.verify()
  with patch.object(inventory,'descriptor_reads',return_value=False):self.verify()
 def test_same_size_same_timestamp_change_is_not_a_cache_hit(self):
  p=self.inc/'main.cpp';before=p.stat();p.write_text('int main(){return 1;}\n');os.utime(p,ns=(before.st_atime_ns,before.st_mtime_ns))
  with self.assertRaisesRegex(ValueError,'changed source'):self.verify()
 def test_symlinked_source_is_rejected(self):
  p=self.inc/'main.cpp';data=p.read_bytes();p.unlink();outside=self.root/'outside';outside.write_bytes(data);p.symlink_to(outside)
  with self.assertRaises(ValueError):self.verify()
 def test_symlinked_parent_is_rejected_even_with_matching_bytes(self):
  original=self.inc/'nested';original.rename(self.root/'outside');original.symlink_to(self.root/'outside',target_is_directory=True)
  with self.assertRaises(ValueError):self.verify()
 def test_escaping_input_is_rejected(self):
  self.files['../outside']='a'*64
  with self.assertRaises(ValueError):self.verify()
 def test_compiled_magic_cannot_hide_in_a_header(self):
  for magic in inventory.MAGICS:
   with self.subTest(magic=magic):
    p=self.inc/'main.cpp';p.write_bytes(magic+b'not source');self.files['inc/main.cpp']=hashlib.sha256(p.read_bytes()).hexdigest()
    with self.assertRaisesRegex(ValueError,'compiled'):self.verify()
 def test_compiled_suffix_rejected_even_with_text_contents(self):
  p=self.inc/'hidden.o';p.write_text('text');self.files['inc/hidden.o']=hashlib.sha256(p.read_bytes()).hexdigest()
  with self.assertRaisesRegex(ValueError,'compiled'):self.verify()
 def test_known_appledouble_metadata_allowed_unknown_files_rejected(self):
  for name in ['._main.cpp','._nested']:(self.inc/name).write_bytes(inventory.APPLEDOUBLE+b'metadata')
  self.verify()
  (self.inc/'._main.cpp').write_bytes(b'unknown header bytes')
  with self.assertRaisesRegex(ValueError,'Unpinned'):self.verify()
 def test_pinned_appledouble_sidecar_is_metadata_not_pinned_source(self):
  side=self.inc/'._main.cpp';side.write_bytes(inventory.APPLEDOUBLE+b'provenance A'.ljust(24,b'\0'))
  self.files['inc/._main.cpp']=hashlib.sha256(side.read_bytes()).hexdigest();self.companions.add('inc/._main.cpp')
  side.write_bytes(inventory.APPLEDOUBLE+b'provenance B'.ljust(24,b'\0'))
  self.verify()
  with patch.object(inventory,'descriptor_reads',return_value=False):self.verify()
  side.write_text('int injected;\n')
  with self.assertRaisesRegex(ValueError,'changed source'):self.verify()
  with patch.object(inventory,'descriptor_reads',return_value=False),self.assertRaisesRegex(ValueError,'changed source'):self.verify()
 def test_dot_underscore_without_inventoried_companion_stays_pinned(self):
  side=self.inc/'._orphan.cpp';side.write_bytes(inventory.APPLEDOUBLE+b'a'*16)
  self.files['inc/._orphan.cpp']=hashlib.sha256(side.read_bytes()).hexdigest();self.companions.add('inc/._orphan.cpp')
  side.write_bytes(inventory.APPLEDOUBLE+b'b'*16)
  with self.assertRaisesRegex(ValueError,'changed source'):inventory.verify_files(self.root,self.files)
 def test_unpinned_header_is_rejected(self):
  (self.inc/'new.h').write_text('#define NEW 1\n')
  with self.assertRaisesRegex(ValueError,'Unpinned'):self.verify()
 @unittest.skipUnless(hasattr(os,'mkfifo'),'POSIX only')
 def test_special_source_and_include_files_rejected_without_blocking(self):
  p=self.inc/'pipe';os.mkfifo(p)
  with self.assertRaisesRegex(ValueError,'special'):inventory.audit_include(self.root,'inc',self.files,self.companions)
  self.files['inc/pipe']='b'*64
  with self.assertRaisesRegex(ValueError,'regular|changed'):inventory.verify_files(self.root,self.files)
 @unittest.skipUnless(inventory.descriptor_reads(),'Descriptor operations required')
 def test_directory_and_file_descriptors_close_after_failure(self):
  self.files['inc/main.cpp']='0'*64;opened=[];original=os.open
  def record(*args,**kwargs):
   fd=original(*args,**kwargs);opened.append(fd);return fd
  with patch.object(inventory,'descriptor_reads',return_value=True),patch.object(inventory.os,'open',side_effect=record):
   with self.assertRaises(ValueError):self.verify()
  for fd in set(opened):
   with self.assertRaises(OSError):os.fstat(fd)
