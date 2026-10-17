# Home-project reuse audit

`python3 ypcb_demo/tools/audit_home.py` inventories every non-hidden top-level
directory in `/home/fedora`, including every Git checkout, and recursively
reads source/build documents. Generated build outputs, caches, dependency
directories and symlink targets are excluded. The JSON report states these
boundaries and retains each checkout revision, inspected file count and
matched source paths/lines. Direct board and toolchain candidates receive a
deeper inspection beyond the automated document scan.

## Directly useful work inspected

| Project | Useful material | Reuse in this port |
| --- | --- | --- |
| tiny-tpu | XOR TPU RTL, board sequencer, checked-in golden regression | Core and command sequence reused directly; golden regression run natively |
| ypcb | Exact board constraints/part, DLC10 Fedora initializer, firmware hashes and v1.1.1 loader | Board XDC and cable workflow reused; fresh USB state remains separate |
| rknpu-fpga | Same-board XDC, reset/LED patterns, Vivado timing/DRC gates, XC7 database and diagnostics | Implementation checks and clock/reset integration informed this port |
| soDLA | `fpga/ypcb/` AXI BRAM/self-test wrapper and full-Vivado flow | Confirms the shared tool and board-electrical prerequisites; its NVDLA datapath is not substituted for TPU |
| somnia | Same-part generated RTL build, power-on reset and LED demo | Useful independent startup/build patterns; matrix scheduling differs from tiny-tpu |
| tiny-gpu | Same-board inputs, matching openXC7 base source/database builds, exact-part frame-tool check | Completed engine objects and chip database reused read-only; local DSP/index fixes passed the full database test, TPU routing and independent full-payload comparison |
| LPULite | `synthesis/ypcb/` build guards, board-reference checks and implementation workflow | Cross-check preparation and source provenance; its electrical notes retain the same unmeasured board values |
| ypcb_ref | openTPU DDR/PCIe constraints, bonetto-soc DDR/JTAG workflow, duplicated vendor board references and blink constraints | Pin and future transport references; existing configuration-voltage/CFGBVS defaults do not establish the physical card's supplies or straps |
| verigpu | `fpga/ypcb/host.tcl` VIO transaction/readback host and command-toggle bridge | Candidate for future host-visible TPU scores; needs a TPU-specific protocol and debug-IP validation |
| vortex | `hw/syn/xilinx/ypcb/` and `docs/proposals/ypcb_port_proposal.md` | Custom PCIe/DDR platform candidate; existing Alveo builds do not produce a YPCB tiny-tpu image |
| ypcb/ref/inspur-adventures | Independent clock/LED pin constraints, openXC7 scripts | Cross-check board pins; macOS-specific setup is not used on Fedora |
| ypcb/ref/ypcb_00338_1p1_hack | Board XML and system-test Vivado projects | Cross-check target part and reset/clock/LED pin names |
| ypcb/ref/LC480T | PCIe/XDMA/MEMTest reference material and bitstreams | Future PCIe/DDR shell candidates; existing example bits do not contain tiny-tpu |
| vivado | Full Vivado 2026.1, Lab tools, installer archive and registry | Full implementation now passed for this TPU; the earlier Lab-only audit remains historical |

## Candidates for later extensions

The main tiny-tpu repository's `mnist_demo` supplies model assets, inference
controllers and tests. Its DE1-SoC wrappers contain Intel Avalon/JTAG bridge
IP; they cannot be loaded as Kintex-7 bits. A YPCB MNIST extension should keep
the model and arithmetic contracts, replace the Intel host bridge with a
Xilinx-compatible transport, and verify full image/result transactions.

The YPCB reference XDMA/system-test designs and `rknpu-fpga` host register/DMA
logic are candidates for transport integration. They require protocol and
clock/reset adaptation; this XOR controller does not claim PCIe or DDR use.
The completed openXC7 implementation, retained earlier failures and remaining
timing-model gaps are documented in [OPENXC7.md](OPENXC7.md) and
[HOST_TOOLS.md](HOST_TOOLS.md).

The VeriGPU VIO host uses four input/output probes, separates payload commit
from a command toggle, bounds acknowledgement waits, checks interface version
and output count, and writes verified result files. Those are useful patterns
for a future tiny-tpu host interface. Its current command/address/data and GPU
instruction layout cannot be copied as the TPU instruction interface. This
port can execute and report its XOR self-test without debug IP.

Projects such as `nvdla`, `tinynvdla`, `tinygrad_nvdla`, `npu`, `etsoc`,
`et-platform`, `tt`, `tt-metal`, `tinytenstorrent`, `tiny-gpu`, `verigpu` and
`vortex` may supply test, simulation or FPGA workflow examples, but their
accelerator instruction interfaces and numeric formats are not tiny-tpu's
two-layer Q8.8 interface. GPU/SoC emulators in `mgpusim`, `navisim`,
`ClusterSim`, `multi2sim`, `qbox`, `et-sysemu`, and model/compiler projects
do not supply a YPCB tiny-tpu bitstream. No unrelated checkout was changed.

The refreshed source-match evidence is in
`artifacts/home-audit-20261006T112304966889Z/report.json`.
The inventory below records that completed snapshot. The earlier
`artifacts/home-audit-20261005T134132882487Z/report.json` retains its historical
44-directory, 24-checkout, 8491-document snapshot. Zero matches means no keyword
match within the stated document scan, not proof that all RTL is irrelevant.

The refreshed scan also found `LPULite`, `ypcb_ref` and
`cpu-fan-diagnostics`. The latter contains host diagnostic scripts and logs;
it had no documents selected by this FPGA/build-document scan. Direct reads
of LPULite's board-reference notes and openTPU's constraints/flash notes
confirmed that their configuration values are source settings or inferences,
without a measurement of this card's AA28 supply, configuration-bank voltage
or CFGBVS strap. openTPU's offline bit-to-MCS comparison checks byte ordering,
not those electrical values. Their defaults therefore do not resolve the
remaining loading prerequisites.

The later direct tool inspection also verified
`/home/fedora/tiny-gpu/build/ypcb/frame-smoke-1791213825695214370/report.json`
and its FASM/frame/bitstream artifact hashes. Its empty-design frame tools
passed for `xc7k480tffg1156-2`; this checks a tool component, not GPU or TPU
placement/routing or physical operation. The same project's matching
nextpnr compiler and die-wide chip database builds completed and were reused
read-only. This port's local PR66 DSP fix and name-index variant passed the
unchanged full architecture test. The actual TPU then passed placement/routing,
DSP/reset FASM checks and a separate full configuration-payload comparison.
The exact reports, hashes and remaining physical/timing limitations are in
[OPENXC7.md](OPENXC7.md).

## Complete directory inventory

The scan inspected 47 directories, 26 top-level Git checkouts and
12567 source/build documents, with 0 document-scan errors.

| Home directory | Git checkout | Documents inspected | Matching files |
| --- | --- | --- | --- |
| ClusterSim | yes | 18 | 0 |
| Desktop | no | 0 | 0 |
| Documents | no | 0 | 0 |
| Downloads | no | 0 | 0 |
| LPULite | yes | 40 | 18 |
| Pictures | no | 0 | 0 |
| Public | no | 0 | 0 |
| Templates | no | 0 | 0 |
| Videos | no | 0 | 0 |
| cpu-fan-diagnostics | no | 0 | 0 |
| et-platform | yes | 270 | 0 |
| et-sysemu | no | 267 | 2 |
| etsoc | yes | 168 | 66 |
| etsoc1-native-matmul-mat-engine-perf-visulization | yes | 1 | 0 |
| go | no | 3988 | 0 |
| html | no | 0 | 0 |
| llama.cpp-tt | yes | 140 | 0 |
| mgpusim | yes | 88 | 0 |
| miaow_gcn4 | yes | 67 | 0 |
| models | no | 0 | 0 |
| multi2sim | yes | 52 | 0 |
| multi2sim-iiswc2016 | yes | 54 | 0 |
| navisim | yes | 58 | 0 |
| navisim-cleanup-backup-20261003T174015Z | no | 0 | 0 |
| npu | yes | 10 | 0 |
| nvdla | yes | 486 | 1 |
| nvgpu | no | 0 | 0 |
| onnc | yes | 62 | 0 |
| qbox | yes | 180 | 0 |
| rknpu-fpga | yes | 40 | 17 |
| soDLA | yes | 42 | 7 |
| somnia | yes | 18 | 5 |
| tiny-gpu | yes | 13 | 8 |
| tiny-tpu | yes | 59 | 34 |
| tinygrad | yes | 63 | 1 |
| tinygrad_nvdla | yes | 64 | 1 |
| tinynvdla | no | 51 | 4 |
| tinytenstorrent | no | 13 | 0 |
| tt | yes | 11 | 0 |
| tt-metal | yes | 344 | 0 |
| verigpu | yes | 51 | 9 |
| vivado | no | 1047 | 43 |
| vortex | yes | 4587 | 97 |
| vortex-tools-v3.0.1 | no | 17 | 0 |
| vpn | no | 0 | 0 |
| ypcb | no | 93 | 50 |
| ypcb_ref | no | 105 | 74 |
