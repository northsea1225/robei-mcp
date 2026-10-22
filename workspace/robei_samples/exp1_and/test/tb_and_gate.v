`timescale 1ns/1ps
// Experiment 1 testbench: walk the four input combinations of a 2-input AND gate.
module tb_and_gate;
  reg a;
  reg b;
  wire y;

  and_gate dut (.a(a), .b(b), .y(y));

  initial begin
    $dumpfile("and_gate.vcd");
    $dumpvars(0, tb_and_gate);

    a = 1'b0; b = 1'b0; #10;
    $display("t=%0t a=%b b=%b y=%b", $time, a, b, y);
    a = 1'b0; b = 1'b1; #10;
    $display("t=%0t a=%b b=%b y=%b", $time, a, b, y);
    a = 1'b1; b = 1'b0; #10;
    $display("t=%0t a=%b b=%b y=%b", $time, a, b, y);
    a = 1'b1; b = 1'b1; #10;
    $display("t=%0t a=%b b=%b y=%b", $time, a, b, y);

    $finish;
  end
endmodule
