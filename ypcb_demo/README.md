# tiny-tpu on YPCB-00338-1P1

This port runs the existing two-layer XOR network through the real tiny-tpu
2×2 systolic array, unified buffer, bias and leaky-ReLU datapath. It starts
after configuration and repeatedly tests all four inputs. It needs the board
clock, reset and three LEDs; the test data and trained parameters are loaded
by a hardware controller. No host inference arithmetic is substituted.
The TPU reset is registered rather than decoded directly from changing state
bits, avoiding asynchronous reset glitches during controller transitions.

The build uses the FPGA RTL already in `../xor_demo/rtl/`. The controller is
derived from `../xor_demo/fpga/de1_soc_tiny_tpu_xor_top.v` and preserves its
command sequence and address map. It removes the DE1-SoC buttons, switches and
seven-segment display. Existing `src/` changes are outside this port.

## Board connections

| Signal | Package pin | I/O standard | Purpose |
| --- | --- | --- | --- |
| SYS_CLK | AA28 | LVCMOS18 | 50 MHz oscillator |
| SYS_RSTN | R28 | LVCMOS18 | Active-low external reset |
| led[0] | P30 | LVCMOS18 | Red LED: clock heartbeat |
| led[1] | M30 | LVCMOS18 | Green LED: all scores passed |
| led[2] | N30 | LVCMOS18 | Yellow LED: sticky failure |

Part: **xc7k480tffg1156-2**. Pin sources in the reference YPCB workspace are
`ypcb/ref/ypcb_00338_1p1_hack/constraints/ypcb003381p1.xdc`,
its `ypcb003381p1/1.0/board.xml`, and the independent blink constraints in
`ypcb/ref/inspur-adventures/constraints/blink.xdc`.
These are reference facts; actual clock, reset, I/O supplies and LED polarity
still need confirmation on the powered card. `--led-active-low 1` in the build
inverts all LEDs if required. A power-on counter and two-stage reset release
also initialize the design when no external reset pulse occurs.
R28 is the board's `SW_RESET`, separate from PCIe PERST# on Y26. The XDC
enables a weak pull-up so reset defaults high when undriven; an external low
still asserts reset. Both implementation paths check that the pull-up is
retained in the routed output.
`fpga/board_reference.json` also cross-checks all five pins against the exact
part's package CSV and retains hashes of each reference. It records unknown
physical configuration-bank voltage, CFGBVS and LED polarity from the initial
reference audit. The later 150 MHz test below observed the configured outputs
with `LED_ACTIVE_LOW=0`; it did not measure the board supply voltages.
The local vendor spreadsheets corroborate the clock, reset and LED pins.
The vendor `XDMA_480T_n_clear.7z` has a conflicting AA28 clock constraint of
LVCMOS33, whereas the board and blink XDCs above use LVCMOS18. The table shows
the default build constraints, not measured supplies. Full Vivado bitstream
emission and experimental openXC7 SRAM loading require an explicit
`--clock-iostandard LVCMOS18` or `LVCMOS33` confirmed for this card. OpenXC7
may prepare a candidate using the default before that confirmation; the
manifest retains the unresolved electrical facts.
See [BOARD_REFERENCES.md](docs/BOARD_REFERENCES.md) for the exact archive member
and the document inspection scope.

The controller verifies the eight first-layer valids and four final scores,
and times out an invocation after 1024 clocks. Golden scores come from the
checked-in `tiny-tpu-hardened/sim/tpu_xor_forward_regression.v` and are checked
again by running that original bench against the FPGA RTL.

| XOR input in stream order | Raw signed Q8.8 score | Classification |
| --- | --- | --- |
| 00 | 0xffff (-1/256) | 0 |
| 01 | 0x00fe (254/256) | 1 |
| 10 | 0x00fe (254/256) | 1 |
| 11 | 0xffff (-1/256) | 0 |

Scores are compared bit for bit, including rejection of unknown bits in
four-state simulation. Classification alone cannot establish PASS.
Failure clears the green LED and latches the yellow LED until reset.

## Verify and prepare

From this directory:

```sh
make test       # offline build/evidence/loader/observer tests, Icarus board/fault and XOR tests
make synth      # also Yosys XC7 mapping and mapped functional simulation
make audit      # inventory every non-hidden ~/ directory and useful documents
make prepare    # portable source bundle and manifest
```

Verification and build commands write fresh directories under `artifacts/`;
the Python unit tests use temporary directories. Logs retain tool
versions, exact arguments and input hashes. Mapping checks the real DSP
datapath and top-level ports. Its functional simulation observes only the
package-level LEDs. Simulation and Yosys resource mapping do not establish
placement, routing, setup/hold timing, electrical DRC or physical operation.
After a verifier-only correction, `python3 tools/verify.py --mapped-run
artifacts/verify-ACTUAL_RUN_DIRECTORY` can reuse a completed successful mapping.
It requires identical RTL, source list and benches, validates the original
synthesis script and check result, retains artifact hashes and provenance,
and runs fresh RTL and mapped simulations. The original run is preserved.
The checked synthesis and Icarus commands do not read XDC, so implementation
constraint changes are recorded separately and applied in the new route.

`make prepare` creates `source-bundle.tar.gz`. Extract it into a new directory
on the build machine, run `make -C ypcb_demo test`, then choose the openXC7
or full Vivado implementation commands below. The bundle contains the RTL,
constraints, scripts, benches and source manifest. The openXC7 route needs
a mapped verification run and the pinned external tool/database checkouts;
these generated tools and mapping artifacts are not in the source bundle.
To verify a generated bundle after clean extraction, including retaining the
new simulation logs, run:

```sh
python3 tools/check_bundle.py artifacts/build-ACTUAL_RUN_DIRECTORY
```

## Implement with openXC7

The open-source route is prepared for the exact part using the current
Himbaechel Xilinx backend and matching Project X-Ray database. See
[OPENXC7.md](docs/OPENXC7.md) for the executable command, pinned sources,
shared local tool builds, database checks and experimental timing scope.
The runner writes routed JSON, detailed timing and FASM after successful
checks. Full Vivado is optional for this route.

The retained experimental candidate passed the full database test,
placement/routing, DSP/reset configuration checks and independent comparison
of all 18,735,004 configuration bytes. The exact artifact and reports are in
[OPENXC7.md](docs/OPENXC7.md). The loader passed an offline preflight with the
user-supplied openTPU reference settings: AA28 LVCMOS18, configuration voltage
1.8 V and CFGBVS GND. Their source provenance and the prepared loading command
are recorded in OPENXC7.md. Physical execution and full timing signoff of
that experimental openXC7 candidate remain unverified. The physical test
below used the separately implemented Vivado 150 MHz image.

## Implement with full Vivado

The vendor build completed with full **Vivado 2026.1** on
2026-10-16 (Hong Kong time), using `~/openTPU` as the board reference.
Put the full Vivado executable on `PATH` to reproduce it.
The earlier Lab-only search is retained as history in
[HOST_TOOLS.md](docs/HOST_TOOLS.md).

Reproduce the bitstream from this directory:

```sh
python3 tools/build.py --vivado "$(command -v vivado)" \
  --emit-bitstream --core-mhz 150 \
  --clock-iostandard LVCMOS18 --config-voltage 1.8 --cfgbvs GND
```

The completed **150 MHz** run is `artifacts/build-20261016T102846491634Z/`:
`tiny_tpu_ypcb.bit` is 2,655,908 bytes, with SHA256
`b377cee1dc3082ab6de7cced229a1538bfa987335cfa5dc8cbd63a9268ecae42`.
Its report is `BITSTREAM_PASS`. Setup slack is +0.544 ns and hold slack is
+0.108 ns. Routing is complete with zero routing errors, missing
internal clocks, unconstrained internal endpoints, combinational loops or
blocking DRC violations. The retained DRC report contains 30 DSP pipelining
recommendations. Final utilization is 9,781 LUTs, 2,868 registers, 9 DSPs and
one MMCM.

AA28 still receives the board's physical 50 MHz oscillator. The 150 MHz
configuration uses an MMCM (900 MHz VCO, divide by 6) and a global clock
buffer to clock the TPU and controller. Reset stays asserted until the MMCM
locks and its release is synchronized to the core clock. `clocks.rpt`
confirms the 20 ns input and auto-derived 6.667 ns core clock with a multiply-by-3
relationship. The build verifies the actual core-clock period before emitting
a bitstream. `--core-mhz 100` selects a 1000 MHz VCO divided by 10;
`--core-mhz 50` (the default) uses the input clock directly. The 100 and
150 MHz builds use retiming and timing-driven physical optimization.

The part, AA28/50 MHz/LVCMOS18 clock, LED pins, 1.8 V configuration and GND
CFGBVS match `~/openTPU/boards/ypcb-00338/constraints/otpu_top_ld.xdc` and its
Vivado project. This flow also uses its compressed BPI16 configuration,
asynchronous BPI setting and `UNUSEDPIN PULLNONE`. The exact reference source
hashes and revision are in
`artifacts/vivado-reference-20261015T163335327441Z.json`.
These are the selected documented board settings; no physical measurement
or board programming is claimed by bitstream preparation.

Check the newly built routed functional netlist with Vivado's XSim. Set
`BUILD_RUN` to the directory containing the `report.json` printed by the
build command above:

```sh
BUILD_RUN=artifacts/build-ACTUAL_NEW_RUN_DIRECTORY
python3 tools/verify_vivado.py --run "$BUILD_RUN" \
  --vivado "$(command -v vivado)"
```

This uses the existing package-pin bench to check startup, repeated TPU XOR
PASS and reset recovery. It exports the actual routed checkpoint and uses
the vendor primitive models. Static timing is checked separately above.
The check passed in
`artifacts/verify-vivado-20261016T103716670066Z/report.json`, with
`YPCB MAPPED REGRESSION PASS` from XSim.

All three variants remain available. Each directory under `artifacts/` holds
`tiny_tpu_ypcb.bit` and its source snapshot and reports:

| Core clock | Build directory | Setup slack | Hold slack |
| --- | --- | --- | --- |
| 150 MHz | `build-20261016T102846491634Z` | +0.544 ns | +0.108 ns |
| 100 MHz | `build-20261016T100507250470Z` | +1.554 ns | +0.112 ns |
| 50 MHz | `build-20261015T163138618115Z` | +7.613 ns | +0.104 ns |

The archived routed simulation results remain historical evidence for these
images. Their original build reports predate the `routed_checkpoint_sha256`
binding now required by `verify_vivado.py`. To reproduce routed simulation
with the current verifier, make a fresh build and use its new directory as
`BUILD_RUN`; the verifier checks both the implementation source hashes and
the recorded checkpoint hash. A variant's archived source bundle preserves
its historical inputs if those are needed for the rebuild. Select the
matching `--core-mhz` when rebuilding.

Allowed voltage/strap pairs are 1.5 or 1.8 with GND, and 2.5 or 3.3 with VCCO.
The script requires complete placement/routing with no route errors, finite
nonnegative setup and hold slack, zero missing internal clocks/endpoints/loops,
and no blocking DRC violations before it writes `tiny_tpu_ypcb.bit`. It runs
the default, routing and timing DRC decks, adds the bitstream deck when
emitting a bitstream, and ignores waivers. It never demotes DRC rules or
programs hardware. The routed checkpoints and complete reports are retained
alongside the generated bitstream.

## Cable and SRAM loading

An earlier managed environment exposed no DLC10 or `/dev/bus/usb` nodes.
The October 17 physical test subsequently detected and used the cable.
Before any future load, run a fresh preflight from a terminal with USB
access and the powered card/cable, from this directory:

```sh
python3 tools/hardware.py check
python3 tools/hardware.py init       # existing staged cable-RAM firmware
python3 tools/hardware.py detect     # require single XC7K480T IDCODE 0x23751093
python3 tools/hardware.py program-sram --run artifacts/build-ACTUAL_RUN_DIRECTORY
```

Put a DLC10-capable `openFPGALoader` on `PATH`, or set `YPCB_OPENFPGALOADER`
or pass `--loader /path/to/openFPGALoader` on each command. `init` calls
`~/ypcb/dlc10-init.sh` by default; set `YPCB_INIT_SCRIPT` or pass
`--init-script /path/to/dlc10-init.sh` when the YPCB workspace is elsewhere.
Its firmware hashes and boot/helper/overlay stages are established in that
separate project; the initializer and firmware are not included here.
It uploads volatile cable firmware and performs detection. The script records
fresh logs; previous cable tests are not a present-board detection result.
The three local firmware files were rehashed and match the initializer's
expected values. Existing `/etc/udev/rules.d/52-xilinx-pcusb.rules` includes
both cold `03fd:0013` and warm `03fd:0008`, so no permission rules were changed.

`program-sram` requires a bitstream matching a passing build report and a
freshly detected single-device chain. An experimental openXC7 candidate
additionally requires `--experimental-openxc7` and the checks documented in
[OPENXC7.md](docs/OPENXC7.md); it is recorded without full timing/DRC signoff.
SRAM configuration is volatile; power
cycling restores the existing boot image. No flash erase/write option or BPI
bridge is used. Observe the heartbeat, steady PASS and unasserted failure LED
to validate the application after loading. A successful loader exit alone
does not prove that the TPU ran correctly.
The SRAM-loading timeout scales with bitstream length at the requested 500 kHz
JTAG rate, with allowance for transfer/startup overhead. The report records an
attempt before loading starts; a failed or timed-out load leaves configuration
state uncertain rather than claiming that the previous image was preserved.

### Physical 150 MHz quick test — October 17, 2026

The existing `build-20261016T102846491634Z/tiny_tpu_ypcb.bit` was loaded into
SRAM on the connected XC7K480T, IDCODE `0x23751093`. Its bitstream,
routed simulation, signoff and critical RTL hashes were checked before load.
The test performed no flash erase or write.

A five-second JTAG SAMPLE observation used the matching AMD BSDL supplied
with Vivado. All 95 samples showed the configured green PASS output high,
sticky failure output low and output drivers enabled. The heartbeat toggled
22 times. Dedicated XADC reads showed 38.39 to 39.25 degrees C die temperature.
This observes the built-in exact XOR scoreboard's PASS result; it does not
read back internal score/count registers or qualify DDR/PCIe operation. The
150 MHz configuration is established by the bound build, not an independent
clock-frequency measurement.

The [physical test report](../HARDWARE_TEST_20261017.md) links to the
[versioned receipts and all raw captures](docs/hardware/20261017/README.md).
The loader exited successfully and the observer released JTAG. At the end
of the recorded observation the FPGA was left running the repeated XOR
self-test in SRAM. The commit review found no active host observer or
programmer; it did not query the FPGA's current state.

Recheck the archived evidence without accessing hardware:

```sh
python3 tools/verify_hardware_result.py
```

Historical receipt validation is independent of later RTL changes. To also
check that current implementation files still match the recorded image, run
`python3 tools/verify_hardware_result.py --check-current-sources`.
The offline observer regression runs simulated JTAG inputs through the real
entry point under normal Python, `-O` and `-OO`, checking that failures are
rejected in every mode. The saved observer snapshot preserves its original
hash and contents.

For another observation of this same loaded image, with an already running
XVC server and `AMD_BSDL` set to the matching `xc7k480t_ffg1156.bsd`:

```sh
python3 -B tools/observe_selftest.py \
  --run artifacts/build-20261016T102846491634Z \
  --load-receipt artifacts/hardware-20261017T101141792179Z/report.json \
  --bsdl "$AMD_BSDL" --seconds 5 \
  --out artifacts/hardware-observation-new
```

The observer uses SAMPLE, IDCODE, BYPASS and read-only XADC access. It does
not select EXTEST, program configuration or pulse the design's reset.

## Remaining work

The Vivado 50/100/150 MHz implementations and routed simulations are complete;
the 150 MHz image also passed the physical quick test above. No additional
FPGA test is required to commit this scoped XOR bring-up result. Longer
soak tests and physical reset recovery would extend the hardware coverage;
50/100 MHz physical tests would be needed to claim those variants ran on
the board. The saved quick test does not measure electrical supplies or
independently measure the clock frequency. The experimental openXC7 image
has separate validation limits described in OPENXC7.md.

This is the tiny-tpu XOR board bring-up path. The existing MNIST demo has
additional Intel Avalon/JTAG IP and DE1-SoC host wiring; it requires a Xilinx
transport port before it can run on YPCB. PCIe/DDR integration, training and
MNIST operation are not claimed by this XOR self-test. The home-project audit
in [HOME_PROJECTS.md](docs/HOME_PROJECTS.md) records candidates for later
transport work and why their accelerator datapaths are not interchangeable.
