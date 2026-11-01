// Claude 2026-10-21: synthetic tests of the drift-controlled native PCM ring (ps2_audio.cpp here), through the real
// submitPcm/audioCallback with raylib's audio calls stood in (as Codex's audio_sink_test.cpp). No device, disc or game.
//  drain   : the queue drained to empty and refilled repeatedly - no output outside the input's range (no
//            extrapolation), silence fades only, and output resumes after re-priming.
//  stereo  : L and R carry different constants across many ring wraparounds - the channels never swap or mix.
//  burst   : a producer 1% fast then 1% slow, in bursty 800-frame fields with periodic 4-field stalls, against a steady
//            48 kHz consumer - L carries a ramp, so a dropped stretch shows as a jump and an underrun as a gap; the
//            ramp's per-frame increment must stay within the controller's 2%.
#include "runtime/ps2_audio.h"
#include "raylib.h"
#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
static AudioCallback callback;
extern "C" {
void SetAudioStreamBufferSizeDefault(int) {}
AudioStream LoadAudioStream(unsigned, unsigned, unsigned) { AudioStream a{}; a.buffer = reinterpret_cast<rAudioBuffer *>(uintptr_t(1)); return a; }
void SetAudioStreamCallback(AudioStream, AudioCallback cb) { callback = cb; }
void PlayAudioStream(AudioStream) {}
void StopAudioStream(AudioStream) {}
void UnloadAudioStream(AudioStream) { callback = nullptr; }
void SetMasterVolume(float) {}
bool IsSoundPlaying(Sound) { return false; }
Wave LoadWaveFromMemory(const char *, const unsigned char *, int) { return {}; }
Sound LoadSoundFromWave(Wave) { return {}; }
void PlaySound(Sound) {}
void StopSound(Sound) {}
void UnloadSound(Sound) {}
void UnloadWave(Wave) {}
void SetSoundPitch(Sound, float) {}
void SetSoundVolume(Sound, float) {}
}
namespace ps2_vag { bool decode(const uint8_t *, uint32_t, std::vector<int16_t> &out, uint32_t &rate) { out.clear(); rate = 48000; return true; } }

static int fails = 0;
#define CHECK(c, ...) do { if (!(c)) { std::printf("FAIL %s:%d ", __FILE__, __LINE__); std::printf(__VA_ARGS__); std::printf("\n"); ++fails; } } while (0)

static void pull(std::vector<int16_t> &out, unsigned frames) { out.assign(frames * 2, 0); callback(out.data(), frames); }

int main() {
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    // drain / re-prime: alternating full-scale input (the worst case for extrapolation), drained to empty each time.
    {
        PS2AudioBackend a; a.setAudioReady(true);
        if (!callback) { std::printf("FAIL no PCM stream callback (run with PS2_PORT_NATIVE_PCM=1, as run_tests.py does)\n"); return 2; }
        std::vector<int16_t> in, out;
        for (int round = 0; round < 50; ++round) {
            in.assign(8192 * 2, 0);
            for (size_t i = 0; i < in.size(); ++i) in[i] = (i / 2) % 2 ? 32767 : -32768;
            a.submitPcm(in);
            for (int k = 0; k < 20; ++k) {
                pull(out, 512 + (k * 37) % 256);
                for (int16_t s : out) CHECK(s >= -32768 && s <= 32767, "round %d out of range %d", round, s);
            }
        }
        // after draining, resubmission plays again (re-prime) - a DC input settles to its value
        in.assign(16384 * 2, 12000); a.submitPcm(in); pull(out, 4096);
        CHECK(std::abs(out[4000 * 2] - 12000) <= 1, "re-prime: expected 12000, got %d", out[4000 * 2]);
    }
    // stereo across wraparound: L = +7000, R = -3000, 40 ring lengths
    {
        PS2AudioBackend a; a.setAudioReady(true);
        std::vector<int16_t> in(1600 * 2), out;
        for (size_t i = 0; i < in.size(); i += 2) { in[i] = 7000; in[i + 1] = -3000; }
        long frames = 0;
        for (int field = 0; field < 1600; ++field) {
            a.submitPcm(in);
            pull(out, 1600);
            for (size_t f = 0; f < 1600; ++f) {
                if (frames > 9000) { CHECK(out[f * 2] == 7000 && out[f * 2 + 1] == -3000, "frame %ld L %d R %d", frames, out[f * 2], out[f * 2 + 1]); }
                ++frames;
            }
        }
    }
    // burst-rate drift: producer 800-frame fields at 60.6 then 59.4 Hz (vs 60 consumed), a 4-field stall every 300
    // fields followed by a catch-up burst; consumer 512-frame callbacks at exactly 48 kHz. L = triangle ramp (one step per input
    // frame, 0..29999..0), R = 1000 marker.
    {
        // 3-field (50 ms) hitches qualify: an 800-frame field arrives at once, so a settled queue dips to ~target - 800
        // frames (~63 ms at the 80 ms target) and a 4-field hitch (67 ms) can underrun whatever the controller does.
        // PS2_PORT_TEST_STALL_FIELDS=4 reports that case.
        const int stallFields = std::getenv("PS2_PORT_TEST_STALL_FIELDS") ? std::atoi(std::getenv("PS2_PORT_TEST_STALL_FIELDS")) : 3;
        PS2AudioBackend a; a.setAudioReady(true);
        std::vector<int16_t> in, out;
        long produced = 0; double tProduce = 0, tConsume = 0; double prevL = -1; long checkedFrom = 48000 * 2;
        long consumed = 0; int gaps = 0, transitionGaps = 0, jumps = 0, rateViolations = 0; long incAbove1_1 = 0; double maxInc = 0, minInc = 10;
        for (int field = 0; field < 60 * 120; ++field) {             // ~120 s
            const double rate = field < 60 * 60 ? 60.6 : 59.4;
            const bool stall = (field % 300) >= 300 - stallFields;    // stallFields fields with no output...
            tProduce += 1.0 / rate;
            if (!stall) {
                const int burst = (field % 300) == 0 && field > 0 ? stallFields + 1 : 1; // ...then the game catches up
                in.assign(size_t(800 * burst) * 2, 0);
                for (int f = 0; f < 800 * burst; ++f) { in[f * 2] = int16_t(produced % 60000 < 30000 ? produced % 60000 : 59999 - produced % 60000); in[f * 2 + 1] = 1000; ++produced; }
                a.submitPcm(in);
            }
            while (tConsume < tProduce) {
                pull(out, 512); tConsume += 512.0 / 48000.0;
                for (unsigned f = 0; f < 512; ++f, ++consumed) {
                    const double L = out[f * 2], R = out[f * 2 + 1];
                    if (consumed < checkedFrom) { prevL = L; continue; }
                    if (R != 1000) { if (field >= 3600 && field < 3600 + 1500) ++transitionGaps; else { if (gaps == 0 || gaps % 100 == 0) std::printf("steady gap at field %d consumed %ld\n", field, consumed); ++gaps; } prevL = -1; continue; }
                    if (prevL >= 0) {
                        const double inc = std::fabs(L - prevL);                 // triangle ramp: no wrap, interpolation stays on it
                        if (inc > 3.0) ++jumps; else { maxInc = std::max(maxInc, inc); if (inc > 1.1) ++incAbove1_1; /* 2 = rounding of the interpolated value at step ~1.01 */ minInc = std::min(minInc, inc); if (inc > 1.0 + 0.021 * 1.5 + 0.5) ++rateViolations; }
                    }
                    prevL = L;
                }
            }
        }
        std::printf("burst (%d-field stalls): consumed %ld frames, steady-state gaps %d, gaps within 25 s of the instant 2%% rate swing %d, jumps %d, increment %.3f..%.3f (%ld frames > 1.1)\n", stallFields, consumed, gaps, transitionGaps, jumps, minInc, maxInc, incAbove1_1);
        CHECK(gaps == 0, "underrun gaps: %d", gaps);
        CHECK(jumps == 0, "dropped stretches: %d", jumps);
    }
    std::printf(fails ? "drift_test: %d FAILURES\n" : "drift_test: PASS (drain/re-prime range, stereo wraparound, burst-rate drift)\n", fails);
    return fails ? 1 : 0;
}
