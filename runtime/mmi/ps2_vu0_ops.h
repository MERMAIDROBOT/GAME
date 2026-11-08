#pragma once
// VU0 macro-mode (COP2) forms that the recompiler emits as calls (ps2recomp/vu0_corrections.h lists
// them), and the EE FPU MAX.S/MIN.S. Each VU0 helper takes the instruction word; its fields fold to
// constants once the call is inlined. Lanes are x, y, z, w; dest bit 3 selects x.
//  - Arithmetic follows the engine's PS2 scalar rules per lane (ps2_port_fpu: no infinities or NaNs,
//    overflow clamps to +-max, denormals flush to zero; host rounding). A multiply-add rounds the
//    product, then the sum.
//  - VF0 always reads (0, 0, 0, 1.0) and ignores writes; VI0 reads 0 and ignores writes.
//  - FMAC forms set the MAC zero, sign, underflow and overflow bits of the written lanes (also for a
//    VF0 destination, the usual flag-only compare) and the status summary with its sticky copies;
//    VDIV/VSQRT/VRSQRT set the status invalid/divide bits (PCSX2 VUops.cpp/VUflags.cpp rules).
//  - MAX/MINI order PS2 floats as sign-magnitude integers, as the hardware does.
// behaviour_check.py runs every listed form against an independent model.
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstring>

#include "ps2_vu_float.h"

namespace ps2_vu0 {

using namespace ps2_vu_float;

struct Vec {
    uint32_t v[4];
};

constexpr uint32_t kOne = 0x3F800000u;

inline uint32_t fsReg(uint32_t w) { return (w >> 11) & 31u; }
inline uint32_t ftReg(uint32_t w) { return (w >> 16) & 31u; }
inline uint32_t fdReg(uint32_t w) { return (w >> 6) & 31u; }
inline uint32_t destMask(uint32_t w) { return (w >> 21) & 15u; }
inline uint32_t fsf(uint32_t w) { return (w >> 21) & 3u; }
inline uint32_t ftf(uint32_t w) { return (w >> 23) & 3u; }

inline Vec vf(const R5900Context *c, uint32_t r) {
    Vec x{{0, 0, 0, kOne}};
    if (r != 0)
        std::memcpy(x.v, &c->vu0_vf[r], 16);
    return x;
}

inline void setVf(R5900Context *c, uint32_t r, const Vec &x, uint32_t dest) {
    if (r == 0)
        return;
    Vec cur;
    std::memcpy(cur.v, &c->vu0_vf[r], 16);
    for (int k = 0; k < 4; ++k)
        if (dest & (8u >> k))
            cur.v[k] = x.v[k];
    std::memcpy(&c->vu0_vf[r], cur.v, 16);
}

inline Vec acc(const R5900Context *c) {
    Vec x;
    std::memcpy(x.v, &c->vu0_acc, 16);
    return x;
}

inline void setAcc(R5900Context *c, const Vec &x, uint32_t dest) {
    Vec cur = acc(c);
    for (int k = 0; k < 4; ++k)
        if (dest & (8u >> k))
            cur.v[k] = x.v[k];
    std::memcpy(&c->vu0_acc, cur.v, 16);
}

inline uint32_t vi(const R5900Context *c, uint32_t r) { return (r & 15u) == 0 ? 0u : c->vi[r & 15u]; }

inline void setVi(R5900Context *c, uint32_t r, uint32_t value) {
    if ((r & 15u) != 0)
        c->vi[r & 15u] = static_cast<uint16_t>(value);
}

inline uint32_t bitsOf(const float &value) {
    uint32_t bits;
    std::memcpy(&bits, &value, 4);
    return bits;
}

inline void setQ(R5900Context *c, uint32_t bits) { std::memcpy(&c->vu0_q, &bits, 4); }

enum class Src { Vt, Bc, Q, I };

// Zero, sign, underflow and overflow bits of the written lanes (x is the high bit of each group);
// the status register summarises them in bits 0-3 with sticky copies in 6-9.
inline void setMac(R5900Context *c, const Lane (&r)[4], uint32_t dest) {
    uint32_t mac = 0, extraSticky = 0;
    for (int k = 0; k < 4; ++k) {
        if (!(dest & (8u >> k)))
            continue;
        if (r[k].productUnderflow) extraSticky |= 0x100u;
        const uint32_t bit = 3 - k;
        if ((r[k].bits & 0x7F800000u) == 0) mac |= 1u << bit;
        if (r[k].bits >> 31) mac |= 1u << (4 + bit);
        if (r[k].underflow) mac |= 1u << (8 + bit);
        if (r[k].overflow) mac |= 1u << (12 + bit);
    }
    c->vu0_mac_flags = mac;
    uint32_t summary = 0;
    for (uint32_t n = 0; n < 4; ++n)
        if (mac & (0xFu << (4 * n)))
            summary |= 1u << n;
    c->vu0_status = static_cast<uint16_t>((c->vu0_status & ~0xFu) | summary | (summary << 6) | extraSticky);
}

// VDIV/VSQRT/VRSQRT: invalid (bit 4) and divide (bit 5) replace the old ones; sticky copies in 10, 11.
inline void setDivideStatus(R5900Context *c, bool invalid, bool divide) {
    uint32_t status = c->vu0_status & ~0x30u;
    if (invalid) status |= 0x10u | 0x400u;
    if (divide) status |= 0x20u | 0x800u;
    c->vu0_status = static_cast<uint16_t>(status);
}

template <Op op, Src src, bool toAcc>
inline void arith(R5900Context *c, uint32_t w) {
    const Vec a = vf(c, fsReg(w)), accumulator = acc(c);
    Vec b;
    if constexpr (src == Src::Vt) {
        b = vf(c, ftReg(w));
    } else if constexpr (src == Src::Bc) {
        const uint32_t x = vf(c, ftReg(w)).v[w & 3u];
        b = Vec{{x, x, x, x}};
    } else {
        const uint32_t x = bitsOf(src == Src::Q ? c->vu0_q : c->vu0_i);
        b = Vec{{x, x, x, x}};
    }
    Lane lanes[4];
    Vec r;
    for (int k = 0; k < 4; ++k) {
        lanes[k] = lane(op, a.v[k], b.v[k], accumulator.v[k]);
        r.v[k] = lanes[k].bits;
    }
    if constexpr (toAcc)
        setAcc(c, r, destMask(w));
    else
        setVf(c, fdReg(w), r, destMask(w));
    if constexpr (op != Op::Max && op != Op::Mini)
        setMac(c, lanes, destMask(w));
}

#define PS2_VU0_ARITH(name, op, src, toAcc) \
    inline void name(R5900Context *c, uint32_t w) { arith<Op::op, Src::src, toAcc>(c, w); }
PS2_VU0_ARITH(vaddbc, Add, Bc, false)
PS2_VU0_ARITH(vsubbc, Sub, Bc, false)
PS2_VU0_ARITH(vmaddbc, Madd, Bc, false)
PS2_VU0_ARITH(vmsubbc, Msub, Bc, false)
PS2_VU0_ARITH(vmaxbc, Max, Bc, false)
PS2_VU0_ARITH(vminibc, Mini, Bc, false)
PS2_VU0_ARITH(vmulbc, Mul, Bc, false)
PS2_VU0_ARITH(vmulq, Mul, Q, false)
PS2_VU0_ARITH(vmaxi, Max, I, false)
PS2_VU0_ARITH(vmuli, Mul, I, false)
PS2_VU0_ARITH(vminii, Mini, I, false)
PS2_VU0_ARITH(vaddq, Add, Q, false)
PS2_VU0_ARITH(vmaddq, Madd, Q, false)
PS2_VU0_ARITH(vaddi, Add, I, false)
PS2_VU0_ARITH(vmaddi, Madd, I, false)
PS2_VU0_ARITH(vsubq, Sub, Q, false)
PS2_VU0_ARITH(vmsubq, Msub, Q, false)
PS2_VU0_ARITH(vsubi, Sub, I, false)
PS2_VU0_ARITH(vmsubi, Msub, I, false)
PS2_VU0_ARITH(vadd, Add, Vt, false)
PS2_VU0_ARITH(vmadd, Madd, Vt, false)
PS2_VU0_ARITH(vmul, Mul, Vt, false)
PS2_VU0_ARITH(vmax, Max, Vt, false)
PS2_VU0_ARITH(vsub, Sub, Vt, false)
PS2_VU0_ARITH(vmsub, Msub, Vt, false)
PS2_VU0_ARITH(vmini, Mini, Vt, false)
PS2_VU0_ARITH(vaddabc, Add, Bc, true)
PS2_VU0_ARITH(vsubabc, Sub, Bc, true)
PS2_VU0_ARITH(vmaddabc, Madd, Bc, true)
PS2_VU0_ARITH(vmsubabc, Msub, Bc, true)
PS2_VU0_ARITH(vmulabc, Mul, Bc, true)
PS2_VU0_ARITH(vmulaq, Mul, Q, true)
PS2_VU0_ARITH(vmulai, Mul, I, true)
PS2_VU0_ARITH(vaddaq, Add, Q, true)
PS2_VU0_ARITH(vmaddaq, Madd, Q, true)
PS2_VU0_ARITH(vaddai, Add, I, true)
PS2_VU0_ARITH(vmaddai, Madd, I, true)
PS2_VU0_ARITH(vsubaq, Sub, Q, true)
PS2_VU0_ARITH(vmsubaq, Msub, Q, true)
PS2_VU0_ARITH(vsubai, Sub, I, true)
PS2_VU0_ARITH(vmsubai, Msub, I, true)
PS2_VU0_ARITH(vadda, Add, Vt, true)
PS2_VU0_ARITH(vmadda, Madd, Vt, true)
PS2_VU0_ARITH(vmula, Mul, Vt, true)
PS2_VU0_ARITH(vsuba, Sub, Vt, true)
PS2_VU0_ARITH(vmsuba, Msub, Vt, true)
#undef PS2_VU0_ARITH

// OPMULA: ACC.xyz = fs.yzx * ft.zxy; OPMSUB: fd.xyz = ACC - fs.yzx * ft.zxy.
inline void outer(R5900Context *c, uint32_t w, bool toAcc) {
    const Vec s = vf(c, fsReg(w)), t = vf(c, ftReg(w)), accumulator = acc(c);
    const Vec a{{s.v[1], s.v[2], s.v[0], s.v[3]}}, b{{t.v[2], t.v[0], t.v[1], t.v[3]}};
    Lane lanes[4];
    Vec r;
    for (int k = 0; k < 4; ++k) {
        lanes[k] = lane(toAcc ? Op::Mul : Op::Msub, a.v[k], b.v[k], accumulator.v[k]);
        r.v[k] = lanes[k].bits;
    }
    const uint32_t dest = destMask(w) & 0xEu;
    if (toAcc)
        setAcc(c, r, dest);
    else
        setVf(c, fdReg(w), r, dest);
    setMac(c, lanes, dest);
}
inline void vopmula(R5900Context *c, uint32_t w) { outer(c, w, true); }
inline void vopmsub(R5900Context *c, uint32_t w) { outer(c, w, false); }

inline int fixedShift(uint32_t w) {
    static const int shifts[4] = {0, 4, 12, 15};
    return shifts[w & 3u];
}

inline void vitof(R5900Context *c, uint32_t w) {
    const Vec s = vf(c, fsReg(w));
    const float scale = 1.0f / static_cast<float>(1 << fixedShift(w));
    Vec r;
    for (int k = 0; k < 4; ++k)
        r.v[k] = u(static_cast<float>(static_cast<int32_t>(s.v[k])) * scale);
    setVf(c, ftReg(w), r, destMask(w));
}

inline void vftoi(R5900Context *c, uint32_t w) {
    const Vec s = vf(c, fsReg(w));
    Vec r;
    for (int k = 0; k < 4; ++k)
        r.v[k] = toFixed(s.v[k], fixedShift(w));
    setVf(c, ftReg(w), r, destMask(w));
}

inline void vabs(R5900Context *c, uint32_t w) {
    Vec r = vf(c, fsReg(w));
    for (auto &x : r.v) x &= 0x7FFFFFFFu;
    setVf(c, ftReg(w), r, destMask(w));
}

inline void vmove(R5900Context *c, uint32_t w) { setVf(c, ftReg(w), vf(c, fsReg(w)), destMask(w)); }

inline void vmr32(R5900Context *c, uint32_t w) {
    const Vec s = vf(c, fsReg(w));
    setVf(c, ftReg(w), Vec{{s.v[1], s.v[2], s.v[3], s.v[0]}}, destMask(w));
}

// Judges fs.x, fs.y, fs.z against |ft.w|: bit 0 beyond +, bit 1 beyond -, per axis; six bits per call.
inline void vclipw(R5900Context *c, uint32_t w) {
    const Vec s = vf(c, fsReg(w));
    uint32_t bound = vf(c, ftReg(w)).v[3];
    if ((bound & 0x7F800000u) == 0) bound &= 0xFF800000u;
    bound &= 0x7FFFFFFFu;
    uint32_t judgement = 0;
    for (int k = 0; k < 3; ++k) {
        uint32_t x = s.v[k];
        if ((x & 0x7F800000u) == 0) x &= 0xFF800000u;
        if ((x & 0x7FFFFFFFu) > bound)
            judgement |= (x >> 31 ? 2u : 1u) << (2 * k);
    }
    c->vu0_clip_flags = ((c->vu0_clip_flags << 6) | judgement) & 0xFFFFFFu;
}

inline void vdiv(R5900Context *c, uint32_t w) {
    const uint32_t a = vf(c, fsReg(w)).v[fsf(w)], b = vf(c, ftReg(w)).v[ftf(w)];
    setQ(c, settle(u(ps2_port_fpu::ps2_fdiv(f(a), f(b))), value(a) / value(b)));
    setDivideStatus(c, isZero(b) && isZero(a), isZero(b) && !isZero(a));
}
inline void vsqrt(R5900Context *c, uint32_t w) {
    const uint32_t b = vf(c, ftReg(w)).v[ftf(w)];
    setQ(c, u(ps2_port_fpu::ps2_fsqrt(f(b))));
    setDivideStatus(c, !isZero(b) && (b >> 31), false);
}
inline void vrsqrt(R5900Context *c, uint32_t w) {
    const uint32_t a = vf(c, fsReg(w)).v[fsf(w)], b = vf(c, ftReg(w)).v[ftf(w)];
    setQ(c, settle(u(ps2_port_fpu::ps2_frsqrt(f(a), f(b))), value(a) / rootValue(b)));
    setDivideStatus(c, (isZero(b) && isZero(a)) || (!isZero(b) && (b >> 31)), isZero(b));
}

inline void vmtir(R5900Context *c, uint32_t w) { setVi(c, ftReg(w), vf(c, fsReg(w)).v[fsf(w)] & 0xFFFFu); }

inline void vmfir(R5900Context *c, uint32_t w) {
    const uint32_t x = static_cast<uint32_t>(static_cast<int32_t>(static_cast<int16_t>(vi(c, fsReg(w)))));
    setVf(c, ftReg(w), Vec{{x, x, x, x}}, destMask(w));
}

inline void viadd(R5900Context *c, uint32_t w) { setVi(c, fdReg(w), vi(c, fsReg(w)) + vi(c, ftReg(w))); }
inline void visub(R5900Context *c, uint32_t w) { setVi(c, fdReg(w), vi(c, fsReg(w)) - vi(c, ftReg(w))); }
inline void viand(R5900Context *c, uint32_t w) { setVi(c, fdReg(w), vi(c, fsReg(w)) & vi(c, ftReg(w))); }
inline void vior(R5900Context *c, uint32_t w) { setVi(c, fdReg(w), vi(c, fsReg(w)) | vi(c, ftReg(w))); }
inline void viaddi(R5900Context *c, uint32_t w) {
    const int32_t imm = static_cast<int32_t>(fdReg(w) << 27) >> 27;
    setVi(c, ftReg(w), vi(c, fsReg(w)) + static_cast<uint32_t>(imm));
}

}  // namespace ps2_vu0

// EE FPU arithmetic with its FCR31 cause and sticky flags (PCSX2 FPU.cpp rules): add/sub/mul and the
// multiply-add forms set O+SO on overflow (leaving U), else clear O and set U+SU on underflow or clear
// U; DIV sets D+SD for x/0 and I+SI for 0/0; SQRT and RSQRT clear D and I first, then flag a negative
// root (I) or a zero RSQRT divisor (D); ABS, NEG, MAX and MIN clear O and U. Values follow the same
// per-lane rules as VU0.
namespace ps2_fpu {

constexpr uint32_t kI = 1u << 17, kD = 1u << 16, kO = 1u << 15, kU = 1u << 14;
constexpr uint32_t kSI = 1u << 6, kSD = 1u << 5, kSO = 1u << 4, kSU = 1u << 3;

using ps2_vu0::f;
using ps2_vu0::isZero;
using ps2_vu0::u;

inline float arith(R5900Context *c, const ps2_vu0::Lane &r) {
    if (r.overflow) {
        c->fcr31 |= kO | kSO;
    } else {
        c->fcr31 &= ~kO;
        c->fcr31 = r.underflow ? (c->fcr31 | kU | kSU) : (c->fcr31 & ~kU);
    }
    return f(r.bits);
}

inline float add(R5900Context *c, float a, float b) { return arith(c, ps2_vu0::lane(ps2_vu0::Op::Add, u(a), u(b), 0)); }
inline float sub(R5900Context *c, float a, float b) { return arith(c, ps2_vu0::lane(ps2_vu0::Op::Sub, u(a), u(b), 0)); }
inline float mul(R5900Context *c, float a, float b) { return arith(c, ps2_vu0::lane(ps2_vu0::Op::Mul, u(a), u(b), 0)); }
inline float madd(R5900Context *c, float acc, float a, float b) {
    return arith(c, ps2_vu0::lane(ps2_vu0::Op::Madd, u(a), u(b), u(acc)));
}
inline float msub(R5900Context *c, float acc, float a, float b) {
    return arith(c, ps2_vu0::lane(ps2_vu0::Op::Msub, u(a), u(b), u(acc)));
}

inline float div(R5900Context *c, float a, float b) {
    if (isZero(u(b)))
        c->fcr31 |= isZero(u(a)) ? (kI | kSI) : (kD | kSD);
    return f(ps2_vu0::settle(u(ps2_port_fpu::ps2_fdiv(a, b)), ps2_vu0::value(u(a)) / ps2_vu0::value(u(b))));
}

inline float sqrt(R5900Context *c, float b) {
    c->fcr31 &= ~(kI | kD);
    if (!isZero(u(b)) && (u(b) >> 31))
        c->fcr31 |= kI | kSI;
    return ps2_port_fpu::ps2_fsqrt(b);
}

inline float rsqrt(R5900Context *c, float a, float b) {
    c->fcr31 &= ~(kI | kD);
    if (isZero(u(b)))
        c->fcr31 |= kD | kSD;
    else if (u(b) >> 31)
        c->fcr31 |= kI | kSI;
    return f(ps2_vu0::settle(u(ps2_port_fpu::ps2_frsqrt(a, b)), ps2_vu0::value(u(a)) / ps2_vu0::rootValue(u(b))));
}

inline float abs(R5900Context *c, float a) {
    c->fcr31 &= ~(kO | kU);
    return f(u(a) & 0x7FFFFFFFu);
}
inline float neg(R5900Context *c, float a) {
    c->fcr31 &= ~(kO | kU);
    return f(u(a) ^ 0x80000000u);
}
inline float max(R5900Context *c, float a, float b) {
    c->fcr31 &= ~(kO | kU);
    return ps2_vu0::fmax(a, b);
}
inline float min(R5900Context *c, float a, float b) {
    c->fcr31 &= ~(kO | kU);
    return ps2_vu0::fmin(a, b);
}

}  // namespace ps2_fpu
