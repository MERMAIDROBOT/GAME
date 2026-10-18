# Scalar instruction correctness and conformance

The shared engine now carries executable DIV.S/RSQRT.S corrections, a scalar
runtime patch, and a compiled hardware-vector checker. This is the first bounded
implementation from the engine roadmap; VU coverage, full runtime/service
conformance and broad automatic diagnosis remain unfinished.

## What changes

`fpu.py` decodes the actual R5900 opcode and operands before rewriting the pinned
generator's known emission. RSQRT.S computes **fs / sqrt(abs(ft))**, rather than
the old **1 / sqrt(fs)**. DIV.S uses the scalar helper instead of emitting IEEE
infinity directly. Both corrections are idempotent, reject unknown emission
shapes, and preserve source-register aliases. DIV retains the existing generated
zero-divisor FCR31 update; this does not qualify complete exception-flag semantics.

The revision-3 knowledge bank activates the correction for subsequent translations.
It invalidates older translation/build evidence by the existing bank fingerprint
contract. Sources from an installed game are not edited in place.

`patches/scalar-fpu.patch` adds `ps2_port_scalar_fpu.h` to the pinned runtime and
routes scalar ADD/SUB/MUL/DIV/SQRT macros through it. Native bootstrap/build applies
the diagnostics patch followed by this patch. The helper is adapted from
PS2Recomp's newer implementation and additionally checks denormal operands before
taking ADD/SUB/MUL's fast path. Exponent-255 values, saturation, signed divide-by-zero,
flushed denormal inputs and sqrt of negative magnitudes receive PS2-compatible
result handling. It is not a complete FCR31, accumulator or VU arithmetic model.

C++-only consumers must use the corresponding patched runtime headers; exporting
generated C++ alone does not install or qualify a runtime. The helper source also
exists at `tools/ps2_scalar_fpu.h` for asset-free tests. A regression check requires
the patch's added header to be byte-identical to that tested helper.

## Run without a disc or Mac

Requires Clang or GCC with C++20 and C99 support. No model downloads, runtime GPU,
retail assets or network access are required by this command.

```sh
# Strict: any mismatch, including known rounding differences, exits nonzero.
python3 run.py instruction-check

# Explicit qualification with the known rounding deviations still recorded.
python3 run.py instruction-check --allow-known-rounding

# Separately maintained integer result model; all tested rows must agree.
python3 run.py instruction-check --backend reference

# Failing host-arithmetic/old-RSQRT control, for diagnosis rather than acceptance.
python3 run.py instruction-check --backend legacy

# New report file only; existing files cannot be overwritten.
python3 run.py instruction-check --backend reference --report .private/fpu-check.json
```

The same command is available through the optional macOS target. JSON reports
include compiler identity/version/hash, flags, host, input/source hashes, rounding
mode, all mismatches and explicit acceptance. Missing/extra/malformed result rows,
unknown operations, compile failures and execution failures cannot pass.

The fixture contains **174 published hardware result rows** across ADD, SUB, MUL,
DIV, SQRT and RSQRT. All rows execute, including the denormal class skipped by the
newer upstream test. A declared rounding difference is allowed only when the
observed result is exactly one encoded step from the expected value and the user
explicitly selected `--allow-known-rounding`; this is not a general tolerance.

On the initial Apple Silicon/Clang check, the scalar helper matched 164 rows
exactly with ten declared rounding differences and zero other mismatches. The
integer reference matched 174/174. The host-arithmetic control matched 85 exactly,
with five declared rounding differences and 84 other failures. These are result-
vector observations, not whole-game accuracy or performance measurements.

The scalar backend therefore **does not pass strict bit-exact acceptance**. Its
remaining rounding differences are visible rather than silently skipped. The
integer reference is used only for tests, not enabled in the game's hot path.
No speed improvement, physical-hardware rerun, full flag conformance or newly
qualified gameplay is claimed. VU operations and scheduling are separate work.

## Sources and licenses

- Scalar helper and DIV/RSQRT generator changes: `ran-j/PS2Recomp`, commit
  `eb8688b2fd191129f103a39543002c005b33409c`,
  `ps2xRuntime/include/ps2_runtime_macros.h` and
  `ps2xRecomp/src/lib/fpu_translator.cpp` (GPL-3.0).
- Hardware-vector collection: the same commit's
  `ps2xTest/src/ps2_float_hw_vectors.inc`, derived upstream from published
  `unknownbrackets/ps2autotests` EE FPU expected results. No new hardware run.
- Integer reference: `libretro/ps2`, commit
  `d4908f40fb32f43c5055852ecaed3c52aa725dce`,
  `pcsx2/ps2float.c` and `.h` (their file notices specify LGPL-3.0-or-later).
  Those files are preserved unmodified under `tools/reference/` with LGPL/GPL
  license texts. This dependency is deliberately separate from the candidate.

Future instruction fixes should add encoded-instruction and compiled execution
fixtures, independent expected results, failing controls and explicit limitations.
The current scalar checker does not alone certify instruction decoding, all
architectural state, flags, ACC, DMA, VU timing or gameplay.
