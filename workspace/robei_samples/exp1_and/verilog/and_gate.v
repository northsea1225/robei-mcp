/*Experiment 1: 2-input AND gate*/

module and_gate(
output wire y,
input wire b,
input wire a
);

  //----Code starts here: integrated by Robei-----
  assign y = a & b;
endmodule    //and_gate
