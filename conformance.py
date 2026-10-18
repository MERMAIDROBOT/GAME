"""Asset-free compiled scalar-result conformance; explicit deviations, no hidden skips.

Expected results are published hardware observations, not calculated by the
candidate. The integer reference backend is independently maintained code, not
proof of every PS2 instruction, flag, accumulator or VU timing behavior.
"""
from pathlib import Path
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
OPS = ('add', 'sub', 'mul', 'div', 'sqrt', 'rsqrt')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_vectors(path=None):
    data = json.loads(Path(path or ROOT/'tests/fixtures/fpu-hardware.json').read_text())
    if data.get('schema') != 1 or not data.get('provenance') or not data.get('cases'):
        raise ValueError('Missing conformance provenance or cases')
    for case in data['cases']:
        if case.get('op') not in OPS or type(case.get('upstream_known')) is not int or case['upstream_known'] not in (0, 1, 2):
            raise ValueError('Unsupported conformance operation/deviation class')
        if any(not isinstance(case.get(k), str) or not re.fullmatch('[0-9a-f]{8}', case[k]) for k in ('a','b','expected')):
            raise ValueError('Expected explicit 32-bit hexadecimal operands/results')
    return data


def assess(cases, outputs, allow_known_rounding=False):
    """All rows participate. A declared rounding difference must be exactly 1 bit-step.

    The upstream denormal exclusion (class 2) is deliberately NOT an allowance.
    Unknown/missing/extra outputs are errors rather than unobserved passes.
    """
    if not cases:
        raise ValueError('No conformance comparisons observed')
    if len(outputs) != len(cases):
        raise ValueError('Missing or extra conformance result rows')
    details=[]; exact=known=failed=0
    for i,(case,actual) in enumerate(zip(cases,outputs)):
        if not re.fullmatch('[0-9a-f]{8}', actual):
            raise ValueError('Malformed conformance result')
        if actual == case['expected']:
            exact += 1
            continue
        # This is an explicit inherited qualification, not a general tolerance.
        declared = case['upstream_known'] == 1 and abs(int(actual,16)-int(case['expected'],16)) == 1
        known += int(declared); failed += int(not declared)
        details.append(dict(index=i,**case,actual=actual,status='known_rounding_difference' if declared else 'failure'))
    accepted = not failed and (allow_known_rounding or not known)
    return dict(total=len(cases),exact=exact,known_rounding_differences=known,failures=failed,
                skipped=0,accepted=accepted,status='failed' if failed else 'qualified_with_deviations' if known else 'exact_on_tested_vectors',
                allow_known_rounding=allow_known_rounding,details=details)


DRIVER = r'''
#include <bit>
#include <cfenv>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <string>
BACKEND_INCLUDE
int main() {
    if (std::fesetround(FE_TONEAREST)) return 3;
    std::string op; uint32_t a,b;
    while(std::cin >> op >> std::hex >> a >> b) {
        uint32_t result=0;
        BACKEND_RUN
        std::cout << std::hex << std::setfill('0') << std::setw(8) << result << '\n';
    }
    return std::cin.eof() ? 0 : 4;
}
'''


def _run(backend='scalar', allow_known_rounding=False, compiler=None):
    if backend not in {'scalar','reference','legacy'}:
        raise ValueError('Unknown conformance backend')
    compiler = compiler or shutil.which('clang++') or shutil.which('g++')
    if not compiler:
        raise RuntimeError('Conformance requires a C++20 compiler; no comparisons observed')
    # Preserve clang++'s invocation name: resolving its symlink to clang can
    # switch the driver to C mode and omit the C++ standard library on Linux.
    compiler = str(Path(compiler).absolute())
    version = subprocess.run([compiler,'--version'],capture_output=True,text=True,check=True,timeout=10).stdout
    data=load_vectors(); cases=data['cases']; inputs={}
    if backend=='reference':
        include='#include "ps2float.h"'
        expression=lambda op: f'ps2f_raw(ps2f_{op}(a'+('' if op=='sqrt' else ',b')+'))'
        inputs={str(p.relative_to(ROOT)):digest(p) for p in (ROOT/'tools/reference').glob('ps2float.*')}
    else:
        include='#include "ps2_scalar_fpu.h"' if backend=='scalar' else ''
        if backend=='scalar':
            expression=lambda op: 'std::bit_cast<uint32_t>(ps2_port_fpu::ps2_f'+op+'(x'+('' if op=='sqrt' else ',y')+'))'
            inputs={'tools/ps2_scalar_fpu.h':digest(ROOT/'tools/ps2_scalar_fpu.h')}
        else:
            # Pinned pre-correction host-float behavior, including old RSQRT operand bug.
            operations={'add':'x+y','sub':'x-y','mul':'x*y','div':'x/y','sqrt':'std::sqrt(x)','rsqrt':'1.0f/std::sqrt(x)'}
            expression=lambda op: 'std::bit_cast<uint32_t>(float('+operations[op]+'))'
    code='float x=std::bit_cast<float>(a),y=std::bit_cast<float>(b);\n'
    code+='\n'.join(('if' if i==0 else 'else if')+f' (op=="{op}") result={expression(op)};' for i,op in enumerate(OPS))
    code+='\nelse return 5;'
    flags=['-O2','-fno-fast-math','-ffp-contract=off','-frounding-math']
    with tempfile.TemporaryDirectory(prefix='ps2-conformance-') as temp:
        directory=Path(temp); source=directory/'driver.cpp'; exe=directory/('check.exe' if os.name=='nt' else 'check')
        source.write_text(DRIVER.replace('BACKEND_INCLUDE',include).replace('BACKEND_RUN',code))
        objects=[]
        if backend=='reference':
            obj=directory/'model.o'
            build=subprocess.run([compiler,'-x','c','-std=c99',*flags,'-c',str(ROOT/'tools/reference/ps2float.c'),'-o',str(obj)],capture_output=True,text=True,timeout=60)
            if build.returncode: raise RuntimeError('Reference compile failed: '+build.stderr[-4000:])
            objects=[str(obj)]
        build=subprocess.run([compiler,'-std=c++20',*flags,'-I',str(ROOT/'tools'),'-I',str(ROOT/'tools/reference'),str(source),*objects,'-o',str(exe)],capture_output=True,text=True,timeout=60)
        if build.returncode: raise RuntimeError('Conformance compile failed: '+build.stderr[-4000:])
        payload=''.join(f"{c['op']} {c['a']} {c['b']}\n" for c in cases)
        result=subprocess.run([str(exe)],input=payload,capture_output=True,text=True,timeout=15)
        if result.returncode: raise RuntimeError('Conformance execution failed; results unqualified: '+result.stderr[-2000:])
        report=assess(cases,result.stdout.splitlines(),allow_known_rounding)
    return dict(schema=1,backend=backend,compiler=compiler,compiler_version=version.strip(),compiler_sha256=digest(compiler),
                flags=flags,host=dict(system=platform.system(),machine=platform.machine()),rounding='FE_TONEAREST',
                fixture_sha256=digest(ROOT/'tests/fixtures/fpu-hardware.json'),source_hashes=inputs,
                provenance=data['provenance'],hardware_rerun=False,
                scope='Scalar result bits only; no instruction decode, FCR31 flags, ACC, VU timing or retail gameplay certification',**report)


def run(backend='scalar', allow_known_rounding=False, compiler=None):
    try:
        return _run(backend, allow_known_rounding, compiler)
    except subprocess.SubprocessError as error:
        raise RuntimeError('Conformance tool failed or timed out; no acceptance recorded') from error
