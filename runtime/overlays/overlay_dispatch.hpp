#pragma once
// Overlay-aware function lookup. Code the game loads at run time (level programs, overlays) is
// translated as separate programs, and several can occupy the same guest addresses. Each one is
// registered with its own dense function table and a content signature: guest words at which it
// differs from every other overlay and from the boot program. A lookup uses an overlay's table only
// while all of its signature words are present in guest RAM, so the program that is actually
// loaded is the one that runs; otherwise the caller falls back to the boot program's table.
#include <cstdint>
#include <cstring>
#include <vector>

struct R5900Context;
class PS2Runtime;

namespace ps2port {

using OverlayFunction = void (*)(uint8_t *, R5900Context *, PS2Runtime *);

struct OverlaySignatureWord {
    uint32_t address;
    uint32_t value;
};

struct OverlayTable {
    const char *id;
    uint32_t base;
    uint32_t end;
    OverlayFunction *table;
    uint32_t slots;
    const OverlaySignatureWord *signature;
    uint32_t signatureWords;
};

class OverlayRegistry {
public:
    static constexpr uint32_t kRamBytes = 32u * 1024u * 1024u;

    void add(const OverlayTable &overlay) { overlays_.push_back(overlay); last_ = nullptr; }
    void clear() { overlays_.clear(); last_ = nullptr; }
    bool empty() const { return overlays_.empty(); }

    // True when every signature word of `overlay` is in `rdram` (guest main RAM, kRamBytes long).
    static bool resident(const uint8_t *rdram, const OverlayTable &overlay) {
        if (overlay.signatureWords == 0u)
            return false;  // without distinguishing words residency cannot be shown
        for (uint32_t i = 0; i < overlay.signatureWords; ++i) {
            const uint32_t at = overlay.signature[i].address & (kRamBytes - 1u);
            if (at > kRamBytes - 4u)
                return false;
            uint32_t word;
            std::memcpy(&word, rdram + at, 4);
            if (word != overlay.signature[i].value)
                return false;
        }
        return true;
    }

    // Whether a resident overlay covers `address`, and its function there (nullptr: none
    // translated). A covered address must not fall back to the boot program: the overlay has
    // replaced whatever the boot program had at that address.
    struct Hit {
        bool covered;
        OverlayFunction function;
    };

    Hit resolve(const uint8_t *rdram, uint32_t address) {
        if (overlays_.empty() || rdram == nullptr || (address & 3u) != 0u)
            return {false, nullptr};
        if (last_ != nullptr && covers(*last_, address) && resident(rdram, *last_))
            return {true, slot(*last_, address)};
        for (const OverlayTable &overlay : overlays_) {
            if (&overlay == last_ || !covers(overlay, address) || !resident(rdram, overlay))
                continue;
            last_ = &overlay;
            return {true, slot(overlay, address)};
        }
        return {false, nullptr};
    }

private:
    static bool covers(const OverlayTable &overlay, uint32_t address) {
        return address >= overlay.base && address < overlay.end && ((address - overlay.base) >> 2) < overlay.slots;
    }
    static OverlayFunction slot(const OverlayTable &overlay, uint32_t address) {
        return overlay.table[(address - overlay.base) >> 2];
    }

    std::vector<OverlayTable> overlays_;
    const OverlayTable *last_ = nullptr;
};

inline OverlayRegistry &overlayRegistry() {
    static OverlayRegistry registry;
    return registry;
}

}  // namespace ps2port
