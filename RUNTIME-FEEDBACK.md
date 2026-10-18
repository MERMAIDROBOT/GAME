# Observed runtime targets to compiler hints

The shared C++ and macOS workflow can now turn existing private missing-target
logs into a candidate analyzer configuration, then explicitly use that config:

```sh
python3 run.py --profile .private/profiles/example.json collect-runtime-targets \
  --log logs/diagnostic.log --name attempt-1
python3 run.py --profile .private/profiles/example.json translate --feedback attempt-1
```

Recognized producers are the existing pinned runtime's
`[guest-branch:missing-target] ... target=0x...` diagnostic and PS2 Web Recomp's
`[ee] no translated code at 0x...` diagnostic. Ordinary numbers, unrelated log
addresses and bare `entries.txt` tokens are not imported. The native producer
reports the first miss once per reset/run; this command does not make it exhaustive.

Candidates must be four-byte aligned and entirely within file-backed executable
PT_LOAD bytes of this exact main ELF. BSS, data, unrecognized overlays, addresses
outside the executable, virtual aliases and malformed segments are excluded.
Existing analyzer entries remain intact. Compiler entry hints use the backend's
existing `name@0xaddress` syntax; this adds discovery evidence, not stub handlers.
The base analyzer config is preserved. The alternate config/report lives in a
new, private `analysis/runtime-feedback/<name>/` directory. Existing runs are not
overwritten. Size/target budgets bound processing; source assets are never edited.

Before translation, the command rechecks the executable, profile identity,
original config, log, toolchain lock, feedback implementation and candidate config. It regenerates the
candidate list/config to reject forged changes and records the selected feedback
receipt in the translation stage. A changed source requires a new feedback run.
Existing translation/build/player-route gates continue to apply.

**Scope:** these are static main-image compilation candidates. Hashes bind local
inputs; they do not authenticate logs or prove live memory was unchanged. Reports
explicitly set runtime_code_identity_verified, module_generation_verified,
functions_verified and playable_certified to false. A data word that happens to
be in an executable segment can still be a false candidate. Self-modified code
and relocated overlays require a future memory/module-generation producer.
No new game run or automatic retry loop is launched; no-new-targets means no
eligible candidates, never a passing game. Stop reasons, route completion,
performance and runtime code identity still need their own evidence.

Design reference: [PS2 Web Recomp dispatcher](https://github.com/snuri00/ps2-web-recomp/blob/c628ae843209f6e4cef34ec04efb6267a1618882/runtime/src/dispatch.cpp)
and [exploration loop](https://github.com/snuri00/ps2-web-recomp/blob/c628ae843209f6e4cef34ec04efb6267a1618882/scripts/port.sh).
This is an original adaptation of the workflow concept; no upstream implementation
was copied. Synthetic tests cover rejection/drift, both log formats, config
preservation and the real shared translation dispatch retaining failure gates.
