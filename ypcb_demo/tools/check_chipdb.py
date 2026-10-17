#!/usr/bin/env python3
"""Run the unmodified full 480T architecture test on the indexed PR66 engine."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

DEMO = Path(__file__).resolve().parents[1]
PART = 'xc7k480tffg1156-2'
HEAD = 'a662c08d25d7d2524ab341a387f120521c8a5d08'
BASE = 'c68c13582e972292c86a5025140d52e713384cbc'
PATCH_SHA = 'effd522ce9c6fdb9ad7f77b97fde25e8e2ca7b21e8b44127726704048176bced'
SOURCE_SHA = '62b8d0a318ae1363c302f8cc868e921b7649eaf64b8a7be4502a7e1e40c5be68'
TEST_OBJECT_SHA = 'bf092791ed47588090b8b857b62a7175cac6fb9431a604a08beaf00c436d0362'
PHASES = ('Running architecture database integrity check.', 'Checking entity names.',
          'Checking pip names..', 'Checking location data.', 'Checking connectivity data.',
          'Checking all PIPs...', 'Checking bucket data.')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-manifest', type=Path, required=True)
    parser.add_argument('--nextpnr', type=Path, required=True)
    parser.add_argument('--chipdb', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=7200)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    out = DEMO / 'artifacts' / ('chipdb-check-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    report = dict(status='RUNNING', part=PART, hardware_programmed=False, commands=[],
                  scope='Full unchanged architecture API test; no timing, electrical or physical signoff',
                  process_nice=os.getpriority(os.PRIO_PROCESS, 0))

    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def unchanged():
        for name, expected in report['inputs_sha256'].items():
            if digest(name) != expected:
                raise RuntimeError('Architecture-test input changed: ' + name)

    save()
    try:
        manifest = json.loads(args.build_manifest.read_text())
        delta = manifest.get('source_delta', {})
        if (manifest.get('status') != 'BUILT_PR66_INDEX_ENGINE_VARIANT' or
                manifest.get('source_revision') != HEAD or manifest.get('base_revision') != BASE or
                Path(manifest.get('executable', '')).resolve() != args.nextpnr.resolve() or
                manifest.get('executable_sha256') != digest(args.nextpnr) or
                delta.get('patch_sha256') != PATCH_SHA or delta.get('patched_sha256') != SOURCE_SHA or
                digest(delta['patch_path']) != PATCH_SHA or digest(delta['patched_source_path']) != SOURCE_SHA):
            raise RuntimeError('Indexed executable does not match the verified build and source delta')
        commands = manifest.get('commands', [])
        if ([c.get('log') for c in commands] != ['apply_patch.log', 'arch.cc.log', 'link.log', 'version.log'] or
                any(c.get('status') != 'PASS' or c.get('returncode') != 0 for c in commands)):
            raise RuntimeError('Indexed engine build commands did not all pass')
        test = manifest.get('unchanged_integrity_test_object', {})
        if len(test) != 1 or list(test.values()) != [TEST_OBJECT_SHA]:
            raise RuntimeError('Engine does not retain the pinned architecture-test object')
        report['inputs_sha256'] = {**manifest['inputs_sha256'], **manifest['link_inputs_sha256']}
        report['inputs_sha256'].update({str(p.resolve()): digest(p) for p in
                                      (Path(__file__), args.build_manifest, args.nextpnr, args.chipdb)})
        report.update(executable=str(args.nextpnr.resolve()), executable_sha256=digest(args.nextpnr),
                      chipdb=str(args.chipdb.resolve()), chipdb_sha256=digest(args.chipdb),
                      engine_build_manifest=str(args.build_manifest.resolve()),
                      engine_build_manifest_sha256=digest(args.build_manifest),
                      source_delta=delta, unchanged_integrity_test_object=test)
        unchanged()
        entry = dict(argv=[str(args.nextpnr.resolve()), '--device', PART,
                           '--chipdb', str(args.chipdb.resolve()), '--test'],
                     log='database_integrity.log', status='RUNNING', timeout_seconds=args.timeout)
        report['commands'].append(entry)
        save()
        started = time.monotonic()
        with (out / entry['log']).open('w') as log:
            process = subprocess.Popen(entry['argv'], stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            entry['pid'] = process.pid
            save()
            try:
                rc = process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                entry.update(status='TIMEOUT', returncode=process.returncode,
                             seconds=round(time.monotonic() - started, 3))
                raise RuntimeError('Full architecture test exceeded its timeout')
        entry.update(status='PASS' if rc == 0 else 'FAIL', returncode=rc,
                     seconds=round(time.monotonic() - started, 3))
        save()
        if rc:
            raise RuntimeError('Full architecture test failed; see database_integrity.log')
        log = (out / entry['log']).read_text()
        position = 0
        for phase in PHASES:
            found = log.find(phase, position)
            if found < 0:
                raise RuntimeError('Full architecture test lacks phase: ' + phase)
            position = found + len(phase)
        if 'ERROR:' in log or 'FATAL:' in log:
            raise RuntimeError('Architecture test reported an error despite its exit status')
        unchanged()
        report.update(status='PASS_FULL_DATABASE_CHECK', phases=list(PHASES),
                      log_sha256=digest(out / entry['log']),
                      completion_evidence='All phase markers in order and rc=0; pinned executeMain returns 0 only after archcheck returns')
    except Exception as exc:
        report.update(status='FAIL', reason=str(exc))
    save()
    print(json.dumps(dict(status=report['status'], reason=report.get('reason'),
                         report=str(out / 'report.json')), indent=2))
    return 0 if report['status'] == 'PASS_FULL_DATABASE_CHECK' else 1


if __name__ == '__main__':
    raise SystemExit(main())
