---
name: Bug 报告 / Bug report
about: 工具行为与文档不符、报错或结果不对
title: "[bug] "
labels: bug
---

**涉及的工具 / Tool**
例如 `simulate`、`create_testbench`

**版本 / Version**
`runtime_status` 返回里的版本与 `git_commit`；操作系统；Python 版本；`iverilog -V` / `yosys -V`

**复现步骤 / Steps to reproduce**
给出最小输入（调用参数和用到的 `.v` / `.model` 片段）。

**期望结果 / Expected**

**实际结果 / Actual**
贴返回的 JSON（`ok`、`code`、`stage`、`hint`）。请先去掉本机用户名等隐私信息。

**其他 / Notes**
是否涉及 GUI 工具（仅 Windows）？是否改过 `robei.toml` 或环境变量？
