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

```sh
python3 pipeline.py --profile .private/profiles/TITLE.json match-function --function NAME \
  --source matching-src/FILE.c --compiler ee-gcc2.96 --name NAME-01 [--flag -O2 --flag -G0]
```

The engine compiles the function with a pinned original-era compiler and compares the result word by word with
the disc's function. Fields that the object's relocations fill (call targets, %hi/%lo, %gp_rel) are masked.
Compilers come from the decompme/compilers release. They live under `toolchain/ps2-compilers/` and count only if
their archive matches `SHA256SUMS`. They run in a Colima/Docker container from a digest-pinned 32-bit Debian
image, with no network and the compiler mounted read-only.

Kelly compiler identification (2026-11-04), using zlib 1.1.3 functions Kelly links (`adler32`, `zcalloc`, `zcfree`):

- None of the Linux builds (2.9-990721, 2.9-991111 and its variants, 2.96-001003) match under any flag tried.
  The closest, 2.96 with `-fno-optimize-sibling-calls`, differs only in the return-address save and restore:
  Kelly uses 128-bit `sq`/`lq`, the Linux builds use `sd`/`ld`.
- Kelly's debug paths are Windows paths (`C:/KS/SRC/...`). The Windows SN-style builds run under decompals' wibo
  (1.2.0, digest-pinned in `match_function.py`). wibo cannot start child processes in the container, so the
  engine runs the driver's stages itself: `cpp.exe` with the specs' predefines, `cc1`/`cc1plus`, then `as.exe`.
- **Result: `zcalloc` and `zcfree` match Kelly byte for byte with SN 2.95.2-273a and SN 2.95.3-114 at
  `-O2 -G0`.** 2.95.3-136 does not. `adler32` differs by one instruction: the build's GNU assembler adds a `nop`
  before a short loop's branch (an R5900 errata workaround), and Kelly's code has none. No `as.exe` option removes
  it, and SN's own `ps2eeas` crashes under wibo. Next: an assembler without that insertion (for example decompals'
  PS2 binutils), then C++ units, which go through `cc1plus`.

A recompiled function counts only when `rebuild-binary` still reports `identical`.

Generated assembly, blobs and reports contain retail code. They stay in private storage and never go into the
source archive.
