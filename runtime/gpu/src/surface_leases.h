#pragma once
#include <cstdint>
#include <limits>
#include <memory>
#include <unordered_map>

namespace ps2gpu {
// Zero requests the newest available surface. A nonzero serial identifies one
// exact field and must not silently change when a ring slot has been recycled.
template<class Slots>
auto selectSurfaceSlot(Slots& slots, std::uint64_t serial) -> decltype(&slots[0]) {
    decltype(&slots[0]) selected=nullptr;
    for (auto& slot : slots) {
        if (!slot.serial || !slot.surface) continue;
        if (serial && slot.serial==serial) return &slot;
        if (!serial && (!selected || slot.serial>selected->serial)) selected=&slot;
    }
    return selected;
}

// Caller holds the presentation mutex. Leases own the backing surface, not
// Vulkan resources, so a window can finish its GPU use after renderer teardown.
// Serial numbers never reset with a renderer/device generation.
class SurfaceLeases {
    struct Lease { std::shared_ptr<void> surface; std::size_t count; };
    std::unordered_map<std::uint64_t, Lease> leases_;
    std::uint64_t next_ = 0;
public:
    std::uint64_t nextSerial() {
        if (next_ == std::numeric_limits<std::uint64_t>::max()) return 0;
        return ++next_;
    }
    bool hold(std::uint64_t serial, const std::shared_ptr<void>& surface) {
        if (!serial || serial > next_ || !surface) return false;
        auto found = leases_.find(serial);
        if (found == leases_.end()) {
            leases_.emplace(serial, Lease{surface, 1}); return true;
        }
        auto& lease = found->second;
        if (lease.surface.get() != surface.get() || lease.surface.owner_before(surface) ||
            surface.owner_before(lease.surface) || lease.count == std::numeric_limits<std::size_t>::max()) return false;
        ++lease.count; return true;
    }
    bool held(std::uint64_t serial) const { return leases_.find(serial) != leases_.end(); }
    void release(std::uint64_t serial) {
        auto found = leases_.find(serial);
        if (found != leases_.end() && --found->second.count == 0) leases_.erase(found);
    }
};
}
