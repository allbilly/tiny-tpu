`timescale 1ns/1ps
`default_nettype none
module ypcb_tiny_tpu_top #(
    parameter LED_ACTIVE_LOW = 0,
    parameter integer WATCHDOG_CYCLES = 1024,
    parameter integer CORE_CLOCK_MHZ = 50
)(
    input wire SYS_CLK,
    input wire SYS_RSTN,
    output wire [2:0] led
);
    wire core_clk;
    wire clock_locked;
    generate
        if (CORE_CLOCK_MHZ == 100 || CORE_CLOCK_MHZ == 150) begin : gen_core_clock
            wire feedback, clock_unbuffered;
            // AA28 remains 50 MHz: 1000/10 = 100 MHz or 900/6 = 150 MHz.
            MMCME2_ADV #(
                .BANDWIDTH("OPTIMIZED"), .COMPENSATION("INTERNAL"),
                .CLKIN1_PERIOD(20.0), .DIVCLK_DIVIDE(1),
                .CLKFBOUT_MULT_F(CORE_CLOCK_MHZ == 150 ? 18.0 : 20.0),
                .CLKOUT0_DIVIDE_F(CORE_CLOCK_MHZ == 150 ? 6.0 : 10.0)
            ) mmcm (
                .CLKIN1(SYS_CLK), .CLKIN2(1'b0), .CLKINSEL(1'b1),
                .CLKFBIN(feedback), .CLKFBOUT(feedback),
                .CLKOUT0(clock_unbuffered), .LOCKED(clock_locked),
                .PWRDWN(1'b0), .RST(!SYS_RSTN),
                .DADDR(7'd0), .DCLK(1'b0), .DEN(1'b0), .DI(16'd0), .DWE(1'b0),
                .PSCLK(1'b0), .PSEN(1'b0), .PSINCDEC(1'b0)
            );
            BUFG clock_buffer (.I(clock_unbuffered), .O(core_clk));
        end else begin : gen_direct_clock
            assign core_clk = SYS_CLK;
            assign clock_locked = 1'b1;
        end
    endgenerate
    wire reset_n = SYS_RSTN && clock_locked;
    // Xilinx FF INIT values hold reset after configuration even without a
    // physical reset pulse. Asynchronous assertion, synchronized release.
    reg [7:0] poweron_count = 0;
    (* ASYNC_REG = "TRUE" *) reg [1:0] reset_pipe = 2'b11;
    always @(posedge core_clk) begin
        if (!(&poweron_count)) poweron_count <= poweron_count + 1'b1;
    end
    always @(posedge core_clk or negedge reset_n) begin
        if (!reset_n) reset_pipe <= 2'b11;
        else reset_pipe <= {reset_pipe[0], !(&poweron_count)};
    end
    wire rst = reset_pipe[1];
    reg [25:0] heartbeat;
    reg start;
    reg passed;
    reg failed;
    reg [31:0] completed_runs;
    wire busy, done, core_failed;
    wire [63:0] scores;

    tiny_tpu_xor_selftest #(.WATCHDOG_CYCLES(WATCHDOG_CYCLES)) core (
        .clk(core_clk), .rst(rst), .start(start), .busy(busy),
        .done(done), .failed(core_failed), .scores(scores)
    );
    always @(posedge core_clk) begin
        if (rst) begin
            heartbeat <= 0;
            start <= 0;
            passed <= 0;
            failed <= 0;
            completed_runs <= 0;
        end else begin
            heartbeat <= heartbeat + 1'b1;
            start <= 0;
            if (done) begin
                if (core_failed !== 1'b0 || scores !== 64'hffff_00fe_00fe_ffff) begin
                    failed <= 1;
                    passed <= 0;
                end else if (!failed) begin
                    passed <= 1;
                    completed_runs <= completed_runs + 1'b1;
                end
            end else if (!busy && !start && !failed) start <= 1;
        end
    end
    // P30 red: heartbeat; M30 green: pass; N30 yellow: sticky failure.
    assign led = {failed, passed && !failed, heartbeat[25]} ^ {3{LED_ACTIVE_LOW != 0}};
endmodule
`default_nettype wire
