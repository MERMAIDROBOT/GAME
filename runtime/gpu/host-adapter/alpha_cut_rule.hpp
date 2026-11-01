// Claude 2026-10-27: when gs.frag's hard-alpha-cut rule (blend_ops bit 18) applies to a draw. A flat, alpha-blended
// 2D piece the GS draws minified (over 1.4 texels a pixel, at least 8x8 pixels) at a scale above 1 - and only where
// the texture's alpha counts (TEX0.TCC). With TCC 0 the GS takes alpha from the vertex and the texels' alpha means
// nothing: the Florida photo is a copy of the frame whose water left alpha 0 in most texels, and the rule's
// nearest-texel switch at each 0/opaque pair drew its lower half as stripes.
#pragma once
#include <cstdint>

namespace alpha_cut_rule {
inline bool Applies(std::uint64_t tex0, bool textured, bool blended, std::uint32_t scale, std::uint32_t vertices,
                    bool flat, float width, float height, float texels_per_pixel) {
    const bool tcc = ((tex0 >> 34) & 1u) != 0;
    return tcc && textured && blended && scale > 1 && vertices >= 2 && vertices <= 6 &&
           flat && width >= 8.0f && height >= 8.0f && texels_per_pixel >= 1.4f;
}
}  // namespace alpha_cut_rule
