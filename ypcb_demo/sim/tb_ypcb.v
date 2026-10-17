`timescale 1ns/1ps
`default_nettype none
module tb_ypcb;
    reg clk = 0;
    always #10 clk = ~clk;
    reg reset_n = 1;
    wire [2:0] led_high, led_low, led_timeout;
    ypcb_tiny_tpu_top dut (.SYS_CLK(clk), .SYS_RSTN(reset_n), .led(led_high));
    ypcb_tiny_tpu_top #(.LED_ACTIVE_LOW(1)) inverted (
        .SYS_CLK(clk), .SYS_RSTN(reset_n), .led(led_low));
    ypcb_tiny_tpu_top #(.WATCHDOG_CYCLES(8)) timed_out (
        .SYS_CLK(clk), .SYS_RSTN(reset_n), .led(led_timeout));

    task wait_runs;
        input integer count;
        integer guard;
        begin
            guard = 0;
            while ((dut.completed_runs >= count) !== 1'b1 && guard < 2500) begin
                @(negedge clk);
                guard = guard + 1;
                if (led_high[2] !== 0) $fatal(1, "Unexpected failure LED");
                if (led_low !== ~led_high) $fatal(1, "LED polarity mismatch");
            end
            if (guard == 2500) $fatal(1, "TPU completion timeout");
            if (dut.core.scores !== 64'hffff_00fe_00fe_ffff)
                $fatal(1, "Incorrect raw scores: %h", dut.core.scores);
            if (led_high[1] !== 1) $fatal(1, "PASS LED missing");
        end
    endtask

    initial begin
        // Prove FPGA-style power-on reset without an external reset pulse.
        wait_runs(3);
        if (led_timeout[2:1] !== 2'b10) $fatal(1, "Watchdog did not latch failure");
        $display("PASS: startup, repeated real TPU XOR, exact scores, LED polarity, watchdog");

        // A missing output stream must time out, rather than leave PASS set.
        force dut.core.vpu_valid_out_1 = 0;
        force dut.core.vpu_valid_out_2 = 0;
        repeat (1200) @(negedge clk);
        if (led_high[2:1] !== 2'b10) $fatal(1, "Missing-valid fault did not fail");
        release dut.core.vpu_valid_out_1;
        release dut.core.vpu_valid_out_2;
        repeat (50) @(negedge clk);
        if (led_high[2:1] !== 2'b10) $fatal(1, "Failure was not sticky");

        // Reset restores execution. Drive away from the active sampling edge.
        reset_n = 0;
        repeat (5) @(negedge clk);
        if (led_high[2:1] !== 0) $fatal(1, "Reset did not clear status");
        reset_n = 1;
        wait_runs(2);

        // Numerical corruption must fail the hardware comparison.
        wait (dut.core.state == 14 && dut.core.vpu_valid_out_1);
        @(negedge clk);
        force dut.core.vpu_data_out_1 = 16'h1234;
        repeat (8) @(negedge clk);
        release dut.core.vpu_data_out_1;
        repeat (100) @(negedge clk);
        if (led_high[2:1] !== 2'b10) $fatal(1, "Corrupted score did not fail");
        reset_n = 0;
        repeat (5) @(negedge clk);
        reset_n = 1;
        wait_runs(1);
        wait (dut.core.state == 14 && dut.core.vpu_valid_out_1);
        @(negedge clk);
        force dut.core.vpu_data_out_1 = 16'hxxxx;
        repeat (8) @(negedge clk);
        release dut.core.vpu_data_out_1;
        repeat (100) @(negedge clk);
        if (led_high[2:1] !== 2'b10) $fatal(1, "Unknown score did not fail");
        $display("PASS: missing-valid and corrupt-score faults, sticky failure, reset recovery");
        $display("PASS: four-state unknown score rejected");
        $display("YPCB RTL REGRESSION PASS");
        $finish;
    end
    initial begin
        #200000;
        $fatal(1, "Global simulation timeout");
    end
endmodule
`default_nettype wire
