"""Compiled synthetic instruction tests: no retail code or SIMD dependencies."""
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest
import knowledge
import mmi


def word(rs=1,rt=2,rd=3): return mmi.VALUE | rs<<21 | rt<<16 | rd<<11


def record(w,code): return f'// 0x1000: 0x{w:08x} label-not-trusted\nctx->pc = 0x1000u;\n'+code+'\n'


class MMIRewriteTests(unittest.TestCase):
    def test_opcode_guard_and_unknown_emission_fail_closed(self):
        with self.assertRaises(ValueError): mmi.registers(word()^1)
        with self.assertRaises(ValueError): mmi.rewrite(record(word(),'mystery();'))
        other=record(word()^1,mmi.legacy(word()))
        self.assertEqual(mmi.rewrite(other),(other,0,0))

    def test_rewrite_aliases_and_idempotence(self):
        for rs,rt,rd in [(1,2,3),(1,2,1),(1,2,2),(1,2,0),(0,2,3),(1,0,3),(1,1,3)]:
            w=word(rs,rt,rd)
            updated,count,changed=mmi.rewrite(record(w,mmi.legacy(w)))
            self.assertEqual((count,changed),(1,1))
            self.assertIn(mmi.emit(w),updated)
            self.assertEqual(mmi.rewrite(updated),(updated,1,0))

    def test_knowledge_does_not_mutate_any_source_on_unknown_emission(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a.cpp';a.write_text(record(word(),mmi.legacy(word())))
            b=Path(tmp)/'b.cpp';b.write_text(record(word(),'mystery();'))
            old=a.read_bytes()
            with self.assertRaises(ValueError): knowledge.apply_translation(tmp)
            self.assertEqual(a.read_bytes(),old)
            b.unlink();report=knowledge.apply_translation(tmp)
            self.assertEqual(report['totals']['corrected'],1)
            self.assertEqual(knowledge.apply_translation(tmp)['totals']['corrected'],0)

    def test_compiled_emission_matches_signed_lanes_and_negative_control_fails(self):
        compiler=shutil.which('clang++') or shutil.which('g++')
        if not compiler: self.skipTest('C++ compiler unavailable')
        variants=[(1,2,3),(1,2,1),(1,2,2),(1,2,0),(0,2,3),(1,0,3),(1,1,3)]
        support=r'''
#include <array>
#include <cstdint>
#include <cstring>
#include <iostream>
using __m128i=std::array<uint32_t,4>;
struct Context { __m128i r[32]{}; uint64_t hi=~0ull,lo=~0ull,hi1=~0ull,lo1=~0ull; uint32_t pc=0; };
#define GPR_VEC(c,rn) ((rn)==0?__m128i{}:(c)->r[rn])
#define SET_GPR_VEC(c,rn,value) do { if(rn) (c)->r[rn]=(value); } while(0)
#define SET_GPR_U64(c,rn,value) do { if(rn) { uint64_t v=(value); std::memcpy(&(c)->r[rn],&v,8); } } while(0)
__m128i _mm_setr_epi32(int32_t a,int32_t b,int32_t c,int32_t d) {return {uint32_t(a),uint32_t(b),uint32_t(c),uint32_t(d)};}
__m128i _mm_madd_epi16(__m128i a,__m128i b) {int16_t x[8],y[8];std::memcpy(x,&a,16);std::memcpy(y,&b,16); __m128i r{}; for(int i=0;i<4;i++) r[i]=uint32_t(int32_t(x[2*i])*y[2*i])+uint32_t(int32_t(x[2*i+1])*y[2*i+1]);return r;}
int32_t _mm_cvtsi128_si32(__m128i a){return int32_t(a[0]);}
__m128i _mm_srli_si128(__m128i a,int n){__m128i out{};std::memcpy(&out,reinterpret_cast<char*>(&a)+n,16-n);return out;}
'''
        functions='\n'.join(f'void f{i}(Context* ctx) '+mmi.emit(word(*v)) for i,v in enumerate(variants))
        functions+='\nvoid bad(Context* ctx) '+mmi.legacy(word())
        dispatch=''.join(f'case {i}: f{i}(&c); break;' for i in range(len(variants)))
        driver=r'''
int main() { int mode; while(std::cin>>mode) { Context c; int16_t a[8],b[8];int v;
for(int i=0;i<8;i++){std::cin>>v;a[i]=int16_t(v);}for(int i=0;i<8;i++){std::cin>>v;b[i]=int16_t(v);}
std::memcpy(&c.r[1],a,16);std::memcpy(&c.r[2],b,16);c.r[0]={11,22,33,44};
switch(mode){DISPATCH default: bad(&c);break;}
std::cout<<c.lo<<' '<<c.hi<<' '<<c.lo1<<' '<<c.hi1;
int dests[]={3,1,2,0,3,3,3};int rd=mode<7?dests[mode]:3;
for(auto x:c.r[rd])std::cout<<' '<<x;
for(auto x:c.r[0])std::cout<<' '<<x;
std::cout<<'\n';}}
'''.replace('DISPATCH',dispatch)
        rng=random.Random(5900)
        fixtures=[([1,-2,3,-4,5,-6,7,-8],[11,12,13,14,15,16,17,18]),
                  ([-32768,-32768,32767,-1,0,32767,-32768,1],[-32768,32767,32767,-32768,-1,-32768,-1,0])]
        fixtures += [([rng.randint(-32768,32767) for _ in range(8)],
                      [rng.randint(-32768,32767) for _ in range(8)]) for _ in range(40)]
        cases=[(i,a,b) for i in range(7) for a,b in fixtures]+[(7,*fixtures[0])]
        encoded=''.join(' '.join(map(str,[i,*a,*b]))+'\n' for i,a,b in cases)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.cpp';path.write_text(support+functions+driver);exe=Path(tmp)/'test'
            built=subprocess.run([compiler,'-std=c++20','-O2',str(path),'-o',str(exe)],capture_output=True,text=True,timeout=60)
            self.assertEqual(built.returncode,0,built.stderr)
            ran=subprocess.run([str(exe)],input=encoded,capture_output=True,text=True,timeout=10,check=True)
        lines=ran.stdout.splitlines(); self.assertEqual(len(lines),len(cases))
        for (i,a,b),line in zip(cases[:-1],lines[:-1]):
            rs,rt,rd=variants[i]
            aa=[0]*8 if rs==0 else a
            bb=[0]*8 if rt==0 else a if rt==1 else b
            products=[x*y & 0xffffffff for x,y in zip(aa,bb)]
            banks=[products[0] | products[1]<<32, products[2] | products[3]<<32,
                   products[4] | products[5]<<32, products[6] | products[7]<<32]
            dest=[products[j] for j in [0,2,4,6]] if rd else [11,22,33,44]
            self.assertEqual(list(map(int,line.split())),banks+dest+[11,22,33,44],(i,a,b))
        # Original emission cannot satisfy the same literal lane contract.
        self.assertNotEqual(int(lines[-1].split()[0]),0xffffffe80000000b)
