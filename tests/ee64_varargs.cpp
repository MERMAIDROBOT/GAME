// Exercise the actual shipped cursor excerpt, including a distinguishing opt-out.
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <cstdio>
#include <array>
struct R5900Context { std::array<std::array<uint64_t,2>,32> r{}; };
struct PS2Runtime {};
static uint32_t getRegU32(R5900Context* c,int r){return uint32_t(c->r[r][0]);}
static bool tryReadWordFromGuest(uint8_t* ram,PS2Runtime*,uint32_t address,uint32_t& out){if(!ram||address>4092)return false;std::memcpy(&out,ram+address,4);return true;}
#include "actual_cursor.h"
int main(int argc,char**){
 R5900Context ctx;std::array<uint8_t,4096> ram{};ctx.r[29][0]=0x100;
 double number=6.5;uint64_t bits=0;std::memcpy(&bits,&number,8);
 ctx.r[6][0]=bits;ctx.r[7][0]=0;
 Ps2VarArgCursor one(ram.data(),&ctx,nullptr,2);
 uint64_t value=one.nextU64();double d=0;std::memcpy(&d,&value,8);
 if(argc>1){assert(d==0.0);return 0;}
 assert(d==6.5);
 ctx.r[6][0]=3;ctx.r[7][0]=bits;
 Ps2VarArgCursor mixed(ram.data(),&ctx,nullptr,2);
 assert(mixed.nextU32()==3);assert(mixed.nextU64()==bits);
 ctx.r[6][0]=0x123456789abcdefULL;
 Ps2VarArgCursor integer(ram.data(),&ctx,nullptr,2);assert(integer.nextU64()==0x123456789abcdefULL);
 ctx.r[11][0]=bits;uint32_t stackFirst=0xaabbccdd;std::memcpy(ram.data()+0x110,&stackFirst,4);
 Ps2VarArgCursor boundary(ram.data(),&ctx,nullptr,7);assert(boundary.nextU64()==bits);assert(boundary.nextU32()==stackFirst);
 // Existing stack-pair behavior remains unchanged by this register correction.
 uint32_t lo=0x89abcdef,hi=0x01234567;std::memcpy(ram.data()+0x110,&lo,4);std::memcpy(ram.data()+0x114,&hi,4);
 Ps2VarArgCursor stack(ram.data(),&ctx,nullptr,8);assert(stack.nextU64()==0x0123456789abcdefULL);
 std::puts("EE64 register cursor controls passed");
}
