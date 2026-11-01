#include <IOSurface/IOSurface.h>
#include <cstdint>
#include <cstring>
static IOSurfaceRef surface=nullptr;
static int* outstanding=nullptr;
extern "C" void mock_surface_config(int* count) {
    outstanding=count;
    int width=3,height=2,bpe=4,format='BGRA';
    CFNumberRef values[]={CFNumberCreate(nullptr,kCFNumberIntType,&width),CFNumberCreate(nullptr,kCFNumberIntType,&height),CFNumberCreate(nullptr,kCFNumberIntType,&bpe),CFNumberCreate(nullptr,kCFNumberIntType,&format)};
    const void* keys[]={kIOSurfaceWidth,kIOSurfaceHeight,kIOSurfaceBytesPerElement,kIOSurfacePixelFormat};
    const void* dictionaryValues[]={values[0],values[1],values[2],values[3]};
    auto dictionary=CFDictionaryCreate(nullptr,keys,dictionaryValues,4,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
    surface=IOSurfaceCreate(dictionary);CFRelease(dictionary);for(auto value:values)CFRelease(value);
    IOSurfaceLock(surface,0,nullptr);
    auto* bytes=static_cast<unsigned char*>(IOSurfaceGetBaseAddress(surface));auto stride=IOSurfaceGetBytesPerRow(surface);
    for(unsigned y=0;y<2;++y)for(unsigned x=0;x<3;++x){auto* p=bytes+y*stride+x*4;p[0]=0x56;p[1]=0x34;p[2]=0x12;p[3]=0;}
    IOSurfaceUnlock(surface,0,nullptr);
}
extern "C" void* hwgs_surface_wait(std::uint64_t*serial,std::uint32_t*w,std::uint32_t*h,int) {
    if(!serial||*serial!=77||!surface)return nullptr;
    *w=3;*h=2;++*outstanding;return surface;
}
extern "C" void hwgs_surface_release(std::uint64_t serial){if(serial==77)--*outstanding;}
__attribute__((destructor)) static void cleanup(){if(surface)CFRelease(surface);}
