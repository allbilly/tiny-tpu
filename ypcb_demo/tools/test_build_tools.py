#!/usr/bin/env python3
"""Offline regressions for build qualification, checkpoint binding and timeouts."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import hardware
from run_process import run_with_timeout
from verify_vivado import checked_checkpoint, check_simulation_log


def qualified_report():
    return {
        'status': 'BITSTREAM_PASS', 'part': 'xc7k480tffg1156-2',
        'returncode': 0, 'bitstream_generated': True,
        'input_clock_mhz': 50, 'core_clock_mhz': 150,
        'clock_iostandard': 'LVCMOS18', 'clock_iostandard_confirmed': True,
        'configuration_voltage': '1.8', 'cfgbvs': 'GND',
        'signoff': {
            'part': 'xc7k480tffg1156-2', 'core_clock_mhz': 150,
            'core_clock_period_ns': 6.667, 'clock_iostandard': 'LVCMOS18',
            'reset_pulltype': 'PULLUP', 'setup_slack_ns': 0.544,
            'hold_slack_ns': 0.108, 'internal_timing_coverage': True,
            'fully_routed': True, 'routing_errors': 0, 'blocking_drc': 0,
            'drc_ruledecks': ['default', 'router_checks', 'timing_checks', 'bitstream_checks'],
        },
    }


class SignoffTest(unittest.TestCase):
    def test_qualified_report(self):
        hardware.validate_vivado_signoff(qualified_report())

    def test_legacy_50mhz_report(self):
        report = qualified_report()
        for key in ('input_clock_mhz', 'core_clock_mhz'):
            del report[key]
        for key in ('core_clock_mhz', 'core_clock_period_ns'):
            del report['signoff'][key]
        hardware.validate_vivado_signoff(report)

    def test_incomplete_or_failing_signoff_rejected(self):
        invalid = [
            ('signoff', {}), ('returncode', 1), ('core_clock_mhz', 100),
            ('input_clock_mhz', 100), ('clock_iostandard_confirmed', False),
            ('configuration_voltage', '3.3'),
        ]
        for key, value in invalid:
            with self.subTest(key=key):
                report = qualified_report()
                report[key] = value
                with self.assertRaises(RuntimeError):
                    hardware.validate_vivado_signoff(report)
        invalid_signoff = [
            ('core_clock_period_ns', float('nan')),
            ('core_clock_period_ns', float('inf')), ('core_clock_period_ns', 20),
            ('core_clock_mhz', None), ('fully_routed', False), ('routing_errors', 1),
            ('blocking_drc', 1), ('internal_timing_coverage', False),
            ('setup_slack_ns', -0.001), ('hold_slack_ns', float('nan')),
            ('setup_slack_ns', True), ('reset_pulltype', 'NONE'),
            ('clock_iostandard', 'LVCMOS33'), ('drc_ruledecks', ['default']),
        ]
        for key, value in invalid_signoff:
            with self.subTest(signoff=key, value=value):
                report = qualified_report()
                report['signoff'][key] = value
                with self.assertRaises(RuntimeError):
                    hardware.validate_vivado_signoff(report)

    def test_status_label_does_not_qualify_bitstream(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = qualified_report()
            del report['signoff']
            # A matching file digest and status label used to bypass signoff.
            bit = directory / 'tiny_tpu_ypcb.bit'
            bit.write_bytes(b'unqualified bitstream')
            report['bitstream_sha256'] = hashlib.sha256(bit.read_bytes()).hexdigest()
            (directory / 'report.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(RuntimeError, 'clock|signoff'):
                hardware.checked_bitstream(directory)


class CheckpointTest(unittest.TestCase):
    def test_checkpoint_bound_to_implementation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            checkpoint = directory / 'routed.dcp'
            checkpoint.write_bytes(b'original routed design')
            report = {'routed_checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
            self.assertEqual(checked_checkpoint(directory, report), checkpoint)
            checkpoint.write_bytes(b'different routed design')
            with self.assertRaisesRegex(RuntimeError, 'differs'):
                checked_checkpoint(directory, report)

    def test_legacy_checkpoint_needs_rebuild_for_fresh_simulation(self):
        with self.assertRaisesRegex(RuntimeError, 'rebuild'):
            checked_checkpoint(Path('/unused'), {})


class SimulationLogTest(unittest.TestCase):
    def test_clean_pass_log(self):
        check_simulation_log('INFO: Simulator started\nYPCB MAPPED REGRESSION PASS\n')

    def test_missing_marker_or_error_with_marker_rejected(self):
        logs = ['INFO: Simulation finished without a marker\n']
        logs += ['YPCB MAPPED REGRESSION PASS\n' + failure + '\n' for failure in (
            'ERROR: [Simulator 1-1] failed', 'FATAL: invalid output', 'Fatal: invalid output',
            '  error: failed after pass', '\tfAtAl invalid output', 'ERROR failed after pass',
        )]
        for log in logs:
            with self.subTest(log=log), self.assertRaises(RuntimeError):
                check_simulation_log(log)


class InitializerTest(unittest.TestCase):
    def test_initializer_override_without_hardware_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            loader, initializer = root / 'loader', root / 'custom-init.sh'
            loader.touch()
            initializer.touch()
            invoked = []

            def fake_run(argv, **kwargs):
                invoked.append(argv)
                if argv == ['lsusb']:
                    kwargs['stdout'].write('Bus 001 Device 002: ID 03fd:0013 DLC10\n')
                    return subprocess.CompletedProcess(argv, 0)
                if argv[:2] == ['pgrep', '-x']:
                    return subprocess.CompletedProcess(argv, 1)
                if argv == ['bash', str(initializer)]:
                    return subprocess.CompletedProcess(argv, 0)
                self.fail(f'unexpected hardware command: {argv}')

            argv = ['hardware.py', 'init', '--loader', str(loader), '--init-script', str(initializer)]
            with patch.object(hardware, 'DEMO', root), patch.object(sys, 'argv', argv), \
                    patch.object(hardware.shutil, 'which', return_value='/mock/lsusb'), \
                    patch.object(hardware.subprocess, 'run', side_effect=fake_run):
                self.assertEqual(hardware.main(), 0)
            self.assertIn(['bash', str(initializer)], invoked)


class ProcessTimeoutTest(unittest.TestCase):
    def test_tool_exit_status_preserved(self):
        result = run_with_timeout([sys.executable, '-c', 'raise SystemExit(7)'], timeout=5)
        self.assertEqual(result.returncode, 7)
        with self.assertRaises(subprocess.CalledProcessError):
            run_with_timeout([sys.executable, '-c', 'raise SystemExit(7)'], timeout=5, check=True)

    def test_timeout_stops_wrapper_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            pid_file = Path(temporary) / 'child.pid'
            script = (
                'import pathlib, subprocess, sys, time; '
                'child=subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]); '
                f'pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); '
                'time.sleep(60)'
            )
            child_pid = None
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_with_timeout([sys.executable, '-c', script], timeout=1)
                self.assertTrue(pid_file.exists(), 'fixture wrapper did not create its child')
                child_pid = int(pid_file.read_text())
                for _ in range(100):
                    status = Path(f'/proc/{child_pid}/stat')
                    try:
                        state = status.read_text().split()[2]
                    except (FileNotFoundError, ProcessLookupError):
                        # Reaping can remove /proc while the status is read.
                        break
                    if state == 'Z':
                        break
                    time.sleep(0.01)
                else:
                    self.fail('timed-out tool left its wrapper child running')
            finally:
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == '__main__':
    unittest.main()
