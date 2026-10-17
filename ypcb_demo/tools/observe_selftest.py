#!/usr/bin/env python3
"""Observe the running XOR self-test through read-only JTAG SAMPLE scans.

Requires an existing XVC server, matching AMD BSDL and a successful SRAM-load
receipt. SAMPLE observes configured outputs; EXTEST and configuration writes
are never selected. No programming, reset pulse or flash write is performed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import struct
import time

from hardware import checked_bitstream


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Xvc:
    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.sendall(b'getinfo:')
        info = bytearray()
        while not info.endswith(b'\n'):
            info.extend(self.recv(1))
            if len(info) > 256:
                raise ValueError('Invalid XVC response')
        self.info = info.decode().strip()
        name, size = self.info.split(':')
        require(name.startswith('xvcServer_'), 'Invalid XVC server name')
        self.max_bits = int(size)
        require(self.max_bits > 0, 'Invalid XVC scan capacity')

    def recv(self, size):
        result = bytearray()
        while len(result) < size:
            chunk = self.sock.recv(size-len(result))
            if not chunk:
                raise EOFError('XVC disconnected')
            result.extend(chunk)
        return bytes(result)

    def shift(self, count, tms, tdi=0):
        require(0 < count <= self.max_bits, 'Scan exceeds XVC scan capacity')
        size = (count+7)//8
        self.sock.sendall(b'shift:' + struct.pack('<I', count) +
                          tms.to_bytes(size, 'little') + tdi.to_bytes(size, 'little'))
        return int.from_bytes(self.recv(size), 'little')

    def scan(self, data, count, ir=False):
        prefix, clocks = (0b0011, 4) if ir else (0b001, 3)
        tms = prefix | (1 << (clocks+count-1)) | (1 << (clocks+count))
        value = self.shift(clocks+count+2, tms, data << clocks)
        return value >> clocks & ((1 << count)-1)

    def reset_tap(self):
        self.shift(6, 0b011111)

    def temperature(self):
        self.scan(0x37, 6, ir=True)  # Dedicated XADC DRP, read only.
        self.scan(1 << 26, 32)
        self.shift(16, 0)
        raw = self.scan(0, 32) & 0xffff
        self.scan(1, 6, ir=True)
        return dict(raw=raw, celsius=None if raw in (0, 0xffff) else
                    round((raw >> 4)*503.975/4096-273.15, 2))

    def close(self):
        try:
            self.scan(0x3f, 6, ir=True)
        finally:
            self.sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--load-receipt', type=Path, required=True)
    parser.add_argument('--bsdl', type=Path, required=True)
    parser.add_argument('--host', default='localhost')
    parser.add_argument('--port', type=int, default=3721)
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not 3 <= args.seconds <= 60:
        parser.error('Observation duration must be between 3 and 60 seconds')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = dict(status='CHECKING_OBSERVATION_INPUTS', hardware_accessed=False,
                  application_verified=False, programming_performed=False,
                  flash_written=False, samples=[])
    link = None

    def save():
        report['utc'] = datetime.now(timezone.utc).isoformat()
        temp = out / 'report.json.tmp'
        temp.write_text(json.dumps(report, indent=2)+'\n')
        temp.replace(out / 'report.json')

    try:
        save()
        bit, build, checksum, part = checked_bitstream(args.run.resolve())
        receipt = json.loads(args.load_receipt.read_text())
        require(receipt['status'] == 'PASS' and receipt['hardware_programmed'] is True,
                'SRAM load receipt did not pass')
        require(receipt['flash_written'] is False, 'Flash write recorded in load receipt')
        require(receipt['bitstream_sha256'] == checksum, 'Load receipt bitstream differs')
        text = args.bsdl.read_text()
        require('entity XC7K480T_FFG1156 is' in text, 'Unexpected BSDL device')
        boundary = re.search(r'attribute BOUNDARY_LENGTH.*?is\s+(\d+);', text)
        require(boundary is not None, 'Missing BSDL boundary length')
        length = int(boundary.group(1))
        require(length == 1395, 'Unexpected BSDL boundary length')
        require(re.search(r'"SAMPLE\s*\(000001\)', text) is not None,
                'Unexpected BSDL SAMPLE instruction')
        pins = {}
        for signal, pin in [('heartbeat', 'P30'), ('pass', 'M30'), ('failure', 'N30')]:
            matches = re.findall(r'"\s*(\d+)\s*\(BC_2,\s*IO_' + pin +
                                 r',\s*output3,\s*X,\s*(\d+),\s*1,\s*Z\)', text)
            require(len(matches) == 1, f'Missing or ambiguous BSDL pin: {pin}')
            output, enable = map(int, matches[0])
            require(0 <= output < length and 0 <= enable < length,
                    f'BSDL boundary cell out of range: {pin}')
            pins[signal] = dict(pin=pin, output_cell=output, disable_cell=enable)
        report.update(bitstream=str(bit), bitstream_sha256=checksum, part=part,
                      core_clock_mhz=build['core_clock_mhz'],
                      load_receipt_sha256=digest(args.load_receipt),
                      bsdl=str(args.bsdl.resolve()), bsdl_sha256=digest(args.bsdl),
                      boundary_length=length, pins=pins, observer_sha256=digest(__file__),
                      scope='Configured output values through SAMPLE, not full internal-score readback')
        save()
        link = Xvc(args.host, args.port)
        report['hardware_accessed'] = True
        report['xvc_info'] = link.info
        require(link.max_bits >= length+5, 'Insufficient XVC scan capacity')
        link.reset_tap()
        link.scan(0x09, 6, ir=True)
        ident = link.scan(0, 32)
        report['idcode'] = hex(ident)
        require(ident == 0x23751093, 'Unexpected JTAG IDCODE')
        report['temperature_before'] = link.temperature()
        capture_ir = link.scan(1, 6, ir=True)
        report['capture_ir'] = capture_ir
        require(capture_ir & 0x30 == 0x30, 'INIT/DONE not asserted')
        start = time.monotonic()
        report['status'] = 'OBSERVING_CONFIGURED_OUTPUTS'
        while time.monotonic()-start < args.seconds:
            value = link.scan(0, length)
            row = dict(elapsed_seconds=time.monotonic()-start,
                       boundary_hex=f'{value:0{(length+3)//4}x}',
                       outputs={signal: (value >> item['output_cell']) & 1
                                for signal, item in pins.items()},
                       disabled={signal: (value >> item['disable_cell']) & 1
                                 for signal, item in pins.items()})
            report['samples'].append(row)
            save()
            time.sleep(.05)
        report['temperature_after'] = link.temperature()
        low = int(build['led_active_low'])
        require(low in (0, 1), 'Invalid LED polarity')
        require(all(row['outputs']['pass'] == (1 ^ low) and
                    row['outputs']['failure'] == low for row in report['samples']),
                'Built-in PASS/failure output state differs; preserve samples')
        require(all(not any(row['disabled'].values()) for row in report['samples']),
                'One or more expected output drivers are disabled')
        heartbeat = [row['outputs']['heartbeat'] for row in report['samples']]
        transitions = sum(a != b for a, b in zip(heartbeat, heartbeat[1:]))
        report['heartbeat_transitions'] = transitions
        require(len(report['samples']) >= 10, 'Too few captures')
        require(transitions >= 2, 'Heartbeat did not keep toggling')
        report.update(status='PASS_PHYSICAL_TINY_TPU_XOR_SELFTEST_OUTPUTS',
                      application_verified=True,
                      limitations=['PASS is the built-in exact XOR scoreboard, observed through its output.',
                                   'No direct internal score/count readback; no DDR or PCIe test.',
                                   'Configured clock comes from the bound bitstream; not an independent frequency measurement.'])
        print(report['status'])
    except BaseException as error:
        report.update(status='FAIL', reason=repr(error))
        raise
    finally:
        try:
            if link is not None:
                link.close()
        finally:
            save()


if __name__ == '__main__':
    main()
