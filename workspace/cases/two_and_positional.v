module two_and_pos(
input wire a,
input wire b,
input wire c,
output wire y
);
  wire t1;
  and2 u1 (a, b, t1);
  and2 u2 (t1, c, y);
endmodule
