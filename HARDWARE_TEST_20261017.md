# YPCB tiny-tpu 150 MHz physical quick test

**Result: PASS**, October 17, 2026. The existing 150 MHz image was loaded
into FPGA SRAM and its built-in XOR self-test was observed over JTAG.

| Check | Observed result |
| --- | --- |
| FPGA | XC7K480T, JTAG IDCODE `0x23751093` |
| Image | `ypcb_demo/artifacts/build-20261016T102846491634Z/tiny_tpu_ypcb.bit` |
| Configured compute clock | 150 MHz; board input remains 50 MHz |
| SRAM load | Successful |
| Observation | Five seconds, 95 JTAG SAMPLE captures |
| PASS output | Asserted in all 95 captures |
| Sticky failure output | Clear in all 95 captures |
| Output drivers | Enabled in all captures |
| Heartbeat | 22 transitions |
| FPGA die temperature | 38.39°C before, 39.25°C after |
| Permanent flash | No erase or write performed by this test |

Bitstream SHA-256:

```text
b377cee1dc3082ab6de7cced229a1538bfa987335cfa5dc8cbd63a9268ecae42
```

Before programming, the bitstream, exact part, routed simulation, critical
RTL hashes and build signoff were checked. The retained build has +0.544 ns
setup slack, +0.108 ns hold slack, complete routing and zero blocking DRC
violations.

The physical observation used the matching AMD `xc7k480t_ffg1156.bsd` from
the local Vivado installation. SAMPLE reads showed M30/PASS high,
N30/failure low and P30/heartbeat toggling. Dedicated XADC reads supplied
the two temperatures above. The observer did not select EXTEST or pulse
the design reset.

PASS is the built-in exact XOR scoreboard's result. Internal score/count
registers were not read directly. This test covers the XOR compute demo;
it does not test external DDR, PCIe, MNIST or LLM inference. The configured
150 MHz clock comes from the checked image; frequency was not independently
measured.

## Evidence

- [Combined test receipt](ypcb_demo/docs/hardware/20261017/manifest.json)
- [All boundary-scan samples](ypcb_demo/docs/hardware/20261017/observation.json)
- [SRAM programming receipt](ypcb_demo/docs/hardware/20261017/program.json)
- [Preload checks](ypcb_demo/docs/hardware/20261017/preflight.json)
- [Build signoff](ypcb_demo/docs/hardware/20261017/build.json)
- [Routed simulation](ypcb_demo/docs/hardware/20261017/simulation.json)
- [Evidence notes and offline verification](ypcb_demo/docs/hardware/20261017/README.md)
- [Reusable observer](ypcb_demo/tools/observe_selftest.py)

These small receipts and all raw captures are retained with the source;
generated bitstreams remain in the ignored `artifacts/` directory. Recheck
the archived evidence from the checkout root without accessing the FPGA:

```sh
python3 ypcb_demo/tools/verify_hardware_result.py
make -C ypcb_demo test
```

Archive validation checks the recorded receipts and captures independently
of later source edits. Add `--check-current-sources` to the verifier to also
require current implementation files to match the tested image. The original
observer snapshot is retained unchanged; the reusable observer now uses
explicit checks that remain active with optimized Python.

JTAG was released after the five-second observation. At the end of that
recorded test the FPGA was left running the repeated XOR self-test in SRAM.
No observer or programmer was running when the host processes were checked
during the commit review. That review did not query the FPGA again, so it
does not confirm the current SRAM image or live output state.

SRAM configuration is volatile; power cycling reloads the existing boot
image. The receipt records that image as OpenTPU c0172080. This test did not
independently read back the complete flash contents.
