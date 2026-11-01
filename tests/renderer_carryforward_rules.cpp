#include "../runtime/gpu/host-adapter/target_freshness.hpp"
#include "../runtime/gpu/host-adapter/adaptive_scale_rule.hpp"
#include <cassert>
#include <cstdio>
#include <limits>
int main(){
    using target_freshness::CpuWrites;
    CpuWrites photo;photo.written(0x3300,0x3320);
    assert(photo.rejects(false,0x32e0,8,512,64,8));
    assert(!photo.rejects(true,0x32e0,8,512,64,8));
    assert(!photo.rejects(false,0x2000,8,512,64,8));
    photo.drawn(0x3300,0x3310);
    assert(photo.rejects(false,0x32e0,8,512,64,8));
    photo.drawn(0x3300,0x3320);
    assert(!photo.rejects(false,0x32e0,8,512,64,8));
    CpuWrites swizzle;swizzle.written(0x3340,0x3360);
    assert(swizzle.rejects(false,0x32e0,8,512,32,8));
    CpuWrites wrapped;wrapped.written(0,16);
    assert(wrapped.rejects(false,16368,2,128,64,8));
    CpuWrites high_byte;high_byte.written(132,164);
    assert(high_byte.rejects(false,100,1,64,64,32));
    CpuWrites stride_gap;stride_gap.written(200,220);
    assert(!stride_gap.rejects(false,100,8,64,64,32));
    assert(!stride_gap.rejects(false,100,8,0,64,32));
    adaptive_scale_rule::Rule sustained;
    for(int window=0;window<3;++window){
        for(int stall=0;stall<6;++stall)assert(!sustained.Feed(20+window*10+stall*0.05,50));
        assert(sustained.Feed(30+window*10,16.7)==(window==2));
    }
    adaptive_scale_rule::Rule normal;
    for(int i=0;i<200;++i)assert(!normal.Feed(i, i%10==0?50:16.7));
    adaptive_scale_rule::Rule idle;
    for(int window=0;window<2;++window){
        for(int i=0;i<6;++i)assert(!idle.Feed(20+window*10+i*0.1,50));
        assert(!idle.Feed(30+window*10,16.7));
    }
    assert(!idle.Feed(80,50)); // Empty windows break consecutive contention.
    for(int i=1;i<6;++i)assert(!idle.Feed(80+i*0.1,50));
    assert(!idle.Feed(90,16.7));
    assert(!idle.Feed(std::numeric_limits<double>::quiet_NaN(),50));
    adaptive_scale_rule::Rule load;
    for(int i=0;i<1600;++i){const double t=i*0.05;assert(!load.Feed(t,t>=40&&t<52?50:16.7));}
    std::puts("renderer carryforward rules passed");
}
