// Claude 2026-10-21: the runtime texture pack (PS2_PORT_TEX_PACK: index.txt, identity.txt, blobs/ - the board skins
// and every other replacement the main app draws) on the HW GS path. The 4K apps set PS2_PORT_TEX_PACK, but only
// paraLLEl-GS's plugin read it: nothing on this path did, so no replacement was ever drawn here.
//
// Selection is the main app's, unchanged: claude_tex_pack_core.hpp is a byte-identical copy of
// engineering-baseline/tex-pack-async's (its core_tests pass against this copy). This file only feeds it the same
// events from the GS model, which is the authority here:
//  - host->local uploads, when the transfer's last pixel is written (Gs::on_transfer_done): the payload rebuilt from
//    local memory in the transfer's own format and rectangle, so its bytes - and hash64 - are the game's original
//    upload bytes, whole (every entry of a palette, sampled or not);
//  - local->local copies: their destination forgotten;
//  - CLUT loads (Gs::on_clut_load), from the game's own TEX0;
//  - frame and depth buffer writes: the target's pages forgotten (and uploads into them refused) whenever a draw
//    targets a different buffer than the last;
//  - a whole-memory reset: everything forgotten.
// Footprints are exact 256-byte blocks (page = block / 32, mask bit = block % 32) for uploads and copies, whole
// pages for targets.
// The one TCC-dependent acceptance (a CT24-uploaded palette sampled with alpha ignored, TCC 0) needs TCC in the
// texture cache key: the bridge's CachedTexture adds it for exactly those textures while the pack is on.
// Env: PS2_PORT_TEX_PACK=<dir> (as the apps set it), PS2_PORT_HW_GS_RUNTIME_PACK=0 off, PS2_PORT_TEX_PACK_LOG=<file>,
// PS2_PORT_TEX_PACK_TINT=1 (replacements tinted magenta, a coverage check), PS2_PORT_TEX_PACK_T8_COMPOUND=1.
#pragma once
#include "claude_tex_pack_core.hpp"
#include "blob_prefetch.hpp"
#include "ps2host/gs.h"
#include <chrono>
#include <map>
#include <unordered_map>
#include <unordered_set>

#define STB_IMAGE_STATIC
#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_PNG
#define STBI_NO_STDIO
#define STBI_NO_LINEAR
#define STBI_NO_HDR
#define STBI_MAX_DIMENSIONS 4096
#include "stb/stb_image.h"
#define STB_IMAGE_WRITE_STATIC
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb/stb_image_write.h"

namespace hw_runtime_pack {
namespace gs = ps2host::gs;
using ClaudeTexPack::Footprint;

struct State {
    bool on = false, tint = false;
    std::string dir;
    ClaudeTexPack::Core core;
    std::unique_ptr<LocalTexture::BlobPrefetch> prefetch;
    std::unordered_set<std::string> prefetch_attempted;
    FILE* log = nullptr;
    std::unordered_set<std::string> logged, failed;
    std::unordered_map<std::string, std::uint64_t> ids;   // blob -> replacement id
    std::uint64_t loads = 0, load_failures = 0;
    std::uint64_t prefetched = 0, synchronous_files = 0, decode_ready = 0, decode_claimed = 0, decode_joined = 0;
    // Content identity (the fallback below): the palette each CLUT load put in the buffer, hashed from memory then.
    struct ClutId { bool valid = false; std::uint64_t hash = 0; std::uint32_t cpsm = 0; };
    ClutId t8, t4;
    std::uint64_t content_hits = 0, fb_gpu = 0, fb_clut = 0, fb_none = 0, fb_reject = 0;
    // The last target written (frame / depth buffer), so each change is noted once.
    std::uint64_t last_frame_key = ~0ull, last_z_key = ~0ull;
    std::chrono::steady_clock::time_point last_report = std::chrono::steady_clock::now();
};

inline State& S() {
    static State s;
    return s;
}

inline void Prefetch(std::uint64_t raw);

inline void Init() {
    State& s = S();
    s.core.on_upload_hash = {};
    s.prefetch.reset();
    s.prefetch_attempted.clear();
    s.on = false;
    s.core.reset(512);                      // 4 MB of local memory: 512 pages of 8 KB
    const char* off = std::getenv("PS2_PORT_HW_GS_RUNTIME_PACK");
    if (off && off[0] == '0') return;
    const char* d = std::getenv("PS2_PORT_TEX_PACK");
    if (!d || !*d || (d[0] == '0' && !d[1])) return;
    s.dir = d;
    const char* t = std::getenv("PS2_PORT_TEX_PACK_TINT");
    s.tint = t && t[0] == '1';
    if (const char* l = std::getenv("PS2_PORT_TEX_PACK_LOG")) s.log = std::fopen(l, "w");
    std::string err;
    const size_t n = s.core.load(s.dir + "/index.txt", s.dir + "/identity.txt", &err);
    s.on = n != 0;
    const char* prefetch = std::getenv("PS2_PORT_HW_GS_BLOB_PREFETCH");
    if (s.on && prefetch && prefetch[0] == '1') {
        s.prefetch = std::make_unique<LocalTexture::BlobPrefetch>();
        s.core.on_upload_hash = [](std::uint64_t raw) { Prefetch(raw); };
    }
    const size_t compound = s.on ? s.core.load_compound_cluts(s.dir + "/compound-cluts.txt") : 0;
    const char* t8c = std::getenv("PS2_PORT_TEX_PACK_T8_COMPOUND");
    s.core.set_t8_compound(t8c && t8c[0] == '1' && !t8c[1]);
    std::fprintf(stderr, "[claude-hw-texpack] %zu entries (%zu rejected lines), %zu compound CLUTs from %s%s%s tint=%d\n", n,
                 s.core.bad_lines, compound, d, err.empty() ? "" : " error: ", err.c_str(), int(s.tint));
}

inline bool On() { return S().on; }
inline thread_local std::uint32_t g_dump_cbp = 0;   // the CBP of the texture being looked up (DumpMissing)

inline void Merge(std::map<std::uint32_t, std::uint32_t>& m, std::uint32_t block) { m[(block >> 5) & 511] |= 1u << (block & 31); }

inline Footprint ToFootprint(const std::map<std::uint32_t, std::uint32_t>& m) {
    Footprint fp;
    fp.reserve(m.size());
    for (const auto& [page, mask] : m) fp.push_back({page, mask});
    return fp;
}

// The blocks a rectangle of a buffer covers: one address a block's width/height of pixels apart, and the far edges.
inline Footprint RectBlocks(std::uint32_t bp, std::uint32_t bw, std::uint32_t psm, std::uint32_t x0, std::uint32_t y0,
                            std::uint32_t w, std::uint32_t h) {
    std::map<std::uint32_t, std::uint32_t> m;
    if (!w || !h) return {};
    const std::uint32_t bits = psm == 0x14 || psm == 0x24 || psm == 0x2C ? 4 : psm == 0x13 || psm == 0x1B ? 8
                             : psm == 0x02 || psm == 0x0A || psm == 0x32 || psm == 0x3A ? 16 : 32;
    const std::uint32_t step_x = bits == 4 ? 32 : bits == 32 ? 8 : 16, step_y = bits >= 16 ? 8 : 16;
    auto add = [&](std::uint32_t x, std::uint32_t y) {
        std::uint32_t nibble = 0;
        Merge(m, gs::Memory::Address(bp, bw, x % 2048, y % 2048, psm, &nibble) / 256);
    };
    for (std::uint32_t y = y0;; y = std::min(y + step_y, y0 + h - 1)) {
        for (std::uint32_t x = x0;; x = std::min(x + step_x, x0 + w - 1)) {
            add(x, y);
            if (x == x0 + w - 1) break;
        }
        if (y == y0 + h - 1) break;
    }
    return ToFootprint(m);
}

// Whole pages of a frame or depth buffer: FBW pages a row, `rows` pixel rows.
inline Footprint BufferPages(std::uint32_t page0, std::uint32_t fbw, std::uint32_t psm, std::uint32_t rows) {
    const bool sixteen = psm == 0x02 || psm == 0x0A || psm == 0x32 || psm == 0x3A;
    const std::uint32_t page_h = sixteen ? 64 : 32, row_pages = std::max(1u, fbw);
    const std::uint32_t page_rows = std::max(1u, (rows + page_h - 1) / page_h);
    Footprint fp;
    for (std::uint32_t r = 0; r < page_rows; ++r)
        for (std::uint32_t c = 0; c < row_pages; ++c) fp.push_back({(page0 + r * row_pages + c) & 511, UINT32_MAX});
    return fp;
}

// The transfer just finished (Gs::on_transfer_done): a host->local upload recorded (its payload rebuilt exactly), a
// local->local copy's destination forgotten.
inline void OnTransferDone(const gs::Gs& g) {
    State& s = S();
    if (!s.on) return;
    gs::u64 bitbltbuf = 0, trxpos = 0, trxreg = 0, trxdir = 0;
    g.TransferRegisters(&bitbltbuf, &trxpos, &trxreg, &trxdir);
    const std::uint32_t dir = std::uint32_t(trxdir & 3);
    if (dir != 0 && dir != 2) return;
    ClaudeTexPack::Upload u;
    u.dbp = std::uint32_t(bitbltbuf >> 32) & 0x3FFF;
    u.dbw = std::uint32_t(bitbltbuf >> 48) & 0x3F;
    u.dpsm = std::uint32_t(bitbltbuf >> 56) & 0x3F;
    u.dsax = std::uint32_t(trxpos >> 32) & 0x7FF;
    u.dsay = std::uint32_t(trxpos >> 48) & 0x7FF;
    u.rrw = std::uint32_t(trxreg) & 0xFFF;
    u.rrh = std::uint32_t(trxreg >> 32) & 0xFFF;
    const Footprint fp = RectBlocks(u.dbp, u.dbw, u.dpsm, u.dsax, u.dsay, u.rrw, u.rrh);
    if (dir == 2) { s.core.on_pages_written(fp, false, ClaudeTexPack::E_FORGET_COPY); return; }
    std::uint32_t bytes_per = 0;            // bytes a pixel; 0 = two pixels a byte (low nibble first, as sent)
    switch (u.dpsm) {
        case 0x00: case 0x30: bytes_per = 4; break;
        case 0x01: case 0x31: bytes_per = 3; break;
        case 0x02: case 0x0A: case 0x32: case 0x3A: bytes_per = 2; break;
        case 0x13: case 0x1B: bytes_per = 1; break;
        case 0x14: case 0x24: case 0x2C: bytes_per = 0; break;
        default: s.core.on_pages_written(fp, false, ClaudeTexPack::E_FORGET_UPLOAD); return;
    }
    const std::size_t pixels = std::size_t(u.rrw) * u.rrh;
    const std::size_t bytes = bytes_per ? pixels * bytes_per : (pixels + 1) / 2;
    if (!pixels || bytes > 4u * 1024u * 1024u) { s.core.on_host_upload(u, fp, nullptr, 0, true); return; }
    std::vector<std::uint8_t> data(bytes, 0);
    std::size_t n = 0;
    for (std::uint32_t y = 0; y < u.rrh; ++y)
        for (std::uint32_t x = 0; x < u.rrw; ++x, ++n) {
            const std::uint32_t v = g.memory.Read(u.dbp, u.dbw, (u.dsax + x) % 2048, (u.dsay + y) % 2048, u.dpsm);
            if (bytes_per) for (std::uint32_t k = 0; k < bytes_per; ++k) data[n * bytes_per + k] = std::uint8_t(v >> (8 * k));
            else data[n / 2] |= std::uint8_t((v & 15) << (4 * (n & 1)));
        }
    s.core.on_host_upload(u, fp, data.data(), data.size(), true);
}

// The bytes an upload of this rectangle in this format sends (low nibble first for 4-bit), hashed as the core hashes
// uploads: the identity a texture uploaded on its own would have.
inline bool RectHash(std::uint32_t bp, std::uint32_t bw, std::uint32_t psm, std::uint32_t w, std::uint32_t h, std::uint64_t* out) {
    std::uint32_t bytes_per = 0;
    switch (psm) {
        case 0x00: bytes_per = 4; break;
        case 0x01: bytes_per = 3; break;
        case 0x02: case 0x0A: bytes_per = 2; break;
        case 0x13: bytes_per = 1; break;
        case 0x14: bytes_per = 0; break;
        default: return false;
    }
    const gs::Gs& g = gs::TheGs();
    const std::size_t pixels = std::size_t(w) * h;
    std::vector<std::uint8_t> data(bytes_per ? pixels * bytes_per : (pixels + 1) / 2, 0);
    std::size_t n = 0;
    for (std::uint32_t y = 0; y < h; ++y)
        for (std::uint32_t x = 0; x < w; ++x, ++n) {
            const std::uint32_t v = g.memory.Read(bp, bw, x % 2048, y % 2048, psm);
            if (bytes_per) for (std::uint32_t k = 0; k < bytes_per; ++k) data[n * bytes_per + k] = std::uint8_t(v >> (8 * k));
            else data[n / 2] |= std::uint8_t((v & 15) << (4 * (n & 1)));
        }
    *out = ClaudeTexPack::hash64(data.data(), data.size());
    return true;
}

inline void OnClutLoad(gs::u64 tex0, bool memory_current) {
    {   // the content identity's palette slots, mirroring the core's T8/T4 slots (CSM1, CSA 0, 16x16 / 8x2 only)
        State& s = S();
        const std::uint32_t psm = std::uint32_t(tex0 >> 20) & 0x3F, cpsm = std::uint32_t(tex0 >> 51) & 0xF;
        const std::uint32_t csa = std::uint32_t(tex0 >> 56) & 0x1F, csm = std::uint32_t(tex0 >> 55) & 1, cbp = std::uint32_t(tex0 >> 37) & 0x3FFF;
        if (s.on) {
            State::ClutId c;
            const bool t8 = psm == ClaudeTexPack::PSM_T8, t4 = psm == ClaudeTexPack::PSM_T4;
            if ((t8 || t4) && csa == 0 && csm == 0 && memory_current) c.valid = RectHash(cbp, 1, cpsm, t8 ? 16 : 8, t8 ? 16 : 2, &c.hash);
            c.cpsm = cpsm;
            static int shown = 0;
            if (t4 && shown < 40 && std::getenv("PS2_PORT_TEX_PACK_CLUTLOG")) { ++shown; std::fprintf(stderr, "[claude-hw-texpack] T4 CLUT load cbp %u cpsm %u csa %u csm %u memory %d valid %d\n", cbp, cpsm, csa, csm, int(memory_current), int(c.valid)); }
            if (t8 && csa == 0) {
                s.t8 = c;
                // A T8 CSM1 load fills entries 0-15 from the 8x2 rectangle at CBP: what a T4 draw (CSA 0) reads next.
                State::ClutId v;
                v.cpsm = cpsm;
                if (csm == 0 && memory_current) v.valid = RectHash(cbp, 1, cpsm, 8, 2, &v.hash);
                s.t4 = v;
            }
            else if (t4 && csa == 0) { s.t4 = c; s.t8 = {}; }
            else { s.t8 = {}; if (!t4) s.t4 = {}; }
        }
    }
    State& s = S();
    if (!s.on) return;
    s.core.on_clut_load(std::uint32_t(tex0 >> 20) & 0x3F, std::uint32_t(tex0 >> 51) & 0xF, std::uint32_t(tex0 >> 56) & 0x1F,
                        std::uint32_t(tex0 >> 55) & 1, std::uint32_t(tex0 >> 37) & 0x3FFF, false);
}

// Each draw: its frame buffer (and depth buffer, when written) noted once per change of target.
inline void OnDrawTarget(gs::u64 frame, gs::u64 zbuf, std::uint32_t rows) {
    State& s = S();
    if (!s.on) return;
    const std::uint64_t fkey = (frame & 0x3F3F01FFull) ^ (std::uint64_t(rows) << 40);
    const bool zwrite = !((zbuf >> 32) & 1);
    const std::uint64_t zkey = zwrite ? ((zbuf & 0x0F0001FFull) ^ (((frame >> 16) & 0x3F) << 32) ^ (std::uint64_t(rows) << 40)) : ~0ull;
    if (fkey == s.last_frame_key && zkey == s.last_z_key) return;
    s.core.on_render_pass_flushed();
    s.last_frame_key = fkey;
    s.last_z_key = zkey;
    const std::uint32_t fbw = std::uint32_t(frame >> 16) & 0x3F;
    s.core.on_pages_written(BufferPages(std::uint32_t(frame) & 0x1FF, fbw, std::uint32_t(frame >> 24) & 0x3F, rows), true,
                            ClaudeTexPack::E_FORGET_RT);
    if (zwrite)
        s.core.on_pages_written(BufferPages(std::uint32_t(zbuf) & 0x1FF, fbw, 0x30 | (std::uint32_t(zbuf >> 24) & 0xF), rows),
                                true, ClaudeTexPack::E_FORGET_RT);
}

inline void ForgetAll() {
    State& s = S();
    if (!s.on) return;
    s.core.on_bytes_written(0, 4u << 20, 8192);
    s.last_frame_key = s.last_z_key = ~0ull;
}

inline void Report(bool force) {
    State& s = S();
    if (!s.on) return;
    const auto now = std::chrono::steady_clock::now();
    if (!force && now - s.last_report < std::chrono::seconds(10)) return;
    s.last_report = now;
    char line[1536];
    int o = std::snprintf(line, sizeof line, "[claude-hw-texpack] loads=%llu load_failures=%llu prefetched=%llu synchronous_files=%llu ready=%llu claimed=%llu joined=%llu content_hits=%llu fallback(gpu=%llu clut=%llu none=%llu reject=%llu)",
                          (unsigned long long)s.loads, (unsigned long long)s.load_failures, (unsigned long long)s.prefetched, (unsigned long long)s.synchronous_files, (unsigned long long)s.decode_ready, (unsigned long long)s.decode_claimed, (unsigned long long)s.decode_joined, (unsigned long long)s.content_hits,
                          (unsigned long long)s.fb_gpu, (unsigned long long)s.fb_clut, (unsigned long long)s.fb_none, (unsigned long long)s.fb_reject);
    for (std::uint32_t r = 0; r < ClaudeTexPack::R_COUNT && o < int(sizeof line) - 40; ++r)
        if (s.core.counts[r]) o += std::snprintf(line + o, sizeof line - o, " %s=%llu", ClaudeTexPack::reason_name(r), (unsigned long long)s.core.counts[r]);
    o += std::snprintf(line + o, sizeof line - o, " |");
    for (std::uint32_t e = 0; e < ClaudeTexPack::E_COUNT && o < int(sizeof line) - 40; ++e)
        if (s.core.events[e]) o += std::snprintf(line + o, sizeof line - o, " %s=%llu", ClaudeTexPack::event_name(e), (unsigned long long)s.core.events[e]);
    std::fprintf(stderr, "%s\n", line);
    if (s.log) { std::fprintf(s.log, "%s\n", line); std::fflush(s.log); }
}

inline bool ReadFile(const std::string& path, std::vector<std::uint8_t>& out, size_t max_bytes) {
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return false;
    bool ok = std::fseek(f, 0, SEEK_END) == 0;
    const long n = ok ? std::ftell(f) : -1;
    ok = ok && n > 0 && size_t(n) <= max_bytes && std::fseek(f, 0, SEEK_SET) == 0;
    if (ok) { out.resize(size_t(n)); ok = std::fread(out.data(), 1, out.size(), f) == out.size(); }
    std::fclose(f);
    return ok;
}

// A blob's pixels: .rgba raw RGBA8 at the index's size, .png lossless 8-bit (as the main app's loader).
inline bool LoadPixelsFile(const std::string& dir, const ClaudeTexPack::Entry& e, std::vector<std::uint8_t>& px) {
    const size_t want = size_t(e.hw) * e.hh * 4;
    const std::string path = dir + "/blobs/" + e.blob;
    if (e.blob.size() > 5 && e.blob.compare(e.blob.size() - 5, 5, ".rgba") == 0) return ReadFile(path, px, want) && px.size() == want;
    std::vector<std::uint8_t> file;
    if (!ReadFile(path, file, size_t(64) * 1024 * 1024)) return false;
    int w = 0, h = 0, comp = 0;
    if (!stbi_info_from_memory(file.data(), int(file.size()), &w, &h, &comp) || std::uint32_t(w) != e.hw ||
        std::uint32_t(h) != e.hh || stbi_is_16_bit_from_memory(file.data(), int(file.size())))
        return false;
    stbi_uc* dec = stbi_load_from_memory(file.data(), int(file.size()), &w, &h, &comp, 4);
    if (!dec) return false;
    px.assign(dec, dec + want);
    stbi_image_free(dec);
    return true;
}

inline std::string BlobKey(const ClaudeTexPack::Entry& e) {
    return e.blob + ":" + std::to_string(e.hw) + "x" + std::to_string(e.hh);
}
inline void Prefetch(std::uint64_t raw) {
    State& s = S();
    if (!s.prefetch) return;
    const auto* entries = s.core.entries_for(raw);
    if (!entries) return;
    for (const auto& entry : *entries) {
        // Already consumed once: its GPU residency is managed by the renderer.
        if (s.ids.count(entry.blob) || s.failed.count(entry.blob)) continue;
        const auto e = entry;
        const auto dir = s.dir;
        const std::string key = BlobKey(e);
        if (s.prefetch_attempted.count(key)) continue;
        const bool queued = s.prefetch->enqueue(key, size_t(e.hw) * e.hh * 4, [dir, e] {
            std::vector<std::uint8_t> pixels;
            if (!LoadPixelsFile(dir, e, pixels)) pixels.clear();
            return pixels;
        });
        if (queued) s.prefetch_attempted.insert(key);
    }
}
inline bool LoadPixels(const ClaudeTexPack::Entry& e, std::vector<std::uint8_t>& px) {
    State& s = S();
    LocalTexture::BlobPrefetch::Taken how;
    if (s.prefetch && s.prefetch->take(BlobKey(e), px, &how)) {
        ++s.prefetched;
        if (how == LocalTexture::BlobPrefetch::Taken::Ready) ++s.decode_ready;
        else if (how == LocalTexture::BlobPrefetch::Taken::Claimed) ++s.decode_claimed;
        else ++s.decode_joined;
        return px.size() == size_t(e.hw) * e.hh * 4;
    }
    ++s.synchronous_files;
    return LoadPixelsFile(s.dir, e, px);
}

// PS2_PORT_TEX_PACK_DUMP=<dir> (diagnostic, to make new pack entries): each texture the pack does not have
// (not_in_index), once, as <raw>_<clut>_<w>x<h>_psm<psm>_cpsm<cpsm>.png - decoded from the GS model's memory (the
// palette from its uploaded 16x16 / 8x2 rectangle at CBP, CSM1 order; alpha shown doubled) - with the key the pack's
// index.txt / identity.txt need. The palette key is hash64 of that rectangle's bytes, as the core records CLUT uploads
// (an index entry made from a wrong guess simply never matches).
inline void DumpMissing(const ClaudeTexPack::Query& q, std::uint64_t raw) {
    static const char* dir = std::getenv("PS2_PORT_TEX_PACK_DUMP");
    static std::unordered_set<std::string> done;
    if (!dir || !*dir || done.size() >= 4000) return;
    if (q.psm == ClaudeTexPack::PSM_CT32 && q.tw >= 9 && q.th >= 9) return;   // 512+ CT32: frame buffers read back, not art
    const gs::Gs& g = gs::TheGs();
    const std::uint32_t w = 1u << std::min(q.tw, 10u), h = 1u << std::min(q.th, 10u);
    const bool t8 = q.psm == ClaudeTexPack::PSM_T8, t4 = q.psm == ClaudeTexPack::PSM_T4;
    std::uint64_t clut = 0;
    std::uint32_t pal[256] = {};
    if (t8 || t4) {
        const std::uint32_t cw = t8 ? 16 : 8, ch = t8 ? 16 : 2;
        const std::uint32_t bpp = q.cpsm == ClaudeTexPack::PSM_CT32 ? 4 : q.cpsm == ClaudeTexPack::PSM_CT24 ? 3 : 2;
        std::vector<std::uint8_t> raw(size_t(cw) * ch * bpp);
        std::uint32_t rect[256] = {};
        for (std::uint32_t y = 0; y < ch; ++y)
            for (std::uint32_t x = 0; x < cw; ++x) {
                const std::uint32_t v = g.memory.Read(g_dump_cbp, 1, x, y, q.cpsm);
                rect[y * cw + x] = v;
                for (std::uint32_t k = 0; k < bpp; ++k) raw[(size_t(y) * cw + x) * bpp + k] = std::uint8_t(v >> (8 * k));
            }
        clut = ClaudeTexPack::hash64(raw.data(), raw.size());
        for (std::uint32_t i = 0; i < (t8 ? 256u : 16u); ++i) {
            const std::uint32_t x = t8 ? (i & 7) + ((i & 16) ? 8 : 0) : (i & 7), y = t8 ? ((i >> 5) * 2) + ((i & 8) ? 1 : 0) : (i >> 3);
            std::uint32_t v = rect[y * cw + x];
            if (q.cpsm == ClaudeTexPack::PSM_CT24) v = (v & 0xFFFFFF) | 0x80000000u;
            else if (q.cpsm != ClaudeTexPack::PSM_CT32) v = ((v & 31) << 3) | (((v >> 5) & 31) << 11) | (((v >> 10) & 31) << 19) | ((v & 0x8000) ? 0x80000000u : 0);
            pal[i] = v;
        }
    }
    char name[200];
    std::snprintf(name, sizeof name, "%016llx_%016llx_%ux%u_psm%u_cpsm%u", (unsigned long long)raw, (unsigned long long)clut, w, h, q.psm, q.cpsm);
    if (!done.insert(name).second) return;
    std::vector<std::uint8_t> px(size_t(w) * h * 4);
    for (std::uint32_t y = 0; y < h; ++y)
        for (std::uint32_t x = 0; x < w; ++x) {
            std::uint32_t v = g.memory.Read(q.tbp0, q.tbw, x % 2048, y % 2048, q.psm);
            if (t8 || t4) v = pal[v & (t8 ? 255 : 15)];
            else if (q.psm == ClaudeTexPack::PSM_CT24) v = (v & 0xFFFFFF) | 0x80000000u;
            else if (q.psm != ClaudeTexPack::PSM_CT32) v = ((v & 31) << 3) | (((v >> 5) & 31) << 11) | (((v >> 10) & 31) << 19) | ((v & 0x8000) ? 0x80000000u : 0);
            std::uint8_t* o = &px[(size_t(y) * w + x) * 4];
            o[0] = std::uint8_t(v); o[1] = std::uint8_t(v >> 8); o[2] = std::uint8_t(v >> 16); o[3] = std::uint8_t(std::min(255u, (v >> 24) * 2));
        }
    stbi_write_png((std::string(dir) + "/" + name + ".png").c_str(), int(w), int(h), 4, px.data(), int(w * 4));
}

inline void LogDecision(const ClaudeTexPack::Query& q, const ClaudeTexPack::Decision& d) {
    State& s = S();
    if (!s.log || s.logged.size() >= 8192) return;
    char k[160];
    std::snprintf(k, sizeof k, "%u %u %u %u %u %016llx %016llx %u", q.tbp0, q.psm, q.tw, q.th, q.tcc, (unsigned long long)d.raw,
                  (unsigned long long)d.clut, std::uint32_t(d.reason));
    if (!s.logged.insert(k).second) return;
    std::fprintf(s.log, "tbp0=%u psm=%u w=%u h=%u raw=%016llx clut=%016llx cpsm=%u csa=%u texa=%u/%u/%u tcc=%u -> %s%s%s\n", q.tbp0,
                 q.psm, 1u << q.tw, 1u << q.th, (unsigned long long)d.raw, (unsigned long long)d.clut, q.cpsm, q.csa, q.ta0, q.ta1,
                 q.aem, q.tcc, ClaudeTexPack::reason_name(d.reason), d.entry ? " " : "", d.entry ? d.entry->blob.c_str() : "");
}

// The replacement for the texture a draw samples: its blob's pixels (and a stable id for them), or false.
// `upload(id, w, h, pixels)` puts them on the GPU unless `resident(id)` says they are there.
template <class Resident, class Upload>
bool Lookup(const gs::Context& c, gs::u64 texa, std::uint32_t levels, bool tcc_keyed, bool memory_ok, std::uint64_t* id_out,
            Resident resident, Upload upload) {
    State& s = S();
    if (!s.on) return false;
    ClaudeTexPack::Query q;
    q.tbp0 = std::uint32_t(c.tex0) & 0x3FFF;
    q.tbw = std::uint32_t(c.tex0 >> 14) & 0x3F;
    q.psm = std::uint32_t(c.tex0 >> 20) & 0x3F;
    q.tw = std::uint32_t(c.tex0 >> 26) & 0xF;
    q.th = std::uint32_t(c.tex0 >> 30) & 0xF;
    q.tcc = std::uint32_t(c.tex0 >> 34) & 1;
    q.cpsm = std::uint32_t(c.tex0 >> 51) & 0xF;
    q.csm = std::uint32_t(c.tex0 >> 55) & 1;
    q.csa = std::uint32_t(c.tex0 >> 56) & 0x1F;
    q.ta0 = std::uint32_t(texa) & 0xFF;
    q.aem = std::uint32_t(texa >> 15) & 1;
    q.ta1 = std::uint32_t(texa >> 32) & 0xFF;
    // A CT24 palette's alpha byte is not the game's (the core's CT24-as-CT32 case): acceptable only with alpha ignored
    // (TCC 0) and TCC in the texture cache key - CachedTexture keys TCC for exactly these when the pack is on
    // (tcc_keyed); otherwise this fails closed.
    const bool palette = q.psm == ClaudeTexPack::PSM_T8 || q.psm == ClaudeTexPack::PSM_T4;
    q.sampled_cpsm = palette && q.cpsm == ClaudeTexPack::PSM_CT24 ? ClaudeTexPack::PSM_CT32 : q.cpsm;
    q.tcc_in_cache_key = tcc_keyed;
    const bool region = (c.clamp & 3) >= 2 || ((c.clamp >> 2) & 3) >= 2;
    q.rect_x = region ? 1 : 0;              // a region-clamped view is not the uploaded texture: refused (R_RECT)
    q.rect_y = 0;
    q.rect_w = 1u << std::min(q.tw, 10u);
    q.rect_h = 1u << std::min(q.th, 10u);
    q.levels = levels;
    q.samples = 1;
    q.long_term = true;
    ClaudeTexPack::Decision d = s.core.decide(q);
    g_dump_cbp = std::uint32_t(c.tex0 >> 37) & 0x3FFF;
    static const bool dump_all = [] { const char* e = std::getenv("PS2_PORT_TEX_PACK_DUMP_ALL"); return e && e[0] == '1'; }();
    if (d.reason == ClaudeTexPack::R_NOT_IN_INDEX || (dump_all && d.raw)) DumpMissing(q, d.raw);
    // Content identity: a texture the core has no standalone upload record for (sent inside a larger or differently
    // shaped transfer - the Wave House water, for one) is identified by its own texel bytes as an upload of it alone
    // would send them - the bytes the pack's keys hash - read from memory when the CPU's copy is current (not when
    // the GPU holds newer blocks). Its palette is the one the last CLUT load put in the buffer, hashed then. The pack
    // entry is chosen and checked exactly as the core does.
    // PS2_PORT_TEX_PACK_CONTENT=0: no content identity (upload records only, as the main app).
    static const bool content = [] { const char* e = std::getenv("PS2_PORT_TEX_PACK_CONTENT"); return !(e && e[0] == '0'); }();
    // Also a palette whose identity the core could not establish from upload records (clut_unknown - the 4-bit spray
    // sprites): the palette hashed when its CLUT load happened, from current memory, stands in.
    const bool fb_reason = d.reason == ClaudeTexPack::R_UPLOAD_LAYOUT || d.reason == ClaudeTexPack::R_NO_UPLOAD ||
                           d.reason == ClaudeTexPack::R_CLUT_UNKNOWN;
    if (content && fb_reason && !memory_ok) ++s.fb_gpu;
    if (content && fb_reason && memory_ok && !q.rect_x) {
        const std::uint32_t w = 1u << std::min(q.tw, 10u), h = 1u << std::min(q.th, 10u);
        const bool palette = q.psm == ClaudeTexPack::PSM_T8 || q.psm == ClaudeTexPack::PSM_T4;
        const State::ClutId& cl = q.psm == ClaudeTexPack::PSM_T8 ? s.t8 : s.t4;
        std::uint64_t raw = 0;
        const bool clut_ok = !palette || (cl.valid && cl.cpsm == q.cpsm && !q.csa && !q.csm);
        if (!clut_ok) {
            ++s.fb_clut;
            static int shown = 0;
            if (q.psm == ClaudeTexPack::PSM_T4 && shown < 40 && std::getenv("PS2_PORT_TEX_PACK_CLUTLOG")) { ++shown; std::fprintf(stderr, "[claude-hw-texpack] T4 lookup tbp %u cbp %u cpsm %u csa %u csm %u slot valid %d cpsm %u\n", q.tbp0, g_dump_cbp, q.cpsm, q.csa, q.csm, int(cl.valid), cl.cpsm); }
        }
        if (clut_ok && RectHash(q.tbp0, q.tbw, q.psm, w, h, &raw)) {
            const std::uint64_t want_clut = palette ? cl.hash : 0;
            const std::vector<ClaudeTexPack::Entry>* entries = s.core.entries_for(raw);
            const ClaudeTexPack::Entry* pick = nullptr;
            bool ambiguous = false;
            if (entries)
                for (const auto& e : *entries) {
                    if (e.w != w || e.h != h || e.clut != want_clut) continue;
                    if (!pick) pick = &e;
                    else if (pick->blob != e.blob || pick->has_id != e.has_id ||
                             (pick->has_id && (pick->id.tex_psm != e.id.tex_psm || pick->id.color_psm != e.id.color_psm || pick->id.safe != e.id.safe)))
                        ambiguous = true;
                }
            const std::uint32_t color = palette ? q.cpsm : q.psm;
            bool ok = pick && !ambiguous && pick->has_id && !pick->id.conflict && pick->id.safe &&
                      pick->id.tex_psm == q.psm && pick->id.color_psm == color;
            if (ok && palette && color == ClaudeTexPack::PSM_CT24) ok = q.sampled_cpsm == ClaudeTexPack::PSM_CT32 && q.tcc == 0 && q.tcc_in_cache_key;
            else if (ok && color == ClaudeTexPack::PSM_CT24) ok = q.aem == 0 && q.ta0 == 0x80;
            else if (ok && (color == ClaudeTexPack::PSM_CT16 || color == ClaudeTexPack::PSM_CT16S)) ok = q.aem == 0 && q.ta0 == 0 && q.ta1 == 0x80;
            else if (ok && palette && q.sampled_cpsm != q.cpsm) ok = false;
            if (ok) {
                s.core.counts[d.reason]--;
                s.core.counts[ClaudeTexPack::R_REPLACED]++;
                ++s.content_hits;
                d.reason = ClaudeTexPack::R_REPLACED;
                d.entry = pick;
                d.raw = raw;
                d.clut = want_clut;
            } else if (!entries) {
                ++s.fb_none;
                DumpMissing(q, raw);
            } else {
                ++s.fb_reject;
            }
        }
    }
    if (d.reason == ClaudeTexPack::R_REPLACED && d.entry) {
        const ClaudeTexPack::Entry& e = *d.entry;
        auto found = s.ids.find(e.blob);
        std::uint64_t id = found != s.ids.end() ? found->second
                                                : (ClaudeTexPack::hash64(reinterpret_cast<const std::uint8_t*>(e.blob.data()), e.blob.size()) | (1ull << 63));
        s.ids[e.blob] = id;
        if (!resident(id)) {
            std::vector<std::uint8_t> px;
            if (s.failed.count(e.blob) || !LoadPixels(e, px)) {
                if (s.failed.insert(e.blob).second) ++s.load_failures;
                s.core.counts[ClaudeTexPack::R_REPLACED]--;
                s.core.counts[ClaudeTexPack::R_LOAD_FAILED]++;
                d.reason = ClaudeTexPack::R_LOAD_FAILED;
                LogDecision(q, d);
                Report(false);
                return false;
            }
            if (s.tint)
                for (size_t i = 0; i + 3 < px.size(); i += 4) { px[i] = std::uint8_t((px[i] + 255) / 2); px[i + 1] = std::uint8_t(px[i + 1] / 2); px[i + 2] = std::uint8_t((px[i + 2] + 255) / 2); }
            if (!upload(id, e.hw, e.hh, px)) { ++s.load_failures; Report(false); return false; }
            ++s.loads;
        }
        *id_out = id;
    }
    LogDecision(q, d);
    Report(false);
    return d.reason == ClaudeTexPack::R_REPLACED;
}
}  // namespace hw_runtime_pack
