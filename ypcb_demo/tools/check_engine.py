#!/usr/bin/env python3
"""Validate the recorded PR66 engine and its optional local lookup patch."""
import hashlib
import json
from pathlib import Path

HEAD = 'a662c08d25d7d2524ab341a387f120521c8a5d08'
BASE = 'c68c13582e972292c86a5025140d52e713384cbc'
LOOKUP_PATCH_SHA = '562a196ce64af6817d57dedc849e6634e6698b23dae650b223e60b9aa2c091c9'
ORIGINAL_SHA = '4043a5803665d268a1a12a6ff67a21934a2281d5da7019aa0493bb541a6bc55e'
PATCHED_SHA = '94da5d9911f1369bb30284cd1b2b67a95efee1288264193412ff681e09fbb902'
INDEX_PATCH_SHA = 'effd522ce9c6fdb9ad7f77b97fde25e8e2ca7b21e8b44127726704048176bced'
INDEX_SOURCE_SHA = '62b8d0a318ae1363c302f8cc868e921b7649eaf64b8a7be4502a7e1e40c5be68'
DATABASE_PHASES = ('Running architecture database integrity check.', 'Checking entity names.',
                   'Checking pip names..', 'Checking location data.', 'Checking connectivity data.',
                   'Checking all PIPs...', 'Checking bucket data.')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def validate_manifest(path, executable):
    """Check actual files and the single-object delta; no tool or cable action."""
    path, executable = Path(path).resolve(), Path(executable).resolve()
    manifest = json.loads(path.read_text())
    status = manifest.get('status')
    if (status not in ('BUILT_PR66_ENGINE_VARIANT', 'BUILT_PR66_LOOKUP_ENGINE_VARIANT',
                      'BUILT_PR66_INDEX_ENGINE_VARIANT') or
            manifest.get('source_revision') != HEAD or manifest.get('base_revision') != BASE or
            manifest.get('executable_sha256') != digest(executable) or
            Path(manifest.get('executable', '')).resolve() != executable):
        raise RuntimeError('Engine does not match its passing pinned build manifest')
    if status == 'BUILT_PR66_ENGINE_VARIANT':
        return manifest
    patch_sha, source_sha, patch_name = (
        (INDEX_PATCH_SHA, INDEX_SOURCE_SHA, 'nextpnr-pip-name-index.patch')
        if status == 'BUILT_PR66_INDEX_ENGINE_VARIANT' else
        (LOOKUP_PATCH_SHA, PATCHED_SHA, 'nextpnr-pip-name-lookup.patch'))
    delta = manifest.get('source_delta', {})
    if (delta.get('source_file') != 'himbaechel/arch.cc' or
            delta.get('patch_sha256') != patch_sha or
            delta.get('original_sha256') != ORIGINAL_SHA or
            delta.get('patched_sha256') != source_sha or
            digest(delta['patch_path']) != patch_sha or
            digest(Path(__file__).with_name(patch_name)) != patch_sha or
            digest(delta['patched_source_path']) != source_sha):
        raise RuntimeError('Engine lookup delta differs from the reviewed source patch')
    parent_entry = manifest.get('parent_manifest', {})
    parent_path = Path(parent_entry['path'])
    if digest(parent_path) != parent_entry.get('sha256'):
        raise RuntimeError('Parent engine manifest changed after the lookup build')
    parent = validate_manifest(parent_path, json.loads(parent_path.read_text())['executable'])
    if (parent['status'] != 'BUILT_PR66_ENGINE_VARIANT' or
            parent['executable_sha256'] != parent_entry.get('executable_sha256')):
        raise RuntimeError('Lookup engine lacks its exact passing PR66 parent')
    before, after = parent['link_inputs_sha256'], manifest['link_inputs_sha256']
    removed, added = set(before) - set(after), set(after) - set(before)
    if (len(removed) != 1 or len(added) != 1 or
            not next(iter(removed)).endswith('/nextpnr-himbaechel.dir/arch.cc.o') or
            not next(iter(added)).endswith('/arch.cc.o') or
            any(before[p] != after[p] for p in set(before) & set(after))):
        raise RuntimeError('Lookup engine changes more than the single architecture object')
    unchanged_test = {p: h for p, h in before.items() if p.endswith('/archcheck.cc.o')}
    if len(unchanged_test) != 1 or manifest.get('unchanged_integrity_test_object') != unchanged_test:
        raise RuntimeError('Lookup engine does not preserve the full architecture-test object')
    if manifest.get('raw_adjacency', {}).get('status') != 'PASS_RAW_ADJACENCY_CENSUS':
        raise RuntimeError('Lookup engine lacks the raw adjacency precondition check')
    commands = manifest.get('commands', [])
    if ([c.get('log') for c in commands] != ['apply_patch.log', 'arch.cc.log', 'link.log', 'version.log'] or
            any(c.get('status') != 'PASS' or c.get('returncode') != 0 for c in commands)):
        raise RuntimeError('Lookup engine build commands did not all pass')
    for record in (parent, manifest):
        for group in ('inputs_sha256', 'link_inputs_sha256'):
            for name, expected in record[group].items():
                if digest(name) != expected:
                    raise RuntimeError('Engine build input changed: ' + name)
    return manifest


def validate_database_check(directory, executable, chipdb):
    """Accept only an actual completed full test for these exact binary inputs."""
    directory = Path(directory).resolve()
    executable, chipdb = Path(executable).resolve(), Path(chipdb).resolve()
    report = json.loads((directory / 'report.json').read_text())
    part = 'xc7k480tffg1156-2'
    if (report.get('status') != 'PASS_FULL_DATABASE_CHECK' or report.get('part') != part or
            report.get('executable_sha256') != digest(executable) or
            report.get('chipdb_sha256') != digest(chipdb) or
            Path(report.get('executable', '')).resolve() != executable or
            Path(report.get('chipdb', '')).resolve() != chipdb):
        raise RuntimeError('Database check is not a completed pass for these exact inputs')
    manifest_path = Path(report['engine_build_manifest'])
    if digest(manifest_path) != report.get('engine_build_manifest_sha256'):
        raise RuntimeError('Database-check engine manifest changed')
    manifest = validate_manifest(manifest_path, executable)
    if (manifest.get('source_delta') != report.get('source_delta') or
            manifest.get('unchanged_integrity_test_object') != report.get('unchanged_integrity_test_object') or
            manifest['inputs_sha256'].get(str(chipdb)) != report['chipdb_sha256']):
        raise RuntimeError('Database check differs from its build and database provenance')
    commands = report.get('commands', [])
    expected = [str(executable), '--device', part, '--chipdb', str(chipdb), '--test']
    if (len(commands) != 1 or commands[0].get('argv') != expected or
            commands[0].get('log') != 'database_integrity.log' or
            commands[0].get('status') != 'PASS' or commands[0].get('returncode') != 0):
        raise RuntimeError('Database check did not execute the exact full-test command')
    log_path = directory / 'database_integrity.log'
    if digest(log_path) != report.get('log_sha256') or report.get('phases') != list(DATABASE_PHASES):
        raise RuntimeError('Database-check log differs from its complete result')
    log = log_path.read_text()
    position = 0
    for phase in DATABASE_PHASES:
        found = log.find(phase, position)
        if found < 0:
            raise RuntimeError('Database-check log lacks phase: ' + phase)
        position = found + len(phase)
    if 'ERROR:' in log or 'FATAL:' in log:
        raise RuntimeError('Database-check log contains an error')
    inputs = report.get('inputs_sha256', {})
    required = (Path(__file__).with_name('check_chipdb.py').resolve(),
                executable, chipdb, manifest_path.resolve())
    if any(inputs.get(str(p)) != digest(p) for p in required):
        raise RuntimeError('Database check lacks unchanged checker, executable or database inputs')
    for name, expected_hash in inputs.items():
        if digest(name) != expected_hash:
            raise RuntimeError('Database-check input changed: ' + name)
    return report
