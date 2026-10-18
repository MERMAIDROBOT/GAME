#pragma once
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
using u64=std::uint64_t; using s64=std::int64_t;
using u32=std::uint32_t; using s32=std::int32_t;
struct Write {u32 address,size;u64 value;};
struct Memory {
    u32 base;
    std::vector<unsigned char> bytes;
    std::vector<Write> writes;
    std::size_t offset(u32 addr,unsigned size) const {
        if ((size!=1 && size!=2 && size!=4) || (addr&(size-1)) || addr<base || u64(addr)+size>u64(base)+bytes.size())
            throw std::runtime_error("unaligned or unmapped memory");
        return addr-base;
    }
    u64 read(u32 addr,unsigned size,bool sign) const {
        auto off=offset(addr,size);u64 value=0;
        for(unsigned i=0;i<size;++i)value|=u64(bytes[off+i])<<(8*i);
        if(sign && (value&(u64(1)<<(8*size-1))))value|=~((u64(1)<<(8*size))-1);
        return value;
    }
    void write(u32 addr,u64 value,unsigned size) {
        auto off=offset(addr,size);value&=(u64(1)<<(8*size))-1;
        for(unsigned i=0;i<size;++i)bytes[off+i]=static_cast<unsigned char>(value>>(8*i));
        writes.push_back({addr,size,value});
    }
};
#define RECOVERY_MAIN int main(){try { \
    std::string line;while(std::getline(std::cin,line)){ \
        std::istringstream in(line);u64 a[8];Memory m{};std::size_t size;std::string hex; \
        for(auto &x:a)if(!(in>>std::hex>>x))return 4; \
        if(!(in>>std::hex>>m.base>>std::dec>>size>>hex)||size>65536)return 5; \
        if(hex=="-")hex.clear();if(hex.size()!=size*2)return 6; \
        for(std::size_t i=0;i<size;++i)m.bytes.push_back(static_cast<unsigned char>(std::stoul(hex.substr(i*2,2),nullptr,16))); \
        u64 value=recovered(m,a[0],a[1],a[2],a[3],a[4],a[5],a[6],a[7]); \
        std::cout<<std::hex<<std::setfill('0')<<std::setw(16)<<value<<'|'; \
        if(m.bytes.empty())std::cout<<'-';else for(unsigned char c:m.bytes)std::cout<<std::setw(2)<<unsigned(c); \
        std::cout<<"|["<<std::dec;bool first=true;for(auto w:m.writes){if(!first)std::cout<<',';first=false;std::cout<<'['<<w.address<<','<<w.size<<','<<w.value<<']';} \
        std::cout<<"]\n"; \
    }return 0;}catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 2;}}
