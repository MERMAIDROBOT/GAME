#include "ordered_coverage.h"
#include <cassert>
#include <limits>
using T=ps2gpu::CoverageTriangle;
int main(){
 T a{{{0,0},{32,0},{0,32}}},b{{{32,0},{32,32},{0,32}}};
 assert(ps2gpu::independentCoverage(a,b));assert(ps2gpu::independentCoverage(b,a));
 T overlapping{{{0,0},{32,0},{4,4}}};assert(!ps2gpu::independentCoverage(a,overlapping));
 T duplicate=a;assert(!ps2gpu::independentCoverage(a,duplicate));
 T far{{{40,0},{50,0},{40,10}}};assert(ps2gpu::independentCoverage(a,far));
 T near{{{32.1f,0},{32.1f,32},{0.1f,32}}};assert(!ps2gpu::independentCoverage(a,near));
 for(unsigned i=0;i<3;++i)for(unsigned j=0;j<3;++j){T x,y;for(unsigned k=0;k<3;++k){x.p[k]=a.p[(i+k)%3];y.p[k]=b.p[(j+k)%3];}assert(ps2gpu::independentCoverage(x,y));std::swap(y.p[1],y.p[2]);assert(ps2gpu::independentCoverage(x,y));}
 T degenerate{{{0,0},{0,0},{32,0}}};assert(!ps2gpu::independentCoverage(a,degenerate));
 T thinA{{{0,0},{32,0},{16,0.001f}}},thinB{{{32,0},{0,0},{16,-0.001f}}};assert(!ps2gpu::independentCoverage(thinA,thinB));
 T invalid=a;invalid.p[0].x=std::numeric_limits<float>::quiet_NaN();assert(!ps2gpu::independentCoverage(invalid,b));
}
