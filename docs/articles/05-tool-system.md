# Tool System

**谁需要读：** 想理解工具设计和扩展机制的开发者
**前置阅读：** 04-agent-runtime.md（理解工具在循环中的执行环境）
**读完能做什么：** 理解 8 个工具（6 基础 + 2 动态）的完整实现、添加自定义工具、理解并发安全

---

## 1. 概述

工具是 Agent 的"手"和"眼"——它通过工具与外部世界交互。Agent 不能直接操作文件系统或执行命令，一切操作都经过工具的封装。

核心设计：`Tool` ABC（class-based，`tools/base.py`）。元数据类变量（name/description/parameters JSON Schema/permission/op_type/scope_type）+ 模板方法 handle()（参数提取 → 路径安全 → run → 统一截断 → 结构化 ToolResult）。

`bind(workdir)` 工厂模式是关键抽象：工具定义与工作目录解耦，同一个工具定义可以在不同工作目录下绑定不同的 handler 实例。注册约束：`registry key == tool.spec.name`（`loop.py:182-183` 校验），保证命名一致。

ADR-0004 的设计动机：注册即用，零插件开销。添加新工具 = 定义 ToolSpec + 写 handler + 注册到 DEFAULT_TOOLS。

---

## 2. 工具一览表（8 个，plans/0019 更新）

| 名称 | 作用 | 必填参数 | scope_type | op_type | 输出限制 |
|------|------|---------|------------|---------|---------|
| read_file | 读取文件/目录（offset/limit 行区间） | path | EXACT | READ | 2000 行 / 50KB，单行 2000 字符，二进制跳过 |
| write_file | 写入文件（原子写） | path, content | EXACT | WRITE / SW | — |
| patch | 精确字符串替换（原子写） | path, old_str, new_str | EXACT | WRITE | 1MB 文件限制 |
| glob | 文件模式匹配（排序确定性） | pattern | PREFIX | READ | 1000 结果截断 |
| grep | 正则搜索文件内容（ripgrep） | pattern | PREFIX | READ | 500 条结果 / 5MB 文件，行内 500 字符 |
| bash | 执行 shell 命令（timeout 可配，输出超限落盘） | command | WORKSPACE | WRITE | 默认 30 秒超时 / 50KB 截断 |
| code_search | 搜索代码库符号定义 | query | EXACT | READ | top-50（默认 20） |
| web_search | 网页搜索（DDG，动态注入） | query | WORKSPACE | READ | 最多 10 条结果 |

> 记忆不是工具（ADR-0014 v2）：v1 的 `memory_search` 已移除，记忆以 `.agent/memory.md` 注入 system prompt。
> `web_search` 权限分类 = `network`（SAFE 拒绝 / NORMAL 确认 / AUTO 放行）；动态注入（`config.web_search.enabled`），评测零影响。

---

## 3. Tool 抽象层（class-based，ADR-0004 重构）

```python
class Tool(ABC):
    name / description / parameters: ClassVar   # 声明式元数据（spec() 生成 JSON Schema）
    permission: ClassVar[str]                   # read/write/bash_safe/bash_dangerous/network
    op_type / scope_type: ClassVar              # 并发资源元数据
    max_lines / max_bytes: ClassVar             # 统一截断上限（默认 2000 行 / 50KB）

    def handle(self, input) -> ToolResult       # 模板方法：run → truncate → ToolResult
    def run(self, input) -> str                 # 子类核心逻辑
    def extract(...) / resolve_path(...)        # 参数校验 / 路径安全基类
    def permission_class(self, input) -> str    # Bash 覆写（safe/dangerous 动态判定）
    def resource_scope(self, input) -> ResourceScope  # WriteFile 覆写（新文件=SW）
```

`Tool` 元数据声明 + 模板方法：参数提取、路径安全、统一截断（`tools/truncate.py`，2000 行/50KB 双限不截半行）是横切关注点，基类提供；子类 `run()` 只写核心逻辑。**权限分类与并发 scope 由工具元数据提供**——permission.py / concurrency.py 不再按工具名硬编码分支。

**结构化输出**：`ToolResult{output, metadata}`——output 模型可见；metadata 带截断统计（truncated/truncated_by/字节数），进 tool_result 事件与 Block.meta。

**两态错误契约**（对齐 Codex RespondToModel|Fatal）：`ToolInputError/ToolPathError/ToolNotFoundError(含 Did you mean? 建议)/ToolExistsError/ToolExecutionError` 的 message 是给模型的修正指引；其余异常 = 致命。

**DEFAULT_TOOLS 注册表**（`tools/__init__.py`）：`dict[str, type[Tool]]`，`bind_tools(registry, workdir)` 实例化。

`code_search` 不在 DEFAULT_TOOLS 中——它是动态注入的：
- `code_search`（`loop.py` 动态注册）：仅在 repo index 构建成功时注册（`repo_map.enabled=True` 且工作区有可解析符号）

> **记忆无工具（ADR-0014 v2）**：v1 的 `memory_search` 动态注入工具已随 SQLite 记忆库整体移除——记忆改为 `.agent/memory.md` 文件式，system prompt 注入全文（限 200 行/25KB），需要细节时用 read 工具直接读文件。

---

## 4. 每个工具的深层剖析

### read_file（`tools.py:29-57`）

读取文件内容的工具。

- **路径解析链：** `user_path → root / path → Path.resolve()` → `is_relative_to(root)` —— 先拼接到工作目录，再 resolve 掉 `..`，最后检查是否仍在工作目录内。任何绕过尝试都会触发 `PermissionError`。
- **安全：** null 字节检测（`\x00` in path_str → `ValueError`）
- **截断：** 统一截断层（`tools/truncate.py`），2000 行 / 50KB 双限（对齐业界参数），不截半行，截断统计入 ToolResult.metadata
- **编码：** `utf-8-sig` 兼容 BOM 头
- **错误类型：** `ValueError`（空路径/null）→ `PermissionError`（路径穿越）→ `FileNotFoundError`（不存在）

### write_file（`tools.py:59-83`）

写入文件的工具。

- 默认 `overwrite=False`：防止意外覆盖已有文件
- 自动创建父目录：`target.parent.mkdir(parents=True, exist_ok=True)`
- 并发标记：新建文件 → STRUCTURAL_WRITE，覆盖已有文件 → WRITE（`concurrency.py:65-70`）
- 错误：`FileExistsError`（已有文件且 overwrite=False）

### patch（`tools.py:107-147`）

精确字符串替换的工具。与 write_file 的区别：patch 做局部修改，write_file 替换整个文件。

- 精确替换语义：只替换第一次出现的 `old_str`
- 0 次匹配 → `ValueError("String not found: {old_str}")`
- 多次匹配 → `ValueError("found {n} occurrences, add more context")`
- 1MB 文件大小限制（`MAX_PATCH_BYTES = 1_048_576`，`tools.py:123`）
- 超限 → `ValueError("File too large for patch. Use write_file to replace the entire file.")`

### glob（`tools.py:85-105`）

文件模式匹配工具。使用 `pathlib.Path.glob()` 实现。

- 返回工作目录相对路径（`relative_to(root)`）
- 1000 结果截断（`MAX_GLOB_RESULTS = 1000`，`tools.py:15`）
- 路径安全：只返回 `is_relative_to(root)` 的结果，排除路径穿越

### grep（`tools.py:149-209`）

正则搜索文件内容的工具。多层截断保护栈：

| 上限 | 常量 | 位置 |
|------|------|------|
| 目录扫描上限 | 5,000 items | `tools.py:183-184` |
| 最大文件扫描 | 500 个（MAX_GREP_FILE_COUNT） | `tools.py:17` |
| 单文件大小跳过 | 5MB（MAX_GREP_FILE_SIZE） | `tools.py:16` |
| 结果条数 | 500 条（MAX_GREP_RESULTS） | `tools.py:18` |

- 正则编译失败友好返回（`"Invalid regex pattern: {e}"`），不抛异常
- UnicodeDecodeError 静默跳过（二进制文件自动忽略）
- 输出格式：`{relpath}:{line}: {content}`

### bash（`tools.py:211-260`）

执行 shell 命令的工具。

- Windows 前缀：`chcp 65001 >nul &` 保证 UTF-8 编码（`tools.py:226-227`）
- 30 秒超时 → `subprocess.Popen.kill()` + Windows `taskkill /F /T /PID`（递归杀死进程树）
- 输出由统一截断层整体截断（2000 行 / 50KB）
- cwd 参数：允许在子目录执行
- 返回格式：`退出码: N\n标准输出:\n{...}\n标准错误输出:\n{...}`
- 错误类型：`ValueError`（空命令）→ `RuntimeError`（超时，附 partial output）

### 记忆注入（非工具，ADR-0014 v2）

记忆不是工具——`.agent/memory.md` 全文（限 200 行/25KB）注入 system prompt「## 项目记忆」段；LLM 需要细节时用 read 工具直接读文件。v1 的 `memory_search` 工具与 SQLite 记忆库已移除（蒸馏产物即上下文，DB 检索是多余分层）。

### code_search（`repomap.py` + `tools.py`）

代码库符号定义搜索工具，基于 tree-sitter 索引（详见 14-repo-map 相关章节）。

- 动态注入时机：仅在 repo index 构建成功时注册（`repo_map.enabled=True` 且工作区有可解析符号）
- 搜索参数：`query: string`（符号名或正则，必填）+ 可选 `path` 过滤
- top-K：固定 20 条，截断保护对齐 `MAX_GREP_RESULTS` 风格
- 返回格式：`file:line: signature` 列表
- 并发 scope：`(EXACT, READ)`——只读，与 read_file 同级，冲突判定天然正确

---

## 5. 安全性保证

### 路径穿越防护（全部 6 个文件工具共享）

所有操作文件的工具使用同一套路径安全机制：

```
user_path → root / path → Path.resolve() → is_relative_to(root)
```

1. 用户输入路径拼接到工作目录
2. `Path.resolve()` 解析符号链接和 `..`
3. `is_relative_to(root)` 检查是否仍在工作目录内
4. null 字节检测：`"\x00" in path_str` → `ValueError`

任何绕过尝试（如绝对路径 `/etc/passwd` 或 `../../../etc/passwd`）都会在步骤 3 被捕获并触发 `PermissionError`。

### bash 安全边界

bash 工具本身不限制命令执行，但通过权限系统做二次分类：

`classify_bash()`（`permission.py:93-100`）使用双重匹配策略：先匹配危险模式 → 再匹配安全模式 → 默认归类危险。

- **18 个安全命令**：ls, git status/log/diff/branch/show/blame/grep, cat, head, tail, wc, echo, pwd, which, whoami, id, uname, env, date, printenv, type, cp, mv（`permission.py:42-61`）
- **24 个危险命令**：rm, rmdir, dd, chmod, chown, ln, kill, killall, pkill, reboot, shutdown, curl|sh, wget|sh, python -c, bash -c, sed -i, find -delete, fuser, mkfs, fdisk, exec, eval, >/dev/*（`permission.py:63-87`）

### 输出截断保护

| 截断点 | 限制 | 位置 |
|--------|------|------|
| 单工具输出 | 50KB | `loop.py:558-565` |
| read_file | 50KB | `tools/fs.py` |
| grep 单文件 | 5MB | `tools/fs.py` |
| grep 文件数 | 500 | `tools/fs.py` |
| grep 结果数 | 500 | `tools/fs.py` |
| glob 结果数 | 1000 | `tools/fs.py` |

---

## 6. 并发模型

**为什么需要并发？** LLM 经常同时发送多个独立的工具调用——比如同时搜索 3 个文件。串行执行让大量 I/O 等待串在一起，浪费 latency。

> ⚠️ 消融数据待 20 题真验收跑出后回填（v0.1 的 83%/93% 等数字基于假 pass/fail，已废弃，不得引用）。评测现状见 `docs/handoff/2026-08-03-vague-code-eval-system.md`。

### ResourceScope 三维模型

```python
@dataclass
class ResourceScope:
    path: str          # 操作的路径
    scope_type: ScopeType  # EXACT / PREFIX / WORKSPACE
    op_type: OpType        # READ / WRITE / STRUCTURAL_WRITE
```

每个工具调用在调度前提取其 ResourceScope：

| 工具 | op_type | scope_type | path 来源 |
|------|---------|------------|----------|
| read_file | READ | EXACT | `input["path"]` |
| write_file（存在） | WRITE | EXACT | `input["path"]` |
| write_file（新建） | STRUCTURAL_WRITE | EXACT | `input["path"]` |
| patch | WRITE | EXACT | `input["path"]` |
| glob | READ | PREFIX | pattern 的目录前缀 |
| grep | READ | PREFIX | `input["path"]`（默认空=完整 workspace） |
| bash | WRITE | WORKSPACE | — |
| code_search | READ | EXACT | `input["path"]`（可选过滤） |

### 冲突判定规则

```
if a.op_type == READ AND b.op_type == READ → 不冲突（两读无害）
if a.scope_type == WORKSPACE OR b.scope_type == WORKSPACE → 冲突
if a.path == b.path → 冲突
if a.scope_type == PREFIX AND path_under(a.path, b.path) → 冲突
if b.scope_type == PREFIX AND path_under(b.path, a.path) → 冲突
else → 不冲突
```

核心原则：纯读不冲突；任何写操作与同路径的其他操作冲突；WORKSPACE 范围的操作与所有操作冲突。

### schedule() 贪心分组算法

```python
for call, scope in zip(calls, scopes):
    placed = False
    for g_calls, g_scopes in zip(groups, group_scopes):
        if not any(scopes_conflict(scope, gs) for gs in g_scopes):
            g_calls.append(call)
            g_scopes.append(scope)
            placed = True
            break
    if not placed:
        groups.append([call])
        group_scopes.append([scope])
```

贪心策略：每个 call 尝试加入第一个无冲突的现有组；无合适组则新建。组内并发执行，组间串行。

时间复杂度 O(N² × M)，N = call 数，M = 已有分组数（通常 N ≤ 5，可忽略）。

### 失败传播与 [skipped] 语义

- **同组内：** 一个 call 失败 → 同组其他 in-flight call 不撤回（已提交无法撤销）
- **跨组间：** 组 N 任意失败 → 组 N+1..M 全部跳过，返回 `[已跳过: 因上游失败取消]`
- 120 秒并发超时（`_CONCURRENT_TIMEOUT = 120.0`）
- `max_workers = max(1, min(len(group), 4))` 防 OOM

---

## 7. 添加新工具：4 步攻略

| 步骤 | 操作 | 代码位置示例 |
|------|------|-------------|
| 1 | 定义 Tool 子类：元数据类变量（name/description/parameters/permission/op_type/scope_type）+ `run()` | `tools/fs.py`（参考 ReadFileTool） |
| 2 | 覆写 `permission_class()` / `resource_scope()`（默认元数据不满足时） | BashTool / WriteFileTool |
| 3 | 注册到 `DEFAULT_TOOLS`（`dict[str, type[Tool]]`） | `tools/__init__.py` |
| 4 | 参数提取用基类 `extract()`、路径安全用 `resolve_path()`；错误抛 ToolError 子类 | `tools/base.py` |

**设计约束：**
- 输入始终为 `dict`：基类 `extract()` 统一校验（None/空/类型）；错误抛 ToolError 子类（message 给模型修正指引）
- 输出始终为 `ToolResult{output, metadata}`：output 经统一截断层（2000 行 / 50KB，`tools/truncate.py`），截断统计入 metadata
- 未捕获异常 = 致命错误（loop 层 `{Type}: {msg}` 转 is_error 回喂）——工具内不要吞异常返回字符串
- 路径安全 `resolve_path()` 基类统一（空字节 + 穿越防护），无需每个工具重复

---

## 下一篇

→ **06-context-engineering.md**：上下文压缩与 token 管理——五层压缩流水线的完整实现。

**相关 ADR：** 0004（Tool Registry + Factory）、0012（并发调度）
**相关 plans：** 0010（concurrency-scheduling）
