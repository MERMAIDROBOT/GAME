// Claude 2026-10-19 (WORKLOG 128): differential test for the VIF1 UNPACK fast path. Random command streams;
// prints a hash of VU1 data memory and the ROW registers after each stream. Run with PS2_PORT_VIF_FASTPATH=0 and
// =1 and compare.
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "runtime/ps2_memory.h"
static uint64_t fnv(const uint8_t *p, size_t n) { uint64_t h = 1469598103934665603ull; for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 1099511628211ull; } return h; }
int main(int argc, char **argv)
{
    PS2Memory m; if (!m.initialize()) return 2;
    uint32_t seed = argc > 1 ? uint32_t(atoi(argv[1])) : 1u;
    auto rnd = [&] { seed = seed * 1103515245u + 12345u; return (seed >> 8) & 0xFFFFFFu; };
    uint64_t fastEligible = 0;
    for (int stream = 0; stream < 3000; stream++)
    {
        std::vector<uint32_t> w;
        const int cmds = 1 + int(rnd() % 6);
        for (int c = 0; c < cmds; c++)
        {
            const uint32_t k = rnd() % 10;
            if (k == 0) { uint32_t cl = rnd() % 5, wl = (rnd() % 3 == 0) ? rnd() % 5 : cl; w.push_back((0x01u << 24) | (wl << 8) | cl); }
            else if (k == 1) w.push_back((0x05u << 24) | ((rnd() % 4 == 0) ? (rnd() % 3) : 0u));
            else if (k == 2) { w.push_back(0x20u << 24); w.push_back(rnd() | (rnd() << 24)); }
            else if (k == 3) { w.push_back(0x30u << 24); for (int i = 0; i < 4; i++) w.push_back(rnd() ^ (rnd() << 12)); }
            else if (k == 4) { w.push_back(0x31u << 24); for (int i = 0; i < 4; i++) w.push_back(rnd() ^ (rnd() << 12)); }
            else
            {
                const uint32_t vn = rnd() % 4, vl = rnd() % 4, mask = (rnd() % 4 == 0) ? 1u : 0u;
                const uint32_t num = 1 + rnd() % 40;
                uint32_t addr = rnd() % 1024; if (rnd() % 3 == 0) addr = rnd() % 900;
                const uint32_t imm = addr | ((rnd() % 2) << 14) | ((rnd() % 6 == 0) ? 0x8000u : 0u);
                w.push_back((uint32_t(0x60u | (mask << 4) | (vn << 2) | vl) << 24) | (num << 16) | imm);
                const uint32_t comps = vn + 1, bits = vl == 0 ? 32 : vl == 1 ? 16 : vl == 2 ? 8 : (vn == 3 ? 4 : 16);
                const uint32_t bitsPerVec = (vl == 3 && vn == 3) ? 16 : comps * bits;
                // Source size depends on CL/WL; over-provide data words (the interpreter consumes what it needs and
                // the remainder is parsed as further commands, so pad with NOPs instead: compute conservatively).
                const uint32_t cycle = m.vif1_regs.cycle; (void)cycle;
                const uint32_t bytes = ((num * bitsPerVec + 7) / 8 + 3) & ~3u;
                for (uint32_t i = 0; i < bytes / 4; i++) w.push_back(rnd() ^ (rnd() << 16));
                if ((m.vif1_regs.mode & 3u) == 0u && !mask) fastEligible++;
            }
        }
        // Commands are interpreted in order; source counts that differ from 'num' (CL<WL) make the tail parse as
        // commands in both runs identically, which keeps the comparison valid.
        m.processVIF1Data(reinterpret_cast<const uint8_t *>(w.data()), uint32_t(w.size() * 4));
        uint8_t regs[32]; std::memcpy(regs, m.vif1_regs.row, 16); std::memcpy(regs + 16, m.vif1_regs.col, 16);
        std::printf("%d %016llx %016llx\n", stream, (unsigned long long)fnv(m.getVU1Data(), PS2_VU1_DATA_SIZE), (unsigned long long)fnv(regs, 32));
    }
    std::fprintf(stderr, "unpacks with mode0/no-mask at generation: %llu\n", (unsigned long long)fastEligible);
    return 0;
}
