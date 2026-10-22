.PHONY: test synth audit prepare build bitstream
test:
	python3 tools/test_hardware.py
	python3 tools/test_build_tools.py
	python3 tools/test_observe_selftest.py
	python3 tools/test_hardware_result.py
	python3 tools/verify.py
synth:
	python3 tools/verify.py --synth --mapped-timeout 3600
audit:
	python3 tools/audit_home.py
prepare:
	python3 tools/build.py --prepare-only
build:
	python3 tools/build.py
# Set the physical configuration-bank values and confirmed AA28 clock standard.
bitstream:
	python3 tools/build.py --emit-bitstream --config-voltage "$(CONFIG_VOLTAGE)" --cfgbvs "$(CFGBVS)" --clock-iostandard "$(CLOCK_IOSTANDARD)"
