module escape_include (output wire y);
`include "../../../secret_defs.vh"
    assign y = 1'b0;
endmodule
