# Runtime comparison — October 6, 2026

Reference project: [Shadow of the Colossus port](https://github.com/LightVelox/sotc-vibe-pc).
Its runtime submodule points to [TaylorNAlbarnaz/PS2Recomp](https://github.com/TaylorNAlbarnaz/PS2Recomp), branch `sotc-port`.
The inspected runtime is pinned at `ac9efa070638ad3b3accd284de6f898d5ab271d1` in a separate local comparison checkout. The active runtime remains the revision in `toolchain.lock.json`, with reviewed patches; it was not replaced wholesale.

## Methods adopted

- Bounded, repeatable scenarios with recorded input, exact build and definition hashes, a sequence of local frame captures, and machine-readable reports.
- Check game progress independently of guest cycle advancement. Repeated program counter/return address and unchanged transfer counts identify suspicious plateaus. Private per-game definitions can name confirmed failure sites. This heuristic does not detect every freeze and can flag a legitimate idle loop.
- Report almost-black frames separately from stalls. A visible goals/menu screen cannot prove gameplay. A nonblack frame cannot prove correct rendering.
- Compare baseline and candidate runs only when the scenario definition matches. Fixed wall-clock input timings can still diverge when runtime performance changes; confirm navigation by inspecting captures locally.
- Keep game-specific inputs, addresses and output private. Public packaging uses an explicit source whitelist and rejects symlink members.

## Instruction fixes under evaluation

The comparison identified two independently reproducible instruction-semantic defects:

1. `CVT.W.S` used host `nearbyintf`, rather than truncation toward zero with signed saturation. Boundary tests fail before the correction. The result behavior is also cross-checked against [PCSX2 COP1](https://github.com/PCSX2/pcsx2/blob/master/pcsx2/FPU.cpp).
2. Direct quadword load/store helpers and their macros retained the low four address bits. The comparison fork aligns those accesses. Synthetic checks cover misaligned reads and writes, untouched neighboring bytes, and byte writes retaining their original address.

These changes are in the generic runtime patch. Their instruction tests passing does not establish that they fix this title's visual or wave-generation failures. Record the actual baseline/candidate outcome in `STATUS.md` after running the same scenario.

## Methods not yet adopted

- Running PCSX2 as a differential reference. No installed PCSX2 or locally supplied PS2 BIOS was found in the inspected application/workspace locations. No BIOS is downloaded or bundled. Source comparisons are not equivalent to an emulator oracle.
- The comparison fork's OpenGL 4.6 renderer, asynchronous VIF, save states, and VU recompilation. These are substantial integrations requiring separate Mac compatibility work and regressions; none is enabled in the current build.
- Scenario save-state starts or game-frame-based input scheduling. Current scenarios use cold boot and host-time button pulses.

## Further references

- [SotC automated scenario harness](https://github.com/LightVelox/sotc-vibe-pc/blob/main/Docs/TESTING.md): state-based test starts, game logic progress checks, black captures and structured reports.
- [God of War reproducible pipeline](https://github.com/KIexster/god-of-war-recomp): separate overrides, ordered patches, bounded tests and documented unresolved rendering.

Only public tool sources were fetched for this comparison. Retail files, generated code, screenshots and traces are not published or included in the public source archive. Existing GPL-3.0 licensing of PS2Recomp and the comparison runtime applies to derivative source changes.

## Observed local outcome

The same 115-second Wave House definition was run before and after the two instruction fixes. The baseline reproduced the confirmed wave stall; the candidate did not, continued transferring graphics, and displayed a surfing HUD after the same goals screen. The world behind the HUD remained black. Neither test establishes playable behavior, and the fixes were not individually isolated in full-game builds. Private before/after reports and frame captures are retained outside this source package.

All 16 Python checks and the synthetic runtime regressions pass. The reference renderer and a PCSX2 oracle have not been integrated.

The SotC fork also corrects R5900 `SQRT.S` source selection from Fs to Ft. This was independently matched to the game’s encoded camera-normalization instruction: the squared vector length is in Ft while Fs contains a coordinate. The active generic tool patch and an instruction-word-checked migration now apply this correction to future translations and existing local output. Full-game visual validation is recorded separately.

## Performance integration review — October 6, 2026

The pinned comparison runtime contains a CPU renderer worker queue as well as its OpenGL backend. The active backend interface matches the worker wrapper's raster operations, making an isolated CPU threading experiment plausible. The wrapper must be adapted to preserve blocking FINISH/flush/readback ordering, copied upload-buffer ownership, bounded queues, worker failure propagation and platform floating-point state. A CPU-only adaptation is now integrated behind `PS2_PORT_GS_THREAD=1`, with synthetic ordering, ownership, barrier, floating-point environment and failure tests. It is disabled by default. Timed scenarios must verify rendering correctness and actual performance before enabling it in the normal app.

The comparison GPU path requests OpenGL 4.6. macOS OpenGL does not provide that context; a Metal implementation or separately evaluated compatible rendering path is required for Mac GPU acceleration. No GPU acceleration or frame-rate gain is claimed by this review.


## Local vector compiler integration — October 7, 2026

The pinned fork's shared interpreter/compiled VU helpers and compiler are adapted
into the generic local workflow. Save-state serialization is omitted; ARM uses
SSE2NEON. The scalar interpreter contracts multiply-add on AppleClang, whereas
separate NEON add/multiply intrinsics round twice. An isolated arithmetic probe
reproduces that divergence; explicit fused operations in compiled product sums
match the interpreter on the exercised game route. Differential verification
compares registers, pipelines, data memory and GIF packets rather than relying
on visible screen progression. The workflow treats missing verification evidence
as inconclusive and stops at the first logged mismatch.

Two captured images compile privately and improve the measured Wave House
guest completion cadence in two comparisons. Unregistered images fall back to
the interpreter. These findings establish neither coverage of other games nor
smooth enhanced gameplay; game-derived evidence remains in private storage.


## GPU/reference-renderer investigation — October 7, 2026

The official [PCSX2 GS runner](https://github.com/PCSX2/pcsx2/blob/master/pcsx2-gsrunner/Main.cpp) supports Metal on Apple platforms, rendering without a visible window, frame snapshots and performance measurements. Its [build target](https://github.com/PCSX2/pcsx2/blob/master/pcsx2-gsrunner/CMakeLists.txt) links the full PCSX2 library, so it is not a ready standalone replacement for this runtime backend. No runner is installed or integrated here.

A useful next reference path is local replay of captured GS work. [The dump loader](https://github.com/PCSX2/pcsx2/blob/master/pcsx2/GSDumpReplayer.cpp) restores GS state and private registers before replaying packets; [the dump writer](https://github.com/PCSX2/pcsx2/blob/master/pcsx2/GS/GSDump.cpp) stores that serialized state in its header. Raw register-history JSON is insufficient. A bridge must produce valid initial state, transfers, register updates and frame boundaries before replay can validate pixels or performance. Retail-derived dumps and reference frames must stay local. This is an integration investigation, not a working GPU backend or an FPS claim.

## Exact renderer replay lead — October 7, 2026

The pinned comparison fork already has [GS recording](https://github.com/TaylorNAlbarnaz/PS2Recomp/blob/ac9efa070638ad3b3accd284de6f898d5ab271d1/ps2xRuntime/src/lib/gs/gs_recording.cpp), [backend state](https://github.com/TaylorNAlbarnaz/PS2Recomp/blob/ac9efa070638ad3b3accd284de6f898d5ab271d1/ps2xRuntime/include/runtime/gs/gs_backend_state.h), and [replay tooling](https://github.com/TaylorNAlbarnaz/PS2Recomp/blob/ac9efa070638ad3b3accd284de6f898d5ab271d1/ps2xTest/tools/gs_replay.cpp). The captured backend state includes the retained texture-page bytes/tag, CLUT, transfer state and readback cursor. This is a useful basis for comparing exactly the same renderer workload instead of separate dynamic game runs. It is not integrated here: the recording code assumes x86 MXCSR handling, and wrapping the current threaded backend needs ordered state export plus safe serialization of concurrent presentation and guest calls. Capture bounds and replay checks must be adapted before use. Any recordings stay private and local.
