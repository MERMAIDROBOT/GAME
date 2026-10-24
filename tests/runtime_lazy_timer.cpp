// Claude 2026-10-19 (WORKLOG 128): differential test for lazy EE timers. Prints a trace of every observable
// (interrupt masks per advance call, timer register reads) for a random script; run with PS2_PORT_LAZY_EE_TIMERS=0
// and =1 and diff the traces.
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <memory>
#include "runtime/ps2_memory.h"
int main(int argc, char **argv)
{
    PS2Memory m; if (!m.initialize()) return 2;
    std::unique_ptr<PS2Memory> second;
    if (argc > 2) { second = std::make_unique<PS2Memory>(); if (!second->initialize()) return 3; second->writeIORegister(0x10000010u, 0x180u); }
    uint32_t seed = argc > 1 ? uint32_t(atoi(argv[1])) : 1u;
    auto rnd = [&] { seed = seed * 1103515245u + 12345u; return (seed >> 8) & 0xFFFFFFu; };
    const uint32_t base[4] = {0x10000000u, 0x10000800u, 0x10001000u, 0x10001800u};
    for (int step = 0; step < 200000; step++)
    {
        const uint32_t r = rnd() % 1000;
        const int t = int(rnd() % 4);
        if (r < 3)        // reconfigure: mode with CUE and random CLKS/ZRET/CMPE/OVFE, plus compare/count
        {
            const uint32_t mode = 0x80u | (rnd() & 0x3u) | ((rnd() & 1u) << 6) | ((rnd() & 1u) << 8) | ((rnd() & 1u) << 9);
            m.writeIORegister(base[t] + 0x20u, rnd() & 0xFFFFu);
            m.writeIORegister(base[t] + 0x00u, rnd() & 0xFFFFu);
            m.writeIORegister(base[t] + 0x10u, mode);
        }
        else if (r < 8)   // acknowledge flags (write 1 to EQUF/OVFF) keeping config
        {
            const uint32_t mode = m.readIORegister(base[t] + 0x10u);
            m.writeIORegister(base[t] + 0x10u, mode | 0xC00u);
            std::printf("ack %d %x\n", t, mode);
        }
        else if (r < 40)  // guest reads count / mode
        {
            std::printf("rd %d %x %x\n", t, m.readIORegister(base[t] + 0x00u), m.readIORegister(base[t] + 0x10u));
        }
        const uint32_t cyc = (rnd() % 100 < 2) ? (rnd() % 200000) : (rnd() % 600);
        if (second) { auto b = second->advanceEeTimers(cyc / 2 + 1); if (b) std::printf("second irq %d %x\n", step, b); }
        if (step % 127 == 0) std::printf("next %d %llu\n", step, (unsigned long long)m.cyclesUntilNextEeTimerInterrupt());
        const uint32_t mask = m.advanceEeTimers(cyc);
        if (mask) std::printf("irq step=%d mask=%x\n", step, mask);
    }
    for (int t = 0; t < 4; t++) std::printf("end %d %x %x\n", t, m.readIORegister(base[t]), m.readIORegister(base[t] + 0x10u));
    return 0;
}
