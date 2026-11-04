#pragma once
// VU0 macro-mode (COP2) forms whose inline SSE translation disagreed with the hardware: host
// infinities, NaNs and denormals where the PS2 clamps and flushes, writes to the read-only VF0/VI0,
// no MAC flags for the compares that read them back, VFTOI saturating positive values to INT_MIN,
// VCLIP with swapped +/- bits and a signed bound, VDIV/VSQRT/VRSQRT special cases and VRSQRT dropping
// its numerator. behaviour_check.py found them against an independent model. Each listed form is
// emitted as ps2_vu0::<helper>(ctx, <instruction word>), defined in the runtime's ps2_vu0_ops.h.
#include <cstdint>

namespace ps2recomp::vu0_corrections {

struct Entry {
    uint8_t code;  // special1: function field; special2: (bits 10..6 << 2) | bits 1..0
    const char *helper;
};

inline constexpr Entry kSpecial1[] = {
    {0x00, "vaddbc"}, {0x01, "vaddbc"}, {0x02, "vaddbc"}, {0x03, "vaddbc"},
    {0x04, "vsubbc"}, {0x05, "vsubbc"}, {0x06, "vsubbc"}, {0x07, "vsubbc"},
    {0x08, "vmaddbc"}, {0x09, "vmaddbc"}, {0x0A, "vmaddbc"}, {0x0B, "vmaddbc"},
    {0x0C, "vmsubbc"}, {0x0D, "vmsubbc"}, {0x0E, "vmsubbc"}, {0x0F, "vmsubbc"},
    {0x10, "vmaxbc"}, {0x11, "vmaxbc"}, {0x12, "vmaxbc"}, {0x13, "vmaxbc"},
    {0x14, "vminibc"}, {0x15, "vminibc"}, {0x16, "vminibc"}, {0x17, "vminibc"},
    {0x18, "vmulbc"}, {0x19, "vmulbc"}, {0x1A, "vmulbc"}, {0x1B, "vmulbc"},
    {0x1C, "vmulq"}, {0x1D, "vmaxi"}, {0x1E, "vmuli"}, {0x1F, "vminii"},
    {0x20, "vaddq"}, {0x21, "vmaddq"}, {0x22, "vaddi"}, {0x23, "vmaddi"},
    {0x24, "vsubq"}, {0x25, "vmsubq"}, {0x26, "vsubi"}, {0x27, "vmsubi"},
    {0x28, "vadd"}, {0x29, "vmadd"}, {0x2A, "vmul"}, {0x2B, "vmax"},
    {0x2C, "vsub"}, {0x2D, "vmsub"}, {0x2E, "vopmsub"}, {0x2F, "vmini"},
    {0x30, "viadd"}, {0x31, "visub"}, {0x32, "viaddi"}, {0x34, "viand"}, {0x35, "vior"},
};

inline constexpr Entry kSpecial2[] = {
    {0x00, "vaddabc"}, {0x01, "vaddabc"}, {0x02, "vaddabc"}, {0x03, "vaddabc"},
    {0x04, "vsubabc"}, {0x05, "vsubabc"}, {0x06, "vsubabc"}, {0x07, "vsubabc"},
    {0x08, "vmaddabc"}, {0x09, "vmaddabc"}, {0x0A, "vmaddabc"}, {0x0B, "vmaddabc"},
    {0x0C, "vmsubabc"}, {0x0D, "vmsubabc"}, {0x0E, "vmsubabc"}, {0x0F, "vmsubabc"},
    {0x10, "vitof"}, {0x11, "vitof"}, {0x12, "vitof"}, {0x13, "vitof"},
    {0x14, "vftoi"}, {0x15, "vftoi"}, {0x16, "vftoi"}, {0x17, "vftoi"},
    {0x18, "vmulabc"}, {0x19, "vmulabc"}, {0x1A, "vmulabc"}, {0x1B, "vmulabc"},
    {0x1C, "vmulaq"}, {0x1D, "vabs"}, {0x1E, "vmulai"}, {0x1F, "vclipw"},
    {0x20, "vaddaq"}, {0x21, "vmaddaq"}, {0x22, "vaddai"}, {0x23, "vmaddai"},
    {0x24, "vsubaq"}, {0x25, "vmsubaq"}, {0x26, "vsubai"}, {0x27, "vmsubai"},
    {0x28, "vadda"}, {0x29, "vmadda"}, {0x2A, "vmula"}, {0x2C, "vsuba"}, {0x2D, "vmsuba"},
    {0x2E, "vopmula"}, {0x30, "vmove"}, {0x31, "vmr32"},
    {0x38, "vdiv"}, {0x39, "vsqrt"}, {0x3A, "vrsqrt"}, {0x3C, "vmtir"}, {0x3D, "vmfir"},
};

// The helper for a COP2 macro instruction (opcode 0x12 with the CO bit), or nullptr.
inline const char *helperFor(uint32_t word) {
    if ((word >> 26) != 0x12u || !(word & (1u << 25)))
        return nullptr;
    const uint32_t function = word & 0x3Fu;
    if (function >= 0x3C) {
        const uint32_t code = (((word >> 6) & 0x1Fu) << 2) | (word & 3u);
        for (const auto &entry : kSpecial2)
            if (entry.code == code)
                return entry.helper;
        return nullptr;
    }
    for (const auto &entry : kSpecial1)
        if (entry.code == function)
            return entry.helper;
    return nullptr;
}

}  // namespace ps2recomp::vu0_corrections
