"""Two products, one compiler/knowledge implementation and one PR stream."""
import platform
import sys
import pipeline

CORE_COMMANDS = {'recover-link-target','audit-abi-layouts','recover-make-plan','select-archive-sources','compare-source-objects','merge-source-graphs','recover-compile-settings','revise-source-graph','audit-archive','stage-source-graph','analyze-field-trace','audit-build','build-source','convert-disc','qualify-port','review-upstreams','oracle-check','recover-function','find-recovery-candidates','collect-runtime-targets','analyze-vu-dependencies','instruction-check','analyze-cadence','doctor','bootstrap','import','verify','inspect','analyze','translate',
                 'check-generated','prepare','status','new-profile','next-game','knowledge-plan',
                 'knowledge-add','enhancement-plan','source-package','freeze-source'}
CORE_COMMANDS.add('import-preserved-copy')
CORE_COMMANDS.add('code-census')
CORE_COMMANDS.add('rebase-generated-corrections')
CORE_COMMANDS.add('translate-overlays')
CORE_COMMANDS.add('audit-reference-overlays')
CORE_COMMANDS.update({'isolate-tool-sources','relocate-source-graph','amend-source-graph','capture-source-corrections','retain-source-corrections','apply-source-corrections'})


def check_target(target,command,system=None):
    if target not in {'cpp','macos'}: raise ValueError('Unknown product target')
    if target=='cpp' and command not in CORE_COMMANDS:
        raise ValueError('This operation belongs to the macOS target; C++ export does not require a Mac runtime')
    if target=='macos' and (system or platform.system())!='Darwin':
        raise ValueError('The macOS build target requires macOS; use the cpp target for code export')


def main():
    if len(sys.argv)<2 or sys.argv[1] not in {'cpp','macos'}:
        raise SystemExit('Usage: python3 target_cli.py {cpp|macos} [pipeline options] command')
    target=sys.argv.pop(1)
    pipeline.cli(target=target)


if __name__=='__main__': main()
