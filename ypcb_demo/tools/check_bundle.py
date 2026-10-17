#!/usr/bin/env python3
"""Check a prepared bundle's hashes and run RTL verification after clean extraction."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path, help='prepared build run directory')
    args = parser.parse_args()
    build = json.loads((args.run / 'report.json').read_text())
    bundle = args.run / 'source-bundle.tar.gz'
    if digest(bundle) != build['source_bundle_sha256']:
        raise RuntimeError('Bundle hash differs from its build report')
    out = args.run / ('bundle-check-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir()
    report = {'status': 'RUNNING', 'bundle_sha256': digest(bundle), 'files_verified': 0}
    try:
        with tempfile.TemporaryDirectory(prefix='tiny-tpu-ypcb-bundle-') as tmp:
            root = Path(tmp)
            with tarfile.open(bundle) as archive:
                archive.extractall(root, filter='data')
            for name, expected in build['source_hashes'].items():
                path = (root / name).resolve()
                if not path.is_relative_to(root) or digest(path) != expected:
                    raise RuntimeError(f'Bundle file hash mismatch: {name}')
                report['files_verified'] += 1
            with (out / 'verify.log').open('w') as log:
                regression = subprocess.run(['python3', 'ypcb_demo/tools/test_hardware.py'], cwd=root,
                                            stdout=log, stderr=subprocess.STDOUT, timeout=30)
                report['loader_regression_returncode'] = regression.returncode
                if regression.returncode:
                    raise RuntimeError('Extracted-bundle loader regression failed; see verify.log')
                if 'ypcb_demo/tools/test_observe_selftest.py' in build['source_hashes']:
                    observer = subprocess.run(['python3', 'ypcb_demo/tools/test_observe_selftest.py'], cwd=root,
                                              stdout=log, stderr=subprocess.STDOUT, timeout=30)
                    report['observer_regression_returncode'] = observer.returncode
                    if observer.returncode:
                        raise RuntimeError('Extracted-bundle observer regression failed; see verify.log')
                else:
                    report['observer_regression'] = 'NOT_IN_HISTORICAL_BUNDLE'
                if 'ypcb_demo/tools/test_hardware_result.py' in build['source_hashes']:
                    evidence = subprocess.run(['python3', 'ypcb_demo/tools/test_hardware_result.py'], cwd=root,
                                              stdout=log, stderr=subprocess.STDOUT, timeout=30)
                    report['hardware_evidence_regression_returncode'] = evidence.returncode
                    if evidence.returncode:
                        raise RuntimeError('Extracted-bundle hardware evidence regression failed; see verify.log')
                else:
                    report['hardware_evidence_regression'] = 'NOT_IN_HISTORICAL_BUNDLE'
                result = subprocess.run(['python3', 'ypcb_demo/tools/verify.py'], cwd=root,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=180)
            report['returncode'] = result.returncode
            for p in (root / 'ypcb_demo/artifacts').glob('verify-*'):
                shutil.copytree(p, out / p.name)
            if result.returncode:
                raise RuntimeError('Extracted-bundle RTL tests failed; see verify.log')
            report['status'] = 'PASS'
    except Exception as exc:
        report.update(status='FAIL', reason=str(exc))
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'report': str(out / 'report.json'),
                      'files_verified': report['files_verified']}, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
