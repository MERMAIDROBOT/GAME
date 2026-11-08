#include "alpha_proof.h"
#include <cassert>
bool actual(unsigned a,unsigned t,unsigned r){switch(t){case 0:return false;case 1:return true;case 2:return a<r;case 3:return a<=r;case 4:return a==r;case 5:return a>=r;case 6:return a>r;case 7:return a!=r;default:return true;}}
int main(){
 unsigned proofs=0;
 for(unsigned lo=0;lo<256;lo+=17)for(unsigned hi=lo;hi<256;hi+=17)
 for(unsigned t=0;t<8;++t)for(unsigned r=0;r<256;++r)if(ps2gpu::alphaIntervalPasses(lo,hi,t,r)){
 ++proofs;for(unsigned a=lo;a<=hi;++a)assert(actual(a,t,r));}
 assert(proofs>1000);assert(!ps2gpu::alphaIntervalPasses(255,256,1,1));
 ps2gpu::GsDrawState s;s.alpha_test=true;s.atst=6;s.aref=1;s.textured=true;s.tcc=false;s.gouraud=true;
 ps2gpu::GsVertex v[3]{};for(auto& p:v)p.a=128;
 assert(ps2gpu::vertexAlphaAlwaysPasses(s,v,3));
 s.tcc=true;assert(!ps2gpu::vertexAlphaAlwaysPasses(s,v,3));
 s.tfx=ps2gpu::TextureFunction::Highlight;assert(ps2gpu::vertexAlphaAlwaysPasses(s,v,3));
 s.tfx=ps2gpu::TextureFunction::Decal;s.atst=5;s.aref=0;assert(ps2gpu::vertexAlphaAlwaysPasses(s,v,3));
 s.atst=6;s.aref=1;s.tfx=ps2gpu::TextureFunction::Modulate;
 s.tcc=false;v[0].a=0;assert(!ps2gpu::vertexAlphaAlwaysPasses(s,v,3));
 s.gouraud=false;assert(ps2gpu::vertexAlphaAlwaysPasses(s,v,3));
 s.aref=128;assert(!ps2gpu::vertexAlphaAlwaysPasses(s,v,3));
 assert(!ps2gpu::vertexAlphaAlwaysPasses(s,nullptr,3));assert(!ps2gpu::vertexAlphaAlwaysPasses(s,v,2));
}
