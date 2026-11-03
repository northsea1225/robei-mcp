# 安全策略 / Security Policy

## 支持的版本

只维护最新发布的版本（见 [Releases](https://github.com/northsea1225/robei-mcp/releases)）。

## 报告漏洞

请**不要**在公开 Issue 里贴可利用的细节。优先使用 GitHub 的私密报告：
仓库 **Security → Report a vulnerability**（Private vulnerability reporting）。
如果该入口不可用，可开一个只写"需要私下沟通安全问题"的 Issue，不含细节，维护者会联系你。

报告里请尽量包含：受影响的工具名与版本、最小复现步骤（输入文件/参数）、预期与实际行为、你的操作系统与 `iverilog`/`yosys` 版本。
我们会尽快确认并回复；修复发布前请先不要公开。

## 范围

本项目的安全承诺见 README 的「安全边界」：

- 只调用固定白名单的外部程序（`shell=False`），不执行任意命令；
- 源码与模型路径必须落在 workspace / 允许的模型根内；
- 子进程有超时；XML 解析拒绝 DOCTYPE/ENTITY；
- Download 恒为 dry-run，不接触真机。

范围内：绕过上述任一条（路径逃逸、命令注入、XML 实体展开、资源耗尽等）。

范围外：路径静态扫描**不是** OS 沙箱，不防御同机其他进程；Robei 本体、iverilog、yosys 等上游工具自身的漏洞请向上游报告；
需要本机管理员权限或已能随意改写 workspace 之外文件的攻击场景。

---

**English summary.** Please report vulnerabilities privately through GitHub's *Report a vulnerability* (Security tab) rather than a public issue.
Include the affected tool, version, a minimal reproduction and your OS/toolchain versions. Only the latest release is supported.
The path scan is an input check for the toolchain, not an OS sandbox.
