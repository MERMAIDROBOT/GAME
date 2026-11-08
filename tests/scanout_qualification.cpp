// Display tuples observed in actual movie/game transitions, plus invalid neighbors.
#include "../runtime/gpu/native-plugin/scanout_qualification.hpp"
#include <cstdio>
int main(){
 std::array<uint64_t,15> p{};p[0]=0xffa1;p[1]=16640;p[2]=1;p[7]=p[9]=4096;p[8]=p[10]=7874698016924284ull;
 int failures=0;auto check=[&](bool ok,const char* msg){if(!ok){std::fprintf(stderr,"FAIL: %s\n",msg);++failures;}};
 check(LocalGS::qualifiedScanout(p),"R&C 512-wide CRT1 movie with ALP/MMOD bits must present");
 p[0]=1;check(LocalGS::qualifiedScanout(p),"512-wide CRT1 is the same geometry without blend control bits");
 p[0]=2;check(LocalGS::qualifiedScanout(p),"512-wide CRT2 baseline");
 p[0]=0;check(!LocalGS::qualifiedScanout(p),"disabled display is not qualified");
 p[0]=0xffa1;p[7]|=1ull<<15;check(!LocalGS::qualifiedScanout(p),"unsupported framebuffer format remains rejected");p[7]=4096;
 p[8]+=1;check(!LocalGS::qualifiedScanout(p),"unexpected display origin remains rejected");p[8]=7874698016924284ull;
 p[7]=4608;check(!LocalGS::qualifiedScanout(p),"576-wide unsupported width remains rejected");
 // Existing Kelly 640x480 movie exception must remain accepted.
 p={};p[0]=0x66;p[1]=16640;p[2]=1;p[9]=5120;p[10]=0x1df9ff0183227cull;
 check(LocalGS::qualifiedScanout(p),"existing Kelly 480-line movie path retained");
 return failures?1:0;
}
