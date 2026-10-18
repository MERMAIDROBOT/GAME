# Executable accumulated knowledge

`knowledge.py` is an executable rule engine, not a prompt or notes search.
`knowledge.json` is its versioned bank. The next-title pipeline runs its contract
discovery stage; translation executes its applicable verified rewrites and emits
per-file hashes, matches and changes into private `analysis/knowledge-translation.json`.

The initial bank contains three executable capabilities extracted from the first
port: the opcode-checked R5900 SQRT.S operand correction, the memory-ownership
gate, and bounded IOP import-table discovery. The latter two prevent rediscovering
basic contracts, but do not implement missing IOP services or automatically choose
safe addresses. A configured memory collision stops the pipeline. Unconfigured
memory and unqualified imports appear as concrete integration gaps.

Rules select built-in audited handlers; JSON cannot run shell/Python code.
Shared rules contain no title addresses. Title rules require exact executable
fingerprints and live in private banks. Proposed rules never mutate translation.
Active rewrites need correction and idempotence fixtures; overlapping rewrites,
bad fixtures, unknown handlers, wrong opcodes and unexpected emission shapes fail.
All source files are validated before mutations begin. Original retail files are
never edited by this stage.

For an extension:

```sh
python3 pipeline.py knowledge-add --bank /absolute/local/bank.json \
  --candidate /absolute/local/new-rule.json
```

This validates and appends a new ID, increments the bank revision and preserves
the old bank by hash in `knowledge-history`. It rejects concurrent edits. To alter
an existing rule, review and version the complete bank explicitly rather than
silently reusing its ID. Fixtures prevent regressions; they are not independent
proof that invented instruction semantics are correct. Review new fixes against
binary/runtime contracts before activating them. A new *kind* of transformation
also needs a new handler and its tests, not arbitrary code stored in JSON.

The default bank is packaged in the reusable source archive and automatically
used by subsequent titles. A changed bank invalidates next-game resume contracts.
Private title-bank selection and automatic native adapter activation are still
unfinished; private rules can be tested through the API but are not implicitly
enabled by another game's profile.

Memory configuration is explicit in the selected profile:

```json
{"engine_memory":{"runtime_header":"build-inputs/State.h","layout":"memory/layout.json"}}
```

Both paths are relative to private game storage. The existing memory parser
checks ELF/BSS plus declared guest allocations against runtime reservations.
It cannot infer a game's entire dynamic heap from the boot executable.

The remaining Kelly lessons belong in shared runtime modules and regression
fixtures, rather than being shoehorned into text substitutions: EE/IOP ABI,
alarms/RPC, SPU2/movies, GS ingress/readback/CLUT, presentation clocks, input,
saves and packaging. Their current implementation/source-consolidation gaps
remain explicit. The engine will improve as verified fixes and runtime modules
are added; it does not train itself by reading notes, recover original C++
automatically, or yet produce playable arbitrary PS2 titles.

## Both products and graphics pipeline

`target_cli.py cpp` exports C++ without requiring the Mac runtime.
`target_cli.py macos` exposes the additional macOS workflow and rejects other
platforms for execution. Both import the same compiler and knowledge modules.
The PR matrix runs common tests for C++ on Linux/macOS and macOS on macOS.
Native game build support remains under consolidation; generic tests do not
certify retail gameplay.

`enhancements.json` contains28 ordered stages and the approved texture/FSR
policy. `enhancements.py` schedules them and qualifies successful-executor
receipts against the executable revision, graph hash, output hashes and parent
receipts. It is not a shell recipe interpreter. Unsupported executors remain
implementation_required; dependencies remain blocked. Existing artifact presence
or setting `UPSCALE=3` alone cannot certify FSR quality or preserved game speed.

Source recovery now has an executable `freeze-source` command; see [COMPILER.md](COMPILER.md). Unit recovery passes do not satisfy the full clean-build or gameplay evidence gates.


The revision-2 bank also corrects the pinned recompiler's **PMULTH** emission.
The original emitted adjacent multiply-adds and then summed all four lanes;
PMULTH instead retains eight independent signed 16×16 products in both HI/LO
banks and selects the even products for `rd`. The built-in handler in `mmi.py`
checks the exact opcode and known legacy emission shape before rewriting. It
snapshots both operands, preserves alias and register-zero semantics, and is
idempotent. Unknown emission shapes stop translation rather than receive a guess.

This lesson comes from the God of War port investigation, checked independently
against PCSX2's instruction implementation. Compiled synthetic tests cover signed
extremes, distinct lanes, aliases, zero registers and a failing original-emission
control. A local probe also passed the actual pinned recompiler output through
the correction and compiled/ran it with the actual runtime context. CI runs the
asset-free portable semantic tests, not a full God of War or Kelly Slater game.
The existing installed game and its generated source are not patched in place.
Changing the knowledge-bank revision invalidates downstream receipts by design;
future builds must retranslate and requalify rather than reuse an old acceptance.

## Engine improvement priorities — October 17, 2026

The VU workflow now includes [entry dependency analysis](VU-DEPENDENCIES.md) in
private reports and compilation manifests. This adds bounded diagnostic evidence
for priority 2, with explicit unknown results and no runtime optimization activation.

Implementation update: [scalar FPU corrections and compiled conformance](FPU.md)
now implement a bounded part of priorities 1 and 4. The revision-3 bank corrects
DIV/RSQRT emission, the native runtime receives reviewed scalar helpers, and
`instruction-check` reports exact results, declared deviations and failures
against 174 published hardware rows. This is not completion of the four tracks.

These are planned capability improvements, not implemented-feature claims.
Prioritize successful, accurate conversion of additional titles. Build speed is
supporting engineering work; loaded-code/guest-pointer work moves up when a
reproducible compatibility failure demonstrates the need.

| Priority | Capability | Direct engine benefit | Completion evidence |
|---|---|---|---|
| 1 | CPU instruction and floating-point accuracy | Fewer incorrect translations across titles | Compiled synthetic cases and independent expected results, with known deviations explicit |
| 2 | VU0/VU1 translation and coverage | More geometry/animation programs execute correctly through native code | Complete state, memory and graphics-event comparisons, observed coverage and safe fallback |
| 3 | Shared runtime behavior | More titles retain correct audio, saving, scheduling and native-service behavior | Versioned service contracts, original/native comparisons and isolated persistence tests |
| 4 | Automatic failure diagnosis and reusable rules | Subsequent conversions expose actionable gaps and reuse verified corrections | Deterministic failure reports, synthetic reproducers and revisioned shared regressions |

### 1. Instruction accuracy

Extend the existing SQRT.S and PMULTH regression mechanisms into an instruction
conformance runner. Compare generated code with independent expected values and,
where qualified, a reference interpreter. Cover register aliases and zero
registers, packed lanes, HI/LO, delay slots, likely branches, floating-point
special values, denormals and rounding.

Latest upstream float helpers are candidates, not a blanket upgrade. Their
current tests document skipped denormal cases and tolerated rounding differences.
A green upstream suite therefore cannot stand for bit-exact PS2 conformance.

Acceptance requires failing-before/passing-after controls and a machine-readable
deviation report. Unsupported instructions, unobserved comparisons, missing
fixtures and exhausted execution budgets remain explicitly unverified or failed.

References:
[PS2Recomp float tests](https://github.com/ran-j/PS2Recomp/blob/eb8688b2fd191129f103a39543002c005b33409c/ps2xTest/src/ps2_float_semantics_tests.cpp),
[PS2 hardware expected results](https://github.com/unknownbrackets/ps2autotests/blob/97469ffbed8631277b94e28d01dabd702aa97ef3/tests/cpu/ee_fpu/arithmetic.expected).

### 2. VU coverage and correctness

Extend the existing captured-program AOT workflow rather than build a duplicate.
Track program bytes, entry PC, semantic mode, observed comparisons and interpreter
fallback. Investigate VU0 microcode coverage and guarded VU1 deduplication.
A hash match alone is insufficient to select compiled code.

Qualify micro-PC wrapping, branch/VI backup, Q/P and flag latency, E-bit delay
slots, self-modification, floating-point contraction and ordered GIF effects.
A synthetic review of Devil Kings found an image-end delay-slot candidate larger
than micro memory; its runtime rejects it safely. Preserve that fallback while
fixing or adapting the analysis, rather than treating the finding as a proven
rendering defect.

Acceptance compares registers, flags, ACC/Q/P, memory and graphics events.
Unsupported programs must retain a safe path. Approximate throughput modes and
speculative worker execution stay disabled until strict comparisons pass and
measured player routes show a benefit at the correct simulation speed.

References:
[Devil Kings analysis](https://github.com/DavvidMac/PS2Recomp-DevilKings/blob/b65592e383703e6def6e840f289681ba512a8903/ps2xRuntime/tools/vu1rec.py),
[compiled-program guard](https://github.com/DavvidMac/PS2Recomp-DevilKings/blob/b65592e383703e6def6e840f289681ba512a8903/ps2xRuntime/src/lib/vu/ps2_vu1_compiled.cpp).

### 3. Reusable runtime behavior

Consolidate the existing generic runtime fixes before adopting another fork.
Define required guest memory/register effects, scheduling, interrupts, deferred
callbacks, clocks and module-version identity for each native replacement.
Retain original execution or a qualified fallback when recognition fails.

Validate audio channel/sample order, movie synchronization and underruns; thread,
timer and RPC behavior; memory-card protocol and ECC; and durable save updates.
Use synthetic fixtures and isolated copied cards, never destructive tests on
user saves. Test interrupted commits and reopening/remounting, not merely a
successful host file write. Correct menu audio does not qualify gameplay or movies.

Keep title addresses, library variants and exceptional behavior in fingerprinted
profiles. Generalize a fix only when its underlying contract is established.

References:
[native IOP module/service interface](https://github.com/Waffle565/God-Of-War-Native-Port/blob/49ca62c1d20b4659c61df6286c54e86edf411012/ps2xIOP/src/native/iop_native.h),
[PCSX2 memory-card implementation](https://github.com/PCSX2/pcsx2/blob/091ab68708a555198d80a7216965481c7ddd0d98/pcsx2/SIO/Memcard/MemoryCardFile.cpp).

### 4. Automatic failure diagnosis and accumulated knowledge

Extend existing translation acceptance, import audits, next-game failure tickets
and the versioned knowledge bank. Record unsupported opcodes/services, unresolved
dispatch targets, fallback use and unqualified assumptions with tool/runtime
versions and local module fingerprints. Reuse the existing player-mode and
cadence gates so a menu or attract demo cannot pass as playable conversion.

Discovery output is evidence, not automatically a correction. Candidate function
boundaries, pointers and structures retain confidence/provenance and unresolved
status until verified. Never accept an address as universal across titles or
overlays.

Every shared correction needs an independent behavioral basis, a synthetic
reproducer, a negative control, revisioned provenance and regression coverage.
Private reports stay local; public issues and PRs contain generic source and
synthetic fixtures only. A later title may add a rule or invalidate an assumption;
the engine does not train itself merely by collecting notes.

References:
[web compiler discovery](https://github.com/snuri00/ps2-web-recomp/blob/c628ae843209f6e4cef34ec04efb6267a1618882/recomp/analysis.cpp),
[miss reporting and dispatch](https://github.com/snuri00/ps2-web-recomp/blob/c628ae843209f6e4cef34ec04efb6267a1618882/runtime/src/dispatch.cpp).

### Implementation gates

Start with the instruction/FPU conformance runner and visible deviation ledger.
Then qualify a bounded VU improvement and a version-guarded runtime service
through that evidence framework. Extend failure reports alongside each change,
so verified lessons become executable tests and shared rules.

Both C++ export and optional macOS workflows use the same shared fixes and PR
stream. Keep the known-good application available. Adopt code selectively only
after license, dependency, edge-case and regression review; source inspection or
another title's reported FPS does not meet these gates.
