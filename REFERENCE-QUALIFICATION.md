# Reference-driven translation checks

The converter can use a matching decompilation's function placements without
executing its scripts or substituting its C implementation. This is optional:
normal executable and overlay analysis remains available without a reference.

`overlay_segments.py` recognizes bounded four-word section-copy records from
the input container. A candidate must contain a coherent chain, explicit
zero-fill, non-overlapping memory ranges, a shared entry point and a code
file-to-memory mapping that independently agrees with the code census. The
compiler receives separate segments at their original load addresses. An early
code prefix omitted by the census is retained when the headers prove it.
Unknown containers retain the existing scan path; ambiguous records are rejected.

An optional `reference_functions` profile entry selects
`module-placements-tsv-v1`. It contains:

- `source`: a private-relative image path and its exact SHA-256.
- `placements`: an absolute metadata-file path and SHA-256. Each tab-separated
  row supplies name, kind, byte size, fingerprint, count, and comma-separated
  `module-index:hex-address` placements. Comment rows start with `#`.
- `modules`: pinned JSON files with `index`, `entry`, and section records
  (`addr`, `size`, `type`, `flags`). Allocated sections must match the recovered
  image exactly. Type 8 denotes zero-fill.
- Optional provenance fields such as repository URL and revision, retained in
  the recipe digest.

The engine verifies the source image and every metadata hash, matches each
module by its actual section layout, rejects conflicting or out-of-code
boundaries, and writes a compiler function map. Existing external maps are
preserved. Missing entries or an omitted reference module fail qualification;
stale accepted translations are refreshed before native linking.

Run the same check independently:

```sh
python3 run.py --target cpp --profile /absolute/game-profile.json audit-reference-overlays
```

The command writes `analysis/reference-entry-audit.json` in private game storage
and exits unsuccessfully if required entries are absent. It separately lists
modules with no reference. Passing means entry coverage in matched modules;
function semantics, hardware-service replacements, audio, timing and gameplay
need their own evidence. The report never marks a complete port from entry counts.

The initial external-format comparison used pinned Lombyte USA metadata and
local disc bytes. No level offsets, function addresses or reference C
bodies are embedded in the shared reader or importer. The same paths operate
on other inputs that satisfy the supported container and metadata contracts.
