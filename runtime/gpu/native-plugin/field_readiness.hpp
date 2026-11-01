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
