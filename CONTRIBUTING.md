# Contributing

GAME owns the shared compiler and versioned knowledge bank; GAME-MACOS is an optional companion. See [PLATFORMS.md](PLATFORMS.md) for the adapter contract and dependency-update policy:

```sh
python3 target_cli.py cpp --profile profiles/your-title.json next-game \
  --title "Your title" --source /local/disc --name first --through translate --execute
python3 target_cli.py macos --help
```

The C++ export target is host-independent at the Python interface; real toolchain
builds still need platform qualification. The macOS target additionally requires
Apple's build/runtime environment. It is not yet a complete automatic native-port
builder. See ENGINE.md for implemented rules and outstanding integration work.

All pull requests run the shared synthetic suite and both target interfaces in
one CI matrix. A fix goes into the common implementation once, not into two forks.
GitHub branch protection must require those checks in repository settings;
workflow YAML alone cannot enforce merge policy. No retail game is needed in CI.

Contribute opcode semantics, ABI/device implementations, asset decoders,
rendering/presentation modules, reference-independent synthetic regressions,
source reconstruction/build recipes, or revision-gated adapters. Add fixture
coverage and provenance with each knowledge-bank extension. Proposed knowledge
must not silently become active. Unsupported behavior must remain visible.

Do not submit disc images/assets, decompiled retail functions, generated retail
code, game binaries, screenshots or raw game traces. Use minimal synthetic
reproducers. Preserve upstream licenses and attribution. Publication uses an
explicit source whitelist; private workspace content is never recursively added.

Use issues for reproducible bugs, compatibility contracts or scoped capabilities.
Report expected/actual behavior, rule/runtime version, platform and synthetic
reproducer. Security issues should avoid public disclosure of exploit data until
a private reporting route is configured for the destination repository.
