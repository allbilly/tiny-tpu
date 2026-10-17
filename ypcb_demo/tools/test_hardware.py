#!/usr/bin/env python3
"""Regression for loading a candidate that reuses a separate database test."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import hardware


class ReusedDatabaseTest(unittest.TestCase):
    def test_keeps_candidate_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, database = root / 'candidate', root / 'database'
            candidate.mkdir()
            database.mkdir()
            database_report = {'status': 'PASS_FULL_DATABASE_CHECK',
                               'chipdb': str(root / 'chipdb'), 'log_sha256': 'checked'}
            report_bytes = (json.dumps(database_report) + '\n').encode()
            (database / 'report.json').write_bytes(report_bytes)
            design, fasm = b'{}\n', b'fixture\n'
            (candidate / 'design.json').write_bytes(design)
            (candidate / 'tiny_tpu_ypcb.fasm').write_bytes(fasm)
            # A small .bit metadata container; this test checks directory selection,
            # while the real candidate integration check covers FPGA payloads.
            bit = bytearray(b'\x00\x00\x00\x01')
            for tag, value in ((b'a', b'test\x00'), (b'b', b'7k480tffg1156\x00'),
                               (b'c', b'2026/10/06\x00'), (b'd', b'00:00:00\x00'),
                               (b'e', b'payload')):
                bit += tag + len(value).to_bytes(4 if tag == b'e' else 2, 'big') + value
            bit_path = candidate / 'tiny_tpu_ypcb.experimental.bit'
            bit_path.write_bytes(bit)
            digest = lambda value: hashlib.sha256(value).hexdigest()
            dsp = {'status': 'PASS_DSP_CONTROL_FASM'}
            build = {
                'part': 'xc7k480tffg1156-2', 'bitstream_generated': True,
                'status': 'OPENXC7_BITSTREAM_EXPERIMENTAL',
                'clock_iostandard': 'LVCMOS18',
                'commands': [{'log': log, 'returncode': 0, 'status': 'PASS', 'argv': ['tool']}
                             for log in ('database_integrity.log', 'place_route.log')],
                'backend_timing': {'fmax': {'clk': {'achieved': 60, 'constraint': 50}}},
                'assembler_returncode': 0,
                'toolchain': {'engine_revision': 'a662c08d25d7d2524ab341a387f120521c8a5d08',
                              'engine_build_manifest': {'report': {'executable': 'engine'}}},
                'database_test_provenance': {'path': str(database),
                                             'report_sha256': digest(report_bytes),
                                             'log_sha256': 'checked', 'report': database_report},
                'design_sha256': digest(design), 'output_sha256': {'tiny_tpu_ypcb.fasm': digest(fasm)},
                'dsp_configuration': dsp,
                'assembler_component_checks': [{'report': {'reference_stem': stem,
                                                           'status': 'PASS_ASSEMBLER_COMPONENT_CHECK'}}
                                                for stem in ('empty-design', 'reset-pullup')],
                'bitstream_sha256': digest(bit),
            }
            (candidate / 'report.json').write_text(json.dumps(build))
            electrical = {'clock_iostandard': 'LVCMOS18',
                          'configuration_voltage': '1.8', 'cfgbvs': 'GND'}
            with patch.object(hardware, 'validate_database_check', return_value=database_report) as check_db, \
                    patch.object(hardware, 'check_configuration', return_value=dsp) as check_dsp:
                actual_path, _, actual_hash, part = hardware.checked_bitstream(candidate, True, electrical)
            check_db.assert_called_once_with(database, 'engine', str(root / 'chipdb'))
            check_dsp.assert_called_once_with(candidate / 'design.json', candidate / 'tiny_tpu_ypcb.fasm')
            self.assertEqual(actual_path, bit_path.resolve())
            self.assertEqual(actual_hash, digest(bit))
            self.assertEqual(part, '7k480tffg1156')


if __name__ == '__main__':
    unittest.main()
