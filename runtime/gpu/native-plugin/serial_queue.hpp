// Private bounded single-owner executor. No game assets or renderer fallback.
#pragma once
#include <condition_variable>
#include <deque>
#include <functional>
#include <future>
#include <mutex>
#include <thread>
#include <type_traits>
#include <stdexcept>
#include <cstdint>
namespace LocalGS {
inline uint64_t currentFPControl() {
    uint64_t value=0;
#if defined(__aarch64__)
    __asm__ volatile("mrs %0, fpcr" : "=r"(value));
#endif
    return value;
}
inline void setFPControl(uint64_t value) {
#if defined(__aarch64__)
    __asm__ volatile("msr fpcr, %0" :: "r"(value));
#else
    (void)value;
#endif
}
struct FPControlScope {
    uint64_t saved=currentFPControl();
    explicit FPControlScope(uint64_t value){setFPControl(value);}
    ~FPControlScope(){setFPControl(saved);}
};
class SerialQueue {
    struct Entry {size_t bytes;uint64_t fp;std::function<void()> run;};
    const size_t maxTasks,maxBytes;
    std::mutex mutex,shutdownMutex;std::condition_variable ready,capacity;
    std::deque<Entry> pending;size_t tasks=0,bytes=0;
    bool closing=false;std::thread worker;std::thread::id ownerID;
    std::exception_ptr deferredError; // Claude 2026-10-13: first failure of a posted (non-waiting) task
    void execute(std::function<void()> setup,std::promise<void> initialized) {
        ownerID=std::this_thread::get_id();
        try {setup();initialized.set_value();}
        catch(...){initialized.set_exception(std::current_exception());return;}
        for(;;) {
            Entry entry;
            {std::unique_lock<std::mutex> lock(mutex);
             ready.wait(lock,[&]{return closing || !pending.empty();});
             if(pending.empty())return;
             entry=std::move(pending.front());pending.pop_front();}
            {FPControlScope scope(entry.fp);entry.run();}
            {std::lock_guard<std::mutex> lock(mutex);--tasks;bytes-=entry.bytes;}
            capacity.notify_all();
        }
    }
public:
    explicit SerialQueue(size_t taskLimit=32,size_t byteLimit=32*1024*1024,
                         std::function<void()> setup=[]{}):maxTasks(taskLimit),maxBytes(byteLimit) {
        if(!maxTasks || !maxBytes)throw std::invalid_argument("Invalid owner queue limits");
        std::promise<void> initialized;auto done=initialized.get_future();
        worker=std::thread([this,setup=std::move(setup),init=std::move(initialized)]() mutable {execute(std::move(setup),std::move(init));});
        try{done.get();}catch(...){worker.join();throw;}
    }
    SerialQueue(const SerialQueue&)=delete;SerialQueue& operator=(const SerialQueue&)=delete;
    ~SerialQueue(){close();}
    void close() {
        if(std::this_thread::get_id()==ownerID)std::terminate();
        std::lock_guard<std::mutex> shutdown(shutdownMutex);
        {std::lock_guard<std::mutex> lock(mutex);closing=true;}
        ready.notify_all();capacity.notify_all();if(worker.joinable())worker.join();
    }
    // Factory copies ingress only after capacity is available, under submission
    // ordering lock. Captured payload bytes must be declared accurately.
    template<class Factory> auto invoke(size_t payloadBytes,Factory factory)
        -> std::invoke_result_t<std::invoke_result_t<Factory>> {
        using Result=std::invoke_result_t<std::invoke_result_t<Factory>>;
        if(std::this_thread::get_id()==ownerID)throw std::logic_error("Recursive owner submission");
        if(payloadBytes>maxBytes)throw std::invalid_argument("Owner payload exceeds byte budget");
        const uint64_t fp=currentFPControl();
        std::unique_lock<std::mutex> lock(mutex);
        if(deferredError){auto e=deferredError;deferredError=nullptr;std::rethrow_exception(e);}
        capacity.wait(lock,[&]{return closing || (tasks<maxTasks && payloadBytes<=maxBytes-bytes);});
        if(closing)throw std::runtime_error("Owner queue is closed");
        // Claude (WORKLOG 98, CR-03): a posted task may have failed while this caller waited for capacity.
        if(deferredError){auto e=deferredError;deferredError=nullptr;std::rethrow_exception(e);}
        auto task=std::make_shared<std::packaged_task<Result()>>(factory());auto future=task->get_future();
        pending.push_back({payloadBytes,fp,[task]{(*task)();}});++tasks;bytes+=payloadBytes;
        lock.unlock();ready.notify_one();return future.get();
    }
    // Claude 2026-10-13 (WORKLOG 84): ordered, bounded, non-waiting submission for void work.
    // A failure is captured and rethrown on the caller's next submission of either kind.
    template<class Factory> void post(size_t payloadBytes,Factory factory) {
        if(std::this_thread::get_id()==ownerID)throw std::logic_error("Recursive owner submission");
        if(payloadBytes>maxBytes)throw std::invalid_argument("Owner payload exceeds byte budget");
        const uint64_t fp=currentFPControl();
        std::unique_lock<std::mutex> lock(mutex);
        if(deferredError){auto e=deferredError;deferredError=nullptr;std::rethrow_exception(e);}
        capacity.wait(lock,[&]{return closing || (tasks<maxTasks && payloadBytes<=maxBytes-bytes);});
        if(closing)throw std::runtime_error("Owner queue is closed");
        // Claude (WORKLOG 98, CR-03): a posted task may have failed while this caller waited for capacity.
        if(deferredError){auto e=deferredError;deferredError=nullptr;std::rethrow_exception(e);}
        auto work=factory();
        pending.push_back({payloadBytes,fp,[this,work=std::move(work)]()mutable{
            try{work();}catch(...){std::lock_guard<std::mutex> g(mutex);if(!deferredError)deferredError=std::current_exception();}
        }});++tasks;bytes+=payloadBytes;
        lock.unlock();ready.notify_one();
    }
    // Claude 2026-10-13 (WORKLOG 84): ordered submission whose result is collected later (e.g. by the presenter).
    template<class Factory> auto enqueue(size_t payloadBytes,Factory factory)
        -> std::shared_future<std::invoke_result_t<std::invoke_result_t<Factory>>> {
        using Result=std::invoke_result_t<std::invoke_result_t<Factory>>;
        if(std::this_thread::get_id()==ownerID)throw std::logic_error("Recursive owner submission");
        if(payloadBytes>maxBytes)throw std::invalid_argument("Owner payload exceeds byte budget");   // CR-07
        const uint64_t fp=currentFPControl();
        std::unique_lock<std::mutex> lock(mutex);
        if(deferredError){auto e=deferredError;deferredError=nullptr;std::rethrow_exception(e);}
        capacity.wait(lock,[&]{return closing || (tasks<maxTasks && payloadBytes<=maxBytes-bytes);});
        if(closing)throw std::runtime_error("Owner queue is closed");
        // Claude (WORKLOG 98, CR-03): a posted task may have failed while this caller waited for capacity.
        if(deferredError){auto e=deferredError;deferredError=nullptr;std::rethrow_exception(e);}
        auto task=std::make_shared<std::packaged_task<Result()>>(factory());std::shared_future<Result> future=task->get_future().share();
        pending.push_back({payloadBytes,fp,[task]{(*task)();}});++tasks;bytes+=payloadBytes;
        lock.unlock();ready.notify_one();return future;
    }
    void rethrowDeferred() {
        std::lock_guard<std::mutex> lock(mutex);
        if(deferredError){auto e=deferredError;deferredError=nullptr;std::rethrow_exception(e);}
    }
};
}
