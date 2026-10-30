# 工具参考

1.7.0 共 51 个工具（文件级 40 + GUI 11）；ROBEI_MCP_GUI=0 时为 40 个。
工具名、参数名、返回字段保持英文。

> 这份文件是从早期 README 拆出来的，示例里的时间戳/路径是当时的真实输出，不要当成当前默认值。

**路径约定**：所有文件参数都相对 `workspace/` 解析。开头的 `workspace/`（大小写不敏感，
`/` 和 `\` 都算）一律去掉，所以 `counter4/counter4.v`、`workspace/counter4/counter4.v`
和 `Workspace\counter4\counter4.v` 等价；去掉前缀后仍然做逃逸检查
（`workspace/../x` 直接被拒）。若工作区里确实存在 `workspace/` 子目录且与根目录同名路径
同时存在，返回 `ambiguous path`。绝对路径必须落在 workspace / 模型根目录之内。

## 工具清单

### `runtime_status`

返回：

- `server_version`（本包版本，如 `1.0.2`）、`mcp_version`（MCP Python SDK 的版本，如 `2.3.0`）——
  两个不是一回事，查版本请看前者
- Python 版本和路径
- 工作区、模型根目录
- Robei、iverilog、vvp、yosys、verilator 的路径和存在性
- `verilator_available`（可执行文件 + `VERILATOR_ROOT` 都在位）
- `tools.verilator.version`（`5.036`）
- `security.strict_path_scan`（当前严格路径扫描开关）
- 自检块（1.5）：
  - `package_version`（= `server_version`，两个名字都给，老客户端不用改）
  - `git_commit`（源码目录的 HEAD 短 hash；不是 git 仓库或取不到时为 `null`，不报错）
  - `source_dir`（**正在运行**的 `robei_mcp` 目录，用来确认服务加载的是哪份代码）
  - `process_started_at` / `process_age_s`（进程加载本模块的时间）
  - `newest_source_mtime`（`robei_mcp/*.py` 的最大 mtime）
  - `stale` / `stale_warning`：源码比进程新时为 `true`，`stale_warning` 说明"要重启服务或重开
    Codex"。`stale=true` 时**每个工具**的返回都会多一条 `warnings`（错误结果也带），不需要
    额外轮询
- 生命周期块（H1）：`path`、`pid`、`current_run_id`、`recent_events`（最近 5 条）、`previous_exit`；日志不可读时返回 `available=false`，不影响其它运行态字段。
- `recent_events` 中 `stdin_closed` 表示客户端断开：正常关闭与 SSH 断开不做服务端来源区分，health 统一显示“客户端断开”。外层 cmd 与 Python 同时被强杀且没有 `exit` 时，health 显示“未观察到终止”。
- `tools/mcp_health.sh` 是 Mac 侧只读检查。命令行 server 标记认 `robei_mcp/server`、`robei_mcp\server`、
  `robei_mcp.server`，大小写不敏感；身份满足以下任一规则才算 `alive`：
  1. server 的 `ExecutablePath` 在 `C:\dev\robei-mcp\.venv`；
  2. 父进程是 `.venv\Scripts\python.exe` 且命令行含 server 标记；
  3. `start.sys_prefix` 指向 `.venv` 且命令行含 server 标记。
  PID 复用或身份不匹配显示 `stale_pid`，不会仅凭 PID 存活判定 `alive`。正常退出与 SSH 断开
  都是 `stdin_closed`（health 统一“客户端断开”）；外层 cmd 与 Python 同时被强杀且没有 `exit`
  时显示“未观察到终止”。

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

返回值顶层的 `strict` / `rejected` / `unresolved` 与 `security.*` 内容一致（成功时
`rejected` 为空数组），客户端不必再往下钻一层取。

严格模式（默认）下，`security.rejected` 非空即拒绝，包括：

- 字面量路径逃出工作区
- `` `include `` 链里的文件（最多递归 8 层）带出的越界字面量
- 无法静态求值的路径参数，例如 `$readmemh(PATH, mem)`、`$fopen(name)`

放宽时设 `ROBEI_MCP_STRICT_PATHS=0`，第 3 类只记进 `security.unresolved`。
返回值里 `security.strict` 会标出本次用的是哪一档。

### `simulate_project`

```json
{"proj_path": "counter4_native/counter4_native.proj",
 "test_name": "tb_counter4.test", "render": true}
```

**无 GUI、无桌面**地把一个 Robei 实验跑起来：读 `test/<name>.test` 的 `Code`，
包成 `` `timescale … module <name>(); … endmodule ``（并补 `$dumpfile` 和 `$dumpvars(0, <name>)`），
把 `model/*.model` 全部导出到本次独立的
`<工程>/build/headless-<工程>-<test>-<时间戳>-<uuid8>/deps/`，再交给 `iverilog` + `vvp` 跑
（同一工程连跑两次不会互相覆盖）。1.3 起 testbench、依赖、`sim-*` 运行目录和 VCD 全部落在
**工程目录**的 `build/` 下，不再堆到 `workspace/build/`。

- 输出结构与 `simulate` 相同（`compile` / `simulation` / `display_output` / `vcd_paths`），
  另外多个 `headless` 段记录生成物路径
- `render=true` 顺带把 VCD 画成 PNG（写到 `<工程>/build/waveforms/`）
- `render=true, inline=true` 额外返回 `ImageContent`（见 `render_waveform` 的上限）
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

`ok=true` 的判据是"能比、且比完没有差异"，以下任一情况都是 `ok=false` 并在 `reasons`
里给出原因（不会静默放过）：

- 缺少信号（golden 有、dut 没有，或反之）
- 没有任何共同信号（信号名/作用域完全不同）
- 同名信号位宽不一致（记进 `incompatible_signals`）
- `max_time` 不一致（一份仿真提前结束）
- timescale 解析不出来
- `signals=[...]` 里点名的信号两边都找不到（返回 `unknown_signals`，不再抛 `KeyError`）

两份文件的 timescale 不同但都可解析时，会按 fs 归一到共同时间轴比较，并返回
`timescale_fs` / `timescale_comparable`；`max_time_matches` 和 `compared_signal_count`
便于确认到底比了多少。**`max_time` 也先换算成 fs 再比**：`1ns` 的 `#20` 与 `1ps` 的
`#20000` 视为同一时刻，返回 `max_time_fs: {golden, dut}`。

`allow_extra_signals=true` 时只比两边共有的信号，单边多出来的内部信号记进
`missing_in_dut` / `missing_in_golden` 但不判失败；默认仍是严格模式。

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
{"limit": 200, "include_qa": false}
```

列出 workspace 里所有 `.proj`（含递归子目录）：工程名、`model/` 下的模型、`test/` 下的
test 模型、`verilog/` 下的依赖、`build/` 里已有的 VCD，以及 `.proj` 里声明的条目数。
只读，用来回答「我现在有哪些工程、哪个已经跑过」。默认跳过 `workspace/_qa_runs` 与
`workspace/qa_*` 这些 QA 临时树；要连它们一起列就传 `include_qa=true`。

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
- `warnings`：根级或实例 `Parameters` 解析失败、或实例写了子模型未声明的参数时逐条列出；
  解析失败的 chunk 不会变成 `parameter` / `#(...)`，能解析的照常输出
  （`sync_robei_verilog` 也会汇总同样的 `warnings`）
- `Parameters` 里的 `//` 注释按行剥离（字符串里的 `//` 不算），注释挂回它所在行的参数；
  导出时注释写在参数行的逗号之后，不会吞掉参数之间的分隔符
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

### `list_library_models`

```json
{"filter": "counter", "include_lint": true}
```

只读扫描 Robei 自带的 ToolBox（默认 `C:\ProgramData\Robei`；可用 `ROBEI_MCP_LIBRARY`
指向别的目录，`;` 分隔多个）。

- 每个模型返回 `name`（文件名）、`module_name`（文件里的 `<Module Name>`）、
  `name_matches_module`、`type` / `class` / `category`、`ports`、`parameters`、`instances`、`size_bytes`
- `constant.model` 里声明的是 `module register`，`mod_n_counter.model` 里是 `ring_counter`，
  这两个 `name_matches_module=false`——导入时得用 `as_name`
- `lint` 是真编译：把模型导出成 Verilog，再 `iverilog -g2012 -y <library root>` 编一遍，
  返回 `ok` / `returncode` / `first_error` / `stderr`；`include_lint=false` 跳过（快很多）
- `filter` 对文件名、模块名、Class、Category 做子串匹配；`limit` 默认 200
- 库目录只读，不写任何东西

### `import_library_model`

```json
{"name": "counter", "project": "myproj/myproj.proj"}
{"name": "constant", "project": "myproj/myproj.proj", "as_name": "register"}
```

把库模型复制到 `<project>/model/<name>.model`，备份并登记 `.proj`，再对副本 lint 一次。

- 目标已存在且 `overwrite=false` → `code=already_exists`；`overwrite=true` 留 `.bak-<时间戳>`
- `.proj` 在登记前也备份（返回 `proj_backup`）
- **文件名和模块名冲突**：`constant.model` 声明的是 `register`，直接导入会被拒绝并提示
  `pass as_name='register'`；给了 `as_name` 之后文件改名的同时把根节点 `Name` 也改写
  （`renamed_module` 记录 from/to），保证 Robei 看到的文件名和模块名一致
- `name` 先按文件名匹配，找不到再按模块名匹配
- `dry_run=true` 只校验并返回计划，不落盘
- 返回 `source` / `target` / `relative_path` / `registration` / `backup` / `proj_backup` /
  `sha256` / `ports` / `parameters` / `lint`
- 工程参数同时接受 `project` 和 `proj_path`（1.2.2 加，和 `read_robei_project` 等工具
  同名参数对齐）。两个都不给、或者名字写错，返回的 hint 会点出正确参数名
  （`path_escape` 之类的通用文案不再兜底），例如
  `pass the project as project="myproj/myproj.proj" or proj_path="myproj/myproj.proj"`

### `create_robei_project`

```json
{"name": "lab2/exp1", "models": ["inv"]}
{"name": "lab2/exp1", "skeleton": false}
{"name": "exp1", "models": []}
```

建骨架：`model/`、`test/`、`const/`、`verilog/` 四个文件夹 + `<leaf>.proj`。

- `name` 可以带目录（`lab2/exp1`）：目录落在 `workspace/lab2/exp1/`，而 `.proj` 文件名、
  XML 根标签和 `name` 属性只用最后一段 `exp1`（Robei 的工程树按这个名字走）。
  每段都必须匹配 `[A-Za-z_][A-Za-z0-9_]*`；`..`、绝对路径、反斜杠、空段
  （`a//b`、`a/b/`）一律 `invalid_argument`。
- `models=None` 且 `skeleton=true`（默认）时照旧建一个与项目同名的空白模型；
  `skeleton=false` 或 `models=[]` 时一个空白模型都不建，`.proj` 里
  `<model name="model" parent="<leaf>" type="folder"/>` 是自闭合节点、不留空行。
- 返回 `project`（最后一段）、`project_path`（`lab2/exp1`）、`root`、`proj`、`models`、
  `relative_root`、`skeleton`。
- 路径解析完还会再做一次 workspace 越界检查。

### `create_robei_model`

```json
{"proj_path": "bus8/bus8.proj", "name": "bus8",
 "ports": [{"name": "a", "direction": "input", "width": "LEN"},
           {"name": "y", "direction": "output", "width": 1}],
 "parameters": {"LEN": 4},
 "code": "assign y = a[0];"}
```

在工程里建一个行为级 `.model`（根参数 + 端口 + Code），并登记进 `.proj`。

- `parameters` 写进模型自己的 `<Module Parameters>`：对象按 key 排序、用 `&#xa;` 拼接；
  字符串写法（`"LEN = 4; // width"`）原样保留。校验与实例覆盖同源（非法名、值里带 `;`/>
  换行、重复、`#(...)`/`.LEN(...)`/`parameter` 一律拒绝，hint 指向 `{"LEN": 4}`）
- 端口 `width` 接受整数、数字字符串或参数表达式：
  - `4` / `"4"` → `Datasize="4"`
  - `"LEN"` → `Datasize="LEN-1:0"`（Robei 5.0 自己就这么存：24 个自带模型里 35 处参数化
    宽度全是范围写法，没有一处裸 `LEN`）
  - `"LEN-1:0"` 原样写入
  - 表达式只允许 标识符 / 整数 / `+ - * /` / 括号；`2*LEN` → `2*LEN-1:0`（`*` 优先级高于
    `-`，不加括号）。`<< >> ? : & | ~ %` 等没有 Robei 样本，直接拒绝并提示改用 `"MSB:LSB"`
- 端口宽度里出现的标识符必须在本模型 `parameters` 里声明，否则报错（hint 给出
  `parameters={"LEN": 4}`）
- 返回值：`parameters` / `parameters_text` / `ports`（`width` 是 int，参数化时为 null；
  `datasize` 数字是 int、表达式是字符串）/ `warnings`
- 新建模型声明的参数，别的画布实例可以直接覆盖（`{"LEN": 8}` → `#(.LEN(8))`）

### `create_robei_test`

```json
{"proj_path": "counter4_native/counter4_native.proj",
 "name": "tb_counter4",
 "code": "reg clk; reg rst_n; wire [3:0] count;\ncounter4 dut (...);\n..."}
```

生成 `test/<name>.test` 并登记进 `.proj` 的 `<test>` 目录。

- `code` 只写 **module body**：Robei 自己补 `module <name>();`、`endmodule` 和
  `$dumpfile("./build/<name>.vcd"); $dumpvars(0, <name>);`（1.2.2 起只 dump testbench
  自己的作用域，不再是裸 `$dumpvars`——这样 iverilog 不会再报
  `Package ($unit) is not dumpable`）
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
- 实例参数写对象：`"parameters": {"LEN": 8}`。落盘规范成 `LEN = 8;`，多个参数按 key 排序、
  换行拼接（XML 属性里是 `&#xa;`，不能写字面换行）。Robei 保存时原样保留，编译时生成
  `#(.LEN(8))`；多参数实测（`gui_probe` 之后新增的 `LEN = 8;&#xa;N = 3;`）同样保留
  （`D:\Temp\robei_samples\gui_probe\pp_p1` 是单参数样本）
  - 也接受 Robei 自己写的 `"LEN = 8;"` 字符串（含行尾 `//` 注释），原样写回
  - 拒收：子模型没声明的参数名、非法参数名、值里带 `;` 或换行、同一参数写两次
  - 拒收三种 Verilog 写法并提示改用对象：`#(.LEN(8))`、`.LEN(8)`、`parameter LEN = 8;`
    （Robei 会静默丢弃前两种、把第三种写成坏的 `.parameterLEN`）
  - 参数解析不出来时仍按原文写入，返回 `warnings` 且导出不生成 `#(...)`（与 1.0.4 一致）
- 位选和切片写在 `wire` 端点上：`{"from": "a[0]", "to": "u1.x"}`、
  `{"from": "data_in[7:0]", "to": "u1.d"}`。落盘成实例端口的 `Connect="a[0]"`，**不生成
  `<Wire>`**（这正是 Robei 自己的存法，生成的 Verilog 是 `.x(a[0])`）
  - 父端口必须存在；索引不能越界；切片宽度必须等于实例端口宽度，否则 `invalid_argument`
  - 父端口宽度是参数表达式（`LEN-1:0`）时无法静态校验，返回 warning 后照常写入
  - 只有父端口一侧能带位选；两端都带位选、或位选落在实例端口一侧，都报错
  - **`<Wire>` 的端点里带 `[` 一律拒绝**：Robei 保存时会静默删除这种线，端口变悬空
    `.x()`；官方样本 `gui_probe/pp_b2` 就是这个反例，`pp_p1` 是正确写法
- 每条 `wire` 可以带 `datasize`；导出 Verilog 时按它生成 `wire [N-1:0]`，丢失会变成 1 位
- 校验：实例名 / 模型名合法、被引用模型存在于同工程 `model/`、连线端点存在
- 只写 workspace 内的工程；目标已存在要 `overwrite=true`（并留 `.bak-<时间戳>`）
- 返回写回的 `sha256`、`instances` / `wires` / `netlist`（可直接检查有没有悬空端口）
- Robei 打开后 F2 Compile 会自己把画布转成 Verilog 实例化

### `edit_robei_canvas`

```json
{"model_path": "chain/model/chain.model",
 "ops": [{"op": "add_instance", "name": "u1", "model": "buf4"},
         {"op": "add_instance", "name": "u2", "model": "buf4"},
         {"op": "connect", "from": "a", "to": "u1.a", "net": "a_n"},
         {"op": "connect", "from": "u1.y", "to": "u2.a", "net": "mid", "datasize": 4},
         {"op": "connect", "from": "u2.y", "to": "y", "net": "y_n"}]}
```

改画布模型，一次调用里的操作**原子执行**（全部校验通过才落盘）：

| op | 参数 | 说明 |
|---|---|---|
| `add_instance` | `name`, `model`, 可选 `parameters` / `x` / `y` | 实例名必须唯一，被引用模型要在同一工程的 `model/` 下 |
| `remove_instance` | `name` | 连带删掉接在该实例上的 wire |
| `add_port` | `name`, `direction`, `width`, 可选 `side` / `datatype` | 顶层端口；`direction` 是 input/output/inout，`side` 默认左进右出 |
| `remove_port` | `name` | 连带删掉接在这个端口上的 wire，`wires_dropped` 是条数 |
| `connect` | `from`, `to`, 可选 `net` / `datasize` | 端点写 `u1.a` 或顶层端口 `a`；两端位宽必须一致。端点带位选（`a[0]`、`a[7:4]`）时改写成实例端口的 `Connect`，不建 wire |
| `disconnect` | `net` / `from` / `to` 至少一个 | 匹配不到任何线就报错；`from`/`to` 命中某个实例端口的 `Connect` 时一并清空（`connects_cleared`） |
| `set_parameters` | `name`, `parameters`（对象或 `"A = 1;"` 字符串） | 重写该实例的 `Parameters`（`LEN=8` / 多参数都走同一套校验），`""` 表示清空 |
| `rename_wire` | `net`（或 `from`）+ `new_name` | 新名字不能和已有 net 撞 |

- 位宽按 Robei 的语义算：`Datasize="4"` 是 4 位、`"3:0"` 是 4 位；`"LEN-1:0"` 这种
  解析不出来的表达式要求两端字面量一致。不一致返回 `code=width_mismatch`，消息里带两边位宽
- `connect` 的位选（`a[0]` / `a[7:0]`）与 `create_robei_canvas_model` 同规则：父端口越界、
  切片宽度与实例端口不等、两端都带位选都报错；参数化宽度只给 warning
- `set_parameters` 是对实例参数的最小改法（本 op 不新增独立工具；1.7.0 工具数为 51 / 40）：子模型没
  声明的名字直接报错，名字大小写按子模型的声明改写（`len` → `LEN`）
- `diff` 里除了 wire，还会报 `connects_added` / `connects_changed` / `connects_removed` /
  `parameters_changed`，位选和参数改动不会"看起来没动"
- `dry_run=true` 时同样会校验并算出 `verilog_preview`
- 端点不存在 / 实例重名 / 未知 op → `invalid_argument`
- 返回 `diff`（增删的实例与 wire）、`ports`、`instances`、`wires`、`netlist`、`verilog_preview`
- 写盘前留 `.bak-<时间戳>`；`dry_run=true` 只返回预览
- 旧式 `<Instance Name="u1" Module="and2"/>` 也能读：`instances[].module` 就是那个字段，
  没有 `<Port>` 子节点时端口从子模型补齐

### `set_robei_code`

```json
{"model_path": "myproj/model/and2.model", "code": "assign y = a & b;"}
```

只改行为级模型的 `Code`。带实例或 wire 的结构级模型直接拒绝（`code=not_behavioral`），
避免把画布写成一半代码一半连线。返回 `code` / `code_lines` / `verilog_preview` /
`sha256_before` / `sha256`；写盘前留备份，`dry_run=true` 不落盘。

### `set_robei_parameter`

```json
{"model_path": "myproj/model/sub.model", "name": "LEN", "value": 8, "action": "set", "dry_run": true}
```

编辑模型自身的根参数。`action` 默认是 `set`，保持旧调用兼容；可选动作如下：

- `set`：改已有参数默认值；整数或引用本模型已声明参数的白名单表达式均可用。
- `add`：新增参数；随后可用 `set_robei_port(width="LEN")` 建立参数化 Datasize。
- `rename`：改名并按标识符 token 同步本模型端口/wire 的 Datasize 与参数表达式。
- `delete`：删除未被本模型 Datasize、其它参数表达式或父实例覆盖引用的参数。

`rename_in_parents=true` 只对 `rename` 有效：扫描同工程的直接父模型，不递归祖父模型；
dry-run 返回完整 `parent_updates` 但不写盘，提交时每个文件先留 `.bak`，事务失败会回滚。
参数名必须是合法 Verilog 标识符，不能是关键字、端口名或已有参数名；错误 hint 会给
`difflib` 候选。

lab16 示例：

```text
set_robei_parameter(action="add", name="LEN", value=4)
set_robei_port(name="a", direction="input", width="LEN")
set_robei_parameter(name="LEN", value=8, dry_run=true)
# affected_ports[0].effective_width == 8
# warnings: top.u1.a / parent_width_conflict / overridden=false
edit_robei_canvas([{"op":"set_parameters", "instance":"u1", "parameters":{"LEN":4}}])
set_robei_parameter(action="rename", name="LEN", new_name="WIDTH",
                    rename_in_parents=true, dry_run=true)
# 先检查 parent_updates，再提交 rename
```

返回 `old_value` / `new_value` / `parameters` / `parameters_text`、`affected_ports`（含
`effective_width`）、`warnings`、`parents_skipped` 和 `parent_updates`；写盘前留备份。

### `set_robei_port`

```json
{"model_path": "proj/model/bus.model", "name": "a", "direction": "input", "width": "LEN"}
```

给一个 `.model` 加/改一个 `<Port>`（写盘前校验 XML，留 `.bak-<时间戳>`）。`width` 与
`create_robei_model` 同一套规则：整数、数字字符串或参数表达式。返回 `port.width`（int，
参数化时为 null）和 `port.datasize`（数字 int / 表达式字符串）。

### `generate_testbench`

```json
{"verilog_path": "counter4/counter4.v", "top": "counter4",
 "out_path": "testbench/counter4_stage4_tb.v"}
```

解析端口后生成骨架：时钟（`clk`/`clock`/`*_clk`）、复位（`rst`/`rst_n`/`reset_n` 等，
`_n` 结尾判定低有效）、`$dumpfile`/`$dumpvars`、`$display`、实例化、TODO 注释。

- 端口解析支持 ANSI 简写继承（`input wire [3:0] a, b, c`）、一行写的 non-ANSI
  （`module m(a,y); input a; output y;`），以及同一行里的多个实例
- 默认写到 `workspace/testbench/<top>_tb.v`，已存在则拒绝覆盖
- 端口位宽必须是能算出整数的常量表达式；`[WIDTH-1:0]` 这种**不会代入参数默认值**，
  而是直接报错（先把参数展开成具体位宽再生成）
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
- 引脚 `<Port>` 同时写出空的 `Drive` / `Pull` / `Voltage` 属性（Robei 的字段），
  由 `export_robei_xdc` 读取并映射
- **同一引脚不能出现在两个端口/同一位**（跳过大小写差异），否则整个调用
  `code=invalid_argument`，不写文件
- `vendor` / `device` / `family` 只在 `kind="const"` 时写入根节点；留空时 Robei 会提示
  `has no Device/Family configured`（综合仍会跑完），填成目标板卡的器件名即可消掉
- 想在 `xc7a*fgg484` 上真的走通 Route / Package，实测可用的一组引脚是
  `swt = A1, A14, A15, A16`、`led = A20, A19, A13, A18`
  （完整写法见 `examples/fpga_demo/const/light_constrain.const`）。**这只是 Roplace 在该器件上
  能通过的一套引脚组合，不是任何板卡的引脚表，也没有上板验证。**
- 已存在要 `overwrite=true`（留 `.bak-<时间戳>`）

### `read_robei_const`

```json
{"path": "const_demo/const/light_constrain.const"}
```

返回 `kind`、`instance`、`model`、`pins`（`{端口: {位: 引脚}}`）、`pin_options`
（引脚 → `function` / `drive` / `pull` / `voltage`）、`ports`、`wires` 和 SHA256。只读。

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

默认输出**标准 Tcl** 的总线写法 `[get_ports {swt[0]}]`（`tcl_quoting: "standard"`），
Vivado 直接可用。Robei 自带的 Route 解析器只认不带花括号的 `[get_ports swt[0]]`：
需要喂回 Robei 时传 `quote_bus=false`，返回 `tcl_quoting: "legacy"`。

`also_build=true` 同时覆盖 `<工程>/build/<name>.xdc`——那是 `Build→Route` 实际解析的副本，
Robei 自己写的版本 `IOSTANDARD` 为空，Route 会因此报参数个数错误。

**Drive / Pull / Voltage**：`.const` 引脚上的 `Drive` 非空时输出
`set_property DRIVE <值>`；`Pull` 接受 `up`/`pullup`/`pull-up` 与
`down`/`pulldown`/`pull-down`，映射成 `PULLUP TRUE` / `PULLDOWN TRUE`，其他值记进
`pull_warnings` 并跳过；`Voltage` **不会**被猜成 IOSTANDARD，只放进
`voltage_hints: [{port, voltage}]` 供调用方判断。空字段不产生任何行。

### `list_fpga_devices`

```json
{"part": "xc7a50tfgg484-1"}
```

读 Robei 的器件库（默认 `C:\ProgramData\Robei\xilinx`，可用 `ROBEI_FPGA_DB` 覆盖），
把 `chipdb/*.bin` 与 `<family>/<part>/part.yaml` 配对：

| 字段 | 说明 |
|---|---|
| `part` | 完整型号，含速度等级，如 `xc7a50tfgg484-1` |
| `family` | `artix7` / `zynq7`（就是 part.yaml 所在的目录名） |
| `chipdb` / `part_yaml` | 两个文件的绝对路径，缺一个就是 `null` |
| `buildable` | 两个都在才为 `true`，否则 `reason` 写明缺哪个 |
| `tools` | `roplace` / `fasm2frames` / `xc7frames2bit` 的路径与是否存在 |

本机实测：5 个 chipdb 对 6 个 part.yaml，`xc7a100tcsg324-1` 只有 part.yaml，
所以它是 `buildable: false`（Robei 自己的器件下拉里也没有它）。`part` 不存在时返回
`invalid_argument`。

### `synthesize_project`

```json
{"proj_path": "fpga_demo/fpga_demo.proj", "part": "xc7a50tfgg484-1", "timeout_s": 120}
```

按 Robei `Build > Synthesis` 的同一条命令综合：

```text
yosys -p "read_verilog <build>/<top>.v; synth_xilinx -flatten -arch xc7 -abc9 -nodsp;
          write_json -noscopeinfo <build>/<const>.json"
```

- 喂给 yosys 的只有"顶层 + 它真正实例化的子模型"（工程里刚建出来的空占位模型不算），
  顶层取自 `.const` 里那个实例的 `Class`，也可以用 `top` 显式指定。
- 之后单独跑一次 `tee -o <build>/<const>_stat.txt stat`，从文件里解析 cell 表：
  `utilization = {LUT, FF, CARRY, BRAM36, BRAM18, DSP, IO}`。`LUT` 含 `INV`
  （7 系列里反相器同样占一个 LUT 位），`IO` 是顶层端口的总位数。
- 产物：`<build>/<const>.json`、`<build>/<const>_stat.txt`，外加一份运行目录
  `workspace/build/fpga-*/`（返回里的 `run_dir`）：`summary.json` 是整次调用的记录
  （`summary_path`），同目录下每个 stage 还有一个 `<stage>.log`（`log_path`）。
- 本工具没有 `steps` 参数（综合只有一段）；分段执行请用 `build_bitstream`。

- 1.7 为本工具及 `build_bitstream` 增加以下字段，原 `utilization` 保持兼容；
  `robei_gui_synthesis` 不因此获得同样的结构化字段：

  | 字段 | 说明 |
  |---|---|
  | `utilization_capacity` | 资源容量字典；没有可用容量时为 `null` |
  | `utilization_percent` | 各项 `100 * used / capacity`，保留三位小数；缺失或容量不大于 0 时为 `null` |
  | `capacity_source` | `{path, lines}`，`lines` 是资源名到 part.yaml 行号的映射；无来源时为 `null` |
  | `utilization_warnings` | 缺少 part、容量文件或可识别容量键的原因 |

  容量只读器件库的 `part.yaml`，当前解析 LUT/FF/BRAM18/BRAM36/DSP/IO 的简单整数标量键，
  不引入 YAML 依赖，不从型号、帧数或其它资源猜容量，也不自动互换 BRAM18/36。
  CARRY 没有对应容量解析键，百分比为 `null`。本机 part.yaml 缺容量键时，
  `capacity_source` 仍可能有路径和空 `lines`，这是“已找到文件”，不是“已取得容量”。
  未知 `part` 返回 `device_unsupported` 并带 `available_parts`；先用 `list_fpga_devices` 查询。

- `part` 可选；给了就校验型号存在，不存在返回 `device_unsupported`。综合本身不依赖 chipdb。
- `timeout = {requested_s, effective_s, max_timeout_s, clamped}`：子进程超时会被
  `ROBEI_MAX_TIMEOUT_S`（默认 120 s）截断，`clamped=true` 时 `warnings` 里会明说；
  大器件请先把上限调大。

### `build_bitstream`

```json
{"proj_path": "fpga_demo/fpga_demo.proj", "part": "xc7a50tfgg484-1",
 "steps": ["synth", "route", "package"], "timeout_s": 120}
```

三段命令与 Robei 完全一致（`<db>` = `C:\ProgramData\Robei\xilinx`）：

```text
Roplace --chipdb <db>/chipdb/<part>.bin --xdc <build>/<const>.xdc
        --json <build>/<const>.json --write <build>/<const>_routed.json
        --fasm <build>/<const>.fasm
fasm2frames --db-root <db>/<family>/ --part <part> <fasm> <frame>
xc7frames2bit --part_file <db>/<family>/<part>/part.yaml --part_name <part>
              --frm_file <frame> --output_file <bit>
```

- `steps` 是枚举，只接受 `"synth"` / `"route"` / `"package"` 三个值（schema 里就是
  `enum`，写错值在 MCP 层就被拒，不会跑到一半才失败）。
- 每段返回 `{stage, command, returncode, duration_s, timeout, log_tail, log_path,
  run_dir, argv, outputs}`；`package` 拆成 `package.frames` 与 `package.bit` 两个 stage。
  每一步的完整 stdout+stderr 落在 `run_dir/<stage>.log`，`log_path` 就是那个文件的绝对
  路径，可以直接喂给 `read_run_log`。
- 第一个失败就停下：`code=fpga_step_failed`，`stage` 指出是哪一段。返回的**顶层**直接带
  `stage` / `argv`（已脱敏）/ `returncode` / `log_tail` / `log_path` / `run_dir`，
  完整的 stage 记录仍在 `record` 里。1.2.2 之前这条路径实际抛
  `AttributeError: FpgaStepFailed has no from_record`，所有非零退出都变成 `code=internal`。
- `route` 段开始前会检查 `build/<const>.xdc`：文件不在就用 `.const` 现场重新导出
  （等价于 `export_robei_xdc`，同时写 `constrain/` 和 `build/`），返回里
  `xdc_generated=true` 并在 `warnings` 里记一条；`.const` 本身不存在则是
  `invalid_argument`，hint 指明先 `create_robei_const`。
- 器件没有 chipdb（或没有 part.yaml）→ `device_unsupported`，`error` 里带原因。
- `.const` 没把某个端口位绑到引脚 → `unconstrained_io`，
  `missing: [{port, direction, missing_bits}]`，此时不会启动 Roplace。
- 三件套二进制找不到 → `fpga_step_failed`，`hint` 提示用 `ROBEI_FPGA_BIN` 指目录。
- `steps` 可以只要前几段（例如 `["synth"]`），此时 `ok=false`、`artifacts.bit=null`，
  其余产物照常返回。
- 已知限制：IDE 打开工程会把 `.const` 根节点的 `Family` / `Device` 清空，所以 GUI 侧的
  Route/Package 无法复现（本工具的 `.fasm` 与 IDE 侧同 json 时逐字节一致，差异不在工具链）。
- `timeout_s` 默认 **120**（就是 `ROBEI_MAX_TIMEOUT_S`），默认调用不带 warning；
  显式要更长时间才会被截断，并在 `timeout.clamped=true` 与 `warnings` 里说明。
- `bit_payload_sha256` / `bit_payload_offset`：从同步字 `0xAA995566` 起算的哈希与偏移。
  `.bit` 头部会写 `.frame` 的绝对路径（`a` 字段）、器件（`b`）、日期时间（`c`/`d`），
  所以**同样构建的 `.bit` 大小随路径长度变化、字节随时间变化**；比对构建结果请用
  `fasm` / `frame`，或者比 `bit_payload_sha256`。
- `fmax_reference` 是 Roplace 自己打印的 `Max frequency for clock ...`，**仅供参考**；
  组合逻辑设计没有时钟，Roplace 只打印 `Warning: No clocks found in design`，该字段为 `null`。
  `timing_note` 会写明本工具不做时序签核。

### `read_run_log`

```json
{"path": "build/fpga-20261024-101530-ab12cd34/route.log"}
{"path": "build/fpga-20261024-101530-ab12cd34/route.log", "tail": 50, "max_bytes": 200000}
```

只读工具：打开 `fpga_step_failed` / `internal` 返回里那个 `log_path`，或者任何落在
workspace 下 `logs/`、`build/` 目录里的文件（工程自带的 `<proj>/build/` 也算）。

- 1.3 起只认三个根（其余一律 `code=path_escape_log`）：
  1. `workspace/logs/**`
  2. `workspace/build/<run dir>/**`，`<run dir>` 必须匹配真实戳记
     `(fpga|lint|sim|library|truth-*|headless-*)-\d{8}-\d{6}-[0-9a-f]{8}`
  3. `<工程目录>/build/**`，且该工程目录里确实有 `*.proj`
  一个只是**碰巧叫 build** 的目录（没有 `.proj`）不再放行。目录穿越（`../`、绝对路径）
  照旧被 `strict_path_scan` 挡掉，返回 `code=path_escape`。
- `max_bytes` 默认 500,000（下限 4,096、上限 5,000,000）：超长文件只保留末尾这一段，
  返回 `truncated=true`、`bytes_read` 与 `size_bytes`，`note` 写明丢掉了多少字节。
- `tail` 默认 200 行：`lines` 是最后 `tail` 行，`line_count` 是实际保留的行数。
- 返回 `path` / `relative_path` / `lines` / `line_count` / `returned_lines` /
  `size_bytes` / `bytes_read` / `max_bytes` / `truncated` / `note`。
- 文件不存在 → `code=not_found`；传的是目录 → `code=not_found`。

### `run_all_tests`

```json
{"proj_path": "part2_exp12/part2_exp12.proj", "timeout_s": 60}
```

跑 `test/*.test` 里的每一个用例（内部就是 `simulate_project`），返回：

```json
{"ok": false, "total": 2, "passed": 1, "failed": 1,
 "tests": [{"test": "tb_ok", "ok": true, "returncode": 0, "duration_ms": 812,
            "vcd_paths": ["workspace/build/headless-.../tb_ok.vcd"], "error_tail": []},
           {"test": "tb_broken", "ok": false, "returncode": 1, "duration_ms": 96,
            "vcd_paths": [], "error_tail": ["... syntax error ..."]}],
 "vcd_paths": ["..."]}
```

一个用例失败不影响其它用例；`error_tail` 会把 `compile` / `simulation` 两段的 stderr 一起收进来。
工程里没有任何 `.test` 时 `total=0`、`ok=false`。

### `robei_gui_open_project`

```json
{"proj_path": "exp6_counter/exp6_counter.proj"}
{"proj_path": "exp6_counter/exp6_counter.proj", "discard_unsaved": true}
```

1.3 起**不再走 File → Open**：Qt 的原生打开对话框一弹出，Robei 的 UIA provider 就不再应答
（实测整个桥 90 s 超时，只能重启 IDE）。现在改成 argv 重启：

```text
robei_gui_close（只关这个桥自己启动的 pid）
Robei.exe "<.proj 的绝对路径>"
按 pid attach → 校验 Files 树根 == .proj 的 stem
```

- 返回 `restarted=true`、`pid`、`argv`、`project`、`tree`、`models`、`unsaved_at_close`、`warnings`
- **会丢掉画布上未保存的修改**：实测 Robei 的窗口标题和模型标签在编辑后都不带 `*`，
  没有可靠的未保存标记；只有真探测到时才会要求 `discard_unsaved=true`
- 有**不是这个桥启动的** Robei 在跑时返回 `code=gui_foreign_instance`，hint 让你自己关；
  桥只会 `taskkill` 自己记录过的 pid（记录在 `workspace/gui_agent/robei_pid.json`），而且动手前
  会核对这个 pid 的进程名与记录里的 exe 路径确实还是 `Robei.exe`——pid 被回收、换成了别的程序、
  或是一次手工启动的 IDE，都当作"没有自己启动的实例"（`closed:false`）并删掉过期记录
- 全程不需要鼠标、键盘或活动桌面（锁屏也能开工程）；一次重启约 1–3 s

### `robei_gui_open_model`

```json
{"name": "dec_counter"}
```

展开 Files 树并双击模型（**唯一还需要鼠标的 GUI 操作**：Qt 的 `TreeItem` 对 UIA 的
`Invoke` / `ExpandCollapse` / `Select` 全部无反应，实测展开状态一直是 0）。

- 桌面不可用（锁屏 / RDP 断开）时返回 `code=gui_needs_active_desktop`，
  不再抛 `RuntimeError: There is no active desktop required for moving mouse cursor!`
- 打开失败的判定仍然是"活动标签 == 模型名"，避免上一个工程留下的旧标签冒充成功

### `robei_gui_compile`

```json
{"timeout_s": 60}
```

Build → Compile。1.3 起用 `ExpandCollapse.Expand()` 打开 Build 菜单、对 `Compile` 条目
`Invoke`（不碰鼠标），`Invoke` 立即返回，完成与否看 Output 里的 `Compile done` /
`Check complete`。返回里 `menu.via` 说明这次是 `uia` 还是回退到 `mouse`，
`generated` 列出 `Generating ...` 的文件。

### `robei_gui_save_project`

```json
{}
```

保存工程。Build → Synthesis 之前必须做一次，否则 Robei 只回
`Error: please save your current project first.`

1.3 起菜单操作走 UIA：`ExpandCollapse.Expand()` 打开 File 菜单，再对 popup 里的
`Save` 调 `Invoke`（不碰鼠标）。返回里的 `menu = {"via": "uia" | "mouse", "uia_error"}`
说明这次实际走的是哪条路；`Invoke` 立即返回，所以仍然用 Output 的变化判断成功。

### `robei_gui_compile_all` 的新字段

每个模型的结果现在固定带 `state`（`ok` / `failed` / `unknown_state`）、`marker`
（实际命中的完成标记）和 `output_tail`（Output 尾部，失败时用来定位），
`unknown_state` 会自动重试一次并标 `retried: true`。

### `robei_gui_screenshot`

```json
{"label": "canvas-check"}
```

抓取整个交互桌面并保存为 `workspace/gui_shots/<label>-<时间戳>.png`，返回 `path`、
`size_bytes`、`width`/`height`、`mean_rgb` 和亮度范围。和其它 GUI 工具一样走
Session-1 桥（direct 或 schtasks）；用来在排查编译/布局问题时"看一眼屏幕"。
`inline=true` 时同样附带 `ImageContent`（5 MiB 上限，超限给 `image_too_large` 报告）。

### `robei_gui_synthesis`

```json
{"timeout_s": 240}
```

Build → Synthesis 并等到 `Synthesis complete`（内部是 yosys 的 Xilinx 流程）。
菜单同样走 UIA（`menu.via` 标明 `uia` 还是回退到 `mouse`）。
返回 `completed`、`errors`、`board_warnings`、`menu`、`output_tail`、`delta`。

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

- `n` 是**输入总位数**，不是端口个数：`input [1:0] a` + `input [3:0] b` 算 6 位（64 行），
  超过 `max_inputs` 返回 `invalid_argument`
- 枚举按位打包：最后一个输入端口的位是最低位，`rows[1]` 只让最后一个端口 +1
- 位宽支持常量表达式（`[4-1:0]` 按 4 位算）；`[WIDTH-1:0]` 这类参数化位宽**不支持**
  （不会拿 `Parameters` 的默认值去代入），会返回 `invalid_argument` 而不是按 1 位枚举
- 生成的 testbench 写在 `build/truth-<top>-<时间戳>-<uuid8>/`，每次调用独立

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

用 venv 内的 matplotlib（Agg 后端）逐信号阶梯图，返回路径和字节数。默认文件名带时间戳 +
`uuid8`，并发调用不会互相覆盖（调用方显式给的 `out_name` 仍然遵守"已存在则拒绝"）；
`x`/`z` 记为 NaN，最后一个值会一直画到 `max_time`（保持段不会缺一截）。

**输出位置（1.3）**：VCD 在某个工程里（同级有 `*.proj`）就写到
`<工程>/build/waveforms/`；`out_dir` 显式指定时以它为准（相对 workspace 解析，越界照样拒）；
两者都没有才回退到 `workspace/build/waveforms/`（1.2 及以前的位置）。返回里的 `out_dir` /
`project` 会说清落点。

**`inline`**：`{"inline": true}` 时在文本 JSON 之外再返回一个 `ImageContent`
（`mimeType=image/png`，`data` 是 base64）。上限 **5 MiB**，超限不报错：payload 里
`inline = {"ok": false, "code": "image_too_large", "size_bytes", "limit_bytes"}`，`path` 照常返回。
默认 `false`，行为和 1.2 完全一致（文本 JSON + `structured_content`）。

## 错误码、进度与取消

失败返回统一长这样，`code` 稳定、`error` 给人看、`hint` 是下一步建议：

```json
{"ok": false, "code": "not_found",
 "error": "FileNotFoundError: file not found in workspace: C:\\...\\missing.v",
 "hint": "the path must exist and, for models/projects, sit under an allowed root"}
```

语义错误（越界位选、未知端口/实例/参数、重复实例/端口/net、没有匹配的 wire、
`<Wire>` 端点带 `[`、切片宽度与端口不等）在 `hint` 里直接说明**哪个字段错了**、合法范围或
候选名（未知名字给 difflib 最近 3 个），并给一个正确写法：

```json
{"ok": false, "code": "invalid_argument",
 "error": "BadRequest: a[9] is out of range: a is 4 bit(s) wide (valid indices 0..3)",
 "hint": "a is 4 bit(s) wide: address a bit as a[0]..a[3], or the whole bus as a[3:0]"}
```

`code` 仍是 `invalid_argument` / `width_mismatch`，老客户端按 code 分支不用改。
`"check the argument names and types in the tool schema"` 只留给真正的类型错误或缺参数。

| `code` | 场景 |
|---|---|
| `unsafe_xml` | 文档里出现 `<!ENTITY>` 或带内部子集的 `<!DOCTYPE>` |
| `not_found` | 文件/工程/模型/端口不存在 |
| `already_exists` | 拒绝覆盖（`overwrite=true` 可覆盖并留 `.bak`） |
| `invalid_argument` | 名字非法、连线端点不存在、参数越界、未知 op |
| `cancelled` | 客户端取消，子进程已被 kill |
| `timeout` | 超过 `timeout_s` 或 `max_timeout_s` |
| `compile_failed` / `run_failed` | iverilog 编译失败 / vvp 运行失败（看 `compile` / `simulation` 段） |
| `path_rejected` | `simulate` 的安全扫描在编译前拒绝了 `include` / `$fopen` 等越界路径 |
| `path_escape` | 传入路径解析到 workspace / 允许根目录之外；hint 会说明"路径必须在 workspace 内" |
| `width_mismatch` | `edit_robei_canvas` 的 `connect` 两端位宽不同 |
| `not_behavioral` | 对结构级模型（有实例或 wire）调用 `set_robei_code` |
| `unknown_parameter` | `set_robei_parameter` 的名字不在 `parameter_list` 里 |
| `device_unsupported` | `build_bitstream` 的器件没有 chipdb / part.yaml（`list_fpga_devices` 看得到哪些可用） |
| `unconstrained_io` | `.const` 里还有端口位没绑引脚，`missing` 列出 port 与 missing_bits |
| `fpga_step_failed` | synth / route / package 某一段失败，`stage` 与 `record.log_tail` 指出位置 |
| `path_escape_log` | `read_run_log` 只开 `workspace/logs`、`workspace/build/<run dir>` 或某个工程的 `build/` |
| `gui_foreign_instance` | 有不是这个桥启动的 Robei 在跑；先自己关掉，桥只 kill 自己记录的 pid |
| `gui_needs_active_desktop` | 桌面锁屏/断开，鼠标类 GUI 操作做不了（`open_project` / `compile` 不受影响） |
| `gui_unsaved_changes` | 探测到画布有未保存修改；传 `discard_unsaved=true` 才会重启（当前版本探测不到） |

**进度**：`simulate` / `simulate_project` / `lint_verilog` 在阶段边界上报
`notifications/progress`（`total` 是阶段数，不是时间估计）。客户端不订阅就静默无副作用。

**取消**：长任务跑在可放弃的工作线程里，`RunToken` 记录这次调用启动的子进程
（`iverilog` / `vvp` / `yosys` / `verilator`）。客户端发 `notifications/cancelled` 后，
服务端置位 token 并 kill 这些进程，线程随即解出；实测从取消到 `vvp.exe` 消失 < 2 s，
服务端仍继续响应后续请求。

`robei_gui_*` 走的是计划任务桥，不是这批子进程：取消会让调用立刻返回，但桥任务会按它
自己的超时跑完（结果丢弃）。已知副作用：被取消的调用可能留下半个 `workspace/build/sim-*/` 目录。

## `build_report`（1.7.0）

```text
build_report(proj_path: str, tests: list[str] | None = None,
             out: str | None = None, include: list[str] | None = None,
             truth_map: dict[str, Any] | None = None,
             overwrite: bool = False, timeout_s: int = 60,
             reuse_existing_runs: bool = False)
```

生成事实型 Markdown 和同名 `.evidence.jsonl`，不生成 HTML，也不推断电路正确性。

- `proj_path` 必须在 workspace；默认报告为 `<project>/report/<project>.md`。
  `out` 可指定该 report 目录内的 `.md` 文件名/路径，不得逃逸到工程外。
- `tests=None` 枚举全部 `test/*.test`；显式列表接受 stem 或带 `.test` 的名字，
  空列表不跑 test。每个 test 顺序调用 `simulate_project`。
- `reuse_existing_runs=true` 优先选 `<project>/build/sim-*/<test>.vcd` 中 mtime 最新的同名文件，
  找不到才仿真。复用只证明找到了 VCD，不是重新确认历史仿真 rc 或自检 PASS；
  当前不会从旧运行恢复 `returncode`、`duration_ms`、`$display`。
- `include` 接受 `simulation`、`truth_table`、`display`、`assertions`、`waveform`、`synthesis`。
  `None` 或空列表启用全部；非空列表才按项选择。`Missing` 清单仍会输出。
- 真值表默认从 `tb_<top>` 推导 top，并找工程 `verilog/<top>.v` 或 `build/<top>.v`；
  找不到时记“未提供”。`truth_map` 可把 test stem 映射到 top 字符串，或
  `{top, verilog_path?, deps?}` 对象。不是从 testbench 自动推断期望真值表。
- waveform 用首个 VCD 的前 16 个信号生成 PNG。assertions 只筛选 `$display` 中含
  `pass|fail|assert|mismatch|selftest` 的行，不是独立断言判定器。
- synthesis 调用 `synthesize_project`；本接口没有 `part` 参数，不会据此猜测容量百分比。
  `timeout_s` 传给 test 仿真及综合；真值表使用其工具自身默认时限，不是整个报告的总时限。
- `.proj/.model/.test/.const` 和输入 VCD 只读；仿真、真值表、波形、综合仍可能生成各自的
  build/Verilog 产物，因此不是“只写 report 目录”的工具。
- Markdown 或 evidence 任一个已存在，默认 `already_exists`；只有 `overwrite=true` 才覆盖。

例：路径相对 workspace，不要额外拼接工作区根目录。

```json
{"proj_path":"exp1_and/exp1_and.proj","tests":["tb_and2"],
 "out":"lab2.md","reuse_existing_runs":true,
 "truth_map":{"tb_and2":{"top":"and2","verilog_path":"exp1_and/verilog/and2.v"}}}
```

主要返回：

| 字段 | 说明 |
|---|---|
| `ok` | 报告生成状态，不等于所有 test 自检通过，也不等于报告验收通过 |
| `project/project_relative` | 工程路径 |
| `report_path/report_relative_path` | Markdown 路径 |
| `evidence_path/evidence_relative_path` | JSON Lines 证据路径 |
| `tests[]` | `test, ok, returncode, duration_ms, vcd_paths, reused, display_output, display_truncated, headless`；可能附带 plot 与证据索引 |
| `sections` | 实际选择的章节 |
| `missing[]` | `section, test, reason`；超限项还含 evidence 索引 |
| `warnings[]` | 真值表超限的 test、输入位数、上限、说明及证据索引 |
| `size_bytes/sha256` | Markdown 大小与 SHA256 |

证据文件一行一个 JSON：`{section, field, value, source, source_line}`。
`[evidence:N]` 应对应 evidence 文件的第 N 行（从 1 开始）；`source/source_line` 可为空，
不能把不可用的来源行号伪造成已知值。常见错误为 `path_escape`、`already_exists`、
`not_found`、`invalid_argument`；依赖步骤也可能返回自己的错误码。

**1.7.0 缺失值与证据约定**：Markdown 的缺失值（含嵌套容量/百分比）统一呈现为“未提供”，
JSON 返回与 JSONL 仍保留原始可空值，不伪造 rc/耗时。$display/断言文字引用对应的 evidence 行；
所有 `[evidence:N]` 在写报告和 evidence 文件前校验，必须指向真实的第 N 条记录。

真值表输入超限只降级该 test 的 truth-table 小节，写“未提供（输入 N 位 > 上限 M）”，
其余 test 和章节继续。返回 `warnings[]`，每项含
`code="truth_table_input_limit", test, input_bits, max_inputs, message, evidence`；
`missing[]` 同时记录原因和证据索引。其它非超限异常不被静默隐藏。
这不是完整穷举通过；不能把局部降级或 `ok=true` 写成该真值表验收通过。

**M5 Lab 2 验收（2026-10-30）**：六个工程的 7 个 test 使用 reuse_existing_runs=false，
全部 rc=0，耗时与执行日志一致；6 份报告均无 None/null，102 处引用全部有效，源文件 SHA256 不变。
tb_alu8 真值表为“未提供（输入 20 位 > 上限 8）”，带一条超限 warning，
且 tb_alu32 与 tb_alu8 均保留在报告中。

### MCP Python 客户端的等待参数

M4 exp1 的 `float + datetime.timedelta` 错误来自临时验收脚本，不是 Codex 本体故障，
也不是 `build_report.timeout_s` 接口错误。本仓库使用的 MCP SDK 2.3.0 中，
`ClientSession.call_tool(..., read_timeout_seconds=...)` 接受数值秒（`float | None`），
不能传 `timedelta`。工具参数 `timeout_s` 是另一层的整数子进程预算：

```python
result = await session.call_tool(
    "build_report",
    {"proj_path": "exp1_and/exp1_and.proj", "out": "lab2-m5.md",
     "timeout_s": 60, "reuse_existing_runs": False},
    read_timeout_seconds=180.0,
)
```

客户端等待并非单个仿真/综合步骤的预算，也不是报告总执行时限。
SDK 可能已发出请求后才抛本地等待参数错误；失败后先检查产物，重试使用新报告名，
不要因此自动覆盖已有报告。无需为此修改服务端接口或 venv。

## `export_vcd_window`（1.7.0）

```text
export_vcd_window(vcd: str, signals: list[str] | None,
                  t_start: int, t_end: int, format: str = "csv",
                  step: str | int = "edges", out: str | None = None,
                  overwrite: bool = False, include_initial: bool = True)
```

- 所有时间都是 **原始 VCD tick**。返回 `timescale` 和 `timescale_fs`，不做隐式物理时间换算；
  例如 `timescale="1ps"`、`timescale_fs=1000` 时，tick 105000 表示 105000 ps。
- `signals=None` 选择全部信号，空列表拒绝。依次按精确名、simple name、完整名后缀匹配；
  未知名或歧义返回 `bad_argument`，不会任选一个信号。
- `step="edges"` 收集所选信号的变化 tick；正整数按固定 tick 间隔采样。
  起点、终点总会输出，每行读取该 tick 已应用变化后的信号值；X/Z 保留原值。
- **当前边界**：`include_initial` 只控制 `initial_missing` 诊断，不会移除起始行；
  `change_count=rows-1`，不代表独立信号的翻转次数。
- 默认输出 `<project>/build/vcd_windows/`；非工程 VCD 输出 workspace 的
  `build/vcd_windows/`。`out` 只能是匹配格式后缀的简单文件名，禁止绝对路径或 `..`。
- 输入、输出均受 `max_vcd_bytes` 限制；超限返回 `invalid_argument`。
  输入 VCD 永不修改；已有输出默认 `already_exists`，需 `overwrite=true` 才覆盖。
- 负时间、起点大于终点、超过 VCD 最大 tick、无效 step/format/空信号列表均为
  `invalid_argument`；越过允许路径为 `path_escape`。

```json
{"vcd":"exp6_counter/build/sim-20261026-002649-3235bde3/tb_counter.vcd",
 "signals":["tb_counter.clk","tb_counter.rst","tb_counter.en","tb_counter.cnt","tb_counter.co"],
 "t_start":95000,"t_end":105000,"format":"csv","step":"edges"}
```

上面的 VCD 路径是 Lab 2 本轮实际使用的文件，不是固定默认路径；其它运行应使用工具返回的路径。
本轮导出为 3 行：

| tick | clk | rst | en | cnt（二进制） | co |
|---:|---:|---:|---:|---|---:|
| 95000 | 1 | 0 | 1 | 1001 | 1 |
| 100000 | 0 | 0 | 1 | 1001 | 1 |
| 105000 | 1 | 0 | 1 | 0000 | 0 |

返回 `source_vcd/source_relative_path`、`path/relative_path`、`format`、
`timescale/timescale_fs`、`window={start,end,step,include_initial}`、信号描述 `signals[]`、
`rows`、`change_count`、`initial_missing`、`size_bytes`、`project`。
