#pragma once
#include <memory>
#include <cstdint>

namespace LocalGS {
// A CPU submission is not a finished GPU image. Keep one pending candidate
// alive until its fence completes, even when newer submissions keep arriving.
// Otherwise polling only the newest fence can starve presentation indefinitely.
template<class Field> class CompletionGate {
    std::shared_ptr<const Field> pending_, completed_;
    std::uint64_t generation_ = 0;
public:
    void clear() { pending_.reset(); completed_.reset(); generation_ = 0; }
    template<class Ready>
    std::shared_ptr<const Field> select(std::shared_ptr<const Field> latest, Ready ready) {
        if (!latest) { clear(); return {}; }
        if (latest->generation != generation_) {
            clear(); generation_ = latest->generation;
        }
        // Claude 2026-11-12: the newest finished image is shown. Waiting on one pending candidate while newer ones
        // arrived ignored them, and showed that candidate late when it finished: a picture shown twice, then one
        // skipped - uneven motion in every title (Kelly Slater: its demo and water judder). The pending candidate
        // still guarantees progress when no newest one is ever finished at the moment of asking.
        const bool newer = !completed_ || latest->boundary.tick > completed_->boundary.tick;
        if (newer && ready(*latest)) {
            completed_ = std::move(latest);
            pending_.reset();
            return completed_;
        }
        if (pending_ && ready(*pending_)) {
            completed_ = std::move(pending_);
        }
        if (newer && latest && (!completed_ || latest->boundary.tick > completed_->boundary.tick) && !pending_)
            pending_ = std::move(latest);
        return completed_;
    }
};
}
