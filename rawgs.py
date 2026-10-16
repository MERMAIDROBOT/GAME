"""Strict streaming validation of private raw GS recordings; no network operations."""
import hashlib
import struct
from pathlib import Path

VRAM_SIZE = 4 * 1024 * 1024
MAX_SIZE = 8 * 1024 * 1024 * 1024
NAMES = {1:'reset', 2:'gif', 3:'register', 4:'upload', 5:'unsupported', 6:'end', 7:'fp_environment', 8:'native_gif'}

def records(path):
    path = Path(path)
    if not 16 <= path.stat().st_size <= MAX_SIZE:
        raise ValueError('Capture size outside bounded format')
    with path.open('rb') as stream:
        header=stream.read(16)
        if header == b'PGSCAP1\0' + struct.pack('<II',1,0): version=1
        elif header == b'PGSCAP2\0' + struct.pack('<II',2,1): version=2
        else: raise ValueError('Capture header invalid')
        sequence, ended, environment_seen = 0, False, False
        while header := stream.read(16):
            if ended or len(header) != 16:
                raise ValueError('Trailing or truncated record header')
            op, length, number = struct.unpack('<IIQ', header)
            if op not in NAMES or number != sequence + 1 or length > 16 * 1024 * 1024:
                raise ValueError('Invalid record type, sequence or size')
            if sequence == 0 and op != 1:
                raise ValueError('Capture must start with VRAM reset')
            payload = stream.read(length)
            if len(payload) != length:
                raise ValueError('Truncated record payload')
            if op in (1, 6) and length != VRAM_SIZE:
                raise ValueError('VRAM snapshot size invalid')
            if op in (7,8) and version != 2:
                raise ValueError('Extended record requires PGSCAP2')
            if op == 1: environment_seen=False
            if op == 7:
                if length != 8 or struct.unpack('<Q',payload)[0] > 0xffffffff:
                    raise ValueError('AArch64 FP environment invalid')
                environment_seen=True
            if version==2 and op in (2,3,4,8) and not environment_seen:
                raise ValueError('Graphics command missing host FP environment')
            if op in (2,8) and (length < 16 or length % 16):
                raise ValueError('GIF packet must contain complete quadwords')
            if op == 3 and (length != 16 or struct.unpack_from('<Q',payload)[0] > 127):
                raise ValueError('GS register record invalid')
            if op == 4 and length <= 32:
                raise ValueError('Upload missing transfer data')
            if op == 5:
                if not length or length > 1024:
                    raise ValueError('Unsupported-operation marker invalid')
                try: payload.decode('ascii')
                except UnicodeDecodeError: raise ValueError('Unsupported-operation marker invalid') from None
            sequence = number
            ended = op == 6
            yield op, number, payload
        if not ended:
            raise ValueError('Capture has no complete footer')

def validate(path):
    counts, unsupported = {}, {}
    initial = final = None
    total = 0
    incomplete_gif = 0
    for op, sequence, payload in records(path):
        name = NAMES[op]
        counts[name] = counts.get(name,0) + 1
        total = sequence
        if op in (2,8) and not gif_packet_complete(payload): incomplete_gif += 1
        if op == 1 and initial is None: initial = hashlib.sha256(payload).hexdigest()
        if op == 6: final = hashlib.sha256(payload).hexdigest()
        if op == 5:
            reason = payload.decode('ascii')
            unsupported[reason] = unsupported.get(reason,0) + 1
    with Path(path).open('rb') as stream: version=2 if stream.read(8)==b'PGSCAP2\0' else 1
    return {'format_version':version,'host_fp_environment_recorded':version==2,
            'ingress_api_distinguished':version==2,
            'eligible_for_strict_cpu_comparison':version==2 and not unsupported and not incomplete_gif and not counts.get('upload',0),
            'status':'complete', 'replayable':not unsupported and not incomplete_gif and not counts.get('upload',0), 'record_count':total,
            'counts':counts, 'unsupported_operations':unsupported,
            'incomplete_gif_packets':incomplete_gif,
            'native_upload_translation_required':bool(counts.get('upload',0)),
            'initial_vram_sha256':initial,'final_vram_sha256':final,'local_only':True}


def gif_packet_complete(payload):
    """Check complete tag/payload boundaries for isolated replay, not GS semantics."""
    cursor = 0
    while cursor < len(payload):
        if cursor + 16 > len(payload):
            return False
        tag = struct.unpack_from('<Q', payload, cursor)[0]
        loops = tag & 0x7fff
        flag = (tag >> 58) & 3
        registers = (tag >> 60) & 15 or 16
        if flag == 0:
            size = loops * registers * 16
        elif flag == 1:
            size = ((loops * registers + 1) // 2) * 16
        else:
            size = loops * 16
        cursor += 16 + size
        if cursor > len(payload):
            return False
    return cursor == len(payload)
