// The same GS packet must draw the same pixels whatever rounding mode the caller left active
// (a VU program sends PATH1 packets under round-toward-zero), and the caller's mode must survive.
#include "runtime/gs/gs_frontend.h"
#include "runtime/gs/gs_types.h"
#include "runtime/ps2_memory.h"
#include <cfenv>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <vector>

static void put(std::vector<uint8_t> &p, uint64_t v) { const size_t n = p.size(); p.resize(n + 8); std::memcpy(p.data() + n, &v, 8); }
static void ad(std::vector<uint8_t> &p, uint64_t v, uint64_t r) { put(p, v); put(p, r); }
static uint32_t bits(float f) { uint32_t b; std::memcpy(&b, &f, 4); return b; }

static uint64_t draw(int mode, int &after)
{
    std::vector<uint8_t> vram(PS2_GS_VRAM_SIZE, 0u);
    auto owner = std::make_unique<GS>();
    GS &gs = *owner;
    gs.init(vram.data(), static_cast<uint32_t>(vram.size()), nullptr);
    uint32_t seed = 12345u;
    auto rnd = [&]() { seed = seed * 1664525u + 1013904223u; return seed >> 8; };
    // A 64x64 32-bit texture at block 256 filled with noise.
    for (uint32_t i = 0; i < 64u * 64u * 4u; ++i) vram[256u * 256u + i] = static_cast<uint8_t>(rnd());
    std::vector<uint8_t> p;
    const int tris = 40;
    put(p, (1ull << 15) | (1ull << 60)); // PACKED, NREG=1 (A+D), EOP
    put(p, 0xEull);
    ad(p, (static_cast<uint64_t>(GS_PSM_CT32) << 24) | (4ull << 16), GS_REG_FRAME_1);
    ad(p, (128ull << 0), GS_REG_ZBUF_1);
    ad(p, (255ull << 16) | (255ull << 48), GS_REG_SCISSOR_1);
    ad(p, 0ull, GS_REG_XYOFFSET_1);
    ad(p, (1ull << 16) | (2ull << 17), GS_REG_TEST_1); // ZTE, ZTST=GEQUAL
    ad(p, 256ull | (1ull << 14) | (static_cast<uint64_t>(GS_PSM_CT32) << 20) | (6ull << 26) | (6ull << 30) | (1ull << 34),
       GS_REG_TEX0_1);
    ad(p, (1ull << 5) | (1ull << 6), GS_REG_TEX1_1); // bilinear magnify and minify
    ad(p, 0ull, GS_REG_CLAMP_1);
    ad(p, 1ull, GS_REG_PRMODECONT);
    for (int t = 0; t < tris; ++t)
    {
        // PRIM: triangle, Gouraud, textured, alpha blend.
        ad(p, 3ull | (1ull << 3) | (1ull << 4) | (1ull << 6), GS_REG_PRIM);
        for (int v = 0; v < 3; ++v)
        {
            const float q = 0.25f + (rnd() % 1000u) / 300.0f;
            const float s = (rnd() % 1000u) / 997.0f * q, tt = (rnd() % 1000u) / 991.0f * q;
            ad(p, bits(s) | (static_cast<uint64_t>(bits(tt)) << 32), GS_REG_ST);
            ad(p, (rnd() & 0xFFFFFFu) | 0x80000000ull | (static_cast<uint64_t>(bits(q)) << 32), GS_REG_RGBAQ);
            const uint64_t x = rnd() % (256u * 16u), y = rnd() % (256u * 16u), z = rnd() & 0xFFFFFFu;
            ad(p, x | (y << 16) | (z << 32), GS_REG_XYZ2);
        }
    }
    // Fix up NLOOP to the number of A+D entries written.
    const uint64_t count = (p.size() - 16u) / 16u;
    uint64_t tag; std::memcpy(&tag, p.data(), 8); tag = (tag & ~0x7FFFull) | count; std::memcpy(p.data(), &tag, 8);
    std::fesetround(mode);
    gs.processGIFPacket(p.data(), static_cast<uint32_t>(p.size()));
    after = std::fegetround();
    owner.reset(); // a threaded renderer finishes its queue before the pixels are read
    std::fesetround(FE_TONEAREST);
    uint64_t h = 1469598103934665603ull;
    for (uint8_t b : vram) h = (h ^ b) * 1099511628211ull;
    return h;
}

int main()
{
    int afterNearest = 0, afterZero = 0;
    const uint64_t nearest = draw(FE_TONEAREST, afterNearest);
    const uint64_t zero = draw(FE_TOWARDZERO, afterZero);
    std::printf("nearest=%016llx towardzero=%016llx restored=%d\n", (unsigned long long)nearest, (unsigned long long)zero,
                afterNearest == FE_TONEAREST && afterZero == FE_TOWARDZERO);
    return (nearest == zero && afterNearest == FE_TONEAREST && afterZero == FE_TOWARDZERO) ? 0 : 1;
}
