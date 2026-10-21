module escape_fopen;
    integer fd;
    initial begin
        fd = $fopen("C:/Windows/Temp/robei_escape_test.txt", "w");
        $fclose(fd);
        $display("opened outside");
        $finish;
    end
endmodule
