// Portable controller derived from xor_demo/fpga/de1_soc_tiny_tpu_xor_top.v.
// Executes the existing two-layer Q8.8 XOR network on the actual TPU RTL.
`timescale 1ns/1ps
`default_nettype none
module tiny_tpu_xor_selftest #(
    parameter integer WATCHDOG_CYCLES = 1024
)(
    input wire clk,
    input wire rst,
    input wire start,
    output wire busy,
    output reg done,
    output reg failed,
    output reg [63:0] scores
);
    localparam [3:0] ST_IDLE      = 4'd0;
    localparam [3:0] ST_TPU_RESET = 4'd1;
    localparam [3:0] ST_LOAD      = 4'd2;
    localparam [3:0] ST_CMD_W1    = 4'd3;
    localparam [3:0] ST_GAP_W1    = 4'd4;
    localparam [3:0] ST_CMD_X     = 4'd5;
    localparam [3:0] ST_SWITCH_1  = 4'd6;
    localparam [3:0] ST_CMD_B1    = 4'd7;
    localparam [3:0] ST_WAIT_1    = 4'd8;
    localparam [3:0] ST_CMD_W2    = 4'd9;
    localparam [3:0] ST_GAP_W2    = 4'd10;
    localparam [3:0] ST_CMD_H1    = 4'd11;
    localparam [3:0] ST_SWITCH_2  = 4'd12;
    localparam [3:0] ST_CMD_B2    = 4'd13;
    localparam [3:0] ST_WAIT_2    = 4'd14;

    reg [3:0] state;
    reg [3:0] load_step;
    reg [15:0] watchdog;
    reg [3:0] stage1_count_1, stage1_count_2, stage2_count;
    reg seen_valid;
    // Drive asynchronous TPU reset from a register. A decoded multi-bit FSM
    // state could glitch during a transition and reset the datapath on silicon.
    reg run_reset;
    wire tpu_reset = rst || run_reset;
    assign busy = (state != ST_IDLE);

    function [15:0] expected_score;
        input [3:0] index;
        begin
            case (index)
                0, 3: expected_score = 16'hffff;
                1, 2: expected_score = 16'h00fe;
                default: expected_score = 16'hdead;
            endcase
        end
    endfunction
    reg [15:0] ub_wr_host_data_in_0;
    reg [15:0] ub_wr_host_data_in_1;
    reg ub_wr_host_valid_in_0;
    reg ub_wr_host_valid_in_1;
    reg ub_rd_start_in;
    reg ub_rd_transpose;
    reg [8:0] ub_ptr_select;
    reg [15:0] ub_rd_addr_in;
    reg [15:0] ub_rd_row_size;
    reg [15:0] ub_rd_col_size;
    reg [3:0] vpu_data_pathway;
    reg sys_switch_in;

    wire [15:0] sys_data_out_21;
    wire [15:0] sys_data_out_22;
    wire sys_valid_out_21;
    wire sys_valid_out_22;
    wire [15:0] vpu_data_out_1;
    wire [15:0] vpu_data_out_2;
    wire vpu_valid_out_1;
    wire vpu_valid_out_2;
    wire [15:0] ub_rd_input_data_out_0;
    wire [15:0] ub_rd_input_data_out_1;
    wire ub_rd_input_valid_out_0;
    wire ub_rd_input_valid_out_1;
    wire [15:0] ub_rd_weight_data_out_0;
    wire [15:0] ub_rd_weight_data_out_1;
    wire ub_rd_weight_valid_out_0;
    wire ub_rd_weight_valid_out_1;
    wire [15:0] ub_rd_bias_data_out_0;
    wire [15:0] ub_rd_bias_data_out_1;
    wire [15:0] ub_rd_Y_data_out_0;
    wire [15:0] ub_rd_Y_data_out_1;
    wire [15:0] ub_rd_H_data_out_0;
    wire [15:0] ub_rd_H_data_out_1;
    wire [15:0] ub_rd_col_size_out;
    wire ub_rd_col_size_valid_out;

    tpu dut (
        .clk(clk),
        .rst(tpu_reset),
        .ub_wr_host_data_in_0(ub_wr_host_data_in_0),
        .ub_wr_host_data_in_1(ub_wr_host_data_in_1),
        .ub_wr_host_valid_in_0(ub_wr_host_valid_in_0),
        .ub_wr_host_valid_in_1(ub_wr_host_valid_in_1),
        .ub_rd_start_in(ub_rd_start_in),
        .ub_rd_transpose(ub_rd_transpose),
        .ub_ptr_select(ub_ptr_select),
        .ub_rd_addr_in(ub_rd_addr_in),
        .ub_rd_row_size(ub_rd_row_size),
        .ub_rd_col_size(ub_rd_col_size),
        .learning_rate_in(16'h00C0),
        .vpu_data_pathway(vpu_data_pathway),
        .sys_switch_in(sys_switch_in),
        .vpu_leak_factor_in(16'h0003),
        .inv_batch_size_times_two_in(16'h0080),
        .sys_data_out_21(sys_data_out_21),
        .sys_data_out_22(sys_data_out_22),
        .sys_valid_out_21(sys_valid_out_21),
        .sys_valid_out_22(sys_valid_out_22),
        .vpu_data_out_1(vpu_data_out_1),
        .vpu_data_out_2(vpu_data_out_2),
        .vpu_valid_out_1(vpu_valid_out_1),
        .vpu_valid_out_2(vpu_valid_out_2),
        .ub_rd_input_data_out_0(ub_rd_input_data_out_0),
        .ub_rd_input_data_out_1(ub_rd_input_data_out_1),
        .ub_rd_input_valid_out_0(ub_rd_input_valid_out_0),
        .ub_rd_input_valid_out_1(ub_rd_input_valid_out_1),
        .ub_rd_weight_data_out_0(ub_rd_weight_data_out_0),
        .ub_rd_weight_data_out_1(ub_rd_weight_data_out_1),
        .ub_rd_weight_valid_out_0(ub_rd_weight_valid_out_0),
        .ub_rd_weight_valid_out_1(ub_rd_weight_valid_out_1),
        .ub_rd_bias_data_out_0(ub_rd_bias_data_out_0),
        .ub_rd_bias_data_out_1(ub_rd_bias_data_out_1),
        .ub_rd_Y_data_out_0(ub_rd_Y_data_out_0),
        .ub_rd_Y_data_out_1(ub_rd_Y_data_out_1),
        .ub_rd_H_data_out_0(ub_rd_H_data_out_0),
        .ub_rd_H_data_out_1(ub_rd_H_data_out_1),
        .ub_rd_col_size_out(ub_rd_col_size_out),
        .ub_rd_col_size_valid_out(ub_rd_col_size_valid_out)
    );

    always @(*) begin
        ub_wr_host_data_in_0 = 16'h0000;
        ub_wr_host_data_in_1 = 16'h0000;
        ub_wr_host_valid_in_0 = 1'b0;
        ub_wr_host_valid_in_1 = 1'b0;
        ub_rd_start_in = 1'b0;
        ub_rd_transpose = 1'b0;
        ub_ptr_select = 9'd0;
        ub_rd_addr_in = 16'd0;
        ub_rd_row_size = 16'd0;
        ub_rd_col_size = 16'd0;
        vpu_data_pathway = 4'b0000;
        sys_switch_in = 1'b0;

        case (state)
            ST_LOAD: begin
                case (load_step)
                    4'd0: begin
                        ub_wr_host_data_in_0 = 16'h0000;
                        ub_wr_host_valid_in_0 = 1'b1;
                    end
                    4'd1, 4'd2, 4'd3, 4'd4: begin
                        ub_wr_host_data_in_0 = (load_step == 2 || load_step == 3) ? 16'h0100 : 16'h0000;
                        ub_wr_host_data_in_1 = (load_step == 2 || load_step == 4) ? 16'h0100 : 16'h0000;
                        ub_wr_host_valid_in_0 = 1'b1;
                        ub_wr_host_valid_in_1 = 1'b1;
                    end
                    4'd5, 4'd6, 4'd7: begin
                        ub_wr_host_data_in_0 = (load_step == 7) ? 16'h0000 : 16'h0100;
                        ub_wr_host_valid_in_0 = 1'b1;
                    end
                    4'd8: begin
                        ub_wr_host_data_in_0 = 16'h00E2;
                        ub_wr_host_valid_in_0 = 1'b1;
                    end
                    4'd9: begin
                        ub_wr_host_data_in_0 = 16'hFEEF;
                        ub_wr_host_valid_in_0 = 1'b1;
                        ub_wr_host_data_in_1 = 16'hFF1E;
                        ub_wr_host_valid_in_1 = 1'b1;
                    end
                    4'd10: begin
                        ub_wr_host_data_in_0 = 16'h0040;
                        ub_wr_host_valid_in_0 = 1'b1;
                        ub_wr_host_data_in_1 = 16'h0111;
                        ub_wr_host_valid_in_1 = 1'b1;
                    end
                    4'd11: begin
                        ub_wr_host_data_in_0 = 16'h0126;
                        ub_wr_host_valid_in_0 = 1'b1;
                        ub_wr_host_data_in_1 = 16'h0000;
                        ub_wr_host_valid_in_1 = 1'b1;
                    end
                    4'd12: begin
                        ub_wr_host_data_in_0 = 16'hFFB6;
                        ub_wr_host_valid_in_0 = 1'b1;
                        ub_wr_host_data_in_1 = 16'h0137;
                        ub_wr_host_valid_in_1 = 1'b1;
                    end
                    default: begin
                    end
                endcase
            end
            ST_CMD_W1: begin
                ub_rd_start_in = 1'b1;
                ub_rd_transpose = 1'b1;
                ub_ptr_select = 9'd1;
                ub_rd_addr_in = 16'd12;
                ub_rd_row_size = 16'd2;
                ub_rd_col_size = 16'd2;
            end
            ST_CMD_X: begin
                ub_rd_start_in = 1'b1;
                ub_ptr_select = 9'd0;
                ub_rd_addr_in = 16'd0;
                ub_rd_row_size = 16'd4;
                ub_rd_col_size = 16'd2;
                vpu_data_pathway = 4'b1100;
            end
            ST_SWITCH_1: begin
                vpu_data_pathway = 4'b1100;
                sys_switch_in = 1'b1;
            end
            ST_CMD_B1: begin
                ub_rd_start_in = 1'b1;
                ub_ptr_select = 9'd2;
                ub_rd_addr_in = 16'd16;
                ub_rd_row_size = 16'd4;
                ub_rd_col_size = 16'd2;
                vpu_data_pathway = 4'b1100;
            end
            ST_WAIT_1: begin
                vpu_data_pathway = 4'b1100;
            end
            ST_CMD_W2: begin
                ub_rd_start_in = 1'b1;
                ub_rd_transpose = 1'b1;
                ub_ptr_select = 9'd1;
                ub_rd_addr_in = 16'd18;
                ub_rd_row_size = 16'd1;
                ub_rd_col_size = 16'd2;
                vpu_data_pathway = 4'b1100;
            end
            ST_GAP_W2: begin
                vpu_data_pathway = 4'b1100;
            end
            ST_CMD_H1: begin
                ub_rd_start_in = 1'b1;
                ub_ptr_select = 9'd0;
                ub_rd_addr_in = 16'd21;
                ub_rd_row_size = 16'd4;
                ub_rd_col_size = 16'd2;
                vpu_data_pathway = 4'b1100;
            end
            ST_SWITCH_2: begin
                vpu_data_pathway = 4'b1100;
                sys_switch_in = 1'b1;
            end
            ST_CMD_B2: begin
                ub_rd_start_in = 1'b1;
                ub_ptr_select = 9'd2;
                ub_rd_addr_in = 16'd20;
                ub_rd_row_size = 16'd4;
                ub_rd_col_size = 16'd1;
                vpu_data_pathway = 4'b1100;
            end
            ST_WAIT_2: begin
                vpu_data_pathway = 4'b1100;
            end
            default: begin
            end
        endcase
    end

    // The watchdog covers the complete invocation, including missing valids.
    // Exact counts and raw score equality are required before publishing PASS.
    always @(posedge clk) begin
        if (rst) begin
            run_reset <= 1;
            state <= ST_IDLE;
            load_step <= 0;
            watchdog <= 0;
            stage1_count_1 <= 0;
            stage1_count_2 <= 0;
            stage2_count <= 0;
            seen_valid <= 0;
            done <= 0;
            failed <= 0;
            scores <= 0;
        end else begin
            run_reset <= (state == ST_IDLE && start);
            done <= 0;
            if (busy) watchdog <= watchdog + 1'b1;
            if (busy && watchdog >= WATCHDOG_CYCLES - 1) begin
                state <= ST_IDLE;
                done <= 1;
                failed <= 1;
            end else begin
                case (state)
                    ST_IDLE: if (start) begin
                        state <= ST_TPU_RESET;
                        load_step <= 0;
                        watchdog <= 0;
                        stage1_count_1 <= 0;
                        stage1_count_2 <= 0;
                        stage2_count <= 0;
                        seen_valid <= 0;
                        failed <= 0;
                        scores <= 0;
                    end
                    ST_TPU_RESET: state <= ST_LOAD;
                    ST_LOAD: if (load_step == 12) begin
                        load_step <= 0;
                        state <= ST_CMD_W1;
                    end else load_step <= load_step + 1'b1;
                    ST_CMD_W1: state <= ST_GAP_W1;
                    ST_GAP_W1: state <= ST_CMD_X;
                    ST_CMD_X: state <= ST_SWITCH_1;
                    ST_SWITCH_1: state <= ST_CMD_B1;
                    ST_CMD_B1: state <= ST_WAIT_1;
                    ST_WAIT_1: begin
                        if (vpu_valid_out_1) begin
                            if (stage1_count_1 < 4) stage1_count_1 <= stage1_count_1 + 1'b1;
                            else failed <= 1;
                        end
                        if (vpu_valid_out_2) begin
                            if (stage1_count_2 < 4) stage1_count_2 <= stage1_count_2 + 1'b1;
                            else failed <= 1;
                        end
                        if (vpu_valid_out_1 || vpu_valid_out_2) seen_valid <= 1;
                        else if (seen_valid) begin
                            seen_valid <= 0;
                            if (stage1_count_1 !== 4 || stage1_count_2 !== 4) failed <= 1;
                            state <= ST_CMD_W2;
                        end
                    end
                    ST_CMD_W2: state <= ST_GAP_W2;
                    ST_GAP_W2: state <= ST_CMD_H1;
                    ST_CMD_H1: state <= ST_SWITCH_2;
                    ST_SWITCH_2: state <= ST_CMD_B2;
                    ST_CMD_B2: state <= ST_WAIT_2;
                    ST_WAIT_2: begin
                        if (vpu_valid_out_1) begin
                            seen_valid <= 1;
                            if (stage2_count < 4) begin
                                scores[16*stage2_count +: 16] <= vpu_data_out_1;
                                stage2_count <= stage2_count + 1'b1;
                                if (vpu_data_out_1 !== expected_score(stage2_count)) failed <= 1;
                            end else failed <= 1;
                        end
                        if (vpu_valid_out_2) failed <= 1;
                        if (!vpu_valid_out_1 && !vpu_valid_out_2 && seen_valid) begin
                            if (stage2_count !== 4) failed <= 1;
                            done <= 1;
                            state <= ST_IDLE;
                        end
                    end
                    default: begin
                        failed <= 1;
                        done <= 1;
                        state <= ST_IDLE;
                    end
                endcase
            end
        end
    end
endmodule
`default_nettype wire
