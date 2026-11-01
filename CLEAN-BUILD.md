# Source builds and historical input audits

`python3 run.py build-source recipe.json --output /path/to/new-build` validates
and plans a pinned source graph. Add `--execute` to compile, archive and link it
with one or two compiler workers. It rejects recipe shell commands, historical
objects/archives, escaping paths, unpinned include files, compiler injection and
overwriting outputs. Compiler dependency files are checked against the staged
inventory. All sources are rehashed after compilation.

A schema 1 recipe has `source_only: true`, `files` (relative path to SHA-256),
`units` and `targets`. A minimal unit is
`{"id":"main","source":"main.cpp","standard":"c++20"}`; a target is
`{"id":"program","kind":"executable","units":["main"]}`. Targets can use other
source-built targets through `uses`; static archives list source units directly.
Flags, includes, defines, SDK libraries and frameworks are allowlisted. Every
file exposed through a recipe include directory is pinned; SDK headers remain
toolchain inputs.

`python3 run.py audit-build --link-argv argv.json --compile-db compile_commands.json --directory /path/to/build --report new-report.json`
derives blockers from native linker arguments without executing them. Object
outputs map to sources by exact path, not unity basenames. Archives and unresolved
`-l` libraries require source targets or SDK resolution. Source mappings are
rebuild tasks, not proof they were rebuilt. Expand wrapper scripts/response files
into structured native arguments before auditing.

## Renderer carryforward

GPU sources carry full replacement mip tails, hardware filtering for compatible
replacement states, bounded asynchronous scanout, hot-path configuration caching
and exact nonwrapping VRAM transfer coverage. Shader decisions use actual filter
and wrap modes; unsupported region/nearest modes retain the manual path. True
VRAM wraps preserve conservative synchronization. These are executable decisions
rather than title-name instructions. Provenance: `runtime/gpu/ORIGIN.json`.

CPU regressions build separately with `cmake -S runtime/gpu/host-model -B NEW_BUILD`,
then `cmake --build NEW_BUILD` and `ctest --test-dir NEW_BUILD`. No retail assets.
The GPU CMake project builds the Vulkan component; its host adapter still needs
compatible host services and a complete title integration recipe.

This is candidate source carryforward. Claude's scripted Mavericks test is not
universal acceptance for every title, media/resize transition, FIFO photo or
input latency. Asynchronous target/shape transitions need qualification. The
experimental game-side bubble modification is not a generic correction and is
excluded. No command here replaces the official game app.

## Kelly reproduction remains incomplete

A fresh disc-to-current-app build is unproven. Historical runtime/raylib/IOP
archives and unity overrides need source closure; title-signature transforms,
native adapter linkage, texture-pack reconstruction and actual player/media
acceptance remain necessary. A passed source build always reports
`gameplay_verified: false`. Translation, linking and synthetic tests alone must
never be labeled a completed native game release.

## Stage source graphs and recover static libraries

`run.py stage-source-graph config.json --output NEW_DIRECTORY` collects declared
source roots, excludes binaries, rewrites declared absolute includes to relative
snapshot includes, pins originals and transformed files, and emits recipe.json
and staging.json. Root names select explicit include order for each unit through
`include_roots`; `force_include` names source headers rather than PCH objects.
Snapshots are retained on failure. Overlapping destinations and source symlinks
are rejected. Unrelated asset links are never copied or traversed.

`run.py audit-archive library.a --compiler-record compile_commands.json
--object-root /path/to/retained-objects --report new-report.json` parses BSD/GNU
archives without loading code, preserves duplicate members, and identifies exact
object-byte matches. It reports conflicting source candidates; neither filename
proximity nor object membership establishes source equivalence. Thin archives
are rejected.

Use `build-source RECIPE --output NEW_BUILD --unit UNIT_ID --execute` for bounded
source controls without linking every target. Compilation receipts record actual
compiler dependencies as well as object hashes. Full builds continue to link only
from their own objects.

## Native source graph integration

A private title profile can declare `native_source_graph` with `recipe`, `sha256`,
`staging`, `staging_sha256`, executable `runner`, distinct executable `tests`, and
a `frameworks` mapping from bundled dylib filenames to source-built shared targets.
Existing translation/knowledge/memory gates run before this branch. The pinned
staging ledger must cover the accepted generated sources, and compiler dependency
receipts must show they were actually compiled. Source declarations alone are
insufficient. Optional target `rpaths` are limited to bundle-relative paths.

`test-runtime` checks and runs declared conformance artifacts by hash. The native
packager checks private library paths/hashes, copies them into Contents/Frameworks,
and refuses to overwrite an existing app. Source graph conformance is required
before packaging. This plumbing still needs a complete title recipe, host adapter
and runtime configuration; it is not evidence of a complete Kelly clean rebuild.

## Recover compiler settings and revise pinned recipes

`run.py recover-compile-settings compile_commands.json --report NEW_REPORT.json`
recovers compiler settings without executing historical commands. Ordered `-I`,
`-isystem`, and `-iquote` paths keep their distinct search behavior. Unknown flags,
response files and frontend plugins stay blocked. `--source-pch` recognizes only
CMake's exact paired cached-PCH/source-header sequence and reconstructs the forced
source header, with its hash, instead of loading an old PCH. PCH-generation entries
remain unnecessary/blocked; reconstruction still needs compiled equivalence checks.

The source stager preserves legacy non-UTF-8 bytes and line endings. Relocated
parent-relative includes follow declared quote/include search paths; conflicting
resolutions fail. Unresolvable conditional platform includes remain literal and
are recorded as deferred. They cannot establish dependency closure. The builder
uses full `-MD` dependency records, including system-marked headers, checks project
files against the staged inventory, and records actual toolchain-header hashes.
Only the selected compiler's built-in search directories qualify as toolchain roots.

`run.py revise-source-graph RECIPE --ledger LEDGER --updates SETTINGS.json
--recipe-output NEW_RECIPE --ledger-output NEW_LEDGER` changes only existing units'
compiler settings. Outputs must be new files beside the same pinned source tree.
It preserves source provenance, carries a matching ledger, and invalidates prior
build/equivalence evidence. It cannot swap source files or target topology.

## Reproducible Finder launch configuration

A private profile may declare `native_launch` with an explicit `environment`,
`paths`, `framework_bindings`, `renderer_packet_batch`, `renderer_deinterlace`, and
`diagnostics`. Known presentation/audio settings are validated; automated input,
headless mode, unknown toggles and injected loader settings are rejected. No
title-specific workaround is enabled by default. This is configuration forwarding,
not proof that a particular title fix or effect is correct.

`paths` uses private-relative paths. A texture pack requires its content-inventory
SHA-256 from `launch_config.directory_pin`; the mutable shader cache is distinct
from source assets. `framework_bindings` can bind the graphics plugin/MoltenVK
environment keys only to receipt-verified source-built bundled libraries. The
launcher resolves those libraries from its current bundle after the app moves.
It clears inherited PS2 diagnostic/input settings, then forwards the explicit
configuration before execution. Package receipts pin configuration and assets.
Launcher compilation and actual app/player acceptance remain separate checks.

`run.py merge-source-graphs CONFIG --output NEW_ROOT` joins independently staged
source recipes/ledgers under distinct namespaces. Each input requires both pins.
Relative includes keep their tree structure, source provenance stays attached,
and explicit new targets can link the component targets. Existing outputs and
case-colliding graph names are rejected. Duplicate staged copies of the same
accepted generated file may satisfy native compilation through either pinned
copy; a declared copy without an actual compiler dependency still cannot pass.

The next-game resume path now rechecks native runners, conformance artifacts,
libraries, the source recipe/inventory, packaged runner/launcher/configuration,
and local texture-pack pins. Changed/missing products invalidate the owning
stage even when its old receipt JSON has not changed. Older packaging receipts
without actual product pins must be regenerated. This preserves resumability
without treating stale build caches as a fresh-disc reproduction.


## Reconstructing complete source linkage

`recover-make-plan` converts a Make dry-run transcript into declarative compiler
units and static targets. It never executes transcript commands. Unknown flags,
objects without source compile records, and repeated outputs block recovery.
Only declared source roots are then staged with `stage-source-graph`.

`compare-source-objects` verifies independently pinned historical controls;
`select-archive-sources` uses those controls to resolve candidate members. Exact
object matches and weaker native-text matches stay distinct. Multiple matching
sources and missing matches remain blocked. Historical objects are not linked.

`recover-link-target` converts structured linker argv and pinned source-product
mappings into a source target. It preserves explicit optimization/LTO options,
SDK frameworks, libraries, and bundle-relative rpaths. Historical LTO caches are
excluded. A direct object after an archive boundary is rejected rather than
silently reordered. Versioned object names are recognized by binary contents;
compiled payloads cannot masquerade as pinned source headers.

`audit-abi-layouts` compiles shared-record probes with each component's real
header search/forced-header settings. A native graph can declare an
`abi_layouts` object containing a private-relative `config` path and SHA256.
Those probes must target that same graph; mismatches block the native build.
The resulting receipt and configuration are pinned by resumable workflows.
These are ABI boundary checks, not gameplay proof.

Compiler source/patch/dependency and binary receipts prevent an old analyzer or
recompiler from silently surviving an engine policy update. Private processing
automatically refreshes stale compilers from existing locked sources using
fully disconnected CMake. Missing tools/dependencies require the public bootstrap
step. The output SDK audit independently rejects known incomplete stateful
libsd substitutions. Their original SDK bodies are compiled into native C++.

Engine compiler/build/control operations share an OS lock across installed tool
copies. External tools and active games still require the process guard and
coordination. macOS AppleDouble sidecars are excluded only when their binary
metadata header and pinned counterpart agree; actual compiler use of unpinned
metadata or headers still fails source dependency closure.

A source build is not a claim that a fresh disc conversion reproduces an
accepted game. The complete runner, media, renderer/plugin, launcher, assets,
configuration, and actual player/audio/temporal acceptance must all close.

## Source-owned graphics component and ABI checks

`bootstrap-native-graphics` provisions only the three public graphics source
revisions in `toolchain.lock.json`. `build-native-graphics --output NEW_OUTPUT`
validates a plan; add `--execute` for a fresh source build. Explicit `--source`
overrides must still have the locked revisions. The runtime revision, runtime
source tree, engine sources and dependency lock are pinned before and after the
build. Products remain candidates until the isolated runner and gameplay checks
pass. The component does not replace an installed game.

`audit-abi-layouts` also accepts `functions` entries alongside `records`. Each
function declares `function`, `returns`, `parameters`, optional boolean `noexcept`,
and at least two independent pinned source probes. For example,
`{"function":"gpu::Wait","returns":"void*","parameters":["std::uint64_t*","int"],"probes":[...]}`.
The compiler checks the exact function pointer type in each component, including
pointer versus value parameters and `noexcept`. Only simple qualified type names,
const qualifiers, pointers and references are accepted; arbitrary C++ expressions
are rejected. Overloaded functions require a separately named interface wrapper.
These checks establish a declared ABI contract, not pixel or gameplay equivalence.

The carried `gif-image2.patch` fixes IMAGE2 transport sizing, arbitration, VIF,
XGKICK, GS decoding and the native upload fast path together. It includes an
asset-free executable VRAM payload test for later runtime conformance builds.

## Pinned source amendments

Native components can be built automatically as part of `build-native`. A
`native_source_graph.native_components` entry may declare `kind: native_graphics`,
`framework: liblocal-native-gs-plugin.dylib` and `build.sources`. The sources map
must contain `volk`, `vulkan_headers` and `glslang`, each with an absolute local
`path` and `sha256` source-tree pin. Git revisions must also match the engine lock.
This performs no dependency downloads while processing private game input.

Provisioning keys its private build cache by source, runtime ABI, engine graphics,
builder, dependency lock and build-tool hashes. A valid source-build receipt may
be reused only after full verification; changed sources create a fresh build.
Failed attempts remain available for diagnosis, and one invocation starts at
most one new attempt. Existing receipt declarations remain supported. Packaging
rechecks the source-built artifact and its provenance. Provisioning is separate
from renderer or player acceptance.

The renderer carries canonical CPU-write tracking to prevent sampling stale
GPU targets when the same VRAM is reused for indexed textures. Its conservative
footprints include swizzled page extents, storage pitch, high-byte formats and
VRAM wrap. `PS2_PORT_HW_GS_STALE_TARGET_FIX=0` retains the old path for diagnosis.
`PS2_PORT_HW_GS_ADAPTIVE=1` opts into a one-way internal-scale reduction under
three consecutive sustained-stall windows. Live surface geometry follows the
reported scale. Recording keeps fixed scale; game/simulation clocks are unchanged.
These scheduling observations do not measure unique simulation or display FPS.

`isolate-tool-sources --output NEW_WORKSPACE` reconstructs locked public compiler
and dependency checkouts from existing local Git objects. It ignores input
working-tree edits, copies no prior compiler binaries, and performs no downloads.
Independent detached checkouts use Git's local pack transport. Recognizable macOS
AppleDouble sidecars beside real packs in the new checkout are removed and
recorded, then Git connectivity is checked; unknown files are preserved. The output receipt pins
each revision and source tree; staging is not compilation evidence.

For subsequent commands set `PS2_PORT_LAB_WORK` to that workspace. If build
tools already exist elsewhere, set `PS2_PORT_LAB_TOOLCHAIN` to their existing
toolchain directory; this avoids relocating virtual environments. Both values
are carried into pipeline child stages. The engine still verifies source and
policy pins and builds fresh analyzer/recompiler binaries before private inputs
are processed. Unknown source edits block patch upgrades before existing fixes
are removed. Existing game apps are not replaced by this operation.

`relocate-source-graph RECIPE --staging LEDGER --output NEW_DIRECTORY` copies a
verified source inventory into a fresh workspace, including external storage.
The recipe and staging ledger retain their exact hashes and source/link topology,
so revision-bound correction selection still works after relocation. Only pinned
source files and the recipe/ledger are copied; unlisted binaries and old build
products stay behind. Every source and copy is rechecked, and changed inputs,
overlapping outputs, existing destinations and case-insensitive path collisions
fail. `relocation.json` records progress and failures. Success means a relocated
source candidate, not a new compilation or original-disc reproduction proof.

`amend-source-graph CONFIG --output NEW_DIRECTORY` copies a pinned graph and its
staging ledger, then applies declared literal edits. CONFIG pins `recipe` and
`staging` with their SHA-256 values and lists `edits`, each with `file`, `sha256`,
`before`, `after`, and `reason`. Each before string must match exactly once in a
unique pinned file. The input graph is preserved. The output is revalidated as
source-only and records the previous file/graph hashes and correction reason.
A successful amendment is a source candidate, not a compiled or qualified build.
This supports reproducible recovery when a retained working object predates an
unshipped source edit, rather than silently building the newest file beside it.

An optional `title_revision` object pins an absolute local `executable` and its
`sha256`; an optional `source_provenance` pins a donor `path` and its `sha256`.
Both are checked before and after amendment. The ledger retains them, source
merges carry them forward, and native builds reject title amendments whose
revision differs from the selected profile. Generic amendments remain possible
without a title revision; private game corrections should always declare one.

## Automatic retention and replay of known source corrections

The engine can retain a correction set as private data and select it on a fresh
source graph, without an AI reapplying the edits:

```sh
python3 run.py retain-source-corrections /private/game/correction.json \
  --bank /private/game/source-corrections
python3 run.py apply-source-corrections --bank /private/game/source-corrections \
  --executable /private/game/disc-root/GAME.ELF \
  --recipe /private/game/fresh/recipe.json \
  --staging /private/game/fresh/staging.json --output /private/game/corrected
```

It can also discover all changed source files by comparing two retained graphs:

```sh
python3 run.py capture-source-corrections --executable /private/game/disc-root/GAME.ELF \
  --before /private/game/before/recipe.json --staging /private/game/before/staging.json \
  --after /private/game/after/recipe.json --output /private/game/captured.json \
  --bank /private/game/source-corrections
```

Capture requires identical file inventories and unit/target topology. It records
every changed file as an exact byte-preserving literal replacement and carries
the resulting graph's source contracts. Added files or changed build structure
require the existing staging/merge tools; capture refuses to guess those changes.
The `after` graph is a pinned source reference, not proof its behavior is correct.

Retention requires a pinned executable revision. Replay selects exactly one set
matching the executable digest, all edited source-file digests, and the complete
unit/target topology. Original absolute graph paths may change. Different source,
topology, game revisions, tampered records, or multiple ready sets stop replay.
Literal edits still require exactly one match, and declared source contracts are
validated in the resulting graph. The original graph stays intact; a pinned
configuration beside the new output records the replay. There is no shell or
native-code execution during selection. Builds and gameplay checks remain separate.

Bank records contain replacement source and may contain retail-derived material.
Keep them under private local game storage. They are not in the public source
allowlist. This is reproducible reuse of known corrections, not automatic discovery
of unknown bugs or proof that a new game behaves correctly.

A native source profile can now declare automatic correction preparation:

```json
{
  "native_source_graph": {
    "recipe": "fresh/recipe.json",
    "sha256": "<base recipe SHA-256>",
    "staging": "fresh/staging.json",
    "staging_sha256": "<base ledger SHA-256>",
    "runner": "game_runner",
    "tests": ["runtime_checks"],
    "corrections": {
      "bank": "private-source-corrections",
      "sha256": "<bank_sha256 returned by retention>"
    }
  }
}
```

`build-native` selects and stages the declared corrections before validating and
compiling the effective graph. The profile and base graph stay unchanged. The
engine keeps the derived graph under private `analysis/source-corrections/`,
indexed by input and implementation hashes. Reuse rechecks the bank, executable,
source recipe/ledger, correction configuration and corrected source inventory.
Next-game resume and packaging also verify the correction receipt; editing the
bank or prepared source invalidates the build evidence. There is no fallback to
uncorrected source when selection fails. Original generated-source/disc checks
and required source contracts still apply. ABI probes tied to a previous graph
must be regenerated for the effective graph; old ABI evidence cannot certify it.

### Platform SDK packaging inputs

The native source graph can separately declare the local MoltenVK platform
library needed by the renderer's explicit Vulkan loader binding:

```json
{"platform_sdks":[{"kind":"moltenvk","path":"native-dependency/libMoltenVK.dylib","sha256":"<local library SHA-256>"}]}
```

This field belongs inside `native_source_graph`. The file must stay inside private
game storage, have the expected library name, contain an ARM64 dynamic-library
slice, and pass dependency inspection. Symlinks, unrecognized SDK kinds, changed
hashes, and competing framework providers fail. Builds retain its provenance;
packaging rechecks it and copies it using the existing framework mechanism. No
download, installation, or library execution occurs during inspection.

SDK artifacts explicitly record `source_build_verified: false`. They are platform
dependencies, not source-built game code, and must not be counted as source-build
proof. An SDK declaration cannot admit a historical game runtime or object archive.

`build-source --target NAME` builds only the selected targets and all transitive
source dependencies. Repeat `--target` to include conformance tests or libraries.
It is mutually exclusive with compile-only `--unit`. Native graph builds choose
the runner, declared tests and packaged libraries automatically. Generated-source
coverage counts only units in the runner's linked dependency graph; unused units
and test-only compilation cannot stand in for a game runner.

A failed source merge can be resumed with `merge-source-graphs --resume`. Existing
files are reused only when their hashes still match the pinned inputs; changed
files are rejected rather than overwritten. The original failed receipt is kept,
and `progress.json` records copy/validation phases. Valid AppleDouble metadata
beside pinned files or source directories is excluded on exFAT; ordinary unpinned
files and any metadata actually consumed by the compiler still fail validation.

Fresh generated folders can resolve source origins by exact basename plus original
SHA-256 when their historical absolute path has changed. Intentional replacements
use `native_source_graph.generated_substitutions`: each entry pins the generated
`source`/`generated_sha256`, `staged`/`staged_sha256`, `executable_sha256`, and a
`reason`. The executable must match the profile's expected revision and the actual
local disc before compilation. A filename match or stale revision cannot activate
an override; the runner still has to consume the selected replacement source.

Source-graph runtime verification is tied to the exact built artifacts. Starting
checks revokes any earlier verification. Test execution removes inherited
`PS2_PORT_*`, `PS2X_*` and dynamic-loader injection switches, and records each
source-built test executable and its log hash. Packaging requires that successful
receipt to match the runner hash, recipe hash and complete declared test set;
changed test binaries, missing logs and stale verified flags block packaging.
These synthetic checks do not certify actual player gameplay or temporal quality.

`build-source --reuse-build PREVIOUS --reuse-recipe RECIPE` is an explicit
incremental mode. It accepts only this builder's directly compiled object receipts,
checks every object hash, requires an identical complete source inventory and
source root, and rechecks compiler/toolchain/header pins. Changed units compile
again. Reuse cannot chain through another cache, and is recorded as incremental;
it does not establish a fresh-build acceptance result. Link failures retain a
structured classification and missing-symbol list rather than inventing stubs.

The native host environment cache now invalidates immutable snapshots on
successful `setenv` and `unsetenv` calls. `putenv` disables caching because its
caller can edit the supplied storage later; Linux `clearenv` also invalidates.
The fixed-size cache falls back to libc at capacity. The CMake overlay includes
the same implementation in the runner and runtime tests on Unix; disable with
`PS2_PORT_CACHE_ENVIRONMENT=OFF` when investigating configuration behavior.
This avoids launch-only caches hiding configuration changes from conformance.


## Runtime source qualification follow-up

The source-selected runtime now passes the same synthetic checks with the public
MPEG stream callback and keyboard backends linked in. This includes synchronous,
worker and host-vsync modes, and 500 callback resets under an exhausted guest
heap. The callback fixture follows the compiler's SDK wrapper contract: guest
`ra` becomes `pc` before the synchronous service call. This is runtime behavior
coverage; the full converted game still requires independent acceptance.

Keyboard capture retains ordered GLFW transitions between presentation polls.
Expanding presses combine into a chord; ordered releases retain intermediate
states, including a held trick button after a direction is released. Synthetic
checks cover rapid complete chords, independent taps, aliases and chaining the
window library callback. Physical keyboard rollover and game trick eligibility
remain separate from this host delivery guarantee.

`test-runtime` takes the engine's shared compute slot and refuses an observed
active game/build/render process before modifying verification receipts. It
never stops another process. Successful conformance remains bound to the built
runner, recipe, test executable hashes and test-log hashes.


## GPU surface transport candidate

The GPU source now contains an explicitly opted-in macOS IOSurface producer
(`PS2_PORT_HW_GS_ZERO_COPY=1`) and typed C API. Surface leases retain backing
storage while the window uses it, even after renderer reset; serial IDs never
restart with a device. Held slots cannot be reused. Fence waiting and renderer
teardown share a mutex. The threaded renderer drains queued draws before a
surface submission. Non-Apple and unsupported paths report unavailable.

The metadata registry outlives static bridge teardown; renderers still destroy
all Vulkan resources, and balanced window releases destroy retained surfaces.
The producer library must remain loaded until its consumers have released all
leases. Record/replay keeps the existing pixel path. Geometry queries do not
advance the displayed-frame counter; a successful surface submission does.

The producer is connected through the native plugin, runtime latch and staged
window consumer. A fresh complete runner build and synchronous, worker and
host-vsync runtime checks have passed locally. The actual Vulkan-to-IOSurface-to-CGL
regression also passed with known-color draws, changing dimensions, exact serials
and renderer/device teardown before copying. These checks establish transport
correctness for the tested cases; installed-game, sustained gameplay and
performance acceptance remain separate requirements.

### Stage native macOS surface transport

`python3 surface_transport.py --recipe <recipe.json> --staging <staging.json>
--destination <new-source-graph>` stages the compiled game window and GS latch
with shared IOSurface presentation headers. It identifies unique compiled
`ps2_runtime.cpp` / `gs_frontend.cpp` sources; ambiguous inventories require
`--window` / `--frontend` relative source selections. Unrecognized source shapes
fail closed. Input graphs remain unchanged, all new headers are pinned, and the
result still requires a source build and gameplay acceptance.

The hardware owner uses tokens only for explicit `PS2_PORT_HW_GS_ZERO_COPY=1`
and packed HD presentation. Unsupported producer/geometry uses existing pixels.
Submitted token payloads hold publication leases through async capture and the
bounded pacing queue. Window GPU copies acquire separate leases. A token names one exact field; recycled tokens are rejected, never replaced by
another image. The window retries without committing its uploaded-frame stamp.
Acquisition precedes texture resizing. Consumer fences retain storage until GPU
completion; shutdown drains the live context before releasing the surface and
producer-library reference. Window/context destruction must follow this cleanup.
Recording continues through the existing pixel producer path. Private packed
pixel-dump requests are not serviced from token bytes. Photos/FIFO and actual
player temporal acceptance must still be qualified in the isolated app.

A runner graph may import a separately built native graphics component through
`native_source_graph.native_components`: each entry declares `kind` as
`native_graphics`, a private relative `receipt` path, its `sha256`, and the
bundled `framework` filename. The engine rechecks the component's complete source
inventory, locked dependencies, runtime ABI source, build tools, compile database,
and plugin bytes before import and again during packaging. Duplicate providers,
unfinished builds, changed inputs and paths outside private build storage fail.
This composes source-built components; it does not certify gameplay acceptance.

The producer flushes batched draws before looking up the display target, so the
first frame can create its target before submission. Crop bounds use the actual
physical target dimensions, including the renderer's existing small-target scaling
policy. `tests/test_mac_vulkan_surface.py` exercises this path with a real source-built
plugin and local Vulkan driver. Set `PS2_PORT_NATIVE_COMPONENT_PLUGIN` and
`PS2_PORT_NATIVE_COMPONENT_DRIVER` to their absolute local paths and run that
unittest under the shared idle guard/compute slot. Missing inputs skip this optional
platform test; release evidence must distinguish a skip from a pass.

### Required carried implementations

A source recipe can declare `source_contracts`: each entry has a unique `id`,
a source `unit`, a required link `target`, and nonempty `required_fragments`.
The inspector requires every fragment exactly once in that unit and verifies
that the target links the unit, including through its archives. Build receipts
record the satisfied contract IDs. A changed source provider, missing helper,
missing call site or disconnected implementation then stops the build.

Keep title-specific fragments and revision/signature data in the private recipe.
This checks literal source retention and link membership, not semantic or gameplay
equivalence. Pair it with behavior tests and an actual level-load check. In
particular, the port auditor rejects repeated blocked IOP RPC messages even if
presentation counters continue to advance; a responsive window is not proof that
the game finished loading.

Merged graphs preserve these contracts and namespace their unit/target IDs. A
private `native_source_graph.required_source_contracts` list can require specific
IDs independently of the source recipe, so replacing a recipe cannot silently
discard its declared obligations. These remain explicit private title policies.

### Declared runtime conformance modes

`native_source_graph.test_cases` optionally lists cases with `name` and
`environment`. The engine executes every source-built test target for every case
and pins both aggregate and individual logs. Only the synthetic runtime controls
`PS2_PORT_HEADLESS=1`, `PS2_PORT_GS_THREAD=0/1`, and
`PS2_PORT_HOST_VSYNC_TEST=0/1` are accepted. Unknown variables, duplicate/unsafe
names and omitted or changed case evidence block verification and packaging.
The test environment is scrubbed first; cases cannot provide commands. This
automates synchronous/worker/host-vsync checks, not gameplay acceptance.

A contract may name a pinned `file` included by its source unit (for example a
generated function included in a unity build). The compiler's recorded dependency
list must contain that file; merely storing or mentioning it does not satisfy the
contract. Merges remap these file paths alongside unit and target IDs. Explicit
amendments can update contract fragments while retaining all obligation IDs.

`PS2_PORT_HW_GS_SURFACE_METRICS=1` is optional local diagnostic telemetry. A
successful `hwgs_surface_wait` logs only after the producer fence is complete.
`cadence.analyze_surface_acquisitions` accepts a bounded log and optional serial
range. It reports acquisition intervals, rate and long gaps. It cannot report
monitor presentation or unique simulation FPS, and does not certify gameplay.
No image capture, hashing or full framebuffer copy is performed for these records.
The generic alpha-cut enhancement now also requires TEX0.TCC; vertex-only-alpha
draws must not be rejected or sampled differently using irrelevant texture alpha.

### Replaying qualified translation rules into retained sources

`native_source_graph.replay_translation_rules: true` enables a guarded source
rebase after private title corrections. It requires a newly accepted translation
with a sealed generated-file manifest and the current instruction-rule hashes.
Known EE instruction emissions are rewritten using opcode-qualified rules; IOP
sources remain 32-bit. Recognized scalar FPU headers are updated together with
call sites. Unknown helper variants and unknown generated-source differences
stop before staging. Title-specific hooks and alternate source copies survive.

Generated function tables may gain resume slots only when every previous mapping
and the entire surrounding C++ remain identical, slot indices agree with bounded
addresses, and each addition targets a previously registered function containing
the pinned instruction and an actual code label. This rule does not add unknown
functions or treat comments as labels. Only exact fresh-source matches receive
updated source-origin bindings. Preserved title alternates are reported separately
and never counted as fresh translation coverage. Rebase receipts bind original
and amended graphs, source manifests, executable identity and rule implementation.
These are source and build guarantees; they do not certify gameplay equivalence.

### Local enhancement asset intake

A pinned `native_launch.paths.PS2_PORT_TEX_PACK` may provide an absolute local
`source` directory in addition to its private `path` and logical `sha256`.
Packaging then stages the source automatically through `asset_intake.stage`.
The standalone `asset_intake.py` command supports the same inputs with
`--source`, `--private`, `--destination` and `--sha256`; it uses the shared
compute slot. No downloads or asset generation occur. Retail or privately
created texture data stays outside the public engine source archive.

Intake checks the logical manifest before copying, every copied file, the full
copied inventory and the source again before publishing the destination. It
rejects symlinks, escaping/overlapping paths and differing existing destinations.
Recognized AppleDouble metadata is excluded consistently with launch pins.
Failed copies and their diagnostic receipt are retained; source data is never
removed. A successful asset receipt proves a local copy, not that the texture
replacement is visible or correct in gameplay.

### Reviewed overrides and incremental source builds

Revision-specific `generated_substitutions` must name the sealed fresh source,
its actual retained replacement, both hashes, the executable hash and a concrete
reason. Compiler dependencies must prove that the runner consumes that replacement.
A byte-identical body under another filename can qualify without an exception;
its current pinned bytes must equal the complete fresh file. Names alone never
qualify it. Source amendments retain their original provenance.

`native_source_graph.source_lineage` is an ordered list of pinned
`source_corrections` and `translation_rebase` receipts. Every adjacent recipe
hash must join, and all prior inputs/outputs are verified again before building,
resuming or packaging. This allows further corrections to an already qualified
graph without erasing the earlier title and instruction-replay evidence.

`build_directory` selects a new private output path. Optional `source_cache`
contains private `build`, `receipt_sha256`, `recipe`, `recipe_sha256` fields.
Only this engine's directly compiled objects, matching compiler/SDK inputs and
unchanged unit settings qualify. Relative source inventory paths must be identical;
changed dependency bytes recompile that unit, while added/deleted paths reject
reuse because include resolution could change. Relocation preserves relative
layout. Build receipts distinguish reused verified units from fresh compilation;
this is incremental evidence, not a new clean-build claim. Prior outputs are kept.

Native build resume contracts exclude only `runtime_verified` and
`conformance_receipt`, which the runtime-test stage owns. Every other field and
all real build products remain pinned. Runtime-stage resume separately verifies
the full conformance receipt and verdict, so revoked or changed evidence reruns
tests instead of silently accepting it. Completing tests no longer invalidates an
unchanged source build, including when a later packaging step needs to be retried.

### Content verification and completed build recovery

On POSIX, pinned source reads use directory descriptors and no-follow opens.
Each file is still hashed on every inspection; timestamps never substitute for
content. Include audits use descriptor walks and reject symlinks, special files,
unpinned inputs and unrecognized metadata. Platforms without these operations
retain the original path checks. Historical source-origin paths are indexed as
provenance strings; only current inputs need filesystem resolution.

All name-and-original-content matches remain source alternatives even when a
canonical path exists. This preserves recorded source amendments that the runner
actually consumes. Exact-byte aliases and explicit revision substitutions remain
distinct. A build now writes `generated-coverage.json` with missing sources and
staged alternatives instead of reporting only a generic coverage failure.

`native_source_graph.completed_build` may name `directory` and `receipt_sha256`
for an already completed source build of the exact current recipe. The engine
revalidates source bytes, target/unit closure, compiler/SDK inputs, verified
source-cache provenance, objects and every linked artifact. No compiler command
runs and existing binaries are not replaced. This supports recovery when source
compilation succeeded but a later wrapper check failed. The resulting stage
explicitly reports adoption; it does not pretend to be a new clean compilation.

## Preserved extracted-disc intake

A verified extracted disc is a supported starting point; a raw ISO is not required
for this workflow. `import-preserved-copy --source DIR --manifest JSON --sha256 PIN`
(with the usual explicit per-game profile) copies exactly the manifest's files to
fresh private storage. File size/hash and boot identity are checked; duplicate or
escaping paths, symlinks, incomplete manifests and existing disc/saves are rejected.
Later saves/test files outside the original manifest stay at the source. Failed
attempts are preserved. The resulting normal disc manifest feeds verify/analyze/
translate. This proves file-copy identity, not original sector layout or gameplay.


Portable local apps can be produced with `package-accepted-app --bundle-assets`.
This requires the separate-save-root source contract to be compiled into the
runner and verified against its source-build receipt. A plist claim alone does
not qualify a source build. The package selects only manifest-pinned disc files
and inventory-pinned textures; extra files such as saves are excluded. Immutable
resources live inside the signed app. Saves, logs and shader cache use
`~/Library/Application Support/PS2 Port Lab/<bundle identifier>/`. Moving the app
does not change this persistent location. Existing saves are not migrated or
overwritten. An extracted, manifest-verified disc copy is sufficient; a raw ISO
is only needed by titles that require sector reads unavailable from extraction.

Interrupted correction staging is recoverable: an incomplete cache with the exact source, staging and executable identity manifest is preserved under an `interrupted` sibling name before a fresh staging attempt. Completed receipts are verified and reused; unknown or symlinked partial outputs block recovery. No partial source is accepted as a completed build.

Pinned incremental caches are followed to their directly compiled ancestor (at most 16 records). Every receipt and recipe pin is checked inside private storage; intermediate cached objects are never accepted. Source, dependency, object and compiler checks still determine reuse, and the resulting build remains incremental evidence.
