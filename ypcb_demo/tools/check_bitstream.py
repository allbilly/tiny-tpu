#!/usr/bin/env python3
"""Compare the complete routed TPU bitstream with independent X-Ray tools."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from check_assembler import allowed_frames, frames, payload
from hardware import bitstream_part
from openxc7 import DB_REV, PART, checkout, digest

DEMO = Path(__file__).resolve().parents[1]
XRAY_REV = 'ed3331c6200f421164101388759fc2860b0f5634'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--prjxray', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True, help='existing X-Ray virtualenv interpreter')
    parser.add_argument('--frames2bit', type=Path, required=True)
    parser.add_argument('--wait-seconds', type=int, default=0)
    parser.add_argument('--timeout', type=int, default=7200, help='timeout per reference command')
    args = parser.parse_args()
    if args.wait_seconds < 0 or args.timeout <= 0:
        parser.error('Wait must be nonnegative and command timeout positive')
    out = DEMO / 'artifacts' / ('bitstream-check-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    report = {'status': 'RUNNING', 'part': PART, 'commands': [], 'hardware_programmed': False,
              'scope': 'Complete routed-FASM configuration payload equivalence; not timing/DRC or physical proof'}

    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(argv, name, stdout=None):
        entry = {'argv': [str(a) for a in argv], 'log': name + '.log', 'status': 'RUNNING',
                 'timeout_seconds': args.timeout, 'stdout': stdout.name if stdout else None}
        report['commands'].append(entry)
        save()
        before = time.monotonic()
        env = dict(os.environ, PYTHONPATH=str(args.prjxray.resolve()))
        with (out / entry['log']).open('w') as log:
            with (stdout.open('w') if stdout else (out / (name + '.stdout')).open('w')) as target:
                process = subprocess.Popen(entry['argv'], stdout=target, stderr=log, env=env,
                                           start_new_session=True)
                try:
                    rc = process.wait(timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    entry.update(status='TIMEOUT', returncode=process.returncode,
                                 seconds=round(time.monotonic() - before, 3))
                    save()
                    raise RuntimeError(f'{name} exceeded {args.timeout} seconds')
        entry.update(status='PASS' if rc == 0 else 'FAIL', returncode=rc,
                     seconds=round(time.monotonic() - before, 3))
        save()
        if rc:
            raise RuntimeError(f'{name} failed ({rc}); see {out / entry["log"]}')

    try:
        checkout(args.prjxray, XRAY_REV)
        checkout(args.database, DB_REV)
        candidate_report = args.run / 'report.json'
        deadline = time.monotonic() + args.wait_seconds
        while True:
            try:
                candidate = json.loads(candidate_report.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Candidate report is absent or incomplete')
                time.sleep(10)
                continue
            if candidate.get('status') == 'OPENXC7_BITSTREAM_EXPERIMENTAL':
                break
            if candidate.get('status') not in ('RUNNING', 'WAITING_TOOL_BUILDS', 'WAITING_DATABASE_CHECK'):
                raise RuntimeError('Candidate did not produce a passing bitstream: ' + str(candidate.get('status')))
            if time.monotonic() >= deadline:
                raise RuntimeError('Candidate is not a completed bitstream within the wait limit')
            report.update(status='WAITING_CANDIDATE', candidate_run=str(args.run.resolve()))
            save()
            time.sleep(10)
        if candidate.get('part') != PART or not candidate.get('bitstream_generated'):
            raise RuntimeError('Candidate has a different part or no assembled bitstream')
        completed = {c.get('log'): c for c in candidate.get('commands', [])}
        for name in ('database_integrity.log', 'place_route.log'):
            if completed.get(name, {}).get('status') != 'PASS':
                raise RuntimeError('Candidate lacks a passing ' + name)
        if candidate.get('assembler_returncode') != 0:
            raise RuntimeError('Candidate assembly did not succeed')
        fasm = args.run / 'tiny_tpu_ypcb.fasm'
        bit = args.run / 'tiny_tpu_ypcb.experimental.bit'
        if (digest(fasm) != candidate.get('output_sha256', {}).get(fasm.name) or
                digest(bit) != candidate.get('bitstream_sha256') or bitstream_part(bit) != PART):
            raise RuntimeError('Candidate FASM or bitstream differs from its passing report')
        converter = args.prjxray / 'utils/fasm2frames.py'
        part_dir = args.database / 'kintex7' / PART
        inputs = [Path(__file__).resolve(), DEMO / 'tools/check_assembler.py', DEMO / 'tools/hardware.py',
                  DEMO / 'tools/openxc7.py', DEMO / 'tools/check_dsp_fasm.py', candidate_report, fasm, bit,
                  DEMO / 'tools/check_engine.py', DEMO / 'tools/nextpnr-pip-name-lookup.patch',
                  DEMO / 'tools/check_chipdb.py', DEMO / 'tools/nextpnr-pip-name-index.patch',
                  converter, args.python, args.frames2bit, part_dir / 'part.json', part_dir / 'part.yaml']
        venv_config = args.python.parent.parent / 'pyvenv.cfg'
        if venv_config.is_file():
            inputs.append(venv_config)
        report.update(status='RUNNING', candidate_run=str(args.run.resolve()),
                      source_revisions={'prjxray': XRAY_REV, 'database': DB_REV},
                      inputs_sha256={str(p.absolute()): digest(p) for p in inputs})
        save()
        # Preserve the virtualenv path: resolving its interpreter symlink would
        # invoke the base interpreter without the installed reference modules.
        reference_frames = out / 'reference.frames'
        run([args.python.absolute(), converter.resolve(), '--part', PART, '--db-root',
             (args.database / 'kintex7').resolve(), fasm.resolve()], 'fasm_to_frames', reference_frames)
        allowed = allowed_frames(json.loads((part_dir / 'part.json').read_text()))
        parsed = frames(reference_frames, allowed)
        nonzero = sum(word != 0 for words in parsed.values() for word in words)
        report['reference_frames'] = {'legal_target_frames': len(allowed), 'supplied': len(parsed),
                                      'nonzero_words': nonzero}
        del parsed
        if not nonzero:
            raise RuntimeError('Full TPU reference configuration contains no nonzero frame words')
        reference_bit = out / 'reference.bit'
        run([args.frames2bit.resolve(), '--part_file', (part_dir / 'part.yaml').resolve(),
             '--part_name', PART, '--frm_file', reference_frames, '--output_file', reference_bit], 'frames_to_bit')
        if bitstream_part(reference_bit) != PART:
            raise RuntimeError('Reference bitstream has a different part header')
        native, reference = payload(bit), payload(reference_bit)
        report['configuration_payload'] = {'candidate_bytes': len(native), 'reference_bytes': len(reference),
                                           'candidate_sha256': hashlib.sha256(native).hexdigest(),
                                           'reference_sha256': hashlib.sha256(reference).hexdigest(),
                                           'equal': native == reference}
        if native != reference:
            raise RuntimeError('Complete TPU configuration payload differs from the independent reference')
        for name, expected in report['inputs_sha256'].items():
            if digest(Path(name)) != expected:
                raise RuntimeError('Input changed during verification: ' + name)
        checkout(args.prjxray, XRAY_REV)
        checkout(args.database, DB_REV)
        report.update(status='PASS_FULL_TPU_BITSTREAM_COMPARISON',
                      output_sha256={p.name: digest(p) for p in out.iterdir()
                                     if p.is_file() and p.name != 'report.json'})
    except Exception as exc:
        report.update(status='FAIL', reason=str(exc))
    save()
    print(json.dumps({'status': report['status'], 'report': str(out / 'report.json'),
                      'reason': report.get('reason')}, indent=2))
    return 0 if report['status'] == 'PASS_FULL_TPU_BITSTREAM_COMPARISON' else 1


if __name__ == '__main__':
    raise SystemExit(main())
