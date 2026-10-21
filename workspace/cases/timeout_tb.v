`timescale 1ns/1ns
module timeout_tb;
    reg clk = 0;
    always #1 clk = ~clk;

    initial begin
        $dumpfile("timeout.vcd");
        $dumpvars(0, timeout_tb);
        #100000000 $finish;
    end
endmodule
