# Rebuilding the original executable

Goal: the engine's own representation of a game rebuilds the disc's boot executable byte for byte, first from
assembly, then function by function from source compiled with the game's original compiler. Every step is checked
against the disc's SHA-256.

## Stage 1: assembly that rebuilds the executable (done)

```sh
python3 pipeline.py --profile .private/profiles/TITLE.json rebuild-binary --name NAME
```

The command splits the boot executable into private storage (`binary-rebuild/NAME/`), rebuilds it from that
output alone, and fails unless the rebuild is identical:

- `asm/`: one file per loaded section. Code sections are split per original source file when the executable
  carries an ECOFF `.mdebug` table (headers excluded; their inline functions stay in the including file).
  Instructions come from `isa_r5900.py`, a table of 391 R5900 forms (integer, COP0, FPU, VU0 macro, MMI). Each form
  accounts for all 32 bits, and no two forms match one word, so decoding and encoding are exact. Words no form
  covers stay as `.word`.
- References are labels: branch and call targets; `%hi`/`%lo` pairs found by following each function's control
  flow (both branch paths, delay slots, in-function jumps, index additions, calls that replace caller-saved
  registers); `%gp_rel` with gp taken from the startup code's SetupThread call; data words that point into the
  image. Names come from `.symtab` when present.
- `layout.json` and `blobs/`: ELF headers as fields; bytes outside loaded sections (debug and symbol tables,
  padding) as SHA-256-pinned blobs.

`report.json` counts what is symbolic. `unpaired_address_like` counts high halves inside the image's address
range that no use resolved. These are candidate missing relocations, to drive toward zero before any function
changes size. `constant_like` counts ones built into constants with `ori`/masks.

Current local results (2026-11-04):

| Title | Instructions | Undecoded | `%hi/%lo` pairs | Unresolved address-like | Identical |
|---|---|---|---|---|---|
| Kelly Slater's Pro Surfer (SLUS_203.34) | 755,100 | 7 | 26,861 | 37 | yes |
| Ratchet & Clank (SCUS_971.99) | 116,161 | 5 | 4,659 | 33 | yes |

Kelly's 551 assembly files follow its source tree (`KS/SRC/AIController.cpp.s`, ...).

Tests: `tests/test_isa_r5900.py` checks table completeness, no overlaps, random-word and text round trips, and
known encodings. `tests/test_binary_rebuild.py` uses a synthetic ELF: identical rebuild; an edited instruction
changes exactly its word; a moved label moves its references; tampered blobs and reused outputs are rejected.

## Stage 2: relocatable assembly

Before a function changes size, every address must be a symbol. To do: resolve the remaining
`unpaired_address_like` cases; detect jump tables and pointer tables inside code; give data-only sections the same
per-file split; then build a shifted image (e.g. one section moved) and check it in the native runtime. A
byte-identical rebuild alone does not prove relocatability.

## Stage 3: matching source

Replace one function's assembly with C or C++ that the original compiler turns into the same bytes. That requires
the exact compiler the title used. Kelly's symbols name Sony's EE GCC 2.9x (`gcc2_compiled.`; its CFI records rule
out anything before 2.95). R&C is stripped. No such compiler is installed here. Getting one is a download that
needs the owner's approval. A recompiled function counts only when `rebuild-binary` still reports `identical`.

Generated assembly, blobs and reports contain retail code. They stay in private storage and never go into the
source archive.
