"""Local presentation telemetry analysis, independent of renderer and platform."""
import csv
import math
from pathlib import Path
import statistics

MAX_ROWS = 1_000_000
MAX_BYTES = 128 * 1024 * 1024


def finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Telemetry numbers must be finite') from None
    if not math.isfinite(number):
        raise ValueError('Telemetry numbers must be finite')
    return number


def counter(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError('Telemetry counters must be nonnegative integers')
    return int(value)


def distribution(values):
    if not values:
        return None
    values = sorted(values)
    return dict(samples=len(values), median=statistics.median(values),
                p99=values[max(0, math.ceil(.99 * len(values)) - 1)], maximum=values[-1])


def analyze(rows, expected_field_hz=60000/1001):
    """One row per actual present; field is a cumulative emulated vblank counter.

    seconds is monotonic host time. Optional hash_valid/image_changed describe
    comparison with the immediately preceding presented image, not GPU submits.
    game_frame is an optional cumulative simulation-update counter.
    """
    expected_field_hz = finite(expected_field_hz)
    if not 1 <= expected_field_hz <= 1000:
        raise ValueError('Expected field rate must be between 1 and 1000 Hz')
    parsed = []
    previous = None
    has_game = None
    for row in rows:
        if len(parsed) >= MAX_ROWS:
            raise ValueError('Too many telemetry rows; select a bounded capture')
        if not isinstance(row, dict) or not {'seconds','field'} <= row.keys() or None in row:
            raise ValueError('Malformed telemetry row')
        now = finite(row['seconds'])
        field = counter(row['field'])
        game = counter(row['game_frame']) if row.get('game_frame') not in (None, '') else None
        valid = row.get('hash_valid', '')
        changed = row.get('image_changed', '')
        if valid not in ('', '0', '1') or changed not in ('', '0', '1'):
            raise ValueError('Image flags must be empty, 0 or 1')
        if valid == '1' and changed == '':
            raise ValueError('Valid image comparison needs image_changed')
        if has_game is None:
            has_game = game is not None
        elif has_game != (game is not None):
            raise ValueError('Simulation counters must cover the entire capture')
        item = (now, field, game, valid == '1', changed == '1')
        if previous:
            if now <= previous[0] or field < previous[1] or (has_game and game < previous[2]):
                raise ValueError('Timestamps must increase; counters must not reset')
        parsed.append(item)
        previous = item
    if len(parsed) < 2:
        raise ValueError('Need at least two presented samples')
    span = parsed[-1][0] - parsed[0][0]
    intervals = [(b[0]-a[0])*1000 for a,b in zip(parsed, parsed[1:])]
    # Do not count the first image as a transition or bridge invalid hash gaps.
    known = changes = 0
    known_seconds = 0.0
    last_change = None
    holds = []
    for a,b in zip(parsed, parsed[1:]):
        if not (a[3] and b[3]):
            last_change = None
            continue
        known += 1
        known_seconds += b[0]-a[0]
        if b[4]:
            changes += 1
            if last_change is not None:
                holds.append((b[0]-last_change)*1000)
            last_change = b[0]
    field_rate = (parsed[-1][1]-parsed[0][1])/span
    return dict(schema=1, status='observed', samples=len(parsed), duration_seconds=span,
        presentation_hz=(len(parsed)-1)/span, present_interval_ms=distribution(intervals),
        emulated_field_hz=field_rate, expected_field_hz=expected_field_hz,
        emulated_speed_ratio=field_rate/expected_field_hz,
        simulation_update_hz=(parsed[-1][2]-parsed[0][2])/span if has_game else None,
        image_comparison_intervals=known, image_comparison_coverage=known/(len(parsed)-1),
        image_changes=changes, image_change_hz=changes/known_seconds if known_seconds else None,
        repeated_image_intervals=known-changes, completed_image_hold_ms=distribution(holds),
        gameplay_verified=False, original_fidelity_verified=False,
        scope='Observed host presents, emulated fields and optional image/simulation counters. '
              'No player-mode qualification, latency measurement, FPS acceptance or proof of unique scene motion. '
              'Static scenes legitimately repeat images; field rate is not simulation frame rate.')


def analyze_capture(profile, private, capture, name, expected_field_hz=60000/1001):
    from compiler import identifier
    from pipeline import digest, save
    identifier(name)
    private = Path(private).resolve()
    source = (private/capture).resolve()
    if not source.is_relative_to(private) or not source.is_file():
        raise ValueError('Telemetry inputs must be files inside private game storage')
    if source.stat().st_size > MAX_BYTES:
        raise ValueError('Telemetry capture exceeds size limit')
    before = digest(source)
    with source.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames) or not {'seconds','field'} <= set(reader.fieldnames):
            raise ValueError('CSV needs unique headers including seconds and field')
        try:
            report = analyze(reader, expected_field_hz)
        except csv.Error:
            raise ValueError('Malformed telemetry CSV') from None
    if digest(source) != before:
        raise ValueError('Telemetry changed while being analyzed')
    output = private/'analysis/cadence'/name
    if not output.resolve().is_relative_to(private):
        raise ValueError('Cadence output escapes private game storage')
    report.update(local_only=True, profile_id=profile['id'], capture_sha256=before)
    output.mkdir(parents=True, exist_ok=False)
    save(output/'report.json', report)
    return report


def analyze_field_trace(path, window_seconds=60):
    """Native swap trace: host_ns, completed_tick, generation, ingress_sequence.

    Completed stamps describe frame delivery, not pixel differences or simulation
    updates. Never bridge startup zeros or generation transitions into a hold.
    """
    path=Path(path);window_seconds=finite(window_seconds)
    if not 0 < window_seconds <= 3600 or path.stat().st_size > MAX_BYTES:
        raise ValueError('Use a bounded field trace/window')
    rows=[]
    with path.open(newline='') as f:
        for row in csv.reader(f):
            if len(rows)>=MAX_ROWS or len(row)!=4:raise ValueError('Malformed or oversized field trace')
            r=tuple(counter(v) for v in row)
            if rows and r[0]<=rows[-1][0]:raise ValueError('Host timestamps must strictly increase')
            rows.append(r)
    if len(rows)<2:raise ValueError('Need at least two trace rows')
    rows=[r for r in rows if r[0]>=rows[-1][0]-window_seconds*1e9]
    if len(rows)<2:raise ValueError('Window too short')
    holds=[];changes=0;known_ns=0;previous_change=None;resets=0
    for a,b in zip(rows,rows[1:]):
        if not a[1] or not b[1] or a[2]!=b[2]:
            previous_change=None;resets+=1;continue
        if b[1]<a[1]:raise ValueError('Completed tick regressed within one generation')
        known_ns+=b[0]-a[0]
        if b[1]!=a[1]:
            changes+=1
            if previous_change is not None:holds.append((b[0]-previous_change)/1e6)
            previous_change=b[0]
    swaps=[(b[0]-a[0])/1e6 for a,b in zip(rows,rows[1:])]
    span=(rows[-1][0]-rows[0][0])/1e9
    rate=changes/(known_ns/1e9) if known_ns else None
    hold=distribution(holds);swap=distribution(swaps)
    flags=[]
    if hold and hold['p99']>50:flags.append('completed_field_delivery_hitches')
    if swap['p99']<34 and hold and hold['p99']>50:
        flags.append('regular_host_loop_does_not_prove_smooth_game_delivery')
    return dict(schema=1,samples=len(rows),duration_seconds=span,host_swap_hz=(len(rows)-1)/span,
        host_interval_ms=swap,completed_stamp_changes=changes,completed_stamp_change_hz=rate,
        completed_hold_ms=hold,invalid_or_generation_boundaries=resets,findings=flags,
        gameplay_verified=False,unique_pixel_fps=None,simulation_fps=None,
        scope='Completed-field stamp transitions at host swaps; no pixel, player-mode or simulation FPS proof.')


def analyze_surface_acquisitions(text, *, start_serial=None, end_serial=None):
    """GPU-fence-ready acquisitions for one consumer, NOT display/game FPS."""
    import re
    if not isinstance(text,str) or len(text)>MAX_BYTES:raise ValueError('Select a bounded surface log')
    for value in (start_serial,end_serial):
        if value is not None and (type(value) is not int or value<1):raise ValueError('Invalid surface serial range')
    if start_serial is not None and end_serial is not None and start_serial>end_serial:raise ValueError('Invalid surface serial range')
    pattern=re.compile(r'^\[native-surface acquired\] ns=(\d+) serial=(\d+) width=(\d+) height=(\d+)$')
    rows=[];observed=0
    for line in text.splitlines():
        if not line.startswith('[native-surface acquired]'):continue
        match=pattern.fullmatch(line)
        if not match:raise ValueError('Malformed surface acquisition record')
        observed+=1
        if observed>MAX_ROWS:raise ValueError('Select a bounded surface interval')
        ns,serial,width,height=map(int,match.groups())
        if ns<=0 or serial<=0 or not 1<=width<=16384 or not 1<=height<=16384:raise ValueError('Invalid surface acquisition dimensions or counters')
        if (start_serial is not None and serial<start_serial) or (end_serial is not None and serial>end_serial):continue
        if rows and (ns<=rows[-1][0] or serial<=rows[-1][1]):raise ValueError('Surface times and serials must increase for one consumer')
        rows.append((ns,serial,width,height))
    intervals=[(b[0]-a[0])/1e6 for a,b in zip(rows,rows[1:])]
    duration=(rows[-1][0]-rows[0][0])/1e9 if len(rows)>1 else 0
    return dict(schema=1,status='observed' if len(rows)>1 else 'insufficient_samples',
        scope='GPU-fence-ready surface acquisitions before the window copy; single consumer',
        samples=len(rows),duration_seconds=duration,acquisition_hz=(len(rows)-1)/duration if duration else None,
        acquisition_interval_ms=distribution(intervals),intervals_over_40ms=sum(value>40 for value in intervals),
        first_serial=rows[0][1] if rows else None,last_serial=rows[-1][1] if rows else None,
        dimensions=sorted(set((r[2],r[3]) for r in rows)),unique_game_fps=None,monitor_presentation_hz=None,
        gameplay_verified=False)
