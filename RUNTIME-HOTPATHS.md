# Runtime hot paths and scalar comparison correctness

The shared patch stack carries three independent runtime candidates derived from
verified runtime work, without any retail code, private watch addresses or asset
window hooks. They apply to the common runtime used by subsequent conversions.
They are not a certification of arbitrary-title gameplay or a threaded IOP.

* VU tracing flags are cached on first use in DMA/VIF paths. Set the environment
  before runtime startup. `PS2_PORT_ENV_CACHE=0` restores dynamic trace lookups.
  Private guest-dispatch watches are deliberately not imported into the engine.
* Exact lazy EE timers defer accounting only before the next possible interrupt.
  IO register access and timer-deadline queries flush accounting; timer writes
  invalidate the deadline. Pending cycles belong to each PS2Memory instance.
  `PS2_PORT_LAZY_EE_TIMERS=0` selects eager accounting.
* Common eligible VIF1 UNPACK streams use a fast path: no mask, MODE 0, equal
  CL/WL, complete input and no destination wrap. Other cases retain the general
  interpreter. `PS2_PORT_VIF_FASTPATH=0` selects that general path.

`test-runtime` now builds and compares eager/optimized timer traces over five
seeds, including two interleaved memory instances and timer-deadline queries,
and compares VIF memory/ROW/COL hashes over 15,000 randomized streams. An empty
trace, failure in either process, or difference fails qualification. Synthetic
fixtures do not prove hardware equivalence, full VIF command coverage, or a
retail performance improvement.

Scalar DIV/SQRT use the existing opcode-guarded translation rules and runtime
helpers. PS2 exponent-255 encodings represent extended finite values; some are
IEEE NaNs when a host float comparison sees them. Therefore EE EQ/LT/LE macros
must compare the same decoded values as the arithmetic helpers. The comparison
patch does this for these three instructions. `PS2_PORT_FPU_COMPARE=0` restores
the previous IEEE host comparisons for differential diagnosis; it can restore
the clamp bug and is not recommended for normal use. Flags, ACC, unsupported
compare variants and VU semantics remain separate contracts.

The synthetic special-meter-style test verifies both signs of the numerator
and signed-zero divisor, subsequent arithmetic, and a lower/upper clamp through
the actual comparison macros. The old IEEE NaN clamp is a failing control. No
retail SpecialMeter implementation or game identifiers are shipped in tests.

The C++ and optional macOS workflows share this source and patch stack. Runtime
bootstrap or a full native rebuild is required; copying a helper into an already
compiled app cannot change its instruction semantics. Do not change shared
headers underneath a shipped precompiled-header build. Reproduce and rebuild
from a snapshot instead.
