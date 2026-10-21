# Robei counter4 手动闭环验证

状态：**待手动验证**。

原因：当前 SSH 进程位于 Windows Session 0，交互桌面 Explorer 位于 Session 1；无法从 SSH 会话看到或点击 Robei GUI。

## 文件

- 工程骨架：`C:\dev\robei-mcp\workspace\robei_counter4_project\counter4_project.proj`
- Verilog：`C:\dev\robei-mcp\workspace\robei_counter4_project\verilog\counter4.v`

未修改 `E:\Robei\project`。

## 手动步骤

1. 在 Windows 交互桌面打开 `E:\Robei\bin\Robei.exe`。
2. `File -> Open`，选择 `C:\dev\robei-mcp\workspace\robei_counter4_project\counter4_project.proj`。
3. 如果工程树里没有现成模块：
   - 新建 `module` 类型模型 `counter4`；
   - 输入端口 `clk`、`rst_n`、`en`，输出 `count[3:0]`；
   - 把 `verilog\counter4.v` 的内容粘贴到 Code 视图，或使用 Robei 的 Verilog 打开/导入入口打开该 `.v` 文件。
4. 执行 `Build -> Compile`。
5. 记录：
   - 是否能打开工程；
   - 是否能导入或识别 `counter4.v`；
   - Compile 的具体输出和错误（如果有）。
6. 不要修改 `E:\Robei\project`，也不要把测试工程写回 Robei 安装目录。

## 注意

- Robei 路径不要包含中文或空格；当前路径没有。
- 这次没有创建 `.model` 包装文件，避免在未验证 schema 的情况下伪造 Robei 工程对象。
