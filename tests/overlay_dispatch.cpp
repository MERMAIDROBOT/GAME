// Overlay dispatch policy on a synthetic guest RAM; no game, runtime or retail code.
#include "overlay_dispatch.hpp"
#include <cstdio>
#include <cstdlib>
#include <memory>

static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); ++failures; } } while (0)

static void levelA(uint8_t *, R5900Context *, PS2Runtime *) {}
static void levelB(uint8_t *, R5900Context *, PS2Runtime *) {}

static void put(uint8_t *ram, uint32_t address, uint32_t value) { std::memcpy(ram + address, &value, 4); }

int main() {
    auto ram = std::make_unique<uint8_t[]>(ps2port::OverlayRegistry::kRamBytes);
    std::memset(ram.get(), 0, ps2port::OverlayRegistry::kRamBytes);
    // Two programs share 0x200000..0x200100; each has its own table and signature words.
    static ps2port::OverlayFunction tableA[64] = {}, tableB[64] = {};
    tableA[0] = levelA;  // 0x200000
    tableB[0] = levelB;
    tableB[4] = levelB;  // 0x200010: only B has a function here
    static const ps2port::OverlaySignatureWord sigA[] = {{0x200020u, 0xAAAA0001u}, {0x2000F0u, 0xAAAA0002u}};
    static const ps2port::OverlaySignatureWord sigB[] = {{0x200020u, 0xBBBB0001u}, {0x2000F0u, 0xBBBB0002u}};
    ps2port::OverlayRegistry registry;
    registry.add({"a", 0x200000u, 0x200100u, tableA, 64u, sigA, 2u});
    registry.add({"b", 0x200000u, 0x200100u, tableB, 64u, sigB, 2u});

    // Nothing loaded: boot program keeps the address.
    CHECK(!registry.resolve(ram.get(), 0x200000u).covered);

    // A loaded: A's function, and an A address without a function is still covered (no fallback).
    put(ram.get(), 0x200020u, 0xAAAA0001u); put(ram.get(), 0x2000F0u, 0xAAAA0002u);
    auto hit = registry.resolve(ram.get(), 0x200000u);
    CHECK(hit.covered && hit.function == levelA);
    hit = registry.resolve(ram.get(), 0x200010u);
    CHECK(hit.covered && hit.function == nullptr);

    // Partial replacement stays rejected even when bounded diagnostics are enabled.
    setenv("PS2_PORT_OVERLAY_MISS_TRACE", "1", 1);
    // Partially overwritten signature: neither program is resident.
    put(ram.get(), 0x2000F0u, 0xBBBB0002u);
    CHECK(!registry.resolve(ram.get(), 0x200000u).covered);

    unsetenv("PS2_PORT_OVERLAY_MISS_TRACE");

    // B loaded: the cached A entry must not win.
    put(ram.get(), 0x200020u, 0xBBBB0001u);
    hit = registry.resolve(ram.get(), 0x200010u);
    CHECK(hit.covered && hit.function == levelB);
    CHECK(registry.resolve(ram.get(), 0x200000u).function == levelB);

    // Outside every overlay, unaligned, empty signature and empty registry.
    CHECK(!registry.resolve(ram.get(), 0x100000u).covered);
    CHECK(!registry.resolve(ram.get(), 0x200002u).covered);
    ps2port::OverlayRegistry unsigned_;
    unsigned_.add({"c", 0x300000u, 0x300010u, tableA, 4u, nullptr, 0u});
    CHECK(!unsigned_.resolve(ram.get(), 0x300000u).covered);
    ps2port::OverlayRegistry none;
    CHECK(!none.resolve(ram.get(), 0x200000u).covered);
    // Signature addresses are guest physical: KSEG mirrors resolve to the same RAM.
    static const ps2port::OverlaySignatureWord sigK[] = {{0x80200020u, 0xBBBB0001u}};
    ps2port::OverlayRegistry mirrored;
    mirrored.add({"k", 0x200000u, 0x200100u, tableB, 64u, sigK, 1u});
    CHECK(mirrored.resolve(ram.get(), 0x200010u).function == levelB);

    if (failures == 0) std::printf("overlay dispatch: all checks passed\n");
    return failures == 0 ? 0 : 1;
}
