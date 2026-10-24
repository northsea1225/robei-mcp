# 验证日志

按时间顺序记录每个阶段「实际跑通了什么」。这里只写**做过并留下证据**的事，不是使用文档。

阅读提示：

- 想装起来用，看 [README](../README.md)；想看每个工具的用法，看 [tool-reference](tool-reference.md)。
- 本文件里的阶段编号（stage2…stage21）对应 `tests/` 下的同名脚本，可以直接重跑。
- 0.11 移除了 9 个工具（`report_lab`、`robei_gui_experiment`、`robei_gui_run`、`robei_gui_wave`、
  `robei_gui_wave_load`、`robei_gui_code_read/write`、`robei_gui_refresh`、`robei_gui_health`），
  早期小节里提到它们的地方属于历史记录，替代关系见 [CHANGELOG](../CHANGELOG.md)。

## 交付状态

### 1.3.0：GUI 去鼠标化、嵌套工程、工程内产物（2026-10-25，草稿）

工具面 **49 个（文件级 38 + GUI 11）**，`ROBEI_MCP_GUI=0` 时 38 个（本版只加参数，不加工具）。

**M0：UIA 能力探针**（Session-1 上的真实 Robei 5.0 Student，Qt6 / `Qt680QWindowIcon`）

| 目标 | 暴露的模式 | 实测 |
|---|---|---|
| `QMenuBar` 的 MenuItem | invoke / value / expand_collapse | `Invoke()` 返回成功但菜单不弹；`ExpandCollapse.Expand()` 才会打开菜单并出现 popup 句柄 |
| popup 里的 MenuItem | invoke | `Invoke()` 可用且 0.00 s 返回；Build 菜单 = Refresh/Compile/Run/Wave/Synthesis/Route/Package/Download/Burn |
| 原生打开对话框 | — | Invoke `Open` 后 Qt 的 UIA provider 阻塞：同进程后续调用和新起的 `status` 作业都 90 s 超时，只能杀进程 + 重启 Robei（两次复现） |
| Files 树 TreeItem | invoke / selection_item / expand_collapse | 三种都改不动展开状态（state 前后都是 0） |
| 命令行 | — | `Robei.exe "<proj>"` 打开工程；`Robei.exe "<model>"` 打开独立模型；已有实例时再启动会新开窗口（不切换原窗口） |

**M3：真实调用证据**

```text
外来实例  foreign_open  -> code=gui_foreign_instance (pid 57212)
          foreign_close -> closed=false，窗口仍在（桥只 kill 自己记录的 pid）
桩掉鼠标/键盘（_click / _double_click / send_keys 一调用就抛异常）：
          open_project  -> ok=true restarted=true pid=64740
                           argv=[Robei.exe, ...\exp6_counter.proj]
          compile       -> ok=true marker="Compile done" menu.via="uia"
真实编译输出：Check complete with 0 warnings / Generating ...\dec_counter.v / Compile done
未保存探测：编辑 Code（ValuePattern，save=False）后 title="Robei  Student"、tabs=["dec_counter"]
          -> 没有 '*' 标记，无法据此拦截
pid 记录校验：owned_pid() 比对进程名 + 记录里的 exe 路径；非 Robei（notepad / 别的目录同名 exe /
          已退出的 pid）一律丢弃记录并让 close() 返回 closed=false（4 条 pytest 覆盖）
```

**产物位置（P1-5）**：`simulate_project` 的 `tb_source` 落在
`exp6_counter/build/headless-…/tb_counter.v`，PNG 落在
`exp6_counter/build/waveforms/tb_counter-….png`。探针同时确认：Robei 的 Compile 会删除
`<工程>/build` 下的**顶层文件**（`marker_probe.txt` 消失），但**保留子目录**
（`probe_dir/inner.txt` 仍在）——所以目录形态的运行目录是安全的。

**inline PNG（真实 MCP 调用）**

```text
simulate_project(render=true, inline=true) -> content = [text, image]
   text.plot.path      = exp6_counter/build/waveforms/tb_counter-….png
   text.plot.inline    = {ok: true, mime_type: image/png, size_bytes: 142573}
   image               = image/png, 55560 base64 chars
render_waveform(inline=false) -> content = [text]，structured_content = dict
render_waveform(inline=true)  -> content = [text, image]
```

**回归（本轮）**

| 项 | 结果 |
|---|---|
| pytest | 178 passed |
| portable_core | 16/16 |
| stage2–21 | 全过：5 → 10/10、7 → 12/12、8 → 10/10、11 → 13/13、12 → 18/18、13 → 18/18、14 → 9/9、16 → 12/12、17 → 28/28、18 → 29/29、19 → 26/26、20 → 14/14、21 → 18/18；stage2/3/4/9/10 只打印 rc=0 |
| `tools/list` | 49（文件级 38 + GUI 11）；`ROBEI_MCP_GUI=0` → 38 |

**未测试 / 注意**

- 锁屏状态下的真实行为：不主动制造锁屏，"不碰鼠标"是用打桩证明的。
- `robei_gui_open_model` 的 `gui_needs_active_desktop` 只走了代码路径，没有真实锁屏复现。
- 没有上板；`check_wheel.py`、`setup.py --apply`、`verify_1_0_2.py` 未跑。
- 1.3 仍然不做：画布总线位选、实例参数（缺 Robei GUI 保存样本）、Download/上板。

### 1.2.2：失败的 FPGA 段、运行日志读取（2026-10-24）

工具面 **49 个（文件级 38 + GUI 11）**，`ROBEI_MCP_GUI=0` 时 38 个。

**P0：`FpgaStepFailed.from_record` 根本不存在（已修）**

1.2.1 的 `fpga.py` 在每段失败时调用 `FpgaStepFailed.from_record(record)`，但 `errors.py`
里没有这个方法，于是任何 rc≠0 的调用都在抛异常那一刻变成 `AttributeError`，顶层只能兜成
`code=internal`。

实测用数电实验六的工程（`workspace/exp6_counter`，`.const` 把 `cnt` 绑到 `M14/M15/N15/N16`）：

```text
build_bitstream(proj_path="exp6_counter/exp6_counter.proj",
                part="xc7a50tfgg484-1", const_name="dec_counter")
→ code=fpga_step_failed   stage=route   returncode=3
  argv      E:\Robei\bin\Roplace.exe --chipdb ...\xc7a50tfgg484-1.bin
            --xdc ...\dec_counter.xdc --json ...\dec_counter.json
            --write ...\dec_counter_routed.json --fasm ...\dec_counter.fasm
  log_path  workspace\build\fpga-20261024-203823-2550804b\route.log
  run_dir   workspace\build\fpga-20261024-203823-2550804b
  log_tail  Error: Unable to constrain IO 'cnt[0]', device does not have a pin named 'M14'
            terminate called after throwing an instance of 'log_execution_error_exception'
```

同一份 `route.log` 再用 MCP 的 `read_run_log(path=log_path, tail=8)` 读回：981 字节、
8 行（`truncated=false`），末行正是那条 `terminate called ...`。

`M14` 是实验指导书那张板的引脚，`xc7a50tfgg484` 上没有这个球——报错是真的，不是工具链问题。

**换成 fgg484 上真实存在的球之后出 `.bit`**（工程是 `exp6_counter` 的临时副本，同一份
RTL：`clk=A1 rst=A14 en=A15 cnt=A20,A19,A13,A18 co=A16`；产物留在
`workspace/build/exp6_valid_pins/`，该目录在 `.gitignore` 里）：

```text
bit                  workspace/build/exp6_valid_pins/dec_counter_valid.bit
size                 2,192,170 bytes
bit_payload_sha256   bcd969f3b531ba4c7266ebeba35e9c3dc1e9c1c19e572fea287b8e0d5adc6e85
bit_payload_offset   206
utilization          LUT 4 / FF 4 / CARRY 1 / BRAM36 0 / BRAM18 0 / DSP 0 / IO 8
fmax_reference       539.08 MHz ($abc$2225$iopadmap$clk)
xdc_generated        true（build/ 里没有 xdc，这次是现场从 .const 导出的）
```

**回归（本轮，Windows）**

| 项 | 结果 |
|---|---|
| pytest | 147 passed（32.5 s） |
| portable_core | 16/16 |
| stage2 / 3 / 4 / 5 / 7 / 8 / 9 / 10 / 11 / 12 / 13 / 14 / 16 / 17 / 18 / 19 / 20 / 21 | 全过：5 → 10/10、7 → 12/12、8 → 10/10、11 → 13/13、12 → 18/18、13 → 18/18、14 → 9/9、16 → 12/12、17 → 28/28、18 → 29/29、19 → 26/26、20 → 14/14、21 → 18/18；stage2/3/4/9/10 只打印 rc=0 |
| `tools/list` | 49（文件级 38 + GUI 11）；`ROBEI_MCP_GUI=0` 时 38 |

**未测试 / 注意**

- GUI 段**第一次**跑时 `robei_gui_open_project` / `open_model` / `compile` 报了
  `RuntimeError: There is no active desktop required for moving mouse cursor!`
  （stage5 7/10、stage7 9/12、stage8 5/9、stage12 13/18、stage16 9/12）。
  同一台机器隔几分钟重跑，五段全过（10/10、12/12、10/10、18/18、12/12）。这是控制台
  会话被锁 / 没有活动输入桌面的环境状态，不是代码回归：只有鼠标类操作受影响，UIA 读窗口
  （`robei_gui_status` / `output`）和 `robei_gui_synthesis` 一直正常。
- 没有上板；没有跑 `check_wheel.py`、`setup.py --apply`、`verify_1_0_2.py`。
- 1.3 待办：画布总线位选、实例参数（Robei GUI 保存样本）、嵌套工程名、产物落到工程目录、
  PNG 内联返回、空骨架模型。

### 1.2.0：FPGA 后端到 .bit（2026-10-24）

工具面 **48 个（文件级 37 + GUI 11）**，`ROBEI_MCP_GUI=0` 时 37 个。

**M0 取证：Robei 自己执行的命令行**（用 Win32 进程快照轮询抓 argv，不是从文档推断）

```text
Synthesis  yosys -p "read_verilog <build>/light.v; synth_xilinx -flatten -arch xc7
                   -abc9 -nodsp; write_json -noscopeinfo <build>/<const>.json"
Route      Roplace.exe --chipdb C:/ProgramData/Robei/xilinx/chipdb/<part>-1.bin
                       --xdc <build>/<const>.xdc --json <build>/<const>.json
                       --write <build>/<const>_routed.json --fasm <build>/<const>.fasm
Package    fasm2frames.exe --db-root C:/ProgramData/Robei/xilinx/<family>/ --part <part>
                           <build>/<const>.fasm <build>/<const>.frame
           xc7frames2bit.exe --part_file <db>/<family>/<part>/part.yaml
                             --part_name <part> --frm_file <frame> --output_file <bit>
```

- 变体 1（项目没先 `Save`+`Compile`）：同一条 Synthesis 命令里**没有** `read_verilog`，
  等于综合空设计；先保存再编译才会读到 `build/light.v`。
- 变体 2（`.const` 的 `Family`/`Device` 为空）：`--chipdb .../chipdb/-1.bin`、
  `--db-root C:/ProgramData/Robei/xilinx/`、`--part -1`，随后 Route 报
  `Unable to read chipdb .../-1.bin`、Package 报 `mapping\devices.yaml does not exist`。
- 中间产物顺序：`<const>.json` → `_routed.json` + `.fasm` → `.frame` → `.bit`；
  Compile 另外产出 `build/<model>.v`、`build/filelist.vf`、`build/<const>.xdc`。

**器件库映射**（`C:\ProgramData\Robei\xilinx`）

| part | chipdb | part.yaml | buildable |
|---|---|---|---|
| xc7a100tcsg324-1 | 缺 | artix7/ | false |
| xc7a100tfgg484-1 | ✓ | artix7/ | true |
| xc7a200tfbg484-1 | ✓ | artix7/ | true |
| xc7a50tfgg484-1 | ✓ | artix7/ | true |
| xc7z020clg400-1 | ✓ | zynq7/ | true |
| xc7z020clg484-1 | ✓ | zynq7/ | true |

`Robei.exe` 的器件字符串只有这 5 个（`artix7` / `zynq7` + 5 个 device），csg324 不在其中。

**端到端产物**（`examples/fpga_demo` → 复制进 `workspace/fpga_demo` 后跑
`build_bitstream(part="xc7a50tfgg484-1")`）：

```text
synth 1.64s / route 0.84s / package.frames 1.62s / package.bit 0.06s，四段 rc=0
bit      workspace/fpga_demo/build/light_constrain.bit
size     2,192,167 bytes
sha256   41da154cc4ccd66f5eb4edb3d32e7ba2c0bf66d7e7ea37649cf591887950e14c
fasm     5,978 bytes
frame    5,585,802 bytes
资源     LUT 4 / FF 0 / CARRY 0 / BRAM 0 / DSP 0 / IO 8
```

**`.bit` 不能逐字节比对（1.2.1 更正）**：头部是 xc7frames2bit 写的 ASCII 字段——
`a` = `.frame` 的**绝对路径**、`b` = 器件、`c` = 日期、`d` = 时间——然后才是同步字
`0xAA995566` 和帧数据。所以同一工程的 `.bit`：

- **大小随路径长度变**：同一工程放在 `workspace/fpga_demo_1201` 时 xc7a50tfgg484-1 是
  2,192,172 B、xc7a100tfgg484-1 是 3,825,949 B；换一个更短的工程目录就整体小几个字节
  （1.2.0 记的 2,192,167 B 只对应当时的目录名）。
- **差异字节在头部**：路径字段（如偏移 46 附近）和 `d` 时间戳（约 142–144），具体偏移随
  路径长度浮动，不能写死。
- 从同步字起算的 `bit_payload_sha256` 在两次构建里相同（实测 `46d75f4c…c44b547`，
  偏移 208；换器件时偏移 209、payload 自然不同）。比对请用 `fasm` / `frame` 或这个值。

`json` 也别当判据：同一环境下连跑两次字节一致，但换一份 yosys 安装（二进制 md5 相同）
会写出字节数不同的 json（9,382,162 B vs 9,594,583 B），拓扑与 fasm 相同。

`E:\Robei\bin` 与 `D:\robei-lab\robei\bin` 里的 yosys / yosys-abc / Roplace /
fasm2frames / xc7frames2bit **md5 完全一致**；同一份 json 喂两边的 Roplace，输出 `.fasm`
逐字节一致（`09abcb0f2a94820b4f9da8ba7a1e1173`，6,230 B）。反过来，同一份输入两次 yosys
会写出**字节数不同**的 json（9,382,162 vs 9,594,583），但拓扑相同、fasm 相同，所以不能拿
json 大小当差异判据。

**引脚**：`const_demo` 的 csg324 球号在 fgg484 上不存在（Roplace 报
`device does not have a pin named 'M14'`）。用逐引脚排除找到可用组合并写进示例：
`swt = A1, A14, A15, A16`、`led = A20, A19, A13, A18`。

**超时截断实测**：`build_bitstream(..., timeout_s=900)` 返回

```json
"timeout": {"requested_s": 900, "effective_s": 120, "max_timeout_s": 120, "clamped": true}
```

并在 `warnings` 里带 `timeout_s was clamped to max_timeout_s=120`。

**测试**

| 项 | 结果 |
|---|---|
| `pytest -q tests` | 137 passed |
| `tests/portable_core.py` | 16 / 16 |
| stage2 / stage3 / stage4 / stage5 | 通过 / 通过 / 通过 / 10 / 10 |
| stage7 / stage8 / stage9 / stage10 | 12/12 / 10/10 / 通过 / 通过 |
| stage11 / stage12 / stage13 / stage14 | 13/13 / 18/18 / 18/18 / 9/9 |
| stage16 / stage17 / stage18 / stage19 | 12/12 / 28/28 / 29/29 / 26/26 |
| stage20 / stage21 | 14/14 / 18/18 |

stage3 / stage4 / stage5 里"工具清单必须完全一致"的断言按增量更新：stage3/4 只**追加**
4 个新工具名；stage5 的 GUI 工具数从 10 改成 11——这是 1.0.3 加 `robei_gui_screenshot`
时就漏改的旧断言（`da37ea3` 上 `GUI_TOOLS` 已经是 11 项，stage5 仍要求 10，属既有失败）。

**未测试：GUI 侧 .bit 对比。** IDE 打开工程会把 `.const` 根节点的 `Family` / `Device`
清空（写进去的值在 `File > Save` 后变成 `Device="" Family=""`），Route 因此报
`light_constrain has no Family configured.` / `has no Device configured.` 并去找
`chipdb/-1.bin`；`HKCU`/`HKLM\Software\Robei` 不存在，工程目录里也没有任何 cfg/ini/json
侧车文件，器件选择只活在 IDE 进程内存里。工具链二进制与 IDE 同 md5、同 json 出的 fasm
逐字节一致，所以这条差异不属于工具链，本版按已知限制结案。

### 1.2.1：1.2.0 的四个实测问题（2026-10-24）

工具面仍是 **48 个（文件级 37 + GUI 11）**，`ROBEI_MCP_GUI=0` 时 37 个。

- **默认超时**：`build_bitstream` 600 → 120、`synthesize_project` 300 → 120（都等于
  `ROBEI_MAX_TIMEOUT_S`）。以前不传参数必然 `clamped:true` + warning；现在默认调用
  `timeout = {requested_s: 120, effective_s: 120, clamped: false}`、`warnings` 里没有截断提示。
- **键名拼写**：`tool_paths()` 的键名多打了一个 l，已改成 `roplace`（返回、tool-reference、
  pytest 同步）。在 `robei_mcp`、`tests`、`docs/tool-reference.md` 上 grep 旧拼写是 0 命中
  （CHANGELOG 与本节各留一处历史说明）。
- **`.bit` 头部**：如上文更正，大小随 `.frame` 路径长度变、字节随时间变；新增
  `bit_payload_sha256`（从同步字 `0xAA995566` 起算）。`examples/fpga_demo` 连跑两次：
  整文件 sha256 `e8f9aef784a7fdd5c4bc8ede3d2e3fa6bedb41ada7b340995c4e1ff01557c685` 与
  `50275fe750884dd18d849ea2e8b2beaf00dde11857ae09e8e230ad09ebd04ebd` 不同，
  `bit_payload_sha256` 两次都是 `46d75f4cc52cd78849b7a6d3a895e9ab6edc542a32b1dfebea5c4cf6cc44b547`
  （offset 203；工程目录名换成 `fpga_demo_1201` 时 offset 变 208，payload 仍相同）。
  `fasm` = `107fcafa125b3d8c2df2ff21a8da3bfbee3060a4aeefa7a7cad709646b43d9b2`、
  `frame` = `e27e1ceb842af553fa997f60a298b7bdbcc9f1f3048ffddefef751cdb2365ec5`，两次一致。
- **json**：不是稳定判据（见上）。

回归：`pytest` **139 项全过**（test_1_1 27 / test_1_2 10 / test_fpga 15 / test_portable 5 /
test_regressions 50 / test_tool_coverage 32）、`portable_core` 16/16、stage2–21 全过
（stage5/20 走 Session 1），工具数 48（GUI 11）/ 37（`ROBEI_MCP_GUI=0`）。

### 1.1.0：元件库与画布增量编辑（2026-10-24）

工具面 **44 个（文件级 33 + GUI 11）**，`ROBEI_MCP_GUI=0` 时 33 个。

**交付前完整性检查**（`git diff --name-only master..HEAD` 的 15 个文件）：

```text
CHANGELOG.md 23253B  README.md 13361B  docs/tool-reference.md 29137B
pyproject.toml 850B  robei_mcp/__init__.py 22B  errors.py 3365B
model.py 20583B  server_app.py 25333B  tools.py 106772B  verilog.py 16853B
tests/stage21_packaging.py 3424B  tests/stage3_cases.py 8419B
tests/stage4_cases.py 14974B  tests/test_1_1.py 22380B
tests/test_tool_coverage.py 15145B
→ 无 NUL 字节、全部 UTF-8 可解码、结尾单换行、无多余空行；
  python -m compileall -q robei_mcp tests 通过
```

（这条检查是因为本版上传通道出过两次静默损坏：一次把 `tools.py` 尾部写进几行垃圾，
一次把 `CHANGELOG.md` 写成 1.35 MB 全 NUL。现在上传固定走
「写 `D:/Temp` → 校验 sha256 → copy 到目标 → 再校验」。）

**M1 元件库**：`list_library_models` 扫 `C:\ProgramData\Robei` 的 24 个 ToolBox 模型，
逐个导出后 `iverilog -g2012` 编译，**24/24 通过**；`name_matches_module=false` 的两个是
`constant.model`（模块名 `register`）与 `mod_n_counter.model`（`ring_counter`）。
`import_library_model` 导入 `andgate` 后：登记 `.proj`（有备份）、副本 lint 通过；
不带 `as_name` 导入 `constant` 被拒并提示 `as_name='register'`，带上后文件与根节点
`Name` 一起改名。

**M2 画布编辑**：`edit_robei_canvas` 从零搭 4 位链路（`chain`，u1/u2 都是 `buf4`）：

```text
ops: add u1 / add u2 / connect a→u1.a(a_n) / connect u1.y→u2.a(mid,4) / connect u2.y→y(y_n)
预览: wire [3:0] mid;   buf4 u1(   buf4 u2(
仿真: A=1011 Y=1011 MATCH   A=0110 Y=0110 MATCH
```

原子性：`add_instance` 成功 + `connect` 打到不存在端口时，整批不落盘（文件字节不变）。
位宽：4 位端口接 1 位子模块端口返回 `code=width_mismatch`，消息带 `4 bit` / `1 bit`。
`set_robei_code` 对结构级模型返回 `not_behavioral`；`set_robei_parameter` 把 `LEN` 从 2 改到 8 后
`width_info` 给出 `LEN-1:0 → 8-1:0, width=8, resolved=true`，其它参数注释保留，
未知名字返回 `unknown_parameter`。

**Robei 自己 Compile**（工程 `workspace/verify_1_1_a`，跑完即删）：导入的 `andgate` 与
编辑过的 `chain` 各 Compile 一次，日志都是 `Check complete with 0 warnings.` +
`Compile done.`；Robei 生成的 `build/chain.v` 与我们导出一致：

```verilog
wire [3:0] mid;
buf4 u1(.a(a), .y(mid));
buf4 u2(.a(mid), .y(y));
```

截图（3200×2000，留在 `workspace/gui_shots/`，该目录 gitignore）：
`1-1-a-andgate-20261024-155633.png` 1,032,217 字节、
`1-1-a-chain-20261024-155642.png` 1,046,331 字节。

**wire_meta 修复**：同一条 net 先声明 `Datasize="1"` 再声明 `Datasize="4"` 时，
旧代码用 `width_of`（把 `"4"` 读成 1 位）比较，先到的那条赢，导出成 `wire mid;`。
改用 `verilog.robei_width()` 后导出 `wire [3:0] mid;`；该测试在旧 `model.py` 上失败、
在新 `model.py` 上通过。

**Windows 回归**：`pytest -q tests` **114 passed**（`test_portable` 5 + `test_regressions` 50 +
`test_1_1` 27 + `test_tool_coverage` 32）；`portable_core` 16/16；stage3、stage4 OK；
stage7 12/12；stage9、stage10 退出码 0；stage11 13/13；stage12 18/18；stage13 18/18；
stage14 9/9；stage17 28/28；stage19 26/26；stage21 18/18（33 文件级 / 44 总计 / GUI 11）。

### 1.0.4：Parameters 注释解析热修（2026-10-24）

**根因**：`parse_parameter_text_checked` 先按 `;` 切分、再剥 `//` 注释。`START = 0; //a` 的
注释把下一行的 `DUTY = 50` 一起卷进注释，只剩第一个参数。这段逻辑从 stage4 就在，
1.0.3 的实例参数覆盖复用它之后才在导出路径上暴露——Robei 自带的 24 个 ToolBox 模型里有
10 个是多参数，且全部带 `//` 注释。

**导出对比**（`C:\ProgramData\Robei\clock.model`，真实 ToolBox 模型，只读复制后导出）：

```text
BEFORE  parameter START = 0
        → iverilog: Unable to bind wire/reg/memory `DUTY' in `clock'
AFTER   parameter START = 0,  // initial_state
        parameter DUTY = 50  // high_voltage/period
        → iverilog ok / yosys ok / yosys_check ok
          （Verilator 仍报 NEEDTIMINGOPT：模型正文有 `#(100-DUTY)`，与参数解析无关）
```

修复时暴露的第二个缺陷：注释变成真注释后，原来的 `",\n".join(...)` 把分隔逗号写在
`// comment` 之后，被注释吃掉导致 iverilog 语法错误；现在逗号写在注释之前。

**24 个 ToolBox 模型：导出 + `iverilog -g2012` 编译**

| 模型 | 1.0.3 | 1.0.4 | 模型 | 1.0.3 | 1.0.4 |
|---|---|---|---|---|---|
| addsub | OK | OK | mod_n_counter | OK | OK |
| andgate | OK | OK | mux | OK | OK |
| bin2gray | OK | OK | notgate | OK | OK |
| clock | FAIL `DUTY` 未绑定 | OK | orgate | OK | OK |
| compare | OK | OK | por | OK | OK |
| constant | FAIL `DEFAULT` 未绑定 | OK | rightshift | FAIL `SHIFT_BITS` 未绑定 | OK |
| counter | FAIL `MAXIMUM` 未绑定 | OK | ring_counter | FAIL `N` 未绑定 | OK |
| dff | OK | OK | selectbits | FAIL `POSITION` 未绑定 | OK |
| dffce | OK | OK | shift_register | OK | OK |
| dual_port_ram | FAIL `DATA_WIDTH` 未绑定 | OK | single_port_ram | FAIL `DATA_WIDTH` 未绑定 | OK |
| jk_trigger | OK（无 Parameters） | OK | xorgate | OK | OK |
| leftshift | FAIL `SHIFT_BITS` 未绑定 | OK | merge | FAIL `LENB` 未绑定 | OK |

合计 **14/24 → 24/24**；10 个失败全部由注释截断引起，没有一条与注释无关的遗留失败
（`counter`/`addsub`/`mux` 的 `LEN = 0` 会生成 `[-1:0]`，`iverilog -g2012` 接受，无需改库模型）。
源模型的 mtime 仍是 2025-04，未被写入。

**这一版新增的回归**（`tests/test_regressions.py` 50 项，含 7 项新增）：

- `test_parameter_text_keeps_every_line_with_trailing_comment`（B1 原始复现用例）
- `test_parameter_comment_may_contain_semicolon`
- `test_parameter_text_without_comments_is_unchanged`
- `test_bad_root_parameters_warn_and_emit_no_parameter`（根级坏 chunk → `parameter_bad` + warnings）
- `test_instance_parameters_with_comments_still_emit_override`（`"W = 4; // four"` → `#(.W(4))`）
- `test_root_parameters_with_comments_keep_the_comma`（注释不吃分隔逗号）
- `test_toolbox_models_export_and_compile`（24 个 ToolBox 模型逐个导出 + 编译，缺目录/iverilog 时跳过）

**Windows 回归**：`pytest -q tests` **55 passed**（5 portable + 50 regressions）；
`portable_core` **16 passed / 0 failed / 0 skipped**；stage3、stage4 OK；stage7 12/12；
stage9、stage10 退出码 0；stage11 13/13；stage12 18/18；stage13 18/18；stage14 9/9；
stage17 28/28；stage19 26/26（含 GUI Compile + Synthesis）；stage21 13/13。
工具数不变：39（GUI 11），`ROBEI_MCP_GUI=0` 时 28。

### 1.0.3：层次化导出与接口补齐（2026-10-24）

**P0 实机证据**（Windows，`workspace/build/evidence_1_0_3` 的临时工程，跑完即删）：

```text
BEFORE  wire mid;        → iverilog: Port 1 (a) of buf4 expects 4 bits, got 1
                         → Y=0001, MISMATCH expected 1011
AFTER   wire [3:0] mid;  → Y=1011, MATCH（无编译告警）
```

即：画布 `<Wire Datasize="4">` 丢失后整条 4 位链路退化成 1 位。修复后内部 wire 按
Datasize 生成范围，实例参数非空且与子模型默认值不同时生成 `#(.W(4))`。

**取证边界**：`C:\ProgramData\Robei`（24 个 ToolBox 模型）、仓库 examples/robei_samples
与 Robei 安装目录共 **67 个 `.model`**，其中嵌套 `<Module Parameters>` 非空的样本 **0 个**。
因此“Robei 把实例参数覆盖写成什么格式”没有实机样本可依；实现按模型根节点同一种
`NAME = value;` 语法解析，解析为空就完全不输出（不会猜测写坏文件）。
`<Wire Datasize>` 字段本身在样本里存在（全部为 `1`）。

**这一版新增/修改的回归**（`tests/test_regressions.py` 42 项，含 9 项新增）：

- `test_canvas_bus_chain_keeps_width_and_simulates`（4 位链路真的跑 iverilog，断言 `Y=1011`）
- `test_canvas_mixes_single_bit_and_bus_wires`（1 位与总线混合）
- `test_instance_parameter_overrides_are_emitted`（覆盖 `#(.W(4))`，等于默认值时不输出）
- `test_path_escape_has_its_own_code`
- `test_bare_workspace_resolves_to_the_root`
- `test_model_roots_are_deduplicated`
- `test_modules_without_ports_parse_consistently`
- `test_list_projects_skips_qa_trees`
- `test_const_drive_pull_and_voltage_reach_xdc`
- `test_malformed_instance_parameters_warn_and_emit_nothing`（写坏的 Parameters 不产生 `#(`，`sync` 返回 warnings）

**Windows 回归**：`pytest -q tests` 48 passed；`portable_core` 16/16；stage3/4 OK；
stage7 12/12；stage9、stage10 退出码 0；stage11 13/13；stage12 18/18；stage13 18/18；
stage14 9/9；stage17 28/28；stage19 26/26（含 GUI Compile + Synthesis）；stage21 13/13。
工具数 39（GUI 11），`ROBEI_MCP_GUI=0` 时 28。

**GUI 截图实测**：打开 `workspace/robei_samples/two_level/two_level.proj` 后调用
`robei_gui_screenshot`，得到
`C:\dev\robei-mcp\workspace\gui_shots\review-1-0-3-20261024-144138.png`，
3200×2000、953,568 字节，`transport=schtasks`；跑完 `git status` 仍只有 5 个 QA 未跟踪目录。

**path_escape 实测**：`read_robei_model("C:\\Windows\\win.ini")` 返回
`code=path_escape` 与 hint；`simulate` 引用 `C:/Windows/win.ini` 返回
`code=path_rejected` + 顶层 `rejected` 列表。

### 1.0.2：QA 回归修复（2026-10-24）

上一轮 QA 在 Windows 实机上把 38 个工具逐个打过一遍，留下 16 组问题。这一版全部修掉，
并用两个脚本把结论钉住：`tests/test_regressions.py`（本地，22 项）和 `verify_1_0_2.py`
（Windows 实机，38 项端到端）。

**实机结果：38/38 PASS**（脚本与结果在 `C:\dev\robei-mcp\workspace\_qa_runs\qa_20261023122612\verify_1_0_2\`）。
上一轮同样的脚本是 34/37，三项失败的性质各不相同，值得单独记：

| 用例 | 上一轮为什么红 | 性质 | 这一版 |
| --- | --- | --- | --- |
| `RUNTIME-VERSION` | 断言 `mcp_version == "1.0.2"`，但那个字段是 MCP SDK 的 `2.3.0` | **用例写错** | `runtime_status` 新增 `server_version`，用例改查它；`mcp_version` 保留原语义 |
| `TRUTH-16` | `Datasize="4"` 导出成 `[4-1:0]`，`width_of` 只认字面量 → 4 位输入只出 2 行 | **真 bug** | 导出折成 `[3:0]`；`width_of` 能算纯数字常量表达式 |
| `SEC-INCLUDE` | 越界 canary 建在 workspace 里，严格扫描放行是对的 | **夹具写错** | canary 改到 `tempfile.mkdtemp()`，用完删掉 |

顺带抓到修复过程中暴露的第三个真 bug：`truth_table` 按**端口个数**移位而不是位数，
混合位宽（`[1:0] a` + `[3:0] b`）的枚举顺序是乱的。新增 `TRUTH-MIXED`（64 行、第二行
`b=0001 → y=000100`）守着。

实机覆盖到的具体断言（全部 PASS）：

- **工具面**：38 个工具；干净 venv 里没装 `pywinauto` 时只有 28 个（`GUI-GATE`）。
- **打包**：wheel 构建、`Provides-Extra: gui`、entry_points 指向 `__main__:main`、LICENSE 入包、
  装进干净 venv 后 `robei-mcp.exe` 读 stdin EOF 退出码 0。
- **解析**：一行 non-ANSI、同行双实例 import（0 悬空端口）、ANSI 简写、`[4-1:0]`、混合位宽。
- **波形**：同文件/选信号/值不同/缺信号/位宽不同/timescale 不同/`max_time` 不同/无共同信号
  八种组合的判决都符合预期；空 VCD 与纯文本 VCD 都被拒。
- **安全**：`$readmemh(PATH)` 与 `` `include `` 传递越界都停在 `stage="security_scan"`，不启动编译器。
- **约束**：重复引脚被拒（`G15 is assigned to both a[0] and a[1]`）；XDC 两种引号风格各自正确。
- **进度**：`0/1 → 0/2 → 1/2 → 2/2 → 2/2`，单调不减。
- **备份**：同一秒两次改模型，两个备份文件名不同且各自 SHA256 与 `sha256_before` 一致。
- **GUI**：10 模型工程里找到并激活 `qa_light_const`（树 7 项）→ Save → Compile（`Finished to
  generate constrain file`）→ Synthesis（`completed: true`，0 error）→ Close。
- **非 ASCII 路径**：`...\workspace\_qa_runs\...\fixtures\路径\bus4.v` 转成
  `C:\dev\ROBEI-~1\WORKSP~1\...\E4D8~1\bus4.v`，iverilog / Yosys / Verilator 三路 lint 全过。

同一套改动在本地（macOS，仅 Icarus 13.0，无 Yosys/Verilator/Robei）跑 `pytest -q tests`：
27 passed（`test_portable` 5 + `test_regressions` 22）。

**这一版仍然没验证的东西**（别当成已通过）：`Build→Route`、`Build→Package`、bitstream、
真实板卡下载；安全扫描是编译输入检查，不是 OS 沙箱；GUI 需要 `[gui]` extra 且在 Session 1。

**复审（2026-10-24 第二轮）**：外部 QA 在真实 Windows 上重跑 1.0.2，报回 6 类问题。
逐条复现后修复：跨 timescale 的 `max_time` 误判（1ns `#20` vs 1ps `#20000`）、安全拒绝时
顶层缺 `rejected`、`workspace/...` 路径被套成两层、点名不存在的信号抛 `KeyError`、
`const_eval` 除法用 floor、参数化位宽静默按 1 位。第二轮又补了「新建路径带 `workspace/` 前缀」
「`Workspace\` 反斜杠写法」「`workspace/../x` 逃逸」「`workspace/` 子目录歧义」
「一行写的参数化模块头」5 项。回归共新增 11 项
（cross-timescale / unknown-signal / allow_extra_signals / path-prefix / Verilog 取整 / 参数化位宽 /
新建前缀 / 反斜杠 / 逃逸 / 歧义 / 参数化模块头），
本地 `pytest -q tests` 38 passed、`portable_core` 14 passed + 1 skipped（本机无 yosys）。
Windows 复跑：`pytest -q tests` 38 passed、`portable_core` 16/16、`stage19_constraints` 26/26、
`stage5_gui_cases` 10/10、`stage20_gui_robustness` 14/14、`stage21_packaging` 13/13、
`verify_1_0_2.py` 42/42（新增 COMPARE-FS-MAXTIME / COMPARE-UNKNOWN / PATH-PREFIX / SEC-TOPREJECT）。
`stage19_constraints` 的 XDC 期望同步到标准 Tcl 写法，并保留一条"Robei 自身仍写 legacy"的对照。

### 0.11：精简工具面（P0）

47 → 38 个工具。移除的都是「只有本机 / 只有课堂场景才用得上」的部分：

| 移除 | 原因 | 替代 |
| --- | --- | --- |
| `report_lab` | 唯一中文模板，绑死中国数电实验报告 | `simulate_project` + `read_waveform` + `render_waveform` |
| `robei_gui_experiment` | 纯编排，与独立工具重复 | 按需组合 `open_project` → `open_model` → `compile` |
| `robei_gui_run` | 仅对 `Type="test"` 有效 | `simulate_project`（同一套 iverilog/vvp，无需桌面） |
| `robei_gui_wave` / `robei_gui_wave_load` | 打开 Wave dock 会让 UIA 树塌掉（`widgets 133 → 0`） | `render_waveform` / `read_waveform` |
| `robei_gui_code_read` / `robei_gui_code_write` | 与文件级读写重复，且绕过备份与 XML 校验 | `read_robei_model` / `create_robei_model` |
| `robei_gui_refresh` | `open_project` 已自动重载 | — |
| `robei_gui_health` | 并入 `robei_gui_status(probe=True)` | `robei_gui_status` |

打包也修了：GUI 驱动（`ui.py` / `driver.py` / `job.py` / `launcher.py`）搬进
`robei_mcp/gui_agent/`，wheel 现在带得上（0.11.0 的 wheel 只有 24 个文件、完全没有
`tools/gui`，`pip install` 后 10 个 GUI 工具必然报"找不到脚本"）。控制文件（job / args / lock）
改放到 `workspace/gui_agent/`，不再写进安装目录；装成 wheel 时 workspace 默认落在
`%LOCALAPPDATA%\robei-mcp\workspace`。`ROBEI_MCP_GUI=0` 可以在 Windows 上强制关掉 GUI 面，
此时 `tools/list` 只有 28 个工具。

配置也改了：`robei_home` 和四个工具路径现在自动发现（`PATH`、`%ProgramFiles%\Robei`、
`<盘符>:\Robei`、`C:\msys64\{ucrt64,mingw64}\bin`），不再假定 `E:\Robei`。
`runtime_status().config.sources` 会报告每个键是 `env` / `toml` / `discovered` / `default` 哪来的。

下面「已验证（阶段 N）」各块是历史记录，其中提到的已移除工具以本节为准。

已验证（阶段 2/3）：

- MCP stdio 协议、4 位计数器 lint/simulate/waveform 闭环
- `iverilog -Wall` 警告提取、Yosys `read_verilog + synth -top` 可综合性检查
- 语法错误返回给 MCP 调用方
- 1 秒超时能终止 `vvp`，无残留 `vvp.exe`
- Robei `.model` 只读解析，`mux.model` 前后 SHA256 不变
- Mac 通过 `ssh my-win` 使用远端 stdio；stdout 只有 JSON-RPC，stderr 为空
- Claude Code 实际调用 `mcp__robei_remote__runtime_status`

已验证（阶段 4，`tests/stage4_cases.py` 全绿）：

- Verilator `5.036` 已安装并接入 lint 路径（`--lint-only -Wall -Wno-fatal`）
- `warning_width_unused.v` 能报出 `unused_signal`（Yosys `opt_clean` 前后网表 diff）
- `read_robei_project` 解析 `.proj` + 四类目录 + 每个 `.model`，未知节点原样保留；`.proj` SHA256 前后一致
- `export_robei_verilog`：`mux.model` 导出 `mux_len1_stage4.v` 并自动 lint 通过，源 `.model` SHA256 不变
- `generate_testbench`：`counter4` 生成 TB 后 `simulate` 跑通（`SIM start` / `SIM end: 1120000` ps）
- `find_events`：`count == 4'b1111` 返回 `t=265000` 等 6 次事件；非法条件被拒绝（不使用 `eval`）
- `render_waveform`：生成 25,991 字节 PNG
- 路径逃逸（`` `include ``、`$fopen`、`$readmemh` 指向工作区外）在编译前被拒绝；工作区内路径正常放行
- 覆盖写拒绝：export / testbench / PNG 同名第二次调用返回 `FileExistsError`

已验证（阶段 9/10，Robei 原生闭环，`tests/stage9_native_run.py` + `tests/stage10_counter_native.py`）：

- 纯 Python 生成的 `test/<name>.test`（`Type="test"`、`Timescale="1ns/1ns"`、Verilog 写在 `Code` 属性里）被 Robei 接受，不需要 GUI 的 New 对话框
- `sync_robei_verilog` 把每个 `.model` 导出成 `<工程>/verilog/<model>.v`；Robei 用 `iverilog -o build/<test> build/filelist.vf -y <工程>/verilog` 编译，子模块因此能被解析
- `robei_gui_experiment` 一次跑完「打开工程 → 打开 test 模型 → Compile → Run」，返回 Robei 自己的输出和 `build/<test>.vcd`
- 与门实验：`t=40 a=1 b=1 y=1`、`$finish called at 50 (1ns)`；VCD 由 `read_waveform` 读到 6 个信号（`y/a/b` 与 `dut.y/a/b`），`find_events(y == 1'b1)` 命中 `t=30`
- 4 位计数器：`t=25 count=0001 … t=165 count=1111 … t=255 count=1000`，`$finish called at 260`；`find_events(count == 4'b1111)` 命中 `t=155`，PNG 落在 `workspace/build/waveforms/`
- 注册型输出端口必须写成 `datatype="reg"`（否则导出 `output wire` 后报 `count is not a valid l-value`），这条是实测出来的
- `read_robei_project` 现在同时解析 `model/*.model` 与 `test/*.test`（含 `code`、`timescale`）

已验证（阶段 12，`tests/stage12_ops.py`，17/17 PASS，全部走 stdio MCP）：

- `list_projects`：一次列出 8 个工程及其 models/tests/verilog/VCD
- `robei_gui_health`：正常时 `widgets=133`、`menu_items=9`；打开 Wave dock 后
  `widgets=0` 并返回原因
- `robei_gui_experiment(heal=True)`：检测到 UIA 静默会自动重启 Robei 再跑，实测
  `heal: "restarting Robei because the UIA bridge is silent"`，随后 Compile+Run 正常
- `robei_gui_experiment(plot=True)`：一次调用返回 `steps.compile/run` + VCD + PNG
- `robei_gui_compile_all`：`two_level` 两个模型 2/2 通过，层次化 `top_two` 靠
  `iverilog -y <工程>/verilog` 找到 `and2`

已验证（阶段 13，`tests/stage13_doctor.py`，18/18 PASS）：

- `robei_doctor` 一项一项报告 python / venv / mcp / matplotlib / workspace 可写 /
  Robei / iverilog / vvp / yosys / verilator / GUI 任务 / 环境变量覆盖
- `deep=true` 会真的编译并运行一个两行 testbench，输出 `DOCTOR_OK` 才算通过
- 负向：把 `ROBEI_HOME` 指到不存在的目录，doctor 返回 `ok=false`、失败项
  `['robei_exe','iverilog','vvp','yosys']`，并给出 `ROBEI_HOME` 的修复提示
- `tools\setup.py --check` 退出码 0；`--apply` 重复执行全部 `skip`
- `.const` 现在会被解析：真机 `File→New→const` 生成的文件是
  `<Module Type="const" Vendor="AMD" Device="" Family="" Code="" .../>`，
  与 `.model`/`.test` 同一个 `<Module>` 根，因此复用同一个解析器

已验证（阶段 14，`tests/stage14_headless.py`，9/9 PASS）：

- `simulate_project` 不需要 IDE：Robei 关掉的情况下，用同一份 `.test` 的 Code +
  每个 `.model` 导出出的 Verilog，自己编译跑出 26 行 `t=… count=…`
- 期望序列是**按 testbench 语义独立推导**的（10 ns 周期、t>12 释放复位、4 位回绕），
  与真实输出逐行相等；`find_events(count == 4'b1111)` 同样是 `t=155`，和 IDE 内跑的一致
- `render=true` 同时产出 PNG；未知 test 名会返回可用列表

已验证（阶段 15，可移植性 + CI）：

- `config.py` 不再硬依赖 `ctypes.wintypes`（非 Windows 条件导入），工具路径按平台探测
  （Windows 走 `<ROBEI_HOME>\bin\*.exe`，POSIX 走 `/usr/bin`、`/usr/local/bin`、
  `/opt/homebrew/bin`、`/opt/local/bin`）
- macOS 上 `git clone` 后 `from robei_mcp import tools` 成功，
  `runtime_status()` 优雅返回 `ok=false` + 各工具 `exists=false`（不再 ImportError）
- `tests/portable_core.py`：15/15 PASS（Windows，真实 iverilog/vvp/yosys），覆盖
  simulate / VCD / generate_testbench / model / project / simulate_project / lint
- macOS（Homebrew iverilog 13.0，Python 3.14）：同一份 `portable_core.py` 跑出
  **14 passed, 1 skipped**（skipped = 本机没装 yosys）
- `.github/workflows/ci.yml`：ubuntu-latest + `apt install iverilog yosys` 跑同一份
  `portable_core.py`（不需要 Robei、不需要 GUI、不需要 Windows）——首次运行就抓到两个
  真实问题，修完后 **CI 绿**（run `39747769496`, commit `d54439d`）
- 注意：需要 **Python 3.10+**（`Path.write_text(newline=...)` 是 3.10 才有的参数；
  macOS 自带的 3.9 会在 `generate_testbench` 处报 TypeError）

已验证（阶段 15，`tests/stage15_report.py`，15/15 PASS，36 个工具）：

- `report_lab`：无 IDE 跑一次实验 → `workspace/reports/<工程>-<test>-<时间>.md`，
  含编译结果、`$display` 原文、信号表、每信号前 12 次跳变、内嵌波形 PNG、5 项自动检查
- `compare_waveforms`：两份 VCD 逐信号比对；同源两份 0 差异，改动一个值后精确报出
  `t=155 golden=1111 dut=1110`（只报这一个信号）
- `workspace/logs/mcp-YYYYMMDD.log`：每次子进程 / 每次 GUI 往返一行 JSON
  （`tool` / `rc` / `ms` / `timeout` / `ok`），出问题能复盘
- GUI 加互斥锁 `workspace/gui_agent/agent.lock`：第二个并发调用立刻返回
  `another GUI job is still running`，不再互相覆盖 job 文件后一起超时
- 工程化：`pyproject.toml`（0.3.0，`requires-python>=3.10`，可选 `dev` = pytest），
  `tests/test_portable.py` 用 pytest 参数化跑 5 个子系统

已验证（阶段 16，`tests/stage16_canvas.py`，12/12 PASS，37 个工具）：

- 真实画布 schema 来自 GitHub 公开工程（`wwkkww1983/uart`，38 个 `.model`：183 个
  `<Wire>`、81 个嵌套 `<Module>`）——仓里没有 LICENSE，因此**只取文件格式，不取任何代码**
- 画布格式：`<Module Type="module">` 里放**嵌套** `<Module Type="model" Class="<被引用模型名>"
  File="Current/<模型>.model">` 表示摆好的实例，`<Wire Name From To>` 表示连线，
  端点语法 `<owner>#<instance>><port>`（实例）/ `<owner>><port>`（顶层端口）；
  Robei 里**没有** `<Instance>` 标签
- `create_robei_canvas_model` 直接写这种 XML（不点画布）→ Robei 打开工程并 Compile，
  自己生成 `//---Module instantiation---` + `and2 u1( .a(a), .b(b), .y(t1) );`
- headless 仿真验证连线电气正确：`y = a&b&c`（两个 `and2` 串联），`$strobe` 真值表 8 行全对
- `read_robei_model` / `read_robei_project` 现在解析 `instances` / `wires` / `netlist`
  （`nets` + `dangling_instance_ports`），`is_canvas` 标识画布级模型

已验证（阶段 17，协议与输入加固，`tests/stage17_hardening.py`，28/28 PASS）：

- 37 个工具全部带 MCP annotations：`read_waveform` 是 `readOnlyHint=true`、
  `robei_gui_close` 是 `destructiveHint=true`、`robei_gui_*` 是 `openWorldHint=true`
- 失败统一带稳定 `code` + `hint`：`not_found` / `already_exists` / `invalid_argument` /
  `unsafe_xml` / `path_rejected` / `cancelled` / `timeout` / `compile_failed` /
  `run_failed` / `internal`，客户端不必再猜错误文本
- XML 防护：`<!ENTITY>` 或带内部子集的 `<!DOCTYPE ... [ ... ]>` 直接拒（`code=unsafe_xml`），
  Robei 自己的裸 `<!DOCTYPE Robei>` 与真实 `mux.model` 照常解析
- 客户端取消：`simulate` 跑到一半取消 → 2 秒内 `vvp.exe` 消失、服务端仍能继续响应
- 进度上报：一次 `simulate` 发出 5 条 `notifications/progress`（started → iverilog → vvp → done）
- 路径逃逸与"文件不存在"现在能区分：越界文件报 `invalid_argument`，缺文件报 `not_found`
- `robei.toml` 生效，`runtime_status().config.sources` 逐键报告 `env` / `toml` / `default`
- 65 个一次性 GUI 探针曾集中到 `tools/gui/archive/`，1.0.0 时随仓库整理移除（结论保留在 `docs/gui-tools.md`）

已验证（阶段 18，P1 第一批，`tests/stage18_p1.py`，29/29 PASS，42 个工具）：

- **Verilog → 画布**：`import_verilog_canvas` 把结构化的 `.v` 变成 Robei 画布 `.model`（具名与**位置**连接都支持）
  （嵌套 `<Module>` + `<Wire>`），Robei 打开工程编译后自己产出
  `//---Module instantiation--- and2 u1( .a(a), .b(b), .y(t1) );`，headless 仿真 `y = a&b&c` 全对
- **连接性检查**：`check_connectivity` 报多驱动 / 未驱动网络 / 位宽不匹配 / 未知模块 / 未连接端口，
  坏样例精确命中 `multi_driver:1, undriven_net:2`
- **真值表**：`truth_table` 穷举输入跑仿真，与门 4 行全对；`max_inputs` 超限直接拒绝
- **波形断言**：`expect_waveform` 支持 `at` 取点和 `from`+`to` 保持窗口，负例能准确报 `failed=1`；
  信号名有歧义（`count` 同时命中 `counter4_tb.count` 和 `counter4_tb.dut.count`）只让这一条失败，不炸整个调用
- **波形统计**：`waveform_stats` 识别时钟周期 `10 ns`（`100 MHz`）、单比特 level coverage `1.0`
- **testbench 升级**：`generate_testbench(full_combo=true)` 自动穷举输入组合（4 行 `combo` 输出）

已验证（阶段 19，引脚约束 + FPGA 前端流程，`tests/stage19_constraints.py`，25/25 PASS，47 个工具）：

- `.const` 的真实 schema 来自网络（`wwkkww1983/uart` 的 `prac_04_led/light_constrain.const`）：
  `Type="constrain"` 画布 + 嵌套 `<Module Type="model">` + 每根 FPGA 引脚一个 `<Port>` +
  `<Wire Name="<bit>">` 把引脚接到实例端口
- `create_robei_const` 生成这种文件并登记进 `.proj` 的 `<const>` 目录；
  **Robei 真的能打开它**（Files 树出现 `light_constrain.const`，双击后画布显示
  `G15/P15/W13/T16 → light_constrain_inst → M14/M15/G14/D18`，截图
  `workspace/gui_shots/const_canvas-20261023-083541.png`）
- `read_robei_const` 返回端口 → 位 → 引脚映射，SHA256 前后一致（只读）
- `export_robei_xdc` 生成的 `constrain/light_constrain.xdc` 与网络工程里 Robei 自己生成的
  那份**逐行相同**（18 行 PACKAGE_PIN + IOSTANDARD）
- 负向：引脚数不匹配 / 端口不存在 / 引脚名非法 / 重复创建，分别返回
  `invalid_argument` 与 `already_exists`
- **Robei 自己就会导出 xdc，入口是 Compile 而不是 Synthesis**：constrain 模型保存后按
  Build→Compile，Robei 写出 `build/<name>.v` + `build/<name>.xdc`（引脚逐行与
  `export_robei_xdc` 一致）；Compile 的完成标记是 `Finished to generate constrain file!`
  （不是 `Compile done`，这也是之前误判“没有产物”的原因）
- **Robei 自己的 FPGA 前端跑通了**：`robei_gui_save_project`（File→Save）+ `robei_gui_synthesis`
  （Build→Synthesis）在生成的 `.const` 上跑完 yosys Xilinx 流程，输出 `Synthesis complete`
  并写出 `build/<name>.json`。三个前提都是实测踩出来并已修进工具的：根节点必须是
  `Type="const"` + `Vendor/Device/Family`（`Type="constrain"` 会被拒绝为
  `is not constrain file`）、工程里要有 `build/` 目录（否则最后一步
  `Can't open output file .../build/<name>.json`）

已验证（阶段 20，GUI 可靠性，`tests/stage20_gui_robustness.py`，14/14 PASS）：

- **完成标记 + 输出静止 双条件**：`_wait_for` 只在"看到 `Compile done` / `Synthesis complete` /
  `Finished to generate constrain file` **并且** 输出连续 800ms 不变"时才返回，避免标记先到、
  错误行后到的竞态；`ERROR:` 之类的停止标记会立即返回
- **`unknown_state` 自动重试一次**：既没有标记、也没有错误行的情况（stage16 真遇到过）
  现在会重打一次 Compile，并在结果里标 `retried: true`
- **`compile_all` 每个模型都带 `state` / `marker` / `output_tail`**，不再是空的
  `{"ok": false, "error": null}`
- **输入注入失败自动补救**：所有鼠标点击走 `_mouse()`，遇到
  `There is no active desktop required for moving mouse cursor` 先 `SetForegroundWindow`
  再重试一次（今天 open_project 就是被这个卡住的）
- 标记字符串集中在 `COMPILE_MARKERS` / `CONSTRAIN_MARKERS` / `SYNTH_MARKERS`，
  Robei 换版本改文案只需要动一处

仅可见 / 待定：

- Robei GUI 打开和编译：当前 SSH 在 Session 0，交互桌面在 Session 1，无法从 SSH 看到/点击 GUI；已准备 `workspace\robei_counter4_project` 和手动步骤，待人工验证。
- Mac Codex 配置已写入 `~/.codex/config.toml`，`codex mcp list` 可见；但当前长期运行的 Codex app-server 没有重新挂载新工具，需要重启 Codex 会话/应用后再验证。


## Robei 原生仿真闭环

不用 GUI 也能让 Robei 自己编译、自己跑 vvp、自己出 VCD，全流程只碰 workspace：

```text
create_robei_project(name)                    建 model/ test/ const/ verilog/ + .proj
create_robei_model(..., datatype="reg")       写 .model（端口 + Code）
create_robei_test(proj, name, code)           写 .test（Type="test"）+ 自动导出 verilog/
robei_gui_experiment(proj, "tb_x.test")       File→Open → 双击 test → Compile → Run
read_waveform / find_events / render_waveform 读 build/<test>.vcd
```

几个实测出来的硬约束：

1. **扩展名与 Type 必须配对**：测试模型是 `test/*.test` 且 `Type="test"`；写成
   `.model` 或 `Type="model"` 时 Run 会回 `is not test, so there is no waveform generated`。
2. **Compile 会清空 `build/`**：每次 Compile 重新生成 `filelist.vf` 与可执行文件，
   所以「先编 DUT 再编 TB」会把 DUT 产物冲掉；正确做法是把子模块放进
   `<工程>/verilog/`，让 `-y` 去解析。
3. **Run 不会隐式编译**：只点 Run 会执行上一次留下的可执行文件，缺失时报
   `build/<name>: Unable to open input file`。顺序必须是 Compile → Run。
4. **注册型输出要 `datatype="reg"`**：`output wire` 上做非阻塞赋值会报
   `count is not a valid l-value`。
5. `.proj` 是登记表：手工放进去的文件必须同时写进 `.proj` 对应目录块，否则
   Files 树里看不到，`robei_gui_open_model` 会返回 not found。

## 持续集成

```text
.github/workflows/ci.yml
  portable (ubuntu-latest)
    apt install iverilog yosys
    pip install -r requirements.txt
    python tests/portable_core.py
```

`tests/portable_core.py` 是"无 Robei / 无 GUI / 跨平台"的子集：simulate、VCD 解析、
`generate_testbench` 往返、`.model`/`.proj` 只读解析、`simulate_project`、lint。
每一步是独立 step（`simulate` / `testbench` / `robei-xml` / `headless` / `lint`），
所以失败时 annotation 直接点名是哪一组，不用翻日志。

第一版 CI 就是靠这个定位到两个问题的：
1. `portable_core` 断言了 `runtime_status()["ok"]`，而它要求 Robei 本体存在——
   CI 里没有 Robei，必然失败；改成断言 iverilog/vvp 存在。
2. Ubuntu 与 macOS 都缺少 Windows 上的 `D:\robei-lab` 沙盒路径，`config.py` 里
   `ctypes.wintypes` 的顶层 import 直接让整包无法导入；改成条件导入 + 按平台探测 PATH。

Robei 相关的一切（`robei_gui_*`、`robei_doctor` 的 GUI 任务检查、stage5–13）只在
Windows 交互桌面上跑，不进 CI。

跨平台注意：Linux/macOS 只支持核心工具链（iverilog/vvp/yosys/verilator），
`robei_gui_*` 会返回 `GUI tools require Windows`。


## 测试

```powershell
C:\dev\robei-mcp\.venv\Scripts\python.exe C:\dev\robei-mcp\tests\stage2_smoke.py
C:\dev\robei-mcp\.venv\Scripts\python.exe C:\dev\robei-mcp\tests\stage3_cases.py
C:\dev\robei-mcp\.venv\Scripts\python.exe C:\dev\robei-mcp\tests\stage4_cases.py
```

`stage4_cases.py` 覆盖（全部通过）：

1. 10 个工具注册 + `runtime_status` 报告 Verilator `5.036`
2. `warning_width_unused.v` 报出 `unused_signal`，Verilator 报 `Warning-SELRANGE`
3. `counter4.v` 三路 lint 干净；`syntax_error.v` 的错误文本返回
4. `read_robei_project` 解析合成样例（2 个 model、未知 `netlist`/`Instance` 节点）+ 真实 `.proj` SHA 不变
5. `export_robei_verilog` 导出并 lint 通过；重复导出被拒绝
6. `generate_testbench` 生成 TB → `simulate` 跑通；重复生成被拒绝
7. `find_events` 命中 15；注入式条件被拒绝
8. `render_waveform` 出 PNG；重复渲染被拒绝
9. 三类路径逃逸被拒 + 工作区内路径放行
10. 1 秒超时后残留 `vvp.exe = 0`

`stage11_mcp_native.py` 覆盖（全部走真实 stdio MCP 客户端，14/14 PASS）：

1. 29 个工具注册，含 `sync_robei_verilog` / `create_robei_test` / `robei_gui_experiment` / `robei_gui_wave_load`
2. `sync_robei_verilog` → `counter4_native/verilog/counter4.v`
3. `read_robei_project` 读到 `.test`（`type=test`、`timescale=1ns/1ns`），`.proj` SHA 不变
4. `create_robei_test` 建新 test 并登记（第二次调用返回 `registration=exists`）
5. `robei_gui_experiment` 在 Robei 里 Compile+Run，输出 `t=45 count=0011`
6. `read_waveform` / `find_events` 读 Robei 生成的 VCD（`count==4'b0011` → `t=35`）

`stage9_native_run.py` / `stage10_counter_native.py` 覆盖（Robei 原生闭环）：

1. 用 `create_robei_project` + `create_robei_model` + `create_robei_test` 造工程
2. `robei_gui_experiment(restart=True)` 在 Robei 里 Compile + Run，返回原始输出
3. `read_waveform` 列信号、按时间取值；`find_events` 命中条件
4. `render_waveform` 出 PNG
5. `read_robei_project` 的 `sha256_before == sha256_after`

`stage16_canvas.py` 覆盖（12/12 PASS，画布级 model，全程真 Robei + 真仿真）：

1. 37 个工具注册，含 `create_robei_canvas_model`
2. 生成「两个 `and2` 实例 + 5 条 `<Wire>`」的画布模型，解析回 `u1` / `u2` / `t1`
3. 连线语义正确：`t1` 是 `u1.y → u2.a`，没有悬空实例端口
4. Robei 打开工程、Compile 通过，输出 `.a(a) .b(b) .y(t1)` 形式的实例化
5. headless 仿真 `$strobe` 真值表 8 行全部满足 `y = a&b&c`

`stage17_hardening.py` 覆盖（28/28 PASS）：

1. 37 个工具都带 annotations；只读 / 破坏性 / open-world 各抽查一个
2. 错误码：缺文件 → `not_found`、重复创建 → `already_exists`、越界路径 → `invalid_argument`
3. XML 防护：实体炸弹 → `unsafe_xml`；裸 `<!DOCTYPE Robei>` 与真实 `mux.model` 仍能解析
4. 画布负向：重复实例名 / 未知实例端口 / 未知顶层端口 / 模型缺失 / 属性注入
5. 进度：`simulate` 期间收到 ≥4 条 progress 通知
6. 取消：先确认 `vvp.exe` 在跑，取消后消失，服务端仍存活
7. `robei.toml`：`ROBEI_MCP_CONFIG` 指向临时文件后 limits 生效且 `sources` 变成 `toml`

`stage18_p1.py` 覆盖（29/29 PASS，需要交互桌面跑 Robei 编译）：

1. 5 个 P1 工具注册，`check_connectivity` 标成只读
2. `truth_table(and2)` 4 行全对；`max_inputs=1` 被拒
3. ERC：干净设计 `ok=true`；坏样例报 `multi_driver=1`、`undriven_net=2`
4. `import_verilog_canvas` 生成 2 个实例 + `t1` 连线，无悬空端口；位置连接版本同样解析正确
5. 导入后的画布在 Robei 里编译成功，产物含 `and2 u1( .a(a), .b(b), .y(t1) )`；headless 仿真 `y=a&b&c`
6. `expect_waveform` 正例通过、反例 `failed=1`、保持窗口通过
7. `waveform_stats` 认出 10 ns / 100 MHz 时钟，coverage 1.0
8. `generate_testbench(full_combo=true)` 生成的 TB 打印 4 行 combo

`stage19_constraints.py` 覆盖（18/18 PASS）：

1. 3 个约束工具注册，`read_robei_const` 标成只读
2. `create_robei_const` 写入并登记；`read_robei_const` 的位→引脚映射与输入一致、SHA256 不变
3. `export_robei_xdc` 输出与网络工程里 Robei 生成的 xdc **逐行相同**
4. 负向：引脚数不符 / 未知端口 / 非法引脚名 / 重复创建
5. Robei 打开工程并双击 `light_constrain.const`，画布 tab 变成 `light_constrain`

`stage3_cases.py` 覆盖：

1. 正常 4 位计数器 lint/simulate/read_waveform
2. `warning_width_unused.v`：越界位选警告；未用信号存在，但 Icarus 12.0 `-Wall` 不报 unused
3. `warning_portbind.v`：悬空输入端口警告
4. `syntax_error.v`：iverilog 和 Yosys 的错误文本返回
5. `timeout_tb.v`：1 秒超时，检查无残留 `vvp.exe`


## Robei GUI 手动闭环

测试工程：

```text
C:\dev\robei-mcp\workspace\robei_counter4_project\
  counter4_project.proj
  verilog\counter4.v
  MANUAL_STEPS.md
```

当前 SSH 进程在 Session 0，交互桌面 Explorer 在 Session 1，无法从 SSH 会话看到或点击 Robei GUI。因此状态是 **待手动验证**。

手动步骤：

1. 在交互桌面打开 `E:\Robei\bin\Robei.exe`。
2. `File -> Open` 打开 `counter4_project.proj`。
3. 新建 `counter4` 模型（输入 `clk`、`rst_n`、`en`，输出 `count[3:0]`），把 `verilog\counter4.v` 粘贴到 Code 视图，或使用 Robei 的 Verilog 打开/导入入口。
4. `Build -> Compile`，记录输出。
5. 不修改 `E:\Robei\project`，不写回 Robei 安装目录。


## 从 SSH 会话推送仓库

SSH 跑在 Session 0，`git push` 会失败：GCM 的凭据在交互会话的 Windows
Credential Manager 里，Session 0 读写不到（`Unable to persist credentials with the
'wincredman' credential store`）。

办法是把 push 交给交互会话（和 GUI 工具共用同一个 schtasks 桥）：

```powershell
# 在 Windows 交互桌面上
C:\dev\robei-mcp\.venv\Scripts\python.exe C:\dev\robei-mcp\tools\gui\probe_push.py

# 或从 SSH 触发
echo probe_push > C:\dev\robei-mcp\workspace\gui_agent\agent-args.txt
schtasks /Run /TN RobeiMCP_GuiAgent
```

脚本设置 `GIT_TERMINAL_PROMPT=0`、`GCM_INTERACTIVE=never`，避免卡在登录窗口，
并对返回文本里的 token 做脱敏。

回归补充：`tests\stage9_native_run.py`、`tests\stage10_counter_native.py`（Robei
原生闭环）、`tests\stage11_mcp_native.py`（29 个工具的 stdio 端到端，14/14 PASS）。
