#pragma once
// R5900 multimedia (MMI) forms that the recompiler emits as calls instead of inline SSE
// (ps2recomp/mmi_corrections.h lists them). Each follows the EE Core instruction set: lanes are
// little-endian, and HI/LO are 128 bits wide, with hi/lo holding bits 63..0 and hi1/lo1 bits
// 127..64. behaviour_check.py runs every listed form against an independent model.
#include <cstdint>
#include <cstring>

namespace ps2_mmi {

template <class T>
struct Q {
    T v[16 / sizeof(T)];
};

template <class T>
inline Q<T> gpr(const R5900Context *c, int r) {
    Q<T> q{};
    if (r != 0)
        std::memcpy(q.v, &c->r[r], 16);
    return q;
}

template <class T>
inline void setGpr(R5900Context *c, int r, const Q<T> &q) {
    if (r != 0)
        std::memcpy(&c->r[r], q.v, 16);
}

template <class T>
inline Q<T> pair(uint64_t low, uint64_t high) {
    const uint64_t d[2] = {low, high};
    Q<T> q;
    std::memcpy(q.v, d, 16);
    return q;
}

template <class T>
inline Q<T> hi(const R5900Context *c) { return pair<T>(c->hi, c->hi1); }

template <class T>
inline Q<T> lo(const R5900Context *c) { return pair<T>(c->lo, c->lo1); }

template <class T>
inline void setHiLo(R5900Context *c, const Q<T> &h, const Q<T> &l) {
    uint64_t d[2];
    std::memcpy(d, h.v, 16);
    c->hi = d[0];
    c->hi1 = d[1];
    std::memcpy(d, l.v, 16);
    c->lo = d[0];
    c->lo1 = d[1];
}

inline uint64_t sx32(uint64_t v) { return (uint64_t)(int64_t)(int32_t)(uint32_t)v; }

template <class T, int N>
inline void permuteRt(R5900Context *c, int rd, int rt, const int (&order)[N]) {
    const Q<T> t = gpr<T>(c, rt);
    Q<T> out;
    for (int i = 0; i < N; ++i)
        out.v[i] = t.v[order[i]];
    setGpr(c, rd, out);
}

// ---- shifts ----
inline void psllh(R5900Context *c, int rd, int, int rt, int sa) {
    Q<uint16_t> t = gpr<uint16_t>(c, rt);
    for (auto &x : t.v) x = (uint16_t)(x << (sa & 15));
    setGpr(c, rd, t);
}
inline void psrlh(R5900Context *c, int rd, int, int rt, int sa) {
    Q<uint16_t> t = gpr<uint16_t>(c, rt);
    for (auto &x : t.v) x = (uint16_t)(x >> (sa & 15));
    setGpr(c, rd, t);
}
inline void psrah(R5900Context *c, int rd, int, int rt, int sa) {
    Q<int16_t> t = gpr<int16_t>(c, rt);
    for (auto &x : t.v) x = (int16_t)(x >> (sa & 15));
    setGpr(c, rd, t);
}

// Even words of rt shifted by the low five bits of the matching rs word; results sign-extended.
template <class Op>
inline void variableShift(R5900Context *c, int rd, int rs, int rt, Op op) {
    const Q<uint32_t> a = gpr<uint32_t>(c, rs), b = gpr<uint32_t>(c, rt);
    Q<uint64_t> out;
    for (int k = 0; k < 2; ++k)
        out.v[k] = sx32(op(b.v[2 * k], a.v[2 * k] & 31u));
    setGpr(c, rd, out);
}
inline void psllvw(R5900Context *c, int rd, int rs, int rt, int) {
    variableShift(c, rd, rs, rt, [](uint32_t x, uint32_t n) { return x << n; });
}
inline void psrlvw(R5900Context *c, int rd, int rs, int rt, int) {
    variableShift(c, rd, rs, rt, [](uint32_t x, uint32_t n) { return x >> n; });
}
inline void psravw(R5900Context *c, int rd, int rs, int rt, int) {
    variableShift(c, rd, rs, rt, [](uint32_t x, uint32_t n) { return (uint32_t)((int32_t)x >> n); });
}

// ---- absolute value and unsigned saturation ----
inline void pabsw(R5900Context *c, int rd, int, int rt, int) {
    Q<int32_t> t = gpr<int32_t>(c, rt);
    for (auto &x : t.v) x = x == INT32_MIN ? INT32_MAX : (x < 0 ? -x : x);
    setGpr(c, rd, t);
}
inline void pabsh(R5900Context *c, int rd, int, int rt, int) {
    Q<int16_t> t = gpr<int16_t>(c, rt);
    for (auto &x : t.v) x = x == INT16_MIN ? INT16_MAX : (int16_t)(x < 0 ? -x : x);
    setGpr(c, rd, t);
}
inline void padduh(R5900Context *c, int rd, int rs, int rt, int) {
    const Q<uint16_t> a = gpr<uint16_t>(c, rs), b = gpr<uint16_t>(c, rt);
    Q<uint16_t> out;
    for (int i = 0; i < 8; ++i) {
        const uint32_t sum = (uint32_t)a.v[i] + b.v[i];
        out.v[i] = (uint16_t)(sum > 0xFFFFu ? 0xFFFFu : sum);
    }
    setGpr(c, rd, out);
}
inline void psubuh(R5900Context *c, int rd, int rs, int rt, int) {
    const Q<uint16_t> a = gpr<uint16_t>(c, rs), b = gpr<uint16_t>(c, rt);
    Q<uint16_t> out;
    for (int i = 0; i < 8; ++i)
        out.v[i] = (uint16_t)(a.v[i] > b.v[i] ? a.v[i] - b.v[i] : 0);
    setGpr(c, rd, out);
}

// ---- lane shuffles ----
inline void pinth(R5900Context *c, int rd, int rs, int rt, int) {
    const Q<uint16_t> a = gpr<uint16_t>(c, rs), b = gpr<uint16_t>(c, rt);
    Q<uint16_t> out;
    for (int i = 0; i < 4; ++i) {
        out.v[2 * i] = b.v[i];
        out.v[2 * i + 1] = a.v[4 + i];
    }
    setGpr(c, rd, out);
}
inline void pinteh(R5900Context *c, int rd, int rs, int rt, int) {
    const Q<uint16_t> a = gpr<uint16_t>(c, rs), b = gpr<uint16_t>(c, rt);
    Q<uint16_t> out;
    for (int i = 0; i < 4; ++i) {
        out.v[2 * i] = b.v[2 * i];
        out.v[2 * i + 1] = a.v[2 * i];
    }
    setGpr(c, rd, out);
}
inline void pexeh(R5900Context *c, int rd, int, int rt, int) {
    static const int order[8] = {2, 1, 0, 3, 6, 5, 4, 7};
    permuteRt<uint16_t>(c, rd, rt, order);
}
inline void prevh(R5900Context *c, int rd, int, int rt, int) {
    static const int order[8] = {3, 2, 1, 0, 7, 6, 5, 4};
    permuteRt<uint16_t>(c, rd, rt, order);
}
inline void pexch(R5900Context *c, int rd, int, int rt, int) {
    static const int order[8] = {0, 2, 1, 3, 4, 6, 5, 7};
    permuteRt<uint16_t>(c, rd, rt, order);
}
inline void pexew(R5900Context *c, int rd, int, int rt, int) {
    static const int order[4] = {2, 1, 0, 3};
    permuteRt<uint32_t>(c, rd, rt, order);
}
inline void pexcw(R5900Context *c, int rd, int, int rt, int) {
    static const int order[4] = {0, 2, 1, 3};
    permuteRt<uint32_t>(c, rd, rt, order);
}
inline void prot3w(R5900Context *c, int rd, int, int rt, int) {
    static const int order[4] = {1, 2, 0, 3};
    permuteRt<uint32_t>(c, rd, rt, order);
}

// ---- HI/LO moves ----
inline void pmfhi(R5900Context *c, int rd, int, int, int) { setGpr(c, rd, hi<uint64_t>(c)); }
inline void pmflo(R5900Context *c, int rd, int, int, int) { setGpr(c, rd, lo<uint64_t>(c)); }
inline void pmthi(R5900Context *c, int, int rs, int, int) {
    const Q<uint64_t> s = gpr<uint64_t>(c, rs);
    c->hi = s.v[0];
    c->hi1 = s.v[1];
}
inline void pmtlo(R5900Context *c, int, int rs, int, int) {
    const Q<uint64_t> s = gpr<uint64_t>(c, rs);
    c->lo = s.v[0];
    c->lo1 = s.v[1];
}
inline void pmfhl_lw(R5900Context *c, int rd, int, int, int) {
    const Q<uint32_t> h = hi<uint32_t>(c), l = lo<uint32_t>(c);
    setGpr(c, rd, Q<uint32_t>{{l.v[0], h.v[0], l.v[2], h.v[2]}});
}
inline void pmfhl_uw(R5900Context *c, int rd, int, int, int) {
    const Q<uint32_t> h = hi<uint32_t>(c), l = lo<uint32_t>(c);
    setGpr(c, rd, Q<uint32_t>{{l.v[1], h.v[1], l.v[3], h.v[3]}});
}
inline void pmfhl_slw(R5900Context *c, int rd, int, int, int) {
    const Q<uint32_t> h = hi<uint32_t>(c), l = lo<uint32_t>(c);
    Q<uint64_t> out;
    for (int k = 0; k < 2; ++k) {
        const int64_t v = (int64_t)(((uint64_t)h.v[2 * k] << 32) | l.v[2 * k]);
        out.v[k] = v > INT32_MAX ? (uint64_t)INT32_MAX : v < INT32_MIN ? (uint64_t)(int64_t)INT32_MIN : sx32(l.v[2 * k]);
    }
    setGpr(c, rd, out);
}
inline void pmfhl_lh(R5900Context *c, int rd, int, int, int) {
    const Q<uint16_t> h = hi<uint16_t>(c), l = lo<uint16_t>(c);
    setGpr(c, rd, Q<uint16_t>{{l.v[0], l.v[2], h.v[0], h.v[2], l.v[4], l.v[6], h.v[4], h.v[6]}});
}
inline void pmfhl_sh(R5900Context *c, int rd, int, int, int) {
    const Q<int32_t> h = hi<int32_t>(c), l = lo<int32_t>(c);
    const int32_t words[8] = {l.v[0], l.v[1], h.v[0], h.v[1], l.v[2], l.v[3], h.v[2], h.v[3]};
    Q<int16_t> out;
    for (int i = 0; i < 8; ++i)
        out.v[i] = (int16_t)(words[i] > INT16_MAX ? INT16_MAX : words[i] < INT16_MIN ? INT16_MIN : words[i]);
    setGpr(c, rd, out);
}
inline void pmthl_lw(R5900Context *c, int, int rs, int, int) {
    const Q<uint32_t> s = gpr<uint32_t>(c, rs);
    Q<uint32_t> h = hi<uint32_t>(c), l = lo<uint32_t>(c);
    l.v[0] = s.v[0];
    h.v[0] = s.v[1];
    l.v[2] = s.v[2];
    h.v[2] = s.v[3];
    setHiLo(c, h, l);
}

// ---- word multiply / divide: one 64-bit result per doubleword, split into its HI and LO ----
inline void wordMultiply(R5900Context *c, int rd, int rs, int rt, bool isSigned, int accumulate) {
    const Q<uint32_t> a = gpr<uint32_t>(c, rs), b = gpr<uint32_t>(c, rt);
    Q<uint64_t> h = hi<uint64_t>(c), l = lo<uint64_t>(c), out;
    for (int k = 0; k < 2; ++k) {
        const uint32_t x = a.v[2 * k], y = b.v[2 * k];
        const uint64_t product = isSigned ? (uint64_t)((int64_t)(int32_t)x * (int64_t)(int32_t)y) : (uint64_t)x * y;
        const uint64_t base = ((h.v[k] & 0xFFFFFFFFull) << 32) | (l.v[k] & 0xFFFFFFFFull);
        const uint64_t total = accumulate > 0 ? base + product : accumulate < 0 ? base - product : product;
        out.v[k] = total;
        l.v[k] = sx32(total);
        h.v[k] = sx32(total >> 32);
    }
    setHiLo(c, h, l);
    setGpr(c, rd, out);
}
inline void pmultw(R5900Context *c, int rd, int rs, int rt, int) { wordMultiply(c, rd, rs, rt, true, 0); }
inline void pmultuw(R5900Context *c, int rd, int rs, int rt, int) { wordMultiply(c, rd, rs, rt, false, 0); }
inline void pmaddw(R5900Context *c, int rd, int rs, int rt, int) { wordMultiply(c, rd, rs, rt, true, 1); }
inline void pmadduw(R5900Context *c, int rd, int rs, int rt, int) { wordMultiply(c, rd, rs, rt, false, 1); }
inline void pmsubw(R5900Context *c, int rd, int rs, int rt, int) { wordMultiply(c, rd, rs, rt, true, -1); }

// Division by zero and INT32_MIN / -1 give the EE's defined results, as DIV does.
inline void signedDivide(int32_t n, int32_t d, int32_t &q, int32_t &r) {
    if (d == 0) {
        q = n < 0 ? 1 : -1;
        r = n;
    } else if (n == INT32_MIN && d == -1) {
        q = INT32_MIN;
        r = 0;
    } else {
        q = n / d;
        r = n % d;
    }
}
inline void pdivw(R5900Context *c, int, int rs, int rt, int) {
    const Q<uint32_t> a = gpr<uint32_t>(c, rs), b = gpr<uint32_t>(c, rt);
    Q<uint64_t> h, l;
    for (int k = 0; k < 2; ++k) {
        int32_t q, r;
        signedDivide((int32_t)a.v[2 * k], (int32_t)b.v[2 * k], q, r);
        l.v[k] = sx32((uint32_t)q);
        h.v[k] = sx32((uint32_t)r);
    }
    setHiLo(c, h, l);
}
inline void pdivuw(R5900Context *c, int, int rs, int rt, int) {
    const Q<uint32_t> a = gpr<uint32_t>(c, rs), b = gpr<uint32_t>(c, rt);
    Q<uint64_t> h, l;
    for (int k = 0; k < 2; ++k) {
        const uint32_t n = a.v[2 * k], d = b.v[2 * k];
        l.v[k] = sx32(d == 0 ? 0xFFFFFFFFu : n / d);
        h.v[k] = sx32(d == 0 ? n : n % d);
    }
    setHiLo(c, h, l);
}
inline void pdivbw(R5900Context *c, int, int rs, int rt, int) {
    const Q<int32_t> a = gpr<int32_t>(c, rs);
    const int32_t d = gpr<int16_t>(c, rt).v[0];
    Q<int32_t> h, l;
    for (int i = 0; i < 4; ++i)
        signedDivide(a.v[i], d, l.v[i], h.v[i]);
    setHiLo(c, h, l);
}

// ---- halfword multiply: products land in LO0 LO1 HI0 HI1 LO2 LO3 HI2 HI3 ----
inline void halfwordMultiply(R5900Context *c, int rd, int rs, int rt, int mode) {
    const Q<int16_t> a = gpr<int16_t>(c, rs), b = gpr<int16_t>(c, rt);
    Q<uint32_t> h = hi<uint32_t>(c), l = lo<uint32_t>(c);
    uint32_t *targets[8] = {&l.v[0], &l.v[1], &h.v[0], &h.v[1], &l.v[2], &l.v[3], &h.v[2], &h.v[3]};
    for (int i = 0; i < 8; ++i) {
        const uint32_t p = (uint32_t)((int32_t)a.v[i] * (int32_t)b.v[i]);
        *targets[i] = mode > 0 ? *targets[i] + p : mode < 0 ? *targets[i] - p : p;
    }
    setHiLo(c, h, l);
    setGpr(c, rd, Q<uint32_t>{{l.v[0], h.v[0], l.v[2], h.v[2]}});
}
inline void pmulth(R5900Context *c, int rd, int rs, int rt, int) { halfwordMultiply(c, rd, rs, rt, 0); }
inline void pmaddh(R5900Context *c, int rd, int rs, int rt, int) { halfwordMultiply(c, rd, rs, rt, 1); }
inline void pmsubh(R5900Context *c, int rd, int rs, int rt, int) { halfwordMultiply(c, rd, rs, rt, -1); }

// Horizontal pairs. The odd HI/LO words are undefined in the manual; the hardware leaves the
// first product there, inverted for the subtracting form (as PCSX2 documents).
inline void horizontalMultiply(R5900Context *c, int rd, int rs, int rt, bool subtract) {
    const Q<int16_t> a = gpr<int16_t>(c, rs), b = gpr<int16_t>(c, rt);
    uint32_t sums[4], firsts[4];
    for (int i = 0; i < 4; ++i) {
        const int32_t first = (int32_t)a.v[2 * i + 1] * b.v[2 * i + 1];
        const int32_t second = (int32_t)a.v[2 * i] * b.v[2 * i];
        sums[i] = subtract ? (uint32_t)first - (uint32_t)second : (uint32_t)first + (uint32_t)second;
        firsts[i] = subtract ? ~(uint32_t)first : (uint32_t)first;
    }
    const Q<uint32_t> l{{sums[0], firsts[0], sums[2], firsts[2]}};
    const Q<uint32_t> h{{sums[1], firsts[1], sums[3], firsts[3]}};
    setHiLo(c, h, l);
    setGpr(c, rd, Q<uint32_t>{{sums[0], sums[1], sums[2], sums[3]}});
}
inline void phmadh(R5900Context *c, int rd, int rs, int rt, int) { horizontalMultiply(c, rd, rs, rt, false); }
inline void phmsbh(R5900Context *c, int rd, int rs, int rt, int) { horizontalMultiply(c, rd, rs, rt, true); }

}  // namespace ps2_mmi
