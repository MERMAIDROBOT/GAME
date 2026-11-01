"""Reject known incomplete SDK substitutions in emitted translation artifacts."""
from pathlib import Path
import re

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
