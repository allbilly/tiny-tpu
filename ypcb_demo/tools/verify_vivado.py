#!/usr/bin/env python3
"""Run the package-pin TPU regression on a Vivado routed functional netlist."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from run_process import run_with_timeout
from hardware import checked_bitstream
from verify_hardware_result import verify_current_sources

ROOT = Path(__file__).resolve().parents[2]
PART = 'xc7k480tffg1156-2'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_checkpoint(build_run, build):
    """Bind the simulated checkpoint to the passing bitstream implementation."""
    checkpoint = build_run / 'routed.dcp'
    expected = build.get('routed_checkpoint_sha256')
    if not expected:
        raise RuntimeError('Build lacks a routed checkpoint hash; rebuild before routed simulation')
    if digest(checkpoint) != expected:
        raise RuntimeError('Routed checkpoint differs from the passing build')
    return checkpoint


def check_simulation_log(log):
    if ('YPCB MAPPED REGRESSION PASS' not in log or
            re.search(r'^\s*(?:FATAL|ERROR)(?:\s|:)', log, re.IGNORECASE | re.MULTILINE)):
        raise RuntimeError('XSim did not prove startup, repeated TPU PASS and reset recovery')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True, help='completed Vivado build directory')
    parser.add_argument('--vivado', type=Path, required=True, help='full Vivado executable')
    args = parser.parse_args()
    build_run = args.run.resolve()
    vivado = args.vivado.resolve()
    out = ROOT / 'ypcb_demo/artifacts' / ('verify-vivado-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir()
    report = {'status': 'RUNNING', 'scope': 'Routed functional netlist in XSim; static timing is checked separately; no physical board test.',
              'part': PART, 'build_run': str(build_run), 'hardware_programmed': False, 'commands': []}

    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(argv, name, timeout=600):
        command = {'argv': [str(a) for a in argv], 'log': name + '.log', 'timeout_seconds': timeout}
        report['commands'].append(command)
        save()
        before = time.monotonic()
        try:
            with (out / command['log']).open('w') as log:
                result = run_with_timeout(command['argv'], cwd=out, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            command.update(returncode=result.returncode, seconds=round(time.monotonic() - before, 3))
            if result.returncode:
                raise RuntimeError(f'{name} failed ({result.returncode}); see {command["log"]}')
        except subprocess.TimeoutExpired:
            command.update(status='TIMEOUT', seconds=round(time.monotonic() - before, 3))
            raise
        finally:
            save()

    save()
    try:
        build_report = build_run / 'report.json'
        build = json.loads(build_report.read_text())
        if build.get('status') != 'BITSTREAM_PASS' or build.get('part') != PART or build.get('returncode') != 0:
            raise RuntimeError('A completed exact-part Vivado bitstream build is required')
        if build.get('led_active_low') != 0:
            raise RuntimeError('This bench requires the default active-high LED candidate')
        bit = build_run / 'tiny_tpu_ypcb.bit'
        checked_bit, _, actual, part_header = checked_bitstream(build_run)
        if checked_bit != bit or actual != build['bitstream_sha256']:
            raise RuntimeError('Bitstream differs from the passing build')
        report['part_header'] = part_header
        verify_current_sources(build, ROOT)
        checkpoint = checked_checkpoint(build_run, build)
        bench = ROOT / 'ypcb_demo/sim/tb_mapped.v'
        glbl = vivado.parent.parent / 'data/verilog/src/glbl.v'
        inputs = [Path(__file__).resolve(), ROOT / 'ypcb_demo/tools/hardware.py',
                  ROOT / 'ypcb_demo/tools/run_process.py',
                  ROOT / 'ypcb_demo/tools/verify_hardware_result.py',
                  build_report, checkpoint, bit, bench, glbl]
        report['inputs_sha256'] = {str(p): digest(p) for p in inputs}
        export = out / 'export.tcl'
        netlist = out / 'routed_funcsim.v'
        export.write_text(f'open_checkpoint {{{checkpoint}}}\n'
                          f'write_verilog -force -mode funcsim {{{netlist}}}\n')
        run([vivado, '-mode', 'batch', '-nojournal', '-log', out / 'export.vivado.log',
             '-source', export], 'export')
        run([vivado.parent / 'xvlog', '--sv', '--work', 'work', netlist, bench, glbl], 'compile')
        run([vivado.parent / 'xelab', '--mt', '2', '-L', 'unisims_ver', '-L', 'unimacro_ver',
             '-L', 'secureip', 'work.tb_mapped', 'work.glbl', '-s', 'tinytpu_vivado'], 'elaborate')
        run([vivado.parent / 'xsim', 'tinytpu_vivado', '-runall', '-onerror', 'quit',
             '-onfinish', 'quit', '-log', out / 'xsim.engine.log'], 'simulation', timeout=180)
        log = (out / 'simulation.log').read_text()
        check_simulation_log(log)
        if report['inputs_sha256'] != {str(p): digest(p) for p in inputs}:
            raise RuntimeError('Inputs changed during simulation')
        report.update(status='PASS_VIVADO_ROUTED_FUNCTIONAL_SIMULATION',
                      netlist_sha256=digest(netlist), pass_marker='YPCB MAPPED REGRESSION PASS')
    except Exception as exc:
        report.update(status='FAIL', reason=str(exc))
    save()
    print(json.dumps({'status': report['status'], 'report': str(out / 'report.json'),
                      'reason': report.get('reason')}, indent=2))
    return 0 if report['status'] == 'PASS_VIVADO_ROUTED_FUNCTIONAL_SIMULATION' else 2


if __name__ == '__main__':
    raise SystemExit(main())
