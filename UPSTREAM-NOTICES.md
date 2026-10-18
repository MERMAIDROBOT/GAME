# Reusable source licensing and dependencies

## VU dependency analysis

`vu_dependency_core.py` adapts the usage decoder, control-flow and live-input
analysis from DavvidMac/PS2Recomp-DevilKings commit
`b65592e383703e6def6e840f289681ba512a8903`,
`ps2xRuntime/tools/vu1rec.py` (GPL-3.0). The adapter's source header records the
changes; `tools/PS2Recomp-LICENSE` preserves the GPL-3.0 text. No generated title
code or microprogram image is included. See [VU-DEPENDENCIES.md](VU-DEPENDENCIES.md)
for the analysis limits and the optimizations deliberately not activated.

## Scalar FPU implementation and independent test reference

`tools/ps2_scalar_fpu.h`, `patches/scalar-fpu.patch` and `fpu.py` adapt the scalar
helpers and DIV/RSQRT generator corrections from ran-j/PS2Recomp commit
`eb8688b2fd191129f103a39543002c005b33409c` (GPL-3.0). The hardware-vector collection
at `tests/fixtures/fpu-hardware.json` is reformatted from that commit's
`ps2xTest/src/ps2_float_hw_vectors.inc`; its original expected values come from
published ps2autotests EE FPU results. No retail code or new hardware capture is
included. Changes and qualification limits are described in [FPU.md](FPU.md).

`tools/reference/ps2float.c` and `.h` are unmodified from libretro/ps2 commit
`d4908f40fb32f43c5055852ecaed3c52aa725dce`, `pcsx2/ps2float.c` and `.h`.
Their original PCSX2 copyright notices and LGPL-3.0-or-later terms remain in the
files. `tools/reference/COPYING.LESSER` and `COPYING.GPLv3` preserve the license
texts. The model is compiled locally for conformance tests, not linked into the
game runtime or treated as proof of untested hardware behavior.

The reusable engine source is prepared under GPL-3.0; the license text is in
LICENSE. Existing upstream notices remain in copied/derived components. This
does not license any user's retail game assets or generated game-specific code.

`tools/vu1_recomp.cpp` derives from the pinned PS2Recomp project and retains its
notice; its license is also preserved as tools/PS2Recomp-LICENSE. Dependency
URLs/revisions are recorded in toolchain.lock.json. Public dependencies are
downloaded separately rather than vendored into this source export.

The first-title local app additionally uses paraLLEl-GS, Granite, MoltenVK,
raylib/GLFW and FFmpeg. Its dependency notices are in the app bundle. Later
consolidation must preserve the notices of every runtime module actually adopted.
FSR1 EASU integration and Real-ESRGAN models must retain their own upstream
attribution/license terms when incorporated. Model files are not included here.

Only explicitly whitelisted reusable source enters the source ZIP or a future
GitHub publication. Disc assets, retail-derived generated C++, binaries, captures,
logs and private title manifests are excluded.


PMULTH investigation credit: the God of War ports by Waffle565 and KIexster
identified the eight-product/HI-LO defect. The guarded correction here is an
independent implementation, checked against PCSX2 MMI.cpp. The recognized legacy
emission is from the pinned GPL-3.0 PS2Recomp compiler already credited above.
No God of War functions, disc bytes or generated game sources are included.

Periodic texture-context handling carries the seam lessons from the first local
port: wrapped inference, consistent baseline/alpha borders, animation families
and avoiding internal model-tile joins. No retail texture or trained model is
included. External GPU renderer candidates remain reference-only and are not
licensed or bundled by this source export.

## Bounded function recovery and original execution

`recovery_core/{emu,vu0,layout,r5900,symexec}.py` are unmodified MIT source from
Daniel Gomes Vieira's `ps2-port-engine` at
`01a93a35cf6653802970c292feaa11c707a04754`. `recovery_core/LICENSE` preserves the
notice and `ORIGIN.json` pins each source. `recovery.py` restricts the public
integer/RAM envelope and supplies an original checked C++ runner and private
reuse adapter. The VU/float code imported by the upstream module is not admitted
by this workflow. See FUNCTION-RECOVERY.md for qualifications and limits.

The private cross-title candidate normalization is an original implementation
informed by GT4 decomp's `tools/dedup.py` and `tools/crossgame.py` at
`ec4261f8572d377f13de6f37d3825a95051075af`. No GT4 source recovery, game code,
asset or compiler binary is bundled. A candidate hash does not prove equivalence;
target original/native comparisons remain mandatory.
