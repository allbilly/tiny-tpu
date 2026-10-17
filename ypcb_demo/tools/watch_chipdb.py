#!/usr/bin/env python3
"""Observe resource use of check_chipdb.py without changing its full test."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

DEMO = Path(__file__).resolve().parents[1]
TICKS = os.sysconf('SC_CLK_TCK')
PAGE = os.sysconf('SC_PAGE_SIZE')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def sample_process(pid):
    """Linux stat fields after the final ')' start at field 3 (state)."""
    directory = Path('/proc') / str(pid)
    raw = (directory / 'stat').read_text()
    values = raw[raw.rfind(')') + 2:].split()
    return dict(pid=pid, ppid=int(values[1]), state=values[0],
                command=raw[raw.find('(') + 1:raw.rfind(')')],
                user_seconds=int(values[11]) / TICKS,
                system_seconds=int(values[12]) / TICKS,
                nice=int(values[16]), threads=int(values[17]),
                start_ticks=int(values[19]), virtual_bytes=int(values[20]),
                rss_bytes=int(values[21]) * PAGE)


def descendants(root):
    pending, rows, errors = [root], [], []
    seen = set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            rows.append(sample_process(pid))
            children = Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
            pending.extend(int(child) for child in children)
        except (FileNotFoundError, ProcessLookupError):
            pass  # A process may exit between enumeration and collection.
        except (OSError, ValueError) as exc:
            errors.append(dict(pid=pid, reason=str(exc)))
    return rows, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-manifest', type=Path, required=True)
    parser.add_argument('--nextpnr', type=Path, required=True)
    parser.add_argument('--chipdb', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=28800)
    parser.add_argument('--interval', type=float, default=30)
    args = parser.parse_args()
    if args.timeout <= 0 or args.interval <= 0:
        parser.error('Timeout and sample interval must be positive')
    base = DEMO / 'artifacts'
    known = set(base.glob('chipdb-check-*/report.json'))
    out = base / ('chipdb-resource-watch-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    checker = DEMO / 'tools/check_chipdb.py'
    argv = [sys.executable, '-u', str(checker), '--build-manifest', str(args.build_manifest.resolve()),
            '--nextpnr', str(args.nextpnr.resolve()), '--chipdb', str(args.chipdb.resolve()),
            '--timeout', str(args.timeout)]
    inputs = {str(p.resolve()): digest(p) for p in
              (Path(__file__), checker, args.build_manifest, args.nextpnr, args.chipdb)}
    report = dict(status='STARTING', utc=datetime.now(timezone.utc).isoformat(), argv=argv,
                  process_nice=os.getpriority(os.PRIO_PROCESS, 0), inputs_sha256=inputs,
                  sample_interval_seconds=args.interval, samples=0, hardware_programmed=False,
                  scope='Read-only CPU/RSS/process-state observation; check_chipdb.py retains all acceptance gates')

    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    save()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    child = subprocess.Popen(argv)
    report.update(status='RUNNING_FULL_DATABASE_CHECK', checker_pid=child.pid)
    print(json.dumps(dict(observer_report=str(out / 'report.json'), checker_pid=child.pid)), flush=True)
    save()
    next_sample = started
    with (out / 'samples.jsonl').open('w') as samples:
        while child.poll() is None:
            now = time.monotonic()
            if now >= next_sample:
                rows, errors = descendants(child.pid)
                entry = dict(utc=datetime.now(timezone.utc).isoformat(),
                             elapsed_seconds=round(now - started, 3), processes=rows, errors=errors)
                samples.write(json.dumps(entry) + '\n')
                samples.flush()
                report['samples'] += 1
                fresh = set(base.glob('chipdb-check-*/report.json')) - known
                if len(fresh) == 1:
                    report['database_check_report'] = str(next(iter(fresh)).resolve())
                save()
                next_sample = now + args.interval
            time.sleep(min(1, args.interval))
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    report.update(status='CHECK_PROCESS_FINISHED', returncode=child.returncode,
                  wall_seconds=round(time.monotonic() - started, 3),
                  child_tree_user_seconds=after.ru_utime - before.ru_utime,
                  child_tree_system_seconds=after.ru_stime - before.ru_stime,
                  child_tree_max_rss_kib=after.ru_maxrss,
                  samples_sha256=digest(out / 'samples.jsonl'))
    report['inputs_unchanged'] = all(digest(p) == expected for p, expected in inputs.items())
    save()
    print(json.dumps(dict(observer_report=str(out / 'report.json'), returncode=child.returncode)), flush=True)
    return child.returncode if child.returncode >= 0 and report['inputs_unchanged'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
