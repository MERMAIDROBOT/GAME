#pragma once
#include <chrono>
#include <future>
#include <memory>

namespace LocalGS {
template<class Field>
bool pixelsReady(const std::shared_ptr<const Field>& field) {
    return field && (field->ready ||
        (field->pending.valid() && field->pending.wait_for(std::chrono::seconds(0)) == std::future_status::ready));
}

// Only completed due fields may reach the presenter. Retaining the previous
// picture during a late capture keeps window/event handling out of GPU waits.
// Claude 2026-11-11: the newest field whose pixels are done, from the last few published. A capture is still
// on the GPU worker as the next field is published, so the newest alone was rarely ready: in SSX 3's movies
// (~120 strips sent ahead of each capture) three fields of four kept the previous picture.
template<class Queue>
typename Queue::value_type newestReady(const Queue& recent, unsigned long long minTick) {
    for (auto it = recent.rbegin(); it != recent.rend(); ++it)
        if ((*it)->boundary.tick >= minTick && pixelsReady(*it)) return *it;
    return {};
}

template<class Queue>
typename Queue::value_type newestReadyDue(const Queue& queue, double now, double offset,
                                         double fieldNs, double delay, unsigned long long minTick) {
    typename Queue::value_type pick;
    for (const auto& field : queue) {
        if (double(field->boundary.tick) * fieldNs + offset + delay > now) break;
        if (field->boundary.tick >= minTick && pixelsReady(field)) pick = field;
    }
    return pick;
}
}
