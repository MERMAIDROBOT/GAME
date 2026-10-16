# PS2 Port Lab

An experimental, local-first PS2 reverse-engineering and porting workbench.
It turns lessons from a first native macOS port into reusable processing code,
versioned correction rules, dependency checks and validation workflows.

**Status: working foundation, not a finished universal converter.** Translation
produces generated C++ representing machine-code behavior; it does not recover
an original developer's C++ source, names, comments or architecture. Translating
an executable does not by itself make a game playable.

## Shared engine and optional platform repositories

| Interface | Purpose | Current limit |
|---|---|---|
| `target_cli.py cpp` | Disc intake, executable inspection, analysis and C++ translation | Upstream toolchain/platform support and missing runtime contracts still need qualification |
| `target_cli.py macos` | Shared translation plus experimental native runtime/build/package tooling | Generic clean build does not yet reproduce every feature of the accepted first-title build |

The primary product in this repository is PS2 → C++. The optional macOS adapter
lives in [GAME-MACOS](https://github.com/MERMAIDROBOT/GAME-MACOS), pins this core
as a submodule, and receives Dependabot update PRs. Both use one knowledge bank
and shared implementation. Other device adapters can use the same API. See
[PLATFORMS.md](PLATFORMS.md) for propagation, review and compatibility checks.

## What is implemented

- Resumable local disc-file import with checksums, BOOT2 discovery, revision-pinned
  profiles, ELF inspection, PS2Recomp analysis/translation and representative
  generated-source syntax checks.
- An executable knowledge bank with three audited handlers: opcode-guarded EE
  SQRT operand correction, declared runtime-memory ownership checks, and bounded
  IOP import-table discovery. New rules require provenance and regression fixtures.
- A 28-stage dependency/evidence scheduler covering runtime, graphics, textures,
  audio, controls, saves, clocks, packaging and preservation. Missing executors
  explicitly remain unfinished; the graph does not imply all 28 stages execute.
- Isolated compile-unit source recovery using real compiler dependencies and
  reference-object/native-code comparisons.
- Local texture decoding, faithful resampling and optional pinned local
  Real-ESRGAN processing, including selective 30% blending for important detail.
  Offline output is separate from verified runtime replacement loading.
- Private scenario, input acknowledgement, visual postcondition and graphics
  replay tooling, plus asset-free regression tests.

## Try the interface

Requires Python 3.11+. Tests also need Pillow, NumPy and Clang; native macOS work needs
Apple Command Line Tools. Public tool downloads are a separate setup stage.

```sh
python3 -m pip install -r requirements.txt
python3 target_cli.py cpp --help
python3 target_cli.py macos --help
python3 -m unittest discover -s tests -v
python3 pipeline.py doctor
```

To process a locally supplied disc directory, follow [NEXT-GAME.md](NEXT-GAME.md).
The default `next-game` invocation plans; `--execute` runs its supported stages.
It stops at explicit integration gaps rather than claiming arbitrary games work.
No game data, BIOS, trained-model weights or playable game binaries are included.

## What remains

The accepted private first-title application reached v56, with paraLLEl-GS through
MoltenVK, adaptive supersampling, FSR1-style presentation, audio and texture
replacement work. This repository does **not** yet rebuild that complete stack
from a fresh checkout. Exact runtime/IOP/renderer source and link consolidation,
revision-gated adapters, native conformance and second-title validation remain
required. No universal compatibility, original-source recovery or 120 FPS claim
is made. The latest local generic suite passed 164 tests; CI results are separate.

Start with [ENGINE.md](ENGINE.md), [COMPILER.md](COMPILER.md),
[NEXT-GAME.md](NEXT-GAME.md), [TEXTURES.md](TEXTURES.md) and
[CONTRIBUTING.md](CONTRIBUTING.md). [STATUS.md](STATUS.md) and the material below
include historical measurements tied to their specific builds. These historical
results do not automatically qualify a new title or current generic runtime.

## Privacy and contributions

Processing is local. Keep retail assets, generated/decompiled game code, binaries,
saves, screenshots and raw traces out of issues and pull requests. Use synthetic
reproducers. Public exports use an explicit file whitelist; game work is excluded.
See [PRIVACY.md](PRIVACY.md) and [UPSTREAM-NOTICES.md](UPSTREAM-NOTICES.md).
Reusable source is GPL-3.0; dependency/model terms remain separate.

## Detailed workflow and historical implementation notes

Private scenario definitions can include ordered `visual_postconditions`. Each names a private PPM `reference`, a four-integer `region`, and an `after_sequence` identifying a visual or post-navigation controller action. Optional bounds are `max_mean_error`, `min_delay_seconds`, `timeout_seconds`, `stable_seconds`, `min_frames` (at least two), and `max_sample_gap_seconds`. The runner waits for the input ACK, ignores the existing capture, then requires fresh matching samples. It holds subsequent actions until the result is observed. Postconditions cannot contain controller actions. A match verifies only the specified sampled regions; complete rendering, player control and sustained gameplay need separate evidence.

`navigation_completed` describes issued actions; `postconditions.status` describes resulting visual observations. Missing ACKs or incomplete/failed requested outcomes cannot pass a scenario. `compare-scenarios` retains diagnostic reports but exits with code 2 when either run lacks successful navigation, passed postconditions or a complete guest-flip measurement window. Legacy runs without postconditions remain reviewable but do not qualify automatically for timing comparisons. Guest flip cadence is not displayed FPS or simulation-speed validation.

This is a reusable orchestration layer, not an automatic universal PS2-to-macOS converter. It brings public tools into one workflow; it does not remove their dependencies or the need for game-specific runtime work. A successful translation is not a successful native game port.

## Enhancement target

The goal is an enhanced native Mac game with measured gameplay FPS above a verified original PS2 baseline. A first engineering target is stable 60 FPS on this Mac, followed by higher rates where the game supports them. Neither target has been achieved or validated yet.

Performance acceptance requires the following evidence:

- Compare the same gameplay scene against a verified PS2 baseline; record the Mac, build, resolution, settings and scene.
- Measure newly completed game frames and frame-time distribution during sustained surfing. Window refreshes, repeated copies of the same image and guest flip-function entries are supporting diagnostics, not delivered gameplay FPS.
- Preserve intended real-time movement, animation, physics, wave behavior, audio and timer speed at the improved frame rate. Validate menu transitions and previews, in-engine cutscenes, and decoded movies independently; extra rendered frames must not advance their clocks faster. Increasing the game clock alone is not an enhancement.
- Check playback duration, progression and frame pacing in each phase. A correct average duration does not excuse bursts, skipped imagery, stalls or audio drift. Record deliberate skip inputs separately from natural playback; a menu-to-attract transition does not count as controlled gameplay.
- Keep the surfer, board and scenery visible and correct, and verify responsive keyboard input. A faster broken scene does not meet the target.
- Retain the scenario definition and results locally, with generic fixes and regression checks in this repeatable workflow. Retail assets and game-derived diagnostics remain private.

The initial renderer rasterized in software on the CPU. The accepted Kelly build
now reuses paraLLEl-GS/MoltenVK; the generic clean-build command still needs those
later changes consolidated. The comparison fork's OpenGL4.6 backend is not a
drop-in macOS backend.

## Use

Requires Python 3.11+, Git, Apple's Command Line Tools on macOS, and network access during tool setup only.

From this directory:

```sh
python3 pipeline.py doctor
python3 pipeline.py bootstrap
python3 pipeline.py --profile profiles/kelly-slater.json verify
python3 pipeline.py --profile profiles/kelly-slater.json inspect
python3 pipeline.py --profile profiles/kelly-slater.json analyze
python3 pipeline.py --profile profiles/kelly-slater.json translate
python3 pipeline.py --profile profiles/kelly-slater.json check-generated
python3 pipeline.py --profile profiles/kelly-slater.json status
```

Native runtime stages (local macOS builds):

```sh
python3 pipeline.py bootstrap-runtime
python3 pipeline.py --profile profiles/kelly-slater.json build-native
python3 pipeline.py --profile profiles/kelly-slater.json test-runtime
python3 pipeline.py --profile profiles/kelly-slater.json boot-probe
python3 pipeline.py --profile profiles/kelly-slater.json diagnose
python3 pipeline.py --profile profiles/kelly-slater.json package-local
```

`bootstrap-runtime` downloads/builds public dependencies without reading game files. FFmpeg is built with network support disabled and MPEG-2 decoding enabled. `build-native` uses the already-downloaded sources with CMake fetching disabled, and attaches local generated game files through the generic build overlay. It compiles and links the entire game runner. `boot-probe` launches that local runner for up to 30 seconds, records exit/timeout and diagnostics, and stops it. A timeout or successful exit does not establish that menus or gameplay work.

`diagnose` runs the same bounded probe without opening a window or audio device, with scheduler and MPEG traces saved locally, plus a private PPM framebuffer capture when a nonblack frame is available. The generic runtime patch advances COP0 Count with scheduled guest cycles and adds opt-in diagnostics. Its synthetic tests check timer advancement, wrapping, guest writes and dispatch persistence, plus 500 MPEG audio callbacks under an exhausted game heap. Callback argument records use recycled runtime scratch memory so a full game heap cannot silently suppress them.

`package-local` creates an ad-hoc signed debug `.app` beside the reusable source directory. It contains generated game code and references the private local disc copy. It is for use on this Mac only: do not upload it, publish it, or include it in the reusable source package. The launcher writes diagnostics into the game's private `logs/finder-launch.log` file. A Finder launch is needed when the command host cannot initialize macOS GUI services.

`prepare` runs verification, inspection, analysis, and translation in sequence. `bootstrap` downloads and builds the pinned public toolchain separately from any game processing. Setup may need platform fixes when a new upstream revision is selected. See `PRIVACY.md` for the local-only processing policy.

For another disc:

```sh
python3 pipeline.py new-profile my-game --title "My game"
python3 pipeline.py --profile profiles/my-game.json import /Volumes/DISC_NAME
python3 pipeline.py --profile profiles/my-game.json prepare
```

An import produces a directory copy and checksum manifest, not a sector-level ISO. It is resumable: previously copied files are checked locally before skipping them. Import stops on a read failure and retains completed files. For discs with multiple executables or unusual boot layouts, edit the profile's `expected_executable` after inspecting the input. Unsupported or stripped executables may require a Ghidra function map instead of the analyzer fallback.

## Stages and evidence

| Stage | Evidence produced | What it does not establish |
|---|---|---|
| Import / verify | Per-file sizes and SHA-256 checks | Independent authenticity of a retail dump |
| Inspect | ELF layout, symbols, boot path, compiler clues | Exact compiler identity or complete function boundaries |
| Analyze | Upstream analyzer configuration and logs | Correct runtime bindings |
| Translate | C++ output, tool return code, output inventory | Compilable output or playable behavior |
| Check generated | Native syntax checks of representative files | Full native build, linking, or runtime behavior |
| Native build / local package | Full native executable and local debug app | Correct boot, menus, gameplay, or a distributable application |
| Behavioral validation | Still required per game | No compatibility or graphics accuracy claim yet |

The runtime commands are experimental. Runtime integration still needs graphics/VU/IOP services, asset access, and game-specific validation. Additional tools such as Ghidra, Rabbitizer, objdiff, or compiler fingerprinting experiments can be added behind the same interface as justified by actual findings.

## Storage

- Reusable source: this directory.
- Public tools and their build environment: the workspace `work/` directory on this machine, configurable using `PS2_PORT_LAB_WORK`.
- Private game state: each profile's `private_dir`, or `.private/games/<id>` for a new profile.
- Logs: private per-game logs for game processing; public tool setup logs under toolchain work.

No remote repository is created and no game artifact is published.

## Local debug controls

The complete mapping is in [CONTROLS.md](CONTROLS.md) and the default in-window guide (F1, or Fn + F1, to hide/show). Focus the game window. Enter is Start; Space or left mouse click is Cross; Escape or right click is Circle. Z is Square, V is Triangle, Q/E are L1/R1, left/right Shift are L2/R2, and Tab is Select. WASD supplies only the left analog stick, arrows supply only the D-pad buttons, and IJKL supplies the right stick. F1 toggles the on-screen guide. Close the window to quit; Escape is a game button.

The window thread captures input and the guest reads a synchronized snapshot. Quick button presses are retained until a guest read consumes them. Mouse buttons are mapped; mouse movement is not an analog camera input.

## Display timing and graphics interrupt diagnostics

The local runtime patch schedules display refreshes from elapsed host time so slow abstract guest-cycle accounting does not stall VBlank. Other scheduled events retain their cycle-based eligibility. Run `PS2_PORT_HOST_VSYNC_TEST=1 python3 pipeline.py test-runtime` to check display progress across a deliberately slow guest dispatch. Synthetic tests also check that a VIF command's interrupt reaches its registered guest handler exactly once.

For a bounded local transition probe, use `PS2_PORT_TEST_START=1 python3 pipeline.py diagnose`. In headless diagnostic mode this injects a brief Start press every two seconds, beginning after two seconds. It is disabled in normal-window launches. Passing a synthetic test does not establish menu or gameplay correctness; inspect the private probe log and framebuffer locally.

To record local GS draw history during a transition probe, run `PS2_PORT_GS_TRACE=1 PS2_PORT_TEST_START=1 python3 pipeline.py diagnose`. This explicitly unpauses the runtime's otherwise paused graphics history. The private log records recent draw/transfer counts, draw bounds and the selected presentation framebuffer. A synthetic recording check is available with `PS2_PORT_GS_TRACE=1 python3 pipeline.py test-runtime`. Event counts refer to a bounded recent history, not lifetime totals.

Graphics tracing also saves a private JSONL event history and a 4 MiB VRAM snapshot beside it. `PS2_PORT_VU_TRACE=1` logs a bounded sample of matrix applications per call site. These files stay under the private game directory and must not be included in a source archive.

`PS2_PORT_TEST_IGNORE_DEPTH=1` is an opt-in diagnostic that bypasses depth comparison to distinguish depth rejection from missing drawing commands. It changes rendering semantics and is not a verified gameplay workaround. Normal launches keep depth comparison enabled.

On AppleClang, the native build uses a private ThinLTO cache inside the native build directory to reduce repeated link work. It can contain compiled game code and is excluded from the reusable source package.

To reproduce a single press rather than repeated presses, add `PS2_PORT_TEST_START_ONCE=1` alongside `PS2_PORT_TEST_START=1`. To press Start again after the title has appeared, also set `PS2_PORT_TEST_EXTRA_START_MS=12000`; this adds a 250 ms pulse twelve seconds after the first published headless pad state. These input injections remain disabled for normal-window launches.

The local debug launcher records its build identifier, bundle path and declared renderer settings. Lightweight cost profiling remains enabled. Detailed graphics history and vector tracing default to disabled because collecting backend snapshots can drain the renderer queue and stall normal play. Set the boolean profile option `graphics_history` to true only for a local rendering investigation, then repackage. The traces and VRAM snapshots are excluded from the reusable archive.

An optional `PS2_PORT_VU_PACKET` absolute private output path saves the first emitted drawing packet larger than 512 bytes when vector tracing is enabled. This packet may contain retail-derived drawing data; keep it private alongside logs and do not distribute it.

### Repeating local menu probes

`PS2_PORT_TEST_PAD_SCRIPT` accepts up to 64 comma-separated `milliseconds:button-mask` pairs for headless probes. Each pair holds the active-low PS2 button mask for 250 ms; for example, `20000:0x4000` presses Cross after 20 seconds. This input is disabled during normal window launches. `PS2_PORT_HEAP_TRACE=1` adds allocator request and result records to local logs. Probe scripts, game-specific inputs, logs, frames, and generated code remain private.

## Repeatable scenario comparisons

`scenario` cold-boots the recorded native build, supplies headless input, retains distinct framebuffer captures, and writes Markdown/JSON observations. It reports known stalls, suspicious unchanged counters and almost-black frames separately. It never labels a run playable automatically. A blocked/review/process-exited run returns exit code 2; `observed` returns 0 but still does not mean gameplay passed.

Store each game-specific scenario JSON under that game's private directory. Example structure (replace the private path for your profile):

```json
{
  "duration_seconds": 115,
  "plateau_samples": 8,
  "environment": {
    "PS2_PORT_TEST_START": "1",
    "PS2_PORT_TEST_START_ONCE": "1",
    "PS2_PORT_TEST_EXTRA_START_MS": "12000",
    "PS2_PORT_TEST_PAD_SCRIPT": "20000:0x4000,26000:0x4000"
  },
  "known_stalls": {}
}
```

Allowed options also include `PS2_PORT_WATCH` and `PS2_PORT_HEAP_TRACE`. Unknown diagnostic environment options are rejected. The runner removes inherited `PS2_PORT_*` diagnostic flags so shell settings cannot silently alter the comparison. Definitions and reports contain game-specific observations and remain private.

```sh
python3 pipeline.py scenario /absolute/private/game/analysis/scenarios/level.json --name baseline
# After a candidate fix and build-native:
python3 pipeline.py scenario /absolute/private/game/analysis/scenarios/level.json --name candidate
python3 pipeline.py compare-scenarios baseline candidate --name comparison
python3 pipeline.py source-package
```

Use unique run names; existing runs are never overwritten. Comparison rejects differing definition hashes. Timed input can reach a different screen when performance changes, so inspect captured frames before concluding a fix helped. All captures and reports go into private `logs/scenarios/`. These checks adopt useful methods from the SotC project; they do not yet run PCSX2 as a differential oracle or use save states. See [runtime comparison](RUNTIME_COMPARISON.md).

`source-package` validates an explicit public-source whitelist, rejects symlinks, and creates the reusable ZIP beside this source directory. It never recursively archives game/work folders or the local app.

## R5900 square-root translation correction

The tool patch makes `SQRT.S` read its encoded Ft register, as the R5900 requires. `translate` and `build-native` also validate and migrate existing pinned-tool output using each instruction’s original word. Only affected private C++ files are rewritten; the correction is generic and contains no retail code. Correction counts are saved in the private analysis directory.

Scenarios accept `PS2_PORT_MATRIX_TRACE`, `PS2_PORT_VU_TRACE` and `PS2_PORT_GS_TRACE` for bounded local rendering investigations. Matrix tracing records a bounded sample of multiplication inputs and outputs; these can contain game-derived state and remain private.

`test-runtime` also builds and runs the pinned extended 43-test VU1 suite and standalone 45-test graphics suite covering texture cache visibility, palette loads/banks, and VRAM formats/aliases. These suites use synthetic data only. A passing suite does not establish real-game graphics accuracy. Graphics-enabled scenarios retain a private bounded event history and VRAM snapshot for investigation.

Keyboard button sampling also consumes queued key-press events, retaining quick taps that finish between presentation polls. Held keys continue to supply analog stick state. This improves event retention; it does not establish input latency or rendering performance.

`PS2_PORT_GS_PROFILE=1` also records VU interpreter run costs and budget exhaustion counts. VU cost includes synchronous XGKICK rendering, so it overlaps GS drawing cost and must not be added to it. `PS2_PORT_TEST_IGNORE_DEPTH=1` is supported only as an explicit scenario diagnostic to isolate depth rejection; its imagery is not a correctness result and the normal launcher leaves it disabled.

## Experimental rendering concurrency

`PS2_PORT_GS_THREAD=1` selects a bounded CPU renderer worker queue; the default remains synchronous. This is adapted from the pinned comparison runtime, with blocking FINISH/flush/readback semantics, copied upload buffers, portable floating-point environment propagation and worker-error reporting. It provides no GPU acceleration.

Run `PS2_PORT_GS_THREAD=1 python3 pipeline.py test-runtime` to exercise the runtime/VU/graphics suites with the worker enabled. A private scenario may also set this switch. Compare captured imagery and progression as well as timing: enabling a second thread is not proof of better FPS. The packaged launcher reads the boolean `renderer_worker` profile setting, which defaults to false for other discs. Kelly Slater enables it after two controlled paired comparisons showed higher guest-flip cadence. This remains experimental; complete gameplay and enhanced display FPS are unverified.

## Private state-based navigation

Scenario definitions may supply `visual_steps`: an ordered list of private PPM reference paths, rectangular regions, bounded mean RGB error thresholds, button masks and optional analog-stick holds. Each step waits for its reference region before issuing a sequenced local input command; it does not use a fixed wall-clock button schedule. References must remain inside the game's private storage. The report records reference hashes, matched frame hashes, action times, and incomplete-navigation errors. Comparisons reject changed reference inputs. A step may specify `reference_alternatives` with at most two additional audited private PPMs of identical dimensions. Every variant uses the same region and error bounds; reports identify the selected reference and hash. Alternatives do not bypass input acknowledgments, capture freshness or postcondition stability.

Each step accepts `name`, `reference`, `region` (X/Y/width/height), `max_mean_error` (default 8), `button_mask` (default 0), `hold_ms` (1–10000, default 250), `left_stick` (two bytes, default neutral), `min_delay_seconds` (default 1) and `timeout_seconds` (default 90). Navigation supports at most 64 steps and cannot be combined with host-time input scripts. Normal keyboard input never reads the local command file. A screen match establishes only that image region; inspect the resulting navigation and gameplay locally.

The headless-only `PS2_PORT_PAD_COMMAND` file protocol is an increasing sequence number, button mask, hold milliseconds, and left-stick X/Y bytes, as decimal integers separated by spaces. The harness replaces this file atomically. The runtime rejects stale or out-of-range commands; the hold expires automatically. This is local diagnostic input, not a network service.

## Controlled renderer treatments and frame timing

Use `scenario <private-definition> --name <run> --renderer-worker off` or `--renderer-worker on` to compare the same binary and scene definition with a recorded renderer treatment. The definition cannot also set `PS2_PORT_GS_THREAD`. The treatment appears in comparison reports; reference images and all controller actions must remain identical.

A private diagnostic build may emit `completed-flip` markers at the final return point of a verified game flip routine. `timing_after_sequence` selects the acknowledged controller action after which to measure, followed by a five-second warm-up exclusion and a common 15-second measurement window. Reports flag whether the full window is covered and record sample count, completed flips per second, median/95th/99th percentile frame intervals and scope limitations. Startup/menu markers before that action, host refreshes and scheduler returns are excluded. Missing markers or acknowledgements do not produce a performance pass. This cadence is a guest-rendering diagnostic; it does not establish monitor delivery, original PS2 performance or correct physics/animation speed.

The title-specific completion probe remains private and temporary. Restore verified original generated sources and rebuild before packaging the normal app. No generated game code, frame captures or private probes belong in the reusable source archive.


The VU runtime commits pending pipeline results on entry/resume and at each advanced cycle boundary before XGKICK reads. It avoids a duplicate scan before the following instruction. XGKICK resets transfer metadata and reuses its 64 KiB packet scratch storage; every byte passed to GIF is copied before submission. Synthetic checks include a shorter packet after a longer one. `PS2_PORT_GS_PROFILE=1` also measures full decode-cache rebuild costs.

Detailed graphics tracing can be compared without changing scene inputs using `scenario ... --graphics-history off` and `scenario ... --graphics-history on`. The report records the treatment; definitions that also declare a conflicting trace flag are rejected. Use matching treatments when comparing runtime optimizations.

The local launcher, probes and scenarios supply an absolute `PS2_PORT_IO_ROOT` before runtime initialization. This seeds host, disc and memory-card paths without a working-directory lookup during memory-card reset. Direct CLI consumers keep the existing current-directory fallback when the option is absent; a relative configured root is rejected. ELF loading still establishes the final per-game paths.

Kelly Slater enables `application_support_storage`: `package-local` stages a separate checksum-verified runtime copy under `~/Library/Application Support/PS2 Port Lab/<profile-id>/disc-root`, with its root accessible only to the current user. Existing saves migrate once and newer runtime saves are retained on later packaging. The original imported archive remains in the private workspace. Other profiles default to their imported disc directory. The launcher uses `/` as its working directory and explicit absolute I/O roots, avoiding protected working-directory lookups during window-library startup. Runtime copies and saves are private and never included in the source ZIP.


Scenario definitions may include `post_navigation_actions` for bounded controller
sequences after visual navigation completes. Each action supplies
`offset_seconds` relative to the last visual action, `button_mask`, `hold_ms`
and optional `left_stick` (two byte values). Offsets must increase; total visual
and timed actions cannot exceed 64. The runner records all commands and checks
runtime acknowledgements. Timed actions do not require new screenshots, and
remain restricted to the headless local scenario runner. Definitions and their
references stay in private game storage. An acknowledgement proves input receipt;
inspect gameplay captures to establish what the game did.


Experimental native VU builds may declare `vu_execution_supported` in their
private build record. Only those builds accept `capture_vu_programs: true` and
`vu_execution` values `interpreter`, `compiled`, or `verify`. Capture writes to
a new owner-only `vu-code` directory within the local scenario output; its
binary images and generated C++ are retail-derived private data. Verification
compares vector state, memory and graphics packets and adds substantial overhead;
use a separate run for timing. Ambient `PS2X_` and `PS2_PORT_` overrides are
removed before applying recorded scenario settings. Support for a treatment
does not establish compiled-program coverage, correctness or a speed improvement.

Verification runs stop at the first logged vector mismatch and report
`vu_verification_failed`. If no compiled verification summary appears, they
report `vu_verification_unobserved`; absence of mismatches alone is insufficient.
Counts are lower bounds from flushed diagnostics, rather than full lifetime totals.

## Local vector compilation

The bundled GPL-3.0 vector compiler is adapted from the pinned comparison
repository; its license is included under `tools/PS2Recomp-LICENSE`. No additional
repository is needed for this stage. For each private game profile:

1. Run `build-native` to build the runner and vector compiler.
2. Run a private scenario with `capture_vu_programs: true` and
   `vu_execution: "interpreter"` through the scenes to optimize.
3. Run `compile-vu <private-run-directory>/vu-code`. Captured images remain
   untouched. Generated sources and their checksum manifest go into the game's
   owner-only `generated-vu` directory, outside the reusable source archive.
4. Set `compiled_vu: true` in that game's profile and run `build-native` again.
   The build rejects modified or unlisted compiled sources.
5. Run a separate scenario with `vu_execution: "verify"`. Check for observed
   comparisons, zero mismatches, correct imagery and navigation. Then measure
   with verification disabled using the same input definition as the baseline.

Only captured images are registered; other images use the interpreter. A capture
from one level does not establish coverage of another. An unchanged compilation
resumes by checking its manifest. Archive an existing `generated-vu` directory
before deliberately replacing it with a different captured source set. A failed
compile does not promote partial output. Keep game files, vector images,
generated C++, binaries and diagnostic captures local.

Host presentation can run on a coalescing background worker while the window continues polling input. The Kelly profile enables `async_presentation`; other profiles default to synchronous presentation. Two phase-verified Wave House GUI pairs showed roughly 59 input polls/s versus 47–48 synchronously, with maximum sampled gaps about 17 ms versus 71–84ms. Completed guest flips remained about 12–13/s; this is an input responsiveness improvement, not a 60 FPS result. Synthetic checks cover coalescing, immutable completed frames, FP environment, worker failures, and reset/destructor joins in plain and queued renderers. Boat/menu stutter, intermittent missing backgrounds, and sustained smooth riding remain unresolved.

### Private raw graphics captures

A scenario can request `capture_raw_gs: true` and `raw_gs_seconds` (1–300,
strictly shorter than its run). This requires a separately built diagnostic
stage with `raw_gs_capture_supported: true`. The normal packaged app does not
record raw commands. The runner chooses a file inside the new private scenario
directory; shell capture settings are cleared before each run.

`rawgs.py` streams and validates the bounded PGSCAP1/PGSCAP2 containers, its sequence,
record sizes, initial VRAM snapshot and terminal VRAM snapshot. Incomplete
recordings fail explicitly. Explicit unsupported host-operation markers make
a complete capture unreplayable until those operations receive a faithful
translation. Container validity does not certify GIF semantics, guest execution,
image correctness or original PlayStation timing. All recordings are private
retail-derived data and must stay local.

PGSCAP2 additionally records the AArch64 floating-point control state before
commands and distinguishes normal from optimized packed ingress. The validator
requires that state before every graphics command. Legacy recordings remain
readable but cannot qualify a strict CPU comparison: their rounding and decoder
route must be inferred. Host rounding can affect the software rasterizer even
when the packet bytes are identical. This is a comparison against the current
native runtime; it does not establish original PS2 fidelity.


Visual navigation and postconditions may opt into `match_block_size` (integer 1–8,
 default 1) to compare local RGB area averages across small rasterization changes.
When aggregation is enabled, `max_fine_mean_error` (default 30, range 0–50) also caps
unaveraged pixel error, rejecting large differences hidden by equal block averages.
Reports retain both errors and the block size. ACK, fresh-capture and stable-output
requirements still apply. These are navigation checks, not rendering-quality proof.


Local raw graphics replay is available through `pipeline.py replay-raw-gs CAPTURE --backend BACKEND_JSON --name UNIQUE_NAME [--batch on|off] [--timeout 300]`. Both inputs must resolve inside the selected profile's private game directory. Output stays under `logs/replays/UNIQUE_NAME`; existing runs are never overwritten.

The backend JSON is private and uses schema1, protocol `pgscap-cli-v1`, and `executable`/`moltenvk` objects, each containing an absolute local `path` and its lowercase `sha256`. It may also record dependency revisions and source hashes. The native executable must accept `--capture PATH --output DIRECTORY --moltenvk PATH` and emit `result.json` and a full4MiB `vram.bin`. This command requires an explicitly built and pinned experimental backend; it does not install a renderer automatically.

Preflight checks the capture's complete records and supported ingress before launching. Results retain capture/backend hashes, treatment, native status, logs and terminal snapshot hash. A successful command means a completed observation: inspect `matches_recorded_footer` separately. A CPU/GPU mismatch is not accepted rendering correctness. PGSCAP is not a PCSX2 GS dump and does not yet retain temporal scanout boundaries; replay cannot measure jitter, simulation speed, gameplay or display FPS. The cross-profile lock prevents overlapping instances of this replay command; audit other game/build/GPU processes before running it. All captures, backend manifests and results remain excluded from the source archive.


Run `pipeline.py memory-audit --runtime-header /local/runtime/Helpers/State.h --layout analysis/memory-layout.json --name unique-audit` before adopting runtime-owned EE pools for a title. The layout stays inside private game storage and contains an `occupied` array of objects with `name`, hexadecimal-string `start`, and hexadecimal-string `size` fields. Include known guest heap, stack, callback arenas and other reservations. ELF load segments, including BSS, are added automatically. The command fingerprints the disc, layout and runtime header, rejects pool overlaps and malformed ranges, and preserves its report under `analysis/memory-audits/unique-audit/`. Existing audits are never overwritten. Exit2 means a collision; malformed input also fails.

This is an explicit audit command, not an automatic build/package gate. A passing result certifies only disjointness of declared ranges. It does not establish that an arbitrary game or PS2 kernel leaves a proposed low-memory region free; dynamic overlays and unknown allocations still require investigation. Actual layouts and reports remain excluded from the source archive.


The compiler consolidation workbench is described in [COMPILER.md](COMPILER.md). `compiler-plan` validates a pinned private component inventory; `compiler-stage` freezes its listed sources in a new private directory. They expose clean-source blockers and do not execute a native build or replace the game.
