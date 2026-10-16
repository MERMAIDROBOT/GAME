# Local game data policy

Retail disc files, executables, extracted symbols, debug data, logs containing game details, generated game code, and compiled game artifacts stay on the user's Mac. Do not upload them to GitHub, web services, cloud compilers, online decompilers, or third-party AI tools.

Network access is permitted only for downloading public development tools, their dependencies, and documentation. The pipeline has no upload, push, publish, or telemetry command. Tool builds occur before game input is passed to tools.

Import, verification, executable inspection, analysis, and translation use local files. The pinned analyzer/recompiler source paths were checked for networking, upload, telemetry, and subprocess calls; none were found in the processing source. The wrapper checks the pinned source revision, permits only the analyzer/recompiler executables, and checks their processing source for common network/process APIs before each run. This check is a precaution, not a complete security audit.

On macOS the wrapper attempts a network-deny sandbox. This Codex host blocks nested sandbox creation, so the wrapper explicitly reports that OS network isolation is unavailable and runs the reviewed local file tools directly. The wrapper does not claim that OS-level network access is disabled in that fallback. Its own game-processing code contains no network requests or upload operations. For independent OS-enforced isolation, disconnect networking after bootstrap and before running `prepare`.

Game data, analysis results, generated code, and build logs are stored in a private directory outside the reusable source directory. The default for a new profile is `.private/games/<id>`; the Kelly Slater profile points at the already-preserved workspace copy. `.gitignore` is an additional safeguard, not a guarantee against a deliberate forced add. There is no configured Git remote.

The optional local debug `.app` is stored beside the reusable source directory. It contains generated game code and must stay local. It is explicitly excluded from the reusable source ZIP. Its launcher uses local file paths and no networking. Runtime provisioning uses public sources only; the FFmpeg build disables networking, and the reviewed runtime/IOP source has no matching common host networking or process-launch APIs. These source checks do not replace OS-level network isolation.

Before ever distributing the reusable pipeline source, review the actual tracked files. A safe-to-share pipeline does not imply that its game inputs or generated game artifacts can be shared. No game data needs to be sent anywhere to use these stages.

Captured vector-program images and generated vector C++ are retail-derived data.
They remain within private game storage, including owner-only compilation
output directories. The bundled vector compiler and runtime helpers are public
GPL-3.0 source; their upstream license is included. The reusable source archive
uses an explicit whitelist that excludes captured images, generated C++,
native binaries, scenario references, screenshots and raw traces.

Opt-in local controller diagnostics record mapped PS2 button masks, gamepad availability and monotonic timestamps, with bounded transition counts. They do not record raw keyboard characters. The launcher enables them only with LocalDiagnostics; resulting logs remain in private game storage and outside the source archive.
