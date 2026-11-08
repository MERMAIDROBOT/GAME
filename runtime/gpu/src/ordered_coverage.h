#pragma once
#include <algorithm>
#include <cmath>
#include "ps2gpu/gs_renderer.h"
namespace ps2gpu {
struct CoverageTriangle { struct Point {float x,y;} p[3]; };
inline CoverageTriangle coverageTriangle(const GsVertex* v) {return {{{v[0].x,v[0].y},{v[1].x,v[1].y},{v[2].x,v[2].y}}};}
// Only proofs of disjoint coverage are accepted. Exact common edges use the
// rasterizer's identical snapped endpoints/top-left rule; interiors on opposite
// sides cannot write the same sample. Other geometry retains the old split.
inline bool independentCoverage(const CoverageTriangle& a,const CoverageTriangle& b) {
    for(auto p:a.p)if(!std::isfinite(p.x)||!std::isfinite(p.y))return false;
    for(auto p:b.p)if(!std::isfinite(p.x)||!std::isfinite(p.y))return false;
    for(unsigned axis=0;axis<2;++axis) {
        float al=axis?a.p[0].y:a.p[0].x,ah=al,bl=axis?b.p[0].y:b.p[0].x,bh=bl;
        for(unsigned i=1;i<3;++i) {
            float av=axis?a.p[i].y:a.p[i].x,bv=axis?b.p[i].y:b.p[i].x;
            al=std::min(al,av);ah=std::max(ah,av);bl=std::min(bl,bv);bh=std::max(bh,bv);
        }
        // One whole GS pixel leaves conservative slack for raster precision.
        if(double(bl)-ah>=1.0 || double(al)-bh>=1.0)return true;
    }
    auto equal=[](auto x,auto y){return x.x==y.x&&x.y==y.y;};
    for(unsigned i=0;i<3;++i)for(unsigned j=0;j<3;++j) {
        const auto u=a.p[i],v=a.p[(i+1)%3];
        if(equal(u,v))continue;
        const auto s=b.p[j],t=b.p[(j+1)%3];
        if(!((equal(u,s)&&equal(v,t))||(equal(u,t)&&equal(v,s))))continue;
        auto side=[&](auto p){return (double(v.x)-u.x)*(double(p.y)-u.y)-(double(v.y)-u.y)*(double(p.x)-u.x);};
        const double x=side(a.p[(i+2)%3]),y=side(b.p[(j+2)%3]);
        const double dx=double(v.x)-u.x,dy=double(v.y)-u.y;
        // Keep both third vertices a whole GS pixel from the common edge.
        // Extremely thin triangles can change orientation after raster snapping.
        if(x*x<dx*dx+dy*dy || y*y<dx*dx+dy*dy)continue;
        if((x<0&&y>0)||(x>0&&y<0))return true;
    }
    return false;
}
}
