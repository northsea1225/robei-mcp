module two_and(
input wire a,
input wire b,
input wire c,
output wire y
);
  wire t1;
  and2 u1 (.a(a), .b(b), .y(t1));
  and2 u2 (.a(t1), .b(c), .y(y));
endmodule
