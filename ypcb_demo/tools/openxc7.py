#!/usr/bin/env python3
"""Implement the YPCB TPU with the current openXC7 Himbaechel backend."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import time

from verify import sources, synthesis_script
from hardware import bitstream_part
from check_dsp_fasm import check_configuration
from check_engine import validate_manifest, validate_database_check

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / 'ypcb_demo'
PART = 'xc7k480tffg1156-2'
ENGINE_BASE_REV = 'c68c13582e972292c86a5025140d52e713384cbc'
ENGINE_REV = 'a662c08d25d7d2524ab341a387f120521c8a5d08'
DB_REV = '517d66a383676cb971177ea92b0ff3b6ea6e8690'
PINS = {'SYS_CLK': 'AA28', 'SYS_RSTN': 'R28', 'led[0]': 'P30',
        'led[1]': 'M30', 'led[2]': 'N30'}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def checkout(path, expected):
    revision = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != expected:
        raise RuntimeError(f'{path}: expected revision {expected}, found {revision}')
    for argv in (['git', '-C', str(path), 'diff', '--quiet'],
                 ['git', '-C', str(path), 'diff', '--cached', '--quiet']):
        if subprocess.run(argv).returncode:
            raise RuntimeError(f'Tracked source changes in {path}')
    return revision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mapped-run', type=Path, required=True,
                        help='completed verify.py run containing successful XC7 synthesis')
    parser.add_argument('--nextpnr', type=Path, required=True)
    parser.add_argument('--chipdb', type=Path, required=True)
    parser.add_argument('--engine-source', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True, help='pinned prjxray-db checkout')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--wait-tools', type=int, default=0,
                        help='wait up to this many seconds for an existing tool build to finish')
    parser.add_argument('--nextpnr-build-log', type=Path)
    parser.add_argument('--nextpnr-build-manifest', type=Path,
                        help='passing PR66, lookup or indexed engine build manifest')
    parser.add_argument('--database-check-run', type=Path,
                        help='reuse a completed check_chipdb.py full test with identical binary inputs')
    parser.add_argument('--wait-database-check', type=int, default=0,
                        help='wait this many seconds for that existing full database test')
    parser.add_argument('--chipdb-build-log', type=Path)
    parser.add_argument('--timeout', type=int, default=7200)
    parser.add_argument('--seed', type=int, default=480)
    parser.add_argument('--clock-iostandard', choices=('LVCMOS18', 'LVCMOS33'))
    parser.add_argument('--fpga-as', type=Path, help='optionally assemble an experimental bitstream')
    parser.add_argument('--assembler-check', type=Path, action='append', default=[],
                        help='passing check_assembler.py run; assembly requires empty and reset-pad checks')
    parser.add_argument('--config-voltage', choices=('1.5', '1.8', '2.5', '3.3'))
    parser.add_argument('--cfgbvs', choices=('GND', 'VCCO'))
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if args.wait_tools < 0:
        parser.error('--wait-tools must not be negative')
    if args.wait_database_check < 0 or (args.wait_database_check and not args.database_check_run):
        parser.error('--wait-database-check needs a check run and a nonnegative wait')
    if args.wait_tools and not (args.nextpnr_build_log and args.chipdb_build_log):
        parser.error('Waiting requires both build logs, to avoid opening partially written outputs')
    if args.fpga_as and not args.prepare_only and not args.assembler_check:
        parser.error('Assembly requires --assembler-check runs for empty-design and reset-pullup')
    if bool(args.config_voltage) != bool(args.cfgbvs):
        parser.error('Supply both --config-voltage and --cfgbvs')
    if args.config_voltage and ((args.config_voltage in ('1.5', '1.8')) != (args.cfgbvs == 'GND')):
        parser.error('Configuration voltage and CFGBVS are incompatible')
    out = DEMO / 'artifacts' / ('openxc7-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    inputs = sources() + [DEMO / 'fpga/ypcb.xdc', DEMO / 'fpga/sources.f',
                          DEMO / 'fpga/board_reference.json',
                          Path(__file__).resolve(), DEMO / 'tools/verify.py',
                          DEMO / 'tools/hardware.py', DEMO / 'tools/check_dsp_fasm.py',
                          DEMO / 'tools/check_engine.py', DEMO / 'tools/nextpnr-pip-name-lookup.patch',
                          DEMO / 'tools/check_chipdb.py', DEMO / 'tools/nextpnr-pip-name-index.patch']
    report = {'status': 'RUNNING', 'part': PART, 'commands': [],
              'hardware_programmed': False, 'bitstream_generated': False,
              'source_hashes': {str(p.relative_to(ROOT)): digest(p) for p in inputs},
              'clock_iostandard': args.clock_iostandard or 'LVCMOS18',
              'clock_iostandard_confirmed': args.clock_iostandard is not None,
              'configuration_voltage': args.config_voltage, 'cfgbvs': args.cfgbvs,
              'timing_signoff': False, 'electrical_drc_signoff': False,
              'limitations': [
                  'Backend selects DEFAULT timing rather than the physical -2 speed grade',
                  'LUT and FF timing uses stub constants',
                  'Kintex carry/mux/BRAM timing is imported from Artix SDF',
                  'DSP48E1 timing coverage is not established',
                  'No vendor electrical/configuration DRC or physical-board result'],
              'toolchain': {'engine_revision': ENGINE_REV, 'database_revision': DB_REV,
                            'engine_base_revision': ENGINE_BASE_REV,
                            'engine_fix': 'Upstream PR66: three DSP constant/inversion/register-encoding fixes',
                            'engine_source': str(args.engine_source.resolve()),
                            'database': str(args.database.resolve())}}

    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(argv, name, timeout=60):
        entry = {'argv': [str(a) for a in argv], 'log': name + '.log',
                 'status': 'RUNNING', 'timeout_seconds': timeout}
        report['commands'].append(entry)
        save()
        before = time.monotonic()
        with (out / entry['log']).open('w') as log:
            process = subprocess.Popen(entry['argv'], cwd=ROOT, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                rc = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                entry.update(status='TIMEOUT', returncode=process.returncode,
                             seconds=round(time.monotonic() - before, 3))
                save()
                raise RuntimeError(f'{name} exceeded {timeout} seconds')
        entry.update(status='PASS' if rc == 0 else 'FAIL', returncode=rc,
                     seconds=round(time.monotonic() - before, 3))
        save()
        if rc:
            raise RuntimeError(f'{name} failed ({rc}); see {out / entry["log"]}')
        return (out / entry['log']).read_text()

    try:
        donor = args.mapped_run.resolve()
        previous = json.loads((donor / 'report.json').read_text())
        completed = [c for c in previous.get('commands', [])
                     if c.get('log') == 'synthesis.log' and c.get('returncode') == 0]
        if previous.get('status') == 'RUNNING' or previous.get('part') != PART or len(completed) != 1:
            raise RuntimeError('Donor needs a completed successful synthesis for the exact part')
        for p in sources() + [DEMO / 'fpga/sources.f']:
            if previous.get('source_hashes', {}).get(str(p.relative_to(ROOT))) != digest(p):
                raise RuntimeError(f'Synthesis input changed: {p}')
        if (donor / 'synth.ys').read_text().strip() != synthesis_script(donor):
            raise RuntimeError('Donor synthesis script differs from required mapping/check flow')
        if 'Found and reported 0 problems.' not in (donor / 'synthesis.log').read_text():
            raise RuntimeError('Donor lacks passing structural check evidence')
        report['mapping_origin'] = {'run': str(donor), 'report_sha256': digest(donor / 'report.json'),
                                    'synthesis_command': completed[0],
                                    'xdc_used_by_synthesis': False,
                                    'xdc_at_mapping_sha256': previous['source_hashes'].get('ypcb_demo/fpga/ypcb.xdc'),
                                    'artifact_sha256': {n: digest(donor / n) for n in
                                                        ('synth.json', 'synth.ys', 'synthesis.log')}}
        mapped = json.loads((donor / 'synth.json').read_text())
        module = mapped['modules']['ypcb_tiny_tpu_top']
        counts = Counter(c['type'] for c in module['cells'].values())
        ports = {n: (p['direction'], len(p['bits'])) for n, p in module['ports'].items()}
        if ports != {'SYS_CLK': ('input', 1), 'SYS_RSTN': ('input', 1), 'led': ('output', 3)}:
            raise RuntimeError('Unexpected mapped package ports')
        if counts['DSP48E1'] != 8 or counts['RAMB18E1'] or counts['RAMB36E1']:
            raise RuntimeError('Unexpected TPU DSP/memory mapping')
        for name, cell in list(module['cells'].items()):
            if cell['type'] == '$scopeinfo' and not cell.get('connections'):
                del module['cells'][name]
            elif cell['type'].startswith('$'):
                raise RuntimeError(f'Unmapped hardware cell: {cell["type"]}')
        report['removed_portless_scopeinfo'] = counts.pop('$scopeinfo', 0)
        report['synthesis_cells'] = dict(counts)
        (out / 'design.json').write_text(json.dumps(mapped) + '\n')
        report['design_sha256'] = digest(out / 'design.json')

        # Expand the original grouped port constraints into the literal names
        # supported by the XDC parser. No internal timing exception is added.
        original_xdc = (DEMO / 'fpga/ypcb.xdc').read_text()
        found_pins = dict((port, pin) for pin, port in re.findall(
            r'set_property PACKAGE_PIN (\w+) \[get_ports \{?([^}\s]+)\}?\]', original_xdc))
        if found_pins != PINS or 'create_clock -name sys_clk -period 20.000 [get_ports SYS_CLK]' not in original_xdc:
            raise RuntimeError('Board pin/clock constraints changed; review translation')
        expected_commands = {
            'set_property IOSTANDARD LVCMOS18 [get_ports {SYS_CLK SYS_RSTN led[*]}]',
            'set_property PULLTYPE PULLUP [get_ports SYS_RSTN]',
            'create_clock -name sys_clk -period 20.000 [get_ports SYS_CLK]',
            'set_false_path -from [get_ports SYS_RSTN]',
            'set_false_path -to [get_ports {led[*]}]'}
        expected_commands.update(f'set_property PACKAGE_PIN {pin} [get_ports ' +
                                 ('{' + port + '}' if port.startswith('led[') else port) + ']'
                                 for port, pin in PINS.items())
        commands = [line.strip() for line in original_xdc.splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
        if set(commands) != expected_commands or len(commands) != len(expected_commands):
            raise RuntimeError('XDC includes a changed, duplicated or unsupported constraint; review translation')
        supported = ['# Expanded from fpga/ypcb.xdc; all internal paths timed.']
        for port, pin in PINS.items():
            standard = report['clock_iostandard'] if port == 'SYS_CLK' else 'LVCMOS18'
            supported += [f'set_property PACKAGE_PIN {pin} [get_ports {{{port}}}]',
                          f'set_property IOSTANDARD {standard} [get_ports {{{port}}}]']
        supported.append('create_clock -period 20.000 [get_ports SYS_CLK]')
        supported.append('set_property PULLTYPE PULLUP [get_ports SYS_RSTN]')
        (out / 'openxc7.xdc').write_text('\n'.join(supported) + '\n')
        report['constraint_translation'] = {
            'sha256': digest(out / 'openxc7.xdc'), 'pins': PINS, 'period_ns': 20.0,
            'reset_pulltype': 'PULLUP',
            'not_applied': ['external SYS_RSTN false path', 'external LED false paths'],
            'internal_false_paths': [], 'reason': 'Backend does not implement set_false_path'}
        report['pnr_argv'] = [str(args.nextpnr.resolve()), '--device', PART,
                              '--chipdb', str(args.chipdb.resolve()), '--json', str(out / 'design.json'),
                              '--freq', '50', '--seed', str(args.seed), '--verbose',
                              '-o', 'xdc=' + str(out / 'openxc7.xdc'),
                              '-o', 'fasm=' + str(out / 'tiny_tpu_ypcb.fasm'), '-o', 'hold-fix',
                              '--write', str(out / 'routed.json'), '--report', str(out / 'timing.json'),
                              '--detailed-timing-report']
        save()
        if args.prepare_only:
            report['status'] = 'PREPARED_OPENXC7'
        else:
            checkout(args.engine_source, ENGINE_REV)
            checkout(args.database, DB_REV)
            if args.wait_tools:
                deadline = time.monotonic() + args.wait_tools
                report.update(status='WAITING_TOOL_BUILDS', tool_wait_seconds=args.wait_tools,
                              tool_build_logs={'nextpnr': str(args.nextpnr_build_log),
                                               'chipdb': str(args.chipdb_build_log)})
                save()
                while True:
                    logs = [p.read_text(errors='replace') if p.exists() else '' for p in
                            (args.nextpnr_build_log, args.chipdb_build_log)]
                    engine_marker = ('YPCB PR66 ENGINE BUILD PASS\n' if args.nextpnr_build_manifest
                                     else 'Built target nextpnr-himbaechel\n')
                    ready = (engine_marker in logs[0] and
                             'Built target nextpnr-himbaechel-xilinx-chipdb\n' in logs[1] and
                             args.nextpnr.is_file() and args.chipdb.is_file())
                    if ready:
                        for name, log_text in zip(('nextpnr-build-snapshot.log', 'chipdb-build-snapshot.log'), logs):
                            (out / name).write_text(log_text)
                        report['tool_build_log_sha256'] = {
                            name: digest(out / name) for name in
                            ('nextpnr-build-snapshot.log', 'chipdb-build-snapshot.log')}
                        report['status'] = 'RUNNING'
                        save()
                        break
                    if any(re.search(r'(?:^|\n)(?:CMake Error|gmake[^\n]*\*\*\*|FAILED:)', log)
                           for log in logs):
                        raise RuntimeError('An existing tool build failed; inspect the recorded build logs')
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Existing tool builds did not finish within --wait-tools')
                    time.sleep(10)
            report['toolchain'].update(nextpnr_sha256=digest(args.nextpnr), chipdb_sha256=digest(args.chipdb))
            if args.nextpnr_build_manifest:
                manifest = validate_manifest(args.nextpnr_build_manifest, args.nextpnr)
                report['toolchain']['engine_build_manifest'] = {
                    'path': str(args.nextpnr_build_manifest.resolve()),
                    'sha256': digest(args.nextpnr_build_manifest), 'report': manifest}
                if manifest['status'] in ('BUILT_PR66_LOOKUP_ENGINE_VARIANT', 'BUILT_PR66_INDEX_ENGINE_VARIANT'):
                    report['toolchain']['engine_source_delta'] = manifest['source_delta']
                    report['toolchain']['engine_fix'] += ('; local per-type PIP-name index with cache lifetime invalidation'
                        if manifest['status'] == 'BUILT_PR66_INDEX_ENGINE_VARIANT' else
                        '; local raw-adjacency PIP-name lookup optimization')
            run([args.nextpnr, '--version'], 'nextpnr_version')
            if args.database_check_run:
                deadline = time.monotonic() + args.wait_database_check
                while True:
                    try:
                        check = json.loads((args.database_check_run / 'report.json').read_text())
                    except (FileNotFoundError, json.JSONDecodeError):
                        check = {'status': 'RUNNING'}
                    if check.get('status') == 'PASS_FULL_DATABASE_CHECK':
                        break
                    if check.get('status') != 'RUNNING':
                        raise RuntimeError('Existing database check did not pass: ' + str(check.get('status')))
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Existing database check did not finish within its wait limit')
                    report.update(status='WAITING_DATABASE_CHECK', database_check_run=str(args.database_check_run.resolve()))
                    save()
                    time.sleep(10)
                check = validate_database_check(args.database_check_run, args.nextpnr, args.chipdb)
                (out / 'database_integrity.log').write_bytes((args.database_check_run / 'database_integrity.log').read_bytes())
                entry = dict(check['commands'][0])
                entry['reused_from'] = str(args.database_check_run.resolve())
                report['commands'].append(entry)
                report['database_test_provenance'] = {
                    'path': str(args.database_check_run.resolve()),
                    'report_sha256': digest(args.database_check_run / 'report.json'),
                    'log_sha256': digest(out / 'database_integrity.log'), 'report': check}
                report['status'] = 'RUNNING'
                save()
            else:
                run([args.nextpnr, '--device', PART, '--chipdb', args.chipdb, '--test'],
                    'database_integrity', args.timeout)
            log = run(report['pnr_argv'], 'place_route', args.timeout)
            if re.search(r'ERROR:|ignored|ignoring|matched nothing|missing target', log, re.I):
                raise RuntimeError('Backend reported ignored constraints or an implementation error')
            timing = json.loads((out / 'timing.json').read_text())
            clocks = timing.get('fmax', {})
            if not clocks:
                raise RuntimeError('No timed clock in implementation report')
            for clock, result in clocks.items():
                achieved, constraint = result.get('achieved'), result.get('constraint')
                if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (achieved, constraint)):
                    raise RuntimeError(f'Invalid clock report: {clock}')
                if constraint < 50 or achieved < constraint:
                    raise RuntimeError(f'Clock misses 50 MHz constraint: {clock}')
            report['backend_timing'] = timing
            for name in ('tiny_tpu_ypcb.fasm', 'routed.json', 'timing.json'):
                if not (out / name).stat().st_size:
                    raise RuntimeError(f'Empty backend output: {name}')
            reset_reference = json.loads((DEMO / 'fpga/board_reference.json').read_text())['pins']['SYS_RSTN']
            reset_iob = int(reset_reference['site'].split('Y')[-1]) % 2
            reset_feature = f'{reset_reference["tile"]}.IOB_Y{reset_iob}.PULLTYPE.PULLUP'
            features = (out / 'tiny_tpu_ypcb.fasm').read_text().splitlines()
            if reset_feature not in [line.strip() for line in features]:
                raise RuntimeError('Routed FASM does not enable the required reset-pad pull-up')
            report['reset_pullup_fasm_feature'] = reset_feature
            report['dsp_configuration'] = check_configuration(out / 'design.json', out / 'tiny_tpu_ypcb.fasm')
            report['output_sha256'] = {n: digest(out / n) for n in
                                       ('tiny_tpu_ypcb.fasm', 'routed.json', 'timing.json')}
            report['status'] = 'OPENXC7_PNR_EXPERIMENTAL'
            if args.fpga_as:
                report['assembler_sha256'] = digest(args.fpga_as)
                checks = []
                for directory in args.assembler_check:
                    check = json.loads((directory / 'report.json').read_text())
                    matched = [value for path, value in check.get('inputs_sha256', {}).items()
                               if Path(path).resolve() == args.fpga_as.resolve()]
                    if (check.get('status') != 'PASS_ASSEMBLER_COMPONENT_CHECK' or check.get('part') != PART or
                            matched != [report['assembler_sha256']]):
                        raise RuntimeError('Assembler component check is not a passing result for this exact executable')
                    checks.append({'path': str(directory.resolve()), 'report_sha256': digest(directory / 'report.json'),
                                   'report': check})
                if {c['report'].get('reference_stem') for c in checks} != {'empty-design', 'reset-pullup'}:
                    raise RuntimeError('Assembly requires both empty-design and reset-pullup component comparisons')
                if not any(c['report'].get('reference_stem') == 'reset-pullup' and
                           c['report'].get('frames', {}).get('reference_nonzero_words', 0) > 0 for c in checks):
                    raise RuntimeError('Assembler check lacks nonzero reset-pad configuration data')
                report['assembler_component_checks'] = checks
                # A candidate file is reversible preparation. Its unresolved
                # electrical assumptions remain recorded, and the separate
                # loader requires confirmation before touching FPGA SRAM.
                with (out / 'tiny_tpu_ypcb.fasm').open('rb') as fasm, \
                     (out / 'tiny_tpu_ypcb.experimental.bit').open('wb') as bit, \
                     (out / 'assemble.log').open('w') as log_file:
                    result = subprocess.run([str(args.fpga_as), '--prjxray_db_path=' +
                                             str(args.database.resolve() / 'kintex7'), '--part=' + PART],
                                            stdin=fasm, stdout=bit, stderr=log_file, timeout=args.timeout)
                report['assembler_returncode'] = result.returncode
                if result.returncode or not (out / 'tiny_tpu_ypcb.experimental.bit').stat().st_size:
                    raise RuntimeError('Bitstream assembly failed')
                if bitstream_part(out / 'tiny_tpu_ypcb.experimental.bit') != PART:
                    raise RuntimeError('Assembled bitstream part header differs from the exact target')
                report.update(status='OPENXC7_BITSTREAM_EXPERIMENTAL', bitstream_generated=True,
                              bitstream_sha256=digest(out / 'tiny_tpu_ypcb.experimental.bit'))
        if report['source_hashes'] != {str(p.relative_to(ROOT)): digest(p) for p in inputs}:
            raise RuntimeError('Implementation inputs changed during execution')
    except Exception as exc:
        report.update(status='FAIL', reason=str(exc))
    save()
    print(json.dumps({'status': report['status'], 'report': str(out / 'report.json'),
                      'reason': report.get('reason')}, indent=2))
    return 0 if report['status'] != 'FAIL' else 1


if __name__ == '__main__':
    raise SystemExit(main())
