module seq100111_detector(input clk, input reset, input din, output reg detected);

  // State encoding
  parameter S0 = 3'd0;
  parameter S1 = 3'd1;
  parameter S2 = 3'd2;
  parameter S3 = 3'd3;
  parameter S4 = 3'd4;
  parameter S5 = 3'd5;
  parameter S6 = 3'd6;

  reg [2:0] state, next_state;

  always @(posedge clk or posedge reset) begin
    if (reset)
      state <= S0;
    else
      state <= next_state;
  end

  always @(*) begin
    detected = 0;
    case (state)
      S0: begin
        if (din == 1'b0) next_state = S0;
        if (din == 1'b1) next_state = S1;
      end
      S1: begin
        if (din == 1'b0) next_state = S2;
        if (din == 1'b1) next_state = S1;
      end
      S2: begin
        if (din == 1'b0) next_state = S3;
        if (din == 1'b1) next_state = S1;
      end
      S3: begin
        if (din == 1'b0) next_state = S0;
        if (din == 1'b1) next_state = S4;
      end
      S4: begin
        if (din == 1'b0) next_state = S2;
        if (din == 1'b1) next_state = S5;
      end
      S5: begin
        if (din == 1'b0) next_state = S2;
        if (din == 1'b1) next_state = S6;
      end
      S6: begin
        detected = 1;
        if (din == 1'b0) next_state = S2;
        if (din == 1'b1) next_state = S1;
      end
      default: next_state = S0;
    endcase
  end
endmodule