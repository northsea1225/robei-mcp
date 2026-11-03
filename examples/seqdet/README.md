# 100111 序列检测器验收夹具

来源：[oniondas/Automation-SeqDetector-Verilog](https://github.com/oniondas/Automation-SeqDetector-Verilog/tree/4b448c563560087e3a8e2b3376f458f9a54ff588)，固定提交 `4b448c563560087e3a8e2b3376f458f9a54ff588`。
Copyright (c) 2025 Shounak Das，MIT；完整许可证保存在 `upstream/LICENSE`，原始 RTL 和 SHA256 保存在 `upstream/`、`PROVENANCE.json`。

`seq100111_detector.v` 是派生副本，仅在组合块开头加入 `next_state = S0`。
原程序虽有 `case default`，但合法状态下 `din=X/Z` 时两个独立 `if` 都不赋值，仍会锁存上次的 `next_state`。
副本约定：X/Z 输入使下一时钟边沿回到 S0；输出仍是当前状态 S6 的 Moore 输出，不承诺异步立即清零。

测试见 `tests/test_1_11.py`：重叠序列、X/Z 后恢复、原版负对照、Yosys `proc` 锁存器计数、上游哈希。
测试必须使用工作区副本；不编译或修改 `upstream/` 本体。这里的仿真语义不是 FPGA 板级验证。
