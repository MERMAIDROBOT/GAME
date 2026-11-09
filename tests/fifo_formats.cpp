#include "../runtime/gpu/native-plugin/fifo_formats.hpp"
#include <cstdio>
int main(){
 int failures=0;auto check=[&](bool ok,const char*m){if(!ok){std::fprintf(stderr,"FAIL: %s\n",m);++failures;}};
 using namespace LocalGS;
 check(guestFifoBytesPerPixel(kPsmCT32)==4&&guestFifoBytesPerPixel(kPsmZ32)==4,"32-bit colour and depth read 4 bytes");
 check(guestFifoBytesPerPixel(kPsmCT24)==3&&guestFifoBytesPerPixel(kPsmZ24)==3,"24-bit colour and depth pack 3 bytes");
 check(guestFifoBytesPerPixel(0x02)==0&&guestFifoBytesPerPixel(0x13)==0&&guestFifoBytesPerPixel(0x32)==0,"other formats stay unqualified");
 check((16u*8u*guestFifoBytesPerPixel(kPsmZ24))%16==0,"observed 16x8 Z24 read is whole qwords (384 bytes)");
 if(!failures)std::puts("fifo formats: ok");return failures?1:0;
}
