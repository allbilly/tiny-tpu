# YPCB-00338-1P1, xc7k480tffg1156-2.
# References: ~/ypcb/ref/ypcb_00338_1p1_hack/constraints/ypcb003381p1.xdc
# and LiteX litex_boards/platforms/inspur.py. All used I/O is LVCMOS18.
set_property PACKAGE_PIN AA28 [get_ports SYS_CLK]
set_property PACKAGE_PIN R28 [get_ports SYS_RSTN]
set_property PACKAGE_PIN P30 [get_ports {led[0]}]
set_property PACKAGE_PIN M30 [get_ports {led[1]}]
set_property PACKAGE_PIN N30 [get_ports {led[2]}]
set_property IOSTANDARD LVCMOS18 [get_ports {SYS_CLK SYS_RSTN led[*]}]
# R28 is SW_RESET, separate from PCIe PERST# on Y26.
# Default to deasserted if no external source drives the active-low reset.
set_property PULLTYPE PULLUP [get_ports SYS_RSTN]
create_clock -name sys_clk -period 20.000 [get_ports SYS_CLK]
# Only the external asynchronous reset assertion is excluded from timing.
set_false_path -from [get_ports SYS_RSTN]
# LEDs have no receiving synchronous clock. All internal paths remain timed.
set_false_path -to [get_ports {led[*]}]
