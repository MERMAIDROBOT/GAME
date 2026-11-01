#include "hw_owner.hpp"
#include <cassert>
#include <cstdlib>
#include <string>
static unsigned cpuCalls=0,surfaceCalls=0,infoCalls=0;
static bool supported=true,infoSupported=true;
static int publicationLeases=0;
static std::vector<unsigned char> pixels(1024*896*4,0x33);
extern "C" {
int hwgs_start(unsigned,char*,size_t){return 1;}
void hwgs_reset(const void*,size_t){}
void hwgs_write(unsigned,unsigned long long){}
void hwgs_gif(unsigned,const void*,size_t){}
void hwgs_upload(unsigned long long,unsigned long long,unsigned long long,unsigned long long,const void*,size_t){}
void hwgs_sync(){}
void hwgs_snapshot(void*){}
void hwgs_privileged(unsigned,unsigned long long){}
int hwgs_content_stamp(unsigned*,unsigned*,unsigned*,unsigned long long*){return 0;}
int hwgs_frame(void* out,size_t capacity,unsigned* w,unsigned* h,unsigned* s){++cpuCalls;*w=1024;*h=896;*s=2;if(out && capacity==size_t(-1))*static_cast<const void**>(out)=pixels.data();return 1;}
void hwgs_surface_release(std::uint64_t serial){assert(serial==77);--publicationLeases;}
int hwgs_picture_cpu(void*,size_t,unsigned*,unsigned*){return 0;}
size_t hwgs_read_fifo(void*,size_t){return 0;}
int hwgs_frame_info(std::uint32_t*w,std::uint32_t*h,std::uint32_t*s){++infoCalls;*w=1024;*h=896;*s=2;return infoSupported;}
int hwgs_frame_surface(std::uint32_t x,std::uint32_t y,std::uint32_t w,std::uint32_t h,std::uint32_t ow,std::uint32_t oh,std::uint64_t* serial){++surfaceCalls;assert(x==0&&y==0&&w==1024&&h==896&&ow==1024&&oh==896);if(!supported)return 0;*serial=77;++publicationLeases;return 1;}
}
int main(int argc,char**argv){
    const std::string mode=argc>1?argv[1]:"default";
    setenv("PS2_PORT_HW_GS_SCALE","2",1);setenv("PS2_PORT_ENHANCE_HIRES_SCANOUT","1",1);
    if(mode=="default")unsetenv("PS2_PORT_HW_GS_ZERO_COPY");
    else setenv("PS2_PORT_HW_GS_ZERO_COPY",mode=="invalid"?"1extra":"1",1);
    supported=mode!="fallback";infoSupported=mode!="info-fallback";
    LocalGS::RawField retained;
    {
    LocalGS::HwOwner owner;
    ParallelGS::PrivRegisterState priv{};priv.pmode.EN2=1;priv.display2.DW=511;priv.display2.DH=447;
    const auto field=owner.field(priv,0);
    ps2port::SurfaceToken token{};
    const bool tokenized=ps2port::decodeSurfaceToken(field.bytes(),&token);
    assert(field.width==1024&&field.height==896);
    if(mode=="surface") {
        retained=field;
        assert(tokenized&&token.serial==77&&token.width==1024&&token.height==896);
        assert(surfaceCalls==1&&infoCalls==1&&publicationLeases==1);
        assert(cpuCalls==0);
    } else {
        assert(!tokenized&&field.bytes().size()==pixels.size());
        assert(field.bytes()[0]==0x33);
        assert(cpuCalls==2);
        assert(surfaceCalls==(mode=="fallback"?1:0));
        assert(infoCalls==((mode=="fallback" || mode=="info-fallback")?1:0));
    }
    }
    assert(publicationLeases==(mode=="surface"?1:0));
    retained=LocalGS::RawField{};
    assert(publicationLeases==0);
}
