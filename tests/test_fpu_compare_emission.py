"""Real pinned FpuTranslator -> emitted line -> compiled PS2 comparison regression."""
from pathlib import Path
import hashlib,json,os,re,shutil,subprocess,tempfile,unittest
import pipeline
class CompareEmission(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.compiler=shutil.which('clang++') or shutil.which('g++')
  if not cls.compiler:raise RuntimeError('C++20 compiler required')
  cls.tmp=tempfile.TemporaryDirectory();cls.h=Path(cls.tmp.name)
  cls.ref=pipeline.ROOT/'tests/reference/fpu-translator';cls.up=cls.ref/'ps2xRecomp'
  cls.probe=cls.h/'probe'
  subprocess.run([cls.compiler,'-std=c++20','-O1','-DFMT_HEADER_ONLY',
   '-I',str(pipeline.ROOT/'tests/fpu-translator-support'),'-I',str(cls.up/'include'),'-I',str(cls.ref/'fmt/include'),
   str(cls.up/'src/lib/fpu_translator.cpp'),str(pipeline.ROOT/'tests/fpu_translator_probe.cpp'),'-o',str(cls.probe)],capture_output=True,check=True,timeout=90)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def emit(self,words):
  p=subprocess.run([str(self.probe)]+[f'{w:08x}' for w in words],capture_output=True,text=True,check=True,timeout=10)
  return {int(line.split('\t',1)[0],16):line.split('\t',1)[1] for line in p.stdout.splitlines()}
 def test_fixture_provenance(self):
  origin=json.loads((self.ref/'ORIGIN.json').read_text())
  self.assertEqual(origin['ps2recomp_revision'],json.loads((pipeline.ROOT/'toolchain.lock.json').read_text())['ps2recomp']['revision'])
  for row in origin['files']:self.assertEqual(hashlib.sha256((self.ref/row['path']).read_bytes()).hexdigest(),row['sha256'])
 def test_r5900_compare_census_uses_ps2_macros(self):
  # Only these four compare function encodings exist on the EE, despite generic MIPS enum names.
  expected={0x30:None,0x32:('FPU_C_EQ_S','ps2_feq'),0x34:('FPU_C_OLT_S','ps2_flt'),0x36:('FPU_C_OLE_S','ps2_fle')}
  words=[0x46000000 | ft<<16 | fs<<11 | fn for fn in expected for fs,ft in [(0,0),(3,4),(31,1)]]
  emitted=self.emit(words);patch=(pipeline.ROOT/'patches/scalar-fpu-compare.patch').read_text()
  macros={m[1]:m[2] for m in re.finditer(r'^\+#define (FPU_C_\w+_S)\(a, b\) (.+)$',patch,re.M)}
  for w,line in emitted.items():
   e=expected[w&63]
   if e is None:self.assertEqual(line,'ctx->fcr31 &= ~0x800000;');continue
   self.assertEqual(re.findall(r'FPU_C_\w+_S',line),[e[0]])
   self.assertIn(e[1],macros[e[0]])
  # Keep aliases correct too, while the above checks the actual EE instruction codes.
  self.assertIn('ps2_flt',macros['FPU_C_LT_S']);self.assertIn('ps2_fle',macros['FPU_C_LE_S'])
 def test_real_meter_words_and_compare_execution(self):
  words=[0x46006034,0x46010034,0x46010032,0x46010036,0x46010030]
  lines=self.emit(words)
  definitions='\n'.join(l[1:] for l in (pipeline.ROOT/'patches/scalar-fpu-compare.patch').read_text().splitlines() if l.startswith('+#define'))
  code='#include "ps2_scalar_fpu.h"\n'+definitions+'\nstruct Context{float f[32]{}; unsigned fcr31=0;};\n'
  for i,w in enumerate(words):code+=f'void op{i}(Context*ctx){{'+lines[w]+'}\n'
  code+='''
using namespace ps2_port_fpu;
float meter(float x){Context c;c.fcr31=0x55;c.f[0]=0;c.f[12]=x;op0(&c);if(c.fcr31&0x800000)return 0;
c.f[0]=1;c.f[1]=x;op1(&c);if(c.fcr31&0x800000)return 1;return x;}
int main(){
 float max=ps2_fdiv(1.f,0.f);
 if(!ps2_compare_extended())return std::isnan(meter(max))?0:1; // old IEEE comparison control
 for(unsigned a=0;a<2;++a)for(unsigned b=0;b<2;++b){float x=ps2_fdiv(a?-1.f:1.f,b?-0.f:0.f);
 if(meter(x)!=((a^b)?0.f:1.f))return 2;if(ps2_fmul(x,0.f)!=0.f)return 3;}
 Context c;c.f[0]=max;c.f[1]=max;c.fcr31=0x55;op2(&c);if(c.fcr31!=0x800055)return 4;
 op3(&c);if(c.fcr31!=0x800055)return 5;op4(&c);if(c.fcr31!=0x55)return 6;
 c.f[0]=-0.f;c.f[1]=0.f;op2(&c);if(!(c.fcr31&0x800000))return 7;
 return 0;}
'''
  src=self.h/'meter.cpp';exe=self.h/'meter';src.write_text(code)
  subprocess.run([self.compiler,'-std=c++20','-O2','-ffp-contract=off','-I',str(pipeline.ROOT/'tools'),str(src),'-o',str(exe)],check=True,capture_output=True,timeout=30)
  for mode in ['0','1']:
   result=subprocess.run([str(exe)],env={**os.environ,'PS2_PORT_FPU_COMPARE':mode},capture_output=True)
   self.assertEqual(result.returncode,0,f'mode={mode}: {result.stderr}')
