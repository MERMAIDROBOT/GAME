#include "native_registers.hpp"
#include "raw_field.hpp"
#include <string>
#include <cmath>
#include "scanout_latch.hpp"
#include "../../presentation/surface_token.hpp"
#include "../include/ps2gpu/surface_api.h"
// Claude 2026-10-20: hardware GS backend for the plugin (CLAUDE_HW_GS builds). Same interface as RawOwner;
// forwards to libclaude-hwgs (ps2-port-engine's GS model + Vulkan hardware renderer, MIT). paraLLEl-GS remains the
// default backend and the exact reference. Frames come back at the renderer's internal scale and are always
// presented through the packed HD path (1024x896).
#pragma once
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>
extern "C" {
int hwgs_start(unsigned scale, char *error, size_t errorSize);
void hwgs_reset(const void *vram, size_t bytes);
void hwgs_write(unsigned address, unsigned long long value);
void hwgs_gif(unsigned path, const void *data, size_t bytes);
void hwgs_upload(unsigned long long bitblt, unsigned long long pos, unsigned long long reg, unsigned long long dir, const void *data, size_t bytes);
void hwgs_sync();
void hwgs_snapshot(void *out);
void hwgs_privileged(unsigned address, unsigned long long value);
int hwgs_content_stamp(unsigned*,unsigned*,unsigned*,unsigned long long*);
int hwgs_frame(void *out, size_t capacity, unsigned *width, unsigned *height, unsigned *scale);
int hwgs_picture_cpu(void *out, size_t capacity, unsigned *width, unsigned *height);
size_t hwgs_read_fifo(void *out, size_t bytes);
}
namespace LocalGS {
class HwOwner {
    std::vector<uint8_t> scratch;
    RawField last;
    unsigned scale_ = 2;
    ScanoutLatch scanoutLatch_;
    // Bilinear RGBA8 resize, separable and fixed point (Claude 2026-10-20): each source row widened once with
    // precomputed taps, then each output row a two-row blend (vectorizes). The per-pixel float version took
    // ~19 ms a picture at 4x (640x448 -> 2048x1792) and stalled the boot. Same sample positions as before.
    std::vector<uint8_t> wide_;
    std::vector<uint32_t> tapX_; std::vector<uint16_t> wX_;
    void resize(const uint8_t *src, unsigned sw, unsigned sh, unsigned stride, uint8_t *dst, unsigned dw, unsigned dh)
    {
        tapX_.resize(dw); wX_.resize(dw);
        for (unsigned x = 0; x < dw; x++)
        {
            const float fx = (x + 0.5f) * sw / dw - 0.5f; const int x0 = std::max(0, int(fx));
            tapX_[x] = unsigned(std::min(int(sw) - 1, x0)); wX_[x] = uint16_t(std::lround(std::clamp(fx - x0, 0.0f, 1.0f) * 256.0f));
        }
        wide_.resize(size_t(dw) * 4 * sh);
        for (unsigned y = 0; y < sh; y++)
        {
            const uint8_t *row = src + size_t(y) * stride * 4; uint8_t *out = &wide_[size_t(y) * dw * 4];
            for (unsigned x = 0; x < dw; x++)
            {
                const unsigned x0 = tapX_[x], x1 = std::min(sw - 1, x0 + 1); const unsigned w = wX_[x];
                for (int c = 0; c < 4; c++) out[x * 4 + c] = uint8_t((row[x0 * 4 + c] * (256 - w) + row[x1 * 4 + c] * w + 128) >> 8);
            }
        }
        const size_t n = size_t(dw) * 4;
        for (unsigned y = 0; y < dh; y++)
        {
            const float fy = (y + 0.5f) * sh / dh - 0.5f; const int y0 = std::max(0, int(fy)); const int y1 = std::min(int(sh) - 1, y0 + 1);
            const unsigned w = unsigned(std::lround(std::clamp(fy - y0, 0.0f, 1.0f) * 256.0f));
            const uint8_t *a = &wide_[size_t(std::min(y0, int(sh) - 1)) * n], *b = &wide_[size_t(y1) * n]; uint8_t *o = dst + size_t(y) * n;
            if (w == 0) { std::memcpy(o, a, n); continue; }
            for (size_t i = 0; i < n; i++) o[i] = uint8_t((a[i] * (256 - w) + b[i] * w + 128) >> 8);
        }
    }
public:
    explicit HwOwner()
    {
        char error[512] = {};
        const char *s = std::getenv("PS2_PORT_HW_GS_SCALE");
        scale_ = s ? unsigned(std::atoi(s)) : 2u;
        if (!hwgs_start(scale_, error, sizeof(error)))
            throw std::runtime_error(std::string("hardware GS renderer: ") + error);
        std::fprintf(stderr, "[claude-hwgs] hardware GS renderer active (scale %s)\n", s ? s : "2");
        const unsigned os = std::min(4u, std::max(2u, scale_));
        last.width = 512 * os; last.height = 448 * os; last.rgba.assign(size_t(last.width) * last.height * 4, 0); last.seal();
    }
    void reset(const void *initial, size_t bytes)
    {
        if (!initial || bytes != 4 * 1024 * 1024) throw std::runtime_error("Invalid initial GPU VRAM");
        scanoutLatch_.reset();
        hwgs_reset(initial, bytes);
    }
    void write(unsigned address, uint64_t value)
    {
        if (address > 127) throw std::runtime_error("Invalid raw GS register");
        hwgs_write(address, value);
    }
    void gif(unsigned path, const void *data, size_t bytes)
    {
        if (path < 1 || path > 3 || !data || bytes < 16 || bytes % 16 || bytes > 16 * 1024 * 1024) throw std::runtime_error("Invalid raw GIF ingress");
        hwgs_gif(path, data, bytes);
    }
    void uploadCT32(uint64_t bitblt, uint64_t position, uint64_t region, uint64_t direction, const void *data, size_t bytes)
    {
        hwgs_upload(bitblt, position, region, direction, data, bytes);
    }
    void sync() { hwgs_sync(); }
    std::vector<uint8_t> readFIFO(size_t bytes)
    {   // Local-to-host transfer (the photo preview's download), served by the hardware GS host.
        // PS2_PORT_HW_GS_FIFO=0: zeros, as before.
        static const bool fifo = [] { const char *e = std::getenv("PS2_PORT_HW_GS_FIFO"); return !(e && e[0] == '0'); }();
        std::vector<uint8_t> out(bytes, 0);
        if (fifo) hwgs_read_fifo(out.data(), bytes);
        return out;
    }
    std::vector<uint8_t> snapshot() { std::vector<uint8_t> v(4 * 1024 * 1024); hwgs_snapshot(v.data()); return v; }
    void replaceVRAM(const std::vector<uint8_t> &memory) { if (memory.size() == 4 * 1024 * 1024) hwgs_reset(memory.data(), memory.size()); }
    RawField field(const ParallelGS::PrivRegisterState &priv, unsigned phase)
    {
        const auto t0 = std::chrono::steady_clock::now();
        struct Timing { HwOwner *o; std::chrono::steady_clock::time_point t0; ~Timing() {
            // Same line as the paraLLEl-GS backend's PS2_PORT_PRIVATE_EDGE_TIMING, so the bench summary compares.
            static unsigned n = 0; static double total = 0, peak = 0;
            const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
            total += ms; peak = std::max(peak, ms);
            if (++n == 120) { std::fprintf(stderr, "[edge-trial timing] fields=%u mean_ms=%.3f peak_ms=%.3f\n", n, total / n, peak); n = 0; total = peak = 0; }
        } } timing{this, t0};
        (void)phase;
        const auto *b = reinterpret_cast<const uint8_t *>(&priv);
        for (unsigned off = 0; off <= 0xE0u; off += 0x10u) { uint64_t v; std::memcpy(&v, b + off, 8); hwgs_privileged(off, v); }
        unsigned w = 0, h = 0, s = 1;
        // Output size: the renderer's internal scale for 512x448 games (2x 1024x896, 3x 1536x1344, 4x 2048x1792).
        const unsigned outScale = std::min(4u, std::max(2u, scale_));
        // Movies and legal screens (640-wide display, DW/MAGH) come out 640x448 multiples so the presenter can
        // tell them from game frames (512x448 multiples): widescreen draws game frames 16:9 and keeps movies 4:3.
        const bool crt1o = priv.pmode.EN1 && !priv.pmode.EN2;
        const auto &dispo = crt1o ? priv.display1 : priv.display2;
        const unsigned shownW = unsigned(dispo.DW + 1) / unsigned(dispo.MAGH + 1);
        const unsigned baseW = shownW >= 600 && shownW <= 720 ? 640 : 512;
        // The runtime presents frames up to 2048 wide (ps2_runtime.cpp's host texture check): a 4x movie
        // (2560x1792) was dropped and the window showed nothing until the first game frame. Movies (low
        // resolution video) are made at most 3x (1920x1344).
        const unsigned movieScale = baseW == 640 ? std::min(outScale, 3u) : outScale;
        unsigned OW = baseW * movieScale, OH = 448 * movieScale;
        RawField r; r.width = OW; r.height = OH;
        ScanoutContent content{}; bool contentValid=false;
        const ScanoutGeometry geometry{shownW,unsigned(dispo.DH+1)/unsigned(dispo.MAGV+1),bool(priv.smode2.INT && priv.smode2.FFMD),unsigned((crt1o?priv.dispfb1:priv.dispfb2).DBX),unsigned((crt1o?priv.dispfb1:priv.dispfb2).DBY)};
        static const bool zeroCopy = [] { const char *e = std::getenv("PS2_PORT_HW_GS_ZERO_COPY"); const char *hd = std::getenv("PS2_PORT_ENHANCE_HIRES_SCANOUT");
            return e && e[0] == '1' && e[1] == 0 && hd && hd[0] == '1' && hd[1] == 0; }();
        // An unavailable info query can mean recording explicitly requires
        // pixels. Do not re-enable tokens after falling back to a pixel probe.
        const bool surfaceAvailable = zeroCopy && hwgs_frame_info(&w,&h,&s);
        bool probed = surfaceAvailable || hwgs_frame(nullptr,0,&w,&h,&s);
        if(probed && w && h && s>=2 && s<=4 && s!=scale_){
            scale_=s;scanoutLatch_.reset();
            const auto live=baseW==640?std::min(s,3u):s;
            OW=baseW*live;OH=448*live;r.width=OW;r.height=OH;
        }
        bool readBack = !surfaceAvailable;
        if (probed && w && h)
        {
            contentValid=hwgs_content_stamp(&content.base,&content.pitch,&content.format,&content.serial);
            if(contentValid && scanoutLatch_.unchanged(content,geometry)) return last;
            if(contentValid && scanoutLatch_.hold(content,geometry)) {
                static unsigned held=0; if(held++<24) std::fprintf(stderr,"[scanout transition] retained unchanged image layout serial=%llu\n",content.serial);
                return last;
            }
            {
            const bool crt1z = priv.pmode.EN1 && !priv.pmode.EN2;
            const auto &fbz = crt1z ? priv.dispfb1 : priv.dispfb2;
            const auto &dispz = crt1z ? priv.display1 : priv.display2;
            unsigned gwz = unsigned(dispz.DW + 1) / unsigned(dispz.MAGH + 1), ghz = unsigned(dispz.DH + 1) / unsigned(dispz.MAGV + 1);
            if (!gwz || gwz > 1024) gwz = 640;
            if (!ghz || ghz > 1024) ghz = 448;
            const unsigned linesz = (priv.smode2.INT && priv.smode2.FFMD) ? std::max(1u, ghz / 2) : ghz;
            const unsigned sxz = std::min(w - 1, unsigned(fbz.DBX) * s), syz = std::min(h - 1, unsigned(fbz.DBY) * s);
            const unsigned dwz = std::min(w - sxz, gwz * s), dhz = std::min(h - syz, linesz * s);
            static const bool rowCopyZ = [] { const char *e = std::getenv("PS2_PORT_HW_GS_ROWCOPY"); return !(e && e[0] == '0'); }();
            static const bool edgeFillZ = [] { const char *e = std::getenv("PS2_PORT_HW_GS_EDGEFILL"); const char *m = std::getenv("PS2_PORT_HW_GS_SUBPIXEL");
                return !(e && e[0] == '0') && (m && m[0] == '0'); }();
            const float ratioz = float(OH) / float(std::max(1u, dhz));
            const bool plain = rowCopyZ && dwz == OW && (std::fabs(ratioz - 1.0f) < 0.02f || std::fabs(ratioz - 2.0f) < 0.04f) && !(edgeFillZ && s > 1);
            std::uint64_t serial = 0;
            if (surfaceAvailable && plain && hwgs_frame_surface(sxz, syz, dwz, dhz, OW, OH, &serial)) {
                static bool told = false;
                if (!told) { told = true; std::fprintf(stderr, "[claude-hw display] zero-copy presentation %ux%u\n", OW, OH); }
                // shared_ptr calls the deleter even if control-block allocation
                // fails, so the publication lease cannot leak on an exception.
                r.surfaceLease=std::shared_ptr<void>(nullptr,[serial](void*){hwgs_surface_release(serial);});
                r.rgba = ps2port::encodeSurfaceToken({serial, OW, OH});
                if(contentValid) scanoutLatch_.accepted(content,geometry); else scanoutLatch_.reset();
                r.seal();
                last = r;
                return r;
            }
            if (!readBack) { readBack = true; if (!hwgs_frame(nullptr, 0, &w, &h, &s) || !w || !h) return last; }
            }
            r.rgba.resize(size_t(OW) * OH * 4);
            // The renderer's pixels in place (capacity -1): no copy of a 16 MB picture at 4x.
            const void *framePixels = nullptr;
            hwgs_frame(&framePixels, size_t(-1), &w, &h, &s);
            const uint8_t *frame = static_cast<const uint8_t *>(framePixels);
            // Displayed region as the GS model's Picture() takes it: the read circuit shown (2 if enabled),
            // its DISPFB start (DBX, DBY), DISPLAY's DW/MAGH and DH/MAGV - in field mode (SMODE2 INT+FFMD,
            // movies) half the lines, each shown twice - all at the internal scale. (Claude 2026-10-20: movies sat
            // in the top half with stale memory below, read as 448 frame-mode lines.)
            const bool crt1 = priv.pmode.EN1 && !priv.pmode.EN2;
            const auto &fb = crt1 ? priv.dispfb1 : priv.dispfb2;
            const auto &disp = crt1 ? priv.display1 : priv.display2;
            unsigned gw = unsigned(disp.DW + 1) / unsigned(disp.MAGH + 1), gh = unsigned(disp.DH + 1) / unsigned(disp.MAGV + 1);
            if (!gw || gw > 1024) gw = 640;
            if (!gh || gh > 1024) gh = 448;
            const unsigned lines = (priv.smode2.INT && priv.smode2.FFMD) ? std::max(1u, gh / 2) : gh;
            const unsigned sx = std::min(w - 1, unsigned(fb.DBX) * s), sy = std::min(h - 1, unsigned(fb.DBY) * s);
            const unsigned dw = std::min(w - sx, gw * s), dh = std::min(h - sy, lines * s);
            const uint8_t *src = frame + (size_t(sy) * w + sx) * 4;
            { static int told = 0; static unsigned lastKey = 0; const unsigned key = gw * 100000 + lines * 10 + (dw == OW && dh == OH);
              if (key != lastKey && told < 12) { lastKey = key; ++told; std::fprintf(stderr, "[claude-hw display] DW %u MAGH %u DH %u MAGV %u INT %u FFMD %u DBX %u DBY %u -> shown %ux%u lines %u, target %ux%u, crop %ux%u, out %ux%u%s\n",
                unsigned(disp.DW), unsigned(disp.MAGH), unsigned(disp.DH), unsigned(disp.MAGV), unsigned(priv.smode2.INT), unsigned(priv.smode2.FFMD), unsigned(fb.DBX), unsigned(fb.DBY),
                gw, gh, lines, w, h, dw, dh, OW, OH, (dw == OW && dh == OH) ? " (copy)" : " (resize)"); } }
            // Same width: rows copied, not filtered - the display's 447 lines from row 1 (DH 446, DBY 1) to 448
            // (the last row again), or a field's lines each twice (field mode), as the PS2 shows them. A bilinear
            // pass there blurred the picture and cost ~7 ms at 4x. PS2_PORT_HW_GS_ROWCOPY=0: always filtered.
            static const bool rowCopy = [] { const char *e = std::getenv("PS2_PORT_HW_GS_ROWCOPY"); return !(e && e[0] == '0'); }();
            const float ratio = float(OH) / float(std::max(1u, dh));
            if (rowCopy && dw == OW && (std::fabs(ratio - 1.0f) < 0.02f || std::fabs(ratio - 2.0f) < 0.04f))
                for (unsigned y = 0; y < OH; y++)
                {
                    const unsigned sy2 = std::min(dh - 1, unsigned((y + 0.5f) / ratio));
                    std::memcpy(&r.rgba[size_t(y) * OW * 4], src + size_t(sy2) * w * 4, size_t(OW) * 4);
                }
            else resize(src, dw, dh, w, r.rgba.data(), OW, OH);
            // Claude 2026-10-21: the renderer moves vertices half a PS2 pixel (gs.vert: the GS samples a pixel at its
            // whole coordinate, Vulkan at its centre). Exact at 1x; at k x the first ceil((k-1)/2) sub-columns and
            // sub-rows of the target are never covered by anything and keep stale content - the white line at the
            // intro film's left edge (a white flash drew there once). Filled from the first covered column/row
            // when the picture starts at the target's edge. PS2_PORT_HW_GS_EDGEFILL=0: off.
            // (Only with the old half-pixel mapping: the sub-pixel mapping, on unless PS2_PORT_HW_GS_SUBPIXEL=0,
            // covers those sub-pixels itself.)
            static const bool edgeFill = [] { const char *e = std::getenv("PS2_PORT_HW_GS_EDGEFILL"); const char *m = std::getenv("PS2_PORT_HW_GS_SUBPIXEL");
                return !(e && e[0] == '0') && (m && m[0] == '0'); }();
            const unsigned uncovered = (s > 1) ? (s - 1 + 1) / 2 : 0;          // ceil((s-1)/2)
            if (edgeFill && uncovered)
            {
                // Through a resize the stale sub-pixels also blend into the next output column/row: one more.
                const unsigned rx = (OW % dw) ? 1u : 0u, ry = (OH % dh) ? 1u : 0u;
                const unsigned nx = sx == 0 ? std::min(OW - 1, (uncovered * OW + dw - 1) / dw + rx) : 0;
                const unsigned ny = sy == 0 ? std::min(OH - 1, (uncovered * OH + dh - 1) / dh + ry) : 0;
                if (nx) for (unsigned y = 0; y < OH; y++) { uint8_t *row = &r.rgba[size_t(y) * OW * 4]; for (unsigned x = 0; x < nx; x++) std::memcpy(row + x * 4, row + nx * 4, 4); }
                for (unsigned y = 0; y < ny; y++) std::memcpy(&r.rgba[size_t(y) * OW * 4], &r.rgba[size_t(ny) * OW * 4], size_t(OW) * 4);
            }
        }
        else if (hwgs_picture_cpu(nullptr, 0, &w, &h) && w && h)
        {
            r.rgba.resize(size_t(OW) * OH * 4);
            scratch.resize(size_t(w) * h * 4);
            hwgs_picture_cpu(scratch.data(), scratch.size(), &w, &h);
            resize(scratch.data(), w, h, w, r.rgba.data(), OW, OH);
        }
        else return last;
        // (Alpha made opaque by the raw backend's HD path; the CPU picture's is already 255.)
        if(contentValid) scanoutLatch_.accepted(content,geometry); else scanoutLatch_.reset();
        r.seal();
        last = r; // Retain the exact pixels without a full-size frame copy.
        return r;
    }
};
}
