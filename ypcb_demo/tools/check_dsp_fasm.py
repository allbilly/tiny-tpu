#!/usr/bin/env python3
"""Require the TPU's mapped DSP controls in actual emitted FASM."""
import argparse
import hashlib
import json
from pathlib import Path
import re

TIED_PINS = ['INMODE' + str(i) for i in range(5)] + ['ALUMODE2', 'ALUMODE3', 'OPMODE6']
ZERO_REGISTERS = ('AREG', 'BREG', 'ACASCREG', 'BCASCREG', 'ADREG', 'ALUMODEREG',
                  'CARRYINREG', 'CARRYINSELREG', 'CREG', 'DREG', 'INMODEREG', 'MREG', 'OPMODEREG', 'PREG')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_configuration(design_path, fasm_path):
    """Check the current eight combinational multipliers; reject other mappings."""
    design = json.loads(design_path.read_text())['modules']['ypcb_tiny_tpu_top']
    dsps = {name: cell for name, cell in design['cells'].items() if cell['type'] == 'DSP48E1'}
    if len(dsps) != 8:
        raise ValueError('Expected exactly eight mapped DSP48E1 multipliers')
    for name, cell in dsps.items():
        params = cell['parameters']
        for register in ZERO_REGISTERS:
            if register not in params or int(str(params[register]), 2) != 0:
                raise ValueError(f'{name}: unsupported nonzero/missing {register}')
        if any(int(str(value), 2) for key, value in params.items() if key.startswith('IS_') and key.endswith('_INVERTED')):
            raise ValueError(f'{name}: unexpected DSP inversion parameter')
        for key, expected in (('A_INPUT', 'DIRECT'), ('B_INPUT', 'DIRECT'), ('USE_DPORT', 'FALSE'),
                              ('USE_MULT', 'MULTIPLY'), ('USE_SIMD', 'ONE48')):
            if params.get(key) != expected:
                raise ValueError(f'{name}: unsupported DSP mode {key}')
        for bus, values in (('INMODE', ['0'] * 5), ('ALUMODE', ['0'] * 4),
                            ('OPMODE', ['1', '0', '1', '0', '0', '0', '0'])):
            if cell['connections'].get(bus) != values:
                raise ValueError(f'{name}: changed multiplier control bus {bus}')
    features = {line.strip() for line in fasm_path.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith('#')}
    sites = set()
    for feature in features:
        match = re.match(r'(DSP_[LR]_X\d+Y\d+)\.DSP48\.(DSP_[01])\.', feature)
        if match:
            sites.add(match.groups())
    if len(sites) != len(dsps):
        raise ValueError(f'FASM describes {len(sites)} DSP sites, expected {len(dsps)}')
    checked = []
    for tile, site in sorted(sites):
        side = tile[4]
        prefix = tile + '.DSP48.' + site + '.'
        required = {prefix + 'AREG_0', prefix + 'BREG_0', prefix + 'ZAREG_2_ACASCREG_1',
                    prefix + 'ZBREG_2_BCASCREG_1'}
        forbidden = {prefix + 'AREG_2', prefix + 'BREG_2', prefix + 'USE_DPORT[0]',
                     prefix + 'USE_SIMD_FOUR12_TWO24', prefix + 'USE_SIMD_FOUR12',
                     prefix + 'A_INPUT[0]', prefix + 'B_INPUT[0]'}
        required.update(prefix + 'Z' + register + '[0]' for register in ZERO_REGISTERS
                        if register not in ('AREG', 'BREG', 'ACASCREG', 'BCASCREG'))
        for pin in TIED_PINS:
            bus, index = re.fullmatch(r'([A-Z]+)(\d+)', pin).groups()
            required.add(prefix + f'ZIS_{bus}_INVERTED[{index}]')
            required.add(tile + '.' + site + '_' + pin + '.DSP_GND_' + side)
            forbidden.add(tile + '.' + site + '_' + pin + '.DSP_VCC_' + side)
        missing = sorted(required - features)
        unexpected = sorted(forbidden & features)
        if missing or unexpected:
            raise ValueError(f'{tile}/{site}: missing {missing}; forbidden {unexpected}')
        checked.append(dict(tile=tile, site=site, required_features=sorted(required),
                            forbidden_features_absent=sorted(forbidden)))
    return dict(status='PASS_DSP_CONTROL_FASM', mapped_dsp_count=len(dsps), checked_sites=checked,
                mapped_design_sha256=sha(design_path), fasm_sha256=sha(fasm_path),
                scope='Eight mapped combinational DSP modes, register bypass, and tile-local constant/inverter bits; not full routing/timing or physical proof')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--design', type=Path, required=True)
    parser.add_argument('--fasm', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(check_configuration(args.design, args.fasm), indent=2))
        return 0
    except Exception as exc:
        print(json.dumps(dict(status='FAIL', reason=str(exc)), indent=2))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
