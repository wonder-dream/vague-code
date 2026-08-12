---
status: accepted
date: 2026-07-21
---

# 工具系统采用可注入注册表 + class-based 抽象层

## 背景

工具不内嵌于 Agent Runtime（loop.py），独立为 `vague_code/agent/tools/` 包。Agent 通过构造函数注入工具注册表（默认 `DEFAULT_TOOLS`），与 backend 注入同一手法——Agent 核心只见抽象，不见具体工具实现。

> **决策更新（2026-08-12，ADR-0004 重构）：** `Tool` 由 dataclass（spec + factory 闭包）重构为 **class-based 抽象层**：元数据声明（permission / op_type / scope_type）+ 模板方法（参数提取 → 路径安全 → run → 统一截断 → 结构化结果）。权限分类与并发 scope 提取从 permission.py / concurrency.py 的**按工具名硬编码分支**迁入工具定义（元数据内聚）。

## 架构（v2，对齐业界调研）

参考实现：opencode（Tool.Def + InvalidArgumentsError + ExecuteResult 结构化）、Codex（FunctionCallError{RespondToModel|Fatal} 两态错误）、PI（truncate.ts 统一截断 + prepareArguments）、Claude Code（PermissionEvaluator 横切）。

```python
class Tool(ABC):
    name / description / parameters: ClassVar      # JSON Schema 声明（spec() 生成）
    permission: ClassVar[str]                      # read/write/bash_safe/bash_dangerous/network
    op_type / scope_type: ClassVar                 # 并发资源元数据（concurrency 消费）
    max_lines / max_bytes: ClassVar                # 统一截断上限（默认 2000 行 / 50KB）

    def handle(self, input) -> ToolResult          # 模板方法：run → truncate → ToolResult
    def run(self, input) -> str                    # 子类实现核心逻辑
    def extract(...) / resolve_path(...)           # 参数校验 / 路径安全基类
    def permission_class(self, input) -> str       # 默认类变量；Bash 覆写（safe/dangerous 动态）
    def resource_scope(self, input) -> ResourceScope  # 默认元数据；WriteFile 覆写（新文件=SW）
```

### 错误契约（两态）

| 态 | 类型 | 语义 |
|---|---|---|
| 回喂模型 | `ToolInputError(ValueError)` / `ToolPathError(PermissionError)` / `ToolNotFoundError(FileNotFoundError)`（含 Did you mean? 建议）/ `ToolExistsError(FileExistsError)` / `ToolExecutionError(RuntimeError)` | message 是给模型的修正指引（对齐 opencode InvalidArgumentsError prose） |
| 致命 | 其余异常 | loop 层 `{Type}: {msg}` 转 is_error 回喂，语义不变 |

多继承内置异常 → 错误类型语义化 + 既有 `pytest.raises(内置)` 兼容。

### 结构化输出

`ToolResult{output: str, metadata: dict}`（对齐 opencode ExecuteResult）：output 模型可见；metadata 携带截断统计（truncated / truncated_by / output_lines / output_bytes / total_bytes），tool_result 事件与 ToolResultBlock.meta 附带，供 TUI 渲染与评测消费。

### 统一截断

`tools/truncate.py`（对齐 PI truncate.ts）：行 + 字节双限先到先胜、不截半行、结构化统计。默认 **2000 行 / 50KB**（业界验证参数；read_file 上限由 10MB 对齐为 50KB——行为变更）。

### 权限 / 并发元数据消费

- `permission.evaluate(mode, permission_class, operation, rules)`：按分类查策略表，删工具名分支；`code_search` 权限分类 = read（**修复**旧实现默认走 write 策略的缺陷）；Operation 仅服务持久化规则匹配
- `concurrency._scope_for(call, tools)`：调用 `tool.resource_scope(input)`，删 `_extract_scope` 工具名分支；未知工具回退 WORKSPACE+WRITE

## 注册表

`DEFAULT_TOOLS: dict[str, type[Tool]]`（6 基础工具）；`bind_tools(registry, workdir)` 实例化（key == name 校验，fail-fast）。动态工具 `CodeSearchTool(repo_index)` 由 loop 在 repo index 成功时实例化注入。

## Considered Options（v1 决策保留）

- **工具定义内嵌 loop.py（否决）**：加工具必须改 loop 本体；权限/并发元数据无挂载点。
- **handler 签名 `(input, workdir)` 每次透传（否决）**：workdir 一次 run 内不变；bind 一次、handler 纯 input 签名最简。
- **factory 闭包（v1，已淘汰）**：路径校验/参数提取/截断每工具手写重复 5 处；权限/scope 按工具名三处硬编码分支（permission.py / concurrency.py / tools.py）——割裂是重构动机。

## Consequences

- 新增工具 = 定义子类 + 注册，Agent 核心零改动；权限/并发元数据随工具声明，一处修改全链路生效
- 模型可见行为变更：read_file 上限 10MB→50KB；错误 message 带修正指引 + Did you mean? 建议
- 输出 metadata 不进 codec（模型不可见），仅事件/Block 层
- 测试：工具行为测试 + 元数据一致性测试（test_tool_metadata.py 防回归到硬编码分支）

> **实施补充（2026-08-12，plans/0019）：** 工具实现层重构：read_file 加 offset/limit（1-indexed 行读）+ 目录读取 + 二进制检测 + 单行截断；glob 结果排序 + path 参数；write/patch 原子写（tempfile + os.replace，mode 保留）；grep 改 ripgrep 驱动（--sort path 确定性 + .gitignore 尊重 + ignore_case/literal/context 参数，rg 不可用降级纯 Python）；bash 加 timeout 参数 + 输出超限落盘（full_output_path，read_file 读回）；code_search 加 k 参数；新增 web_search 工具（DuckDuckGo 零 key，permission=network 分类首次落地，动态注入评测零影响）。
