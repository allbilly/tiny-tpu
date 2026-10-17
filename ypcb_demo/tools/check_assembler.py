#!/usr/bin/env python3
"""Compare the native assembler with exact-part reference frames and bit data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from hardware import bitstream_part

DEMO = Path(__file__).resolve().parents[1]
PART = 'xc7k480tffg1156-2'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def payload(path):
    data = path.read_bytes()
    pos = 2 + int.from_bytes(data[:2], 'big')
    if data[pos:pos + 2] != b'\0\1':
        raise ValueError('Invalid bitstream header prefix')
    pos += 2
    for tag in b'abcde':
        if data[pos:pos + 1] != bytes([tag]):
            raise ValueError('Invalid bitstream header tag')
        pos += 1
        width = 4 if tag == ord('e') else 2
        size = int.from_bytes(data[pos:pos + width], 'big')
        pos += width
        if pos + size > len(data):
            raise ValueError('Truncated bitstream field')
        if tag == ord('e'):
            if pos + size != len(data) or size % 4:
                raise ValueError('Invalid configuration payload length')
            return data[pos:pos + size]
        pos += size
    raise ValueError('Missing configuration payload')


def allowed_frames(part):
    addresses = set()
    buses = {'CLB_IO_CLK': 0, 'BLOCK_RAM': 1, 'CFG_CLB': 2}
    for half, region in part['global_clock_regions'].items():
        for row, config_row in region['rows'].items():
            for kind, bus in config_row['configuration_buses'].items():
                for column, config_column in bus['configuration_columns'].items():
                    base = (buses[kind] << 23) | ((half == 'bottom') << 22) | (int(row) << 17) | (int(column) << 7)
                    addresses.update(base + minor for minor in range(config_column['frame_count']))
    return addresses


def frames(path, allowed):
    result = {}
    for number, line in enumerate(path.open(), 1):
        fields = line.split()
        if len(fields) != 2:
            raise ValueError(f'Malformed frame row {number}')
        address = int(fields[0], 16)
        words = tuple(int(word, 16) for word in fields[1].split(','))
        if address not in allowed or address in result:
            raise ValueError(f'Invalid or duplicated frame address {address:#x}')
        if len(words) != 101 or any(word < 0 or word > 0xffffffff for word in words):
            raise ValueError(f'Invalid 101-word frame row {number}')
        result[address] = words
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fpga-as', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--reference-run', type=Path, required=True)
    parser.add_argument('--reference-stem', default='empty-design',
                        help='reference FASM/frame/bit filename stem')
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    out = DEMO / 'artifacts' / ('assembler-check-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    report = {'status': 'RUNNING', 'scope': 'Reference-fixture frame/bit equivalence and malformed-FASM rejection only; no TPU P&R or board result',
              'part': PART, 'hardware_programmed': False, 'commands': [], 'failures': []}

    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def assemble(fasm, stem, dump=False):
        argv = [str(args.fpga_as.resolve()), '--prjxray_db_path=' + str(args.database.resolve() / 'kintex7'),
                '--part=' + PART]
        if dump:
            argv.append('--dump_frames_file=' + str(out / (stem + '.frames')))
        entry = {'argv': argv, 'input': fasm.name, 'output': stem + '.bit', 'log': stem + '.log',
                 'status': 'RUNNING', 'timeout_seconds': args.timeout}
        report['commands'].append(entry)
        save()
        before = time.monotonic()
        try:
            with fasm.open('rb') as source, (out / entry['output']).open('wb') as bit, (out / entry['log']).open('w') as log:
                result = subprocess.run(argv, stdin=source, stdout=bit, stderr=log, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            entry.update(status='TIMEOUT', seconds=round(time.monotonic() - before, 3))
            save()
            raise
        entry.update(status='RETURNED', returncode=result.returncode, seconds=round(time.monotonic() - before, 3))
        save()
        return result.returncode

    try:
        donor = args.reference_run.resolve()
        stem = args.reference_stem
        if not stem or Path(stem).name != stem or stem in ('.', '..'):
            raise ValueError('Reference stem must be a filename stem')
        fasm_name, frame_name, bit_name = (stem + suffix for suffix in ('.fasm', '.frames', '.bit'))
        previous = json.loads((donor / 'report.json').read_text())
        if previous.get('status') != 'PASS_EXACT_PART_FRAME_TOOLS_ONLY' or previous.get('part') != PART:
            raise ValueError('Reference is not a passing exact-part frame-tool smoke run')
        for name in (fasm_name, frame_name, bit_name):
            if digest(donor / name) != previous.get('artifacts', {}).get(name):
                raise ValueError(f'Reference artifact changed: {name}')
        part_json = args.database / 'kintex7' / PART / 'part.json'
        report['inputs_sha256'] = {str(p): digest(p) for p in
                                  (Path(__file__).resolve(), args.fpga_as, part_json, donor / 'report.json',
                                   donor / fasm_name, donor / frame_name, donor / bit_name)}
        report['reference_run'] = str(donor)
        report['reference_scope'] = previous.get('scope')
        report['reference_stem'] = stem
        shutil.copy2(donor / fasm_name, out / fasm_name)
        if assemble(out / fasm_name, 'native-reference', dump=True):
            raise ValueError('Native assembler failed on the reference FASM')
        if bitstream_part(out / 'native-reference.bit') != PART:
            raise ValueError('Native bitstream has a different part header')
        allowed = allowed_frames(json.loads(part_json.read_text()))
        native = frames(out / 'native-reference.frames', allowed)
        reference = frames(donor / frame_name, allowed)
        zero = (0,) * 101
        mismatches = [address for address in sorted(allowed)
                      if native.get(address, zero) != reference.get(address, zero)]
        report['frames'] = {'legal_target_frames': len(allowed), 'native_supplied': len(native),
                            'reference_supplied': len(reference), 'effective_mismatches': len(mismatches),
                            'reference_nonzero_words': sum(word != 0 for words in reference.values() for word in words),
                            'first_mismatches': [hex(address) for address in mismatches[:8]]}
        if mismatches:
            report['failures'].append('Native effective frames differ from the reference')
        a, b = payload(out / 'native-reference.bit'), payload(donor / bit_name)
        report['configuration_payload'] = {'native_bytes': len(a), 'reference_bytes': len(b),
                                           'native_sha256': hashlib.sha256(a).hexdigest(),
                                           'reference_sha256': hashlib.sha256(b).hexdigest(), 'equal': a == b}
        if a != b:
            report['failures'].append('Configuration payload differs after excluding only .bit metadata')
        for stem, text in (
                ('unknown-feature', 'NONEXISTENT_TILE_X0Y0.NONEXISTENT_FEATURE\n'),
                ('inverted-range', 'NONEXISTENT_TILE_X0Y0.NONEXISTENT_FEATURE[0:8] = 1\n')):
            fixture = out / (stem + '.fasm')
            fixture.write_text(text)
            if assemble(fixture, stem) == 0:
                report['failures'].append(f'Assembler accepted invalid FASM: {stem}')
        for name, expected in report['inputs_sha256'].items():
            if digest(Path(name)) != expected:
                report['failures'].append(f'Input changed during checks: {name}')
        if report['failures']:
            raise ValueError('; '.join(report['failures']))
        report['status'] = 'PASS_ASSEMBLER_COMPONENT_CHECK'
        report['output_sha256'] = {p.name: digest(p) for p in out.iterdir() if p.is_file() and p.name != 'report.json'}
    except Exception as exc:
        report.update(status='FAIL', reason=str(exc))
    save()
    print(json.dumps({'status': report['status'], 'report': str(out / 'report.json'),
                      'reason': report.get('reason')}, indent=2))
    return 0 if report['status'] == 'PASS_ASSEMBLER_COMPONENT_CHECK' else 1


if __name__ == '__main__':
    raise SystemExit(main())
