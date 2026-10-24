// SPDX-License-Identifier: GPL-3.0-only
// Adapted from ran-j/PS2Recomp eb8688b2fd191129f103a39543002c005b33409c
// ps2xRuntime/include/ps2_runtime_macros.h; original notices/license retained in tools/PS2Recomp-LICENSE.
// Port Lab change: ADD/SUB/MUL guard both operands as well as the result,
// so exponent-zero operands cannot enter a host-normal-result fast path.
// Scalar result compatibility only: not a full FCR31/ACC/VU timing model.
// Host rounding differences are reported by the instruction conformance command.
#ifndef PS2_PORT_SCALAR_FPU_H
#define PS2_PORT_SCALAR_FPU_H
#if defined(__FAST_MATH__)
#error "PS2 scalar result helpers require fast-math disabled"
#endif
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstdlib>
namespace ps2_port_fpu {
#if defined(_MSC_VER)
#define PS2_FLOAT_NOINLINE __declspec(noinline)
#else
#define PS2_FLOAT_NOINLINE __attribute__((noinline))
#endif

constexpr uint32_t PS2_FLOAT_MAX_BITS = 0x7FFFFFFFu;
constexpr uint32_t PS2_FLOAT_EXP_MASK = 0x7F800000u;
constexpr uint32_t PS2_FLOAT_SIGN_MASK = 0x80000000u;

// True when a host float operation cannot be trusted with x: exponent 255, or exponent 0 with a mantissa.
inline bool ps2_fspecial(float x)
{
    const uint32_t t = std::bit_cast<uint32_t>(x) & 0x7FFFFFFFu;
    return t != 0u && (t - 0x00800000u) >= 0x7F000000u;
}

inline double ps2_to_double(float x)
{
    uint32_t b = std::bit_cast<uint32_t>(x);
    const uint32_t exponent = b & PS2_FLOAT_EXP_MASK;
    if (exponent == 0u)
        return static_cast<double>(std::bit_cast<float>(b & PS2_FLOAT_SIGN_MASK));
    if (exponent == PS2_FLOAT_EXP_MASK)
        return static_cast<double>(std::bit_cast<float>(b - 0x00800000u)) * 2.0;
    return static_cast<double>(x);
}

inline float ps2_from_double(double d)
{
    const double magnitude = std::fabs(d);
    const uint32_t sign = std::signbit(d) ? PS2_FLOAT_SIGN_MASK : 0u;
    if (magnitude >= 0x1p129)
        return std::bit_cast<float>(sign | PS2_FLOAT_MAX_BITS);
    if (magnitude < 0x1p-126)
        return std::bit_cast<float>(sign);
    if (magnitude >= 0x1p128)
    {
        // Convert one exponent lower, then put the exponent back as 255.
        const uint32_t b = std::bit_cast<uint32_t>(static_cast<float>(d * 0.5));
        if ((b & PS2_FLOAT_EXP_MASK) == PS2_FLOAT_EXP_MASK)
            return std::bit_cast<float>(sign | PS2_FLOAT_MAX_BITS);
        return std::bit_cast<float>(b + 0x00800000u);
    }
    // A value that rounds up to 2^128 comes out as the host's Inf pattern, which is 2^128 on the PS2.
    return static_cast<float>(d);
}

PS2_FLOAT_NOINLINE inline float ps2_fadd_slow(float a, float b)
{
    // Zero operands and results end up here too; the host result is right for them.
    const float r = a + b;
    if (!(ps2_fspecial(a) | ps2_fspecial(b) | ps2_fspecial(r)))
        return r;
    return ps2_from_double(ps2_to_double(a) + ps2_to_double(b));
}

PS2_FLOAT_NOINLINE inline float ps2_fsub_slow(float a, float b)
{
    // Zero operands and results end up here too; the host result is right for them.
    const float r = a - b;
    if (!(ps2_fspecial(a) | ps2_fspecial(b) | ps2_fspecial(r)))
        return r;
    return ps2_from_double(ps2_to_double(a) - ps2_to_double(b));
}

PS2_FLOAT_NOINLINE inline float ps2_fmul_slow(float a, float b)
{
    // Zero operands and results end up here too; the host result is right for them.
    const float r = a * b;
    if (!(ps2_fspecial(a) | ps2_fspecial(b) | ps2_fspecial(r)))
        return r;
    return ps2_from_double(ps2_to_double(a) * ps2_to_double(b));
}

// A zero divisor gives +-max with the sign of fs xor ft.
PS2_FLOAT_NOINLINE inline float ps2_fdiv_slow(float a, float b)
{
    const uint32_t ua = std::bit_cast<uint32_t>(a);
    const uint32_t ub = std::bit_cast<uint32_t>(b);
    if ((ub & PS2_FLOAT_EXP_MASK) == 0u)
        return std::bit_cast<float>(((ua ^ ub) & PS2_FLOAT_SIGN_MASK) | PS2_FLOAT_MAX_BITS);
    const float r = a / b;
    if (!(ps2_fspecial(a) | ps2_fspecial(b) | ps2_fspecial(r)))
        return r;
    return ps2_from_double(ps2_to_double(a) / ps2_to_double(b));
}

// sqrt(|ft|); the result is never negative, sqrt(-0) is +0.
PS2_FLOAT_NOINLINE inline float ps2_fsqrt_slow(float a)
{
    if ((std::bit_cast<uint32_t>(a) & PS2_FLOAT_EXP_MASK) == 0u)
        return 0.0f;
    return ps2_from_double(std::sqrt(std::fabs(ps2_to_double(a))));
}

// fs / sqrt(|ft|). The sign of ft is ignored everywhere, so a zero ft gives +-max with the sign of fs alone.
PS2_FLOAT_NOINLINE inline float ps2_frsqrt_slow(float a, float b)
{
    if ((std::bit_cast<uint32_t>(b) & PS2_FLOAT_EXP_MASK) == 0u)
        return std::bit_cast<float>((std::bit_cast<uint32_t>(a) & PS2_FLOAT_SIGN_MASK) | PS2_FLOAT_MAX_BITS);
    // The root is rounded to a float before the division, as on the hardware.
    const float root = ps2_from_double(std::sqrt(std::fabs(ps2_to_double(b))));
    return ps2_from_double(ps2_to_double(a) / static_cast<double>(root));
}

// True when a, b and the host result r are all normal numbers, so r is also the PS2 result.
inline bool ps2_fall_normal(float a, float b, float r)
{
    // Doubling drops the sign; adding one exponent step wraps exponent 255 round to 0 next to exponent 0.
    const uint32_t sa = std::bit_cast<uint32_t>(a) * 2u + 0x01000000u;
    const uint32_t sb = std::bit_cast<uint32_t>(b) * 2u + 0x01000000u;
    const uint32_t sr = std::bit_cast<uint32_t>(r) * 2u + 0x01000000u;
    uint32_t lowest = sa < sb ? sa : sb;
    lowest = lowest < sr ? lowest : sr;
    return lowest >= 0x02000000u;
}

// The same test on the result alone. An exponent-255 operand always leaves exponent 255 in the host result
// of an addition, subtraction or multiplication (Inf or NaN), so this finds those operands as well.
inline bool ps2_fnormal(float r)
{
    return (std::bit_cast<uint32_t>(r) * 2u + 0x01000000u) >= 0x02000000u;
}

inline float ps2_fadd(float a, float b)
{
    const float r = a + b;
    return ps2_fall_normal(a, b, r) ? r : ps2_fadd_slow(a, b);
}

inline float ps2_fsub(float a, float b)
{
    const float r = a - b;
    return ps2_fall_normal(a, b, r) ? r : ps2_fsub_slow(a, b);
}

inline float ps2_fmul(float a, float b)
{
    const float r = a * b;
    return ps2_fall_normal(a, b, r) ? r : ps2_fmul_slow(a, b);
}

inline float ps2_fdiv(float a, float b)
{
    const float r = a / b;
    return ps2_fall_normal(a, b, r) ? r : ps2_fdiv_slow(a, b);
}

inline float ps2_fsqrt(float a)
{
    const float r = std::sqrt(a);
    return ps2_fall_normal(a, a, r) ? r : ps2_fsqrt_slow(a);
}

inline float ps2_frsqrt(float a, float b)
{
    const float r = a / std::sqrt(b);
    return ps2_fall_normal(a, b, r) ? r : ps2_frsqrt_slow(a, b);
}

// EE exponent-255 encodings are finite extended values, not IEEE NaNs.
// Comparisons must use the same representation as arithmetic or clamps fail.
inline bool ps2_compare_extended() { static const bool on=[] { const char *v=std::getenv("PS2_PORT_FPU_COMPARE"); return !(v && v[0]=='0'); }(); return on; }
inline bool ps2_flt(float a, float b) { return ps2_compare_extended() ? ps2_to_double(a) < ps2_to_double(b) : a < b; }
inline bool ps2_fle(float a, float b) { return ps2_compare_extended() ? ps2_to_double(a) <= ps2_to_double(b) : a <= b; }
inline bool ps2_feq(float a, float b) { return ps2_compare_extended() ? ps2_to_double(a) == ps2_to_double(b) : a == b; }

} // namespace ps2_port_fpu
#undef PS2_FLOAT_NOINLINE
#endif
