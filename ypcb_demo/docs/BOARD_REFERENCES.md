# Board reference cross-check, 2026-10-05

The local board XML, XDCs and exact-part package database agree on
`xc7k480tffg1156-2`, AA28 for the 50 MHz clock, R28 for reset, and P30/M30/N30
for red/green/yellow LEDs. The vendor spreadsheet
`~/ypcb/ref/ypcb_00338_1p1_hack/constraints/physical_pinout.xlsx`
corroborates all five pin assignments in rows 66–68, 74 and 77. The separate
`~/ypcb/ref/LC480T/YPCB-00338-1P1/时钟和LED.xlsx` corroborates the
clock and LED assignments. Neither establishes electrical supplies or polarity.
The reference file hashes are retained in `../fpga/board_reference.json`.

The board pin file `ypcb003381p1/1.0/part0_pins.xml` identifies R28 as
`SW_RESET`. The board XML's `resetn` interface maps to that component pin.
Its separate `pcie_perstn` interface maps to `pcie_perstn_rst` at Y26.
The port adds a weak pull-up on R28 so an undriven active-low reset does not
prevent standalone startup. This is an implementation choice, rather than
evidence that the physical reset wiring or driver was measured. AMD documents
the property in [PULLTYPE](https://docs.amd.com/r/2025.2-English/ug912-vivado-properties/PULLTYPE).

## Conflicting clock I/O constraints

| Source | AA28 clock I/O standard |
| --- | --- |
| `ypcb_00338_1p1_hack/constraints/ypcb003381p1.xdc` | LVCMOS18 |
| `inspur-adventures/constraints/blink.xdc` | LVCMOS18 |
| `LC480T/YPCB-00338-1P1/XDMA_480T_n_clear.7z`, member `XDMA_480T_n/XDMA_480T_n.srcs/constrs_1/new/IO.xdc` | LVCMOS33 |

The archived XDMA constraint agrees on AA28 and the three LED pins; it uses
LVCMOS18 for the LEDs. It supplies no CONFIG_VOLTAGE or CFGBVS value. Its clock
constraint is evidence of a reference disagreement, not proof of the actual
card's oscillator output or bank 14 supply. No physical voltage measurement
was available during preparation.

The port's default XDC follows the first two references. A full Vivado build
can override the clock with `--clock-iostandard LVCMOS18` or `LVCMOS33`.
Bitstream emission requires this explicit selection after checking the card's
oscillator output and bank 14 supply. The routed report records the actual
standard applied. Configuration-bank voltage/CFGBVS and LED polarity remain
separate physical facts to verify.

## Archive inspection scope

Six local ZIP archives were listed and their XDC/Tcl/XPR/XML/text documents
up to 512 KiB were searched for CONFIG_VOLTAGE and CFGBVS. That covered 250
documents; 12 larger documents were skipped in that first pass. No match was found in the scanned
documents. The audit is retained at
`../artifacts/board-archive-audit-20261005T144047265121Z.json`.

The `XDMA_480T_n_clear.7z` member list and its IO.xdc were also read directly.
The DOCX beside the board-file ZIP contains only the text `a`.
The follow-up read all 12 larger XML documents and found no CONFIG_VOLTAGE
or CFGBVS setting. Its archive and member hashes are retained in
`../artifacts/board-archive-followup-20261005T180607598190Z.json`.

The installed 7-Zip could not open `LC_7K480T.rar`, but the system libarchive
reader successfully listed it and read its three constraint files and
screenshot. The screenshot independently agrees on the 50 MHz AA28 clock
and all three LED pins/colors. `PCIE.xdc` places PCIe reset at Y26 with
LVCMOS33; it does not describe the AA28 clock or configuration straps. The
two memory UCFs contain DDR pin assignments without I/O standards or
configuration voltage/strap settings. These references do not resolve the
physical clock supply or configuration bank values. Extracted documents,
hashes and the visual inspection are retained in
`../artifacts/board-rar-followup-20261005T180742817585Z/`.

No vendor executables,
firmware, prebuilt accelerator images or host drivers were run or copied
into this source bundle.
