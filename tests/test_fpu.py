from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import fpu
import knowledge
import pipeline


def word(op,fd=2,fs=3,ft=4):return op | fd<<6 | fs<<11 | ft<<16
def record(w,body):return f'// 0x1000: 0x{w:08x} untrusted-name\nctx->pc = 0x1000u;\n'+body+'\n'


class FpuTests(unittest.TestCase):
    def test_operand_decode_shapes_aliases_and_idempotence(self):
        for op in fpu.VALUES:
            for fd,fs,ft in [(2,3,4),(3,3,4),(4,3,4),(2,3,3),(0,0,4)]:
                w=word(op,fd,fs,ft)
                new,count,changed=fpu.rewrite(record(w,fpu.legacy(w)))
                self.assertEqual((count,changed),(1,1))
                self.assertIn(fpu.emit(w),new)
                self.assertEqual(fpu.rewrite(new),(new,1,0))
                self.assertEqual(fpu.registers(w),(fd,fs,ft))
        with self.assertRaises(ValueError):fpu.registers(0)

    def test_wrong_opcode_unknown_emission_and_bank_conflicts(self):
        w=word(0x46000016)
        unrelated=record(w^1,fpu.legacy(w))
        self.assertEqual(fpu.rewrite(unrelated),(unrelated,0,0))
        with self.assertRaises(ValueError):fpu.rewrite(record(w,'unknown();'))
        bank=knowledge.load();rule=next(r for r in bank['rules'] if r['kind']=='fpu_scalar')
        bank['rules'].append(dict(rule,id='duplicate-kind'))
        with self.assertRaises(ValueError):knowledge.validate(bank)

    def test_all_sources_validate_before_mutation(self):
        w=word(0x46000016)
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a.cpp';a.write_text(record(w,fpu.legacy(w)))
            b=Path(tmp)/'b.cpp';b.write_text(record(w,'unknown();'))
            before=a.read_bytes()
            with self.assertRaises(ValueError):knowledge.apply_translation(tmp)
            self.assertEqual(a.read_bytes(),before)
            b.unlink()
            self.assertEqual(knowledge.apply_translation(tmp)['totals']['corrected'],1)
            self.assertEqual(knowledge.apply_translation(tmp)['totals']['corrected'],0)

    def test_compiled_generated_corrections_and_special_values(self):
        compiler=shutil.which('clang++') or shutil.which('g++')
        if not compiler:self.fail('C++20 compiler required')
        support=r'''
#include <bit>
#include <cmath>
#include <cstdint>
#include "ps2_scalar_fpu.h"
struct Context {float f[32]{}; uint32_t fcr31=0x1234,pc=0;};
#define FPU_DIV_S(a,b) ps2_port_fpu::ps2_fdiv(a,b)
#define FPU_SQRT_S(a) ps2_port_fpu::ps2_fsqrt(a)
using namespace ps2_port_fpu;
'''
        functions=''
        for i,(op,fd) in enumerate((op,fd) for op in fpu.VALUES for fd in [2,3,4]):
            functions+=f'void f{i}(Context* ctx) {{'+fpu.emit(word(op,fd))+'}\n'
        functions+='void bad(Context* ctx) {'+fpu.legacy(word(0x46000016))+'}\n'
        main='int main(){ Context c;'
        for i,(op,fd) in enumerate((op,fd) for op in fpu.VALUES for fd in [2,3,4]):
            # fs=8, ft=16: DIV=0.5; RSQRT=2. Destination aliases both sources.
            expected='0.5f' if op==0x46000003 else '2.0f'
            main+=f'c.f[3]=8;c.f[4]=16;f{i}(&c);if(c.f[{fd}]!={expected} || c.fcr31!=0x1234) return {i+1};'
        main+='c.f[3]=8;c.f[4]=16;bad(&c);if(c.f[2]==2.0f)return 20;'
        main+='c.f[3]=1;c.f[4]=-0.0f;f0(&c);if(std::bit_cast<uint32_t>(c.f[2])!=0xffffffffu || c.fcr31!=(0x1234|0x100000))return 21;'
        # Denormal input guard was an explicit gap in upstream's result-only fast path.
        main+='if(std::bit_cast<uint32_t>(ps2_fadd(std::bit_cast<float>(0x00800000u),std::bit_cast<float>(0x007fffffu)))!=0x00800000u)return 22;'
        main+='if(ps2_fsqrt(-16.0f)!=4.0f)return 23;return 0;}'
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'check.cpp';exe=Path(tmp)/'check';src.write_text(support+functions+main)
            subprocess.run([compiler,'-std=c++20','-O2','-ffp-contract=off','-I',str(pipeline.ROOT/'tools'),str(src),'-o',str(exe)],capture_output=True,check=True,timeout=60)
            ran=subprocess.run([str(exe)],capture_output=True,timeout=10)
            self.assertEqual(ran.returncode,0,ran.stderr)

    def test_runtime_patch_matches_tested_helper_and_export_whitelist(self):
        if not shutil.which('git'):self.fail('git required')
        # Minimal pinned header context sufficient for the actual unified-diff hunk.
        patch=(pipeline.ROOT/'patches/scalar-fpu.patch').read_text()
        # Check that patch's added header is byte-identical to the compiled candidate.
        header_part=patch.split('+++ b/ps2xRuntime/include/ps2_port_scalar_fpu.h\n',1)[1]
        payload=''.join(line[1:] for line in header_part.splitlines(True) if line.startswith('+'))
        self.assertEqual(payload,(pipeline.ROOT/'tools/ps2_scalar_fpu.h').read_text())
        for name in ['fpu.py','conformance.py','patches/scalar-fpu.patch','tests/fixtures/fpu-hardware.json']:
            self.assertIn(name,pipeline.SOURCE_FILES)
