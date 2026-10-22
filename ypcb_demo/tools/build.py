#!/usr/bin/env python3
"""Prepare a portable source bundle, then optionally implement with full Vivado."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

from run_process import run_with_timeout
from hardware import validate_vivado_signoff

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / 'ypcb_demo'
PART = 'xc7k480tffg1156-2'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--vivado', default=shutil.which('vivado'))
    parser.add_argument('--led-active-low', choices=('0', '1'), default='0')
    parser.add_argument('--core-mhz', type=int, choices=(50, 100, 150), default=50,
                        help='TPU clock; 100/150 use an MMCM from the fixed 50 MHz board oscillator')
    parser.add_argument('--clock-iostandard', choices=('LVCMOS18', 'LVCMOS33'),
                        help='board-confirmed AA28 clock I/O standard; required for bitstream emission')
    parser.add_argument('--emit-bitstream', action='store_true')
    parser.add_argument('--config-voltage', choices=('1.5', '1.8', '2.5', '3.3'))
    parser.add_argument('--cfgbvs', choices=('GND', 'VCCO'))
    args = parser.parse_args()
    if bool(args.config_voltage) != bool(args.cfgbvs):
        parser.error('Supply both --config-voltage and --cfgbvs')
    if args.emit_bitstream and not args.config_voltage:
        parser.error('Bitstream emission requires board-verified --config-voltage and --cfgbvs')
    if args.emit_bitstream and not args.clock_iostandard:
        parser.error('Bitstream emission requires --clock-iostandard; vendor references disagree on AA28 voltage')
    if args.config_voltage and ((args.config_voltage in ('1.5', '1.8')) != (args.cfgbvs == 'GND')):
        parser.error('Configuration voltage and CFGBVS are incompatible')
    out = DEMO / 'artifacts' / ('build-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    files = [ROOT / p for p in (DEMO / 'fpga/sources.f').read_text().splitlines() if p]
    files += [p for d in ('fpga', 'tools', 'sim') for p in (DEMO / d).iterdir() if p.is_file()]
    files += [p for p in DEMO.glob('*.md') if p.is_file()]
    files += [p for p in (DEMO / 'docs').rglob('*') if p.is_file()]
    files += [p for p in ROOT.glob('HARDWARE_TEST_*.md') if p.is_file()]
    files += [DEMO / '.gitignore', DEMO / 'artifacts/README.md', DEMO / 'Makefile']
    files += [ROOT / 'tiny-tpu-hardened/sim/tpu_xor_forward_regression.v']
    files = sorted(set(files))
    report = {'status': 'PREPARED', 'part': PART, 'hardware_programmed': False,
              'bitstream_generated': False, 'led_active_low': int(args.led_active_low),
              'configuration_voltage': args.config_voltage, 'cfgbvs': args.cfgbvs,
              'clock_iostandard': args.clock_iostandard or 'LVCMOS18',
              'clock_iostandard_confirmed': args.clock_iostandard is not None,
              'input_clock_mhz': 50, 'core_clock_mhz': args.core_mhz,
              'source_hashes': {str(p.relative_to(ROOT)): digest(p) for p in files}}
    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    save()
    with tarfile.open(out / 'source-bundle.tar.gz', 'w:gz') as archive:
        for p in files:
            archive.add(p, arcname=str(p.relative_to(ROOT)))
        archive.add(out / 'report.json', arcname='ypcb_demo/prepared-manifest.json')
    report['source_bundle_sha256'] = digest(out / 'source-bundle.tar.gz')
    # Exact argument list is retained even when Vivado is absent.
    report['argv'] = [args.vivado or 'vivado', '-mode', 'batch', '-nojournal',
                      '-log', str(out / 'vivado.log'), '-source', str(DEMO / 'fpga/build.tcl'),
                      '-tclargs', str(ROOT), str(out), args.led_active_low,
                      str(int(args.emit_bitstream)), args.config_voltage or '', args.cfgbvs or '', PART,
                      args.clock_iostandard or 'LVCMOS18', str(args.core_mhz)]
    try:
        if not args.prepare_only:
            if not args.vivado:
                report.update(status='BLOCKED_MISSING_VIVADO', reason='Full Vivado executable unavailable; Vivado Lab cannot implement this design')
            else:
                report['status'] = 'RUNNING'
                save()
                with (out / 'version.log').open('w') as log:
                    run_with_timeout([args.vivado, '-version'], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
                with (out / 'build.log').open('w') as log:
                    result = run_with_timeout(report['argv'], cwd=out, stdout=log, stderr=subprocess.STDOUT, timeout=14400)
                report['returncode'] = result.returncode
                if result.returncode or 'YPCB BUILD PASS' not in (out / 'build.log').read_text():
                    raise RuntimeError(f'Implementation failed ({result.returncode}); inspect build.log')
                report['signoff'] = json.loads((out / 'signoff.json').read_text())
                validate_vivado_signoff(report, args.emit_bitstream)
                report['routed_checkpoint_sha256'] = digest(out / 'routed.dcp')
                report['status'] = 'IMPLEMENTATION_PASS'
                if args.emit_bitstream:
                    report['bitstream_sha256'] = digest(out / 'tiny_tpu_ypcb.bit')
                    report['bitstream_generated'] = True
                    report['status'] = 'BITSTREAM_PASS'
        if report['source_hashes'] != {str(p.relative_to(ROOT)): digest(p) for p in files}:
            raise RuntimeError('Build sources changed during execution')
    except Exception as exc:
        report.update(status='FAIL', reason=str(exc))
    save()
    print(json.dumps({'status': report['status'], 'report': str(out / 'report.json'),
                      'source_bundle': str(out / 'source-bundle.tar.gz'), 'reason': report.get('reason')}, indent=2))
    return 0 if report['status'] in ('PREPARED', 'IMPLEMENTATION_PASS', 'BITSTREAM_PASS') else 2


if __name__ == '__main__':
    raise SystemExit(main())
