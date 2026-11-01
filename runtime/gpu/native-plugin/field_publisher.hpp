// Private immutable completed fields. UI reads pixels and their clock together.
#pragma once
#include "queued_owner.hpp"
#include "field_readiness.hpp"
#include "runtime/gs/gs_frontend.h"
#include <atomic>
#include <deque>
#include <future>
#include <mutex>
#include <cmath>
#include <memory>
#include <chrono>
#include <cstdio>
#include <cstdlib>
namespace LocalGS {
struct CompletedField {
    GSFieldBoundary boundary;
    uint64_t ingressSequence=0,generation=0;
    RawField pixels;
    // Claude 2026-10-13 (WORKLOG 84): when asynchronous capture is enabled the pixels arrive later;
    // the presenter resolves them (waiting if needed) instead of the emulated EE thread.
    std::shared_future<CapturedRawField> pending{};
    bool ready=true;
    // A shared_future retains its result; read it in place rather than copying
    // the full scanout vector into a second CompletedField.
    const RawField& image() const { return pending.valid() ? pending.get().pixels : pixels; }
};
class FieldPublisher {
    QueuedOwner &owner;
    std::shared_ptr<const CompletedField> completed; // libc++ shared_ptr atomic free functions
public:
    explicit FieldPublisher(QueuedOwner &gpu):owner(gpu){}
    void capture(GSFieldBoundary boundary) {
        if(!boundary.tick || boundary.phase!=(boundary.tick&1u))
            throw std::invalid_argument("Invalid field boundary clock/parity");
        ParallelGS::PrivRegisterState priv={};
        static_assert(offsetof(ParallelGS::PrivRegisterState,bgcolor)==14*16);
        for(unsigned i=0;i<15;i++)std::memcpy(&priv.qwords_lo[i*2],&boundary.privileged[i],8);
        // Called synchronously by EE boundary consumer, before later guest ingress.
        // Raw scanout and its sequence are one transaction on the GPU worker.
        // Claude 2026-10-13 (WORKLOG 96): PS2_PORT_ENHANCE_FIELDLOG=1 enables the same field log in the user
        // app, whose launcher strips PS2_PORT_PRIVATE_FIELD_METRICS. Used to find hitches during play.
        static const bool metrics=[] {const char *v=std::getenv("PS2_PORT_PRIVATE_FIELD_METRICS");const char *e=std::getenv("PS2_PORT_ENHANCE_FIELDLOG");return (v && *v=='1') || (e && *e=='1');}();
        FPControlScope preserveCallerFP(currentFPControl());
        const auto begin=metrics?std::chrono::steady_clock::now():std::chrono::steady_clock::time_point{};
        if(QueuedOwner::asyncCapture()) {
            auto future=owner.captureAsync(priv,boundary.phase);
            if(metrics) {
                const auto end=std::chrono::steady_clock::now();
                std::fprintf(stderr,"[private-gpu field] ns=%lld tick=%llu phase=%u ingress=0 generation=0 capture_ns=%lld pmode=%llu smode2=%llu fbp=%llu\n",
                    (long long)std::chrono::duration_cast<std::chrono::nanoseconds>(end.time_since_epoch()).count(),
                    (unsigned long long)boundary.tick,boundary.phase,(long long)std::chrono::duration_cast<std::chrono::nanoseconds>(end-begin).count(),
                    (unsigned long long)boundary.privileged[0],(unsigned long long)boundary.privileged[2],
                    (unsigned long long)(boundary.privileged[(boundary.privileged[0]&3)==1?7:9]&511));
            }
            publish(std::make_shared<const CompletedField>(CompletedField{std::move(boundary),0,0,{},std::move(future),false}));
            return;
        }
        auto captured=owner.capture(priv,boundary.phase);
        if(metrics) {
            const auto end=std::chrono::steady_clock::now();
            const auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(end.time_since_epoch()).count();
            const auto cost=std::chrono::duration_cast<std::chrono::nanoseconds>(end-begin).count();
            // Completion cadence, not unique game frames or monitor presentation.
            std::fprintf(stderr,"[private-gpu field] ns=%lld tick=%llu phase=%u ingress=%llu generation=%llu capture_ns=%lld pmode=%llu smode2=%llu fbp=%llu\n",
                (long long)ns,(unsigned long long)boundary.tick,boundary.phase,
                (unsigned long long)captured.ingressSequence,(unsigned long long)captured.generation,
                (long long)cost,(unsigned long long)boundary.privileged[0],(unsigned long long)boundary.privileged[2],
                (unsigned long long)(boundary.privileged[(boundary.privileged[0]&3)==1?7:9]&511));
        }
        publish(std::make_shared<const CompletedField>(CompletedField{
            std::move(boundary),captured.ingressSequence,captured.generation,std::move(captured.pixels)}));
    }
    void publish(std::shared_ptr<const CompletedField> frame) {
        if(pacingDelayNs()>0) {
            // Claude 2026-10-13 (WORKLOG 84): jitter buffer. Emulated fields arrive in bursts
            // (10-28 ms apart, mean 16.7 ms). Map each tick onto an evenly spaced timeline and
            // let the presenter take the newest field whose ideal time has passed.
            const int64_t now=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
            std::lock_guard<std::mutex> lock(pacingMutex);
            const double raw=double(now)-double(frame->boundary.tick)*kFieldNs;
            if(!pacingAnchored || std::abs(raw-pacingOffset)>60e6) {pacingOffset=raw;pacingAnchored=true;queue.clear();}
            else pacingOffset+=0.02*(raw-pacingOffset);
            queue.push_back(frame);while(queue.size()>8)queue.pop_front();
        }
        std::atomic_store_explicit(&completed,std::move(frame),std::memory_order_release);
    }
    std::shared_ptr<const CompletedField> latest() const {
        const int64_t now=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
        std::shared_ptr<const CompletedField> pick;
        uint64_t epoch;
        {
            std::lock_guard<std::mutex> lock(pacingMutex);
            epoch=presentationEpoch;
            const uint64_t floor=lastPresented?lastPresented->boundary.tick:0;
            if(pacingDelayNs()>0)
                pick=newestReadyDue(queue,double(now),pacingOffset,kFieldNs,double(pacingDelayNs()),floor);
            else {
                auto newest=std::atomic_load_explicit(&completed,std::memory_order_acquire);
                if(pixelsReady(newest) && newest->boundary.tick>=floor)pick=newest;
            }
            if(!pick)return lastPresented;
        }
        auto done=resolve(pick); // readiness was checked; this get cannot wait for GPU work
        std::lock_guard<std::mutex> lock(pacingMutex);
        if(epoch!=presentationEpoch)return {};
        if(!lastPresented || done->boundary.tick>=lastPresented->boundary.tick)lastPresented=done;
        return lastPresented;
    }
    // Waits for GPU pixels without holding the publisher lock, so the EE thread never blocks here.
    std::shared_ptr<const CompletedField> resolve(const std::shared_ptr<const CompletedField> &f) const {
        if(f->ready)return f;
        const auto& r=f->pending.get();
        auto done=std::make_shared<const CompletedField>(CompletedField{f->boundary,r.ingressSequence,r.generation,{},f->pending,true});
        {std::lock_guard<std::mutex> lock(pacingMutex);for(auto &q:queue)if(q==f)q=done;}
        auto expected=f;std::atomic_compare_exchange_strong(&const_cast<FieldPublisher*>(this)->completed,&expected,done);
        return done;
    }
    void clear(){
        {std::lock_guard<std::mutex> lock(pacingMutex);queue.clear();pacingAnchored=false;lastPresented.reset();++presentationEpoch;}
        std::atomic_store_explicit(&completed,std::shared_ptr<const CompletedField>{},std::memory_order_release);}
private:
    static constexpr double kFieldNs=1e9/59.94;
    static int64_t pacingDelayNs(){static const int64_t v=[] {const char *e=std::getenv("PS2_PORT_ENHANCE_PACING_MS");return e?int64_t(std::atof(e)*1e6):int64_t(0);}();return v;}
    mutable std::mutex pacingMutex;
    mutable std::deque<std::shared_ptr<const CompletedField>> queue;
    mutable std::shared_ptr<const CompletedField> lastPresented;
    uint64_t presentationEpoch=0;
    double pacingOffset=0;bool pacingAnchored=false;
};
}
