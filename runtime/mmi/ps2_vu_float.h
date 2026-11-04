#pragma once
// Per-lane PS2 float arithmetic shared by the VU0 macro-mode helpers (ps2_vu0_ops.h), the EE FPU
// helpers and the VU interpreter: the engine's scalar rules (ps2_port_fpu: no infinities or NaNs,
// exponent 255 is a number, overflow clamps to +-max, denormals flush to zero; host rounding) plus the
// overflow/underflow each result reports. A multiply-add rounds the product, then the sum, and
// reports the flags of the final add. behaviour_check.py and vu_micro_check.py test it.
#include <bit>
#include <cmath>
#include <cstdint>
#if __has_include("ps2_port_scalar_fpu.h")
#include "ps2_port_scalar_fpu.h"  // ps2_port_fpu (the runtime's copy of tools/ps2_scalar_fpu.h)
#endif

namespace ps2_vu_float {

inline float f(uint32_t bits) { return std::bit_cast<float>(bits); }
inline uint32_t u(float value) { return std::bit_cast<uint32_t>(value); }

// A lane result with the overflow/underflow the hardware reports for it.
struct Lane {
    uint32_t bits;
    bool overflow, underflow;
};

inline double value(uint32_t bits) { return ps2_port_fpu::ps2_to_double(f(bits)); }

// Only a result with exponent 0 or 255 can be an underflow (flushed to zero) or an overflow (clamped
// to +-max); the exact result decides which. A host result of exactly +-2^-126 may be a value just
// below it rounded up to nearest: the EE truncates, so that underflows to zero.
inline Lane flagged(uint32_t bits, double exact) {
    const double magnitude = exact < 0 ? -exact : exact;
    if ((bits & 0x7FFFFFFFu) == 0x00800000u && magnitude < 0x1p-126)
        return {bits & 0x80000000u, false, magnitude != 0.0};
    // Under round-toward-zero (the VU interpreter's host mode) a host overflow saturates at FLT_MAX
    // instead of reaching infinity; the PS2 result continues into exponent 255 up to 2^129.
    if ((bits & 0x7FFFFFFFu) == 0x7F7FFFFFu && magnitude >= 0x1p128)
        bits = u(ps2_port_fpu::ps2_from_double(exact));
    const uint32_t exponent = bits & 0x7F800000u;
    if (exponent != 0 && exponent != 0x7F800000u)
        return {bits, false, false};
    return {bits, magnitude >= 0x1p129, magnitude != 0.0 && magnitude < 0x1p-126};
}

// The value alone, with the same min-normal correction (division and reciprocal roots).
inline uint32_t settle(uint32_t bits, double exact) { return flagged(bits, exact).bits; }

inline double rootValue(uint32_t bits) {
    const double v = value(bits);
    return std::sqrt(v < 0 ? -v : v);
}

inline bool isZero(uint32_t bits) { return (bits & 0x7F800000u) == 0; }

// PS2 floats order as sign-magnitude integers: exponent 255 is a large number, not NaN.
inline int order(uint32_t a, uint32_t b) {
    int64_t ka = a & 0x7FFFFFFFu, kb = b & 0x7FFFFFFFu;
    if (a >> 31) ka = -ka;
    if (b >> 31) kb = -kb;
    return (ka > kb) - (ka < kb);
}

inline float fmax(float a, float b) { return order(u(a), u(b)) >= 0 ? a : b; }
inline float fmin(float a, float b) { return order(u(a), u(b)) <= 0 ? a : b; }

enum class Op { Add, Sub, Mul, Madd, Msub, Max, Mini };

inline Lane lane(Op op, uint32_t a, uint32_t b, uint32_t accumulator) {
    using namespace ps2_port_fpu;
    switch (op) {
    case Op::Add: return flagged(u(ps2_fadd(f(a), f(b))), value(a) + value(b));
    case Op::Sub: return flagged(u(ps2_fsub(f(a), f(b))), value(a) - value(b));
    case Op::Mul: return flagged(u(ps2_fmul(f(a), f(b))), value(a) * value(b));
    case Op::Madd: {  // the product is rounded first; the flags are those of the final add
        const uint32_t p = lane(Op::Mul, a, b, 0).bits;
        return flagged(u(ps2_fadd(f(accumulator), f(p))), value(accumulator) + value(p));
    }
    case Op::Msub: {
        const uint32_t p = lane(Op::Mul, a, b, 0).bits;
        return flagged(u(ps2_fsub(f(accumulator), f(p))), value(accumulator) - value(p));
    }
    case Op::Max: return {order(a, b) >= 0 ? a : b, false, false};
    case Op::Mini: return {order(a, b) <= 0 ? a : b, false, false};
    }
    return {0, false, false};
}

// Truncates toward zero; out-of-range values saturate by sign; exponent 0 is zero.
inline uint32_t toFixed(uint32_t bits, int shift) {
    const int exponent = static_cast<int>((bits >> 23) & 0xFFu);
    if (exponent == 0)
        return 0;
    const int64_t mantissa = (1 << 23) | (bits & 0x7FFFFFu);
    const int scale = exponent - 150 + shift;
    int64_t magnitude;
    if (scale >= 9)
        magnitude = int64_t(1) << 40;  // beyond 2^31 either way
    else if (scale >= 0)
        magnitude = mantissa << scale;
    else
        magnitude = scale <= -24 ? 0 : mantissa >> -scale;
    const int64_t value = (bits >> 31) ? -magnitude : magnitude;
    if (value > INT32_MAX) return 0x7FFFFFFFu;
    if (value < INT32_MIN) return 0x80000000u;
    return static_cast<uint32_t>(static_cast<int32_t>(value));
}

}  // namespace ps2_vu_float
