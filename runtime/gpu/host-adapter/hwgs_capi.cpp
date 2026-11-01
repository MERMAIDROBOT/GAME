// Claude 2026-10-20: libclaude-hwgs - ps2-port-engine's GS model + hardware renderer (MIT) behind a small C API for
// our GS plugin (PS2_PORT_HW_GS=1). Everything here runs on the plugin's single GS worker thread.
#include <cstdio>
#include <chrono>
#include <atomic>
#include <unistd.h>
#include <sys/stat.h>
#include <cstdlib>
#include <cstring>
#include <string>
#include <map>
#include <vector>
#include "ps2host/gs.h"
#include "ps2gpu/gs_renderer.h"
#include "ps2gpu/surface_api.h"
#include "ps2host/run.h"
#include "adaptive_scale_rule.hpp"
using namespace ps2host;
namespace ps2host { void RuntimePackForgetAll(); void ClaudeHwScanoutRegister(unsigned,gs::u64); }
namespace ps2host { gs::u32 GpuCurrentScale(); }
namespace ps2host { bool ClaudeHwContentStamp(unsigned*,unsigned*,unsigned*,unsigned long long*); bool ClaudeHwFrame(gs::u64 frame, const std::vector<gs::u32> **rgba, gs::u32 *w, gs::u32 *h, gs::u32 *scale); }
static bool g_started = false;
// Claude 2026-10-25: GS input recorder for offline replay (hw_replay, PGSCAP2). PS2_PORT_HW_GS_RECORD=<file>: armed;
// recording starts when <file>.start exists (checked once a displayed frame) with a Reset record (all 4 MB, the GPU's
// newer blocks brought back first), then every GIF packet (op 9: path + bytes), upload (op 4) and register write
// (op 3), until <file>.stop exists or PS2_PORT_HW_GS_RECORD_MB (default 3000) is reached; it ends with an End record
// (all 4 MB). Unset: nothing. The recording is the game's input to the GS, the same whichever renderer draws it.
namespace rec {
static std::FILE *f = nullptr;
static bool armed = false, on = false, done = false;
static std::string path;
static unsigned long long seq = 0, bytes = 0, limit = 0;
static std::map<unsigned, unsigned long long> privileged;   // the latest value of each, written when recording starts
static void put(unsigned type, const void *a, size_t na, const void *b = nullptr, size_t nb = 0)
{
    const unsigned length = unsigned(na + nb);
    std::fwrite(&type, 4, 1, f); std::fwrite(&length, 4, 1, f); std::fwrite(&seq, 8, 1, f);
    if (na) std::fwrite(a, 1, na, f);
    if (nb) std::fwrite(b, 1, nb, f);
    ++seq; bytes += 16 + length;
}
static void snapshot(unsigned type)
{
    gs::Gs &g = gs::TheGs(); g.Finish();
    if (g.on_memory) g.on_memory(0, 16384, false);
    put(type, g.memory.data(), 4u << 20);
}
static void stop()
{
    if (!on) return;
    snapshot(6); std::fclose(f); f = nullptr; on = false; done = true;
    std::fprintf(stderr, "[claude-hwgs] recording stopped: %llu records, %.1f MB\n", seq, bytes / 1048576.0);
}
static void poll()
{
    if (!armed || done) return;
    struct stat st;
    if (!on && stat((path + ".start").c_str(), &st) == 0) {
        f = std::fopen(path.c_str(), "wb");
        if (!f) { done = true; return; }
        const char magic[8] = {'P', 'G', 'S', 'C', 'A', 'P', '2', 0}; const unsigned version = 2, reserved = 0;
        std::fwrite(magic, 1, 8, f); std::fwrite(&version, 4, 1, f); std::fwrite(&reserved, 4, 1, f);
        on = true; snapshot(1);
        for (const auto& [address, value] : privileged) { const u64 v[2] = {address, value}; put(11, v, 16); }
        std::fprintf(stderr, "[claude-hwgs] recording to %s\n", path.c_str());
    } else if (on && (stat((path + ".stop").c_str(), &st) == 0 || bytes > limit)) stop();
}
static void init()
{
    const char *p = std::getenv("PS2_PORT_HW_GS_RECORD");
    if (!p || !*p) return;
    path = p; armed = true;
    const char *mb = std::getenv("PS2_PORT_HW_GS_RECORD_MB");
    limit = (mb ? std::strtoull(mb, nullptr, 10) : 3000ull) << 20;
    std::atexit(stop);
}
}   // namespace rec
// Explicit renderer policy: preserve authored simulation clocks and opt-out
// identity. Recording/replay never changes scale because of host contention.
static void AdaptiveScale() {
    static const bool enabled=[] {const char* e=std::getenv("PS2_PORT_HW_GS_ADAPTIVE");return e && std::strcmp(e,"1")==0;}();
    static bool done=false;
    if(!enabled || done || rec::armed || ps2host::GpuCurrentScale()<=2)return;
    using clock=std::chrono::steady_clock;
    static const auto start=clock::now();static auto last=start;
    static unsigned long long previous=~0ull;
    unsigned bp=0,bw=0,psm=0;unsigned long long serial=0;
    if(!ps2host::ClaudeHwContentStamp(&bp,&bw,&psm,&serial)||serial==previous)return;
    const auto now=clock::now();const auto gap=std::chrono::duration<double,std::milli>(now-last).count();
    last=now;previous=serial;
    static adaptive_scale_rule::Rule rule=[] {
        adaptive_scale_rule::Rule r;const char* e=std::getenv("PS2_PORT_HW_GS_ADAPTIVE_STALLS");
        char* end=nullptr;const long n=e?std::strtol(e,&end,10):6;
        if(e && end!=e && *end==0 && n>0 && n<=100000)r.need=unsigned(n);
        return r;
    }();
    if(rule.Feed(std::chrono::duration<double>(now-start).count(),gap)){
        const auto scale=ps2host::GpuCurrentScale();GpuSetScale(2);done=true;
        std::fprintf(stderr,"[native renderer] sustained source-content stalls: scale %u -> 2\n",scale);
    }
}
static gs::u64 g_frame = 0;
static void words(int path, const void *data, size_t bytes)
{
    std::vector<gs::u32> w((bytes + 3) / 4, 0u); std::memcpy(w.data(), data, bytes);
    gs::TheGs().Words(path, w.data(), gs::u32(w.size()));
}
// Claude 2026-10-25: at load, before the GPU is made: the renderer's PS2HOST_* diagnostics by a forwarded name.
__attribute__((constructor)) static void claude_forward_profile_env()
{
    if (const char *v = std::getenv("PS2_PORT_HW_GS_PROFILE"); v && *v == '1') {
        setenv("PS2HOST_GPU_PROFILE", "1", 1); setenv("PS2HOST_GPU_COUNT", "1", 1); setenv("PS2HOST_GPU_LOG", "1", 1);
    }
}
extern "C" {
__attribute__((visibility("default"))) int hwgs_start(unsigned scale, char *error, size_t errorSize)
{
    if (g_started) return 1;
    // Diagnostics (PS2HOST_GPU_TEXDUMP etc.) write relative files: run them in a chosen folder.
    if (const char *dir = std::getenv("PS2_PORT_HW_GS_DUMPDIR"); dir && *dir) { if (chdir(dir) == 0) std::fprintf(stderr, "[claude-hwgs] dump folder %s\n", dir); }
    // Claude 2026-10-21: the app launcher forwards only PS2_PORT_* / PS2X_* variables, so the renderer's own PS2HOST_*
    // texture settings never arrived. PS2_PORT_HW_GS_TEXPACK=<folder> -> PS2HOST_TEXTURE_PACK (replacements on the HW
    // path), PS2_PORT_HW_GS_TEXDUMP=<folder> -> PS2HOST_TEXTURE_DUMP (each texture's first decode as .bmp). Unset: as before.
    if (const char *v = std::getenv("PS2_PORT_HW_GS_TEXPACK"); v && *v) { setenv("PS2HOST_TEXTURE_PACK", v, 1); std::fprintf(stderr, "[claude-hwgs] texture pack %s\n", v); }
    if (const char *v = std::getenv("PS2_PORT_HW_GS_TEXDUMP"); v && *v) { setenv("PS2HOST_TEXTURE_DUMP", v, 1); std::fprintf(stderr, "[claude-hwgs] texture dump %s\n", v); }
    // Claude 2026-10-25: the launcher forwards PS2_PORT_* only - the renderer's GPU profile/counters by that name.
    if (const char *v = std::getenv("PS2_PORT_HW_GS_PROFILE"); v && *v == '1') { setenv("PS2HOST_GPU_PROFILE", "1", 1); setenv("PS2HOST_GPU_COUNT", "1", 1); setenv("PS2HOST_GPU_LOG", "1", 1); setenv("PS2HOST_GPU_WRITE_LOG", "1", 1); }
    rec::init();
    if (const char *sw = std::getenv("PS2_PORT_HW_GS_SOFTWARE"); sw && sw[0] == '1')
    {   // Diagnostic: the same GS model with its software rasterizer (no GPU); frames come from hwgs_picture_cpu.
        g_started = true; std::fprintf(stderr, "[claude-hwgs] SOFTWARE GS model (diagnostic)\n"); return 1;
    }
    std::string e;
    if (!StartGpuRenderer(&e, GpuMode::Primary, false)) { std::snprintf(error, errorSize, "%s", e.c_str()); return 0; }
    GpuSetScale(scale ? scale : 2u);
    g_started = true;
    return 1;
}
__attribute__((visibility("default"))) void hwgs_reset(const void *vram, size_t bytes)
{
    gs::Gs &g = gs::TheGs();
    if (rec::on && bytes == (4u << 20)) rec::put(1, vram, bytes);
    if (bytes == (4u << 20)) std::memcpy(g.memory.data(), vram, bytes);
    // Units are 256-byte blocks (16384 in 4 MB); 2 = block written whole. (Claude 2026-10-20: a null cover crashed.)
    static const std::vector<unsigned char> whole(16384, 2);
    if (g.on_write) g.on_write(0, 16384, whole.data());     // whole memory changed under the GPU
    ps2host::RuntimePackForgetAll();                           // Claude 2026-10-21: and every texture-pack record with it
    g.WriteRegister(0x1A, 1);                                  // PRMODECONT
    g.WriteRegister(0x01, 0x3f80000080808080ull);              // RGBAQ
    g.WriteRegister(0x4C, u64(10) << 16); g.WriteRegister(0x4D, u64(10) << 16);   // FRAME_1/2: FBW=10
    const u64 scissor = u64(639) << 16 | u64(447) << 48;
    g.WriteRegister(0x40, scissor); g.WriteRegister(0x41, scissor);
    g.WriteRegister(0x53, 3);                                  // TRXDIR: none
}
__attribute__((visibility("default"))) void hwgs_write(unsigned address, unsigned long long value) { if (rec::on) { const u64 v[2] = {address, value}; rec::put(3, v, 16); } gs::TheGs().WriteRegister(address, value); static const bool fifo_trace_w=std::getenv("PS2_PORT_FIFO_TRACE")!=nullptr; if(address==0x53 && fifo_trace_w) std::fprintf(stderr,"[fifo cursor] dir-write=%llu offset512=%llu\n",value,(unsigned long long)gs::TheGs().LocalReadOffset(512)); }
__attribute__((visibility("default"))) void hwgs_gif(unsigned path, const void *data, size_t bytes) { if (rec::on) { const unsigned pth = path ? path : 3; rec::put(9, &pth, 4, data, bytes); } words(int(path ? path : 3), data, bytes); }
__attribute__((visibility("default"))) void hwgs_upload(unsigned long long bitblt, unsigned long long pos, unsigned long long reg, unsigned long long dir, const void *data, size_t bytes)
{
    gs::Gs &g = gs::TheGs();
    if (rec::on) { const u64 r[4] = {bitblt, pos, reg, dir}; rec::put(4, r, 32, data, bytes); }
    g.WriteRegister(0x50, bitblt); g.WriteRegister(0x51, pos); g.WriteRegister(0x52, reg); g.WriteRegister(0x53, dir);
    const auto *src = static_cast<const unsigned char *>(data);
    for (size_t off = 0; off < bytes;)
    {
        const size_t amount = std::min(bytes - off, size_t(32767) * 16);
        std::vector<unsigned char> pkt(16 + ((amount + 15) & ~size_t(15)), 0);
        const u64 tag = u64((amount + 15) / 16) | (1ull << 15) | (2ull << 58);
        std::memcpy(pkt.data(), &tag, 8); std::memcpy(pkt.data() + 16, src + off, amount);
        // Path 0 is reserved for these synthesized IMAGE packets (as in the paraLLEl-GS adapter): injecting them on
        // path 3 could split a partially consumed game packet. (Claude 2026-10-20)
        words(0, pkt.data(), pkt.size()); off += amount;
    }
}
__attribute__((visibility("default"))) void hwgs_sync() { gs::TheGs().Finish(); }
__attribute__((visibility("default"))) void hwgs_snapshot(void *out)
{
    gs::Gs &g = gs::TheGs(); g.Finish();
    if (g.on_memory) g.on_memory(0, 16384, false);
    std::memcpy(out, g.memory.data(), 4u << 20);
}
__attribute__((visibility("default"))) void hwgs_privileged(unsigned address, unsigned long long value) { if (rec::armed) rec::privileged[address] = value; if (rec::on) { const u64 v[2] = {address, value}; rec::put(11, v, 16); } gs::TheGs().SetPrivileged(address, value); ps2host::ClaudeHwScanoutRegister(address,value); }
// One displayed frame: RGBA8 into *out (resized by the caller via the size query: pass out=null first).
__attribute__((visibility("default"))) int hwgs_content_stamp(unsigned* fbp,unsigned* fbw,unsigned* psm,unsigned long long* serial) { return ps2host::ClaudeHwContentStamp(fbp,fbw,psm,serial); }
__attribute__((visibility("default"))) int hwgs_frame(void *out, size_t capacity, unsigned *width, unsigned *height, unsigned *scale)
{
    static const std::vector<gs::u32>* rgba=nullptr; static gs::u32 w = 0, h = 0, s = 1;
    if (!out)
    {
        rec::poll();
        if (rec::on) rec::put(10, nullptr, 0);     // a displayed frame (the replay presents one here)
        gs::TheGs().Finish();
        AdaptiveScale();
        if (!ClaudeHwFrame(g_frame++, &rgba, &w, &h, &s)) { *width = *height = 0; return 0; }
        *width = w; *height = h; *scale = s; return 1;
    }
    const size_t bytes = size_t(w) * h * 4;
    if (capacity == size_t(-1)) { *static_cast<const void **>(out) = rgba->data(); return 1; }   // the pixels in place
    if (capacity < bytes) return 0;
    std::memcpy(out, rgba->data(), bytes);
    return 1;
}
}
extern "C" {
// The displayed picture from local memory (no GPU target, e.g. movies): RGBA8, at the PS2's resolution.
__attribute__((visibility("default"))) int hwgs_picture_cpu(void *out, size_t capacity, unsigned *width, unsigned *height)
{
    static std::vector<gs::u32> bgra; static gs::u32 w = 0, h = 0;
    gs::Gs &g = gs::TheGs();
    if (!out)
    {
        g.Finish();
        if (g.on_memory) g.on_memory(0, 16384, false);
        if (!g.Picture(&bgra, &w, &h)) { *width = *height = 0; return 0; }
        *width = w; *height = h; return 1;
    }
    if (capacity < size_t(w) * h * 4) return 0;
    auto *o = static_cast<unsigned char *>(out);
    for (size_t i = 0; i < size_t(w) * h; i++)
    {
        const gs::u32 c = bgra[i];
        o[i * 4 + 0] = (c >> 16) & 0xFF; o[i * 4 + 1] = (c >> 8) & 0xFF; o[i * 4 + 2] = c & 0xFF; o[i * 4 + 3] = 255;
    }
    return 1;
}
}

extern "C" {
// Claude 2026-10-20: local-to-host transfer (TRXDIR 1) read, `bytes` at a time - the photo preview's
// download (nglDownloadTexture, CT32 128x128). GPU-held blocks are brought back first, then the rectangle
// (BITBLTBUF SBP/SBW/SPSM, TRXPOS SSAX/SSAY, TRXREG RRW/RRH) is read pixel by pixel in the model's layout.
// Each TRXDIR write starts a new transfer, even with identical registers. Bytes past the rectangle are zero.
__attribute__((visibility("default"))) size_t hwgs_read_fifo(void *out, size_t bytes)
{
    gs::Gs &g = gs::TheGs();
    g.Finish();
    gs::u64 bb = 0, pos = 0, reg = 0, dir = 0;
    g.TransferRegisters(&bb, &pos, &reg, &dir);
    const gs::u32 sbp = gs::u32(bb & 0x3FFF), sbw = gs::u32((bb >> 16) & 0x3F), spsm = gs::u32((bb >> 24) & 0x3F);
    const gs::u32 sx = gs::u32(pos & 0x7FF), sy = gs::u32((pos >> 16) & 0x7FF);
    const gs::u32 w = gs::u32(reg & 0xFFF), h = gs::u32((reg >> 32) & 0xFFF);
    const gs::u32 bpp = spsm == 0x00 ? 4 : spsm == 0x01 ? 3 : (spsm == 0x02 || spsm == 0x0A) ? 2 : spsm == 0x13 ? 1 : 0;
    const size_t rowBytes = size_t(w) * (bpp ? bpp : 1);
    size_t offset = size_t(g.LocalReadOffset(gs::u32(rowBytes)));
    static const bool fifo_trace=std::getenv("PS2_PORT_FIFO_TRACE")!=nullptr;
    if(fifo_trace) std::fprintf(stderr,"[fifo cursor] read offset=%zu bytes=%zu row=%zu\n",offset,bytes,rowBytes);
    if (offset == 0 && g.on_memory) g.on_memory(0, 16384, false);
    auto *o = static_cast<unsigned char *>(out);
    std::memset(o, 0, bytes);
    static int warned = 0;
    if (!bpp || !w || !h) { if (warned++ < 4) std::fprintf(stderr, "[claude-hwgs] local-to-host read: psm %02X %ux%u not served\n", unsigned(spsm), unsigned(w), unsigned(h)); g.LocalReadOffset(offset + bytes, gs::u32(rowBytes)); return bytes; }
    const size_t total = size_t(w) * h * bpp;
    for (size_t i = 0; i < bytes && offset + i < total; i++)
    {
        const size_t at = offset + i, px = at / bpp; const unsigned byte = unsigned(at % bpp);
        const gs::u32 x = gs::u32(px % w), y = gs::u32(px / w);
        const gs::u32 v = g.memory.Read(sbp, sbw, sx + x, sy + y, spsm);
        o[i] = (unsigned char)(v >> (8 * byte));
    }
    static int told = 0;
    if (offset == 0 && told++ < 8) std::fprintf(stderr, "[claude-hwgs] local-to-host read: SBP %X SBW %u psm %02X %ux%u at %u,%u (%zu bytes)\n", unsigned(sbp), unsigned(sbw), unsigned(spsm), unsigned(w), unsigned(h), unsigned(sx), unsigned(sy), total);
    g.LocalReadOffset(offset + bytes, gs::u32(rowBytes));
    return bytes;
}
}

namespace ps2host {
bool ClaudeHwFrameInfo(gs::u64, gs::u32*, gs::u32*, gs::u32*);
bool ClaudeHwFrameSurface(gs::u64,gs::u32,gs::u32,gs::u32,gs::u32,gs::u32,gs::u32,unsigned long long*);
}
extern "C" {
__attribute__((visibility("default"))) int hwgs_frame_info(uint32_t* width,uint32_t* height,uint32_t* scale) {
    if (!g_started || !width || !height || !scale) return 0;
    *width=*height=0;*scale=1;
    // Record/replay retains the established pixel path and its exact boundaries.
    if (rec::armed) return 0;
    gs::TheGs().Finish();
    AdaptiveScale();
    return ps2host::ClaudeHwFrameInfo(g_frame,width,height,scale);
}
__attribute__((visibility("default"))) int hwgs_frame_surface(uint32_t sx,uint32_t sy,uint32_t dw,uint32_t dh,
    uint32_t ow,uint32_t oh,uint64_t* serial) {
    if (!g_started || !serial || rec::armed) return 0;
    unsigned long long value=0;
    if (!ps2host::ClaudeHwFrameSurface(g_frame,sx,sy,dw,dh,ow,oh,&value)) return 0;
    ++g_frame;*serial=value;return 1;
}
__attribute__((visibility("default"))) void* hwgs_surface_wait(uint64_t* serial,uint32_t* width,uint32_t* height,int timeout_ms) {
    void* surface=ps2gpu::ZeroCopyWait(serial,width,height,timeout_ms);
    static const bool metrics=[] {const char* value=std::getenv("PS2_PORT_HW_GS_SURFACE_METRICS");return value && std::strcmp(value,"1")==0;}();
    static std::atomic<uint64_t> lastSerial{0};
    if (surface && metrics && lastSerial.exchange(*serial,std::memory_order_relaxed)!=*serial) {
        // The producer fence has completed. This precedes the window GL copy;
        // it is neither monitor presentation nor a unique simulation-frame count.
        const auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
        std::fprintf(stderr,"[native-surface acquired] ns=%lld serial=%llu width=%u height=%u\n",(long long)ns,(unsigned long long)*serial,*width,*height);
    }
    return surface;
}
__attribute__((visibility("default"))) void hwgs_surface_release(uint64_t serial) {ps2gpu::ZeroCopyRelease(serial);}
}
