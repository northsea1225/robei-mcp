module inside_paths;
    integer fd;
    initial begin
        fd = $fopen("inside_ok.txt", "w");
        $fdisplay(fd, "ok");
        $fclose(fd);
        $display("inside paths accepted");
        $finish;
    end
endmodule
