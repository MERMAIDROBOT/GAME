#include <cstdlib>
#include <cassert>
#include "../runtime/gpu/native-plugin/hw_owner.hpp"
static unsigned live_scale=3,releases=0,pixel_probes=0;
static unsigned long long content_serial=1;
extern "C" {
int hwgs_start(unsigned,char*,size_t){return 1;}
void hwgs_privileged(unsigned,unsigned long long){}
int hwgs_content_stamp(unsigned* bp,unsigned* bw,unsigned* psm,unsigned long long* serial){*bp=1;*bw=8;*psm=0;*serial=content_serial;return 1;}
int hwgs_frame_info(std::uint32_t* w,std::uint32_t* h,std::uint32_t* s){*s=live_scale;*w=512*live_scale;*h=448*live_scale;return 1;}
int hwgs_frame(void*,size_t,unsigned*,unsigned*,unsigned*){++pixel_probes;return 0;}
int hwgs_frame_surface(std::uint32_t,std::uint32_t,std::uint32_t dw,std::uint32_t dh,std::uint32_t ow,std::uint32_t oh,std::uint64_t* serial){assert(dw==ow && dh==oh);*serial=content_serial;return 1;}
void hwgs_surface_release(std::uint64_t){++releases;}
int hwgs_picture_cpu(void*,size_t,unsigned*,unsigned*){return 0;}
}
int main(){
    setenv("PS2_PORT_HW_GS_SCALE","3",1);setenv("PS2_PORT_HW_GS_ZERO_COPY","1",1);setenv("PS2_PORT_ENHANCE_HIRES_SCANOUT","1",1);
    ParallelGS::PrivRegisterState priv{};priv.pmode.EN1=1;priv.display1.DW=511;priv.display1.DH=447;
    {
        LocalGS::HwOwner owner;
        auto old=owner.field(priv,0);assert(old.width==1536 && old.height==1344);
        live_scale=2; // The same content must still publish the new geometry.
        auto low=owner.field(priv,0);assert(low.width==1024 && low.height==896);
        ps2port::SurfaceToken token{};assert(ps2port::decodeSurfaceToken(low.bytes(),&token));assert(token.width==1024 && token.height==896);
        assert(old.width==1536 && old.height==1344);assert(releases==0);
        ++content_serial;auto next=owner.field(priv,0);assert(next.width==1024 && next.height==896);
        assert(pixel_probes==0);
    }
    assert(releases==3);
}
