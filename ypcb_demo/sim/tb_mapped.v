`timescale 1ns/1ps
`default_nettype none
// Observe only package-level pins, so this also works after hierarchy removal.
module tb_mapped;
    reg clk = 0;
    always #10 clk = ~clk;
    reg reset_n = 1;
    wire [2:0] led;
    ypcb_tiny_tpu_top dut (.SYS_CLK(clk), .SYS_RSTN(reset_n), .led(led));
    task wait_pass;
        integer guard;
        begin
            guard = 0;
            while (led[1] !== 1'b1 && guard < 2500) begin
                @(negedge clk);
                guard = guard + 1;
            end
            if (led[2:1] !== 2'b01) $fatal(1, "Mapped TPU did not pass: %b", led);
        end
    endtask
    initial begin
        wait_pass;
        repeat (2000) begin
            @(negedge clk);
            if (led[2:1] !== 2'b01) $fatal(1, "Mapped TPU lost PASS: %b", led);
        end
        reset_n = 0;
        repeat (5) @(negedge clk);
        if (led[2:1] !== 0) $fatal(1, "Mapped reset failed");
        reset_n = 1;
        wait_pass;
        $display("YPCB MAPPED REGRESSION PASS");
        $finish;
    end
    initial begin
        #200000;
        $fatal(1, "Mapped simulation timeout");
    end
endmodule
`default_nettype wire
