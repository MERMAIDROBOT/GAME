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
        if (pending_ && ready(*pending_)) {
            completed_ = std::move(pending_);
        }
        if (!pending_ && (!completed_ || latest->boundary.tick > completed_->boundary.tick)) {
            if (ready(*latest)) completed_ = std::move(latest);
            else pending_ = std::move(latest);
        }
        return completed_;
    }
};
}
