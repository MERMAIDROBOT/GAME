# Shared compiler and optional platform adapters

`MERMAIDROBOT/GAME` owns PS2 intake, analysis, C++ translation, the versioned
knowledge bank, shared contracts and test fixtures. Its primary interface is
`python3 target_cli.py cpp ...`. Generated C++ is a machine-code translation,
not restoration of a developer's original source.

`MERMAIDROBOT/GAME-MACOS` is an optional macOS companion. It references an exact
core commit through a Git submodule instead of copying compiler code. The early
adapter delegates to the experimental native implementation still in core while
runtime source consolidation continues. Physical extraction of those older
macOS internals is unfinished; the adapter is not a new complete native backend.

## How changes propagate

- Shared fixes belong in GAME, regardless of where they were discovered.
- GAME's PR matrix checks both target interfaces, plus the latest macOS adapter
  against the candidate core on macOS. A broken adapter contract blocks that
  check; requiring checks before merge needs repository branch protection.
- GAME-MACOS tests its pinned core and latest core. A scheduled compatibility run
  repeats every six hours. Dependabot checks the core submodule daily and opens
  an update PR when its commit changes. GitHub may delay scheduled runs.
- Update PRs need review/merge. They are not automatically merged and propagation
  is not instantaneous. No cross-repository write token is stored or required.
- A macOS-only fix stays in its adapter. If it changes a shared contract, submit
  the shared fix to GAME first, test it against the adapter, then update the
  adapter's core pin. There is no competing copied core to synchronize backward.

## Add another target

Create a platform repository with an `adapter.json` declaring `schema: 1`, a
stable `id`, `core_api: 1`, supported host `systems` (Darwin/Linux/Windows), and a
relative Python `entrypoint`. Pin GAME as its `core` submodule. The entrypoint
receives arguments unchanged and `PS2_PORT_LAB_CORE` pointing to the shared engine.

```sh
python3 platform_adapter.py --adapter /local/target/adapter.json --describe
python3 platform_adapter.py --adapter /local/target/adapter.json doctor
```

Discovery reads metadata without importing adapter code. Running an adapter
executes explicitly installed local Python code; metadata validation is not a
sandbox. Review third-party adapters before running them. Platform adapters must
qualify ABI, graphics, audio, controls, clocks and packaging independently. A new
manifest alone cannot make translated C++ work on a device.

Contribute game-independent corrections as tested, provenance-backed rules;
proposed rules remain inactive. Use synthetic inputs in public PRs. Retail assets,
generated retail C++, saves, binaries and raw game diagnostics remain private.
