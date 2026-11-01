// Exercise actual transfer coverage at VRAM's boundary, not a copy of its formula.
#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <memory>
#include <string>
#include <vector>
#define private public
#include "ps2host/gs.h"
#undef private
#include "ps2host/profile.h"
#include <cassert>
#include <cstdlib>
namespace ps2host {
std::uint32_t g_dma_source=0;
Timed::Timed(Section s):section_(s){} Timed::~Timed(){}
}
int main(int argc,char**) {
 using namespace ps2host::gs;
 auto gs=std::make_unique<Gs>();
 bool exact=false,coarse=false;u32 first=0,last=0;std::vector<u8> coverage;
 gs->on_memory=[&](u32 a,u32 b,bool){coarse=true;first=a;last=b;};
 gs->on_write=[&](u32 a,u32 b,const u8* c){exact=true;first=a;last=b;coverage.assign(c,c+b-a);};
 gs->Touch(0x3fef,1,PSMT8,0,0,64,64,true);
 if(argc>1){assert(coarse&&!exact&&last>16384);return 0;}
 assert(exact&&!coarse&&first==0x3fef&&last==0x3fff);
 // Every actual texel block must be covered, with no invented framebuffer wrap.
 for(u32 y=0;y<64;++y)for(u32 x=0;x<64;++x){u32 n=0;auto b=Memory::Address(0x3fef,1,x,y,PSMT8,&n)/256;assert(b>=first&&b<last&&coverage[b-first]);}
 exact=coarse=false;
 gs->Touch(0x3fff,1,PSMT8,0,0,64,64,true);
 assert(coarse&&!exact); // Genuine wrap keeps conservative synchronization.
 exact=coarse=false;
 gs->Touch(0x3fef,1,PSMT8,0,0,64,64,false);
 assert(coarse&&!exact); // CPU read barriers must not be relaxed by write optimization.
 std::puts("VRAM boundary: false wrap removed; real wrap/read barriers preserved");
}
