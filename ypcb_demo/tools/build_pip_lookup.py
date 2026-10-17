#!/usr/bin/env python3
"""Rebuild only the PIP-name lookup unit on the verified PR66 engine."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import mmap
from pathlib import Path
import signal
import struct
import subprocess
import time

DEMO = Path(__file__).resolve().parents[1]
HEAD = 'a662c08d25d7d2524ab341a387f120521c8a5d08'
BASE = 'c68c13582e972292c86a5025140d52e713384cbc'
PATCH_SHA = '562a196ce64af6817d57dedc849e6634e6698b23dae650b223e60b9aa2c091c9'
ORIGINAL_SHA = '4043a5803665d268a1a12a6ff67a21934a2281d5da7019aa0493bb541a6bc55e'
PATCHED_SHA = '94da5d9911f1369bb30284cd1b2b67a95efee1288264193412ff681e09fbb902'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def clean_source(source):
    if subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip() != HEAD:
        raise ValueError('Expected the exact PR66 source head')
    for extra in ([], ['--cached']):
        subprocess.run(['git', '-C', str(source), 'diff', *extra, '--quiet'], check=True)


def adjacency_census(path):
    """Check the actual schema-6 raw adjacency lists needed by this lookup."""
    with path.open('rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        def read(offset, fmt):
            size = struct.calcsize(fmt)
            if offset < 0 or offset + size > len(data):
                raise ValueError('Chip database pointer outside the file')
            return struct.unpack_from(fmt, data, offset)

        def array(offset, width):
            relative, count = read(offset, '<iI')
            start = offset + relative
            if start < 0 or start + count * width > len(data):
                raise ValueError('Chip database slice outside the file')
            return start, count

        root = read(0, '<i')[0]
        magic, version, width, height = read(root, '<4i')
        if magic != 0x00ca7ca7 or version != 6:
            raise ValueError('Expected the pinned schema-6 database')
        types, count = array(root + 28, 40)
        instances, ntiles = array(root + 36, 16)
        if count != 98 or ntiles != 136359 or ntiles != width * height:
            raise ValueError('Database does not match the completed 480T dimensions')
        populations = Counter(read(instances + n * 16 + 4, '<i')[0] for n in range(ntiles))
        if any(t < 0 or t >= count for t in populations):
            raise ValueError('Invalid tile type in an instance')
        total_pips = total_wires = comparisons = 0
        rows = []
        for tile_type in range(count):
            record = types + tile_type * 40
            wires, nwires = array(record + 12, 48)
            pips, npips = array(record + 20, 24)
            endpoints = [read(pips + n * 24, '<2i') for n in range(npips)]
            if any(s < 0 or d < 0 or s >= nwires or d >= nwires for s, d in endpoints):
                raise ValueError('PIP endpoint outside its tile wire table')
            up_seen, down_seen = [0] * npips, [0] * npips
            names = []
            for wire in range(nwires):
                w = wires + wire * 48
                names.append(read(w, '<i')[0])
                for delta, endpoint, seen in ((24, 1, up_seen), (32, 0, down_seen)):
                    begin, length = array(w + delta, 4)
                    for n in range(length):
                        pip = read(begin + n * 4, '<i')[0]
                        if pip < 0 or pip >= npips or endpoints[pip][endpoint] != wire:
                            raise ValueError(f'Incorrect raw adjacency in tile type {tile_type}, wire {wire}')
                        seen[pip] += 1
            if any(n != 1 for n in up_seen + down_seen):
                raise ValueError('Every PIP must occur exactly once in each raw adjacency direction')
            population = populations[tile_type]
            total_pips += population * npips
            total_wires += population * nwires
            comparisons += population * npips * (npips + 1) // 2
            rows.append(dict(tile_type=tile_type, instances=population, wires=nwires, pips=npips,
                             repeated_wire_names=nwires - len(set(names))))
    return dict(status='PASS_RAW_ADJACENCY_CENSUS', tile_types=count, tile_instances=ntiles,
                expanded_pips=total_pips, expanded_raw_wires=total_wires, checked_types=rows,
                old_name_scan_iterations_if_every_name_roundtrip_passes=comparisons,
                scope='Raw per-tile-type up/down adjacency only; full architecture check remains required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-source', type=Path, required=True)
    parser.add_argument('--parent-manifest', type=Path, required=True)
    parser.add_argument('--chipdb', type=Path, required=True)
    args = parser.parse_args()
    source = args.engine_source.resolve()
    out = DEMO / 'artifacts/openxc7-toolchain' / ('nextpnr-lookup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    report = dict(status='RUNNING', base_revision=BASE, source_revision=HEAD,
                  scope='PR66 parent objects reused read-only; only arch.cc rebuilt with local PIP-name lookup patch; test body and database unchanged',
                  commands=[], hardware_programmed=False)

    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(argv, name):
        entry = dict(argv=list(map(str, argv)), log=name + '.log', status='RUNNING', timeout_seconds=7200)
        report['commands'].append(entry)
        save()
        started = time.monotonic()
        with (out / entry['log']).open('w') as log:
            process = subprocess.Popen(entry['argv'], cwd=out, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                rc = process.wait(timeout=7200)
            except subprocess.TimeoutExpired:
                try:
                    import os
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                entry.update(status='TIMEOUT', returncode=process.returncode,
                             seconds=round(time.monotonic() - started, 3))
                save()
                raise ValueError(name + ' exceeded 7200 seconds')
        entry.update(status='PASS' if rc == 0 else 'FAIL', returncode=rc,
                     seconds=round(time.monotonic() - started, 3))
        save()
        if rc:
            raise ValueError(name + ' failed; see its log')

    try:
        clean_source(source)
        parent = json.loads(args.parent_manifest.read_text())
        if (parent.get('status') != 'BUILT_PR66_ENGINE_VARIANT' or parent.get('source_revision') != HEAD or
                parent.get('base_revision') != BASE or digest(Path(parent['executable'])) != parent['executable_sha256']):
            raise ValueError('Parent is not the verified PR66 build')
        for group in ('inputs_sha256', 'link_inputs_sha256'):
            for name, expected in parent[group].items():
                if digest(Path(name)) != expected:
                    raise ValueError('Parent input changed: ' + name)
        for name, expected in parent['changed_source_sha256'].items():
            if digest(source / name) != expected:
                raise ValueError('Parent DSP source changed: ' + name)
        patch = DEMO / 'tools/nextpnr-pip-name-lookup.patch'
        original = source / 'himbaechel/arch.cc'
        if digest(patch) != PATCH_SHA or digest(original) != ORIGINAL_SHA:
            raise ValueError('Lookup patch or original source differs from the reviewed bytes')
        patched = out / 'source/himbaechel/arch.cc'
        patched.parent.mkdir(parents=True)
        patched.write_bytes(original.read_bytes())
        report['inputs_sha256'] = {str(p.resolve()): digest(p) for p in
                                  (Path(__file__), args.parent_manifest, args.chipdb, patch, original)}
        report['parent_manifest'] = dict(path=str(args.parent_manifest.resolve()),
                                         sha256=digest(args.parent_manifest), executable_sha256=parent['executable_sha256'])
        report['raw_adjacency'] = adjacency_census(args.chipdb)
        save()
        run(['patch', '--batch', '--forward', '-p1', '-d', out / 'source', '-i', patch], 'apply_patch')
        if digest(patched) != PATCHED_SHA:
            raise ValueError('Applied patch did not reproduce the reviewed source')
        report['source_delta'] = dict(patch_path=str(patch), patch_sha256=PATCH_SHA,
                                     source_file='himbaechel/arch.cc', original_sha256=ORIGINAL_SHA,
                                     patched_source_path=str(patched), patched_sha256=PATCHED_SHA,
                                     source_revision_note='source_revision identifies clean PR66 base; this delta identifies the additional local source change')
        compile_command = next(c['argv'] for c in parent['commands'] if c['log'] == 'fasm.cc.log')
        target = out / 'arch.cc.o'
        run([*compile_command[:compile_command.index('-o')], '-o', target, '-c', patched], 'arch.cc')
        link = list(next(c['argv'] for c in parent['commands'] if c['log'] == 'link.log'))
        old_arch = [p for p in parent['link_inputs_sha256'] if p.endswith('/nextpnr-himbaechel.dir/arch.cc.o')]
        if len(old_arch) != 1 or link.count(old_arch[0]) != 1:
            raise ValueError('Parent link does not contain exactly one architecture unit')
        link[link.index(old_arch[0])] = str(target)
        executable = out / 'nextpnr-himbaechel'
        link[link.index('-o') + 1] = str(executable)
        link = ['-Wl,--dependency-file=' + str(out / 'link.d') if x.startswith('-Wl,--dependency-file=') else x for x in link]
        objects = [Path(x) for x in link if x.endswith(('.o', '.a', '.so.1.90.0'))]
        report['link_inputs_sha256'] = {str(p): digest(p) for p in objects}
        report['unchanged_integrity_test_object'] = {p: h for p, h in parent['link_inputs_sha256'].items() if p.endswith('/archcheck.cc.o')}
        run(link, 'link')
        run([executable, '--version'], 'version')
        for group in ('inputs_sha256', 'link_inputs_sha256'):
            for name, expected in report[group].items():
                if digest(Path(name)) != expected:
                    raise ValueError('Build input changed: ' + name)
        if digest(patched) != PATCHED_SHA:
            raise ValueError('Patched source changed during compilation')
        clean_source(source)
        report.update(status='BUILT_PR66_LOOKUP_ENGINE_VARIANT', executable=str(executable),
                      executable_sha256=digest(executable),
                      version_note='CLI version is from reused base objects; PR66 base and additional local lookup patch are recorded here')
    except Exception as exc:
        report.update(status='FAIL', reason=str(exc))
    save()
    print(json.dumps(dict(status=report['status'], report=str(out / 'report.json'), reason=report.get('reason')), indent=2))
    return 0 if report['status'] == 'BUILT_PR66_LOOKUP_ENGINE_VARIANT' else 1


if __name__ == '__main__':
    raise SystemExit(main())
