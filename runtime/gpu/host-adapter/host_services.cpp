// Claude 2026-10-20: stand-ins for the parts of ps2-port-engine's host that gpu_bridge/gs reference, for an
// offscreen GS replay (no EE, no VIF worker thread, no window, no timeline).
#include <chrono>
#include <functional>
#include "ps2host/run.h"
#include "ps2host/timeline.h"
#include "ps2host/dma.h"
namespace ps2host {
std::uint32_t g_dma_source = 0;
static u64 now_ns() { return u64(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count()); }
u64 ClockNs() { return now_ns(); }
void FastThread() {}
u64 IdleNanoseconds() { return 0; }
void JoinVif1(const char *, u32) {}
u64 LastVblankNs() { return now_ns(); }
bool OnVif1Worker() { return false; }
bool Vif1Async() { return false; }
u64 Vif1BusyMicroseconds() { return 0; }
void Vif1Run(std::function<void()> command) { command(); }
void Vif1RunFrame(std::function<void()> command) { command(); }
bool VirtualClock() { return false; }
namespace timeline {
bool On() { return false; }
std::uint64_t Now() { return now_ns(); }
void NameThread(const char *) {}
void Span(const char *, std::uint64_t, std::uint64_t) {}
void Instant(const char *, std::uint64_t, std::uint64_t) {}
}
}
