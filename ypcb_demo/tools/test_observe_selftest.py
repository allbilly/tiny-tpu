#!/usr/bin/env python3
"""Exercise the real observer entry point offline, including optimized Python."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import observe_selftest as observer


def run_fixture(case, directory):
    """Replace only hardware/build inputs and the clock; run the actual main()."""
    directory = Path(directory)
    checksum = 'a' * 64
    receipt = dict(status='PASS', hardware_programmed=True, flash_written=False,
                   bitstream_sha256=checksum)
    if case == 'failed_load':
        receipt['status'] = 'FAIL'
    elif case == 'wrong_image':
        receipt['bitstream_sha256'] = 'b' * 64
    elif case == 'flash_write':
        receipt['flash_written'] = True
    (directory / 'load.json').write_text(json.dumps(receipt))
    bsdl = ('entity XC7K480T_FFG1156 is\n'
            'attribute BOUNDARY_LENGTH of XC7K480T_FFG1156 : entity is 1395;\n'
            '"SAMPLE (000001)"\n'
            '"852 (BC_2, IO_P30, output3, X, 851, 1, Z)"\n'
            '"846 (BC_2, IO_M30, output3, X, 845, 1, Z)"\n'
            '"849 (BC_2, IO_N30, output3, X, 848, 1, Z)"\n')
    if case == 'wrong_device':
        bsdl = bsdl.replace('XC7K480T_FFG1156', 'OTHER_DEVICE')
    elif case == 'wrong_boundary':
        bsdl = bsdl.replace('1395', '1396')
    elif case == 'wrong_instruction':
        bsdl = bsdl.replace('000001', '000000')
    elif case == 'duplicate_pin':
        bsdl += '"846 (BC_2, IO_M30, output3, X, 845, 1, Z)"\n'
    elif case == 'out_of_range':
        bsdl = bsdl.replace('852 (', '1395 (')
    (directory / 'device.bsd').write_text(bsdl)

    class FakeClock:
        elapsed = 0

        def monotonic(self):
            return self.elapsed

        def sleep(self, seconds):
            self.elapsed += 1.5 if case == 'few_samples' else .25

    class FakeXvc:
        info = 'xvcServer_v1.0:4096'
        max_bits = 100 if case == 'short_server' else 4096
        samples = 0

        def __init__(self, host, port):
            pass

        def reset_tap(self):
            pass

        def temperature(self):
            return dict(raw=40512, celsius=38.39)

        def close(self):
            pass

        def scan(self, data, count, ir=False):
            if ir:
                return 0 if case == 'not_configured' else 0x30
            if count == 32:
                return 0 if case == 'wrong_idcode' else 0x23751093
            self.samples += 1
            heartbeat = 0 if case == 'frozen_heartbeat' else self.samples % 2
            value = (1 << 846) | (heartbeat << 852)
            if case == 'pass_deasserted':
                value &= ~(1 << 846)
            elif case == 'failure_asserted':
                value |= 1 << 849
            elif case == 'disabled_output':
                value |= 1 << 845
            return value

    argv = ['observe_selftest.py', '--run', str(directory), '--load-receipt',
            str(directory / 'load.json'), '--bsdl', str(directory / 'device.bsd'),
            '--seconds', '3', '--out', str(directory / 'observation')]
    build = dict(core_clock_mhz=150, led_active_low=0)
    with patch.object(sys, 'argv', argv), \
            patch.object(observer, 'checked_bitstream', return_value=(
                directory / 'image.bit', build, checksum, '7k480tffg1156')), \
            patch.object(observer, 'Xvc', FakeXvc), \
            patch.object(observer, 'time', FakeClock()), \
            patch.object(observer.socket, 'create_connection',
                         side_effect=RuntimeError('Unexpected real socket access')):
        observer.main()


class ObserverRegression(unittest.TestCase):
    def test_entry_point_with_and_without_optimization(self):
        cases = {'good': None, 'failed_load': 'load receipt',
                 'wrong_image': 'bitstream', 'flash_write': 'Flash write',
                 'wrong_device': 'BSDL device', 'wrong_boundary': 'boundary length',
                 'wrong_instruction': 'SAMPLE instruction', 'duplicate_pin': 'BSDL pin',
                 'out_of_range': 'boundary cell', 'short_server': 'scan capacity',
                 'wrong_idcode': 'JTAG IDCODE', 'not_configured': 'INIT/DONE',
                 'pass_deasserted': 'PASS/failure', 'failure_asserted': 'PASS/failure',
                 'disabled_output': 'output drivers', 'frozen_heartbeat': 'Heartbeat',
                 'few_samples': 'Too few captures'}
        for flags in ([], ['-O'], ['-OO']):
            for case, reason in cases.items():
                with self.subTest(flags=flags, case=case), tempfile.TemporaryDirectory() as tmp:
                    result = subprocess.run(
                        [sys.executable, *flags, str(Path(__file__).resolve()),
                         '--fixture', case, tmp], capture_output=True, text=True, timeout=10)
                    report = json.loads((Path(tmp) / 'observation/report.json').read_text())
                    if reason is None:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(report['status'],
                                         'PASS_PHYSICAL_TINY_TPU_XOR_SELFTEST_OUTPUTS')
                        self.assertTrue(report['application_verified'])
                        self.assertGreaterEqual(report['heartbeat_transitions'], 2)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(report['status'], 'FAIL')
                        self.assertFalse(report['application_verified'])
                        self.assertIn(reason, report['reason'])
                    self.assertFalse(report['programming_performed'])
                    self.assertFalse(report['flash_written'])


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--fixture':
        run_fixture(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
