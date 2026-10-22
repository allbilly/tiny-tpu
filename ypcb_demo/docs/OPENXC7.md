# OpenXC7 implementation

The requested open-source route uses Yosys mapping, the pinned openXC7
Himbaechel Xilinx backend, and Project X-Ray's exact Kintex-7 database.
Full Vivado is optional. The old `rknpu-fpga` nextpnr-xilinx executable is
retained as a reference; its failed database tests are not used as a pass.

The [installer snapshot inspected on October 5, 2026](https://github.com/openXC7/toolchain-installer/blob/0cb31563418a2cfec44d68d7d0836e000d73fba6/toolchain-sources-builder.sh)
pins these sources:

| Component | Revision |
| --- | --- |
| openXC7/nextpnr base | `c68c13582e972292c86a5025140d52e713384cbc` |
| openXC7/prjxray-db | `517d66a383676cb971177ea92b0ff3b6ea6e8690` |
| openXC7/prjxray | `ed3331c6200f421164101388759fc2860b0f5634` |
| hansfbaier/fpga-assembler | `cf0e3f08455d502fc6392889c07f482ab8dd2d62` |

Source checkouts and installer text are isolated in
`artifacts/openxc7-toolchain/`. The installer's automatic package installation
and cleanup functions were not used; install the prerequisites appropriate
to the build host separately. Existing
Yosys 0.65 performed this TPU's mapping; its successful synthesis, version,
script and hashes are retained rather than relabeled as the installer's
newer Yosys version.

The examples below run from the repository root and refer to the preserved
run identifiers from this build. Generated tools, databases and timestamped
run directories are not included in a source checkout. Replace those run
identifiers with the corresponding outputs when rebuilding. Set the external
tool workspace to your own location before using the examples:

```sh
export YPCB_TOOLS=/absolute/path/to/shared/ypcb-tool-build
export TPU_ARTIFACTS="$PWD/ypcb_demo/artifacts"
```

`YPCB_TOOLS` denotes a workspace containing `nextpnr-build/`,
`nextpnr-himbaechel/`, `nextpnr-cli-build/`, `prjxray/`, `prjxray-db/`,
`prjxray-build/` and `xray-venv/`, as used in the recorded build. If your
tools use a different layout, supply their actual paths to the respective
command options. The variable does not create or install those tools.

The installer-pinned engine has a DSP control defect affecting this TPU's
eight multipliers. [Issue 39](https://github.com/openXC7/nextpnr/issues/39)
and the proposed [PR66 fix](https://github.com/openXC7/nextpnr/pull/66)
describe missing tile constants and incorrect inversion/register encoding.
This port uses PR66's exact head,
`a662c08d25d7d2524ab341a387f120521c8a5d08`, instead. The fix was an
open upstream PR when inspected; its reported comparisons with Vivado do not establish
physical operation on YPCB. The local mapping has AREG/BREG/MREG/PREG zero,
INMODE/ALUMODE zero and OPMODE `0000101` on all eight DSPs.

The `tiny-gpu` project's clean base engine and matching 480T database have
finished building. `tools/build_pr66.py` rebuilds the fix's two changed translation
units in this project's artifacts, then reuses unchanged base objects
read-only after that build finishes. Its manifest records every linked
object hash, compile/link command, patch, source revision and output hash.
The CLI version text comes from base objects; the manifest identifies the
actual fixed source. No changes or build restarts are made in `tiny-gpu`.
The generator and metadata are unchanged by PR66, so the die-wide chip
database remains compatible. The database
is die-wide, while `--device xc7k480tffg1156-2` selects this package. The
speed-grade spelling does not give the backend a validated -2 timing model.

The preserved PR66 engine build passed in
`artifacts/openxc7-toolchain/nextpnr-pr66-20261005T164805878986Z/report.json`.
The completed database is 63,345,877 bytes, with SHA256
`790e05a88e8406b4da69d4fb6a158317e2621c85aa8452973edc85a8a4be1492`.
The implementation in
`artifacts/openxc7-20261005T190339180054Z/report.json` failed when the full
database integrity check exceeded 7,200 seconds while checking PIP names.
No placement, routing or bitstream was produced. Earlier tracking handles became unavailable
after an execution-environment change. The original reports remain preserved,
and `artifacts/runtime-handover-20261005T190335183717Z.json` records that
handover and the unchanged input/tool hashes.

The pinned backend scans every tile PIP to resolve each PIP name. The full
480T database contains 209,267,576 expanded PIPs, so this round-trip test
performs a very large number of linear scans. The local
`tools/nextpnr-pip-name-lookup.patch` instead uses each destination wire's
existing incoming-PIP list and retains the lowest matching PIP index. It
does not change database contents, inversion selection or the integrity-test
body. `tools/build_pip_lookup.py` checks all raw upstream/downstream adjacency
tables, copies and patches only `himbaechel/arch.cc`, and replaces exactly
that object in the verified PR66 link. The original source checkout and
parent binary remain unchanged. Its report distinguishes the pinned PR66
base from this additional local source delta. The full architecture test
must still pass with the rebuilt executable; the raw adjacency census alone
does not establish that pass.

Rebuild the lookup variant from the preserved PR66 parent:

```sh
python3 ypcb_demo/tools/build_pip_lookup.py \
  --engine-source ypcb_demo/artifacts/openxc7-toolchain/nextpnr \
  --parent-manifest ypcb_demo/artifacts/openxc7-toolchain/nextpnr-pr66-20261005T164805878986Z/report.json \
  --chipdb "$YPCB_TOOLS/nextpnr-build/himbaechel/uarch/xilinx/chipdb-xc7k480t.bin"
```

The first lookup build passed in
`artifacts/openxc7-toolchain/nextpnr-lookup-20261005T211614473260Z/report.json`.
Its full test in `artifacts/openxc7-20261005T212452164570Z/report.json` also
exceeded 7,200 seconds during PIP-name checks. It still scanned each tile's
wire table on every lookup. Both timeout results remain failures; neither
produced placement/routing or a bitstream. The terminal transition and all
27 reverified source hashes are recorded in
`artifacts/engine-transition-20261005T233130585960Z.json`.

The indexed variant uses `tools/nextpnr-pip-name-index.patch`. Each thread
indexes raw wire names once per tile type, retains all aliases and selects
the original lowest matching PIP index from the incoming-PIP lists. The
database contains 94,488 raw wire rows across 98 types, rather than
60,712,913 expanded raw wires across tile instances. Architecture construction
and database reload increment a global epoch, invalidating caches in every
thread; owner checks also separate live contexts. Only `arch.cc` changes.
The class layout, database and full integrity-test object remain unchanged.

`tools/build_pip_index.py` applies that exact patch to a copy of the pinned
source and replaces the single architecture object in the verified PR66
link. Its build passed in
`artifacts/openxc7-toolchain/nextpnr-index-20261005T230057413667Z/report.json`:
compilation took 604.941 seconds, linking 44.185 seconds, and the executable
version check passed. The build ran at niceness 5. The actual executable's
SHA256 is `b711ded613a3c968246fe0087185d9bcfc21371271c362ad666c9b10246fac3c`.
The initial patch-preparation record remains in
`artifacts/openxc7-toolchain/nextpnr-index-preparation-20261005T224903127179Z/report.json`.

Rebuild the indexed variant with the same arguments as `build_pip_lookup.py`,
substituting `build_pip_index.py`. `check_engine.py` verifies the patch bytes,
parent manifest, unchanged test object, single-object replacement and actual
input/output hashes. The runner and SRAM loader retain this explicit local
source delta alongside the pinned PR66 base revision.

The first indexed full test in
`artifacts/chipdb-check-20261005T232333701344Z/report.json` timed out after
7,201.678 seconds (return code -9). It finished entity-name, location,
wire-connectivity and BEL-connectivity checks before timing out during
all-PIP connectivity. The dependent TPU runner
`artifacts/openxc7-20261005T233936027394Z/report.json` and comparison
`artifacts/bitstream-check-20261005T234042092561Z/report.json` also ended
`FAIL`; no placement/routing, bitstream or reference encoding was produced.
The terminal record in `artifacts/engine-transition-20261006T012649995523Z.json`
reverified all 78 database-test inputs and all 29 TPU source inputs unchanged.

The four-hour attempt in
`artifacts/chipdb-check-20261006T012557572868Z/report.json` also timed out
during routing connectivity, after 14,400.574 seconds (return code -9).
Its dependent runner `artifacts/openxc7-20261006T012715613029Z/report.json`
and comparison `artifacts/bitstream-check-20261006T012909183071Z/report.json`
ended `FAIL` before routing or encoding. No mismatch was reported before
the time limit, but the full test did not pass. The terminal audit in
`artifacts/engine-transition-20261006T052933486669Z.json` reverified all
78 database-test inputs and 29 TPU source inputs unchanged.

The unchanged full test in
`artifacts/chipdb-check-20261006T053306875084Z/report.json` passed all seven
phases with return code 0 after 11,180.881 seconds. All 78 captured inputs
were unchanged. It used an eight-hour limit and normal process priority
(niceness 0). Its observer in
`artifacts/chipdb-resource-watch-20261006T053240947141Z/` records CPU time,
resident memory and process state for the checker and its actual descendants
every 30 seconds. The observer was checked against a real CPU-bound child;
that separate check is `artifacts/resource-observer-probe-20261006T053147128756Z.json`.
These measurements do not establish the cause of the previous timeout.
The executable, database, test body and acceptance gates are unchanged.
To reproduce the observed run:

```sh
python3 ypcb_demo/tools/watch_chipdb.py \
  --build-manifest ypcb_demo/artifacts/openxc7-toolchain/nextpnr-index-20261005T230057413667Z/report.json \
  --nextpnr ypcb_demo/artifacts/openxc7-toolchain/nextpnr-index-20261005T230057413667Z/nextpnr-himbaechel \
  --chipdb "$YPCB_TOOLS/nextpnr-build/himbaechel/uarch/xilinx/chipdb-xc7k480t.bin" \
  --timeout 28800 --interval 30
```

`check_chipdb.py` runs the exact unchanged `--test` command, records the child PID,
timeout, exit status and input/output hashes, and requires all test phases
in order. The pinned command handler returns zero only after `archcheck()`
returns. The full architecture test has passed; timing, electrical and
physical signoff require separate evidence.

From the repository root, prepare or execute the route:

```sh
python3 ypcb_demo/tools/openxc7.py \
  --mapped-run ypcb_demo/artifacts/verify-20261005T135302980969Z \
  --nextpnr ypcb_demo/artifacts/openxc7-toolchain/nextpnr-index-20261005T230057413667Z/nextpnr-himbaechel \
  --engine-source ypcb_demo/artifacts/openxc7-toolchain/nextpnr \
  --nextpnr-build-manifest ypcb_demo/artifacts/openxc7-toolchain/nextpnr-index-20261005T230057413667Z/report.json \
  --chipdb "$YPCB_TOOLS/nextpnr-build/himbaechel/uarch/xilinx/chipdb-xc7k480t.bin" \
  --database "$YPCB_TOOLS/prjxray-db" \
  --database-check-run ypcb_demo/artifacts/chipdb-check-20261006T053306875084Z \
  --wait-database-check 36000
```

Add `--prepare-only` to generate the translated XDC, mapping JSON, manifest
and exact command without needing completed tools. The recorded mapping
donor has a successful synthesis command but a failed historical verifier
result due to portless `$scopeinfo` metadata; that original result remains
unchanged. The importer checks the successful command, original synthesis
script, input hashes, structural check, eight DSPs and package ports. It
removes only portless `$scopeinfo`, recording the derived JSON's hash.
XDC is not an input to the validated Yosys script; the donor constraint hash
is retained separately while new implementation constraints are applied.

For the original PR66 build, the runner can wait for its completion log and
the shared database build log without reading unfinished binary files:

```sh
  --wait-tools 14400 \
  --nextpnr-build-log ypcb_demo/artifacts/openxc7-toolchain/nextpnr-pr66-build.log \
  --chipdb-build-log "$YPCB_TOOLS/nextpnr-build.log"
```

For an indexed build, require `BUILT_PR66_INDEX_ENGINE_VARIANT` in its
manifest first, then run without `--wait-tools`. The retained lookup variant
uses `BUILT_PR66_LOOKUP_ENGINE_VARIANT`.

The runner checks pinned clean source/database revisions and records binary,
database, design and constraint hashes. It runs the backend's unchanged
full database integrity check before placement and routing, uses seed 480,
requests 50 MHz and hold repair, and retains routed JSON, FASM and detailed
timing reports. It never adds timing-failure, unrouted-net, or ignored-loop
waivers. Failed checks remain failures.

`--database-check-run` reuses only a completed `PASS_FULL_DATABASE_CHECK`
for the exact executable, database and part. Validation rechecks the known
engine build, every captured input hash, the exact full-test command, all
phase markers and the log hash. The copied command and log retain their
original provenance. `--wait-database-check` waits for that existing job;
an unfinished or failed report is rejected. The hardware loader independently
revalidates the same reuse record. Without this option, the runner executes
its own full test as before. Placement/routing, DSP FASM validation and
native candidate assembly and full TPU bitstream comparison have passed.

The indexed TPU runner is
`artifacts/openxc7-20261006T053431049894Z/report.json`. It validated and reused
the passing full test above, and completed placement/routing with return
code 0 after 6,276.605 seconds. The backend's 50 MHz check, all eight DSP
configuration checks and the reset pad's pull-up check passed. Native
assembly returned zero and produced `tiny_tpu_ypcb.experimental.bit`
(18,735,109 bytes), with status `OPENXC7_BITSTREAM_EXPERIMENTAL`.
The audit in `artifacts/candidate-audit-20261006T103001306948Z.json`
recomputed all 29 source hashes and all four output hashes successfully.
The candidate uses LVCMOS18 without physical confirmation; configuration
voltage and CFGBVS remain unknown. The timing-model limitations below still
apply. Its full bitstream
comparison is `artifacts/bitstream-check-20261006T053618412738Z/report.json`,
attached to that same run and passed. Its independently encoded reference
matches all 18,735,004 configuration bytes. No physical operation has
been established.

The XDC translator expands each of the five package pins and I/O standards
into literal port constraints and retains the 20 ns clock. It also applies
`PULLTYPE PULLUP` to the R28 `SW_RESET` input, which defaults the active-low
reset high when undriven. PCIe PERST# is a separate input at Y26. Successful
FASM must contain the reset pad's exact pull-up feature before assembly.
`check_dsp_fasm.py` also requires eight emitted DSP sites with the mapped
combinational register modes, all eight previously missing constant pins
tied to GND, their inverters disabled, and no opposite VCC tieoffs. The
hardware loader independently rechecks the same design/FASM hashes and
DSP configuration before allowing SRAM loading.
This backend does not implement `set_false_path`; the two original exceptions for external
asynchronous reset and unsynchronized LEDs are recorded as not applied.
No internal false paths are introduced. Clock I/O remains the reference
default LVCMOS18 unless `--clock-iostandard` is supplied; the conflicting
physical clock voltage evidence is described in BOARD_REFERENCES.md.

Successful routing is labeled `OPENXC7_PNR_EXPERIMENTAL`. The current source
selects `DEFAULT` timing, uses stub LUT/FF timing, and imports Artix SDF for
Kintex carry/mux/BRAM paths. DSP timing coverage and vendor electrical DRC
are not established. A backend timing report is useful evidence, but it
does not establish full setup/hold or electrical signoff on this card.

Optional `--fpga-as /absolute/path/to/fpga-as` assembles the routed FASM to
`tiny_tpu_ypcb.experimental.bit`. Supply two passing `--assembler-check`
run directories, one each for `empty-design` and `reset-pullup`, produced by
the component checker below. They must identify the exact assembler binary
being used. Candidate assembly may use the reference
LVCMOS18 clock constraint while electrical facts remain unconfirmed; the
manifest preserves that uncertainty. Before loading, supply board-confirmed
`--clock-iostandard`, `--config-voltage` and `--cfgbvs` either when building or
in the SRAM-loading command. The confirmed clock standard must match the
candidate; a different standard requires a rebuild. Assembly uses the same
pinned database and exact part and checks
the output part header; it is labeled `OPENXC7_BITSTREAM_EXPERIMENTAL`. The hardware loader
accepts this status only with the explicit `--experimental-openxc7` option;
it requires successful database/P&R/assembly results, the backend's 50 MHz
constraint, confirmed electrical values, the exact part header and bitstream
hash. It records incomplete timing/DRC coverage in the load report. The
ordinary Vivado load path keeps its existing requirements. No programming
or flash operation occurs in this implementation flow.

For the current preserved and verified assembler, add:

```sh
  --fpga-as ypcb_demo/artifacts/openxc7-toolchain/fpga-as-input-ecc.elf \
  --assembler-check ypcb_demo/artifacts/assembler-check-20261005T174806522228Z \
  --assembler-check ypcb_demo/artifacts/assembler-check-20261005T174808668846Z
```

The user-supplied openTPU reference
`openTPU/boards/ypcb-00338/constraints/otpu_top_ld.xdc`
sets AA28 at 50 MHz with LVCMOS18 (lines 8–10), and configuration voltage
1.8 V with CFGBVS GND (lines 81–83). These are explicit source settings;
openTPU's `docs/flash.md` labels them as configuration settings and says the
flash procedure has not run on the card. Their reference provenance is
recorded separately from a physical voltage/strap measurement.

The actual candidate passed read-only loading validation with those reference
parameters in
`artifacts/loader-fix-20261006T123204180152Z/offline-preflight.json`.
This check revalidated the engine/database provenance, design/FASM, DSP
configuration and bitstream hash/header. It performed no USB/JTAG operation.
It also fixed a loader defect: validation of a reused database test had
replaced the candidate directory, causing design/bitstream lookup in the
database-test directory. `tools/test_hardware.py` reproduces that error with
the preserved old helper and passes with the fix; `make test` and clean
bundle verification now run that regression.

The original build and full-encoding comparison retain their recorded helper
hash. The old helper is preserved in
`artifacts/loader-fix-20261006T123204180152Z/hardware.before.py`; only the
directory variable in `checked_bitstream` changed. The bitstream-header
parser, programming command and timeout calculation are unchanged. The
offline-preflight report binds the fixed helper to the unchanged candidate;
the historical build/comparison reports have not been rewritten.

After a fresh host preflight and selection of these reference settings for
the card, the prepared experimental SRAM-loading command is:

```sh
python3 ypcb_demo/tools/hardware.py program-sram \
  --run ypcb_demo/artifacts/openxc7-20261006T053431049894Z --experimental-openxc7 \
  --clock-iostandard LVCMOS18 --config-voltage 1.8 --cfgbvs GND
```

This command changes FPGA SRAM. It has not been executed in this preparation
work. It still requires the initialized DLC10 and a fresh single-device chain
with IDCODE `0x23751093`, and never writes flash. LED behavior after loading
must establish whether the TPU application actually ran.

The mapped Icarus run reached its 900-second wall-clock limit. A diagnostic
showed VVP was still loading its 47 MiB compiled netlist. To reuse the
successful compilation with identical RTL, source list, benches and primitive models,
while preserving the original timeout result:

```sh
python3 ypcb_demo/tools/verify.py \
  --compiled-run ypcb_demo/artifacts/verify-20261005T145421927320Z \
  --mapped-timeout 3600
```

The new run records compilation provenance and hash, repeats the native
golden/fault regressions, and keeps every mapped assertion unchanged. Verbose
VVP loading and event statistics help distinguish loading time from a
simulation that does not advance. A pending run is not a passing result.

This reuse completed successfully in
`artifacts/verify-20261005T153156380147Z/report.json`: the native golden/fault
tests and unchanged mapped stability/reset regression all passed. VVP
reported 4,707 simulation time steps and the final
`YPCB MAPPED REGRESSION PASS` marker. The mapped design retains eight real
DSP48E1 cells. Earlier timeout and metadata-failure reports remain unchanged.
`make synth` now allows 3,600 seconds for mapped simulation on this busy host.
The implementation and candidate assembly results above remain separate
from this simulation evidence. Physical operation has not been established.

Before assembling the TPU configuration, compare the native assembler
against the independently retained exact-part frame-tool smoke artifacts:

```sh
python3 ypcb_demo/tools/check_assembler.py \
  --fpga-as ypcb_demo/artifacts/openxc7-toolchain/fpga-as-input-ecc.elf \
  --database "$YPCB_TOOLS/prjxray-db" \
  --reference-run "$YPCB_TOOLS/frame-smoke-1791213825695214370"
```

The checker verifies the reference artifact hashes, validates exact-part
frame geometry, compares effective frames including implicit zero filling,
and compares the entire configuration payload after excluding only `.bit`
metadata. It also requires rejection of unknown features and an inverted
FASM bit range. Its report preserves commands, tool/input hashes and failures.
These are component checks; they do not establish that the TPU was routed
or that a board ran it. A nonzero R28 pull-up reference also passed in
`artifacts/reset-frame-reference-20261005T165019765654Z/report.json`, with
two nonzero frame words. Compare that fixture using the same command with
`--reference-run` pointing there and `--reference-stem reset-pullup`.
The separate upstream FASM parser test and full native assembler build
passed. The initial full-assembler comparison reproduced a wrapper bug:
an inverted `[0:8]` range was skipped by the parser but accepted by the
assembler. Its valid empty-design frames and 18,735,004-byte configuration
payload matched the independent reference exactly. The failed check remains
in `artifacts/assembler-check-20261005T165907818781Z/report.json`.
The parser-only fix rejected both invalid inputs and passed the empty-design
comparison. The nonzero reset-pad comparison then exposed a second defect:
the raw frames matched, but the encoded payload omitted the frame ECC words
at addresses `0x26` and `0x27`. The reference values are `0x1a1e` and
`0x1a1d`; an independent calculation from the native raw frames matches both.
The failed comparison remains in
`artifacts/assembler-check-20261005T171446026935Z/report.json`.
`tools/fpga-as-input-ecc.patch` contains both corrections: reject parser
results at or above `kSkipped`, and update each XC7 frame's ECC before
encoding. The parser itself is unchanged. It also supplies the ECC helper's
explicit template argument and the encoder's build dependency.
The corrected native build and both repeated component comparisons passed:
`artifacts/assembler-check-20261005T174806522228Z/report.json` covers the empty
design, and `artifacts/assembler-check-20261005T174808668846Z/report.json`
covers the nonzero reset pad. Both have zero effective frame mismatches,
identical complete configuration payloads and nonzero exits for both invalid
inputs. The preserved executable's SHA256 is
`f874ab5287d5213e8d7efc02af8a7bc3c668b536d6b2d5e845efbe846beeb4b4`.
The earlier parser-only patch is retained for provenance. The combined patch
was applied to pristine pinned files in a temporary directory and reproduced
the actual build inputs exactly. Apply it to the pinned assembler source when reproducing this port;
the successful comparison reports must identify the resulting binary hash.
The local build
uses Bazel 9.2.0, while the upstream installer
pins 8.5.0; actual output is checked rather than assuming version equivalence.
The successful build explicitly selects the installed LLVM linker; earlier
missing-linker and incompatible-bfd attempts are preserved in the tool logs.

With Bazel and LLVM `ld.lld` on `PATH`, run this from the pinned assembler
checkout (keep `TPU_ARTIFACTS` set to the absolute path above):

```sh
bazel --batch \
  --output_user_root="$TPU_ARTIFACTS/openxc7-toolchain/bazel-cache" \
  build //fpga:fpga-as -c opt --curses=no --jobs=2 --verbose_failures \
  --linkopt="-B$(dirname "$(command -v ld.lld)")" --linkopt=-fuse-ld=lld
```

For an isolated DSP engine build, from the repository root:

```sh
python3 ypcb_demo/tools/build_pr66.py \
  --base-source "$YPCB_TOOLS/nextpnr-himbaechel" \
  --base-build "$YPCB_TOOLS/nextpnr-cli-build" \
  --base-build-log "$YPCB_TOOLS/nextpnr-cli-build.log" \
  --fixed-source ypcb_demo/artifacts/openxc7-toolchain/nextpnr
```

Use the new run's executable and manifest when repeating that command. Both
source checkouts must be clean at their exact pinned revisions, and the base
build must finish before its objects can be linked.

After the TPU candidate is assembled, compare its complete configuration
payload against the independent pinned Project X-Ray converter and native
frame encoder:

```sh
python3 ypcb_demo/tools/check_bitstream.py \
  --run ypcb_demo/artifacts/openxc7-20261006T053431049894Z \
  --prjxray "$YPCB_TOOLS/prjxray" \
  --database "$YPCB_TOOLS/prjxray-db" \
  --python "$YPCB_TOOLS/xray-venv/bin/python" \
  --frames2bit "$YPCB_TOOLS/prjxray-build/tools/xc7frames2bit"
```

`--wait-seconds 57600` lets this check wait for the existing TPU runner;
it does not restart that run. The checker accepts only a completed bitstream
with passing database/route/assembly results and matching FASM/bit hashes.
It records tool/input hashes, commands and timeouts, validates the reference
frame geometry, and compares every configuration byte after excluding only
the `.bit` metadata. A passing result is
`PASS_FULL_TPU_BITSTREAM_COMPARISON`. The real candidate comparison passed:
all 18,735,004 configuration bytes match, with payload SHA-256
`9c488528c5c73be9a504929e2e61bd7705c48a6f7dd22e0522f44677cd3fb138`.
The reference frame check covered 46,336 legal target frames and counted
139,026 nonzero words. The FASM-to-frames command returned zero after
1,702.2 seconds; frames-to-bit returned zero after 7.707 seconds.
The reference encoder reported that the fast ANTLR parser was unavailable
and completed using the slower Python textX fallback.
This verifies complete bitstream encoding without claiming timing,
electrical DRC or physical execution. Earlier empty and reset-pad comparisons
remain separate component evidence.
