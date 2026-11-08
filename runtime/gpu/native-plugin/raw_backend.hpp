#include "../../presentation/surface_token.hpp"
#include <set>
#include <cmath>
#include <algorithm>
// Private raw-owner backend, initially qualified for CT32 guest access/NTSC CRT2.
#pragma once
#include "field_publisher.hpp"
#include "completion_gate.hpp"
#include "image_stamp.hpp"
#include "scanout_qualification.hpp"
#include <cstdio>
#include "runtime/gs/gs_backend.h"
namespace LocalGS {
class RawBackend final:public GSRasterBackend {
    std::shared_ptr<void> resources; // must outlive publisher and GPU reference
    QueuedOwner &gpu;
    FieldPublisher publisher;
    CompletionGate<CompletedField> completedResources;
    GSTransferSnapshot transfer{};
    std::vector<uint8_t> fifo;size_t fifoOffset=0;
    ImageStamp<GSPresentationStamp> presentedImage;
    uint64_t lastPictureSampleTick = 0;
    unsigned pictureSamples = 0;
    std::vector<uint8_t> weaveRows; // Claude 2026-10-13: movie field weave buffer
    mutable std::recursive_mutex mutex;
    const bool batchGIF=[] {const char *v=std::getenv("PS2_PORT_PRIVATE_GIF_BATCH");return v && *v=='1';}();
    mutable std::vector<OwnedGIF> pendingGIF;
    mutable size_t pendingBytes=0;
    void flushGIF() const {
        if(pendingGIF.empty())return;
        auto packets=std::move(pendingGIF);pendingGIF.clear();pendingBytes=0;
        gpu.gifBatch(std::move(packets));
    }
    void tracePackedPicture(const CompletedField &field) {
        static const bool enabled = [] {
            const char *setting = std::getenv("PS2_PORT_GS_PICTURE_TRACE");
            return setting && std::strcmp(setting, "1") == 0;
        }();
        if (!enabled || pictureSamples >= 18000 || field.boundary.tick == lastPictureSampleTick) return;
        const auto &image = field.image();
        const auto &pixels = image.bytes();
        const auto now = std::chrono::steady_clock::now().time_since_epoch();
        const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
        // Sample RGB on a fixed grid. Compare successive observations of the same
        // field parity and size. Equal hashes are evidence about the sampled pixels.
        uint64_t fingerprint = 14695981039346656037ull;
        for (unsigned y = 0; y < 48; ++y) {
            const size_t row = size_t((2 * y + 1) * image.height / 96) * image.width;
            for (unsigned x = 0; x < 64; ++x) {
                const size_t pixel = (row + (2 * x + 1) * image.width / 128) * 4;
                for (unsigned channel = 0; channel < 3; ++channel) {
                    fingerprint ^= pixels[pixel + channel];
                    fingerprint *= 1099511628211ull;
                }
            }
        }
        lastPictureSampleTick = field.boundary.tick;
        ++pictureSamples;
        std::fprintf(stderr, "[gs picture] ns=%lld tick=%llu phase=%u size=%ux%u rgb_sample=%016llx\n",
                     static_cast<long long>(ns), static_cast<unsigned long long>(field.boundary.tick),
                     field.boundary.phase, image.width, image.height,
                     static_cast<unsigned long long>(fingerprint));
    }
public:
    explicit RawBackend(QueuedOwner &owner,std::shared_ptr<void> lifetime={}):resources(std::move(lifetime)),gpu(owner),publisher(owner){}
    bool OwnsRawGS()const override{return true;}
    void Initialize(uint8_t*p,uint32_t n)override {
        std::lock_guard<std::recursive_mutex> lock(mutex);
        if(!p || n!=4*1024*1024)throw std::invalid_argument("Raw backend requires exact4MiB initialized VRAM");
        pendingGIF.clear();pendingBytes=0;publisher.clear();completedResources.clear();gpu.reset(p,n);transfer={};fifo.clear();fifoOffset=0;
    }
    void Reset()override {
        std::lock_guard<std::recursive_mutex> lock(mutex);
        flushGIF();publisher.clear();completedResources.clear();auto memory=gpu.snapshot();gpu.reset(memory.data(),memory.size());
        transfer={};fifo.clear();fifoOffset=0;
    }
    void RawGIF(const uint8_t*p,uint32_t n)override {
        std::lock_guard<std::recursive_mutex> lock(mutex);
        if(!p || n<16 || n%16 || n>16*1024*1024)throw std::invalid_argument("Invalid raw GIF payload");
        if(!batchGIF || n>256*1024) {flushGIF();gpu.gif(1,p,n);return;}
        if(pendingGIF.size()==32 || n>256*1024-pendingBytes)flushGIF();
        pendingGIF.push_back({std::vector<uint8_t>(p,p+n),currentFPControl()});pendingBytes+=n;
        if(pendingGIF.size()==32 || pendingBytes==256*1024)flushGIF();
    }
    void RawRegister(uint8_t a,uint64_t v)override {
        std::lock_guard<std::recursive_mutex> lock(mutex);flushGIF();gpu.write(a,v);
    }
    void RawUpload(uint64_t b,uint64_t p,uint64_t r,uint64_t d,const uint8_t*s,uint32_t n)override {
        std::lock_guard<std::recursive_mutex> lock(mutex);flushGIF();gpu.uploadCT32(b,p,r,d,s,n);
    }
    void Submit(const GSPrimitiveBatch&)override{throw std::logic_error("Decoded draw reached raw owner");}
    void LoadClut(const GSTex0Reg&,const GSTexClutReg&)override{throw std::logic_error("Decoded CLUT reached raw owner");}
    void UploadImage(const uint8_t*,uint32_t)override{throw std::logic_error("Decoded upload reached raw owner");}
    void BeginTransfer(const GSTransferCommand&c)override {
        std::lock_guard<std::recursive_mutex> lock(mutex);
        flushGIF();transfer={};transfer.direction=c.direction;transfer.x=c.trxpos.dsax;transfer.y=c.trxpos.dsay;
        transfer.totalPixels=unsigned(c.trxreg.rrw)*c.trxreg.rrh;fifo.clear();fifoOffset=0;
        if(c.direction==1) {
            const size_t bytes=size_t(transfer.totalPixels)*4;
            if(c.bitbltbuf.spsm!=GS_PSM_CT32 || !bytes || bytes%16 || bytes>16*1024*1024)
                throw std::runtime_error("Unqualified guest FIFO format/size");
            // Read the GS-owned FIFO captured at raw TRXDIR, not later mutable VRAM.
            fifo=gpu.readFIFO(bytes);transfer.localToHostPendingBytes=bytes;
        }
    }
    void Flush()override{std::lock_guard<std::recursive_mutex> lock(mutex);flushGIF();gpu.sync();}
    void TextureFlush()override{} // Raw TEXFLUSH already entered the owner.
    void Sync(GSSyncReason)override{std::lock_guard<std::recursive_mutex> lock(mutex);flushGIF();gpu.sync();}
    PresentationFrame Present(const GSPresentationRequest&)override{throw std::logic_error("Mutable MMIO request reached raw owner");}
    void CaptureField(const std::array<uint64_t,15>&p,uint64_t t,uint32_t phase)override {
        std::lock_guard<std::recursive_mutex> lock(mutex);
        flushGIF();if(!(p[0]&3)){publisher.clear();completedResources.clear();return;}
        ParallelGS::PrivRegisterState priv={};
        for(unsigned i=0;i<15;i++)std::memcpy(&priv.qwords_lo[i*2],&p[i],8);
        // Select the enabled circuit by PMODE.EN1/EN2, not the entire PMODE word.
        // ALP/MMOD/SLBG do not change its framebuffer geometry. The existing
        // format/timing/width checks below apply equally to 512- and 640-wide CRT1.
        // Comparing PMODE to 1 and requiring CRT1 width 640 froze 512-wide movies
        // after they enabled CRT1 with PMODE 0xffa1, although their draws reached FRAME 0.
        const bool interlaced=priv.smode2.INT;
        const bool crt1=(p[0]&3)==1;const auto &fb=crt1?priv.dispfb1:priv.dispfb2;const auto &display=crt1?priv.display1:priv.display2;
        const unsigned width=fb.FBW*64;
        if(!qualifiedScanout(p))
        {
            // Only the exceptional diagnostic needs the worker's ingress stamp.
            // Querying it for every valid field drains the async GPU queue on
            // the game thread before captureAsync can be enqueued.
            const auto [sequence,generation]=gpu.ingressStatus();
            // Claude 2026-10-21: an unlisted scanout tuple ended the game. Now it is logged (once per tuple) and the field
            // skipped - the last picture stays up. PS2_PORT_HW_SCANOUT_STRICT=1: fatal, as before.
            const std::string what="Unqualified live privileged scanout geometry pmode="+std::to_string(p[0])+" smode1="+std::to_string(p[1])+" smode2="+std::to_string(p[2])+" dispfb1="+std::to_string(p[7])+" display1="+std::to_string(p[8])+" dispfb2="+std::to_string(p[9])+" display2="+std::to_string(p[10])+" tick="+std::to_string(t)+" ingress="+std::to_string(sequence)+" generation="+std::to_string(generation);
            static const bool strict=[]{const char*e=std::getenv("PS2_PORT_HW_SCANOUT_STRICT");return e&&e[0]=='1';}();
            if(strict)throw std::runtime_error(what);
            static std::set<std::string> seen;
            const std::string tuple=std::to_string(p[0])+"/"+std::to_string(p[1])+"/"+std::to_string(p[2])+"/"+std::to_string(p[7])+"/"+std::to_string(p[8])+"/"+std::to_string(p[9])+"/"+std::to_string(p[10]);
            if(seen.size()<32 && seen.insert(tuple).second)std::fprintf(stderr,"[claude-scanout] skipped field: %s\n",what.c_str());
            return;
        }
        publisher.capture({p,t,phase});
    }
    PresentationFrame PresentCompletedField()override {
        auto f=completedResources.select(publisher.latest(), [](const CompletedField& field) {
            ps2port::SurfaceToken token{};
            if (!ps2port::decodeSurfaceToken(field.image().bytes(),&token)) return true;
            auto serial=token.serial; std::uint32_t width=0,height=0;
            if (!hwgs_surface_wait(&serial,&width,&height,0)) return false;
            hwgs_surface_release(serial);
            if (serial!=token.serial || width!=token.width || height!=token.height)
                throw std::runtime_error("Completed surface differs from submitted field");
            return true;
        });
        if(!f)return {};
        // First bounded integration uses only qualified NTSC CRT2 interlaced rows.
        const auto &p=f->boundary.privileged;
        const unsigned width=f->image().width;const bool interlaced=p[2]&1;
        const char *hd=std::getenv("PS2_PORT_ENHANCE_HIRES_SCANOUT");
        // Claude 2026-10-20: the hardware GS backend renders at 2x/3x/4x (1024x896, 1536x1344, 2048x1792).
        const unsigned hdHeight=f->image().height;
        const bool hdSize=(width==1024 && hdHeight==896) || (width==1536 && hdHeight==1344) || (width==2048 && hdHeight==1792) ||
                          (width==1280 && hdHeight==896) || (width==1920 && hdHeight==1344) || (width==2560 && hdHeight==1792);   // movies (640-wide)
        if(hd && hd[0]=='1' && hd[1]==0 && hdSize) {
            ps2port::SurfaceToken token{};
            const bool surface=ps2port::decodeSurfaceToken(f->image().bytes(),&token);
            if(surface && (token.width!=width || token.height!=hdHeight))
                throw std::runtime_error("Surface token dimensions differ from completed field");
            if(!surface && f->image().bytes().size()!=size_t(width)*hdHeight*4)
                throw std::runtime_error("Invalid packed HD scanout size");
            if (!surface) tracePackedPicture(*f);
            PresentationFrame result;
            result.stamp=presentedImage.select(f->image().retained,width,hdHeight,GSPresentationStamp{f->boundary.tick,f->generation,f->ingressSequence,f->boundary.phase});
            result.width=width;result.height=hdHeight;
            result.displayFbp=result.sourceFbp=p[(p[0]&3)==1?7:9]&511;
            result.pixels=f->image().bytes();
            result.retainedResource=f->image().surfaceLease;
            if(!surface) for(size_t i=3;i<result.pixels.size();i+=4)result.pixels[i]=255;
            // Native renderer readback on explicit local request; not desktop capture.
            if(const char *request=std::getenv("PS2_PORT_PRIVATE_EDGE_CAPTURE"); request && !surface) {
                if(FILE *q=std::fopen(request,"rb")) {
                    std::fclose(q);std::remove(request);
                    const std::string output=std::string(request)+".ppm";
                    if(FILE *fp=std::fopen(output.c_str(),"wb")) {
                        std::fprintf(fp,"P6\n%u %u\n255\n",result.width,result.height);
                        for(size_t i=0;i<result.pixels.size();i+=4)std::fwrite(result.pixels.data()+i,1,3,fp);
                        std::fclose(fp);std::fprintf(stderr,"[edge-trial capture] %s\n",output.c_str());
                    }
                }
            }
            static bool reported=false;
            if(!reported){std::fprintf(stderr,"[board-hires] packed GPU scanout 1024x896\n");reported=true;}
            return result;
        }

        const char *enhanced=std::getenv("PS2_PORT_PRIVATE_DEINTERLACE");
        const bool fullHeight=interlaced && enhanced && *enhanced=='1' && f->image().height==448;
        if(((p[0]&3)!=2 && (p[0]&3)!=1 && p[0]!=32871 && p[0]!=32775) || ((p[1]>>13)&3)!=2 ||
           (width!=512 && width!=640) || (f->image().height!=224 && !fullHeight))
            throw std::runtime_error("Unqualified live raw scanout mode");
        PresentationFrame result;result.stamp={f->boundary.tick,f->generation,f->ingressSequence,f->boundary.phase};result.width=width;result.height=interlaced?448:224;
        result.displayFbp=result.sourceFbp=p[(p[0]&3)==1?7:9]&511;result.pixels.resize(640*480*4);
        // Claude 2026-10-13 (WORKLOG 88): movies scan out in field mode (SMODE2 INT+FFMD); each decoded
        // picture spans two fields, so weaving the latest even and odd fields rebuilds the full
        // 448-line frame instead of line-doubling one field (which bobbed and blurred the logos).
        // PS2_PORT_ENHANCE_MOVIE_WEAVE=1 (phase0 -> even lines) or =2 (phase0 -> odd lines).
        static const int weave=[]{const char *v=std::getenv("PS2_PORT_ENHANCE_MOVIE_WEAVE");return v?std::atoi(v):0;}();
        // Aligned bob (=3: phase1 shifted down half a line, =4: phase0 shifted): fields sit half a
        // line apart, so line-doubling both at the same rows makes still content shake.
        if((weave==3||weave==4) && interlaced && !fullHeight && width==640 && f->image().height==224) {
            const bool shifted=((f->boundary.phase&1u)==1u)==(weave==3);
            const uint8_t *src=f->image().bytes().data();
            for(unsigned y=0;y<448;y++) {
                auto *row=result.pixels.data()+size_t(y)*640*4;
                // field row coordinate for output line y: shifted fields sample at (y-1)/2
                const float fy=shifted?(float(y)-1.0f)*0.5f:float(y)*0.5f;
                int y0=int(std::floor(fy));float t=fy-float(y0);int y1=y0+1;
                y0=std::clamp(y0,0,223);y1=std::clamp(y1,0,223);
                const uint8_t *r0=src+size_t(y0)*width*4,*r1=src+size_t(y1)*width*4;
                for(unsigned x=0;x<width*4;x++)row[x]=uint8_t(r0[x]+(r1[x]-r0[x])*t+0.5f);
                for(unsigned x=0;x<width;x++)row[x*4+3]=255;
            }
            return result;
        }
        if((weave==1||weave==2) && interlaced && !fullHeight && width==640 && f->image().height==224) {
            if(weaveRows.size()!=size_t(640)*448*4){weaveRows.assign(size_t(640)*448*4,0);}
            const unsigned parity=(f->boundary.phase&1u)^(weave==2?1u:0u);
            for(unsigned y=0;y<224;y++)
                std::memcpy(weaveRows.data()+size_t(2*y+parity)*640*4,f->image().bytes().data()+size_t(y)*width*4,width*4);
            for(unsigned y=0;y<448;y++) {
                auto *row=result.pixels.data()+size_t(y)*640*4;
                std::memcpy(row,weaveRows.data()+size_t(y)*640*4,640*4);
                for(unsigned x=0;x<width;x++)row[x*4+3]=255;
            }
            return result;
        }
        for(unsigned y=0;y<result.height;y++) {
            auto *row=result.pixels.data()+size_t(y)*640*4;
            std::memcpy(row,f->image().bytes().data()+size_t(interlaced&&!fullHeight?y/2:y)*width*4,width*4);
            // GS framebuffer alpha was already consumed by CRT composition.
            // The final opaque display must not blend again against the host window.
            // Keep VRAM and immutable raw field alpha unchanged for guest semantics.
            for(unsigned x=0;x<width;x++)row[x*4+3]=255;
        }
        return result;
    }
    bool ClearFramebuffer(const GSContext&,uint32_t)override{return false;}
    uint32_t ConsumeLocalToHostBytes(uint8_t*d,uint32_t n)override {
        std::lock_guard<std::recursive_mutex> lock(mutex);if(n&&!d)throw std::invalid_argument("Null FIFO destination");
        const size_t count=std::min<size_t>(n,fifo.size()-fifoOffset);
        if(count)std::memcpy(d,fifo.data()+fifoOffset,count);fifoOffset+=count;
        transfer.localToHostPendingBytes=fifo.size()-fifoOffset;transfer.copiedPixels=fifoOffset/4;return count;
    }
    uint32_t ReadVram(uint32_t p,uint32_t b,uint32_t w,uint32_t x,uint32_t y)const override {
        std::lock_guard<std::recursive_mutex> lock(mutex);flushGIF();
        if(p!=GS_PSM_CT32)throw std::runtime_error("Unqualified guest VRAM read format");
        auto memory=gpu.snapshot();auto bytes=unpackCT32(memory,b,w,x,y,1,1);uint32_t v;std::memcpy(&v,bytes.data(),4);return v;
    }
    void WriteVram(uint32_t p,uint32_t b,uint32_t w,uint32_t x,uint32_t y,uint32_t v)override {
        std::lock_guard<std::recursive_mutex> lock(mutex);flushGIF();
        if(p!=GS_PSM_CT32)throw std::runtime_error("Unqualified guest VRAM write format");gpu.writePointCT32(b,w,x,y,v);
    }
    void SnapshotVram(std::vector<uint8_t>&out)const override{std::lock_guard<std::recursive_mutex> lock(mutex);flushGIF();out=gpu.snapshot();}
    GSTransferSnapshot GetTransferSnapshot()const override{std::lock_guard<std::recursive_mutex> lock(mutex);return transfer;}
};
}
