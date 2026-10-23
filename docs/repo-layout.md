# 仓库结构

```text
C:\dev\robei-mcp\
  .venv\                         Python 3.12.10，仅安装 mcp 及其依赖
  .gitignore
  .mcp.json                      Windows Claude Code 项目配置
  README.md
  pyproject.toml                 0.10.0，requires-python>=3.10，可选 dev=pytest
  robei.toml.example             路径/限额配置样例（复制成 robei.toml 生效）
  requirements.txt               mcp==2.3.0, matplotlib
  run-mcp.cmd                    Windows stdio 包装脚本
  examples\
    codex-windows.toml
    codex-mac-remote.toml
    claude-windows.mcp.json
    claude-mac-remote.mcp.json
  server.py                      MCP stdio 入口
  robei_mcp\
    config.py                    环境变量 + robei.toml，逐键记录来源
    errors.py                    稳定错误码（code / hint / stage）
    xmlsafe.py                   XML 防护：拒 <!ENTITY> 与 DOCTYPE 内部子集
    runstate.py                  RunToken：取消时 kill 已注册子进程
    model.py                     .model 解析（含画布 instances/wires/netlist）+ 通用节点树 + Verilog 导出
    verilog_ast.py               结构级 Verilog 读取（端口/线网/实例/assign）
    erc.py                       连接性规则：多驱动、未驱动、位宽、未知模块、未连接端口
    p1.py                        Verilog→画布、ERC、真值表、波形断言与统计
    constraints.py               .const 写入/读取 + .xdc 投影
    security.py                  路径策略 + simulate 前静态路径扫描
    server_app.py                MCP 工具注册
    tools.py                     工具实现
    oplog.py                     workspace/logs 下的操作日志
    vcd.py                       VCD 解析、事件查询、PNG 渲染
    verilog.py                   端口解析 + testbench 生成
    doctor.py                    环境自检（python/venv/包/路径/任务/深度仿真）
  tools\
    setup.py                     一键 bootstrap + 自检（--check / --apply）
    gui\                          Session 1 侧 UIA 驱动与 schtasks 桥
      archive\                    65 个一次性探针（仅作证据，不再维护）
  tests\
    stage2_smoke.py              正常路径冒烟
    stage3_cases.py              警告、语法错误、超时、残留进程
    stage4_cases.py              Verilator/未用信号、工程解析、导出、TB、波形、路径逃逸
    stage16_canvas.py            画布级 model：生成 → 解析 → Robei 编译 → 真值表
    stage17_hardening.py         annotations、错误码、XML 防护、进度、取消、robei.toml
    stage18_p1.py                Verilog→画布、ERC、真值表、波形断言/统计、combo TB
    stage19_constraints.py       引脚约束：写 .const、读回、投影 .xdc、Robei 打开
    stage20_gui_robustness.py    标记/静止双条件、unknown_state 重试、compile_all 字段
  workspace\
    counter4\
      counter4.v                 4 位计数器
      counter4_tb.v              testbench
    cases\
      warning_width_unused.v     越界位选 + 未用信号
      warning_portbind.v         悬空输入
      syntax_error.v             语法错误
      timeout_tb.v               长仿真超时
      escape_include.v           路径逃逸负向用例
      escape_fopen.v             路径逃逸负向用例
      escape_readmemh.v          路径逃逸负向用例
      inside_paths.v             路径逃逸正向对照
      and2.v / two_and.v         结构级样例（P1 导入/ERC 用）
      two_and_positional.v       位置连接的实例化样例
      erc_bad.v                  ERC 负向样例（多驱动 + 未驱动）
    testbench\                    generate_testbench 输出目录
    robei_samples\                层次化样例（_synthetic 为容错自测样例）
    robei_counter4_project\      Robei 手动闭环测试工程
```

说明：

- `robei_mcp/gui_agent/` 是 0.11.1 起 GUI 驱动的正式位置（`ui` / `driver` / `job` / `launcher` / `paths`）。
  `tools/gui/` 下同名文件只是向后兼容的 shim，给已注册的计划任务用。
- `tools/gui/` 里只剩还会用到的脚本：`register_task.py`（注册助手任务）、`probe_push.py`（交互会话里推送）、
  `probe_desktop.py` / `probe_input.py`（输入管道诊断）、`prune.py`；其余一次性探针已在 1.0.0 移除。
- `workspace/` 下的 `build/`、`logs/`、`gui_shots/`、`gui_agent/`、`reports/` 都在 `.gitignore` 里。
