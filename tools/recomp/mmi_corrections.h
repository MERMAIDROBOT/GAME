#pragma once
// MMI forms whose inline SSE translation disagreed with the R5900 definition: 128-bit HI/LO
// truncated to 64 bits, a multiply-add summing all lanes into one accumulator, unsaturated PADDUH
// and PSUBUH, PABSW/PABSH reading rs, shuffles in the wrong lane order and halfword shifts that
// ignore the 4-bit amount mask. behaviour_check.py found them against an independent model of
// the EE. Each listed form is emitted as a call to ps2_mmi::<helper>(ctx, rd, rs, rt, sa), defined
// in the runtime's ps2_mmi_ops.h; every other form keeps its inline translation.
#include <cstdint>

namespace ps2recomp::mmi_corrections {

struct Entry {
    uint8_t function;  // MMI function field (bits 5..0)
    int8_t sub;        // sa field for the grouped forms and PMFHL/PMTHL; -1 matches any (shift amount)
    const char *helper;
};

inline constexpr Entry kEntries[] = {
    {0x34, -1, "psllh"}, {0x36, -1, "psrlh"}, {0x37, -1, "psrah"},
    {0x30, 0, "pmfhl_lw"}, {0x30, 1, "pmfhl_uw"}, {0x30, 2, "pmfhl_slw"}, {0x30, 3, "pmfhl_lh"}, {0x30, 4, "pmfhl_sh"},
    {0x31, 0, "pmthl_lw"},
    // MMI1
    {0x28, 0x01, "pabsw"}, {0x28, 0x05, "pabsh"}, {0x28, 0x14, "padduh"}, {0x28, 0x15, "psubuh"},
    // MMI2
    {0x09, 0x00, "pmaddw"}, {0x09, 0x02, "psllvw"}, {0x09, 0x03, "psrlvw"}, {0x09, 0x04, "pmsubw"},
    {0x09, 0x08, "pmfhi"}, {0x09, 0x09, "pmflo"}, {0x09, 0x0A, "pinth"}, {0x09, 0x0C, "pmultw"},
    {0x09, 0x0D, "pdivw"}, {0x09, 0x10, "pmaddh"}, {0x09, 0x11, "phmadh"}, {0x09, 0x14, "pmsubh"},
    {0x09, 0x15, "phmsbh"}, {0x09, 0x1A, "pexeh"}, {0x09, 0x1B, "prevh"}, {0x09, 0x1C, "pmulth"},
    {0x09, 0x1D, "pdivbw"}, {0x09, 0x1E, "pexew"}, {0x09, 0x1F, "prot3w"},
    // MMI3
    {0x29, 0x00, "pmadduw"}, {0x29, 0x03, "psravw"}, {0x29, 0x08, "pmthi"}, {0x29, 0x09, "pmtlo"},
    {0x29, 0x0A, "pinteh"}, {0x29, 0x0C, "pmultuw"}, {0x29, 0x0D, "pdivuw"}, {0x29, 0x1A, "pexch"},
    {0x29, 0x1E, "pexcw"},
};

// The helper for an MMI instruction (opcode 0x1C) with these function and sa fields, or nullptr.
inline const char *helperFor(uint32_t function, uint32_t sa) {
    for (const auto &entry : kEntries) {
        if (entry.function == function && (entry.sub < 0 || (uint32_t)entry.sub == sa))
            return entry.helper;
    }
    return nullptr;
}

}  // namespace ps2recomp::mmi_corrections
