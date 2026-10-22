# Recorded 150 MHz FPGA test, October 17, 2026

These are byte-for-byte copies of the original test receipts and logs, retained
with the sources so that a checkout contains the evidence behind
[the physical test report](../../../../HARDWARE_TEST_20261017.md).
The JSON files retain historical host paths; the offline verifier resolves the
copies here and does not depend on those paths existing.

- `manifest.json`: combined physical test result and receipt hashes.
- `program.json`, `detect.log`, `program_sram.log`: successful volatile SRAM load.
- `observation.json`: all 95 raw boundary-scan captures, decoded pins and XADC values.
- `preflight.json`, `build.json`, `simulation.json`, `simulation.log`: image,
  implementation signoff and routed simulation bindings.
- `observe_selftest.py.snapshot`: exact observer source whose hash appears in
  the observation receipt; preserved for historical provenance.

From the checkout root, recheck the historical receipts and raw captures
without USB, JTAG, Vivado or network access:

```sh
python3 ypcb_demo/tools/verify_hardware_result.py
python3 ypcb_demo/tools/test_hardware_result.py
```

Historical evidence remains valid after later RTL changes. To also require
the current implementation inputs to match the recorded image, run:

```sh
python3 ypcb_demo/tools/verify_hardware_result.py --check-current-sources
```

This explicit check requires the build Tcl, XDC, source list and every compiled
source to have recorded hashes, and fails if an implementation file has changed or is missing;
the routine test target validates the archive independently of current RTL.
The observer snapshot remains the exact historical source; the reusable
observer has since replaced assertions with checks that also run under
`python -O` and `python -OO`.
It also checks the complete SRAM-load receipt and exact device pin mapping,
and reports PASS only after restoring BYPASS and closing the XVC connection.

The bitstream and vendor BSDL are not bundled. If the original bitstream is
available, add `--bitstream PATH/TO/tiny_tpu_ypcb.bit` to also check its hash.
The raw captures use the recorded XC7K480T-FFG1156 BSDL mapping and hash.
Receipt consistency checks validate archived evidence; they do not contact the
board or establish its present state. The recorded test performed no flash
write; it did not independently read back the complete boot flash contents.
