#pragma once
#include <cstdint>
#include <memory>
#include <vector>
namespace LocalGS {
struct RawField {
    unsigned width = 0, height = 0;
    std::vector<uint8_t> rgba;
    std::shared_ptr<const std::vector<uint8_t>> retained;
    // Keeps a submitted surface immutable across async capture and pacing.
    // The window acquires its own lease before this field can be replaced.
    std::shared_ptr<void> surfaceLease;
    const std::vector<uint8_t>& bytes() const { return retained ? *retained : rgba; }
    // A published frame and the owner's no-picture fallback share immutable storage.
    void seal() { if (!retained) retained = std::make_shared<const std::vector<uint8_t>>(std::move(rgba)); }
};
}
