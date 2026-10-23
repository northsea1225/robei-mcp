module erc_bad(
input wire a,
input wire b,
output wire y,
output wire z
);
  wire shared;
  and2 u1 (.a(a), .b(b), .y(shared));
  and2 u2 (.a(a), .b(b), .y(shared));
  and2 u3 (.a(shared), .b(ghost), .y(y));
endmodule
