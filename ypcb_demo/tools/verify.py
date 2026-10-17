#!/usr/bin/env python3
"""Verify the YPCB build inputs using native simulators and Xilinx synthesis."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / 'ypcb_demo'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    return [ROOT / line for line in (DEMO / 'fpga/sources.f').read_text().splitlines() if line]


def synthesis_script(out):
    quoted = ' '.join(json.dumps(str(p)) for p in sources())
    return (f'read_verilog -sv {quoted}; hierarchy -check -top ypcb_tiny_tpu_top; '
            'synth_xilinx -family xc7 -top ypcb_tiny_tpu_top -flatten; '
            f'check -assert; write_json {json.dumps(str(out / "synth.json"))}; '
            f'write_verilog -noattr {json.dumps(str(out / "synth.v"))}; stat')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mapping = parser.add_mutually_exclusive_group()
    mapping.add_argument('--synth', action='store_true', help='also run synth_xilinx -family xc7')
    mapping.add_argument('--mapped-run', type=Path,
                         help='reuse a completed mapping with identical RTL, constraints and benches')
    mapping.add_argument('--compiled-run', type=Path,
                         help='reuse completed Icarus compilation with identical inputs and primitive models')
    parser.add_argument('--mapped-timeout', type=int, default=900,
                        help='mapped simulation wall-clock limit, including VVP loading (default 900 seconds)')
    args = parser.parse_args()
    if args.mapped_timeout <= 0:
        parser.error('--mapped-timeout must be positive')
    compiled = None
    if args.compiled_run:
        compiled = args.compiled_run.resolve()
        donor_report = json.loads((compiled / 'report.json').read_text())
        args.mapped_run = Path(donor_report.get('mapping_origin', {}).get('run', str(compiled)))
    out = DEMO / 'artifacts' / ('verify-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    reference_bench = ROOT / 'tiny-tpu-hardened/sim/tpu_xor_forward_regression.v'
    inputs = sources() + [DEMO / 'sim/tb_ypcb.v', DEMO / 'sim/tb_mapped.v', reference_bench, DEMO / 'fpga/ypcb.xdc',
                          DEMO / 'fpga/sources.f', Path(__file__).resolve()]
    report = {'status': 'RUNNING', 'scope': 'RTL simulation and optional XC7 mapping; no board signoff',
              'part': 'xc7k480tffg1156-2', 'source_hashes': {str(p.relative_to(ROOT)): digest(p) for p in inputs},
              'commands': [], 'bitstream_generated': False, 'hardware_run': False}
    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    def run(argv, name, timeout=180):
        entry = {'argv': [str(a) for a in argv], 'log': name + '.log',
                 'timeout_seconds': timeout}
        report['commands'].append(entry)
        save()
        before = time.monotonic()
        with (out / entry['log']).open('w') as log:
            process = subprocess.Popen(entry['argv'], cwd=ROOT, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Icarus/Yosys launch children. On an actual command timeout,
                # stop only this command's own group, leaving no orphan job.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                returncode = process.wait()
                entry.update(status='TIMEOUT', returncode=returncode,
                             seconds=round(time.monotonic() - before, 3))
                save()
                raise RuntimeError(f'{name} timed out after {timeout} seconds; see {out / entry["log"]}')
        entry.update(returncode=returncode, seconds=round(time.monotonic() - before, 3))
        save()
        if returncode:
            raise RuntimeError(f'{name} failed ({returncode}); see {out / entry["log"]}')
        return (out / entry['log']).read_text()
    try:
        for name in ('iverilog', 'vvp') + (('yosys',) if args.synth or args.mapped_run else ()):
            if not shutil.which(name):
                raise RuntimeError(f'Missing {name}')
        revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, capture_output=True)
        report['git_revision'] = revision.stdout.strip() if revision.returncode == 0 else None
        run(['iverilog', '-V'], 'iverilog_version')
        run(['iverilog', '-g2012', '-s', 'tb_ypcb', '-o', out / 'sim.vvp',
             *sources(), DEMO / 'sim/tb_ypcb.v'], 'compile')
        log = run(['vvp', out / 'sim.vvp'], 'simulation')
        if 'YPCB RTL REGRESSION PASS' not in log or 'FAIL' in log or 'FATAL' in log:
            raise RuntimeError('Simulation did not pass')
        run(['iverilog', '-g2012', '-s', 'tpu_xor_forward_regression', '-o', out / 'reference.vvp',
             *sources(), reference_bench], 'reference_compile')
        reference = run(['vvp', out / 'reference.vvp'], 'reference_simulation')
        if 'REGRESSION PASS' not in reference or 'FAIL' in reference or 'FATAL' in reference:
            raise RuntimeError('Original TPU XOR golden regression did not pass')
        if args.synth or args.mapped_run:
            run(['yosys', '-V'], 'yosys_version')
            if args.mapped_run:
                donor = args.mapped_run.resolve()
                previous = json.loads((donor / 'report.json').read_text())
                if previous.get('status') == 'RUNNING' or previous.get('part') != report['part']:
                    raise RuntimeError('Mapping donor is still running or targets another part')
                completed = [c for c in previous.get('commands', [])
                             if c.get('log') == 'synthesis.log' and c.get('returncode') == 0]
                if len(completed) != 1:
                    raise RuntimeError('Mapping donor has no completed successful synthesis command')
                verifier_name = str(Path(__file__).resolve().relative_to(ROOT))
                # The validated synthesis/compile scripts do not read XDC.
                # Preserve changed implementation constraints explicitly while
                # requiring identical RTL, source list, benches and models.
                non_synthesis = {verifier_name, 'ypcb_demo/fpga/ypcb.xdc'}
                without_verifier = lambda hashes: {k: v for k, v in hashes.items() if k not in non_synthesis}
                if without_verifier(previous.get('source_hashes', {})) != without_verifier(report['source_hashes']):
                    raise RuntimeError('Mapping donor inputs differ from current RTL, source list or benches')
                report['implementation_constraints'] = {
                    'used_by_synthesis': False,
                    'mapping_run_sha256': previous['source_hashes'].get('ypcb_demo/fpga/ypcb.xdc'),
                    'current_sha256': report['source_hashes']['ypcb_demo/fpga/ypcb.xdc']}
                if (donor / 'synth.ys').read_text().strip() != synthesis_script(donor):
                    raise RuntimeError('Mapping donor did not run the required synthesis/check script')
                if 'Found and reported 0 problems.' not in (donor / 'synthesis.log').read_text():
                    raise RuntimeError('Mapping donor has no passing structural check evidence')
                report['mapping_origin'] = {
                    'run': str(donor), 'report_sha256': digest(donor / 'report.json'),
                    'source_hashes': previous['source_hashes'],
                    'artifact_sha256': {name: digest(donor / name) for name in
                                        ('synth.ys', 'synth.json', 'synth.v', 'synthesis.log', 'yosys_version.log')},
                    'synthesis_command': completed[0]}
                for name in ('synth.json', 'synth.v'):
                    shutil.copy2(donor / name, out / name)
                    if digest(out / name) != report['mapping_origin']['artifact_sha256'][name]:
                        raise RuntimeError('Mapping artifact changed during reuse')
                save()
            else:
                (out / 'synth.ys').write_text(synthesis_script(out) + '\n')
                run(['yosys', '-Q', '-T', '-s', out / 'synth.ys'], 'synthesis', timeout=3600)
            module = json.loads((out / 'synth.json').read_text())['modules']['ypcb_tiny_tpu_top']
            cells = Counter(c['type'] for c in module['cells'].values())
            # Yosys flattening preserves hierarchy attributes in portless
            # $scopeinfo metadata. These are not hardware or unmapped logic.
            for cell in module['cells'].values():
                if cell['type'] == '$scopeinfo' and cell.get('connections'):
                    raise RuntimeError('Unexpected connected hierarchy metadata')
            report['synthesis_metadata_cells'] = {'$scopeinfo': cells.pop('$scopeinfo', 0)}
            report['synthesis_cells'] = dict(cells)
            if any(c.startswith('$') for c in cells):
                raise RuntimeError('Unmapped synthesis cells remain')
            if not cells['DSP48E1']:
                raise RuntimeError('Actual TPU multiplier datapath was not mapped to DSPs')
            ports = {name: (port['direction'], len(port['bits'])) for name, port in module['ports'].items()}
            if ports != {'SYS_CLK': ('input', 1), 'SYS_RSTN': ('input', 1), 'led': ('output', 3)}:
                raise RuntimeError(f'Unexpected mapped board ports: {ports}')
            # Yosys implements DSP/FF/LUT behavior, but its RAMB primitives are
            # black boxes. Refuse to call a BRAM netlist simulation verified.
            if cells['RAMB18E1'] or cells['RAMB36E1']:
                raise RuntimeError('Mapped BRAM requires functional UNISIM memory models')
            yosys_path = Path(shutil.which('yosys')).resolve()
            models = yosys_path.parent.parent / 'share/yosys/xilinx/cells_sim.v'
            if not models.is_file():
                raise RuntimeError(f'Missing Yosys functional primitive models: {models}')
            report['primitive_models'] = {'path': str(models), 'sha256': digest(models)}
            if compiled:
                previous = json.loads((compiled / 'report.json').read_text())
                compilation = [c for c in previous.get('commands', [])
                               if c.get('log') == 'mapped_compile.log' and c.get('returncode') == 0]
                expected_argv = ['iverilog', '-g2012', '-s', 'tb_mapped', '-o',
                                 str(compiled / 'mapped.vvp'), str(compiled / 'synth.v'),
                                 str(models), str(DEMO / 'sim/tb_mapped.v')]
                if previous.get('status') == 'RUNNING' or len(compilation) != 1:
                    raise RuntimeError('Compiled donor has no completed successful mapped compilation')
                if compilation[0]['argv'] != expected_argv:
                    raise RuntimeError('Compiled donor used another mapped compile command')
                if without_verifier(previous.get('source_hashes', {})) != without_verifier(report['source_hashes']):
                    raise RuntimeError('Compiled donor inputs differ from current RTL, source list or benches')
                if previous.get('primitive_models') != report['primitive_models']:
                    raise RuntimeError('Compiled donor primitive models differ')
                if digest(compiled / 'synth.v') != digest(out / 'synth.v'):
                    raise RuntimeError('Compiled donor netlist differs from validated synthesis')
                report['compilation_origin'] = {
                    'run': str(compiled), 'report_sha256': digest(compiled / 'report.json'),
                    'command': compilation[0], 'vvp_sha256': digest(compiled / 'mapped.vvp')}
                shutil.copy2(compiled / 'mapped.vvp', out / 'mapped.vvp')
                if digest(out / 'mapped.vvp') != report['compilation_origin']['vvp_sha256']:
                    raise RuntimeError('Compiled simulation changed during reuse')
                save()
            else:
                run(['iverilog', '-g2012', '-s', 'tb_mapped', '-o', out / 'mapped.vvp',
                     out / 'synth.v', models, DEMO / 'sim/tb_mapped.v'], 'mapped_compile', timeout=900)
            # Verbose loading/event statistics distinguish slow VVP loading
            # from a simulation that is not advancing. Assertions are unchanged.
            mapped_argv = ['vvp', '-v', out / 'mapped.vvp']
            if shutil.which('stdbuf'):
                mapped_argv = ['stdbuf', '-oL', '-eL', *mapped_argv]
            mapped = run(mapped_argv, 'mapped_simulation', timeout=args.mapped_timeout)
            if 'YPCB MAPPED REGRESSION PASS' not in mapped or 'FAIL' in mapped or 'FATAL' in mapped:
                raise RuntimeError('Mapped netlist simulation did not pass')
        if report['source_hashes'] != {str(p.relative_to(ROOT)): digest(p) for p in inputs}:
            raise RuntimeError('Verification inputs changed during execution')
        report['status'] = 'PASS'
    except Exception as exc:
        report.update(status='FAIL', reason=str(exc))
    save()
    print(json.dumps({'status': report['status'], 'report': str(out / 'report.json'),
                      'reason': report.get('reason'), 'cells': report.get('synthesis_cells')}, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
