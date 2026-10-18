# New PS2 recompilation projects: week of October 12–17, 2026

Scope: repositories created Monday October 12 through Saturday October 17,
America/Denver, as checked on October 17. Dates below are local dates converted
from GitHub REST `created_at`. Creation/publication is distinguished from a
versioned release. GitHub does not expose an exact public-visibility date for
formerly private repositories, so creation is a proxy, not proof of first public
availability. Recently modified older projects and unchanged new forks do not
qualify. Discovery used date-filtered GitHub repository searches for PS2,
PlayStation 2, recomp and macOS; it is not a guarantee of exhaustive coverage.

Only selected public source files were inspected. Build, performance and gameplay
claims below are upstream claims unless explicitly labeled as our own test.
No retail files, generated game functions or upstream game binaries were imported.

| New repository | Created | Relevant work and decision |
| --- | --- | --- |
| [SirBraga/ps2-native-gs](https://github.com/SirBraga/ps2-native-gs) | Oct 15 | Most relevant reusable graphics candidate: Metal/Vulkan GS module, versioned ABI and scene replay tool. Inspected actual renderer, API, CMake and replay sources. GPL-3.0; no GitHub release at inspection. Evaluate separately; not an active dependency. |
| [thiagordsfx/urban-reign-recomp](https://github.com/thiagordsfx/urban-reign-recomp) | Oct 12 | PS2-to-C++ Windows port with runtime and diagnostic tools. GitHub v1.3.0 published Oct 13. Its presentation/image-cadence scripts prompted our independent portable cadence analyzer. GPL-3.0. Its gameplay and performance were not locally tested. |
| [koaque/Metropolismania-PC](https://github.com/koaque/Metropolismania-PC) | Oct 15 | Pinned upstream, local disc extraction, overrides and per-function capture/replay differential testing. Inspected pipeline layout and differential-test interface. README warns experimental/not playable; describes reaching a city scene. GPL-3.0; no release at inspection. Keep differential testing as an integration candidate; our terminal-VRAM replay is not equivalent. |
| [byescaleira/naruto-uzumaki-chronicles-2](https://github.com/byescaleira/naruto-uzumaki-chronicles-2) | Oct 12 | Title-specific Swift/Metal reimplementation for Apple platforms, not a general C++ translator. Publishes an R5900 REA profile patch. README explicitly lists missing animations, collision and battles. Inspected the patch; no source copied because no repository license was detected. Inspired architecture-evidence reporting only. |
| [DavvidMac/PS2Recomp-DevilKings](https://github.com/DavvidMac/PS2Recomp-DevilKings) | Oct 16 | New independently named runtime derivative (not marked a GitHub fork), with claimed VU/VIF scheduling, native IOP and Vulkan work. README inspected, GPL-3.0. A candidate for later instruction/runtime comparison, not a qualified replacement or measured speed improvement. |
| [acklavidian/decomp_wots2](https://github.com/acklavidian/decomp_wots2) | Oct 13 | Claims native C++ Linux/Windows reconstruction and high-refresh interpolation over fixed-rate simulation. README reviewed; no license detected. No code imported and no playability claim independently verified. |

Pinned inspected trees:

- ps2-native-gs: `063e1d3c7b15110d5ff905b51ee59c2c7edde935`
- Urban Reign: `2fa53c4dcfade75205f0384fceb0e72db3b9c2a2`
- Metropolismania: `1275522e3513c60eb1322f1a90ef89fdf2676c60`
- Naruto: `aa6d35a134d99454c98548d7d6a10208dd49d867`

Other newly created candidates found, not prioritized for adoption in this pass:
[SaintSeiyaRecomp](https://github.com/matpdev/SaintSeiyaRecomp),
[God Hand](https://github.com/Ryzellx/godhand-port),
[Shadow of the Colossus](https://github.com/TheRealestNwah/sotc-decomp),
[Monster Hunter 2](https://github.com/Batenkaitos-dev/mh2-decomp).
Their existence/metadata is not evidence of a completed translator or playable port.

Explicit exclusions: RoadTripAdventure-recomp's first releases were October 8
(previous week); the original PS2Recomp predates this week; PS2 Classics for Mac
creates PS3-compatible packages rather than translating PS2 to native Mac game
code; emulators, PS2-targeted homebrew, websites and unmodified forks are outside
scope. ReBlack-PS2's UTC October 12 creation falls on October 11 in Denver.

## Changes adopted here

1. `analyze-cadence`: independent, standard-library implementation separating
   host presents, emulated field speed, optional simulation updates and image
   changes. Supports Urban Reign's documented telemetry columns. Handles invalid
   hash gaps, short captures, counter resets and private output receipts. See
   [CADENCE.md](CADENCE.md). It analyzes supplied telemetry; it does not add
   telemetry hooks to existing game runners or certify gameplay.
2. ELF inspection now exposes flags, whether the R5900 machine marker is present,
   and a conditional Ghidra language candidate. A generic MIPS header alone
   cannot select the PS2 REA lane. Existing unmarked executables remain accepted
   for inspection and require independent ISA/ABI verification. The new Naruto
   patch is a reference, not applied code or a claim that REA runs here.

Our code is independently written; upstream GPL notices were not removed.
No external renderer, game-specific patch, title source or asset was vendored.

## Metal/Vulkan evaluation before adoption

The inspected module exports `black_parallel_gs_api` with API version 6. Its API
feeds GIF/register/image operations, memory/FIFO reads, scanout and optional
window attachment. This is not the `pgscap-cli-v1` process protocol supported by
our current replay command. Source and ABI compatibility must be established;
pointing the current backend JSON at this library is not sufficient.

A future isolated adapter must:

1. Pin source and build dependencies, retain GPL/BSD/MIT component notices,
   explicitly check ABI version and required function pointers.
2. Preserve feed ordering, readbacks, reset and lock ownership. Scanout has
   unlock/relock callbacks; their semantics require review before integration.
3. Translate the capture format explicitly or use this module's own gs-dump
   reader. Our terminal VRAM captures cannot prove its per-frame scanout fidelity.
4. Replay identical locally stored scenes through the existing renderer and new
   module at 1× with enhancements off. Compare frame sequences, alpha, palettes,
   framebuffer feedback and interlaced output. Cross-backend agreement alone
   is not original-console correctness.
5. Only after correctness, measure player-qualified presentation cadence,
   simulation speed, shader compilation stalls and input latency. Then evaluate
   resolution, texture replacement hashes, HUD handling and optional effects.

Upstream reports Metal/Vulkan comparisons across seven scenes, but those are not
our Kelly Slater results. The installed game and its seam-corrected texture pack
are unchanged by this research update.

## Deeper discovery and God of War follow-up

A second pass read all three pages of the date-filtered PS2 search, language-based
recomp searches (which find repositories with no PS2 description), project
metadata, README upstream links, runtime branches and the
[recomp.board directory](https://recomp.fyi/). Directory status is owner-supplied;
last-commit date is not repository creation or proof of playability. The findings
below use repository metadata and selected primary source files, not directory
labels as acceptance evidence.

Additional newly created projects:

| Repository | Created, Denver date | Finding / adoption decision |
| --- | --- | --- |
| [SirBraga/silent-hill-origins-recomp](https://github.com/SirBraga/silent-hill-origins-recomp) | Oct 16 | A directly relevant Mac-native release: v1.0 published Oct 16; Windows release also published that local date. Default branch is `macos`. Uses the separately published Metal/Vulkan renderer already pinned above. README claims playable; not independently played here. Repository license not detected, so no port code copied. |
| [Jeffsteelflex/nightfire-port](https://github.com/Jeffsteelflex/nightfire-port) | Oct 12 | PS2 reconstruction with Windows and a Mac CI build. Explicitly says the Mac build has not been played on real Mac hardware. Fixed-step simulation with interpolated rendering is a useful design reference, not native higher-rate gameplay proof. No repository license detected; no code copied. |
| [matthewperiut/night100](https://github.com/matthewperiut/night100) | Oct 16 | Scooby-Doo PS2 native project with Switch and a Mac development target. Claims 50–60 fps in early areas, later levels untested. GPL-3.0. Candidate for VU commit guards and zone regression methodology; no game-specific overrides or encrypted executables imported. |
| [danielgomesvieira2000/mister-mosquito-pc](https://github.com/danielgomesvieira2000/mister-mosquito-pc) | Oct 13 | Source/pipeline project with original-game comparison and per-title findings. README reviewed; work in progress. License needs further review before copying. No adoption based on screenshot checksums alone. |
| [ZackLucas/PS2Recomp-GOW2](https://github.com/ZackLucas/PS2Recomp-GOW2) | Oct 14 | A new GitHub fork of PS2Recomp, not evidence of a new completed God of War II port. Did not find a reason to replace our pinned compiler from its generic README. |
| [NabilPervez/def-jam-vendetta-recomp](https://github.com/NabilPervez/def-jam-vendetta-recomp) | Oct 15 | A new fork pointing to jcsix694's older upstream (created Oct 6), not an independent new engine. Follow upstream for changes; do not count the fork as a second success. |

Additional inspected pins: Silent Hill `81f6e906615a746a6467ffc88c262f53e41672aa`;
Nightfire `91b365001da726fd912274acc4873a9c8dd3f873`;
Scooby-Doo `d97ba7780e5d46f70136087009948a1050f3d9e0`;
Mister Mosquito `7260b46e153036318dc22bd3d1a42eced8121476`;
GOW2 fork `eb8688b2fd191129f103a39543002c005b33409c`.

### God of War: explicitly requested exception to the creation-date filter

[Waffle565/God-Of-War-Native-Port](https://github.com/Waffle565/God-Of-War-Native-Port)
was created October 11 and [KIexster/god-of-war-recomp](https://github.com/KIexster/god-of-war-recomp)
October 4 in Denver. Neither had a GitHub release when checked. They are reviewed
here because the user specifically requested them, not labeled newly created this
week. Waffle's runtime is a separate branch, not a second unrelated tool.

The useful verified lesson is **PMULTH correctness**. KIexster's preserved patch
shows its former translation collapsed independent signed products into one sum.
Our pinned `2c5fbb9` compiler has the same defect. The reference lane layout in
[PCSX2 MMI.cpp](https://github.com/PCSX2/pcsx2/blob/master/pcsx2/MMI.cpp)
independently confirms the intended HI/LO and destination behavior. Our compiled
synthetic controls reproduce the old failure and cover the corrected behavior,
including aliases, zero registers and signed extremes.

The shared executable knowledge bank now corrects only the recognized emission
under the actual PMULTH opcode. Unknown forms fail closed; original disc files
and installed game code are untouched. Local validation also generated a synthetic
ELF through our real pinned compiler, matched/corrected its output, and compiled
and ran the correction with the actual runtime context. No title binary was used.

Pinned God of War references: KIexster `cd1377ed9ff64600b771d170bdbe0955ef01752e`,
Waffle `34f30d5e432751369177af115104f4d19af6d080`.
Other ideas—native IOP/VU execution, zone sweeps, fixed-step interpolation, shader
caching, render-target reuse—need per-service or scene comparisons. They were not
copied wholesale or enabled on the assumption that another game's FPS generalizes.

### Seam handling is an engine stage

The enhancement graph now requires `texture-repeat-and-uv-seams` before alpha,
mip/coverage and runtime-loader qualification. `sr_batch.run` implements periodic
inference/baseline/alpha context, explicit animation families and post-processing
checks for repeated edges and declared cross-texture adjacency. Failed seams block
completion. Undeclared UV seams and mipmaps remain explicitly unverified.
See [TEXTURES.md](TEXTURES.md#seam-preserving-classified-ai-batches).
