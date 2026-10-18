# Portable cadence analysis

`analyze-cadence` is an offline core command shared by the C++ and macOS targets.
It analyzes existing local telemetry; it does not instrument, launch or modify a game.
A renderer adapter must record one row per actual host present, not per draw or GPU submission.

```sh
python3 run.py --profile .private/profiles/example-game.json analyze-cadence \
  logs/presents.csv --name baseline --expected-field-hz 59.94005994005994
```

The CSV and output stay inside that profile's private game storage. Output is
`analysis/cadence/baseline/report.json`. Existing run names are never overwritten.
Use the expected rate for the disc and mode (for example 50 for PAL); do not use
an enhanced render target such as 120 as the emulated field rate.

CSV columns:

| Column | Meaning |
| --- | --- |
| `seconds` | Strictly increasing monotonic host timestamp in seconds. |
| `field` | Nondecreasing cumulative emulated vblank/field count. |
| `game_frame` | Optional cumulative simulation-update count; present on every row or none. |
| `hash_valid` | Optional `1` if this image comparison is valid, otherwise `0`/empty. |
| `image_changed` | `0`/`1` comparing the displayed image with the immediately preceding present. Required when hash_valid=1. |

The `seconds,field,hash_valid,image_changed` columns used by Urban Reign's
`PS2X_FRAME_TIMES` telemetry are accepted; extra columns are ignored. That
runtime is not bundled and its instrumentation has not been installed here.
Our existing private runner requires an exporter with these semantics before
its logs can be used. No inference is made from an FPS overlay.

The report separates host presentation Hz, emulated speed relative to the
requested field rate, optional simulation-update Hz and image-change Hz.
Intervals include median, nearest-rank p99 and maximum. The first row is a
baseline, not a new-image event. Invalid hash gaps are excluded from image-rate
coverage and never bridged into image-hold measurements. Incomplete first/last
image holds are not treated as completed samples.

Synthetic regression: 60 presents/s, 30 simulation updates/s and every second
image repeated reports 60 presentation Hz, 30 image-change Hz and normal
emulated speed. Doubling the emulated field counter reports 2× speed; it does
not claim a smoothness improvement.

These are observations, not gameplay qualification. A still menu can correctly
repeat images; moving HUD pixels can change a hash while the world stalls.
Identify actual player mode and the scene independently, keep the same inputs,
record instrumentation overhead, and compare equivalent windows before accepting
an optimization. Input latency and original-hardware fidelity remain separate tests.
