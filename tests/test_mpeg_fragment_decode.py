"""Asset-free video: arbitrary IPU byte boundaries must preserve complete decoded pictures.
Build the actual runtime decoder class against the pinned local FFmpeg, not a second implementation.
The negative control sends arbitrary pieces directly to the codec (the lost/corrupt-picture regression).
"""
from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
import native,pipeline
from compute_slot import slot,assert_idle
ROOT=Path(__file__).resolve().parents[1]
SOURCE=pipeline.SOURCE/'ps2xRuntime/src/lib/Kernel/Stubs/MPEG.cpp'
PREFIX=native.PREFIX

def synthetic_movie():
 def bits(v,n):return format(v,'0'+str(n)+'b')
 def packed(s):return int(s+'0'*((-len(s))%8),2).to_bytes((len(s)+7)//8,'big')
 data=bytearray(b'\0\0\1\xb3'+packed(bits(16,12)+bits(16,12)+bits(1,4)+bits(3,4)+bits(10000,18)+'1'+bits(20,10)+'000'))
 for n in range(64):
  data+=b'\0\0\1\0'+packed(bits(n,10)+'001'+bits(65535,16)+'0')
  data+=b'\0\0\1\1'+packed(bits(8,5)+'011'+('10010'*4)+('0010'*2))
 return bytes(data)+b'\0\0\1\xb7'

PRE='''#include <array>
#include <deque>
#include <memory>
#include <mutex>
#include <vector>
#include <string>
#include <iostream>
#include <limits>
#include <cstring>
#include <fstream>
#include <algorithm>
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavutil/error.h>
#include <libavutil/log.h>
#include <libswscale/swscale.h>
}
#define PS2X_HAS_FFMPEG 1
#define AGRESSIVE_LOGS 0
#define PS2_PORT_MPEG_TRACE(code) do{}while(false)
'''
MAIN=r'''
#endif
int main(int argc,char** argv){
 MpegFfmpegDecoder d;std::ifstream in(argv[1],std::ios::binary);
 unsigned chunk=std::stoul(argv[2]),count=0;std::deque<MpegDecodedFrame> frames;
 std::vector<uint8_t> bytes(chunk+AV_INPUT_BUFFER_PADDING_SIZE,0);uint64_t hash=1469598103934665603ull;
 auto collect=[&]{while(!frames.empty()){auto f=std::move(frames.front());frames.pop_front();if(f.width!=16||f.height!=16)return false;for(auto b:f.rgba){hash^=b;hash*=1099511628211ull;}++count;}return true;};
 while(in){in.read((char*)bytes.data(),chunk);auto n=in.gcount();if(!n)break;std::fill(bytes.begin()+n,bytes.end(),0);if(!d.feed(bytes.data(),n,frames)||!collect())return 2;}
 if(!d.flush(frames)||!collect())return 3;
 std::cout<<count<<" "<<hash<<"\n";return count==64?0:4;
}
'''

@unittest.skipUnless(shutil.which('clang++') and SOURCE.is_file() and (PREFIX/'lib/libavcodec.a').is_file(),'local patched runtime and pinned FFmpeg required')
class FragmentDecode(unittest.TestCase):
 def test_complete_pictures_survive_arbitrary_byte_boundaries(self):
  with slot('mpeg-fragment-regression'):
   assert_idle()
   source=SOURCE.read_text();start=source.index('        struct MpegDecodedFrame');end=source.index('#else\n        // TODO',start)
   decoder=source[start:end]
   with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);movie=tmp/'synthetic.m1v';movie.write_bytes(synthetic_movie())
    def compile_one(body,name):
     cpp=tmp/(name+'.cpp');exe=tmp/name;cpp.write_text(PRE+body+MAIN)
     command=['clang++','-std=c++20','-O1',str(cpp),'-I'+str(PREFIX/'include'),'-L'+str(PREFIX/'lib'),'-lavcodec','-lswscale','-lavutil','-o',str(exe)]
     if os.uname().sysname=='Darwin':command+=['-framework','CoreFoundation','-framework','CoreVideo','-framework','CoreMedia']
     ran=subprocess.run(command,capture_output=True,text=True);self.assertEqual(ran.returncode,0,ran.stderr)
     return exe
    exe=compile_one(decoder,'actual')
    reference=None
    for n in (4096,2048,17,7,1):
     ran=subprocess.run([str(exe),str(movie),str(n)],capture_output=True,text=True,timeout=15)
     self.assertEqual(ran.returncode,0,ran.stderr+ran.stdout)
     if reference is None:reference=ran.stdout
     self.assertEqual(ran.stdout,reference,f'picture output changed at {n}-byte boundaries')
     self.assertEqual(ran.stderr,'','valid synthetic stream produced decoder errors')
    # Bypass the parser exactly where feed enters after codec initialization.
    marker='                static uint32_t s_feedLogCount = 0u;'
    self.assertIn(marker,decoder)
    bad=compile_one(decoder.replace(marker,'                return sendPacket(data,size,frames);\n'+marker,1),'raw-negative')
    ran=subprocess.run([str(bad),str(movie),'7'],capture_output=True,text=True,timeout=15)
    self.assertTrue(ran.returncode!=0 or ran.stdout!=reference,'negative control failed to detect parser bypass')
