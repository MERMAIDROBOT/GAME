// VF0 reads (0, 0, 0, 1) whatever is aimed at it: an upper instruction with VF0 as destination
// (arithmetic kept only for its flags) must not change what the lower instruction of the same pair,
// or any later instruction, reads from VF0.
#include "runtime/ps2_vu1.h"
#include "runtime/gs/gs_frontend.h"
#include <cstdio>
#include <cstring>
#include <vector>

alignas(64) static unsigned char g_gs[sizeof(GS)];

int main()
{
    constexpr uint32_t NOP_LOWER = 0x8000033Cu, NOP_UPPER = 0x000002FFu, E_BIT = 1u << 30;
    constexpr uint32_t ADD_VF0 = 0x00C07828u; // ADD.yz vf0, vf15, vf0
    constexpr uint32_t SQ_VF0 = 0x02A30002u;  // SQ.yw vf0, 2(vi3)
    const float expected[4] = {0.0f, 0.0f, 0.0f, 1.0f};
    int failures = 0;
    for (uint32_t distance = 0; distance < 6u; ++distance)
    {
        std::vector<uint8_t> code(16384, 0), data(16384, 0);
        auto put = [&](uint32_t pair, uint32_t lower, uint32_t upper)
        {
            std::memcpy(&code[8 * pair], &lower, 4);
            std::memcpy(&code[8 * pair + 4], &upper, 4);
        };
        for (uint32_t k = 0; k < 16u; ++k)
            put(k, NOP_LOWER, NOP_UPPER);
        put(0, NOP_LOWER, ADD_VF0);
        put(distance, SQ_VF0, distance == 0u ? ADD_VF0 : NOP_UPPER);
        put(12, NOP_LOWER, NOP_UPPER | E_BIT);
        VU1Interpreter vu(VU1Interpreter::Unit::VU1);
        vu.reset();
        vu.state().vi[3] = 0x180;
        for (float &lane : vu.state().vf[15])
            lane = 1.5f;
        vu.execute(code.data(), 16384u, data.data(), 16384u, *reinterpret_cast<GS *>(g_gs), nullptr, 0u, 0u, 0u, 1000u);
        float stored[4];
        std::memcpy(stored, &data[0x1820], sizeof(stored));
        if (std::memcmp(stored, expected, sizeof(stored)) != 0)
        {
            std::printf("store %u pairs after the ADD wrote %g %g %g %g\n", distance, stored[0], stored[1], stored[2], stored[3]);
            ++failures;
        }
    }
    return failures == 0 ? 0 : 1;
}
