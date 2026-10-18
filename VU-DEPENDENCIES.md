# VU1 entry dependency reports

The engine now borrows Devil Kings' instruction-usage and live-input analysis
to explain which incoming state a discovered VU1 entry reads. It is integrated
into the private capture/compilation workflow, not a disconnected example.

This supplies evidence our existing AOT compiler did not emit: vector lanes,
integer registers, ACC lanes, Q/I and flag inputs that have not been replaced
before they are read. For example, writing only VF1.X does not remove the input
dependencies on VF1.Y/Z/W. These distinctions matter before reusing program work
or changing its scheduling.

## Run

No native runtime, Mac, GPU or game launch is needed to analyze an existing
capture directory. All captured images and reports remain private.

```sh
python3 run.py --profile .private/profiles/my-game.json \
  analyze-vu-dependencies .private/games/my-game/logs/my-capture/vu-code --name first
```

The command writes a new owner-only directory at
`analysis/vu-dependencies/first/report.json` inside the selected game's private
storage. Existing runs are not overwritten. It accepts 1–128 complete 16 KiB
`vu1_*.bin` images. Symlinks and paths outside that private directory are rejected.
The same shared command is available through the optional macOS target.

`compile-vu` now adds `vu-dependencies.json` to its private generated-source
directory. Its manifest pins the analysis implementation, input snapshots and
report hash. Validated resume checks those identities; changed analysis requires
archiving the old output and recompiling. Capture or compiler changes during a
compile prevent promotion of the result. Source images are never rewritten.
Modified reports fail validation even if generated C++ is unchanged.

Legacy compiled-source manifests remain readable for their existing serial
workflow; they do not acquire dependency evidence by implication. Re-running
compilation requires the current analysis record. The reports do not change
program registration, runtime lookup, interpretation fallback or execution order.

## What the report establishes

- `modeled` entries have a finite live-input result from this pinned decoder and
  analysis model, with visited-state counts and explicit input descriptions.
- Unknown results contain `inputs: null`, never an empty input list that could
  be mistaken for independence. State/entry budgets bound the analysis.
- Default budgets are 4,096 states per entry and 20,000 total per image, with at
  most 64 discovered entries. Reports identify incomplete analysis explicitly.
- Indirect branch targets and sticky-status writes whose assumptions have not
  been qualified are reported rather than promoted.
- Entry discovery covers PC0 and discovered E-bit resume points. It does not
  claim every possible MSCAL entry, memory-read dependency or termination proof.
- Every report sets `runtime_optimization_eligible: false`. Hardware accuracy,
  parallel execution and prefix reuse are not enabled by a metadata result.

## Validation and adaptation

Source: DavvidMac/PS2Recomp-DevilKings commit
`b65592e383703e6def6e840f289681ba512a8903`, `ps2xRuntime/tools/vu1rec.py`.
The borrowed decoder/analysis portion is GPL-3.0; its source pin and modifications
are recorded in `vu_dependency_core.py`. The license text is preserved in
`tools/PS2Recomp-LICENSE`.

We removed code generation and filesystem entry points, exposed visited-state
counts, required complete images, and fixed micro-PC wrapping in delay slots.
The upstream analyzer can otherwise construct a phantom pair at 0x4000 when the
E bit is in the last pair; the adapted analyzer wraps within the 16 KiB image.
The existing C++ VU compiler already wraps its PC and is not replaced.

Tests cover full/partial overwrites, Q inputs, analysis-budget exhaustion,
unsupported and indirect control flow, last-pair wrap, source preservation,
private paths, tampering and resume invalidation. Seeded ADD-only execution
perturbations independently check that inputs reported as irrelevant cannot
change written outputs in those finite synthetic cases. A negative control
changes a genuinely required input and observes changed output.

Those tests demonstrate useful dependency classification for the tested cases;
they are not full validation of the decoder, all flag latencies, PS2 arithmetic
or the complete VU instruction set. Use our existing compiled/interpreter
state-and-packet verifier before any future execution optimization.

## Other borrowing candidates: current decision

| Candidate | Decision | Evidence still needed before integration |
|---|---|---|
| Devil Kings dependency analysis | Added as bounded serial diagnostic evidence | Broader decoder/runtime perturbation tests before scheduling or reuse |
| Devil Kings code-prefix lookup | Not activated | Proof that each selected entry cannot execute omitted bytes; invalidation and full runtime comparison |
| Devil Kings parallel runner | Not imported | Complete register/flag/memory dependencies, ordered commit, stale reruns and measured player-route benefit |
| RAC / Metropolismania function oracle | Not imported wholesale | Original-execution backend and deterministic device/memory environment integrated with our runtime; hooks/masks and shared-helper blind spots qualified |
| Waffle native IOP interface | Not substituted for our IOP | Version-guarded implementation plus register/memory/callback-order tests; an interface alone does not improve behavior |
| Web runtime target feedback | Not imported as a parser-only feature | Actual unresolved-target producer in our runtime, overlay identity and alignment/executable-boundary controls |

These deferred items remain worthwhile candidates. Source inspection alone is not
proof that inserting them into this engine is a safe improvement. No FPS gain,
newly playable title or change to the installed Kelly Slater app is claimed.
