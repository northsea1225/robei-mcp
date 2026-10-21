module warn_child(input wire a, input wire b, output wire y);
    assign y = a & b;
endmodule

module warning_portbind(input wire a, output wire y);
    warn_child u(.a(a), .y(y));
endmodule
