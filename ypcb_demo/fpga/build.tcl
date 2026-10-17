# Run via tools/build.py with full Vivado. Never programs hardware.
if {[llength [info commands synth_design]] == 0} {
    error "Full Vivado is required; Vivado Lab cannot synthesize or implement"
}
if {[llength $argv] ni {8 9}} {
    error "Expected repository output-directory LED-polarity emit-bitstream voltage CFGBVS part clock-IO-standard ?core-MHz?"
}
lassign $argv root out active_low emit voltage cfgbvs part clock_standard core_mhz
if {$core_mhz eq ""} { set core_mhz 50 }
if {$core_mhz ni {50 100 150}} { error "Unsupported core clock frequency" }
if {$part ne "xc7k480tffg1156-2"} { error "Wrong FPGA part" }
if {$active_low ni {0 1} || $emit ni {0 1}} { error "Invalid boolean build option" }
if {$clock_standard ni {LVCMOS18 LVCMOS33}} { error "Invalid clock I/O standard" }
if {$emit && ($voltage eq "" || $cfgbvs eq "")} {
    error "Bitstream requires board-verified configuration voltage and CFGBVS"
}
if {$voltage ne "" || $cfgbvs ne ""} {
    if {$voltage ni {1.5 1.8 2.5 3.3} || $cfgbvs ni {GND VCCO}} {
        error "Invalid configuration voltage or CFGBVS"
    }
    if {($voltage in {1.5 1.8} && $cfgbvs ne "GND") ||
        ($voltage in {2.5 3.3} && $cfgbvs ne "VCCO")} {
        error "Incompatible voltage and CFGBVS"
    }
}
file mkdir $out
set_param general.maxThreads 4
create_project -in_memory -part $part
set filelist [open [file join $root ypcb_demo fpga sources.f] r]
foreach line [split [read $filelist] "\n"] {
    if {[string trim $line] ne ""} { read_verilog [file join $root $line] }
}
close $filelist
read_xdc [file join $root ypcb_demo fpga ypcb.xdc]
set generics [list LED_ACTIVE_LOW=$active_low CORE_CLOCK_MHZ=$core_mhz]
if {$core_mhz > 50} {
    synth_design -top ypcb_tiny_tpu_top -part $part -generic $generics -retiming -fanout_limit 100
} else {
    synth_design -top ypcb_tiny_tpu_top -part $part -generic $generics
}
# Ports are available after synthesis; before it the XDC is queued for reading.
set_property IOSTANDARD $clock_standard [get_ports SYS_CLK]
if {$voltage ne ""} {
    set_property CONFIG_VOLTAGE $voltage [current_design]
    set_property CFGBVS $cfgbvs [current_design]
}
# Match ~/openTPU/boards/ypcb-00338/constraints/otpu_top_ld.xdc.
# Leave the board's other interfaces undriven and compress the JTAG bitstream.
set_property BITSTREAM.CONFIG.UNUSEDPIN PULLNONE [current_design]
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
set_property CONFIG_MODE BPI16 [current_design]
set_property BITSTREAM.CONFIG.BPI_SYNC_MODE DISABLE [current_design]
if {[llength [get_cells -hierarchical -filter {REF_NAME == DSP48E1}]] == 0} {
    error "TPU DSP datapath missing after synthesis"
}
write_checkpoint -force [file join $out post_synth.dcp]
report_utilization -file [file join $out utilization_synth.rpt]
if {$core_mhz > 50} {
    opt_design -directive Explore
    place_design -directive Explore
    phys_opt_design -directive AggressiveExplore
    route_design -directive Explore
    phys_opt_design -directive AggressiveExplore
} else {
    opt_design
    place_design
    phys_opt_design
    route_design
}
write_checkpoint -force [file join $out routed.dcp]
report_utilization -file [file join $out utilization.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $out timing.rpt]
report_route_status -file [file join $out route_status.rpt]
report_clocks -file [file join $out clocks.rpt]
if {$core_mhz > 50} {
    set mmcms [get_cells -hierarchical -filter {REF_NAME == MMCME2_ADV}]
    if {[llength $mmcms] != 1} { error "Expected one core-clock MMCM" }
    set core_clocks [get_clocks -of_objects [get_pins -of_objects $mmcms -filter {REF_PIN_NAME == CLKOUT0}]]
} else {
    set core_clocks [get_clocks -of_objects [get_ports SYS_CLK]]
}
if {[llength $core_clocks] != 1} { error "Cannot identify the actual core clock" }
set core_period [get_property PERIOD $core_clocks]
if {abs($core_period - 1000.0 / $core_mhz) > 0.001} { error "Actual core clock period differs from request" }
foreach category {PLACED_FULLY ROUTED_FULLY} {
    if {[report_route_status -boolean_check $category] ne "1"} {
        error "Implementation is not $category; inspect route_status.rpt"
    }
}
if {[report_route_status -boolean_check ERRORS_IN_ROUTES] ne "0"} {
    error "Routing errors remain; inspect route_status.rpt"
}
check_timing -verbose -file [file join $out check_timing.rpt]
set drc_decks {default router_checks timing_checks}
if {$emit} { lappend drc_decks bitstream_checks }
report_drc -ruledecks $drc_decks -no_waivers -file [file join $out drc.rpt]
# Exclude only external asynchronous reset / LED timing at the XDC boundary.
# Missing internal clocks, endpoints or loops must not pass signoff.
set checks [check_timing -override_defaults {no_clock unconstrained_internal_endpoints loops} -return_string]
set coverage [open [file join $out internal_timing_coverage.rpt] w]
puts $coverage $checks
close $coverage
foreach category {no_clock unconstrained_internal_endpoints loops} {
    if {![regexp [format {checking %s \(([0-9]+)\)} $category] [string tolower $checks] -> count]} {
        error "Cannot establish timing coverage for $category; inspect internal_timing_coverage.rpt"
    }
    if {$count != 0} { error "$category has $count uncovered/invalid internal paths" }
}
set worst {}
foreach delay_type {max min} {
    set paths [get_timing_paths -delay_type $delay_type -max_paths 1]
    if {[llength $paths] != 1} { error "Missing $delay_type timing result" }
    set slack [get_property SLACK [lindex $paths 0]]
    if {![string is double -strict $slack] ||
        [catch {expr {abs(double($slack)) < Inf}} finite] || !$finite || $slack < 0} {
        error "Invalid or failing $delay_type timing slack: $slack"
    }
    lappend worst $slack
}
foreach violation [get_drc_violations] {
    set severity [string toupper [get_property SEVERITY $violation]]
    if {$severity ni {WARNING ADVISORY INFO}} {
        error "Blocking DRC violation $violation ($severity)"
    }
}
set routed_clock_standard [get_property IOSTANDARD [get_ports SYS_CLK]]
if {$routed_clock_standard ne $clock_standard} { error "Clock I/O standard changed during implementation" }
set reset_pulltype [get_property PULLTYPE [get_ports SYS_RSTN]]
if {$reset_pulltype ne "PULLUP"} { error "Reset input is missing its required pull-up" }
set result [open [file join $out signoff.json] w]
set deck_json "\"[join $drc_decks {","}]\""
puts $result "{\"part\":\"$part\",\"core_clock_mhz\":$core_mhz,\"core_clock_period_ns\":$core_period,\"clock_iostandard\":\"$routed_clock_standard\",\"reset_pulltype\":\"$reset_pulltype\",\"setup_slack_ns\":[lindex $worst 0],\"hold_slack_ns\":[lindex $worst 1],\"internal_timing_coverage\":true,\"fully_routed\":true,\"routing_errors\":0,\"blocking_drc\":0,\"drc_ruledecks\":\[$deck_json\]}"
close $result
if {$emit} { write_bitstream -force [file join $out tiny_tpu_ypcb.bit] }
puts "YPCB BUILD PASS; bitstream emitted=$emit; hardware not programmed"
