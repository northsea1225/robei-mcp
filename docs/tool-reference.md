# 工具参考

38 个工具的完整参数、返回值和真实调用示例。工具名、参数名、返回字段保持英文。

> 这份文件是从早期 README 拆出来的，示例里的时间戳/路径是当时的真实输出，不要当成当前默认值。

## 工具清单

### `runtime_status`

返回：

- `mcp_version`
- Python 版本和路径
- 工作区、模型根目录
- Robei、iverilog、vvp、yosys、verilator 的路径和存在性
- `verilator_available`（可执行文件 + `VERILATOR_ROOT` 都在位）
- `tools.verilator.version`（`5.036`）

Robei 版本从 PE 文件版本资源读取。Yosys 这个厂商构建的 `-V` 输出 `Synthesis complete`，因此 `version` 为 `null`，但路径和综合检查可用。

### `lint_verilog`

```json
{"files": ["counter4/counter4.v"], "top": "counter4", "timeout_s": 30}
```

分别执行三路检查：

1. `iverilog -Wall -g2012 ...`（语法 + 警告，包含 `warning_count` / `warnings`）
2. `yosys -p "read_verilog ...; synth -top counter4"`（可综合性）
3. `verilator_bin --lint-only -Wall -Wno-fatal --top-module ...`（`verilator` 段）

另外执行一次 `yosys_check`：`read_verilog → hierarchy -check → proc → check → write_json → opt_clean → write_json`，
对比前后网表，**被 `opt_clean` 删掉且不是端口** 的 net 名就是未用信号，写入 `unused_signals`（含模块名和 `src` 位置）。

`ok` 需要 iverilog、Yosys synth、Verilator 三路都通过。Verilator 不存在时该段返回
`skipped: true` + `skip_reason`，不会让工具失败。`-Wno-fatal` 让告警不改变退出码，
告警从输出里的 `%Warning-CODE:` 解析。

样例（`cases/warning_width_unused.v`）：

```json
{"ok": true,
 "verilator": {"warning_count": 1, "warnings": [{"code": "Warning-SELRANGE"}]},
 "unused_signals": [{"module": "warning_width_unused", "name": "unused_signal",
                     "src": "...warning_width_unused.v:5.10-5.23"}]}
```

### `simulate`

```json
{"files": ["counter4/counter4.v", "counter4/counter4_tb.v"], "top": "counter4_tb", "timeout_s": 30}
```

编译前先做静态路径扫描（见「安全边界」）；被拒绝时返回 `stage: "security_scan"`，
不会启动 `iverilog`。通过后执行 `iverilog` 编译和 `vvp` 运行，
返回 `$display`、VCD 绝对/相对路径。超时会终止子进程并返回 `simulation.timeout=true`。

### `simulate_project`

```json
{"proj_path": "counter4_native/counter4_native.proj",
 "test_name": "tb_counter4.test", "render": true}
```

**无 GUI、无桌面**地把一个 Robei 实验跑起来：读 `test/<name>.test` 的 `Code`，
包成 `` `timescale … module <name>(); … endmodule ``（并补 `$dumpfile`/`$dumpvars`），
把 `model/*.model` 全部导出到 `build/headless/<工程>-<test>/deps/`，再交给
`iverilog` + `vvp` 跑。

- 输出结构与 `simulate` 相同（`compile` / `simulation` / `display_output` / `vcd_paths`），
  另外多个 `headless` 段记录生成物路径
- `render=true` 顺带把 VCD 画成 PNG
- 适用场景：桌面锁屏、SSH 里没开 Session 1、或者 UIA 桥已经塌了

### `read_waveform`

```json
{"vcd_path": "build/sim-.../counter4.vcd", "signals": ["counter4_tb.dut.count"], "time": 45}
```

不传 `signals` 时列信号；传 `signals` 时按原始 VCD 时间刻度取值。返回值含 `timescale`、`max_time`、`effective_time`、二进制 `value` 和可解析时的 `value_int`。

### `compare_waveforms`

```json
{"golden_vcd": "build/sim-.../tb.vcd", "dut_vcd": "build/golden/tb.vcd"}
```

两份 VCD 逐信号比对，比较点在**两边跳变时间的并集**上，所以"一边完全没跳"也能被抓到。
返回每个信号的跳变次数、差异条数、首个差异（时间/两侧值），以及总和
`ok / mismatch_total / differing_signals`。

### `read_robei_model`

```json
{"path": "C:\\ProgramData\\Robei\\mux.model"}
```

只读解析 `.model` 的模块名、类型、参数、端口、`parameter_list` 和 `Code`。
画布级模型另外返回 `instances`（嵌套 `<Module Type="model">`：实例名 / `Class` / `File` /
坐标 / 实例端口）、`wires`（`net` / `from` / `to`，端点为 `parse_endpoint` 拆好的
`scope` / `instance` / `port`）、`netlist`（`nets` 网名→端点表 + `dangling_instance_ports`），
以及 `is_canvas`。仍然不写回。
非 `Port` / `<Wire>` / 嵌套 `<Module>` 的子节点照旧原样保留在 `other_children`。

### `robei_doctor`

```json
{"deep": false}
```

自检整条链路，每项返回 `{name, ok, required, detail, hint}`，`ok` 只由
`required` 项决定（Verilator、GUI 任务、matplotlib 是可选件）。

`deep=true` 时会在 `workspace/build/doctor/` 编译并运行一个两行 testbench，
只有拿到 `DOCTOR_OK` 才算通过——用来区分"路径对"和"真的能跑"。

### `list_projects`

```json
{"limit": 200}
```

列出 workspace 里所有 `.proj`（含递归子目录）：工程名、`model/` 下的模型、`test/` 下的
test 模型、`verilog/` 下的依赖、`build/` 里已有的 VCD，以及 `.proj` 里声明的条目数。
只读，用来回答「我现在有哪些工程、哪个已经跑过」。

### `read_robei_project`

```json
{"proj_path": "robei_samples/_synthetic/sample.proj"}
```

只读解析 `.proj`：

- `folders`：`model` / `test` / `const` / `verilog` 四个目录的文件清单（名字、大小、SHA256）
- `models`：`model/` 下每个 `.model` 的完整解析结果
- 每个模型都带 `instances` / `wires` / `netlist`（画布级工程直接得到实例与连线），
  因此不用额外调用 `read_robei_model` 就能看清层次
- `unknown_nodes` / `node_tree`：**未知节点原样保留**（tag / attributes / text / children），不推断结构
- `sha256_before` / `sha256_after` / `unchanged`：证明没有改写

允许根目录：`workspace`、`C:\ProgramData\Robei`、`E:\Robei\project`（只读）。

### `export_robei_verilog`

```json
{"model_path": "C:\\ProgramData\\Robei\\mux.model",
 "out_name": "mux_len1.v",
 "parameter_overrides": {"LEN": 1}}
```

把 `Parameters` + `Port` + `Code` 拼成完整 `module ... endmodule`，写入
`workspace/build/export/<name>.v`，随后自动跑 `lint_verilog`。

- 只读输入，只写工作区；目标已存在则 `FileExistsError`
- `parameter_overrides` 用于修正 `mux.model` 的 `LEN = 0`（直接导出会得到非法的 `[-1:0]`）
- 返回 `model_sha256_before/after`，证明 `.model` 未被改动

### `sync_robei_verilog`

```json
{"proj_path": "counter4_native/counter4_native.proj", "overwrite": true}
```

把工程 `model/` 下每个 `.model` 的 `Parameters + Port + Code` 导成
`<工程>/verilog/<model>.v`（模块名与文件名一致）。这是 Robei 仿真能解析被实例化
子模块的前提——它的编译命令只把当前 test 模型写进 `filelist.vf`，其余模块靠
`-y <工程>/verilog` 目录查找。

- 只写工程自己的 `verilog/` 目录（工程必须位于 workspace 内）
- 返回每个文件的 `sha256`、大小与出错列表

### `create_robei_test`

```json
{"proj_path": "counter4_native/counter4_native.proj",
 "name": "tb_counter4",
 "code": "reg clk; reg rst_n; wire [3:0] count;\ncounter4 dut (...);\n..."}
```

生成 `test/<name>.test` 并登记进 `.proj` 的 `<test>` 目录。

- `code` 只写 **module body**：Robei 自己补 `module <name>();`、`endmodule` 和
  `$dumpfile("./build/<name>.vcd"); $dumpvars;`
- 默认 `sync_deps=true`，顺带刷新 `verilog/` 依赖库
- 目标已存在时 `FileExistsError`（`overwrite=true` 才覆盖，并留 `.bak-<时间戳>`）
- 登记是幂等的：返回 `registered_in_project`（布尔）与 `registration`（`created` / `exists`）

### `create_robei_canvas_model`

```json
{"proj_path": "canvas_stage16/canvas_stage16.proj",
 "name": "top_canvas",
 "ports": [{"name": "a", "direction": "input"},
           {"name": "b", "direction": "input"},
           {"name": "c", "direction": "input"},
           {"name": "y", "direction": "output"}],
 "instances": [{"name": "u1", "model": "and2"},
               {"name": "u2", "model": "and2"}],
 "wires": [{"net": "t1", "from": "u1.y", "to": "u2.a"},
           {"net": "w_a", "from": "a", "to": "u1.a"},
           {"net": "w_b", "from": "b", "to": "u1.b"},
           {"net": "w_c", "from": "c", "to": "u2.b"},
           {"net": "w_y", "from": "u2.y", "to": "y"}]}
```

写画布级 `.model`：实例写成嵌套 `<Module Type="model">`，连线写成 `<Wire>`，端点接受
`u1.y` / `y` 这种简写（内部转成 `top#u1>y` / `top>y`）。

- 实例的 `Class` 写**被引用模型的模型名**（Robei 自己的写法），`File` 指向
  `Current/<模型>.model`；实例端口从被引用模型自动读取，不需要手填
- 校验：实例名 / 模型名合法、被引用模型存在于同工程 `model/`、连线端点存在
- 只写 workspace 内的工程；目标已存在要 `overwrite=true`（并留 `.bak-<时间戳>`）
- 返回写回的 `sha256`、`instances` / `wires` / `netlist`（可直接检查有没有悬空端口）
- Robei 打开后 F2 Compile 会自己把画布转成 Verilog 实例化

### `generate_testbench`

```json
{"verilog_path": "counter4/counter4.v", "top": "counter4",
 "out_path": "testbench/counter4_stage4_tb.v"}
```

解析端口后生成骨架：时钟（`clk`/`clock`/`*_clk`）、复位（`rst`/`rst_n`/`reset_n` 等，
`_n` 结尾判定低有效）、`$dumpfile`/`$dumpvars`、`$display`、实例化、TODO 注释。

- 默认写到 `workspace/testbench/<top>_tb.v`，已存在则拒绝覆盖
- 生成模块名固定为 `<top>_tb`
- 多时钟：所有 `clk`/`clock`/`*_clk` 输入都会生成 `always` 翻转
- 默认：其余输入先置 0，复位释放后把第一个非时钟/复位输入拉高，并留 TODO 提示
- `full_combo=true`：穷举所有自由输入的组合（`2^bits` 行，`bits > max_combo_bits` 直接拒绝），
  每步 `$display("combo a=%b b=%b y=%b", ...)`，适合组合逻辑直接出真值表

### `create_robei_const`

```json
{"proj_path": "const_demo/const_demo.proj", "name": "light_constrain", "model": "light",
 "pins": {"swt": ["G15", "P15", "W13", "T16"], "led": ["M14", "M15", "G14", "D18"]}}
```

写 Robei 的引脚约束画布：

- `pins` 的每个端口都必须给全，数量必须等于端口位宽（4 位端口就要 4 个引脚）
- 引脚名按 `[A-Za-z][A-Za-z0-9_]*` 校验（Xilinx 的 `G15`/`M14` 这类）
- 文件写到 `<工程>/const/<name>.const`，并登记进 `.proj` 的 `<const>` 目录
- `kind` 默认 **`const`**（Robei 5.x 的 FPGA 流程认这个；传 `constrain` 会被 Build→Synthesis
  拒绝为 `is not constrain file`）
- `iostandard`（默认 `LVCMOS33`）写进每个引脚 `<Port Function="...">`
- `vendor` / `device` / `family` 只在 `kind="const"` 时写入根节点；留空时 Robei 会提示
  `has no Device/Family configured`（综合仍会跑完），填成目标板卡的器件名即可消掉
- 已存在要 `overwrite=true`（留 `.bak-<时间戳>`）

### `read_robei_const`

```json
{"path": "const_demo/const/light_constrain.const"}
```

返回 `kind`、`instance`、`model`、`pins`（`{端口: {位: 引脚}}`）、`ports`、`wires` 和 SHA256。只读。

### `export_robei_xdc`

```json
{"proj_path": "const_demo/const_demo.proj", "iostandard": "LVCMOS33"}
```

把 `.const` 投影成 `<工程>/constrain/<名字>.xdc`：

```
#This file is generated by Robei!
#Pin Assignment for Xilinx FPGA with Software Vivado.
set_property PACKAGE_PIN G15 [get_ports swt[0]]
set_property IOSTANDARD LVCMOS33 [get_ports swt[0]]
```

端口顺序按 `.const` 里的声明顺序；`PACKAGE_PIN` 行与 Robei 自己生成的 `build/<name>.xdc`
逐行相同（头部注释与空 `IOSTANDARD` 两处例外）。

`also_build=true` 同时覆盖 `<工程>/build/<name>.xdc`——那是 `Build→Route` 实际解析的副本，
Robei 自己写的版本 `IOSTANDARD` 为空，Route 会因此报参数个数错误。

### `robei_gui_save_project`

```json
{}
```

点 File → Save。Build → Synthesis 之前必须做一次，否则 Robei 只回
`Error: please save your current project first.`。

### `robei_gui_compile_all` 的新字段

每个模型的结果现在固定带 `state`（`ok` / `failed` / `unknown_state`）、`marker`
（实际命中的完成标记）和 `output_tail`（Output 尾部，失败时用来定位），
`unknown_state` 会自动重试一次并标 `retried: true`。

### `robei_gui_synthesis`

```json
{"timeout_s": 240}
```

点 Build → Synthesis 并等到 `Synthesis complete`（内部是 yosys 的 Xilinx 流程）。
返回 `completed`、`errors`、`board_warnings`、`output_tail`、`delta`。

- `board_warnings` 单独列出 `has no Device/Family configured`，不算失败
- 前提：当前打开的是 `.const`（`Type="const"`），工程里有 `build/` 目录

### `check_connectivity`

```json
{"files": ["cases/and2.v", "cases/erc_bad.v"], "top": "erc_bad"}
```

静态 ERC（不跑仿真）：

- `multi_driver`：一个网络有两个以上驱动（实例输出或 `assign` 左值）
- `undriven_net`：被读但没有任何驱动（顶层输入算驱动）
- `width_mismatch`：连接网络的位宽 ≠ 实例端口位宽
- `unknown_module`：实例化了一个不在本次源码里的模块
- `unconnected_port`：被引用模块的端口没出现在实例连接里

返回 `counts`（按规则计数）、`issues`（逐条带模块/实例/网络）。常量与拼接表达式跳过不判。

### `import_verilog_canvas`

```json
{"proj_path": "p1_demo/p1_demo.proj", "verilog_path": "cases/two_and.v"}
```

把结构化的 Verilog 模块写成画布级 `.model`：

- 实例 → 嵌套 `<Module Type="model">`，被引用模块必须已存在于 `<工程>/model/<模块>.model`
- 端口连接 → `<Wire>`；同一网络超过两个端点时以第一个为基准发多条同名 wire
- `assign` / `always` **不会**被画布承载，原样列在 `not_imported` 里；常量 / 拼接连接列在
  `skipped_connections`
- 目标 `.model` 已存在要 `overwrite=true`

### `truth_table`

```json
{"verilog_path": "cases/and2.v", "top": "and2", "max_inputs": 8}
```

穷举输入（`2^n` 行，`n ≤ max_inputs`）生成 testbench → `simulate` → 解析 `TT` 行，
返回 `rows`（每行是 `{输入: 二进制串, 输出: 二进制串}`）、`expected_rows`、`row_count`。
`ok` 要求仿真成功且行数等于 `2^n`。

### `expect_waveform`

```json
{"vcd_path": "build/sim-.../tb.vcd",
 "checks": [{"signal": "y", "value": "1", "at": 25},
            {"signal": "y", "value": "0", "from": 0, "to": 10}]}
```

- `at`：按原始 VCD 时间刻度取该点值
- `from` + `to`：值在 `from` 等于期望，且 `(from, to]` 区间内没有跳变
- 信号名重复（多个作用域同名）只把这一条标成 error 并给出候选全名，不影响其他检查项

### `waveform_stats`

```json
{"vcd_path": "build/sim-.../counter4.vcd"}
```

每个信号的跳变次数、首次/末次跳变、不同取值数、是否见过 0/1/X/Z；汇总
`coverage`（单比特信号里 0/1 都出现过的比例）与 `clock`（最规整的高频信号，
`period` = 两次边沿间隔的两倍，附 `frequency_mhz`）。纯启发式，不做时序签核。

### `find_events`

### `find_events`

```json
{"vcd_path": "build/sim-.../counter4_tb.vcd",
 "signal": "counter4_tb.dut.count", "condition": "== 4'b1111", "limit": 3}
```

条件语法只有 `== != > >= < <=` 加字面量（`4'b1111`、`8'hFF`、`15`），
**不使用 `eval`**；非法条件抛 `ConditionError`。返回每次「进入条件」的时刻。

### `render_waveform`

```json
{"vcd_path": "build/sim-.../counter4_tb.vcd",
 "signals": ["counter4_tb.clk", "counter4_tb.dut.count"],
 "out_name": "counter4.png"}
```

用 venv 内的 matplotlib（Agg 后端）逐信号阶梯图，写入
`workspace/build/waveforms/`，返回路径和字节数。已存在则拒绝覆盖；`x`/`z` 记为 NaN。

## 错误码、进度与取消

失败返回统一长这样，`code` 稳定、`error` 给人看、`hint` 是下一步建议：

```json
{"ok": false, "code": "not_found",
 "error": "FileNotFoundError: file not found in workspace: C:\\...\\missing.v",
 "hint": "the path must exist and, for models/projects, sit under an allowed root"}
```

| `code` | 场景 |
|---|---|
| `unsafe_xml` | 文档里出现 `<!ENTITY>` 或带内部子集的 `<!DOCTYPE>` |
| `not_found` | 文件/工程/模型/端口不存在 |
| `already_exists` | 拒绝覆盖（`overwrite=true` 可覆盖并留 `.bak`） |
| `invalid_argument` | 名字非法、连线端点不存在、路径逃逸、参数越界 |
| `cancelled` | 客户端取消，子进程已被 kill |
| `timeout` | 超过 `timeout_s` 或 `max_timeout_s` |
| `compile_failed` / `run_failed` | iverilog 编译失败 / vvp 运行失败（看 `compile` / `simulation` 段） |
| `path_rejected` | `simulate` 的安全扫描在编译前拒绝了 `include` / `$fopen` 等越界路径 |

**进度**：`simulate` / `simulate_project` / `lint_verilog` 在阶段边界上报
`notifications/progress`（`total` 是阶段数，不是时间估计）。客户端不订阅就静默无副作用。

**取消**：长任务跑在可放弃的工作线程里，`RunToken` 记录这次调用启动的子进程
（`iverilog` / `vvp` / `yosys` / `verilator`）。客户端发 `notifications/cancelled` 后，
服务端置位 token 并 kill 这些进程，线程随即解出；实测从取消到 `vvp.exe` 消失 < 2 s，
服务端仍继续响应后续请求。

`robei_gui_*` 走的是计划任务桥，不是这批子进程：取消会让调用立刻返回，但桥任务会按它
自己的超时跑完（结果丢弃）。已知副作用：被取消的调用可能留下半个 `workspace/build/sim-*/` 目录。

