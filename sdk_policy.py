"""Reject known incomplete SDK substitutions in emitted translation artifacts."""
from pathlib import Path
import re
import tomllib

REQUIRES_BODY={'sceSdRemote','sceSdRemoteInit'}
LITERALS=re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/',re.S)
CALL=re.compile(r'\bps2_stubs\s*::\s*('+ '|'.join(sorted(REQUIRES_BODY)) +r')\s*\(')


def audit_generated(directory):
 root=Path(directory).resolve();findings=[];count=0
 if not root.is_dir():raise ValueError('Generated SDK audit requires a source directory')
 from pipeline import generated_sources
 for path in generated_sources(root):
  count+=1;clean=LITERALS.sub(lambda m:'\n'*m[0].count('\n')+' ',path.read_text())
  for match in CALL.finditer(clean):findings.append(dict(file=str(path.relative_to(root)),symbol=match[1],line=clean.count('\n',0,match.start())+1,reason='Stateful SDK RPC requires its compiled body; generic host handler is incomplete'))
 return dict(schema=1,files_scanned=count,passed=not findings,findings=findings,scope='Known incomplete SDK substitutions only; other handler completeness and gameplay remain unverified')


IMAGE_HANDLERS = {'sceGsSetDefLoadImage', 'sceGsSetDefStoreImage',
                  'sceGsExecLoadImage', 'sceGsExecStoreImage'}


def audit_native_image_bindings(config, directory):
 """Configured, qualified libgraph services must reach their native handlers.

 Address-qualified analyzer declarations are the authority; no game address or
 guessed signature is introduced here. An old translated hardware loop fails
 this preflight even if its address happens to be registered.
 """
 root=Path(directory);settings=tomllib.loads(Path(config).read_text())
 declarations=settings.get('general',{}).get('stubs',settings.get('stubs',[]))
 expected={}
 for declaration in declarations:
  match=re.fullmatch(r'(\w+)@0x([0-9a-fA-F]+)',declaration)
  if match and match[1] in IMAGE_HANDLERS:
   address=int(match[2],16)
   if address in expected and expected[address]!=match[1]:
    raise ValueError('Conflicting native libgraph bindings')
   expected[address]=match[1]
 table=root/'register_functions.cpp'
 owners={int(address,16):name for name,address in re.findall(
  r'\[[0-9]+u?\]\s*=\s*(\w+)\s*;\s*//\s*0x([0-9a-fA-F]+)',table.read_text())} if table.is_file() else {}
 findings=[]
 for address,name in sorted(expected.items()):
  owner=owners.get(address);source=root/(owner+'.cpp') if owner else None
  text=LITERALS.sub(' ',source.read_text()) if source and source.is_file() else ''
  if not re.search(r'\bps2_stubs\s*::\s*'+name+r'\s*\(',text):
   findings.append(dict(address=f'{address:08x}',symbol=name,owner=owner,
                        reason='Configured native image service is missing or still executes its guest body'))
 return dict(schema=1,contracts_checked=len(expected),passed=not findings,findings=findings,
             scope='Configured libgraph image bindings only; runtime and gameplay require separate checks')
