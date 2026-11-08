import struct
from pathlib import Path
import tempfile
import unittest
from overlay_segments import discover,build_elf


def fixture():
    blob=bytearray(64);entry=0x200020;headers=[]
    for addr,size,kind in ((0x110000,64,1),(0x120000,32,8),(0x130000,64,1),(0x200000,128,1)):
        headers.append(len(blob));blob.extend(struct.pack('<4I',addr,size,kind,entry));blob.extend(bytes([len(headers)])*size)
    code=headers[-1]+16
    # The code census starts inside the first code segment. Its independent
    # file-to-memory relationship still proves the full segment's placement.
    args=dict(origin=0,region_offset=code+16,region_bytes=112,region_base=0x200010)
    return blob,args,headers


class SectionLayouts(unittest.TestCase):
    def test_engine_reader_selects_header_layout_and_recovers_code_prefix(self):
        import overlays
        blob,args,_=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'disc-root').mkdir()
            (root/'disc-root/level.bin').write_bytes(blob)
            region=dict(source='file',file='level.bin',offset=args['region_offset'],
                        bytes=args['region_bytes'],estimated_load_address=hex(args['region_base']))
            image=overlays.program_image(root,region)
            self.assertEqual(image['base'],0x200000)
            self.assertEqual(len(image['data']),128)
            self.assertEqual(image['spans'],[(0,128)])
            self.assertEqual(len(image['layout']['segments']),4)

    def test_separate_load_addresses_and_missed_code_prefix_are_preserved(self):
        blob,args,_=fixture();layout=discover(blob,**args)
        self.assertEqual(len(layout['segments']),4)
        elf=build_elf(layout,blob,origin=0)
        for i,segment in enumerate(layout['segments']):
            kind,offset,address,_,file_bytes,memory_bytes,flags,_=struct.unpack_from('<8I',elf,52+i*32)
            self.assertEqual(address,segment['address']);self.assertEqual(memory_bytes,segment['bytes'])
            self.assertEqual(file_bytes,0 if segment['zero_fill'] else segment['bytes'])
            self.assertEqual(bool(flags&1),segment['executable'])
            if file_bytes:self.assertEqual(elf[offset:offset+file_bytes],blob[segment['file_offset']:segment['file_offset']+file_bytes])

    def test_changed_payload_cannot_reuse_layout_receipt(self):
        blob,args,_=fixture();layout=discover(blob,**args);blob[layout['segments'][0]['file_offset']]^=1
        with self.assertRaisesRegex(ValueError,'payload differs'):build_elf(layout,blob,origin=0)

    def test_overlapping_sections_are_rejected(self):
        blob,args,headers=fixture();struct.pack_into('<I',blob,headers[1],0x110010)
        self.assertIsNone(discover(blob,**args))

    def test_mismatched_entry_breaks_chain(self):
        blob,args,headers=fixture();struct.pack_into('<I',blob,headers[1]+12,0x200024)
        self.assertIsNone(discover(blob,**args))

    def test_entry_must_be_in_code_not_zero_fill(self):
        blob,args,headers=fixture()
        for off in headers:struct.pack_into('<I',blob,off+12,0x120000)
        self.assertIsNone(discover(blob,**args))

    def test_census_mapping_disagreement_is_rejected(self):
        blob,args,_=fixture();args['region_base']+=4
        self.assertIsNone(discover(blob,**args))

    def test_no_chain_is_not_fabricated(self):
        self.assertIsNone(discover(bytes(512),origin=0,region_offset=0,region_bytes=512,region_base=0x100000))

    def test_truncated_census_window_is_rejected(self):
        blob,args,_=fixture()
        with self.assertRaisesRegex(ValueError,'not backed'):discover(blob[:-4],**args)


if __name__=='__main__':unittest.main()
