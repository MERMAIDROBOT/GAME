#include "mac_surface_presenter.hpp"
#include <cassert>
#include <vector>
int main(int argc,char**argv){
    assert(argc==2);
    CGLPixelFormatAttribute attributes[]={kCGLPFAAccelerated,kCGLPFAAllowOfflineRenderers,kCGLPFAOpenGLProfile,static_cast<CGLPixelFormatAttribute>(kCGLOGLPVersion_3_2_Core),static_cast<CGLPixelFormatAttribute>(0)};
    CGLPixelFormatObj format=nullptr;GLint count=0;
    assert(CGLChoosePixelFormat(attributes,&format,&count)==kCGLNoError&&format);
    ps2port::MacSurfacePresenter presenter;
    for(int generation=0;generation<2;++generation){
        CGLContextObj context=nullptr;assert(CGLCreateContext(format,nullptr,&context)==kCGLNoError);assert(CGLSetCurrentContext(context)==kCGLNoError);
        void* producer=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);assert(producer);
        auto configure=reinterpret_cast<void(*)(int*)>(dlsym(producer,"mock_surface_config"));assert(configure);
        int outstanding=0;configure(&outstanding);
        GLuint texture=0;glGenTextures(1,&texture);glBindTexture(GL_TEXTURE_2D,texture);glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,3,2,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
        assert(presenter.configureProducer(producer));
        assert(!presenter.acquire({76,3,2})&&outstanding==0); // stale exact token
        assert(!presenter.acquire({77,4,2})&&outstanding==0); // mismatched geometry releases safely
        assert(presenter.acquire({77,3,2})&&outstanding==1);
        dlclose(producer); // presenter must keep the originating module alive
        glEnable(GL_SCISSOR_TEST);glScissor(0,0,0,0);glColorMask(GL_TRUE,GL_FALSE,GL_TRUE,GL_FALSE);glClearColor(.1f,.2f,.3f,.4f);
        assert(presenter.copyAcquired(texture,{77,3,2}));
        assert(glIsEnabled(GL_SCISSOR_TEST));GLboolean mask[4];glGetBooleanv(GL_COLOR_WRITEMASK,mask);assert(mask[0]&&!mask[1]&&mask[2]&&!mask[3]);
        std::vector<unsigned char> pixels(3*2*4);glBindTexture(GL_TEXTURE_2D,texture);glGetTexImage(GL_TEXTURE_2D,0,GL_RGBA,GL_UNSIGNED_BYTE,pixels.data());
        for(unsigned i=0;i<6;++i){assert(pixels[i*4]==0x12&&pixels[i*4+1]==0x34&&pixels[i*4+2]==0x56&&pixels[i*4+3]==255);}
        assert(presenter.shutdown()&&outstanding==0);assert(presenter.shutdown());
        glDeleteTextures(1,&texture);CGLSetCurrentContext(nullptr);CGLDestroyContext(context);
    }
    CGLDestroyPixelFormat(format);
}
