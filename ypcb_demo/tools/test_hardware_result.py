#!/usr/bin/env python3
"""Offline regressions for corrupted or failing archived FPGA observations."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from verify_hardware_result import DEFAULT_EVIDENCE, PINS, ROOT, digest, verify


class ArchivedHardwareResult(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.evidence = Path(self.temporary.name) / 'evidence'
        shutil.copytree(DEFAULT_EVIDENCE, self.evidence)

    def test_original_evidence(self):
        result = verify(self.evidence)
        self.assertEqual(result['samples'], 95)
        self.assertEqual(result['heartbeat_transitions'], 22)
        self.assertFalse(result['hardware_accessed'])
        self.assertFalse(result['current_sources_checked'])

    def source_checkout(self):
        # Bind only temporary receipts to synthetic sources. These tests must
        # keep working when real RTL evolves beyond the recorded board image.
        root = Path(self.temporary.name) / 'checkout'
        root.mkdir()
        build = json.loads((self.evidence / 'build.json').read_text())
        build['source_hashes'] = {}
        for name in ('design.v', 'extra.sv', 'ypcb_demo/fpga/ypcb.xdc',
                     'ypcb_demo/fpga/build.tcl', 'ypcb_demo/fpga/sources.f'):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('design.v\nextra.sv\n' if path.name == 'sources.f' else 'fixture\n')
            build['source_hashes'][name] = digest(path)
        self.change_build(build)
        return root

    def change_build(self, build):
        build_path = self.evidence / 'build.json'
        build_path.write_text(json.dumps(build))
        simulation_path = self.evidence / 'simulation.json'
        simulation = json.loads(simulation_path.read_text())
        simulation['inputs_sha256'][str(Path(simulation['build_run']) / 'report.json')] = digest(build_path)
        simulation_path.write_text(json.dumps(simulation))
        preflight_path = self.evidence / 'preflight.json'
        preflight = json.loads(preflight_path.read_text())
        preflight['build_report_sha256'] = digest(build_path)
        preflight['simulation_report_sha256'] = digest(simulation_path)
        preflight_path.write_text(json.dumps(preflight))

    def test_explicit_source_binding(self):
        result = verify(self.evidence, root=self.source_checkout())
        self.assertTrue(result['current_sources_checked'])

    def test_source_edit_preserves_history_but_fails_explicit_binding(self):
        root = self.source_checkout()
        changed = next(root.rglob('*.v'))
        with changed.open('a') as stream:
            stream.write('\n// A later comment does not invalidate historical evidence.\n')
        self.assertEqual(verify(self.evidence)['status'], 'PASS_ARCHIVED_HARDWARE_EVIDENCE')
        with self.assertRaisesRegex(ValueError, 'Implementation source changed'):
            verify(self.evidence, root=root)

    def test_missing_current_sources_fail_explicit_binding(self):
        with self.assertRaises(OSError):
            verify(self.evidence, root=Path(self.temporary.name) / 'empty-checkout')

    def test_empty_bindings_cannot_claim_current_sources_checked(self):
        root = self.source_checkout()
        build = json.loads((self.evidence / 'build.json').read_text())
        build['source_hashes'] = {}
        self.change_build(build)
        with self.assertRaisesRegex(ValueError, 'Incomplete implementation source bindings'):
            verify(self.evidence, root=root)

    def test_omitted_compiled_source_binding(self):
        root = self.source_checkout()
        build = json.loads((self.evidence / 'build.json').read_text())
        del build['source_hashes']['design.v']
        self.change_build(build)
        with self.assertRaisesRegex(ValueError, 'Incomplete implementation source bindings'):
            verify(self.evidence, root=root)

    def test_systemverilog_source_edit_fails_explicit_binding(self):
        root = self.source_checkout()
        (root / 'extra.sv').write_text('changed SystemVerilog input\n')
        with self.assertRaisesRegex(ValueError, 'Implementation source changed: extra.sv'):
            verify(self.evidence, root=root)

    def test_source_binding_cannot_escape_checkout(self):
        root = self.source_checkout()
        build = json.loads((self.evidence / 'build.json').read_text())
        build['source_hashes']['../outside.v'] = 'a' * 64
        self.change_build(build)
        with self.assertRaisesRegex(ValueError, 'Source path escapes checkout'):
            verify(self.evidence, root=root)

    def test_archive_cli_without_implementation_sources(self):
        root = Path(self.temporary.name) / 'archive-only'
        tool = root / 'ypcb_demo/tools/verify_hardware_result.py'
        tool.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'ypcb_demo/tools/verify_hardware_result.py', tool)
        shutil.copytree(self.evidence, root / 'ypcb_demo/docs/hardware/20261017')
        result = subprocess.run([sys.executable, str(tool)], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['current_sources_checked'])
        strict = subprocess.run([sys.executable, str(tool), '--check-current-sources'],
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn('FAIL_ARCHIVED_HARDWARE_EVIDENCE', strict.stderr)

    def change_observation(self, mutate):
        path = self.evidence / 'observation.json'
        data = json.loads(path.read_text())
        mutate(data)
        path.write_text(json.dumps(data))
        # Rebind the outer digest to test semantic rejection beyond hash checks.
        manifest_path = self.evidence / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['observation_receipt_sha256'] = digest(path)
        manifest_path.write_text(json.dumps(manifest))

    @staticmethod
    def set_bit(row, signal, value, field='outputs'):
        cell = PINS[signal]['output_cell' if field == 'outputs' else 'disable_cell']
        raw = int(row['boundary_hex'], 16)
        raw = (raw & ~(1 << cell)) | (value << cell)
        row['boundary_hex'] = f'{raw:0349x}'
        row[field][signal] = value

    def test_receipt_tampering(self):
        with (self.evidence / 'program.json').open('a') as stream:
            stream.write(' ')
        with self.assertRaisesRegex(ValueError, 'Receipt hash mismatch'):
            verify(self.evidence)

    def test_simulation_error_after_pass_marker(self):
        with (self.evidence / 'simulation.log').open('a') as stream:
            stream.write('\nFATAL: routed regression failed after its PASS marker\n')
        with self.assertRaisesRegex(ValueError, 'Routed simulation did not pass'):
            verify(self.evidence)

    def test_raw_failure_despite_pass_status(self):
        self.change_observation(lambda data: self.set_bit(data['samples'][0], 'failure', 1))
        with self.assertRaisesRegex(ValueError, 'PASS/failure output failed'):
            verify(self.evidence)

    def test_pass_deasserted(self):
        self.change_observation(lambda data: self.set_bit(data['samples'][0], 'pass', 0))
        with self.assertRaisesRegex(ValueError, 'PASS/failure output failed'):
            verify(self.evidence)

    def test_disabled_output(self):
        self.change_observation(lambda data: self.set_bit(data['samples'][0], 'pass', 1, 'disabled'))
        with self.assertRaisesRegex(ValueError, 'Output driver disabled'):
            verify(self.evidence)

    def test_frozen_heartbeat(self):
        def freeze(data):
            for row in data['samples']:
                self.set_bit(row, 'heartbeat', 0)
        self.change_observation(freeze)
        with self.assertRaisesRegex(ValueError, 'Heartbeat did not keep toggling'):
            verify(self.evidence)

    def test_empty_captures(self):
        self.change_observation(lambda data: data.update(samples=[]))
        with self.assertRaisesRegex(ValueError, 'Too few captures'):
            verify(self.evidence)

    def test_decoded_values_disagree(self):
        self.change_observation(lambda data: data['samples'][0]['outputs'].update(failure=1))
        with self.assertRaisesRegex(ValueError, 'Raw capture and decoded values differ'):
            verify(self.evidence)

    def test_wrong_actual_bitstream(self):
        wrong_bit = Path(self.temporary.name) / 'wrong.bit'
        wrong_bit.write_bytes(b'wrong image')
        with self.assertRaisesRegex(ValueError, 'Actual bitstream hash mismatch'):
            verify(self.evidence, wrong_bit)


if __name__ == '__main__':
    unittest.main()
