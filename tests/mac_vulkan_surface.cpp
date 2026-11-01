#include "ps2gpu/gs_renderer.h"
#include "ps2gpu/surface_api.h"
#include "mac_surface_presenter.hpp"
#include <cassert>
#include <cstdio>
#include <thread>
#include <chrono>
#include <vector>
int main(int argc,char**argv) {
    assert(argc==2);
    auto* producer=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);assert(producer);
    CGLPixelFormatAttribute attributes[]={kCGLPFAAccelerated,kCGLPFAAllowOfflineRenderers,kCGLPFAOpenGLProfile,static_cast<CGLPixelFormatAttribute>(kCGLOGLPVersion_3_2_Core),static_cast<CGLPixelFormatAttribute>(0)};
    CGLPixelFormatObj format=nullptr;GLint n=0;assert(CGLChoosePixelFormat(attributes,&format,&n)==kCGLNoError&&format);
    CGLContextObj context=nullptr;assert(CGLCreateContext(format,nullptr,&context)==kCGLNoError);CGLSetCurrentContext(context);
    ps2port::MacSurfacePresenter presenter;assert(presenter.configureProducer(producer));
    std::uint64_t previous=0;
    for(unsigned generation=0;generation<2;++generation) {
        std::string error;auto gpu=ps2gpu::CreateGpu(&error);
        if(!gpu){std::fprintf(stderr,"GPU: %s\n",error.c_str());return 1;}
        auto renderer=ps2gpu::CreateGsRenderer(*gpu,&error);
        if(!renderer){std::fprintf(stderr,"renderer: %s\n",error.c_str());return 1;}
        renderer->SetScale(2);
        const unsigned side=generation?384:512,output=side*2;
        const unsigned framebufferWidth=side/64;
        ps2gpu::GsDrawState state{};state.fbp=0x10;state.fbw=framebufferWidth;state.width=side;state.height=side;state.scissor_x1=side-1;state.scissor_y1=side-1;
        const std::uint8_t red=generation?0x46:0x12,green=generation?0x28:0x34,blue=generation?0x6a:0x56;
        float end=float(side);
        const ps2gpu::GsVertex vertices[6]={{0,0,0,red,green,blue,0x80,0,0,1},{end,0,0,red,green,blue,0x80,0,0,1},{0,end,0,red,green,blue,0x80,0,0,1},{end,0,0,red,green,blue,0x80,0,0,1},{end,end,0,red,green,blue,0x80,0,0,1},{0,end,0,red,green,blue,0x80,0,0,1}};
        renderer->DrawTriangles(state,vertices,6);
        std::uint64_t serial=0;
        if(!renderer->BlitTargetToSurface(0x10,framebufferWidth,0,0,0,output,output,output,output,&serial)) {
            std::fprintf(stderr,"Vulkan IOSurface producer unavailable\n");return 1;
        }
        assert(serial>previous);
        if(previous) {
            auto stale=previous;std::uint32_t w=0,h=0;
            assert(!hwgs_surface_wait(&stale,&w,&h,0));
        }
        const ps2port::SurfaceToken token{serial,output,output};
        bool acquired=false;
        for(unsigned i=0;i<2000&&!acquired;++i){acquired=presenter.acquire(token);if(!acquired)std::this_thread::sleep_for(std::chrono::milliseconds(1));}
        assert(acquired);
        hwgs_surface_release(serial); // retire publication; consumer still owns backing storage
        renderer.reset();gpu.reset(); // actual Vulkan/device teardown before the GL copy
        GLuint texture=0;glGenTextures(1,&texture);glBindTexture(GL_TEXTURE_2D,texture);glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,output,output,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
        assert(presenter.copyAcquired(texture,token));
        std::vector<unsigned char> pixels(output*output*4);glGetTexImage(GL_TEXTURE_2D,0,GL_RGBA,GL_UNSIGNED_BYTE,pixels.data());
        const auto offset=(size_t(output/2)*output+output/2)*4;
        auto near=[](unsigned a,unsigned b){return a>b?a-b<=1:b-a<=1;};
        if(!near(pixels[offset],red)||!near(pixels[offset+1],green)||!near(pixels[offset+2],blue)||pixels[offset+3]!=255){
            std::fprintf(stderr,"Color mismatch: %u,%u,%u,%u expected %u,%u,%u,255\n",pixels[offset],pixels[offset+1],pixels[offset+2],pixels[offset+3],red,green,blue);return 1;
        }
        assert(presenter.shutdown());glDeleteTextures(1,&texture);
        assert(presenter.configureProducer(producer));previous=serial;
    }
    assert(presenter.shutdown());dlclose(producer);CGLSetCurrentContext(nullptr);CGLDestroyContext(context);CGLDestroyPixelFormat(format);
    std::puts("Vulkan -> IOSurface -> CGL: colors, resize, exact serials and renderer/device teardown passed");
}
