#!/usr/bin/env python3
"""Build the pinned DSP fix using unchanged objects from the base engine."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
import threading
import time

from openxc7 import checkout, digest

BASE_REV = 'c68c13582e972292c86a5025140d52e713384cbc'
FIX_REV = 'a662c08d25d7d2524ab341a387f120521c8a5d08'
CHANGED = {'himbaechel/uarch/xilinx/fasm.cc', 'himbaechel/uarch/xilinx/pack_dsp_xc7.cc'}
DEMO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-source', type=Path, required=True)
    parser.add_argument('--base-build', type=Path, required=True)
    parser.add_argument('--base-build-log', type=Path, required=True)
    parser.add_argument('--fixed-source', type=Path, required=True)
    parser.add_argument('--wait-seconds', type=int, default=14400)
    args = parser.parse_args()
    base, build, fixed = (p.resolve() for p in (args.base_source, args.base_build, args.fixed_source))
    out = DEMO / 'artifacts/openxc7-toolchain' / ('nextpnr-pr66-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    report = dict(status='RUNNING', base_revision=BASE_REV, source_revision=FIX_REV,
                  scope='Two upstream DSP translation units rebuilt; other engine objects reused read-only from identical base source',
                  commands=[], hardware_programmed=False)
    report_lock = threading.RLock()

    def save():
        with report_lock:
            (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(argv, name):
        entry = dict(argv=list(map(str, argv)), log=name + '.log', status='RUNNING', timeout_seconds=7200)
        with report_lock:
            report['commands'].append(entry)
            save()
        before = time.monotonic()
        try:
            with (out / entry['log']).open('w') as log:
                result = subprocess.run(entry['argv'], cwd=out, stdout=log, stderr=subprocess.STDOUT, timeout=7200)
        except subprocess.TimeoutExpired:
            with report_lock:
                entry.update(status='TIMEOUT', seconds=round(time.monotonic() - before, 3))
                save()
            raise
        with report_lock:
            entry.update(status='PASS' if result.returncode == 0 else 'FAIL', returncode=result.returncode,
                         seconds=round(time.monotonic() - before, 3))
            save()
        if result.returncode:
            raise ValueError('Build command failed: ' + name)

    try:
        checkout(base, BASE_REV)
        checkout(fixed, FIX_REV)
        changed = set(subprocess.check_output(['git', '-C', str(fixed), 'diff', '--name-only', BASE_REV, FIX_REV], text=True).splitlines())
        if changed != CHANGED:
            raise ValueError('Upstream fix changes more than the two reviewed DSP files')
        patch = subprocess.check_output(['git', '-C', str(fixed), 'diff', BASE_REV, FIX_REV])
        (out / 'upstream-pr66.diff').write_bytes(patch)
        for submodule in ('himbaechel/uarch/xilinx/meta', '3rdparty/googletest'):
            a = subprocess.check_output(['git', '-C', str(base), 'ls-tree', 'HEAD', submodule])
            b = subprocess.check_output(['git', '-C', str(fixed), 'ls-tree', 'HEAD', submodule])
            if a != b:
                raise ValueError('Submodule pin differs: ' + submodule)
        flags_path = build / 'himbaechel/CMakeFiles/nextpnr-himbaechel.dir/flags.make'
        link_path = build / 'himbaechel/CMakeFiles/nextpnr-himbaechel.dir/link.txt'
        flags = dict(line.split(' = ', 1) for line in flags_path.read_text().splitlines() if ' = ' in line)
        compiler = '/usr/bin/c++'
        report['inputs_sha256'] = {str(p): digest(p) for p in
                                  (Path(__file__).resolve(), flags_path, link_path, Path(compiler))}
        report['patch_sha256'] = digest(out / 'upstream-pr66.diff')
        report['changed_source_sha256'] = {name: digest(fixed / name) for name in sorted(CHANGED)}
        arguments = []
        for key in ('CXX_DEFINES', 'CXX_INCLUDES', 'CXX_FLAGS'):
            arguments.extend(shlex.split(flags[key].replace(str(base), str(fixed))))
        replacements = {}
        # Each unit is independent; both outputs belong only to this run.
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = []
            for name in sorted(CHANGED):
                target = out / (Path(name).name + '.o')
                replacements['CMakeFiles/nextpnr-himbaechel.dir/uarch/xilinx/' + Path(name).name + '.o'] = str(target)
                futures.append(pool.submit(run, [compiler, *arguments, '-o', target, '-c', fixed / name], Path(name).name))
            for future in futures:
                future.result()
        report['status'] = 'WAITING_BASE_BUILD'
        save()
        deadline = time.monotonic() + args.wait_seconds
        while True:
            log = args.base_build_log.read_text(errors='replace')
            if 'Built target nextpnr-himbaechel\n' in log and (build / 'nextpnr-himbaechel').is_file():
                break
            if time.monotonic() >= deadline:
                raise ValueError('Base build did not finish within the wait limit')
            time.sleep(10)
        checkout(base, BASE_REV)
        checkout(fixed, FIX_REV)
        link = shlex.split(link_path.read_text())
        for index, token in enumerate(link):
            if token in replacements:
                link[index] = replacements[token]
            elif token.endswith(('.o', '.a')):
                link[index] = str((build / 'himbaechel' / token).resolve())
            elif token.startswith('-Wl,--dependency-file='):
                link[index] = '-Wl,--dependency-file=' + str(out / 'link.d')
        output = out / 'nextpnr-himbaechel'
        link[link.index('-o') + 1] = str(output)
        objects = [Path(a) for a in link if a.endswith(('.o', '.a', '.so.1.90.0'))]
        report['link_inputs_sha256'] = {str(p): digest(p) for p in objects}
        report['base_build_log_sha256'] = digest(args.base_build_log)
        report['base_executable_sha256'] = digest(build / 'nextpnr-himbaechel')
        run(link, 'link')
        run([output, '--version'], 'version')
        for names in ('inputs_sha256', 'link_inputs_sha256'):
            for name, expected in report[names].items():
                if digest(Path(name)) != expected:
                    raise ValueError('Input changed during build: ' + name)
        for name, expected in report['changed_source_sha256'].items():
            if digest(fixed / name) != expected:
                raise ValueError('Fixed source changed during build: ' + name)
        checkout(base, BASE_REV)
        checkout(fixed, FIX_REV)
        report.update(status='BUILT_PR66_ENGINE_VARIANT', executable=str(output), executable_sha256=digest(output),
                      version_note='CLI version string comes from reused base objects; source revision and DSP patch are identified by this manifest')
        save()
        print('YPCB PR66 ENGINE BUILD PASS', flush=True)
    except Exception as exc:
        report.update(status='FAIL', reason=str(exc))
        save()
    print(json.dumps(dict(status=report['status'], report=str(out / 'report.json'), reason=report.get('reason')), indent=2))
    return 0 if report['status'] == 'BUILT_PR66_ENGINE_VARIANT' else 1


if __name__ == '__main__':
    raise SystemExit(main())
