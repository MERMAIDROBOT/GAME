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
