# Executable port qualification

`qualify-port` checks a packaged app and its receipt without launching the game. It verifies referenced files, hashes, source branch contracts, release configuration, and bounded optional log/GIF inputs. Scenario captures must be bound to the exact receipt. A passing structural report is not gameplay acceptance and never reports measured unique FPS from residency counters.

The compiler applies signed EE branch corrections only when decoded instruction records establish the low-64-bit signed branch contract. Unknown output shapes fail closed before writing translated files. This is instruction semantics, not title or scenery recognition.

`convert-disc --help` describes the mounted-disc conversion entry point. C++ translation and macOS packaging are separate targets. Native runtime tests and packaging do not prove arbitrary game compatibility. Keep the original disc and generated game sources local.

## Preserved runtime components

The patch stack includes IOP service/device work, VU0 macro handling, scalar FPU semantics, lazy timers, VIF unpack paths, native PCM submission, and host keyboard input. Native PCM output still consumes a compatible IOP/SPU producer; it does not remove every hardware compatibility layer. Host input sources preserve the qualified v7 behavior; unfinished v8 event capture is excluded.

`runtime/gpu` preserves the Vulkan/MoltenVK host renderer, worker residency optimization, shader and bridge source. The host adapter requires compatible GS/texture-pack APIs. Asset-free worker tests validate command ordering and residency behavior; they do not validate every shader, photo, title, or scene. Antigravity v8 depth-clear experiments are deliberately excluded.

## Acceptance

Require actual player gameplay, representative composed captures, audio listening, photos/FIFO, input chords, transitions, window/fullscreen behavior and saves before promoting a game build. Keep candidates separate from a stable app. No automatic game launch, retail upload or claim of universal native conversion follows from a passing source test.

`runtime/presentation/completed_field.hpp` carries immutable scanout ownership independently of a title adapter. Its 14 MiB lifetime fixture confirms resolution retains the shared future and does not copy the image. Adapters must still publish frame clocks and pixels together.
