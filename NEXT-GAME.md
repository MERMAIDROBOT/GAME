# Next owned PS2 disc: automated intake and translation

Updated October 15, 2026. These commands run locally. They do not upload assets,
download games, call AI services, or claim an arbitrary disc is playable.

One-time public-tool setup, separate from processing retail input:

```sh
python3 pipeline.py doctor
python3 pipeline.py bootstrap
python3 pipeline.py bootstrap-runtime
```

For a new game, choose a distinct ID (and a separate profile for each revision):

```sh
python3 pipeline.py --profile profiles/next-title.json next-game \
  --title "Next title" --source /Volumes/DISC_NAME --name first --through translate --execute
```

This command creates a missing profile and pins its boot executable revision,
imports/resumes the disc copy, verifies checksums, reads BOOT2
and the MIPS ELF, runs the executable knowledge contract/import stage, analyzes functions, generates C++, applies the versioned rule bank including the known SQRT.S
operand issue and runs representative native syntax checks. It stops at the first
failure with a local ticket and log, rather than continuing with invalid output.
`--through intake` stops after inspection; omitting `--execute` only records a plan.

Reports live in the profile's private `next-game-runs/first/report.json`.
An IRX hash census and file-format inventory accompany successful runs. This is
not automatic IOP import coverage: that still needs module-level analysis.
To resume after an interruption with the same mounted source and unchanged tools:

```sh
python3 pipeline.py --profile profiles/next-title.json next-game \
  --source /Volumes/DISC_NAME --name first --through translate --execute --resume
```

Resume re-verifies disc files, validates saved output hashes, and reuses matching
expensive stages. Changed profile, executable revision or pipeline implementation
requires a new run name. Changed stage outputs invalidate that stage and later
stages. Keep the same source argument on resume. If the disc is unavailable, start
a new run without `--source` to use the verified local copy.

The runner holds a shared workflow lock and checks for running game/build/GPU
processes. It does not interrupt another worker. Direct legacy commands and
external tools do not yet participate in the lock; keep those idle yourself.

## From translation to a playable app

Generated C++ is a static translation, not recovered original authoring source.
The generic native build path exists, but the later Kelly v56 runtime and renderer
improvements are not yet all consolidated into that clean-build path. Automatically
using a Kelly-linked binary or copying Kelly addresses into another title would
produce a false success. The runner therefore reports `integration_required`.

The next implementation milestone is a source-complete shared runtime release,
with a pinned build description consumed by `build-native`, and explicit title
adapters. Existing `compiler-plan`/`compiler-stage` validate source provenance but
do not execute complete clean-build recipes. Their blockers remain real gates.

Once integrated: run memory/ABI/device tests, state-gated menu and actual-player
routes, movie/menu/gameplay audio, pause/retry/save/load, and independent speed and
frame-pacing comparisons. Keep original-behavior and enhancement profiles separate.
Only promote a tested app. A timeout, input ACK or nonblack screenshot cannot
automatically certify playability. See [LESSONS.md](LESSONS.md).

`package-accepted-app` provides a reusable local packaging shell for an already
accepted GPU bundle: preserved engine hashes, icon, numeric version, explicit
settings, diagnostic isolation, launch errors, duplicate-launch lock and ad-hoc
signature verification. It references the private disc/texture directories;
it is not a portable asset installer or a substitute for gameplay acceptance.

## Full-process enhancement engine

The next-game run also emits `analysis/enhancement-engine-plan.json`. This is a
28-stage dependency graph including local AI skin/texture work, CLUT/alpha/mips,
widescreen/culling/UI, adaptive supersampling, FSR1 EASU, audio, controls, saves,
correct speed, pacing, app packaging, rebuild preservation and approved cleanup.
Completed concrete stages emit revision/output/predecessor-pinned receipts.
Changed evidence invalidates dependent stages. Missing handlers or unqualified
outputs never pass just because a report exists. The current graph schedules
the whole process; several native/runtime/enhancement executors still need
consolidation. It is not yet automatic execution of all28 stages.
