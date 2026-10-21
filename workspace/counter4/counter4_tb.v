`timescale 1ns/1ns

module counter4_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg en = 1;
    wire [3:0] count;

    counter4 dut (
        .clk(clk),
        .rst_n(rst_n),
        .en(en),
        .count(count)
    );

    always #5 clk = ~clk;

    initial begin
        $dumpfile("counter4.vcd");
        $dumpvars(0, counter4_tb);

        #12 rst_n = 1;
        #38 $display("MID t=%0t count=%0d", $time, count);
        #10 en = 0;
        #10 $display("FINAL t=%0t count=%0d", $time, count);
        $finish;
    end
endmodule
