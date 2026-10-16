# Repeatable PS2-to-macOS compiler workbench

The first goal is to rebuild the improved Kelly Slater port from pinned source in an empty build directory. Then exercise the same workflow on another owned title, adding shared capabilities and regression tests. Different discs expose different SDKs, IOP modules, rendering states, overlays and timing requirements. No universal success rate is established by one game.

## Available now

The existing pipeline imports and verifies a local disc, inspects its ELF, runs static recompilation, checks generated C++, and has native build/test/package commands. It also supports private scene evidence, raw graphics replay and an explicit memory-ownership audit.

The new recipe layer consolidates provenance before rebuilding:

```sh
python3 pipeline.py --profile profiles/TITLE.json compiler-toolchain --name host-01

python3 pipeline.py --profile profiles/TITLE.json compiler-plan \
  --recipe compiler-recipes/REVISION.json --name review-01

python3 pipeline.py --profile profiles/TITLE.json compiler-stage \
  --recipe compiler-recipes/REVISION.json --name sources-01
```

Both commands validate the disc fingerprint, toolchain lock and every listed input hash. Staging copies source, scripts and configuration into a unique private directory; objects, binaries and evidence remain pinned references. Changed inputs, unsafe paths, malformed recipes and reused output names fail. Copy-time corruption retains a failed report.

These commands do **not** compile C++, execute recipe text, launch games or replace an app. They succeed when they produce a valid report, even when that report lists blockers. Inspect `clean_build_ready`: it concerns declared source readiness, not proven clean rebuilds or gameplay. Schema1 recipes are inventories, not executable build descriptions or guarantees that all transitive dependencies are listed.

## Recipe contract

Keep recipes in the title's private storage. Schema1 records `id`, `title_id`, `executable_sha256`, `toolchain_lock_sha256`, and `components`. Each component declares:

- `id`: a unique name.
- `scope`: `shared`, `title`, or `enhancement`.
- `qualification`: `synthetic`, `route`, `reported`, or `unverified`.
- `clean_source`: an explicit assessment of source reconstruction completeness.
- `files`: inputs with `root` (`work`, `lab`, or `game`), relative `path`, `role` and `sha256`.

Roles are `source`, `script`, `config`, `object`, `binary`, and `evidence`. Recipe text cannot supply shell commands. Declaring clean source does not prove it; later clean-build and conformance gates must do that.

`compiler-toolchain` fingerprints the selected local Clang/Clang++, linker, archiver, libLTO, driver tools and SDK settings, including developer/SDK selection and compiler versions. It installs nothing. A recipe may pin that private lock as `host_toolchain` using root/path/role=`config`/hash fields; inspection then rejects a different selected compiler or SDK. This complements dependency-file hashes and build flags, rather than proving complete toolchain hermeticity.

A component may pin a `dependency_closure` evidence file using the same root/path/role/hash fields. This records compiler-discovered inputs, frozen project paths and external compiler/SDK headers. Recipe inspection rechecks those hashes and rejects changed SDK files or closure evidence that failed isolation/object comparison. External SDK headers remain local toolchain requirements, not vendored source or evidence of complete runtime reproducibility.

## Lessons to carry forward

| Lesson | Required compiler/runtime capability |
|---|---|
| Compiled code can still call silent SDK stubs | Audit replacement boundaries and require reviewed implementation or explicit unsupported results |
| Runtime buffers can overwrite the game's heap | Audit ELF/BSS, guest allocations, stacks and runtime reservations |
| EE and IOP ABI conventions differ | CPU-specific argument/return handling with synthetic conformance tests |
| An unfinished RPC can become stranded | Bound execution without losing progress or changing clock ownership |
| Movie pointers and ring wrap are part of the ABI | Preserve callback lifetime, ring coordinates and explicit backpressure |
| Audio rate, simulation speed and display cadence differ | Verify each clock independently; buffer counts are not unique animation FPS |
| GPU reuse requires correct ingress and state ownership | Pin dependencies and qualify replay, synchronization and readback |
| Incremental success may not reproduce cleanly | Reconstruct exact source/flags; cached objects are evidence, not clean source |
| Title-specific repairs can remain useful | Revision/signature gates, tests and removal criteria |
| Enhancements can hide parity bugs | Separate enhancement settings from the correctness baseline |

## Ordered next milestones

1. Consolidate exact runtime, scheduler, recompiler opt-outs, IOP and renderer changes from the first-title recipe. Resolve source/object mismatches and inherited headers/flags.
2. Build in an isolated directory using pinned dependencies and explicit title adapters. Retain the current playable app as a regression reference.
3. Run memory/ABI/clock/device tests, confirmed player-mode scene checks and sustained play. Preserve original speed; measure unique frames and latency separately.
4. Package only that tested build with controls, notices, settings, signatures and private asset boundaries.
5. Repeat with a second owned title. Record new capability gaps and promote fixes to shared code when contracts and tests justify reuse.

Whole-machine snapshots, automatic stub/import coverage, full independent-renderer comparison and automatic clean-build/package gating remain unfinished. Earlier design notes include proposed targets as well as implemented features.

## Revision-gated title adapters

`pipeline.py stage-adapters --manifest compiler-adapters/REVISION.json --name adapter-01` validates a private schema1 manifest against the title ID, boot executable hash and each module's full input hash. Every edit has a file offset, expected original bytes and equal-length replacement bytes; optional guards verify other instruction/context bytes. All revisions and guards must pass before any patched copy is emitted. Overlaps, size changes and unknown input revisions fail.

Each adapter records its reason and removal condition. Outputs and reports stay under the private `adapter-runs` directory; the imported disc is untouched. Staging does not automatically activate a module in game loading or packaging. That integration and a regression run are required before removing an equivalent title-specific workaround from shared runtime code. Actual patch bytes/descriptors and patched modules stay out of the reusable source archive.

Disc assets, generated game code, private recipes, executables/objects, captures and reports remain local and outside the reusable source archive. Public dependency downloads are separate from game processing.

## Optional local texture stage

The `enhance-textures` command supplies the faithful resampling/cache baseline;
`sr_batch.py` and [TEXTURES.md](TEXTURES.md) add the later approved local
Real-ESRGAN policy: full AI for classified suitable surfaces,30% AI for
words/numbers/important details, faithful fallbacks for sensitive UI/effects.
TIM2 and ATEX decoders cover bounded layouts; per-title archive extraction and
semantic classification remain adapters. Animated water is kept faithful.
Kelly v56 loads a qualified private replacement pack through its GPU plugin;
that does not establish replacement compatibility for another title. Preserve
guest-VRAM identity, CLUT/alpha/layout guards, invalidation and scene acceptance.
An offline batch alone never establishes in-game improvement.

For the current automated intake/translation entry point and source-consolidation
gates, see [NEXT-GAME.md](NEXT-GAME.md) and [LESSONS.md](LESSONS.md).

## Recovering exact local compile inputs

`freeze-source` captures compiler-discovered project dependencies, copies them to
an isolated snapshot, pins compiler/SDK inputs, rebuilds, and compares native
code with a preserved reference object. Example (all paths are private/local):

```sh
python3 target_cli.py cpp freeze-source --compile-db /local/work/compile_commands.json \
  --unit /Component.cpp --source /local/work/Component.cpp \
  --reference /local/work/Component.cpp.o --name component-closure-01
```

The selector must identify one compile unit. Use a new run name for each attempt.
A passing report records whether the original-path object matches the reference
byte-for-byte and whether the isolated rebuild has matching native text. It also
rejects continued access to original project dependencies. SDK dependencies stay
pinned in place. This does not establish matching data/relocations, a complete
native link, runtime ABI correctness or gameplay parity.

Private first-title controls recovered the memory-card, FSR presentation texture,
and adaptive-supersampling GPU plugin units. Each original-path compile matched
its preserved object exactly; isolated native text matched. The full v56 runtime,
IOP and renderer archive closure still needs consolidation and a clean link.
