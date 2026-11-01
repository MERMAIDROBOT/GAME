#pragma once
#include <cstdint>
#include <stdexcept>

namespace ps2port {
// Platform adapters translate their fence result into these three outcomes.
// Timeout and wait failure are never evidence of GPU completion.
enum class SurfaceCompletion { pending, complete, failed };

class SurfaceConsumer {
    std::uint64_t serial_ = 0;
public:
    SurfaceConsumer() = default;
    SurfaceConsumer(const SurfaceConsumer&) = delete;
    SurfaceConsumer& operator=(const SurfaceConsumer&) = delete;
    std::uint64_t serial() const { return serial_; }
    bool busy() const { return serial_ != 0; }
    void acquired(std::uint64_t serial) {
        if (!serial || busy()) throw std::logic_error("Invalid surface acquisition");
        serial_ = serial;
    }
    // release must be the originating producer's callback. Its module and the
    // consumer context must remain alive until this returns true.
    template<class Release>
    bool retire(SurfaceCompletion completion, Release release) {
        if (!busy()) return true;
        if (completion != SurfaceCompletion::complete) return false;
        const auto serial = serial_;
        release(serial);
        serial_ = 0;
        return true;
    }
    // No destructor release: CPU object destruction does not complete GPU work.
    // Window shutdown must drain its context, retire, then unload the producer.
};
}
