# 细纲：05-tool-system.md

**预估行数：** ~550 行
**定位：** 工具系统的完整设计。

---

## 开头

- **谁需要读：** 想理解工具设计和扩展机制的开发者
- **前置阅读：** 04-agent-runtime.md（理解工具在循环中的执行环境）
- **读完能做什么：** 理解 8 个工具的完整实现、添加自定义工具、理解并发安全

---

## 细纲

### 1. 概述（~30 行）

- 工具是 Agent 的"手"和"眼"——改变外部世界的唯一渠道
- `Tool` dataclass = ToolSpec + factory（`tools.py:20-26`）
- `bind(workdir)` 工厂模式：解耦工具定义与工作目录绑定
- ADR-0004 的设计动机：注册即用，零插件开销

### 2. 8 个工具一览表（~40 行）

| 名称 | 作用 | 必填参数 | scope_type | op_type | 输出限制 |
|------|------|---------|------------|---------|---------|
| read_file | 读取文件内容 | path | EXACT | READ | 10MB 截断 |
| write_file | 写入文件 | path, content | EXACT | WRITE / SW | — |
| patch | 精确字符串替换 | path, old_str, new_str | EXACT | WRITE | 1MB 文件限制 |
| glob | 文件模式匹配 | pattern | PREFIX | READ | 1000 结果截断 |
| grep | 正则搜索文件内容 | pattern | PREFIX | READ | 500 条结果 / 500 文件 / 5MB 文件 |
| bash | 执行 shell 命令 | command | WORKSPACE | WRITE | 30 秒超时 / 50KB 截断 |
| memory_search | 搜索跨会话记忆 | query | WORKSPACE | READ | top-5 结果 |
| code_search | 搜索代码库符号定义 | query | EXACT | READ | top-20 结果 |

### 3. Tool dataclass + bind 工厂模式（~30 行）

**`Tool(spec, factory)` 的双层结构（`tools.py:20-26`）：**
```python
@dataclass
class Tool:
    spec: ToolSpec          # 工具的定义（Schema）
    factory: Callable[[str], Callable[[dict], str]]  # workdir → handler

    def bind(self, workdir: str) -> Callable[[dict], str]:
        return self.factory(workdir)
```

**`DEFAULT_TOOLS` 注册表（`tools.py:341-348`）：**
```python
DEFAULT_TOOLS: dict[str, Tool] = {
    "read_file": Tool(spec=READ_FILE_SPEC, factory=_read_file_factory),
    "write_file": Tool(spec=WRITE_FILE_SPEC, factory=_write_file_factory),
    "glob": Tool(spec=GLOB_SPEC, factory=_glob_factory),
    "patch": Tool(spec=PATCH_SPEC, factory=_patch_factory),
    "grep": Tool(spec=GREP_SPEC, factory=_grep_factory),
    "bash": Tool(spec=BASH_SPEC, factory=_bash_factory),
}
```

**注册约束：** `registry key == tool.spec.name`（`loop.py:182-183` 校验）

### 4. 每个工具的深层剖析（~180 行，~25 行/工具）

**read_file（`tools.py:29-57`）：**
- 路径解析链：`Path.resolve()` → `is_relative_to(root)`（遍历防护）
- 安全：null 字节检测（`\x00` → `ValueError`）
- 截断：MAX_READ_BYTES = 10MB（`tools.py:13`），超出返回前半部分 + `[... output truncated at ... bytes]`
- 编码：`utf-8-sig`（兼容 BOM）
- 错误类型：`ValueError`（空路径 / null）→ `PermissionError`（路径穿越）→ `FileNotFoundError`（不存在）

**write_file（`tools.py:59-83`）：**
- `overwrite` 参数默认 false：防止意外覆盖已有文件
- 自动创建父目录：`target.parent.mkdir(parents=True, exist_ok=True)`
- 并发标记：新文件 → SW（STRUCTURAL_WRITE），已有文件 → W（WRITE）（`concurrency.py:65-70`）
- 错误：`FileExistsError`（已有文件且 overwrite=false）

**patch（`tools.py:107-147`）：**
- 精确替换语义：只替换第一次出现的 `old_str`
- 0 次匹配 → `ValueError("String not found: {old_str}")`
- 多次匹配 → `ValueError("found {n} occurrences, add more context")`
- 1MB 文件大小限制（`MAX_PATCH_BYTES = 1_048_576`，`tools.py:123`）
- 超限 → `ValueError("File too large for patch. Use write_file to replace the entire file.")`

**glob（`tools.py:85-105`）：**
- 返回工作目录相对路径
- 1000 结果截断（`MAX_GLOB_RESULTS = 1000`，`tools.py:15`）
- 路径安全：只返回 `is_relative_to(root)` 的结果

**grep（`tools.py:149-209`）：**
- 多层截断保护栈：

| 上限 | 常量 | 位置 |
|------|------|------|
| 目录扫描上限 | 5,000 items | `tools.py:183-184` |
| 最大文件扫描 | 500 个（MAX_GREP_FILE_COUNT） | `tools.py:17` |
| 单文件大小跳过 | 5MB（MAX_GREP_FILE_SIZE） | `tools.py:16` |
| 结果条数 | 500 条（MAX_GREP_RESULTS） | `tools.py:18` |

- 正则编译失败友好返回（`"Invalid regex pattern: {e}"`），不抛异常
- UnicodeDecodeError 静默跳过（`tools.py:196-197`）
- 输出格式：`{relpath}:{line}: {content}`

**bash（`tools.py:211-260`）：**
- Windows 前缀：`chcp 65001 >nul &` 保证 UTF-8 编码（`tools.py:226-227`）
- 30 秒超时 → `subprocess.Popen.kill()` + Windows `taskkill /F /T /PID`（进程树杀死）
- stdout / stderr 各自 50KB 截断（`tools.py:255-258`）
- cwd 参数：允许在子目录执行
- 返回格式：`exit code: N\nstdout:\n{...}\nstderr:\n{...}`
- 错误类型：`ValueError`（空命令）→ `RuntimeError`（超时，附 partial output）→ `PermissionError`（目录穿越）

**memory_search（`memory_tool.py:5-32`）：**
- 动态注入时机（`loop.py`）：仅在 `memory.enabled=True` 且 task 非空时注册
- 搜索参数：`query: string`
- top-K：`MemoryConfig.search_top_k`（默认 5）
- 返回格式：`--- Memory (confidence: {n}) ---\n{content}`

**code_search（`repomap.py` + `tools.py`）：**
- 动态注入时机（`loop.py`）：仅在 repo index 构建成功时注册
- 搜索参数：`query: string`（必填）+ 可选 `path` 过滤
- top-K：20 条，截断保护对齐 `MAX_GREP_RESULTS`
- 返回格式：`file:line: signature` 列表
- 并发 scope：`(EXACT, READ)`——只读，与 read_file 同级

### 5. 安全性保证（~60 行）

**路径穿越防护（全部 6 个文件工具共享）：**
- 执行时 resolved path 链：`user path → root / path → resolve() → is_relative_to(root)`
- null 字节检测：`"\x00" in path_str` → `ValueError`
- 绕过路径：绝对路径（"/etc/passwd"） → `resolve()` 后仍在 root 外 → 触发 `PermissionError`

**bash 安全边界：**
- `classify_bash()`（`permission.py:93-100`）：先匹配危险模式 → 再匹配安全模式 → 默认归类危险
- 18 个安全命令模式：ls, git status/log/diff/branch/show/blame/grep, cat, head, tail, wc, echo, pwd, which, whoami, id, uname, env, date, printenv, type, cp, mv（`permission.py:42-61`）
- 24 个危险命令模式：rm, rmdir, dd, chmod, chown, ln, kill, killall, pkill, reboot, shutdown, curl|sh, wget|sh, python -c, bash -c, sed -i, find -delete, fuser, mkfs, fdisk, exec, eval, >/dev/*（`permission.py:63-87`）

**输出截断保护：**
- 单工具输出 50K 硬截断（`loop.py:558-565` `_truncate_tool_content()`）
- 文件读取 10MB 硬截断（`tools.py:46-54`）
- grep 复合上限（500 结果 / 500 文件 / 5MB）
- glob 1000 结果上限

### 6. 并发模型（~120 行）

**为什么需要并发：**
- 消融数据：并发 OFF = 83% pass rate，635K tokens；并发 ON = 93% pass rate，614K tokens（+10pp，-3% tokens）
- LLM 经常同时发送多个独立的工具调用（如同时 grep 搜索 3 个文件）

**ResourceScope 三维模型（`concurrency.py:25-30`）：**
```python
@dataclass
class ResourceScope:
    path: str          # 操作的路径
    scope_type: ScopeType  # EXACT / PREFIX / WORKSPACE
    op_type: OpType        # READ / WRITE / STRUCTURAL_WRITE
```

**每个工具的 scope 提取映射表（`concurrency.py:54-85`）：**

| 工具 | op_type | scope_type | path 来源 |
|------|---------|------------|----------|
| read_file | READ | EXACT | `input["path"]` |
| write_file（存在） | WRITE | EXACT | `input["path"]` |
| write_file（新建） | STRUCTURAL_WRITE | EXACT | `input["path"]` |
| patch | WRITE | EXACT | `input["path"]` |
| glob | READ | PREFIX | pattern 的目录前缀（`_pattern_prefix()`） |
| grep | READ | PREFIX | `input["path"]`（默认 "" = 完整 workspace） |
| bash | WRITE | WORKSPACE | — |
| memory_search | READ | WORKSPACE | — |
| code_search | READ | EXACT | `input["path"]`（可选过滤） |

**冲突判定规则（`concurrency.py:90-104 ` `_scopes_conflict()`）：**
```
if a.op_type == READ AND b.op_type == READ → 不冲突（两读无害）
if a.scope_type == WORKSPACE OR b.scope_type == WORKSPACE → 冲突
if a.path == b.path → 冲突
if a.scope_type == PREFIX AND path_under(a.path, b.path) → 冲突
if b.scope_type == PREFIX AND path_under(b.path, a.path) → 冲突
else → 不冲突
```

**schedule() 贪心分组算法（`concurrency.py:118-138`）：**
```python
groups = []                      # groups: list[list[ToolUseBlock]]
group_scopes = []                # group_scopes: list[list[ResourceScope]]

for call, scope in zip(calls, scopes):
    placed = False
    for g_calls, g_scopes in zip(groups, group_scopes):
        # 如果 call 与 g_scopes 中所有 scope 都不冲突 → 加入
        if not any(scopes_conflict(scope, gs) for gs in g_scopes):
            g_calls.append(call)
            g_scopes.append(scope)
            placed = True
            break
    if not placed:
        groups.append([call])
        group_scopes.append([scope])
```

- 时间复杂度 O(N² × M)，N=call 数，M=已有分组数（通常 N≤5，可忽略）

**失败传播与 [skipped] 语义（`concurrency.py:146-212`）：**
- 同组内：一个 call 失败 → 同组其他 in-flight call 不撤回（已提交无法撤销）
- 跨组间：组 N 任意失败 → 组 N+1..M 全部跳过，返回 `[skipped: cancelled due to upstream failure]`
- 120 秒并发超时（`_CONCURRENT_TIMEOUT = 120.0`）
- max_workers = `max(1, min(len(group), 4))`（防 OOM）

**消融数据讨论（引 README.md:123-132）：**
| Compression | Concurrency | Pass Rate | Avg Tokens |
|------------|-------------|-----------|------------|
| ✗ | ✗ | 83% | 635K |
| ✗ | ✓ | 93% | 614K |
| ✓ | ✗ | 76% | 735K |
| ✓ | ✓ | 73% | 759K |
- 并发是最大单项增益（+10pp），同时 token 减少 3%
- 并发 + 压缩存在负协同（73% < 76% 和 93%），需要进一步分析

### 7. 添加新工具：5 步攻略（~40 行）

| 步骤 | 操作 | 代码位置示例 |
|------|------|-------------|
| 1 | 定义 Handler 函数 `_xxx_factory(workdir) → handler(input) → str` | `tools.py:29-57`（参考 read_file） |
| 2 | 定义 ToolSpec（name / description / parameters JSON Schema） | `tools.py:262-272`（参考 READ_FILE_SPEC） |
| 3 | 构造 Tool 实例 `Tool(spec=xxx_SPEC, factory=_xxx_factory)` | `tools.py:342-343` |
| 4 | 注册到 `DEFAULT_TOOLS` | `tools.py:341-348` |
| 5 | 更新 scope 提取器 `_extract_scope()`（concurrency.py） | `concurrency.py:54-85` |

**设计约束：**
- 输入始终为 `dict`（JSON Schema 校验在 LLM 侧，handler 信任类型）
- 输出始终为 `str`（超过 50K 自动被 `_truncate_tool_content()` 截断）
- 异常必须在 handler 内部捕获并返回友好消息（不抛异常到 Agent）
- 路径安全 "先 resolve 后相对性 check" 模式必须在所有文件操作中重复

---

## 结尾

**下一篇推荐：** → 06-context-engineering.md（上下文压缩与 token 管理）
**相关 ADR：** 0004（Tool Registry + Factory）、0012（并发调度）
**相关 plans：** 0010（concurrency-scheduling）

---

## 本文件说明

这是文档 `05-tool-system.md` 的细纲（大纲）。写作时每个工具的 handler 代码需与实际源码逐行核对。并发部分需确保 scope 映射表与实际 `_extract_scope()` 的分支一致。
