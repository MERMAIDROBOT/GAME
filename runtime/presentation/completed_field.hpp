// Shared scanout ownership carried from the qualified native presentation adapter.
// A shared_future owns the completed payload. Resolving must not copy its pixel buffer.
#pragma once
#include <cstdint>
#include <future>
#include <memory>
#include <stdexcept>
namespace ps2port {
template<class Boundary, class Image, class Capture>
struct CompletedField {
    Boundary boundary{};
    std::uint64_t ingressSequence=0, generation=0;
    Image pixels{};
    std::shared_future<Capture> pending{};
    bool ready=true;
    const Image& image() const { return pending.valid() ? pending.get().pixels : pixels; }
};
// The returned immutable wrapper retains the same future, so provider destruction
// cannot invalidate the pixels. Waiting occurs without holding a publisher lock.
template<class Field>
std::shared_ptr<const Field> resolveField(const std::shared_ptr<const Field>& f) {
    if (!f || f->ready) return f;
    if (!f->pending.valid()) throw std::logic_error("Pending field has no capture owner");
    const auto& capture=f->pending.get();
    auto done=std::make_shared<Field>();
    done->boundary=f->boundary;
    done->ingressSequence=capture.ingressSequence;
    done->generation=capture.generation;
    done->pending=f->pending;
    done->ready=true;
    return done;
}
}
