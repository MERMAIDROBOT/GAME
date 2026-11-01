#pragma once
#include <algorithm>
#include <cstdint>
#include <utility>
#include <vector>
namespace VramRanges {
using Range=std::pair<std::uint32_t,std::uint32_t>;
constexpr std::uint32_t blocks=16384;
inline void add(std::vector<Range>& out,std::uint32_t begin,std::uint32_t count) {
    if(!count)return;
    if(count>=blocks){out={{0,blocks}};return;}
    begin%=blocks;
    const auto first=std::min(count,blocks-begin);
    out.emplace_back(begin,begin+first);
    if(first<count)out.emplace_back(0,count-first);
    std::sort(out.begin(),out.end());
    std::vector<Range> merged;
    for(auto r:out) {
        if(merged.empty() || merged.back().second<r.first)merged.push_back(r);
        else merged.back().second=std::max(merged.back().second,r.second);
    }
    out.swap(merged);
}
inline bool overlaps(const std::vector<Range>& ranges,std::uint32_t begin,std::uint32_t end) {
    for(auto r:ranges)if(r.first<end && begin<r.second)return true;
    return false;
}
}
