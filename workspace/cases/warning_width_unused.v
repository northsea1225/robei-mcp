module warning_width_unused (
    input wire [7:0] a,
    output wire y
);
    wire unused_signal;
    assign y = a[8];
endmodule
