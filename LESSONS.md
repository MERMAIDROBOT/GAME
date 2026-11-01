# Lessons carried into the next port

October 15, 2026 consolidation of GPT and Claude work. Local source references
below are relative to the project root, not dependencies to upload. The detailed
chronology remains `outputs/claude-review/WORKLOG.md`; permanent rule IDs remain
in `MACHINE-RULES.md`. Earlier solution documents are historical designs and
may describe already-fixed issues. No second-title compatibility claim is made.

| Area | Reusable contract and next-game check | Kelly evidence / limits |
| --- | --- | --- |
| Intake | Resumable checksummed import; distinct revision identity; never replace unreadable files with guessed online versions | `pipeline.py` import/verify tests; directory copy is not a sector ISO |
| Translation | Decode instruction words, not disassembler comments; explicit EE/IOP ABI, guest alignment, register widths and FP behavior | SQRT.S Ft correction tested; machine rules MR-IN/MR-TR contain additional proposed semantics, not all implemented |
| Source recovery | Keep decompile, readable reconstructed C++, generated dispatch C++, and executable behavior distinct | Source-of-truth translations are analysis inputs, not proof the shipped game links those readable files |
| Memory | Audit ELF/BSS, game heap, RPC buffers, callback stacks and runtime reservations for overlap | WORKLOG85: shared-runtime pools inside game heap caused loading/photo corruption. Kelly's chosen free addresses are title-specific |
| IOP/audio | Preserve EE/IOP argument widths, import results, alarm/thread progress, SPU2 AutoDMA state and PCM sample rate | Movie/audio contracts and WORKLOG62–82; nonzero PCM is insufficient to prove clean audible playback |
| Movies | Ring wrap, DMA coordinates, callback lifetime, drain and backpressure are ABI contracts | `MOVIE-AUDIO-DRAIN-AND-BACKPRESSURE.md`, `PSS-PCM-FRAMING-CONTRACT.md`; don't fix static/echo by changing global speed |
| Rendering | Reuse a pinned complete GS implementation; qualify raw ingress, ordered transfers, hazards, readback and render-to-texture | paraLLEl-GS + Granite + MoltenVK used by v56; early custom Metal experiments are historical |
| Presentation | Distinguish fields, full images, simulation ticks and refresh; choose weave/progressive by scene evidence | WORKLOG83–101; nonblocking publisher and staging-pool ideas did not improve the tested scene and were not automatically promoted |
| Performance | Measure CPU/GPU/queue waits and p95/p99 holds at equal settings; preserve content clocks | WORKLOG101/113: SSAA4 too expensive; v56 uses adaptive SSAA2. Reported flip cadence is not independently certified unique-frame FPS |
| 120 Hz | Budget the EE/VU work, then separate update/render deadlines; inspect per-call animation and rounded replay timing | GPT-NATIVE-120FPS-PLAN and WORKLOG106; native120 remains unproved, not a setting to inherit |
| Input | One host device per guest port, one-shot edges across scene changes, focus loss release, hardware bounce diagnosis | WORKLOG90/98/114; PS3 hardware bursts observed. A universal debounce filter was not implemented |
| Widescreen | Projection and both regular/guard culling planes must share effective FOV; avoid repeated widening | WORKLOG104 boat edge fix; engine addresses/hooks remain Kelly/NGL adapters until discovered semantically in another title |
| UI | Authored panel anchors, hit tests and labels need consistent projection | WORKLOG101 menu-pointer fix is title-specific; supersampling alone does not fix placement |
| Saves | Emulate descriptor semantics, asynchronous results and error codes; preserve user's card data | WORKLOG100: lowest free memory-card fd starts at0. v56 still uses external project save paths; user-dir migration is separate work |
| Texture extraction | TIM2 plus bounded ATEX parsing; match texels AND active palette with an explicitly named hash algorithm | 165 trace misses were ATEX water, not missing surfers; 675 SURFERS assets processed does not prove all variants rendered |
| Replacement | Original TEX0/TEXA/TCC, CPSM/CSA/CSM, exact upload footprint, hazard ordering, stale-write invalidation, mips, alpha | `tex-pack-coverage2..4` controls; qualified partial T8→T4 palette view does not make all256 T8 colors known |
| Upscaling | Local pinned model; full AI for suitable art,30% AI for words/numbers/important detail, faithful UI/alpha effects | Approved913/402/817 policy; animated water kept faithful to avoid flicker. Pack04 logo reclassification remains private, not v56-qualified |
| Texture evidence | Verify source preservation, dimensions/alpha, coverage and final visible replacement; don't confuse loaded/cache-returned with visible | Normal9-base-character roster + tint proof and player water in live6; locked outfits/boards and wetsuits not exhaustively qualified |
| Title repairs | Keep revision/context guards, reason, reproducer and removal criteria outside shared runtime | GAS, map ring, pier collision and NGL/UI hooks are title/engine adapters; pier veto is a workaround with incomplete live qualification |
| Packaging | Accepted binary hashes, explicit settings, stable identity/version/icon, notices, signatures, controls and launch-context smoke | v56 release shell preserves runner/plugin/MoltenVK; external asset dependencies remain local |
| Evidence | State-gated navigation and actual player influence; demo playback is a separate test | Scenario postconditions and OCR route corrected earlier mislabeled character captures |
| Storage/privacy | Private per-title outputs; serial GPU jobs, bounded traces, whitelisted reusable source archive | No retail assets, generated code, binaries, captures or raw logs enter the source ZIP |

## Promotion record for every fix

Record subsystem, source revision, symptom, first divergent state, cause evidence,
smallest reproducer, asset-free regression if possible, title-vs-shared scope,
before/after route, performance cost, rollback and remaining unknowns. A proposed
rule becomes shared only when its implementation and tests travel with the build.
Cross-title reuse remains provisional until another game exercises it.

## Second-title promotions (SSX 3, 2026-10-18)

These were found on SSX 3 and promoted as shared, asset-free fixes. Private details are in
the title's worklog and ENGINE-LESSONS.md. Kelly was A/B-checked (isolated builds, Oct 18): synthetic suites pass
on both stacks and boot/intro output matches; scripted gameplay was not reached (reference frames
are from another renderer).
That check is now `run.py --target macos ... ab-check` (ENGINE.md, "Cross-title A/B gate"): run it on
every title that boots before a shared patch or knowledge-bank change lands.

| Fix | Where | Symptom → cause | Test |
| --- | --- | --- | --- |
| VCALLMSR reads CMSAR0 | knowledge bank rev 4, rule `vu0-vcallmsr-cmsar0` (`vu0_macro.py`) | Rider fell through the course and the course was culled → translator emitted `ctx->vi[27]` (vi holds VI0–VI15); CTC2 vi27 stores `ctx->vu0_cmsar0` | `tests/test_vu0_macro.py` |
| Macro VU0 memory ops address VU0 data memory | knowledge bank rev 5, rule `vu0-macro-vu-memory` (`vu0_macro.py`) | SSX 3 side terrain culled: macro VSQI matrix uploads went to main RAM (fields swapped too), so the VU0 patch classifier read stale data | `tests/test_vu0_macro.py` |
| `ctx->vi[N≥16]` lint | `knowledge.apply_translation`, always on, fails closed | Catches any out-of-bounds VI access in generated code | `tests/test_vu0_macro.py` |
| VU MAX/MINI on raw bits, raw I immediate | `runtime-title-conformance.patch` | Integer vertex colours flushed to 0 → operands normalized before compare | `tests/test_title_conformance.py` |
| VU branch VI backup rules (PCSX2 `_vuBackupVI`) | same patch | JR/JALR used the backup (PCSX2 interpreter and microVU read the current VI; upstream test corrected); repeated writes kept the newest value. Stalls keep the backup (interpreter; microVU's opposite is a stated assumption) | same |
| PATH3: one GIF packet per MSKPATH3 pulse | same patch | Textures under the wrong CLUT (noise, wrong menu images, corrupt movie columns) → unmask flushed the whole frame's texture chain | same |
| Runtime heap reserve only for titles that own their stack layout | same patch | Kelly printed "out of memory" and exited on the shared stack → the SSX 3 heap-reserve fold took 1 MB from every title's "rest of RAM" heap; stack -1/heap -1 titles now keep the previous layout | same |
| Started threads inherit VU0 state; VF0 stays (0,0,0,1) | `runtime-title-conformance.patch` (`EeScheduler::startThread`) | SSX 3's player rider was invisible: its model was set up on a fresh thread whose zeroed context had VF0 = 0, so macro code building 1.0 from VF0.w produced zero inverse-bind matrices and the mesh collapsed | `tests/test_title_conformance.py` |
| Alarm syscalls 0xFD / -0xFE | same patch | ReleaseAlarm/iSetAlarm unhandled in ps2sdk numbering | same |
| Patch stack follows in-place edits | `native.apply_runtime_patch` records the applied stack in `.git/port-lab-applied-stack` | Editing a public patch broke build-native for every title (old content couldn't be peeled) | `tests/test_runtime_patch_stack.py` |

Open translator gaps (not fixed):
- Macro VMAX/VMINI use host `_mm_max_ps`.
- VDIV/VRSQRT ignore the numerator and return 0 on divide-by-zero.
- EE `C.cond.S` are host float compares.

## Remaining automation work, in dependency order

1. Freeze v56's full source/flags/dependency closure and reproduce its link in an
   empty build directory. Existing object/archive relinks must not be labelled
   clean source builds. Seed v41 recipes need updates, not blind reuse.
2. Extract signature-gated Kelly/NGL/GAS adapters, while consolidating shared EE,
   IOP, audio, GS and presentation changes into one buildable runtime tree.
3. Add executable instruction/import/stub coverage gates and integrate existing
   memory and ABI controls into the native build command.
4. Automate per-title state detectors, reference scenes and acceptance reports;
   use independent renderer/reference runs for parity. Whole-machine snapshots
   and automatic full-game acceptance remain proposed.
5. Add texture extraction/replacement capability discovery with the approved
   policy and explicit fallbacks; do not apply independent AI to animation frames.
6. Exercise a second owned title and track time-to-menu, time-to-playable,
   unsupported imports, adapter count, regression coverage and p95 frame holds.
