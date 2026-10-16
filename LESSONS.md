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
