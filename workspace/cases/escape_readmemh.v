module escape_readmemh;
    reg [7:0] mem [0:1];
    initial begin
        $readmemh("../../../outside.hex", mem);
        $display("mem0=%h", mem[0]);
        $finish;
    end
endmodule
