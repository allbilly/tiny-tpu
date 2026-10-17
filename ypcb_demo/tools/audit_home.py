#!/usr/bin/env python3
"""Inventory home projects and inspect source/build documents for useful FPGA work."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HOME_ROOT = Path('/home/fedora')
DEMO = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.cache', '.local', '.venv', 'venv', 'node_modules', '__pycache__',
        'build', 'builds', 'sim_build', 'output', 'output_files', 'artifacts',
        'obj_dir', 'target', 'dist', 'vendor', 'third_party', 'third-party',
        'external', 'runs', 'waveforms', 'site-packages', 'go', '.Xil', '.xinstall'}
PATTERN = re.compile(r'ypcb|xc7k480|openxc7|nextpnr|prjxray|synth_xilinx|vivado|jtag|tiny.tpu|systolic|cocotb', re.I)
EXTENSIONS = {'.md', '.rst', '.xdc', '.tcl', '.qsf', '.sdc', '.yml', '.yaml', '.toml'}


def command(argv):
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=20)
        return {'argv': argv, 'returncode': result.returncode,
                'stdout': result.stdout.strip(), 'stderr': result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'argv': argv, 'error': str(exc)}


def main():
    report = {'collected_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Every non-hidden top-level home directory; recursive source/build-document scan, generated/dependency directories excluded. Symlinks are not followed. Files are read up to 256 KiB.',
              'excluded_directory_names': sorted(SKIP), 'directories': [],
              'tools': {n: shutil.which(n) for n in ('iverilog', 'vvp', 'yosys', 'verilator', 'vivado', 'vivado_lab', 'openFPGALoader', 'nextpnr-xilinx', 'nix')},
              'live_commands': [command(['lsusb']), command(['findmnt', '-rn', '-o', 'TARGET,SOURCE,FSTYPE']),
                                command(['podman', 'ps', '-a', '--format', '{{.Names}} {{.Image}} {{.Status}}']),
                                command(['podman', 'images', '--format', '{{.Repository}}:{{.Tag}}'])]}
    for root in sorted(HOME_ROOT.iterdir()):
        if not root.is_dir() or root.name.startswith('.'):
            continue
        row = {'path': str(root), 'git': (root / '.git').exists(), 'files_examined': 0,
               'matching_files': 0, 'evidence': [], 'errors': []}
        if row['git']:
            row['revision'] = command(['git', '-C', str(root), 'rev-parse', 'HEAD'])
        for cur, dirs, files in os.walk(root, onerror=lambda e: row['errors'].append(str(e))):
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith('.'))
            for name in sorted(files):
                p = Path(cur) / name
                if p.suffix.lower() not in EXTENSIONS and name not in ('Makefile', 'CMakeLists.txt'):
                    continue
                if p.is_symlink():
                    continue
                row['files_examined'] += 1
                try:
                    with p.open('r', errors='replace') as stream:
                        content = stream.read(262144)
                    matches = [(n, line[:240]) for n, line in enumerate(content.splitlines(), 1) if PATTERN.search(line)]
                    if matches:
                        row['matching_files'] += 1
                        # Prioritize direct board evidence rather than repeated generic tool references.
                        priority = 2 if re.search(r'ypcb|xc7k480|openxc7', content, re.I) else 1
                        row['evidence'].append({'file': str(p), 'priority': priority, 'lines': matches[:4]})
                except OSError as exc:
                    row['errors'].append(str(exc))
        row['evidence'].sort(key=lambda e: (-e['priority'], len(e['file']), e['file']))
        row['evidence'] = row['evidence'][:12]
        report['directories'].append(row)
    out = DEMO / 'artifacts' / ('home-audit-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'Report: {out / "report.json"}')
    for row in report['directories']:
        print(f'{Path(row["path"]).name}: {row["files_examined"]} source/build documents, {row["matching_files"]} matching files')


if __name__ == '__main__':
    main()
