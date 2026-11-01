#pragma once
#include <condition_variable>
#include <deque>
#include <functional>
#include <future>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

// CPU file/decode work only. Renderer objects never cross this boundary.
// A consumer claims queued work immediately, or joins an already running decode.
// A failed decode is still reported to the existing synchronous failure path.
namespace LocalTexture {
class BlobPrefetch {
public:
    using Pixels = std::vector<unsigned char>;
    using Loader = std::function<Pixels()>;
    explicit BlobPrefetch(size_t limit = 64u << 20) : limit_(limit) {
        for (int i = 0; i < 2; ++i) workers_.emplace_back([this] { run(); });
    }
    ~BlobPrefetch() {
        { std::lock_guard<std::mutex> lock(mutex_); stopping_ = true; }
        changed_.notify_all();
        for (auto& t : workers_) t.join();
    }
    bool enqueue(const std::string& key, size_t bytes, Loader load) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (tasks_.count(key)) return true;
        if (!bytes || bytes > limit_) return false;
        // Unused palette alternatives must not monopolize the bounded cache forever.
        for (auto i = tasks_.begin(); i != tasks_.end() && (used_ > limit_ - bytes || tasks_.size() >= 128); ) {
            auto& task = i->second;
            if (task->started && task->ready.wait_for(std::chrono::seconds(0)) == std::future_status::ready) {
                used_ -= task->bytes; i = tasks_.erase(i);
            } else ++i;
        }
        if (used_ > limit_ - bytes || tasks_.size() >= 128) return false;
        auto task = std::make_shared<Task>();
        task->bytes = bytes; task->load = std::move(load);
        tasks_.emplace(key, task); queue_.push_back(task); used_ += bytes;
        changed_.notify_one();
        return true;
    }
    enum class Taken { Ready, Claimed, Joined };
    bool take(const std::string& key, Pixels& out, Taken* how = nullptr) {
        std::shared_ptr<Task> task;
        bool claimed = false;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            auto i = tasks_.find(key);
            if (i == tasks_.end()) return false;
            task = i->second;
            if (how) *how = task->ready.wait_for(std::chrono::seconds(0)) == std::future_status::ready ? Taken::Ready : Taken::Joined;
            if (!task->started) { task->started = true; claimed = true; if (how) *how = Taken::Claimed; }
        }
        if (claimed) finish(task);
        task->ready.wait();
        // Only the renderer consumer takes pixels; the worker has finished before this move.
        out = std::move(task->pixels);
        {
            std::lock_guard<std::mutex> lock(mutex_);
            tasks_.erase(key); used_ -= task->bytes;
        }
        return true;
    }
private:
    struct Task {
        size_t bytes = 0;
        bool started = false;
        Loader load;
        Pixels pixels;
        std::promise<void> done;
        std::future<void> ready = done.get_future();
    };
    static void finish(const std::shared_ptr<Task>& task) {
        try { task->pixels = task->load(); } catch (...) { task->pixels.clear(); }
        task->load = {};
        task->done.set_value();
    }
    void run() {
        for (;;) {
            std::shared_ptr<Task> task;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                changed_.wait(lock, [this] { return stopping_ || !queue_.empty(); });
                if (stopping_) return;
                task = queue_.front(); queue_.pop_front();
                if (task->started) continue;
                task->started = true;
            }
            finish(task);
        }
    }
    size_t limit_, used_ = 0;
    std::mutex mutex_;
    std::condition_variable changed_;
    bool stopping_ = false;
    std::unordered_map<std::string, std::shared_ptr<Task>> tasks_;
    std::deque<std::shared_ptr<Task>> queue_;
    std::vector<std::thread> workers_;
};
}
