// Private synchronized readback conversion; never a CPU drawing backend.
#pragma once
#include "runtime/gs/ps2_gs_memory.h"
#include <vector>
#include <algorithm>
#include <cstring>
#include <utility>
#include <stdexcept>
namespace LocalGS {
// Initialize GSMem lookup tables during owner setup, before any worker access.
inline std::vector<uint8_t> unpackCT32(std::vector<uint8_t> &vram,unsigned base,unsigned bw,
                                     unsigned x,unsigned y,unsigned width,unsigned height) {
    if(vram.size()!=4*1024*1024 || base>16383 || !bw || bw>63 || !width || !height ||
       width>2048 || height>2048 || x>2047 || y>2047 || width>2048-x || height>2048-y)
        throw std::runtime_error("Unsupported CT32 readback region");
    std::vector<uint8_t> output(size_t(width)*height*4);
    for(unsigned row=0;row<height;row++)for(unsigned col=0;col<width;col++) {
        const auto value=GSMem::ReadCT32(vram.data(),base,bw,x+col,y+row);
        const size_t at=4*(size_t(row)*width+col);
        for(unsigned c=0;c<4;c++)output[at+c]=uint8_t(value>>(8*c));
    }
    return output;
}
class ReadbackCursor {
    std::vector<uint8_t> bytes;size_t offset=0;
public:
    explicit ReadbackCursor(std::vector<uint8_t> data):bytes(std::move(data)) {
        if(bytes.size()>16*1024*1024)throw std::runtime_error("Readback cursor exceeds bound");
    }
    size_t consume(void *destination,size_t limit) {
        if(limit && !destination)throw std::runtime_error("Null readback destination");
        const size_t count=std::min(limit,bytes.size()-offset);
        if(count)std::memcpy(destination,bytes.data()+offset,count);
        offset+=count;return count;
    }
};
}
