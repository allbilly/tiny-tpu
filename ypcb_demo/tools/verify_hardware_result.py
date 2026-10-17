#!/usr/bin/env python3
"""Recheck archived hardware receipts and raw captures without accessing hardware."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE = ROOT / 'ypcb_demo/docs/hardware/20261017'
PINS = {
    'heartbeat': dict(pin='P30', output_cell=852, disable_cell=851),
    'pass': dict(pin='M30', output_cell=846, disable_cell=845),
    'failure': dict(pin='N30', output_cell=849, disable_cell=848),
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(evidence=DEFAULT_EVIDENCE, bitstream=None, root=None):
    """Validate historical evidence; optionally bind implementation inputs to root."""
    evidence = Path(evidence)
    manifest, preflight, program, observation, build, simulation = (
        json.loads((evidence / name).read_text()) for name in
        ('manifest.json', 'preflight.json', 'program.json', 'observation.json',
         'build.json', 'simulation.json'))
    for name, expected in (
        ('program.json', manifest['program_receipt_sha256']),
        ('observation.json', manifest['observation_receipt_sha256']),
        ('program.json', observation['load_receipt_sha256']),
        ('build.json', preflight['build_report_sha256']),
        ('simulation.json', preflight['simulation_report_sha256']),
        ('observe_selftest.py.snapshot', observation['observer_sha256']),
    ):
        require(digest(evidence / name) == expected, f'Receipt hash mismatch: {name}')

    require(manifest['status'] == 'PASS_PHYSICAL_TINY_TPU_150MHZ_XOR_QUICK_TEST'
            and manifest['hardware_accessed'] is True
            and manifest['fpga_programmed'] is True
            and manifest['application_verified'] is True, 'Combined test did not pass')
    require(preflight['status'] == 'PASS_PRELOAD_ARTIFACT_AND_ROUTED_SIMULATION_BINDING',
            'Preflight did not pass')
    require(program['status'] == 'PASS' and program['action'] == 'program-sram'
            and program['hardware_programmed'] is True
            and program['configuration_state'] == 'SRAM_LOAD_COMPLETED', 'SRAM load did not pass')
    require(observation['status'] == 'PASS_PHYSICAL_TINY_TPU_XOR_SELFTEST_OUTPUTS'
            and observation['hardware_accessed'] is True
            and observation['application_verified'] is True
            and observation['programming_performed'] is False, 'Observation did not pass')
    require(all(report['flash_written'] is False for report in
                (manifest, preflight, program, observation)), 'Flash write recorded')
    require(build['status'] == 'BITSTREAM_PASS' and build['returncode'] == 0
            and build['bitstream_generated'] is True, 'Build did not pass')
    require(simulation['status'] == 'PASS_VIVADO_ROUTED_FUNCTIONAL_SIMULATION'
            and simulation['pass_marker'] == 'YPCB MAPPED REGRESSION PASS'
            and simulation['pass_marker'] in (evidence / 'simulation.log').read_text(),
            'Routed simulation did not pass')
    for report in (program, simulation):
        require(report['commands'] and all(c['returncode'] == 0 for c in report['commands']),
                'Recorded command failed')
    require(build['part'] == simulation['part'] == 'xc7k480tffg1156-2'
            and preflight['part'] == observation['part'] == program['bitstream_part']
            == '7k480tffg1156', 'Wrong FPGA part')
    require(manifest['core_clock_mhz'] == observation['core_clock_mhz']
            == build['core_clock_mhz'] == preflight['core_mhz'] == 150
            and build['input_clock_mhz'] == 50, 'Wrong clock variant')
    checksum = manifest['bitstream_sha256']
    require(re.fullmatch(r'[0-9a-f]{64}', checksum) is not None
            and all(report['bitstream_sha256'] == checksum for report in
                    (preflight, program, observation, build)), 'Bitstream bindings differ')
    sim_inputs = simulation['inputs_sha256']
    for name, expected in (('report.json', digest(evidence / 'build.json')),
                           ('tiny_tpu_ypcb.bit', checksum)):
        require(sim_inputs.get(str(Path(simulation['build_run']) / name)) == expected,
                f'Simulation binding differs: {name}')
    if bitstream is not None:
        require(digest(bitstream) == checksum, 'Actual bitstream hash mismatch')
    signoff = build['signoff']
    require(signoff == preflight['signoff'], 'Signoff differs from preflight')
    require(signoff['fully_routed'] is True and signoff['internal_timing_coverage'] is True
            and signoff['routing_errors'] == signoff['blocking_drc'] == 0
            and signoff['core_clock_mhz'] == 150
            and abs(signoff['core_clock_period_ns'] - 1000 / 150) < .001,
            'Implementation signoff failed')
    require(all(math.isfinite(signoff[key]) and signoff[key] >= 0
                for key in ('setup_slack_ns', 'hold_slack_ns')), 'Timing did not pass')
    # Historical receipt validity is independent of later source edits. Check
    # current implementation hashes only when explicitly binding this checkout
    # to the recorded image. Documentation and Python tools are not FPGA inputs.
    if root is not None:
        root = Path(root).resolve()
        for name, expected in build['source_hashes'].items():
            path = (root / name).resolve()
            require(path.is_relative_to(root), 'Source path escapes checkout')
            if path.suffix in ('.v', '.xdc', '.tcl') or path.name == 'sources.f':
                require(digest(path) == expected, f'Implementation source changed: {name}')

    require(program['idcode'] == observation['idcode'] == '0x23751093', 'Wrong JTAG IDCODE')
    require(observation['capture_ir'] & 0x30 == 0x30, 'INIT/DONE not asserted')
    require(observation['boundary_length'] == 1395 and observation['pins'] == PINS,
            'Unexpected boundary scan mapping')
    rows = observation['samples']
    require(len(rows) >= 10, 'Too few captures')
    low = build['led_active_low']
    require(low in (0, 1), 'Invalid LED polarity')
    previous_time = -1
    heartbeat = []
    for row in rows:
        elapsed = row['elapsed_seconds']
        require(math.isfinite(elapsed) and elapsed >= 0 and elapsed > previous_time,
                'Capture times are not increasing')
        previous_time = elapsed
        raw = row['boundary_hex']
        require(re.fullmatch(r'[0-9a-f]{349}', raw) is not None, 'Invalid boundary capture')
        value = int(raw, 16)
        require(value.bit_length() <= 1395, 'Boundary capture exceeds device length')
        outputs = {signal: (value >> pin['output_cell']) & 1 for signal, pin in PINS.items()}
        disabled = {signal: (value >> pin['disable_cell']) & 1 for signal, pin in PINS.items()}
        require(outputs == row['outputs'] and disabled == row['disabled'],
                'Raw capture and decoded values differ')
        require(outputs['pass'] == (1 ^ low) and outputs['failure'] == low,
                'PASS/failure output failed')
        require(not any(disabled.values()), 'Output driver disabled')
        heartbeat.append(outputs['heartbeat'])
    transitions = sum(a != b for a, b in zip(heartbeat, heartbeat[1:]))
    require(transitions >= 2, 'Heartbeat did not keep toggling')
    require(transitions == observation['heartbeat_transitions'] == manifest['heartbeat_transitions']
            and len(rows) == manifest['samples'] == manifest['pass_samples']
            and manifest['failure_samples'] == 0, 'Summary counts differ from captures')
    for when in ('before', 'after'):
        temperature = observation[f'temperature_{when}']
        expected = round((temperature['raw'] >> 4) * 503.975 / 4096 - 273.15, 2)
        require(temperature['celsius'] == manifest[f'temperature_{when}_c'] == expected,
                'Temperature differs from raw XADC value')
    return dict(status='PASS_ARCHIVED_HARDWARE_EVIDENCE', samples=len(rows),
                heartbeat_transitions=transitions, bitstream_sha256=checksum,
                hardware_accessed=False, bitstream_file_checked=bitstream is not None,
                current_sources_checked=root is not None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', nargs='?', type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument('--bitstream', type=Path, help='also hash an available original .bit file')
    parser.add_argument('--check-current-sources', action='store_true',
                        help='also require current implementation inputs to match the recorded image')
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.evidence, args.bitstream,
                                root=ROOT if args.check_current_sources else None), indent=2))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f'FAIL_ARCHIVED_HARDWARE_EVIDENCE: {error}\n')


if __name__ == '__main__':
    main()
