// Private queued GPU owner. The device is exclusive to this adapter while alive.
#pragma once
#include "hw_owner.hpp"
#include "serial_queue.hpp"
#include "staging.hpp"
#include <memory>
#include <cstdlib>
#include <cstdio>
namespace LocalGS {
struct OwnedGIF {std::vector<uint8_t> bytes;uint64_t fp=0;};
struct CapturedRawField { RawField pixels;uint64_t ingressSequence=0,generation=0; };
using OwnerType = HwOwner;
class QueuedOwner {
    std::unique_ptr<OwnerType> owner;
    uint64_t ingressSequence=0,generation=0; // accessed only on GPU worker
    SerialQueue queue;
    static bool asyncSubmit(){static const bool v=[]{const char *e=std::getenv("PS2_PORT_ENHANCE_ASYNC_GPU");return e && *e=='1';}();return v;}
    template<class Factory> void submit(size_t bytes,Factory f){ if(asyncSubmit())queue.post(bytes,std::move(f)); else queue.invoke(bytes,std::move(f)); }
    static void validatePayload(const void *data,size_t bytes) {
        if(!data || !bytes || bytes>16*1024*1024)
            throw std::invalid_argument("Invalid queued GPU payload");
    }
public:
    explicit QueuedOwner():queue(32,32*1024*1024,[this]{
        GSMem::InitLookupTables();
        owner=std::make_unique<OwnerType>();
    }){}
    // Claude (WORKLOG 98, CR-01): destructors are noexcept. invoke() rethrows a deferred error from an
    // earlier posted task, which used to terminate the process and skip the owner's worker-thread teardown.
    // Report that error, then retry so RawOwner is still destroyed on its own worker.
    ~QueuedOwner(){
        for(int attempt=0;attempt<2;++attempt) {
            try{queue.invoke(0,[this]{return [this]{owner.reset();};});break;}
            catch(const std::exception &e){std::fprintf(stderr,"[private-gpu] owner shutdown: deferred error: %s\n",e.what());}
            catch(...){std::fprintf(stderr,"[private-gpu] owner shutdown: deferred error (unknown)\n");}
        }
        try{queue.close();}catch(...){}
    }
    QueuedOwner(const QueuedOwner&)=delete;QueuedOwner& operator=(const QueuedOwner&)=delete;
    void reset(const void *data,size_t bytes) {
        if(bytes!=4*1024*1024)throw std::invalid_argument("Invalid queued reset size");
        validatePayload(data,bytes);
        queue.invoke(bytes,[=,this]{std::vector<uint8_t> copy(static_cast<const uint8_t*>(data),static_cast<const uint8_t*>(data)+bytes);
            return [this,copy=std::move(copy)]{owner->reset(copy.data(),copy.size());ingressSequence=0;++generation;};});
    }
    void write(unsigned address,uint64_t value) {
        submit(0,[=,this]{return [=,this]{owner->write(address,value);++ingressSequence;};});
    }
    template<class T> void write(ParallelGS::RegisterAddr address,const T &value) {
        static_assert(sizeof(T)==8);uint64_t bits;std::memcpy(&bits,&value,8);write(unsigned(address),bits);
    }
    void gif(unsigned path,const void *data,size_t bytes) {
        validatePayload(data,bytes);
        if(path<1 || path>3 || bytes<16 || bytes%16)throw std::invalid_argument("Invalid queued GIF ingress");
        submit(bytes,[=,this]{std::vector<uint8_t> copy(static_cast<const uint8_t*>(data),static_cast<const uint8_t*>(data)+bytes);
            return [this,path,copy=std::move(copy)]{owner->gif(path,copy.data(),copy.size());++ingressSequence;};});
    }
    // One bounded owner handoff, preserving every packet's own FP environment.
    void gifBatch(std::vector<OwnedGIF> packets) {
        size_t bytes=0;
        if(packets.empty() || packets.size()>32)throw std::invalid_argument("Invalid GIF batch count");
        for(const auto &p:packets) {
            if(p.bytes.empty() || p.bytes.size()%16 || p.bytes.size()>256*1024-bytes)
                throw std::invalid_argument("Invalid GIF batch byte budget");
            bytes+=p.bytes.size();
        }
        submit(bytes,[this,packets=std::move(packets)]()mutable {
            return [this,packets=std::move(packets)] {
                for(const auto &p:packets) {FPControlScope preserve(p.fp);owner->gif(1,p.bytes.data(),p.bytes.size());++ingressSequence;}
            };
        });
    }
    void uploadCT32(uint64_t bitblt,uint64_t position,uint64_t region,uint64_t direction,const void *data,size_t bytes) {
        validatePayload(data,bytes);
        submit(bytes,[=,this]{std::vector<uint8_t> copy(static_cast<const uint8_t*>(data),static_cast<const uint8_t*>(data)+bytes);
            return [=,this,copy=std::move(copy)]{owner->uploadCT32(bitblt,position,region,direction,copy.data(),copy.size());++ingressSequence;};});
    }
    void sync() {queue.invoke(0,[this]{return [this]{owner->sync();};});}
    std::vector<uint8_t> readFIFO(size_t bytes) {
        return queue.invoke(0,[=,this]{return [=,this]{return owner->readFIFO(bytes);};});
    }
    std::vector<uint8_t> snapshot() {
        return queue.invoke(0,[this]{return [this]{return owner->snapshot();};});
    }
    CapturedRawField capture(const ParallelGS::PrivRegisterState &priv,unsigned phase) {
        return queue.invoke(sizeof(priv),[=,this]{return [=,this]{
            return CapturedRawField{owner->field(priv,phase),ingressSequence,generation};
        };});
    }
    std::shared_future<CapturedRawField> captureAsync(const ParallelGS::PrivRegisterState &priv,unsigned phase) {
        return queue.enqueue(sizeof(priv),[=,this]{return [=,this]{
            return CapturedRawField{owner->field(priv,phase),ingressSequence,generation};
        };});
    }
    static bool asyncCapture(){return asyncSubmit();}
    RawField field(const ParallelGS::PrivRegisterState &priv,unsigned phase) {
        return capture(priv,phase).pixels;
    }
    void writePointCT32(unsigned base,unsigned bw,unsigned x,unsigned y,uint32_t value) {
        if(base>16383 || !bw || bw>63 || x>2047 || y>2047)
            throw std::invalid_argument("Unsupported queued CT32 point write");
        queue.invoke(0,[=,this]{return [=,this]{
            auto memory=owner->snapshot();GSMem::WriteCT32(memory.data(),base,bw,x,y,value);
            owner->replaceVRAM(memory);++ingressSequence;
        };});
    }
    std::pair<uint64_t,uint64_t> ingressStatus() {
        return queue.invoke(0,[this]{return [this]{return std::pair{ingressSequence,generation};};});
    }
    uint64_t workerFPControl() {
        return queue.invoke(0,[]{return []{return currentFPControl();};});
    }
};
}
