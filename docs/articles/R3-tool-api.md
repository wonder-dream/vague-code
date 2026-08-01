# R3：Tool API 参考

**谁需要读：** 编写自定义工具或理解工具接口的开发者
**前置阅读：** 05-tool-system.md
**读完能做什么：** 理解每个工具的 JSON Schema、参数类型、返回值格式、错误类型、边界限制值

---

## 1. read_file

**代码位置：** `tools.py:29-57`

| 属性 | 值 |
|------|-----|
| name | `"read_file"` |
| 必填参数 | `path: string`（相对工作目录的文件路径） |
| 返回值 | 文件内容（str），UTF-8-SIG 编码 |
| 错误类型 | `ValueError("需要提供路径")` / `PermissionError("检测到路径穿越")` / `FileNotFoundError` |
| 边界 | MAX_READ_BYTES = 10MB，超出返回截断内容 + `[... 输出截断于 {n} 字节]` |

---

## 2. write_file

**代码位置：** `tools.py:59-83`

| 属性 | 值 |
|------|-----|
| name | `"write_file"` |
| 必填参数 | `path: string` + `content: string` |
| 可选参数 | `overwrite: boolean`（默认 false） |
| 返回值 | `"已将 {len} 字符写入 {path}"` |
| 错误类型 | `ValueError("需要提供路径")` / `FileExistsError("文件已存在。设置 overwrite=true 覆盖。")` / `ValueError("内容必须是非空字符串")` |
| 行为 | 自动创建父目录（`target.parent.mkdir(parents=True, exist_ok=True)`） |

---

## 3. patch

**代码位置：** `tools.py:107-147`

| 属性 | 值 |
|------|-----|
| name | `"patch"` |
| 必填参数 | `path: string` + `old_str: string` + `new_str: string` |
| 返回值 | `"已将 {len} 字符写入 {path}"` |
| 错误类型 | `ValueError("需要提供 old_str")` / `ValueError("未找到字符串: ...")` / `ValueError("发现 {n} 处匹配，请添加更多上下文")` / `ValueError("文件过大，无法使用 patch")` |
| 边界 | MAX_PATCH_BYTES = 1MB（`tools.py:123`） |

---

## 4. glob

**代码位置：** `tools.py:85-105`

| 属性 | 值 |
|------|-----|
| name | `"glob"` |
| 必填参数 | `pattern: string`（glob 模式） |
| 返回值 | 每行一个相对路径（str，多行） |
| 错误类型 | `ValueError("pattern is required/null")` |
| 边界 | MAX_GLOB_RESULTS = 1000（`tools.py:15`） |

---

## 5. grep

**代码位置：** `tools.py:149-209`

| 属性 | 值 |
|------|-----|
| name | `"grep"` |
| 必填参数 | `pattern: string`（正则表达式） |
| 可选参数 | `path: string`（搜索目录） + `include: string`（文件过滤 glob） |
| 返回值 | 每行 `{relpath}:{line}: {content}`（str，多行） |
| 错误类型 | `ValueError("pattern must be a non-empty string")` / 正则编译失败返回友好消息 |
| 边界 | MAX_GREP_RESULTS=500 / MAX_GREP_FILE_SIZE=5MB / MAX_GREP_FILE_COUNT=500 / 目录扫描上限=5000（`tools.py:16-18,183-184`） |

---

## 6. bash

**代码位置：** `tools.py:211-260`

| 属性 | 值 |
|------|-----|
| name | `"bash"` |
| 必填参数 | `command: string` |
| 可选参数 | `cwd: string`（工作子目录） |
| 返回值 | `退出码: N\n标准输出:\n{...}\n标准错误输出:\n{...}` |
| 错误类型 | `ValueError("需要提供命令")` / `RuntimeError("命令在 30 秒后超时")`（附部分输出） |
| 边界 | 30 秒超时 + 进程树 kill（`taskkill /F /T`），标准输出/标准错误输出各 50KB 截断（`tools.py:14`） |
| 特殊 | Windows 自动 `chcp 65001 >nul &` 前缀（`tools.py:226-227`） |

---

## 7. memory_search

**代码位置：** `memory_tool.py:5-32`

| 属性 | 值 |
|------|-----|
| name | `"memory_search"` |
| 必填参数 | `query: string` |
| 返回值 | `--- 记忆（置信度: {n}）---\n{content}`，双换行分隔多条 |
| 错误类型 | 无查询→`"未提供查询内容"`，无结果→`"未找到相关记忆"` |
| 边界 | top-K = `MemoryConfig.search_top_k`（默认 5） |
| 注册方式 | 动态注入（`loop.py`），仅在 memory 开启时注册 |

---

## 8. code_search

**代码位置：** `tools.py`（spec）+ `repomap.py`（索引）+

| 属性 | 值 |
|------|-----|
| name | `"code_search"` |
| 必填参数 | `query: string`（符号名或正则） |
| 可选参数 | `path: string`（过滤文件路径） |
| 返回值 | `file:line: signature` 列表，换行分隔 |
| 错误类型 | 无查询→`"需要提供搜索查询内容"`，无结果/无效正则→`"未找到与 ... 匹配的符号"` |
| 边界 | top-K = 20，截断保护对齐 `MAX_GREP_RESULTS` |
| 注册方式 | 动态注入（`loop.py`），仅在 repo index 构建成功时注册 |
| 并发 scope | `(EXACT, READ)` |

---

## 9. Tool dataclass

**代码位置：** `tools.py:20-26`

```python
@dataclass
class Tool:
    spec: ToolSpec
    factory: Callable[[str], Callable[[dict], str]]

    def bind(self, workdir: str) -> Callable[[dict], str]:
        return self.factory(workdir)
```

| 属性 | 说明 |
|------|------|
| `Tool.spec` | `ToolSpec` 实例（name + description + parameters） |
| `Tool.factory` | `Callable[[str], Callable[[dict], str]]` — workdir → handler |
| `Tool.bind(workdir)` | 返回绑定工作目录的 handler 函数 |

**DEFAULT_TOOLS 注册表**（`tools.py:341-348`）：`dict[str, Tool]`，key 必须等于 `tool.spec.name`。`memory_search` 和 `code_search` 不在 DEFAULT_TOOLS 中，由 `loop.py` 动态注入（memory 开启 / repo index 成功时）。

---

## 下一篇

→ **R4：Trajectory Events 参考**——12 种事件类型的 payload 字段表和 JSONL 示例。
