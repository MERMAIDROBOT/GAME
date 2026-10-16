// Synthetic runtime test: no retail data or generated game functions.
#include "ps2_runtime.h"
#include "runtime/ee_scheduler.h"
#include "runtime/ps2_memory.h"
#include "ps2_stubs.h"
#include "ps2_runtime_macros.h"
#include "runtime/gs/ps2_gs_psmct32.h"
#include "runtime/gs/gs_threaded_backend.h"
#include "runtime/gs/gs_cpu_backend.h"
#include <cstring>
#include <array>
#include <vector>
#include <unordered_set>
#include <chrono>
#include <thread>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <fstream>
#include <filesystem>

extern const uint32_t g_ps2RecompiledFunctionTableBase = 0x00100000u;
extern const uint32_t g_ps2RecompiledFunctionTableEnd = 0x0010000cu;
extern const uint32_t g_ps2RecompiledFunctionTableSlotCount = 3u;
PS2Runtime::RecompiledFunction g_ps2RecompiledFunctionTable[3] = {};
namespace ps2_stubs { void sceVu0InnerProduct(uint8_t*, R5900Context*, PS2Runtime*); void sceVu0MulMatrix(uint8_t*, R5900Context*, PS2Runtime*); void sceVu0ApplyMatrix(uint8_t*, R5900Context*, PS2Runtime*); void sceDmaPutEnv(uint8_t*, R5900Context*, PS2Runtime*); }
static uint32_t expected = 0;
static bool finished = false;

void second(uint8_t *, R5900Context *ctx, PS2Runtime *runtime) {
    if (ctx->cop0_count != expected + 8u)
        throw std::runtime_error("Count did not persist across guest dispatch");
    if (std::getenv("PS2_PORT_HOST_VSYNC_TEST") && runtime->eeScheduler().currentVSyncTick() < 2u)
        throw std::runtime_error("Display clock stalled behind slow guest cycle accounting");
    finished = true;
    runtime->requestStop();
}

void first(uint8_t *, R5900Context *ctx, PS2Runtime *runtime) {
    if (std::getenv("PS2_PORT_BENCHMARK")) {
        const auto start = std::chrono::steady_clock::now();
        for (unsigned n = 0; n < 200000u; ++n)
            if (runtime->eeScheduler().rotateReadyQueue(1, false) != 0)
                throw std::runtime_error("Synthetic ready-queue benchmark failed");
        std::cout << "BENCHMARK: 200000 ready-queue rotations, microseconds="
                  << std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-start).count() << std::endl;
    }
    const uint32_t before = ctx->cop0_count;
    (void)runtime->eeScheduler().checkpointDue(32u);
    if (ctx->cop0_count != before + 32u)
        throw std::runtime_error("Count did not advance or wrap with guest cycles");
    ctx->cop0_count = 1000u; // Simulate guest MTC0 Count.
    (void)runtime->eeScheduler().checkpointDue(32u);
    if (ctx->cop0_count != 1032u)
        throw std::runtime_error("Guest Count write was not preserved");
    expected = ctx->cop0_count;
    if (std::getenv("PS2_PORT_HOST_VSYNC_TEST")) std::this_thread::sleep_for(std::chrono::milliseconds(50));
    ctx->pc = 0x00100004u;
}


static unsigned audioCallbackCount = 0;
static std::unordered_set<uint32_t> callbackRecords;
void audioCallback(uint8_t *rdram, R5900Context *ctx, PS2Runtime *) {
    const uint32_t addr = getRegU32(ctx, 5) & PS2_RAM_MASK;
    uint32_t type = 0, data = 0, len = 0;
    std::memcpy(&type, rdram + addr, 4);
    std::memcpy(&data, rdram + addr + 8, 4);
    std::memcpy(&len, rdram + addr + 12, 4);
    if (getRegU32(ctx, 4) != 0x120000u || getRegU32(ctx, 6) != 0x1234u ||
        type != 2u || len != 12u || std::memcmp(rdram + data + 4, "SShd", 4) != 0)
        throw std::runtime_error("Synthetic MPEG callback arguments were corrupted");
    if (addr >= 0x1ff0000u || getRegU32(ctx, 29) >= 0x1ff0000u) throw std::runtime_error("Callback scratch overlaps the main guest stack");
    callbackRecords.insert(addr);
    if (callbackRecords.size() > 20u) throw std::runtime_error("Callback scratch records were not recycled across MPEG resets");
    ++audioCallbackCount;
    ctx->pc = 0;
}
void audioEnd(uint8_t *, R5900Context *ctx, PS2Runtime *runtime) {
    if (!audioCallbackCount) throw std::runtime_error("MPEG audio callback was silently skipped with exhausted guest heap");
    if (audioCallbackCount < 500u) ctx->pc = 0x100000u;
    else runtime->requestStop();
}
void audioStart(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime) {
    runtime->eeScheduler().setupCurrentThread(0x1ff8000u, 0x8000u, 0u);
    runtime->configureGuestHeap(0x200000u, 0x200040u);
    if (runtime->guestMalloc(64u) == 0 || runtime->guestMalloc(32u) != 0)
        throw std::runtime_error("Synthetic exhausted-heap setup failed");
    ps2_stubs::sceMpegInit(rdram, ctx, runtime);
    SET_GPR_U32(ctx, 4, 0x120000u);
    SET_GPR_U32(ctx, 5, 2u);
    SET_GPR_U32(ctx, 6, 0u);
    SET_GPR_U32(ctx, 7, 0x100008u);
    SET_GPR_U32(ctx, 8, 0x1234u);
    ps2_stubs::sceMpegAddStrCallback(rdram, ctx, runtime);
    // Synthetic MPEG-2 private PES containing a fake audio header.
    const uint8_t packet[] = {0,0,1,0xbd,0,15,0x80,0,0,0xa0,0,0,0,'S','S','h','d',0,0,0,0};
    std::memcpy(rdram + 0x130000u, packet, sizeof(packet));
    SET_GPR_U32(ctx, 4, 0x120000u);
    SET_GPR_U32(ctx, 5, 0x130000u);
    SET_GPR_U32(ctx, 6, sizeof(packet));
    for (unsigned n = 0; n < 20u; ++n) {
        SET_GPR_U32(ctx, 4, 0x120000u);
        SET_GPR_U32(ctx, 5, 0x130000u);
        SET_GPR_U32(ctx, 6, sizeof(packet));
        ps2_stubs::sceMpegDemuxPss(rdram, ctx, runtime);
    }
    ctx->pc = 0x100004u;
}

static unsigned graphicsIrqCount = 0;
void graphicsIrq(uint8_t *, R5900Context *ctx, PS2Runtime *) {
    if (getRegU32(ctx,4) != 5u) throw std::runtime_error("Wrong VIF interrupt cause");
    ++graphicsIrqCount;
    ctx->pc = 0;
}
void graphicsEnd(uint8_t *, R5900Context *ctx, PS2Runtime *runtime) {
    if (graphicsIrqCount != 1u) throw std::runtime_error("VIF command interrupt was not delivered exactly once");
    ctx->pc = 0;
    runtime->requestStop();
}
void graphicsStart(uint8_t *, R5900Context *ctx, PS2Runtime *runtime) {
    runtime->eeScheduler().addIrqHandler(false,5u,0x100008u,0,0,0,0);
    runtime->eeScheduler().setIrqCauseEnabled(false,5u,true);
    const uint32_t command = 0x80000000u; // Synthetic NOP with interrupt flag.
    runtime->memory().processVIF1Data(reinterpret_cast<const uint8_t*>(&command),4u);
    ctx->pc = 0x100004u;
}

void ps2PortPublishPadState(const uint8_t *state);
// Synthetic worker contract: FIFO, owned upload bytes, FINISH/readback barriers,
// producer FP state, bounded publication, and exception propagation.
class QueueTestBackend final : public GSRasterBackend {
public:
    std::vector<uint32_t> events;
    bool fail = false;
    bool gateFlush = false, flushEntered = false, releaseFlush = false;
    std::mutex gateMutex;
    std::condition_variable gateCv;
    void Initialize(uint8_t*, uint32_t) override {}
    void Reset() override { events.clear(); }
    void Submit(const GSPrimitiveBatch& b) override {
        if (fail) throw std::runtime_error("synthetic worker failure");
        events.push_back(b.vertices[0].r);
        if (std::fegetround() != FE_DOWNWARD) throw std::runtime_error("Renderer lost producer FP environment");
    }
    void LoadClut(const GSTex0Reg&, const GSTexClutReg&) override { events.push_back(21); }
    void BeginTransfer(const GSTransferCommand&) override { events.push_back(22); }
    void UploadImage(const uint8_t* p, uint32_t n) override { events.push_back(n ? p[0] : 0); }
    void Flush() override {
        events.push_back(24);
        std::unique_lock<std::mutex> lock(gateMutex);
        if (gateFlush) {
            flushEntered = true; gateCv.notify_all();
            gateCv.wait(lock,[&] { return releaseFlush; });
        }
    }
    void TextureFlush() override { events.push_back(25); }
    void Sync(GSSyncReason) override { events.push_back(26); }
    PresentationFrame Present(const GSPresentationRequest&) override {
        events.push_back(27); PresentationFrame result;
        result.pixels.assign(events.begin(),events.end()); return result;
    }
    bool ClearFramebuffer(const GSContext&, uint32_t) override { events.push_back(28); return true; }
    uint32_t ConsumeLocalToHostBytes(uint8_t*, uint32_t) override { return events.size(); }
    uint32_t ReadVram(uint32_t,uint32_t,uint32_t,uint32_t,uint32_t) const override { return events.size(); }
    void WriteVram(uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t v) override { events.push_back(v); }
    void SnapshotVram(std::vector<uint8_t>& out) const override { out.assign(events.begin(), events.end()); }
    GSTransferSnapshot GetTransferSnapshot() const override { GSTransferSnapshot s; s.copiedPixels=events.size(); return s; }
};
static void testRendererQueue() {
    const int savedRound=std::fegetround();
    std::fesetround(FE_DOWNWARD);
    auto inner=std::make_unique<QueueTestBackend>();
    auto* observed=inner.get();
    GSThreadedBackend queue(std::move(inner));
    queue.Initialize(nullptr,0);
    queue.Reset();
    GSPrimitiveBatch draw{}; draw.vertexCount=1; draw.vertices[0].r=20;
    queue.Submit(draw); draw.vertices[0].r=99;
    queue.LoadClut({},{}); queue.BeginTransfer({});
    uint8_t bytes[]={23}; queue.UploadImage(bytes,1); bytes[0]=99;
    queue.Flush(); queue.TextureFlush(); queue.Sync(GSSyncReason::Finish);
    queue.Present({});
    if (!queue.ClearFramebuffer({},0)) throw std::runtime_error("Renderer clear return lost");
    queue.WriteVram(0,0,0,0,0,29);
    if (queue.ReadVram(0,0,0,0,0)!=10 || queue.GetTransferSnapshot().copiedPixels!=10)
        throw std::runtime_error("Renderer readback did not wait for queued commands");
    std::vector<uint8_t> snapshot; queue.SnapshotVram(snapshot);
    if (snapshot != std::vector<uint8_t>({20,21,22,23,24,25,26,27,28,29}))
        throw std::runtime_error("Renderer ordering or command ownership failed");
    queue.WriteVram(0,0,0,0,0,30);
    queue.PresentSynchronized({});
    queue.WriteVram(0,0,0,0,0,31);
    queue.SnapshotVram(snapshot);
    if (snapshot != std::vector<uint8_t>({20,21,22,23,24,25,26,27,28,29,30,24,26,27,31}))
        throw std::runtime_error("Synchronized presentation lost prior writes or reordered flush/sync/snapshot");
    // Enough commands to cross many chunks and exercise bounded back pressure.
    for (unsigned n=0;n<10000;++n) queue.Submit(draw);
    queue.Sync(GSSyncReason::Finish);
    if (observed->events.size()!=10016) throw std::runtime_error("Renderer queue lost commands");
    // A write arriving during Flush must remain after the same presentation's
    // Sync and snapshot, rather than slipping through a separate drain barrier.
    observed->gateFlush = true;
    PresentationFrame frame;
    std::exception_ptr presenterFailure;
    std::thread presenter([&] {
        try { frame = queue.PresentSynchronized({}); }
        catch (...) { presenterFailure = std::current_exception(); }
    });
    {
        std::unique_lock<std::mutex> lock(observed->gateMutex);
        observed->gateCv.wait(lock,[&] { return observed->flushEntered; });
    }
    queue.WriteVram(0,0,0,0,0,32);
    {
        std::lock_guard<std::mutex> lock(observed->gateMutex);
        observed->releaseFlush = true;
    }
    observed->gateCv.notify_all();
    presenter.join();
    if (presenterFailure) std::rethrow_exception(presenterFailure);
    if (frame.pixels.size()!=10019 || frame.pixels.back()!=27)
        throw std::runtime_error("Concurrent producer changed synchronized presentation snapshot");
    queue.SnapshotVram(snapshot);
    if (snapshot.size()!=10020 || snapshot.back()!=32)
        throw std::runtime_error("Presentation lost the following concurrent write");
    std::fesetround(savedRound);
    auto failing=std::make_unique<QueueTestBackend>(); failing->fail=true;
    GSThreadedBackend failureQueue(std::move(failing));
    failureQueue.Submit(draw);
    bool caught=false;
    try { failureQueue.Flush(); } catch (const std::runtime_error& e) { caught=std::string(e.what())=="synthetic worker failure"; }
    if (!caught) throw std::runtime_error("Renderer worker failure was not propagated");
    std::cout << "PASS: renderer queue ordering, owned payloads, barriers, FP state, back pressure and failures" << std::endl;
}

static void testLocalPadCommands() {
    const auto directory=std::filesystem::temp_directory_path()/
        ("ps2-pad-command-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directory(directory); const auto path=directory/"command.txt";
    const auto oldHeadless=std::getenv("PS2_PORT_HEADLESS");
    const bool hadHeadless=oldHeadless; const std::string savedHeadless=oldHeadless?oldHeadless:"";
    const auto oldCommand=std::getenv("PS2_PORT_PAD_COMMAND");
    const bool hadCommand=oldCommand; const std::string savedCommand=oldCommand?oldCommand:"";
    setenv("PS2_PORT_PAD_COMMAND",path.c_str(),1); unsetenv("PS2_PORT_HEADLESS");
    uint8_t neutral[32]{};neutral[0]=1;neutral[1]=0x73;neutral[2]=neutral[3]=255;
    neutral[4]=neutral[5]=neutral[6]=neutral[7]=128;
    uint8_t read[32]; PSPadBackend pad;
    {std::ofstream f(path);f<<"1 16384 1000 128 0\n";}
    ps2PortPublishPadState(neutral);pad.readState(0,0,read,sizeof(read));
    if (read[2]!=255 || read[3]!=255 || read[7]!=128) throw std::runtime_error("File commands changed normal keyboard mode");
    setenv("PS2_PORT_HEADLESS","1",1);
    ps2PortPublishPadState(neutral);pad.readState(0,0,read,sizeof(read));
    if ((read[3]&64)!=0 || read[7]!=0) throw std::runtime_error("Headless command lost button or analog input");
    {std::ofstream f(path);f<<"2 4096 10001 128 255\n";}
    ps2PortPublishPadState(neutral);pad.readState(0,0,read,sizeof(read));
    if ((read[3]&64)!=0 || (read[3]&16)==0 || read[7]!=0) throw std::runtime_error("Invalid hold command was accepted");
    {std::ofstream f(path);f<<"1 4096 1000 128 255\n";}
    ps2PortPublishPadState(neutral);pad.readState(0,0,read,sizeof(read));
    if ((read[3]&16)==0 || read[7]!=0) throw std::runtime_error("Stale command was replayed");
    {std::ofstream f(path);f<<"2 0 1 128 128\n";}
    ps2PortPublishPadState(neutral);pad.readState(0,0,read,sizeof(read));
    if (read[3]!=255 || read[7]!=128) throw std::runtime_error("Explicit release command failed");
    if(hadHeadless) setenv("PS2_PORT_HEADLESS",savedHeadless.c_str(),1); else unsetenv("PS2_PORT_HEADLESS");
    if(hadCommand) setenv("PS2_PORT_PAD_COMMAND",savedCommand.c_str(),1); else unsetenv("PS2_PORT_PAD_COMMAND");
    std::filesystem::remove_all(directory);
    std::cout<<"PASS: local pad commands are headless-only, bounded, sequenced and preserve analog input"<<std::endl;
}

// Hardware behavior reference: PCSXX2 GSRendererHW.cpp, 24-bit DATE guard.
static void testDestinationAlphaFormats() {
    std::vector<uint8_t> vram(4u * 1024u * 1024u);
    GSCpuBackend backend;
    backend.Initialize(vram.data(), static_cast<uint32_t>(vram.size()));
    for (uint8_t psm : {uint8_t(0), uint8_t(1), uint8_t(2), uint8_t(10)}) {
        for (unsigned alphaBit : {0u, 1u}) {
            for (unsigned datm : {0u, 1u}) {
                GSPrimitiveBatch point{};
                point.vertexCount = 1;
                point.state.prim.type = GS_PRIM_POINT;
                auto& ctx = point.state.context;
                ctx.frame.fbp = 32; ctx.frame.fbw = 1; ctx.frame.psm = psm;
                ctx.zbuf.zbp = 64; ctx.zbuf.psm = 0x30; ctx.zbuf.zmask = false;
                ctx.test = (1ull << 14) | (uint64_t(datm) << 15) | (1ull << 16) | (1ull << 17);
                ctx.scissor.x0 = ctx.scissor.y0 = ctx.scissor.x1 = ctx.scissor.y1 = 0;
                const uint32_t original = (psm == 2 || psm == 10)
                    ? (0x1fu | (alphaBit << 15)) : (0xffu | ((psm == 1 ? 0u : alphaBit) << 31));
                backend.WriteVram(psm, 32u * 32u, 1, 0, 0, original);
                backend.WriteVram(0x30, 64u * 32u, 1, 0, 0, 10);
                auto& v = point.vertices[0];
                v.r = 0; v.g = 248; v.b = 0; v.a = 128; v.z = 20;
                backend.Submit(point);
                const bool pass = psm != 1 && alphaBit == datm;
                const uint32_t expected = pass ? ((psm == 2 || psm == 10) ? 0x83e0u : 0x8000f800u) : original;
                if (backend.ReadVram(psm, 32u * 32u, 1, 0, 0) != expected ||
                    backend.ReadVram(0x30, 64u * 32u, 1, 0, 0) != (pass ? 20u : 10u))
                    throw std::runtime_error("Destination alpha changed color/depth incorrectly: PSM=" + std::to_string(psm) + ", alpha=" + std::to_string(alphaBit) + ", DATM=" + std::to_string(datm));
            }
        }
    }
    // With DATE disabled, RGB24 must still draw and preserve the alpha lane.
    GSPrimitiveBatch point{};
    point.vertexCount = 1; point.state.prim.type = GS_PRIM_POINT;
    auto& ctx = point.state.context;
    ctx.frame.fbp = 32; ctx.frame.fbw = 1; ctx.frame.psm = 1;
    ctx.zbuf.zmask = true;
    ctx.scissor.x0 = ctx.scissor.y0 = ctx.scissor.x1 = ctx.scissor.y1 = 0;
    backend.WriteVram(0, 32u * 32u, 1, 0, 0, 0xa50000ffu);
    point.vertices[0].g = 248; point.vertices[0].a = 128;
    backend.Submit(point);
    if (backend.ReadVram(0, 32u * 32u, 1, 0, 0) != 0xa500f800u)
        throw std::runtime_error("RGB24 without DATE failed to draw or preserve alpha");
    std::cout << "PASS: RGB24 DATE rejects color and depth; disabled DATE draws; RGBA32/16 obeys DATM" << std::endl;
}

// Bilinear feedback goldens exercise both retained-page reuse and page
// alternation. Framebuffer writes deliberately alias the sampled texture.
static void testBilinearFeedbackQuads() {
    std::vector<uint8_t> vram(4u * 1024u * 1024u);
    GSCpuBackend backend;
    backend.Initialize(vram.data(), static_cast<uint32_t>(vram.size()));
    GSPrimitiveBatch sprite{};
    sprite.vertexCount = 2;
    auto& state = sprite.state;
    state.prim.type = GS_PRIM_SPRITE; state.prim.tme = true; state.prim.fst = true;
    state.linearFilter = true; state.textureWidth = 128; state.textureHeight = 64;
    auto& ctx = state.context;
    ctx.frame.fbp = 32; ctx.frame.fbw = 2; ctx.frame.psm = GS_PSM_CT32;
    ctx.zbuf.zmask = true; ctx.scissor.x1 = 127; ctx.scissor.y1 = 63;
    ctx.tex0.tbp0 = 32u * 32u; ctx.tex0.tbw = 2; ctx.tex0.psm = GS_PSM_CT32;
    ctx.tex0.tw = 7; ctx.tex0.th = 6; ctx.tex0.tfx = 1; ctx.tex0.tcc = 1;
    ctx.clamp = 5; // clamp both coordinates to the texture's edges
    auto& a = sprite.vertices[0]; auto& b = sprite.vertices[1];
    b.x = 4; b.y = 1; b.u = 32;
    for (unsigned x = 0; x < 4; ++x)
        backend.WriteVram(GS_PSM_CT32, 32u * 32u, 2, x, 0, 0x80000000u | (x * 80u));
    backend.TextureFlush(); backend.Submit(sprite);
    const unsigned expected[] = {0, 20, 60, 100};
    for (unsigned x = 0; x < 4; ++x)
        if (backend.ReadVram(GS_PSM_CT32, 32u * 32u, 2, x, 0) != (0x80000000u | expected[x]))
            throw std::runtime_error("Bilinear same-page feedback lost retained texture data");

    // Keep the same cached page/quad but change its alpha interpretation on
    // a new primitive. TEXA changes decoded RGB24 alpha without a TEXFLUSH.
    a.x = 0; b.x = 1; a.u = b.u = 32;
    ctx.tex0.psm = GS_PSM_CT24; state.texa.ta0 = 17;
    backend.Submit(sprite);
    if (backend.ReadVram(GS_PSM_CT32, 32u * 32u, 2, 0, 0) != 0x11000078u)
        throw std::runtime_error("Bilinear quad ignored new primitive TEXA state");
    ctx.tex0.psm = GS_PSM_CT32;

    // Every quad crosses the 64-pixel physical page boundary. A write to
    // x=63 must be seen when the next pixel reloads that page; caching the
    // first quad's colors across the eviction would incorrectly reuse 120.
    a.x = 63; b.x = 65; a.u = b.u = 64u * 16u;
    backend.WriteVram(GS_PSM_CT32, 32u * 32u, 2, 63, 0, 0x80000028u);
    backend.WriteVram(GS_PSM_CT32, 32u * 32u, 2, 64, 0, 0x800000c8u);
    backend.TextureFlush(); backend.Submit(sprite);
    if (backend.ReadVram(GS_PSM_CT32, 32u * 32u, 2, 63, 0) != 0x80000078u ||
        backend.ReadVram(GS_PSM_CT32, 32u * 32u, 2, 64, 0) != 0x800000a0u)
        throw std::runtime_error("Bilinear alternating-page feedback omitted ordered reloads");

    // The next Submit may change texture data and state while keeping the
    // same source quad. A memo must not survive the primitive boundary.
    backend.WriteVram(GS_PSM_CT32, 32u * 32u, 2, 63, 0, 0x80000014u);
    backend.WriteVram(GS_PSM_CT32, 32u * 32u, 2, 64, 0, 0x80000064u);
    a.x = 0; b.x = 1;
    backend.TextureFlush(); backend.Submit(sprite);
    if (backend.ReadVram(GS_PSM_CT32, 32u * 32u, 2, 0, 0) != 0x8000003cu)
        throw std::runtime_error("Bilinear quad reused data from a preceding Submit");
    std::cout << "PASS: bilinear retained-page feedback, ordered page reloads and primitive boundaries" << std::endl;
}

static void testTrianglePixelCenters() {
    std::vector<uint8_t> memory(4u*1024u*1024u, 0u);
    GS gs; gs.init(memory.data(), memory.size(), nullptr);
    gs.writeRegister(0x1au,1u);
    gs.writeRegister(0x4cu,1ull<<16u);
    gs.writeRegister(0x18u,0u);
    gs.writeRegister(0x40u,(63ull<<16u)|(63ull<<48u));
    gs.writeRegister(0x47u,0u);
    gs.writeRegister(0x4eu,1ull<<32u);
    gs.writeRegister(0u,3u);
    gs.writeRegister(1u,0x3f80000080112233ull);
    // Fractional vertices avoid edge ties. GS pixel centers are integer XY.
    // Independent expected interior: x>10.25, y>10.25, x+y<25.5.
    for (const auto &xy : {std::pair{164u,164u},std::pair{244u,164u},std::pair{164u,244u}})
        gs.writeRegister(5u,xy.first|(uint64_t(xy.second)<<16u)|(1ull<<32u));
    gs.latchHostPresentationFrame();
    unsigned covered=0;
    for (unsigned y=0;y<64;y++) for (unsigned x=0;x<64;x++) {
        const bool inside=x>=11 && y>=11 && x+y<=25;
        const uint32_t expectedPixel=inside?0x80112233u:0u;
        if (gs.ReadVram(0u,0u,1u,x,y)!=expectedPixel)
            throw std::runtime_error("Triangle does not sample integer GS pixel centers");
        covered+=inside;
    }
    if(covered!=10) throw std::runtime_error("Invalid triangle coverage fixture");
    std::cout<<"PASS: fractional triangle covers exactly ten integer pixel centers"<<std::endl;
}

static void testGsParameterABI() {
 setenv("PS2_PORT_HEADLESS","1",1);PS2Runtime runtime;if(!runtime.initialize("Synthetic SCE GS parameter ABI"))throw std::runtime_error("Synthetic GS ABI initialization failed");
 for(unsigned field:{0u,1u}){
  R5900Context ctx{};SET_GPR_U32(&ctx,4,0);SET_GPR_U32(&ctx,5,1);SET_GPR_U32(&ctx,6,2);SET_GPR_U32(&ctx,7,field);
  ps2_stubs::sceGsResetGraph(runtime.memory().getRDRAM(),&ctx,&runtime);
  ps2_stubs::sceGsGetGParam(runtime.memory().getRDRAM(),&ctx,&runtime);
  uint32_t address=getRegU32(&ctx,2);if(address<PS2_SCRATCHPAD_BASE || address>PS2_SCRATCHPAD_BASE+PS2_SCRATCHPAD_SIZE-16)throw std::runtime_error("Bad GS parameter address");
  auto *ptr=runtime.memory().getScratchpad()+address-PS2_SCRATCHPAD_BASE;uint16_t values[4];std::memcpy(values,ptr,8);
  std::cout<<"field="<<field<<" parameters="<<values[0]<<","<<values[1]<<","<<values[2]<<","<<values[3]<<"\n";
  if(values[0]!=1 || values[1]!=2 || values[2]!=field || values[3]!=3)throw std::runtime_error("SCE GS parameters must use four 16-bit fields at offsets 0,2,4,6");
  uint32_t sentinels[2]={0x12345678,0x23456789};std::memcpy(ptr+8,sentinels,8);
  ps2_stubs::sceGsGetGParam(runtime.memory().getRDRAM(),&ctx,&runtime);
  if(std::memcmp(ptr+8,sentinels,8))throw std::runtime_error("GS parameter query overwrote guest-owned callback state");
 }
 std::cout<<"PASS: SCE halfword fields and guest-owned trailing state preserved\n";

}

static void testInterlacedPresentationRows() {

 std::vector<uint8_t> vram(4*1024*1024);GSCpuBackend backend;backend.Initialize(vram.data(),vram.size());
 for(unsigned n=0;n<32;n++) {
  const bool frameMode=n>=16;const unsigned height=frameMode?224:448;
  auto color=[&](unsigned y){return uint32_t(uint8_t(19+n*7+y*31))|(uint32_t(uint8_t(37+n*11+y*47))<<8)|(uint32_t(uint8_t(53+n*13+y*59))<<16)|0x80000000u;};
  for(unsigned y=0;y<448;y++)for(unsigned x=0;x<512;x++)backend.WriteVram(0,0,8,x,y,y<height?color(y):0u);
  GSPresentationRequest req{};req.pmode=0x66;req.smode2=1u|(frameMode?2u:0u);req.vsyncTick=n%2;req.dispfb2=8ull<<9;
  req.display2=636ull|(50ull<<12)|(4ull<<23)|(2559ull<<32)|(447ull<<44);
  auto image=backend.Present(req);if(image.width!=512||image.height!=448)throw std::runtime_error("Unexpected CPU bob output size");
  for(unsigned y=0;y<448;y++)for(unsigned x=0;x<512;x++) {
   const unsigned source=frameMode?y/2:2*(y/2)+(n%2);const uint32_t expected=color(source);const size_t at=(y*640+x)*4;
   for(unsigned c=0;c<3;c++)if(image.pixels[at+c]!=uint8_t(expected>>(c*8))) {
    std::cout<<"Mismatch field="<<n<<" FFMD="<<frameMode<<" phase="<<n%2<<" x="<<x<<" y="<<y<<" source_row="<<source<<"\n";
    throw std::runtime_error("CPU bob presentation differs from independently qualified GPU raw field rows");
   }
  }
 }
 std::cout<<"PASS: 32 CPU bob frames preserve GPU-qualified consecutive/even/odd source rows\n";

}

static void testGsDisplayEnvironment() {

 setenv("PS2_PORT_HEADLESS","1",1);PS2Runtime runtime;if(!runtime.initialize("Synthetic SCE display environment"))throw std::runtime_error("Synthetic CRT initialization failed");
 for(unsigned mode: {2u,3u}) for(unsigned interlace: {0u,1u}) for(unsigned field: {0u,1u}) for(unsigned width: {320u,512u,640u}) {
  R5900Context ctx{};auto *ram=runtime.memory().getRDRAM();
  SET_GPR_U32(&ctx,4,0);SET_GPR_U32(&ctx,5,interlace);SET_GPR_U32(&ctx,6,mode);SET_GPR_U32(&ctx,7,field);
  ps2_stubs::sceGsResetGraph(ram,&ctx,&runtime);
  SET_GPR_U32(&ctx,4,0x130000);SET_GPR_U32(&ctx,5,0);SET_GPR_U32(&ctx,6,width);SET_GPR_U32(&ctx,7,224);
  SET_GPR_U32(&ctx,8,0);SET_GPR_U32(&ctx,9,0);SET_GPR_U32(&ctx,29,0x140000);
  uint32_t garbage[2]={0xdead,0xbeef};std::memcpy(ram+0x140010,garbage,8);std::memset(ram+0x130000,0xa5,40);
  ps2_stubs::sceGsSetDefDispEnv(ram,&ctx,&runtime);
  uint64_t env[5];std::memcpy(env,ram+0x130000,40);const unsigned scale=2560/width;
  const unsigned dx=env[3]&4095,dy=(env[3]>>12)&2047,magh=(env[3]>>23)&15,dw=(env[3]>>32)&4095,dh=(env[3]>>44)&2047;
  std::cout<<"mode="<<mode<<" interlace="<<interlace<<" field="<<field<<" width="<<width<<" PMODE="<<env[0]<<" SMODE2="<<env[1]<<" DISPLAY="<<dx<<","<<dy<<","<<magh<<","<<dw<<","<<dh<<"\n";
  if(env[0]!=0x66 || env[1]!=(interlace?(field?3u:1u):2u) || env[2]!=((width+63)/64<<9) || env[4]!=0 || dx!=(mode==2?636u:656u) || dy!=(mode==2?25u:36u)*(interlace?2u:1u) || magh!=scale-1 || dw!=scale*width-1 || dh!=224*(interlace&&field?2u:1u)-1)
   throw std::runtime_error("SCE display environment failed analog geometry, field/frame, or zero register-argument contract");
 }
 std::cout<<"PASS: 24 analog display-environment cases; zero arguments ignore unrelated stack data\n";

}

static void testGsDoubleBufferGeometry() {
 setenv("PS2_PORT_HEADLESS","1",1);PS2Runtime runtime;
 if(!runtime.initialize("Synthetic double-buffer display geometry"))throw std::runtime_error("Synthetic DBuff initialization failed");
 struct Case {unsigned mode,interlace,frame,width,height;uint64_t display;};
 const Case cases[]={
  {2,1,1,640,224,0x1bf9ff0183227cull},
  {2,1,0,512,448,0x1bf9ff0203227cull},
  {2,0,1,640,224,0xdf9ff0181927cull},
  {3,1,1,640,256,0x1ff9ff01848290ull}};
 auto *ram=runtime.memory().getRDRAM();
 for(const auto &c:cases)for(unsigned dual:{0u,1u}) {
  R5900Context ctx{};SET_GPR_U32(&ctx,4,0);SET_GPR_U32(&ctx,5,c.interlace);SET_GPR_U32(&ctx,6,c.mode);SET_GPR_U32(&ctx,7,c.frame);
  ps2_stubs::sceGsResetGraph(ram,&ctx,&runtime);
  SET_GPR_U32(&ctx,4,0x130000);SET_GPR_U32(&ctx,5,0);SET_GPR_U32(&ctx,6,c.width);SET_GPR_U32(&ctx,7,c.height);
  SET_GPR_U32(&ctx,8,0);SET_GPR_U32(&ctx,9,0);SET_GPR_U32(&ctx,10,0);SET_GPR_U32(&ctx,29,0x140000);
  std::memset(ram+0x140000,0,64);std::memset(ram+0x130000,0xa5,0x400);
  if(dual)ps2_stubs::sceGsSetDefDBuffDc(ram,&ctx,&runtime);else ps2_stubs::sceGsSetDefDBuff(ram,&ctx,&runtime);
  for(unsigned page=0;page<2;page++) {
   uint64_t display;std::memcpy(&display,ram+0x130000+page*40+24,8);
   if(display!=c.display)throw std::runtime_error("Double-buffer helper emitted placeholder analog display geometry");
   uint64_t pmode,smode2;std::memcpy(&pmode,ram+0x130000+page*40,8);std::memcpy(&smode2,ram+0x130000+page*40+8,8);
   if(pmode!=0x66 || smode2!=(c.interlace?(c.frame?3u:1u):2u))throw std::runtime_error("Double-buffer helper differs from owned SDK PMODE/SMODE2 initialization");
  }
 }
 std::cout<<"PASS: 16 double-buffer display pages preserve NTSC/PAL movie and field geometry\n";
}

static void testGsAnalogCRT() {

 setenv("PS2_PORT_HEADLESS","1",1);
 PS2Runtime runtime;if(!runtime.initialize("Synthetic CRT initialization contract"))throw std::runtime_error("Synthetic CRT initialization failed");
 for(unsigned field: {0u,1u}){
  runtime.memory().gs().smode2=field?1u:3u;
  R5900Context ctx{};SET_GPR_U32(&ctx,4,0);SET_GPR_U32(&ctx,5,1);SET_GPR_U32(&ctx,6,2);SET_GPR_U32(&ctx,7,field);
  ps2_stubs::sceGsResetGraph(runtime.memory().getRDRAM(),&ctx,&runtime);
  const auto &regs=runtime.memory().gs();
  const unsigned lc=(regs.smode1>>3)&127, cmod=(regs.smode1>>13)&3;
  std::cout<<"field="<<field<<" SMODE1="<<regs.smode1<<" SMODE2="<<regs.smode2<<" LC="<<lc<<" CMOD="<<cmod<<"\n";
  if(regs.smode2!=(1u|(field<<1)) || lc!=32 || cmod!=2)
   throw std::runtime_error("SDK ResetGraph failed to initialize requested interlaced NTSC mode");
 }
 std::cout<<"PASS: requested field/frame modes and NTSC clock initialized\n";

}

int main() {
    testInterlacedPresentationRows();
    testTrianglePixelCenters();
    testBilinearFeedbackQuads();
    testDestinationAlphaFormats();
    // Explicit app paths must initialize before memory-card reset without
    // querying the process's working directory (which can block at startup).
    const auto testCwd = std::filesystem::current_path();
    const auto configuredRoot = testCwd / "synthetic-explicit-io-root";
    setenv("PS2_PORT_IO_ROOT", configuredRoot.string().c_str(), 1);
    const auto paths = PS2Runtime::getIoPaths();
    if (paths.elfDirectory != configuredRoot || paths.hostRoot != configuredRoot ||
        paths.cdRoot != configuredRoot || paths.mcRoot != configuredRoot / "mc0")
        throw std::runtime_error("Explicit I/O root was not applied before runtime initialization");
    unsetenv("PS2_PORT_IO_ROOT");
    PS2Runtime::IoPaths restoredPaths;
    restoredPaths.elfDirectory = testCwd;
    PS2Runtime::setIoPaths(restoredPaths);
    testGsParameterABI();
    testGsDisplayEnvironment();
    testGsAnalogCRT();
    testGsDoubleBufferGeometry();
    testLocalPadCommands();
    testRendererQueue();
    // Instruction semantics identified while comparing the SotC runtime fork.
    // These independent boundary cases fail with host rounding and unaligned LQ/SQ.
    if (FPU_CVT_W_S(1.75f) != 1 || FPU_CVT_W_S(-1.75f) != -1 ||
        FPU_CVT_W_S(2147483648.0f) != INT32_MAX || FPU_CVT_W_S(-2147483648.0f) != INT32_MIN)
        throw std::runtime_error("PS2 CVT.W.S must truncate and saturate, independent of host rounding");
    {
        alignas(16) uint8_t memory[64];
        for (unsigned n = 0; n < sizeof(memory); ++n) memory[n] = static_cast<uint8_t>(n);
        alignas(16) uint8_t loaded[16];
        _mm_storeu_si128(reinterpret_cast<__m128i*>(loaded), Ps2FastRead128(memory, 23));
        if (std::memcmp(loaded, memory + 16, 16) != 0)
            throw std::runtime_error("PS2 LQ must ignore the low four address bits");
        Ps2FastWrite128(memory, 23, _mm_set1_epi32(0x11223344));
        uint32_t first;
        std::memcpy(&first, memory + 16, 4);
        if (first != 0x11223344 || memory[15] != 15 || memory[32] != 32)
            throw std::runtime_error("PS2 SQ must write exactly the aligned quadword");
        PS2Runtime *runtime = nullptr; // These addresses take only the direct RAM path.
        R5900Context context{};
        R5900Context *ctx = &context;
        uint8_t *rdram = memory;
        WRITE8(23, 0xab);
        if (memory[23] != 0xab || memory[16] != 0x44)
            throw std::runtime_error("Byte writes must retain all address bits");
        WRITE128(23, _mm_set1_epi32(0x55667788));
        _mm_storeu_si128(reinterpret_cast<__m128i*>(loaded), READ128(23));
        std::memcpy(&first, loaded, 4);
        if (first != 0x55667788 || memory[32] != 32)
            throw std::runtime_error("Quadword macros must align both reads and writes");
    }
    std::cout << "PASS: PS2 scalar conversion and quadword alignment boundary cases" << std::endl;
    PSPadBackend pad;
    uint8_t keys[32]{}; keys[2]=keys[3]=255;
    keys[4]=keys[5]=keys[6]=keys[7]=128;
    ps2PortPublishPadState(keys);
    keys[2] &= ~8u; // Press Start between two guest reads.
    ps2PortPublishPadState(keys);
    keys[2] = 255;
    ps2PortPublishPadState(keys); // Release before the guest polls.
    uint8_t observed[32]{};
    if (!pad.readState(0,0,observed,32) || (observed[2] & 8u)) return 4;
    if (!pad.readState(0,0,observed,32) || !(observed[2] & 8u)) return 5;
    keys[6]=255; keys[7]=1; ps2PortPublishPadState(keys);
    if (!pad.readState(0,0,observed,32) || observed[6]!=255 || observed[7]!=1) return 6;
    std::cout << "PASS: quick Start press is delivered once; analog state survives polling" << std::endl;

    setenv("PS2_PORT_HEADLESS", "1", 1);
    // The guest heap must reach the reclaimed RAM while callback stacks stay
    // in their reserved arena even when every guest heap byte is occupied.
    {
        PS2Runtime heapRuntime;
        if (!heapRuntime.initialize("Synthetic heap boundary test")) return 2;
        heapRuntime.configureGuestHeap(0x100000u,0x2000000u);
        const uint32_t base = heapRuntime.guestHeapBase();
        const uint32_t limit = heapRuntime.guestHeapLimit();
        const uint32_t block = heapRuntime.guestMalloc(limit-base,16u);
        if (limit <= 0x1f00000u || block != base || heapRuntime.guestMalloc(16u) != 0u)
            throw std::runtime_error("Guest heap lost reclaimed RAM or crossed the callback boundary");
        const uint32_t stack = heapRuntime.reserveAsyncCallbackStack(0x4000u,16u);
        if (!stack || stack + 16u > base || stack + 16u - 0x4000u < 0x80000u)
            throw std::runtime_error("Callback stack overlaps a fully occupied guest heap");
        // A minimal synthetic MIPS ELF must not overwrite callback storage.
        uint8_t elf[88]{};
        auto word = [&](size_t at, uint32_t value) { std::memcpy(elf+at,&value,4u); };
        auto half = [&](size_t at, uint16_t value) { std::memcpy(elf+at,&value,2u); };
        word(0,0x464c457fu); elf[4]=1; elf[5]=1; elf[6]=1;
        half(16,2); half(18,8); word(20,1); word(24,0x80000u);
        word(28,52); half(40,52); half(42,32); half(44,1);
        word(52,1); word(56,84); word(60,0x80000u); word(64,0x80000u);
        word(68,4); word(72,4); word(76,5); word(80,16);
        word(84,0x12345678u);
        const auto fixture = std::filesystem::temp_directory_path() /
            ("ps2-port-callback-boundary-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count())+".elf");
        { std::ofstream file(fixture,std::ios::binary); file.write(reinterpret_cast<const char*>(elf),sizeof(elf)); }
        uint8_t *heapRam = heapRuntime.memory().getRDRAM();
        std::memset(heapRam+0x80000u,0xabu,4u);
        const bool accepted = heapRuntime.loadELF(fixture.string());
        std::filesystem::remove(fixture);
        if (accepted || heapRam[0x80000u] != 0xabu)
            throw std::runtime_error("ELF loader overwrote the reserved callback arena");
        heapRuntime.guestFree(block);
        if (heapRuntime.guestMalloc(limit-base,16u) != base)
            throw std::runtime_error("Guest heap did not recover its full allocation after free");
        std::cout << "PASS: reclaimed guest heap RAM remains separate from callback stacks" << std::endl;
    }
    PS2Runtime runtime;
    if (!runtime.initialize("Synthetic timer test")) return 2;
    // An ELF starts after platform initialization; environment setup must not
    // silently turn the initially runnable DMA controller off.
    uint8_t *dmaRam = runtime.memory().getRDRAM();
    std::memset(dmaRam+0x180000u,0,20u);
    auto &dmaCtx = runtime.cpu(); SET_GPR_U32(&dmaCtx,4,0x180000u);
    ps2_stubs::sceDmaPutEnv(dmaRam,&dmaCtx,&runtime);
    const uint64_t scratchWords[2] = {0x0123456789abcdefull,0xfedcba9876543210ull};
    std::memcpy(runtime.memory().getScratchpad(),scratchWords,16u);
    auto &dmaMemory = runtime.memory();
    dmaMemory.writeIORegister(0x1000d010u,0x180040u);
    dmaMemory.writeIORegister(0x1000d020u,1u);
    dmaMemory.writeIORegister(0x1000d080u,0u);
    dmaMemory.writeIORegister(0x1000d000u,0x101u);
    if (std::memcmp(dmaRam+0x180040u,scratchWords,16u)!=0)
        throw std::runtime_error("Initial DMA environment dropped the scratchpad transfer");
    // Explicit guest disabling must remain effective.
    dmaMemory.writeIORegister(0x1000e000u,0u);
    dmaMemory.writeIORegister(0x1000d010u,0x180080u);
    dmaMemory.writeIORegister(0x1000d020u,1u);
    dmaMemory.writeIORegister(0x1000d000u,0x101u);
    if (std::memcmp(dmaRam+0x180080u,scratchWords,16u)==0)
        throw std::runtime_error("Explicitly disabled DMA controller still transferred data");
    dmaMemory.writeIORegister(0x1000e000u,1u);
    (void)dmaMemory.consumeCompletedDmacCauses();
    std::cout << "PASS: initial DMA environment preserves runnable transfers; explicit disabling is honored" << std::endl;
    // Column-major composition must equal applying the right factor first.
    // Noncommuting scale and translation expose a reversed product order.
    uint8_t *ram = runtime.memory().getRDRAM();
    const float scale[16] = {2,0,0,0, 0,3,0,0, 0,0,4,0, 0,0,0,1};
    const float translate[16] = {1,0,0,0, 0,1,0,0, 0,0,1,0, 5,6,7,1};
    const float vertex[4] = {1,2,3,1};
    std::memcpy(ram+0x140000u,scale,sizeof(scale));
    std::memcpy(ram+0x140040u,translate,sizeof(translate));
    std::memcpy(ram+0x140080u,vertex,sizeof(vertex));
    auto &matrixCtx = runtime.cpu();
    SET_GPR_U32(&matrixCtx,4,0x140000u); // In-place product.
    SET_GPR_U32(&matrixCtx,5,0x140000u);
    SET_GPR_U32(&matrixCtx,6,0x140040u);
    ps2_stubs::sceVu0MulMatrix(ram,&matrixCtx,&runtime);
    SET_GPR_U32(&matrixCtx,4,0x140080u); // In-place application.
    SET_GPR_U32(&matrixCtx,5,0x140000u);
    SET_GPR_U32(&matrixCtx,6,0x140080u);
    ps2_stubs::sceVu0ApplyMatrix(ram,&matrixCtx,&runtime);
    float transformed[4]; std::memcpy(transformed,ram+0x140080u,sizeof(transformed));
    if (transformed[0]!=12 || transformed[1]!=24 || transformed[2]!=40 || transformed[3]!=1)
        throw std::runtime_error("VU matrix composition reversed its factors");
    std::cout << "PASS: VU matrix composition preserves column-major factor order and in-place operands" << std::endl;
    // The SDK inner product is XYZ only; plane distance is handled by callers.
    float dotLhs[4] = {2.0f, -3.0f, 4.0f, 1000.0f};
    float dotRhs[4] = {5.0f, 6.0f, 7.0f, -2000.0f};
    std::memcpy(ram+0x140100u,dotLhs,sizeof(dotLhs));
    std::memcpy(ram+0x140110u,dotRhs,sizeof(dotRhs));
    SET_GPR_U32(&matrixCtx,4,0x140100u); SET_GPR_U32(&matrixCtx,5,0x140110u);
    ps2_stubs::sceVu0InnerProduct(ram,&matrixCtx,&runtime);
    if (matrixCtx.f[0] != 20.0f) throw std::runtime_error("SDK inner product included homogeneous W");
    uint32_t dotBits=0;std::memcpy(&dotBits,&matrixCtx.f[0],4u);
    if (getRegU32(&matrixCtx,2)!=dotBits) throw std::runtime_error("SDK inner product return registers disagree");
    dotLhs[3]=std::numeric_limits<float>::quiet_NaN();
    std::memcpy(ram+0x140100u,dotLhs,sizeof(dotLhs));
    ps2_stubs::sceVu0InnerProduct(ram,&matrixCtx,&runtime);
    if (matrixCtx.f[0] != 20.0f) throw std::runtime_error("Unused W contaminated SDK inner product");
    std::cout << "PASS: SDK inner product excludes W and returns XYZ through the float ABI" << std::endl;

    if (std::getenv("PS2_PORT_GS_TRACE")) {
        if (runtime.gs().isDebugHistoryPaused()) throw std::runtime_error("Opt-in GS history remained paused");
        runtime.gs().writeRegister(0u, 0u);
        if (runtime.gs().getDebugHistory().empty()) throw std::runtime_error("Opt-in GS trace did not record a synthetic register write");
        std::cout << "PASS: opt-in GS history records synthetic rendering state" << std::endl;
    }
    // Synthetic VIF packets check expansion independently of retail assets.
    auto unpack = [&](uint8_t format, uint16_t flags, const std::vector<uint8_t> &payload) {
        const uint32_t cycle = 0x01000101u;
        runtime.memory().processVIF1Data(reinterpret_cast<const uint8_t *>(&cycle), 4u);
        std::vector<uint8_t> packet(4u + ((payload.size() + 3u) & ~3u), 0u);
        const uint32_t command = (uint32_t(format) << 24u) | (1u << 16u) | flags;
        std::memcpy(packet.data(), &command, 4u);
        std::memcpy(packet.data() + 4u, payload.data(), payload.size());
        std::memset(runtime.memory().getVU1Data(), 0xa5, 16u);
        runtime.memory().processVIF1Data(packet.data(), uint32_t(packet.size()));
        std::array<uint32_t, 4> lanes;
        std::memcpy(lanes.data(), runtime.memory().getVU1Data(), 16u);
        return lanes;
    };
    if (unpack(0x66u, 0u, {0xffu, 0x7fu}) != std::array<uint32_t,4>{0xffffffffu,127u,0xffffffffu,127u})
        throw std::runtime_error("VIF V2-8 failed signed XYXY expansion");
    if (unpack(0x65u, 0x4000u, {0xffu,0xffu,0x34u,0x12u}) != std::array<uint32_t,4>{65535u,4660u,65535u,4660u})
        throw std::runtime_error("VIF V2-16 failed unsigned XYXY expansion");
    if (unpack(0x64u, 0u, {1u,0u,0u,0u,2u,0u,0u,0u}) != std::array<uint32_t,4>{1u,2u,1u,2u})
        throw std::runtime_error("VIF V2-32 failed XYXY expansion");
    if (unpack(0x6fu, 0u, {0x1fu,0x80u}) != std::array<uint32_t,4>{248u,0u,0u,128u})
        throw std::runtime_error("VIF V4-5 failed color/alpha expansion");
    const uint32_t v3Packet[] = {0x68020000u, 1u,2u,3u,4u,5u,6u,0u};
    std::memset(runtime.memory().getVU1Data(), 0xa5, 32u);
    runtime.memory().processVIF1Data(reinterpret_cast<const uint8_t *>(v3Packet), sizeof(v3Packet));
    std::array<uint32_t,8> v3Lanes;
    std::memcpy(v3Lanes.data(), runtime.memory().getVU1Data(), 32u);
    if (v3Lanes != std::array<uint32_t,8>{1u,2u,3u,4u,4u,5u,6u,0u})
        throw std::runtime_error("VIF V3-32 failed fourth-lane source lookahead");
    const uint32_t v3Mask[] = {0x20000000u, 0xc0u};
    runtime.memory().processVIF1Data(reinterpret_cast<const uint8_t *>(v3Mask), sizeof(v3Mask));
    auto maskedV3 = std::array<uint32_t,8>{0x78020000u,1u,2u,3u,4u,5u,6u,0u};
    std::memset(runtime.memory().getVU1Data(), 0xa5, 32u);
    runtime.memory().processVIF1Data(reinterpret_cast<const uint8_t *>(maskedV3.data()), sizeof(maskedV3));
    std::memcpy(v3Lanes.data(), runtime.memory().getVU1Data(), 32u);
    if (v3Lanes[3] != 0xa5a5a5a5u || v3Lanes[7] != 0xa5a5a5a5u)
        throw std::runtime_error("VIF V3-32 lookahead ignored protected W lanes");
    if (unpack(0x68u, 0u, {1u,0u,0u,0u,2u,0u,0u,0u,3u,0u,0u,0u})[3] != 0xa5a5a5a5u)
        throw std::runtime_error("VIF V3-32 read past a buffer without lookahead");
    const uint32_t maskPacket[] = {0x20000000u, 0x87u,
        0x30000000u, 11u,22u,33u,44u, 0x31000000u,77u,88u,99u,111u};
    runtime.memory().processVIF1Data(reinterpret_cast<const uint8_t *>(maskPacket), sizeof(maskPacket));
    if (unpack(0x76u, 0u, {0xffu,0x7fu}) != std::array<uint32_t,4>{0xa5a5a5a5u,22u,0xffffffffu,77u})
        throw std::runtime_error("VIF expansion ignored row, column, or protected-lane masks");
    const uint32_t modePacket = 0x05000001u;
    runtime.memory().processVIF1Data(reinterpret_cast<const uint8_t *>(&modePacket), sizeof(modePacket));
    if (unpack(0x6fu, 0u, {0xffu,0xffu}) != std::array<uint32_t,4>{248u,248u,248u,128u})
        throw std::runtime_error("VIF packed colors incorrectly applied STMOD offsets");
    std::cout << "PASS: VIF two-component vectors replicate XYXY; packed colors expand RGB and alpha" << std::endl;
    {
        PS2Memory flushMemory;
        if (!flushMemory.initialize()) throw std::runtime_error("Could not initialize synthetic VIF flush memory");
        unsigned processed = 0;
        GifArbiter arbiter([&](const uint8_t *, uint32_t) { ++processed; });
        flushMemory.setGifArbiter(&arbiter);
        const std::array<uint8_t,16> packet{};
        arbiter.submit(GifPathId::Path3, packet.data(), packet.size());
        const uint32_t flushE = 0x10000000u;
        flushMemory.processVIF1Data(reinterpret_cast<const uint8_t *>(&flushE), 4u);
        if (processed != 0 || arbiter.empty())
            throw std::runtime_error("VIF FLUSHE incorrectly drained unrelated PATH3 work");
        const uint32_t flushA = 0x13000000u;
        flushMemory.processVIF1Data(reinterpret_cast<const uint8_t *>(&flushA), 4u);
        if (processed != 1 || !arbiter.empty())
            throw std::runtime_error("VIF FLUSHA did not complete queued GIF work before continuing");
        const uint32_t interrupted[] = {0x80000000u, 0x01000103u};
        flushMemory.processVIF1Data(reinterpret_cast<const uint8_t *>(interrupted), sizeof(interrupted));
        if (!(flushMemory.vif1_regs.stat & (1u << 10)) || flushMemory.vif1_regs.cycle == 0x103u)
            throw std::runtime_error("VIF interrupt failed to pause subsequent commands");
        flushMemory.writeIORegister(0x10003c10u, 8u);
        if ((flushMemory.vif1_regs.stat & (1u << 10)) || flushMemory.vif1_regs.cycle != 0x103u)
            throw std::runtime_error("VIF stall cancellation failed to resume commands");
        bool geometryStarted = false;
        flushMemory.setVu1MscalCallback([&](uint32_t, uint32_t, uint32_t) {
            if (processed != 2u) throw std::runtime_error("Geometry resumed before its queued texture upload");
            geometryStarted = true;
        });
        flushMemory.writeIORegister(0x10003c20u,0u);
        const uint32_t interruptedGeometry[] = {0x80000000u,0x14000000u};
        flushMemory.processVIF1Data(reinterpret_cast<const uint8_t *>(interruptedGeometry),sizeof(interruptedGeometry));
        if (geometryStarted) throw std::runtime_error("Geometry started before interrupt acknowledgement");
        arbiter.submit(GifPathId::Path3,packet.data(),packet.size());
        flushMemory.writeIORegister(0x10003c10u,8u);
        if (!geometryStarted) throw std::runtime_error("Geometry failed to resume after texture upload");
        const uint32_t dmaInterrupted[] = {0x80000000u,0x01000102u,0u,0u};
        std::memcpy(flushMemory.getRDRAM()+0x180000u,dmaInterrupted,sizeof(dmaInterrupted));
        flushMemory.writeIORegister(0x10009010u,0x180000u);
        flushMemory.writeIORegister(0x10009020u,1u);
        flushMemory.writeIORegister(0x10009000u,0x101u);
        if (!(flushMemory.readIORegister(0x10009000u) & 0x100u) ||
            !flushMemory.consumeCompletedDmacCauses().empty())
            throw std::runtime_error("VIF DMA completed while interrupted data remained stalled");
        flushMemory.writeIORegister(0x10003c10u,8u);
        if ((flushMemory.readIORegister(0x10009000u) & 0x100u) ||
            flushMemory.vif1_regs.cycle != 0x102u ||
            flushMemory.consumeCompletedDmacCauses() != std::vector<uint32_t>{1u})
            throw std::runtime_error("VIF DMA failed to complete once after stall cancellation");
        flushMemory.writeIORegister(0x10003c20u,1u); // ERR.MII masks command interrupts.
        flushMemory.processVIF1Data(reinterpret_cast<const uint8_t *>(interrupted),sizeof(interrupted));
        if ((flushMemory.vif1_regs.stat & (1u << 10)) || flushMemory.vif1_regs.cycle != 0x103u)
            throw std::runtime_error("Masked VIF command interrupt incorrectly stalled execution");
    }
    // Rasterize a synthetic red rectangle with a nonzero screen offset.
    // This checks the visible result across the same-sized frontend surface.
    auto &gs = runtime.gs();
    gs.writeRegister(0x4cu, 8ull << 16u); // FRAME_1: 512-wide RGBA32 at base zero.
    gs.writeRegister(0x18u, (1792ull * 16u) | ((1792ull * 16u) << 32u));
    gs.writeRegister(0x40u, (511ull << 16u) | (447ull << 48u));
    gs.writeRegister(0x47u, 0u); // TEST_1: no alpha or depth rejection.
    gs.writeRegister(0u, 6u); // Untextured sprite.
    gs.writeRegister(1u, 0x3f800000800000ffull); // Red, alpha 128, Q=1.
    gs.writeRegister(5u, (1792ull * 16u) | ((1792ull * 16u) << 16u));
    gs.writeRegister(5u, (2304ull * 16u) | ((2240ull * 16u) << 16u));
    gs.latchHostPresentationFrame();
    for (const auto &xy : {std::pair{0u,0u}, std::pair{255u,223u}, std::pair{511u,447u}}) {
        uint32_t pixel = 0;
        const uint32_t address = GSPSMCT32::addrPSMCT32(0u,8u,xy.first,xy.second);
        std::memcpy(&pixel, runtime.memory().getGSVRAM()+address,4u);
        if ((pixel & 0xffffffu) != 0xffu) throw std::runtime_error("Synthetic sprite did not cover its intended frontend surface");
    }
    // Enabling NEVER must reject the next rectangle; enabling ALWAYS must draw.
    gs.writeRegister(1u, 0x3f8000008000ff00ull);
    gs.writeRegister(0x4eu, 1ull << 32u); // Mask depth writes.
    gs.writeRegister(0x47u, 1ull << 16u); // Enabled depth test, NEVER.
    auto drawAgain = [&] {
        gs.writeRegister(5u, (1792ull * 16u) | ((1792ull * 16u) << 16u));
        gs.writeRegister(5u, (2304ull * 16u) | ((2240ull * 16u) << 16u));
        gs.latchHostPresentationFrame();
    };
    drawAgain();
    if ((gs.ReadVram(0u,0u,8u,255u,223u) & 0xffffffu) != 0xffu)
        throw std::runtime_error("Enabled NEVER depth comparison was bypassed");
    gs.writeRegister(0x47u, (1ull << 16u) | (1ull << 17u));
    drawAgain();
    if ((gs.ReadVram(0u,0u,8u,255u,223u) & 0xffffffu) != 0xff00u)
        throw std::runtime_error("Enabled ALWAYS depth comparison failed");
    std::cout << "PASS: offset sprite fills the surface; disabled, NEVER and ALWAYS depth modes behave correctly" << std::endl;
    runtime.registerFunction(0x00100000u, first);
    runtime.registerFunction(0x00100004u, second);
    R5900Context ctx{};
    ctx.pc = 0x00100000u;
    ctx.cop0_count = 0xfffffff0u;
    runtime.eeScheduler().reset(runtime.memory().getRDRAM(), ctx);
    std::array<uint64_t,15> originalDisplay{},fieldDisplay{};
    auto &display=runtime.memory().gs();
    std::memcpy(originalDisplay.data(),&display,sizeof(originalDisplay));
    for(unsigned i=0;i<15;i++)fieldDisplay[i]=0x123400000000ull+i;
    std::memcpy(&display,fieldDisplay.data(),sizeof(fieldDisplay));
    unsigned boundaryCalls=0;const auto eeOwner=std::this_thread::get_id();
    runtime.gs().setFieldBoundaryHandler([&](GSFieldBoundary field){
        if(std::this_thread::get_id()!=eeOwner || field.privileged!=fieldDisplay ||
           field.phase!=(field.tick&1u) || display.vsyncTick.load(std::memory_order_acquire)+1!=field.tick)
            throw std::runtime_error("EE field boundary not captured before matching UI tick");
        ++boundaryCalls;
    });
    runtime.eeScheduler().run();
    runtime.gs().setFieldBoundaryHandler({});
    std::memcpy(&display,originalDisplay.data(),sizeof(originalDisplay));
    if(std::getenv("PS2_PORT_HOST_VSYNC_TEST") && boundaryCalls<2)
        throw std::runtime_error("Field boundary hook missed real scheduler vsync events");
    if(std::getenv("PS2_PORT_HOST_VSYNC_TEST"))
        std::cout<<"PASS: scheduler field boundary precedes atomic UI clock on EE owner\n";
    if (!finished) return 3;
    std::cout << "PASS: Count advances, wraps, honors guest writes and survives dispatch" << std::endl;
    runtime.registerFunction(0x100000u, audioStart);
    runtime.registerFunction(0x100004u, audioEnd);
    runtime.registerFunction(0x100008u, audioCallback);
    ctx.pc = 0x100000u;
    runtime.eeScheduler().reset(runtime.memory().getRDRAM(), ctx);
    runtime.eeScheduler().run();
    std::cout << "PASS: 500 MPEG audio callbacks execute with exhausted guest heap and reused scratch records" << std::endl;
    runtime.registerFunction(0x100000u, graphicsStart);
    runtime.registerFunction(0x100004u, graphicsEnd);
    runtime.registerFunction(0x100008u, graphicsIrq);
    ctx.pc = 0x100000u;
    runtime.eeScheduler().reset(runtime.memory().getRDRAM(), ctx);
    runtime.eeScheduler().run();
    std::cout << "PASS: VIF command interrupt reaches its guest handler exactly once" << std::endl;
}
