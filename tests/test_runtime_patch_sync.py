"""The runtime build takes vendored sources from the patch stack, not from runtime/. A source edited under runtime/
without regenerating its patch is silently left out of the build, so every new file a patch carries must equal its
runtime/ copy."""
import re,shutil,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
# patch, prefix inside the patched checkout, matching runtime/ directory
CARRIED=(('patches/native-sound-host.patch','ps2xRuntime/src/lib/native_sound/','runtime/native_sound/'),)

def new_files(patch):
    out={};name=None;lines=[]
    for line in (ROOT/patch).read_text().splitlines(True)+['diff --git end\n']:
        if line.startswith('diff --git '):
            if name is not None: out[name]=''.join(lines)
            name=None;lines=[]
        elif line.startswith('+++ b/') and name is None: name=line[6:].rstrip('\n');lines=[];new=True
        elif name is not None and line.startswith('+'): lines.append(line[1:])
        elif name is not None and line.startswith('@@') and not re.match(r'@@ -0,0 ',line): name=None
    return out

class RuntimePatchSyncTests(unittest.TestCase):
    def after_later_patches(self,patch,name,body):
        import sys; sys.path.insert(0,str(ROOT)); import native
        stack=list(native.RUNTIME_PATCHES); later=stack[stack.index(patch)+1:]
        touching=[p for p in later if f'+++ b/{name}\n' in (ROOT/p).read_text()]
        if not touching: return body
        if not shutil.which('git'): self.skipTest('git needed to apply later patches')
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/name; target.parent.mkdir(parents=True); target.write_text(body)
            for p in touching:
                run=subprocess.run(['git','apply','--include='+name,str(ROOT/p)],cwd=tmp,capture_output=True,text=True)
                self.assertEqual(run.returncode,0,f'{p}: {run.stderr}')
            return target.read_text()

    def test_carried_sources_match_runtime_copies(self):
        for patch,prefix,local in CARRIED:
            files={k:v for k,v in new_files(patch).items() if k.startswith(prefix)}
            self.assertTrue(files,patch)
            for name,body in files.items():
                path=ROOT/local/name[len(prefix):]
                if not path.exists(): continue
                body=self.after_later_patches(patch,name,body)
                text=path.read_text()
                self.assertEqual(body,text if text.endswith('\n') else text+'\n',f'{path.relative_to(ROOT)} differs from {patch}; regenerate the patch')

if __name__=='__main__': unittest.main()
