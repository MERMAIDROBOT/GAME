#pragma once
#include <algorithm>
#include <cstdint>

namespace target_freshness {
// Canonical local-memory writes remain authoritative until the GPU redraws
// their entire conservative block range. Texture pages include swizzle gaps.
struct CpuWrites {
    std::uint32_t begin=~0u,end=0;
    void written(std::uint32_t first,std::uint32_t last) {
        if(first>=last)return;
        begin=std::min(begin,first);end=std::max(end,last);
    }
    void drawn(std::uint32_t first,std::uint32_t last) {
        if(first<=begin && last>=end){begin=~0u;end=0;}
    }
    bool rejects(bool gpu_newer,std::uint32_t bp,std::uint32_t bw,
                 std::uint32_t width,std::uint32_t height,std::uint32_t storage_bits)const {
        if(gpu_newer || begin>=end || !width || !height)return false;
        // High-byte indexed formats occupy 32-bit storage pages, not 4/8-bit
        // packed pages; callers pass physical storage bits (PixelBits).
        const auto pw=storage_bits>=16?64u:128u;
        const auto ph=storage_bits>=32?32u:storage_bits==16?64u:storage_bits==8?64u:128u;
        const auto pitch=std::max(1u,(std::max(1u,bw)*64+pw-1)/pw);
        const auto cols=(width+pw-1)/pw,rows=(height+ph-1)/ph;
        for(std::uint32_t row=0;row<rows;++row){
            const auto first=(bp+row*pitch*32)%16384u;
            const auto count=cols*32;
            if(count>=16384)return true;
            const auto a=std::min(count,16384u-first);
            if(first<end && begin<first+a)return true;
            if(count>a && begin<count-a)return true;
        }
        return false;
    }
};
}
