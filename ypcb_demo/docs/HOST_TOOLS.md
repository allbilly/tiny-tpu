# Host tools and installation history

## Full Vivado build verified, 2026-10-16

On the recorded build host, `~/vivado/2026.1/Vivado/bin/vivado -version` returned Vivado
2026.1, build 6511674. Synthesis and implementation successfully checked out
their licenses for `xc7k480t`. The actual tiny-tpu vendor build is
`artifacts/build-20261016T102846491634Z/report.json`, status `BITSTREAM_PASS`.
It generated the compressed exact-part bitstream with complete routing,
setup slack +0.544 ns, hold slack +0.108 ns and no blocking DRC violations
at a 150 MHz TPU clock. An MMCM derives that clock from the unchanged 50 MHz
AA28 input. The routed functional regression also passed with the vendor models.
The 30 retained DRC warnings recommend DSP pipelining. Current commands and
artifact hashes are in [../README.md](../README.md).

`~/openTPU` was the reference checkout;
`~/ypcb_ref/openTPU` resolved to it. Its board XDC and Vivado Tcl
agree on `xc7k480tffg1156-2`, the 50 MHz AA28 LVCMOS18 clock, LED pins,
CONFIG_VOLTAGE 1.8 and CFGBVS GND. Reference hashes and revision are retained
in `artifacts/vivado-reference-20261015T163335327441Z.json`.

## Historical audit, 2026-10-05

At that time, tools included native Icarus, Yosys and Verilator under Linuxbrew,
`/usr/bin/openFPGALoader`, the cable-specific
`~/ypcb/openFPGALoader/build-xpcu-debug/openFPGALoader` (v1.1.1),
and `~/vivado/2026.1.1/Vivado_Lab/bin/vivado_lab`.
`command -v vivado` then returned no executable. The following search and
installer observations describe that earlier snapshot.

An expanded filesystem search inspected 241,992 files in `/home`, `/opt`,
`/tools`, `/usr/local`, `/mnt`, `/media`, `/run/media`, `/srv`, `/tmp` and
`/root` where accessible. It excluded Git internals, caches, rootless
container storage and several generated dependency trees. A separate follow-up
searched all 2,897 files under `et-platform`, 9,814 under `tt-metal`, and 28
under `.local/bin`; it found no Vivado executable or installer. The two running
Podman environments also have no `vivado` on PATH or Xilinx/AMD tool directory
at the standard `/opt` and `/tools` locations. Unreadable private
system service directories are outside the user development projects.
Mounted persistent filesystems are the local NVMe root/home and boot
partitions; no separate mounted build/tool drive was present.

The only Xilinx installer found is
`~/vivado/v.tar` (4,080,148,480 bytes). Its root directory is
`Vivado_Lab_2026.1.1_0904_0832`, verified by inspecting the tar members.
The extracted installer and `.Xilinx/registry/installedSW.xml` also identify
Vivado Lab Edition (Standalone). This archive cannot restore full Vivado.
The current Podman images and containers contain simulator/build environments;
none is identified as a Vivado environment. No installation was removed.
The live `vivado_lab -version` probe produced no output before a 90-second
timeout; that result is retained in `artifacts/tool-probe/report.json`.
The Lab installation files exist, but its current runtime health is not
established by this probe. The prepared SRAM-loading route uses the separate
Fedora openFPGALoader binary, which did return its version successfully.

The `rknpu-fpga` project contains a native openXC7 toolchain and the exact
XC7K480T database. Its unchanged integrity test failed on BEL name lookup;
the latest isolated lookup-fix attempt also failed (`std::bad_alloc`) in
`build/openxc7/lookupfix-1790000053728435896/report.json`. The donor documents
missing hold analysis and incomplete device timing coverage. These are useful
diagnostic tools but do not establish a passing implementation route.

The user selected an open-source alternative. The current openXC7 installer
uses the newer Himbaechel Xilinx backend, with nextpnr revision
`c68c13582e972292c86a5025140d52e713384cbc` and database revision
`517d66a383676cb971177ea92b0ff3b6ea6e8690`. The `tiny-gpu` project's matching
engine and database builds completed and are reused read-only.
The TPU requires the DSP fix from openXC7 PR66 on top of this base engine;
its two changed source files were compiled separately here. The subsequent
full database check and its first lookup optimization both timed out during
PIP-name checks. The per-type indexed variant has now built successfully
without changing the class layout, test body or database. Its independent
full architecture check finished names, locations, wire connectivity and
BEL connectivity before a two-hour timeout during all-PIP connectivity.
On 2026-10-06 the four-hour attempt also timed out during routing connectivity.
The same full test passed all phases with return code 0 after 11,180.881
seconds, using an eight-hour limit, normal process priority and an observer
recording CPU, memory and process state. All 78 captured inputs were unchanged.
The TPU runner reused that verified result, passed placement/routing and
generated an experimental candidate bitstream. Its eight DSP configuration
checks and reset pull-up check passed. Independent full payload comparison
also passed: all 18,735,004 configuration bytes matched. Physical
clock/configuration voltages and CFGBVS still need confirmation. The inspected
Apio openXC7 release `2026-10-03` lists no Kintex-7 parts, so it cannot supply
this card's chip database. The separate TPU runner, assembler corrections,
and experimental timing limits are documented in
[OPENXC7.md](OPENXC7.md). Full Vivado is an optional alternative below.

## Earlier full Vivado installer research, 2026-10-05

AMD's [Vivado licensing table](https://www.amd.com/en/products/software/adaptive-socs-and-fpgas/vivado/vivado-licensing-options.html)
listed the free **Basic** tier with Linux support and all 7-series devices
when inspected for the earlier audit. The exact-part installed tool/license
has now been tested successfully as recorded above. That audit noted Basic's
annual license renewal. Old Standard-edition device lists are not sufficient
to describe the 2026.1 licensing model.

Use the [AMD 2026.1 downloads page](https://www.amd.com/en/support/downloads/adaptive-socs-and-fpgas/development-tools/2026-1.html)
and select **AMD Unified Installer for FPGAs & Adaptive SoCs 2026.1: Linux
Self Extracting Web Installer**, 394.17 MB. The official filename is
`FPGAs_AdaptiveSoCs_Unified_SDI_2026.1_0616_1700_Lin64.bin`.
The [download entry](https://account.amd.com/en/forms/downloads/xef.html?filename=FPGAs_AdaptiveSoCs_Unified_SDI_2026.1_0616_1700_Lin64.bin)
redirected this session to an account/session error, so no installer payload
was downloaded or installed. Authenticated AMD access is needed to continue
through that route.

For a Basic license, select **2026.1**, not 2026.1.1: AMD's downloads page
explicitly says Basic does not support minor releases such as 2026.1.1.
Install full Vivado with Kintex-7 device support into a separate destination
alongside the existing Lab tools, then obtain the applicable Basic license.
Vitis, Versal device support and the 96 GB all-products archive are not
required by this RTL project.

At the time of that audit, the installer had not been run on its Linux host.
Tool/device/license compatibility and any missing host libraries must be
verified after installation. A compatible Linux build machine can consume the
prepared source bundle without changing the board programming host.

After installation, verify:

```sh
/absolute/path/to/Vivado/bin/vivado -version
python3 ypcb_demo/tools/build.py --vivado /absolute/path/to/Vivado/bin/vivado
```

The build exercises the exact target part and records tool/licensing failures
as failures. It requires no board connection for synthesis or implementation.
