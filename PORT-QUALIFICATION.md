# Executable port qualification

`qualify-port` checks a packaged app and its receipt without launching the game. It verifies referenced files, hashes, source branch contracts, release configuration, and bounded optional log/GIF inputs. Scenario captures must be bound to the exact receipt. A passing structural report is not gameplay acceptance and never reports measured unique FPS from residency counters.

The compiler applies signed EE branch corrections only when decoded instruction records establish the low-64-bit signed branch contract. Unknown output shapes fail closed before writing translated files. This is instruction semantics, not title or scenery recognition.

`convert-disc --help` describes the mounted-disc conversion entry point. C++ translation and macOS packaging are separate targets. Native runtime tests and packaging do not prove arbitrary game compatibility. Keep the original disc and generated game sources local.

## Preserved runtime components

The patch stack includes IOP service/device work, VU0 macro handling, scalar FPU semantics, lazy timers, VIF unpack paths, native PCM submission, and host keyboard input. Native PCM output still consumes a compatible IOP/SPU producer; it does not remove every hardware compatibility layer. Host input preserves event-ordered keyboard transitions, release barriers, independent analog steering and button aliases. Optional remote touchscreen direction recovery is disabled by default; L2 remains unchanged. Compiled synthetic checks cover these contracts, not every game's trick timing.

`runtime/gpu` preserves the Vulkan/MoltenVK host renderer, worker residency optimization, shader and bridge source. The host adapter requires compatible GS/texture-pack APIs. Asset-free worker tests validate command ordering and residency behavior; they do not validate every shader, photo, title, or scene. Antigravity v8 depth-clear experiments are deliberately excluded.

## Acceptance

Require actual player gameplay, representative composed captures, audio listening, photos/FIFO, input chords, transitions, window/fullscreen behavior and saves before promoting a game build. Keep candidates separate from a stable app. No automatic game launch, retail upload or claim of universal native conversion follows from a passing source test.

`runtime/presentation/completed_field.hpp` carries immutable scanout ownership independently of a title adapter. Its 14 MiB lifetime fixture confirms resolution retains the shared future and does not copy the image. Adapters must still publish frame clocks and pixels together.


Known stateful `sceSdRemote` / `sceSdRemoteInit` substitutions are now compiler
policy gates: their generic handlers do not implement the complete RPC protocol.
The compiler preserves their original bodies, and generated-source qualification
rejects stale output that still calls those handlers. Other SDK coverage remains
a separate conformance requirement; preserving two functions does not certify
music, speech, or every game's audio.

Boot probes now save a hashed qualification report and log receipt. Unimplemented
SDK calls, missing compiled guest targets, failed IOP allocations and blocked RPC
fail automatic runtime qualification even when process/frame/audio counters look
clean. A nonzero exit also fails. A clean bounded timeout remains an observation,
not player gameplay, audio or temporal acceptance. The probe uses the shared
compute guard and clears inherited game/debug flags before explicit launch setup.

Direct boot probes resolve the source-build renderer libraries and use the same
validated native_launch environment, texture pins, worker/async controls, packet
batching, deinterlace and declared diagnostics as the packaged launcher. Missing
renderer libraries or changed texture assets fail before starting a process.
Probe receipts retain effective_environment, launch_assets and explicit headless
probe_overrides. The private disc/save root remains isolated; these probes do not
claim installed-app equivalence, player gameplay or smoothness acceptance.

## Required release scenarios

Use repeated `--require-scenario ID` arguments with `qualify-port` to require a
project's explicit acceptance set (for example `player-motion`, `audio-speech`,
`audio-music`, `window-resize`, and `pause-retry`). Missing, failed or unverified
requirements make qualification fail even when all structural checks and
runtime counters are clean. Empty requirements never mean complete acceptance.

A supplied acceptance file must name distinct scenario IDs, passed/failed/
unverified status, and hashed local evidence for each passed result. Its receipt
hash binds the review to the candidate. That receipt must inventory the launcher,
runner, all bundled frameworks, Info.plist and RunConfiguration.plist. A receipt
that pins only a renderer cannot qualify different executable code or settings.
`declared_acceptance_complete` means the required reviewed evidence is present
and no error finding remains. It does not independently certify the contents of
a review, audibility, motion quality or universal game compatibility;
`gameplay_verified` remains false for this static analyzer.

Native audio header reconciliation: when staging a source graph that contains the
native PCM backend, the engine recognizes the exact older bool-only readiness
header and replaces each staged alias with the owned PCM declaration. Originals
are preserved. Unknown variants block staging for review. A header-family source
contract rejects later conflicting declarations, including inline methods that
have the same layout but bypass initialization. The optimized split-translation-
unit regression reproduces the old silent path and verifies the corrected call.
This source-level check does not substitute for captured and reviewed game audio.

`qualify-port --expected-audio-wav /local/capture.wav --report /local/report.json` checks a decoded capture from a scene explicitly expected to be audible. Repeat for up to16 files. Entirely silent PCM fails qualification; malformed/truncated/unsupported input is rejected. Nonzero samples never certify speech, music, fidelity or correct timing. WAV data and filenames remain local; reports bind SHA-256 evidence.
