"""Asset-free eager/optimized runtime differential qualification."""
from pathlib import Path
import hashlib,json,os,subprocess

def compare(executable, flag, seeds, log_dir, multi=False):
    executable,log_dir=Path(executable).resolve(),Path(log_dir)
    log_dir.mkdir(parents=True,exist_ok=True)
    env={k:v for k,v in os.environ.items() if not k.startswith(('PS2_PORT','PS2X_'))}
    receipts=[]
    for seed in seeds:
        for extra in ([False,True] if multi else [False]):
            outputs=[]
            for mode in ('0','1'):
                result=subprocess.run([str(executable),str(seed)]+(['multi'] if extra else []),
                    env={**env,flag:mode},capture_output=True,timeout=120)
                stem=f'{executable.name}-{seed}-{int(extra)}-{mode}'
                (log_dir/(stem+'.out')).write_bytes(result.stdout)
                (log_dir/(stem+'.err')).write_bytes(result.stderr)
                if result.returncode:raise RuntimeError(f'{stem} exited {result.returncode}; see local logs')
                if not result.stdout:raise RuntimeError(f'{stem} emitted an empty trace')
                outputs.append(result.stdout)
            if outputs[0]!=outputs[1]:raise RuntimeError(f'{executable.name} differs for seed {seed}, multi={extra}')
            receipts.append(dict(seed=seed,multi=extra,lines=len(outputs[0].splitlines()),
                sha256=hashlib.sha256(outputs[0]).hexdigest()))
    return receipts

def verify(build,log_dir):
    build,log_dir=Path(build),Path(log_dir)
    result={'schema':1,'scope':'synthetic_runtime_only',
        'timers':compare(build/'ps2PortLazyTimerTests','PS2_PORT_LAZY_EE_TIMERS',[1,7,42,1234,99991],log_dir,True),
        'vif':compare(build/'ps2PortVifUnpackTests','PS2_PORT_VIF_FASTPATH',[1,2,3,77,4242],log_dir)}
    (log_dir/'differential.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
