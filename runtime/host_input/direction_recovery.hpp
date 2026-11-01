// Optional recovery for remote touchscreen direction dropouts. GPL-3.0.
// Derived from the locally reviewed October 29 Console behavior; ordinary
// keyboards, physical controllers and scripted input remain unchanged.
#pragma once
#include <array>
#include <cstdint>

namespace ps2port {
struct DirectionRecovery {
    static constexpr uint16_t directions = 0x00f0;
    static constexpr std::array<uint16_t, 4> bits{0x10, 0x20, 0x40, 0x80};
    static constexpr std::array<uint16_t, 4> opposites{0x40, 0x80, 0x10, 0x20};
    uint16_t previous = 0;
    std::array<int64_t, 4> deadlines{};
    int64_t lastOtherRelease = 0, lastTime = 0;
    bool linked = false, initialized = false;

    void reset() { *this = {}; }

    uint16_t apply(uint16_t raw, int64_t now) {
        // Reset on clock regression; stale deadlines cannot retain controls.
        if (now < 0 || (initialized && now < lastTime)) reset();
        if (now < 0) return raw;
        initialized = true;
        lastTime = now;
        const uint16_t released = uint16_t(previous & ~raw);
        if (released & uint16_t(~directions)) {
            linked = true;
            lastOtherRelease = now;
        }
        // A real repress or reversal ends this recovery gesture, including its
        // release linkage, so releasing that new gesture cannot revive it.
        for (unsigned i = 0; i < bits.size(); ++i)
            if (deadlines[i] && (raw & (bits[i] | opposites[i]))) linked = false;
        uint16_t output = raw;
        for (unsigned i = 0; i < bits.size(); ++i) {
            if (raw & (bits[i] | opposites[i])) deadlines[i] = 0;
            else if ((released & bits[i]) && linked && now - lastOtherRelease <= 100)
                deadlines[i] = now <= INT64_MAX - 300 ? now + 300 : now;
            if (now < deadlines[i]) output |= bits[i];
        }
        previous = raw;
        return output;
    }
};
} // namespace ps2port
