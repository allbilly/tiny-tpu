#!/usr/bin/env python3
"""Inspect DLC10 readiness or explicitly load a verified build into FPGA SRAM."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess

from check_dsp_fasm import check_configuration
from check_engine import validate_manifest, validate_database_check

DEMO = Path(__file__).resolve().parents[1]
DEFAULT_LOADER = Path('/home/fedora/ypcb/openFPGALoader/build-xpcu-debug/openFPGALoader')
JTAG_HZ = 500000


def programming_timeout(bitstream_bytes):
    # Clocking eight bits per byte provides a lower bound, not a measured
    # loading time. Allow four times that bound plus USB/startup overhead.
    minimum_seconds = (bitstream_bytes * 8 + JTAG_HZ - 1) // JTAG_HZ
    return max(180, 4 * minimum_seconds + 60)


def bitstream_part(path):
    """Read Xilinx .bit metadata using the same field layout as BitParser."""
    data = path.read_bytes()
    def read_uint(pos, width):
        if pos < 0 or pos + width > len(data):
            raise ValueError('Truncated bitstream header')
        return int.from_bytes(data[pos:pos + width], 'big')
    pos = 2 + read_uint(0, 2)
    if read_uint(pos, 2) != 1:
        raise ValueError('Invalid bitstream header field prefix')
    pos += 2
    part = None
    for tag in (b'a', b'b', b'c', b'd', b'e'):
        if data[pos:pos + 1] != tag:
            raise ValueError('Unexpected bitstream header tag')
        pos += 1
        width = 4 if tag == b'e' else 2
        size = read_uint(pos, width)
        pos += width
        if pos + size > len(data):
            raise ValueError('Truncated bitstream field')
        if tag == b'b':
            part = data[pos:pos + size].rstrip(b'\0').decode('ascii')
        pos += size
    return part


def checked_bitstream(directory, experimental=False, electrical=None):
    """Validate a build independently of cable access; do not program anything."""
    build = json.loads((directory / 'report.json').read_text())
    if build.get('part') != 'xc7k480tffg1156-2' or build.get('bitstream_generated') is not True:
        raise RuntimeError('Build report lacks an exact-part YPCB bitstream')
    status = build.get('status')
    if status == 'BITSTREAM_PASS':
        bit = directory / 'tiny_tpu_ypcb.bit'
    elif status == 'OPENXC7_BITSTREAM_EXPERIMENTAL' and experimental:
        clock = build.get('clock_iostandard')
        confirmed = build.get('clock_iostandard_confirmed') is True
        voltage, strap = build.get('configuration_voltage'), build.get('cfgbvs')
        if electrical is not None:
            if electrical['clock_iostandard'] != clock:
                raise RuntimeError('Confirmed clock standard differs from the candidate; rebuild with that standard')
            voltage, strap = electrical['configuration_voltage'], electrical['cfgbvs']
            confirmed = True
        if not confirmed or clock not in ('LVCMOS18', 'LVCMOS33'):
            raise RuntimeError('Experimental build lacks a confirmed clock I/O standard')
        if (voltage, strap) not in (('1.5', 'GND'), ('1.8', 'GND'), ('2.5', 'VCCO'), ('3.3', 'VCCO')):
            raise RuntimeError('Experimental build lacks confirmed compatible configuration-bank values')
        build['load_electrical_confirmation'] = {
            'clock_iostandard': clock, 'configuration_voltage': voltage, 'cfgbvs': strap,
            'source': 'program_command' if electrical is not None else 'build_report'}
        for name in ('database_integrity.log', 'place_route.log'):
            commands = [c for c in build.get('commands', []) if c.get('log') == name]
            if len(commands) != 1 or commands[0].get('returncode') != 0 or commands[0].get('status') != 'PASS':
                raise RuntimeError(f'Experimental build lacks a passing {name}')
            forbidden = ('--timing-allow-fail', '--ignore-loops', '--ignore-rel-clk',
                         '--ignore-unrouted', '--ignore-phys', '--force')
            if any(a in forbidden for a in commands[0]['argv']):
                raise RuntimeError('Experimental implementation used an unsupported waiver')
        clocks = build.get('backend_timing', {}).get('fmax', {})
        if not clocks:
            raise RuntimeError('Experimental build lacks backend clock timing evidence')
        for timing in clocks.values():
            achieved, constraint = timing.get('achieved'), timing.get('constraint')
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (achieved, constraint)):
                raise RuntimeError('Experimental build has invalid backend timing')
            if constraint < 50 or achieved < constraint:
                raise RuntimeError('Experimental build misses its 50 MHz backend constraint')
        if build.get('assembler_returncode') != 0:
            raise RuntimeError('Experimental build lacks successful bitstream assembly')
        if build.get('toolchain', {}).get('engine_revision') != 'a662c08d25d7d2524ab341a387f120521c8a5d08':
            raise RuntimeError('Experimental engine lacks the pinned DSP control fix')
        toolchain = build['toolchain']
        if 'engine_source_delta' in toolchain:
            entry = toolchain.get('engine_build_manifest', {})
            path = Path(entry['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get('sha256'):
                raise RuntimeError('Experimental engine build manifest changed')
            manifest = validate_manifest(path, entry['report']['executable'])
            if (manifest != entry['report'] or
                    manifest.get('source_delta') != toolchain['engine_source_delta'] or
                    manifest['executable_sha256'] != toolchain.get('nextpnr_sha256')):
                raise RuntimeError('Experimental engine lookup provenance differs from its build result')
        if 'database_test_provenance' in build:
            entry = build['database_test_provenance']
            database_directory = Path(entry['path'])
            if hashlib.sha256((database_directory / 'report.json').read_bytes()).hexdigest() != entry['report_sha256']:
                raise RuntimeError('Experimental reused database-test report changed')
            checked = validate_database_check(database_directory, build['toolchain']['engine_build_manifest']['report']['executable'],
                                              entry['report']['chipdb'])
            if checked != entry['report'] or checked['log_sha256'] != entry['log_sha256']:
                raise RuntimeError('Experimental reused database-test provenance differs from its checked result')
        design, fasm = directory / 'design.json', directory / 'tiny_tpu_ypcb.fasm'
        if hashlib.sha256(design.read_bytes()).hexdigest() != build.get('design_sha256'):
            raise RuntimeError('Experimental mapped design changed after implementation')
        if hashlib.sha256(fasm.read_bytes()).hexdigest() != build.get('output_sha256', {}).get(fasm.name):
            raise RuntimeError('Experimental FASM changed after implementation')
        if check_configuration(design, fasm) != build.get('dsp_configuration'):
            raise RuntimeError('Experimental DSP control features differ from their checked build result')
        checks = [entry.get('report', {}) for entry in build.get('assembler_component_checks', [])]
        if {c.get('reference_stem') for c in checks} != {'empty-design', 'reset-pullup'} or any(
                c.get('status') != 'PASS_ASSEMBLER_COMPONENT_CHECK' for c in checks):
            raise RuntimeError('Experimental build lacks both passing native assembler component checks')
        bit = directory / 'tiny_tpu_ypcb.experimental.bit'
    elif status == 'OPENXC7_BITSTREAM_EXPERIMENTAL':
        raise RuntimeError('Experimental openXC7 loading requires --experimental-openxc7')
    else:
        raise RuntimeError('Build report does not prove a passing YPCB bitstream')
    bit = bit.resolve()
    actual = hashlib.sha256(bit.read_bytes()).hexdigest()
    if actual != build.get('bitstream_sha256'):
        raise RuntimeError('Bitstream does not match its build report')
    part = bitstream_part(bit)
    if not re.fullmatch(r'(?:xc)?7k480tffg1156(?:-2)?', part or ''):
        raise RuntimeError(f'Unexpected bitstream part header: {part}')
    return bit, build, actual, part


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'detect', 'init', 'program-sram'))
    parser.add_argument('--run', type=Path, help='build run directory containing report.json and .bit')
    parser.add_argument('--loader', type=Path, default=DEFAULT_LOADER)
    parser.add_argument('--experimental-openxc7', action='store_true',
                        help='explicitly allow an openXC7 experimental build with incomplete device timing/DRC coverage')
    parser.add_argument('--clock-iostandard', choices=('LVCMOS18', 'LVCMOS33'),
                        help='board-confirmed clock standard, which must match the candidate')
    parser.add_argument('--config-voltage', choices=('1.5', '1.8', '2.5', '3.3'))
    parser.add_argument('--cfgbvs', choices=('GND', 'VCCO'))
    args = parser.parse_args()
    supplied = (args.clock_iostandard, args.config_voltage, args.cfgbvs)
    if any(supplied) and not all(supplied):
        parser.error('Electrical confirmation requires clock standard, configuration voltage and CFGBVS together')
    electrical = {'clock_iostandard': args.clock_iostandard,
                  'configuration_voltage': args.config_voltage, 'cfgbvs': args.cfgbvs} if all(supplied) else None
    out = DEMO / 'artifacts' / ('hardware-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    report = {'status': 'RUNNING', 'action': args.action, 'hardware_programmed': False,
              'hardware_programming_attempted': False, 'configuration_state': 'NOT_TOUCHED',
              'application_verified': False, 'flash_written': False, 'commands': []}
    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    def run(argv, name, timeout=60):
        entry = {'argv': [str(a) for a in argv], 'log': name + '.log',
                 'timeout_seconds': timeout}
        report['commands'].append(entry)
        save()
        try:
            with (out / entry['log']).open('w') as log:
                result = subprocess.run(entry['argv'], stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            entry['status'] = 'TIMEOUT'
            save()
            raise
        entry['returncode'] = result.returncode
        save()
        output = (out / entry['log']).read_text()
        if result.returncode:
            raise RuntimeError(f'{name} failed: {output[-2000:]}')
        return output
    try:
        if not shutil.which('lsusb') or not args.loader.is_file():
            raise RuntimeError('lsusb or the Fedora DLC10 loader is unavailable')
        usb = run(['lsusb'], 'usb')
        cables = [line for line in usb.splitlines() if re.search(r'ID 03fd:(0013|0008)\b', line)]
        report['dlc10_devices'] = cables
        for process in ('hw_server', 'xsdb', 'fxload'):
            result = subprocess.run(['pgrep', '-x', process], capture_output=True, text=True)
            if result.returncode == 0:
                raise RuntimeError(f'{process} is using or may claim the cable; close it before cable operations')
            if result.returncode != 1:
                raise RuntimeError(f'Could not inspect {process} process state')
        if len(cables) != 1:
            raise RuntimeError(f'Expected exactly one DLC10 cable; found {len(cables)}')
        if args.action == 'init':
            run(['bash', '/home/fedora/ypcb/dlc10-init.sh'], 'initialization', timeout=180)
        elif args.action in ('detect', 'program-sram'):
            if '03fd:0008' not in cables[0]:
                raise RuntimeError('Cable is cold; run hardware.py init before detection')
            detection = run([args.loader, '--cable', 'xilinxPlatformCableUsb', '--freq', str(JTAG_HZ),
                             '--detect', '--verbose-level', '2'], 'detect')
            idcodes = re.findall(r'\bidcode\s+0x([0-9a-fA-F]+)', detection, re.I)
            if idcodes != ['23751093']:
                raise RuntimeError(f'Unexpected FPGA chain: {idcodes}')
            report['idcode'] = '0x23751093'
            if args.action == 'program-sram':
                if not args.run:
                    raise RuntimeError('program-sram requires --run BUILD_RUN_DIRECTORY')
                bit, build, actual, part = checked_bitstream(args.run, args.experimental_openxc7, electrical)
                report['build_status'] = build['status']
                report['experimental_openxc7'] = build['status'] == 'OPENXC7_BITSTREAM_EXPERIMENTAL'
                if report['experimental_openxc7']:
                    report['build_limitations'] = build.get('limitations', [])
                    report['timing_signoff'] = False
                    report['electrical_drc_signoff'] = False
                    report['electrical_confirmation'] = build['load_electrical_confirmation']
                report['bitstream_part'] = part
                report['bitstream_sha256'] = actual
                report['requested_jtag_hz'] = JTAG_HZ
                report['bitstream_bytes'] = bit.stat().st_size
                report['hardware_programming_attempted'] = True
                report['configuration_state'] = 'UNCERTAIN'
                save()
                # No -f/--write-flash or bridge bitstream is ever passed here.
                run([args.loader, '--cable', 'xilinxPlatformCableUsb', '--freq', str(JTAG_HZ),
                     str(bit)], 'program_sram', timeout=programming_timeout(bit.stat().st_size))
                report['hardware_programmed'] = True
                report['configuration_state'] = 'SRAM_LOAD_COMPLETED'
        report['status'] = 'PASS'
    except Exception as exc:
        report.update(status='BLOCKED' if 'found 0' in str(exc) else 'FAIL', reason=str(exc))
    save()
    print(json.dumps({'status': report['status'], 'report': str(out / 'report.json'),
                      'reason': report.get('reason')}, indent=2))
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
