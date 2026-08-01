# 细纲：tool-api.md

**预估行数：** ~200 行（表格风格）
**定位：** API 参考——工具接口。

---

## 开头

- **谁需要读：** 编写自定义工具或理解工具接口的开发者
- **前置阅读：** 05-tool-system.md
- **读完能做什么：** 理解每个工具的 JSON Schema、参数类型、返回值格式、错误类型、边界限制值

---

## 细纲

### 1. read_file

**代码位置：** `tools.py:29-57`, `tools.py:262-272`

| 属性 | 值 |
|------|-----|
| name | `"read_file"` |
| 必填参数 | `path: string`（相对工作目录的文件路径） |
| 返回值 | 文件内容（`str`），UTF-8-SIG 编码 |
| 错误 | `ValueError("path is required/null")` / `PermissionError("Path traversal detected")` / `FileNotFoundError` |
| 边界 | MAX_READ_BYTES = 10MB（`tools.py:13`），超出返回截断内容 + `[... output truncated at {n} bytes]` |

### 2. write_file

**代码位置：** `tools.py:59-83`, `tools.py:274-286`

| 属性 | 值 |
|------|-----|
| name | `"write_file"` |
| 必填参数 | `path: string` + `content: string` |
| 可选参数 | `overwrite: boolean`（默认 false，文件存在时报错） |
| 返回值 | `"Wrote {len} chars to {path}"` |
| 错误 | `ValueError("path is required/null")` / `FileExistsError("File already exists. Set overwrite=true to replace.")` / `ValueError("content must be a non-empty string, got null")` |
| 行为 | 自动创建父目录（`target.parent.mkdir(parents=True, exist_ok=True)`） |

### 3. patch

**代码位置：** `tools.py:107-147`, `tools.py:300-312`

| 属性 | 值 |
|------|-----|
| name | `"patch"` |
| 必填参数 | `path: string` + `old_str: string` + `new_str: string` |
| 返回值 | `"Wrote {len} chars to {path}"` |
| 错误 | `ValueError("old_str is required")` / `ValueError("String not found: {old_str}")` / `ValueError("found {n} occurrences, add more context")` / `ValueError("File too large for patch. Use write_file to replace.")` |
| 边界 | MAX_PATCH_BYTES = 1MB（`tools.py:123`） |

### 4. glob

**代码位置：** `tools.py:85-105`, `tools.py:288-298`

| 属性 | 值 |
|------|-----|
| name | `"glob"` |
| 必填参数 | `pattern: string`（glob 模式） |
| 返回值 | 每行一个相对路径（`str`，多行） |
| 错误 | `ValueError("pattern is required/null")` |
| 边界 | MAX_GLOB_RESULTS = 1000（`tools.py:15`） |

### 5. grep

**代码位置：** `tools.py:149-209`, `tools.py:314-326`

| 属性 | 值 |
|------|-----|
| name | `"grep"` |
| 必填参数 | `pattern: string`（正则表达式） |
| 可选参数 | `path: string`（搜索目录） + `include: string`（文件过滤 glob） |
| 返回值 | 每行 `{relpath}:{line}: {content}`（`str`，多行） |
| 错误 | `ValueError("pattern must be a non-empty string")` / 正则编译错误（友好返回） |
| 边界 | MAX_GREP_RESULTS=500 / MAX_GREP_FILE_SIZE=5MB / MAX_GREP_FILE_COUNT=500 / 目录扫描上限=5000（`tools.py:16-18,183-184`） |

### 6. bash

**代码位置：** `tools.py:211-260`, `tools.py:328-339`

| 属性 | 值 |
|------|-----|
| name | `"bash"` |
| 必填参数 | `command: string` |
| 可选参数 | `cwd: string`（工作子目录） |
| 返回值 | `exit code: N\nstdout:\n{...}\nstderr:\n{...}` |
| 错误 | `ValueError("command is required/null")` / `RuntimeError("command timed out after 30 seconds")`（附 partial output）|
| 边界 | 30 秒超时 + 进程树 kill（`taskkill /F /T`），stdout/stderr 各 50KB 截断（`tools.py:14`）|
| 特殊 | Windows 自动 `chcp 65001 >nul &`（`tools.py:226-227`） |

### 7. memory_search

**代码位置：** `memory_tool.py:5-32`

| 属性 | 值 |
|------|-----|
| name | `"memory_search"` |
| 必填参数 | `query: string` |
| 返回值 | `--- Memory (confidence: {n}) ---\n{content}`，双换行分隔 |
| 错误 | 无查询→`"No query provided"`，无结果→`"No relevant memories found"` |
| 边界 | top-K = `MemoryConfig.search_top_k`（默认 5） |
| 注册 | 动态注入（`loop.py`），仅在 memory 开启时 |

### 8. code_search

**代码位置：** `tools.py`（spec）+ `repomap.py`（索引）

| 属性 | 值 |
|------|-----|
| name | `"code_search"` |
| 必填参数 | `query: string`（符号名或正则） |
| 可选参数 | `path: string`（过滤文件路径） |
| 返回值 | `file:line: signature` 列表，换行分隔 |
| 错误 | 无查询→`"需要提供搜索查询内容"`，无结果/无效正则→`"未找到与 ... 匹配的符号"` |
| 边界 | top-K = 20，截断保护对齐 `MAX_GREP_RESULTS` |
| 注册 | 动态注入（`loop.py`），仅在 repo index 构建成功时 |

### 9. Tool dataclass

**代码位置：** `tools.py:20-26`

| 属性 | 说明 |
|------|------|
| `Tool.spec` | `ToolSpec` 实例 |
| `Tool.factory` | `Callable[[str], Callable[[dict], str]]` — workdir → handler |
| `Tool.bind(workdir)` | 返回绑定工作目录的 handler |

**`DEFAULT_TOOLS` 注册表（`tools.py:341-348`）：** `dict[str, Tool]`，key 必须等于 `tool.spec.name`

---

## 结尾

**下一篇推荐：** → R4：Trajectory Events 参考

---

## 本文件说明

这是文档 `tool-api.md` 的细纲。实际写作时需与 `tools.py` 中每个 handler 的实现逐行核对错误消息。
