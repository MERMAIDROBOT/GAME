#pragma once
#include "surface_consumer.hpp"
#include "surface_token.hpp"
#if defined(__APPLE__)
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
#include <IOSurface/IOSurface.h>
#include <dlfcn.h>

namespace ps2port {
// One instance per window/context. Call shutdown BEFORE destroying that context.
// The originating producer stays loaded until its last GPU consumer releases.
class MacSurfacePresenter {
    using Wait = void* (*)(std::uint64_t*,std::uint32_t*,std::uint32_t*,int);
    using Release = void (*)(std::uint64_t);
    Wait wait_ = nullptr;
    Release release_ = nullptr;
    void* module_ = nullptr;
    CGLContextObj context_ = nullptr;
    GLuint rectangle_ = 0, read_ = 0, draw_ = 0;
    GLsync fence_ = nullptr;
    SurfaceConsumer consumer_;
    IOSurfaceRef acquired_ = nullptr;
    SurfaceToken acquiredToken_{};
    bool bindProducer(void* loaderHandle=nullptr) {
        auto lookup=loaderHandle?loaderHandle:RTLD_DEFAULT;
        auto wait = reinterpret_cast<Wait>(dlsym(lookup,"hwgs_surface_wait"));
        auto release = reinterpret_cast<Release>(dlsym(lookup,"hwgs_surface_release"));
        if (module_) return loaderHandle ? wait==wait_ && release==release_ : true;
        Dl_info wi{},ri{};
        if (!wait || !release || !dladdr(reinterpret_cast<void*>(wait),&wi) ||
            !dladdr(reinterpret_cast<void*>(release),&ri) || wi.dli_fbase!=ri.dli_fbase) return false;
        module_=dlopen(wi.dli_fname,RTLD_NOW|RTLD_LOCAL);
        if (!module_) return false;
        // Resolve against the pinned handle, not a replaceable global lookup.
        wait_=reinterpret_cast<Wait>(dlsym(module_,"hwgs_surface_wait"));
        release_=reinterpret_cast<Release>(dlsym(module_,"hwgs_surface_release"));
        if (!wait_ || !release_) {dlclose(module_);module_=nullptr;return false;}
        return true;
    }
    bool retire(bool drain) {
        if (!consumer_.busy()) return true;
        if (!context_ || CGLGetCurrentContext()!=context_) return false;
        SurfaceCompletion completion=SurfaceCompletion::pending;
        if (drain || !fence_) {
            // A forced timeout is insufficient. This completes all prior work
            // in the context before making the IOSurface writable again.
            glFinish(); completion=SurfaceCompletion::complete;
        } else {
            const auto result=glClientWaitSync(fence_,GL_SYNC_FLUSH_COMMANDS_BIT,0);
            if (result==GL_ALREADY_SIGNALED || result==GL_CONDITION_SATISFIED)
                completion=SurfaceCompletion::complete;
            else if (result==GL_WAIT_FAILED) {
                // A broken fence is not completion; explicitly drain this
                // still-current context before returning its backing storage.
                glFinish();completion=SurfaceCompletion::complete;
            }
        }
        if (!consumer_.retire(completion,release_)) return false;
        acquired_=nullptr;acquiredToken_={};
        if (fence_) {glDeleteSync(fence_);fence_=nullptr;}
        return true;
    }
public:
    MacSurfacePresenter() = default;
    MacSurfacePresenter(const MacSurfacePresenter&)=delete;
    MacSurfacePresenter& operator=(const MacSurfacePresenter&)=delete;
    ~MacSurfacePresenter() { shutdown(); }
    bool configureProducer(void* loaderHandle) {
        if (!loaderHandle || consumer_.busy()) return false;
        return bindProducer(loaderHandle);
    }
    bool ready() {
        auto current=CGLGetCurrentContext();
        if (!current || (context_ && current!=context_)) return false;
        context_=current;
        return retire(false);
    }
    bool acquire(const SurfaceToken& token) {
        if (!validSurfaceToken(token) || !ready() || !bindProducer()) return false;
        auto serial=token.serial;std::uint32_t w=0,h=0;
        auto* surface=static_cast<IOSurfaceRef>(wait_(&serial,&w,&h,0));
        if (!surface) return false; // stale tokens are rejected; keep the last window picture
        consumer_.acquired(serial);
        if (serial!=token.serial || w!=token.width || h!=token.height) {retire(true);return false;}
        acquired_=surface;acquiredToken_=token;
        return true;
    }
    bool copyAcquired(GLuint destination,const SurfaceToken& token) {
        if (!destination || !acquired_ || consumer_.serial()!=token.serial || token.width!=acquiredToken_.width || token.height!=acquiredToken_.height || CGLGetCurrentContext()!=context_) return false;
        const auto w=token.width,h=token.height;
        auto surface=acquired_;
        if (!rectangle_) {glGenTextures(1,&rectangle_);glGenFramebuffers(1,&read_);glGenFramebuffers(1,&draw_);}
        GLint oldRead=0,oldDraw=0,oldRect=0;GLfloat clear[4];GLboolean mask[4];
        glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING,&oldRead);glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING,&oldDraw);
        glGetIntegerv(GL_TEXTURE_BINDING_RECTANGLE,&oldRect);glGetFloatv(GL_COLOR_CLEAR_VALUE,clear);glGetBooleanv(GL_COLOR_WRITEMASK,mask);
        const bool scissor=glIsEnabled(GL_SCISSOR_TEST);
        glBindTexture(GL_TEXTURE_RECTANGLE,rectangle_);
        bool ok=CGLTexImageIOSurface2D(context_,GL_TEXTURE_RECTANGLE,GL_RGBA,GLsizei(w),GLsizei(h),GL_BGRA,GL_UNSIGNED_INT_8_8_8_8_REV,surface,0)==kCGLNoError;
        if (ok) {
            glBindFramebuffer(GL_READ_FRAMEBUFFER,read_);glFramebufferTexture2D(GL_READ_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_RECTANGLE,rectangle_,0);
            glBindFramebuffer(GL_DRAW_FRAMEBUFFER,draw_);glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,destination,0);
            ok=glCheckFramebufferStatus(GL_READ_FRAMEBUFFER)==GL_FRAMEBUFFER_COMPLETE && glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER)==GL_FRAMEBUFFER_COMPLETE;
            if (ok) {
                glDisable(GL_SCISSOR_TEST);
                glBlitFramebuffer(0,0,w,h,0,0,w,h,GL_COLOR_BUFFER_BIT,GL_NEAREST);
                glColorMask(GL_FALSE,GL_FALSE,GL_FALSE,GL_TRUE);glClearColor(0,0,0,1);glClear(GL_COLOR_BUFFER_BIT);
            }
            glFramebufferTexture2D(GL_READ_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_RECTANGLE,0,0);
            glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,0,0);
        }
        if (scissor) glEnable(GL_SCISSOR_TEST); else glDisable(GL_SCISSOR_TEST);
        glColorMask(mask[0],mask[1],mask[2],mask[3]);glClearColor(clear[0],clear[1],clear[2],clear[3]);
        glBindFramebuffer(GL_READ_FRAMEBUFFER,oldRead);glBindFramebuffer(GL_DRAW_FRAMEBUFFER,oldDraw);glBindTexture(GL_TEXTURE_RECTANGLE,oldRect);
        fence_=glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE,0);glFlush();
        if (!fence_ || !ok) retire(true);
        return ok;
    }
    bool shutdown() {
        if (context_ && CGLGetCurrentContext()!=context_) return false;
        if (!retire(true)) return false;
        if (rectangle_) glDeleteTextures(1,&rectangle_);
        if (read_) glDeleteFramebuffers(1,&read_);
        if (draw_) glDeleteFramebuffers(1,&draw_);
        rectangle_=read_=draw_=0;context_=nullptr;
        if (module_) dlclose(module_);
        module_=nullptr;wait_=nullptr;release_=nullptr;
        return true;
    }
};
}
#endif
