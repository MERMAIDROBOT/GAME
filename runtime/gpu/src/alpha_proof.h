#pragma once
#include "ps2gpu/gs_renderer.h"
#include <algorithm>
#include <cstddef>
namespace ps2gpu {
// Prove the alpha test passes for an entire conservative integer interval.
// Unknown/texture-dependent alpha retains the original ordered AFAIL passes.
inline bool alphaIntervalPasses(unsigned low,unsigned high,unsigned test,unsigned reference) {
    if (low>high || high>255 || reference>255) return false;
    switch(test) {
    case 1:return true;
    case 2:return high<reference;
    case 3:return high<=reference;
    case 4:return low==reference&&high==reference;
    case 5:return low>=reference;
    case 6:return low>reference;
    case 7:return reference<low||reference>high;
    default:return false;
    }
}
inline bool vertexAlphaAlwaysPasses(const GsDrawState& state,const GsVertex* vertices,std::size_t count) {
    if (!state.alpha_test || !vertices || count<3 || count%3) return false;
    unsigned low=255,high=0;
    for(std::size_t i=0;i<count;i+=3) {
        for(unsigned k=state.gouraud?0:2;k<3;++k) {
            low=std::min(low,unsigned(vertices[i+k].a));high=std::max(high,unsigned(vertices[i+k].a));
        }
    }
    // shader alpha is round(interpolated colour * 255); retain an extra byte
    // at each end to cover interpolation/normalization roundoff conservatively.
    low=low?low-1:0;high=std::min(255u,high+1);
    if (state.textured && state.tcc) {
        if (state.tfx==TextureFunction::Highlight) high=255;
        else if (state.tfx==TextureFunction::Modulate) {
            high=std::min(255u,255u*high/128u);low=0;
        } else {low=0;high=255;}
    }
    return alphaIntervalPasses(low,high,state.atst,state.aref);
}
}
