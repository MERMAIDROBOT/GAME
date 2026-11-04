// A VU program longer than one cycle budget must be continued to its end, as on the hardware, and
// continuing it in slices must give the same result as one long run.
#include "runtime/ps2_vu1.h"
#include "runtime/gs/gs_frontend.h"
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

alignas(64) static unsigned char g_gs[sizeof(GS)];

static void put(std::vector<uint8_t> &code, uint32_t pair, uint32_t lower, uint32_t upper)
{
    std::memcpy(code.data() + 8 * pair, &lower, 4);
    std::memcpy(code.data() + 8 * pair + 4, &upper, 4);
}

int main()
{
    constexpr uint32_t NOP_LOWER = 0x8000033Cu, NOP_UPPER = 0x000002FFu, E_BIT = 1u << 30;
    constexpr uint32_t kIterations = 0x7FFFu;
    // The branch reads vi1 from before the decrement just ahead of it, so the loop runs once more.
    constexpr int32_t kExpectedDelaySlots = static_cast<int16_t>(kIterations + 1u);
    std::vector<uint8_t> code(16384, 0);
    put(code, 0, (0x08u << 25) | ((kIterations >> 11) << 21) | (1u << 16) | (kIterations & 0x7FFu), NOP_UPPER); // iaddiu vi1, vi0, N
    put(code, 1, NOP_LOWER, NOP_UPPER); // a branch right after two writes to vi1 would see the value before both
    put(code, 2, (0x09u << 25) | (1u << 16) | (1u << 11) | 1u, NOP_UPPER); // loop: isubiu vi1, vi1, 1
    put(code, 3, (0x29u << 25) | (1u << 11) | 0x7FEu, NOP_UPPER);         // ibne vi1, vi0, loop
    put(code, 4, (0x08u << 25) | (2u << 16) | (2u << 11) | 1u, NOP_UPPER); // delay: iaddiu vi2, vi2, 1
    put(code, 5, NOP_LOWER, NOP_UPPER | E_BIT);
    put(code, 6, NOP_LOWER, NOP_UPPER);
    GS &gs = *reinterpret_cast<GS *>(g_gs);

    std::vector<uint8_t> data(16384, 0);
    VU1Interpreter whole(VU1Interpreter::Unit::VU1);
    whole.reset();
    whole.execute(code.data(), 16384u, data.data(), 16384u, gs, nullptr, 0u, 0u, 0u, 1u << 30);
    if (whole.running() || whole.state().vi[2] != kExpectedDelaySlots || whole.state().cycles <= 65536u)
    {
        std::printf("reference run did not end as expected: running=%d vi2=%d cycles=%llu\n", whole.running(),
                    whole.state().vi[2], static_cast<unsigned long long>(whole.state().cycles));
        return 1;
    }

    VU1Interpreter sliced(VU1Interpreter::Unit::VU1);
    sliced.reset();
    sliced.execute(code.data(), 16384u, data.data(), 16384u, gs, nullptr, 0u, 0u, 0u, 65536u);
    if (!sliced.running())
    {
        std::printf("a program cut off by its budget must report that it is still running\n");
        return 1;
    }
    int slices = 1;
    while (sliced.running() && slices < 64)
    {
        sliced.resume(code.data(), 16384u, data.data(), 16384u, gs, nullptr, sliced.state().top, sliced.state().itop, 65536u);
        ++slices;
    }
    const VU1State &a = whole.state(), &b = sliced.state();
    const bool same = !sliced.running() && std::memcmp(a.vi, b.vi, sizeof(a.vi)) == 0 && a.cycles == b.cycles && a.pc == b.pc;
    std::printf("whole: vi2=%d cycles=%llu; sliced: %d slices, vi2=%d cycles=%llu\n", a.vi[2],
                static_cast<unsigned long long>(a.cycles), slices, b.vi[2], static_cast<unsigned long long>(b.cycles));
    return same ? 0 : 1;
}
