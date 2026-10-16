# One repository, optional platform targets

GAME contains the shared PS2-to-C++ engine and optional platform adapters. Users
clone once; contributors submit one PR. Common fixes reach every target through
the same versioned source and knowledge bank, without synchronization bots or
copied compiler implementations. No second repository or Git submodule is needed.

```text
GAME/
  run.py                    # one user entry point, cpp by default
  pipeline.py               # shared intake/analysis/translation stages
  knowledge.py / .json       # reviewed growing rule bank
  platforms/
    macos/
      adapter.json
      macos_adapter.py
  tests/                     # shared and platform contract regressions
```

```sh
python3 run.py --list-targets
python3 run.py doctor
python3 run.py --profile /local/game.json next-game --name first
python3 run.py --target macos --profile /local/game.json build-native
```

The macOS target requires macOS. Its current adapter delegates to experimental
native internals still in native.py; clean v56 source/build consolidation and
physical runtime extraction into platform-specific modules remain unfinished.
The legacy target_cli.py and explicit platform_adapter.py interfaces remain
available for scripts. Adding a target does not make arbitrary games playable.

## Add a device target

Add `platforms/<target>/adapter.json` with `schema: 1`, a unique stable `id`,
`core_api: 1`, supported host `systems` (Darwin/Linux/Windows), and a relative
Python `entrypoint`. Keep implementation next to its manifest. A supported host
is the computer running the compiler; describe the output device separately.

The entrypoint receives forwarded arguments and PS2_PORT_LAB_CORE pointing to
the shared engine. Discovery validates metadata without importing target code.
Execution runs installed local Python; this is not a sandbox. Review adapters
before running them. Unsupported platform/ABI operations must fail explicitly.

Contribute synthetic tests, dependency pins/notices and platform build/package
recipes. Qualify ABI, graphics, audio, controls, clocks and packaging separately.
Put game-independent corrections in the shared engine or knowledge bank with
provenance and regression fixtures. Keep revision-specific exceptions gated.
Never contribute retail assets, generated/decompiled retail code, saves, binaries
or raw game traces. Both shared code and adapters use the project's GPL-3.0 terms;
upstream dependency/model terms remain separate.

Every PR runs the shared synthetic suite and cpp/macOS target checks from that
same checkout. Required merge checks still depend on repository protection
settings. CI does not establish full gameplay fidelity or universal compatibility.

The former GAME-MACOS companion is superseded by platforms/macos in this repo.
