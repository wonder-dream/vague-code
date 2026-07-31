# 0014: Repo Map 代码库符号索引（tree-sitter）

新增基于 tree-sitter 的仓库符号索引子系统，提供 `code_search` 工具 + 浓缩符号地图注入，解决 XClaw 代码理解能力与主流产品（Aider repo map / Cursor 向量索引）的最大差距。

---

## 背景与动机

### 现状问题

XClaw 的"代码理解"完全依赖 `grep` / `glob` / `read_file` 三个无状态工具（`tools.py:149-209`、`85-105`、`29-57`），每次 run 都由 Agent 反复搜索定位，**没有持久索引**。对比：

| 产品 | 代码理解机制 |
|------|-------------|
| Aider | tree-sitter 解析 + 每次请求注入最相关 ~1K token 的 repo map（graph ranking 挑选） |
| Cursor | 全仓库 embedding 向量索引，语义检索 |
| XClaw（现状） | 仅 grep/glob/read 工具，无索引 |

在 SWE-bench 30 题评测中，Agent 大量轮次浪费在"反复 grep 找文件定位符号"上——这正是 repo map 能直接压缩的。

### 决策来源

1. **pinned 判定为伪需求**：生效范围（全局 memory.db、无项目隔离）与用途（项目约定）不匹配，且 `.agent/rules.md` 层级加载（ADR-0008）可完整替代。主流产品（CLAUDE.md / AGENTS.md / .cursor/rules）的"常驻知识"也全部走人写规则文件。**决定：移除 pinned，工程预算转向代码理解。**
2. **行业共识验证**：主流 Coding Agent 的差异化能力在"代码库理解"（Aider repo map、Cursor 向量索引），而非"跨会话对话记忆"。各家均采用此路线，可信赖。
3. **技术选型确认**：用 **tree-sitter**（Aider 同款，纯本地无外部服务，符合零外部服务铁律），而非 embedding（需外部模型/API，违反 ADR-0014 约束）。

---

## 技术选型

| 项 | 选择 | 理由 |
|----|------|------|
| 解析器 | `tree-sitter>=0.26.0` + `tree-sitter-python` | Aider 同款；纯本地；有 cp312/win_amd64 预编译 wheel（PyPI 2026-06 发布） |
| 语法范围 | v1 仅 Python | SWE-bench 30 题全为 Python，评测可验证；后续加语言只需新增语法包 |
| 索引时机 | `Agent.start()` 构建一次 + mtime 增量刷新 | 避免每轮全量重建；写操作后不失效 |
| 注入预算 | `max_map_tokens=1000`（Aider 默认 `--map-tokens 1k`） | 地图在 system prompt 中不可被压缩回收，必须硬上限 |
| 地图挑选 | 按符号被引用次数排序（简化图排序） | Aider 用 graph ranking，v1 用引用计数近似 |

---

## 核心设计

### 1. 新模块 `src/agent/repomap.py`

```python
@dataclass
class Symbol:
    name: str                       # 符号名
    kind: str                       # class / function / method
    file: str                       # 相对 workdir 路径
    line: int                       # 起始行号
    signature: str                  # 签名文本（如 def foo(a, b) -> int）

class RepoIndex:
    def build(self, workdir, max_files=2000) -> None: ...
    def search(self, query, k=20, path=None) -> list[Symbol]: ...
    def top_symbols(self, k=100) -> list[Symbol]: ...
    def refresh(self, paths) -> None: ...
    def to_map_text(self, max_tokens=1000) -> str: ...
```

- **提取**：tree-sitter 解析每个 `.py` 文件，提取 `class` / `function` / `method` 定义 + 签名 + 行号；解析容错（tree-sitter 本身容错），坏文件跳过。
- **搜索**：`search(query)` 按 name / signature 做正则匹配，支持 `path` 过滤。
- **热度排序**：`top_symbols()` 按符号在文件间被引用次数降序，供注入挑选。
- **注入文本**：`to_map_text()` 生成 `file:line: signature` 列表，按热度挑选直到达到 token 预算。

### 2. 新工具 `code_search`（`src/agent/tools.py`）

- **ToolSpec**：`name="code_search"`，输入 `query`（符号名/正则，必填）+ 可选 `path` 过滤；输出 `file:line: signature` 列表（截断保护，对齐既有 `MAX_GREP_RESULTS` 风格）。
- **factory**：绑定 `workdir`，handler 查询 `self._repo_index`（经 Agent 注入绑定）。
- **并发 scope**（`concurrency.py:54-85` 加分支）：`code_search` → `ResourceScope(path=过滤path, scope_type=EXACT, op_type=READ)`——只读，与 read_file 同级，冲突判定天然正确。
- **权限**：只读工具，safe 模式放行（与 read_file 同级）。

### 3. 注入（`loop.py:199-208` 改造）

替换现 pinned 注入位（pinned 移除后该位腾空）：

```
system_prompt = SystemPrompt(workdir).build()    # identity + rules + workdir
+ "\n\n## 代码库符号地图\n" + index.to_map_text(max_tokens)
```

- `Agent.start()` 构建 `RepoIndex`，绑定 `self._repo_index`，供 `code_search` 与增量刷新共用。
- 构建失败/超时 → 不注入，不影响主循环（降级为纯工具模式）。

### 4. 配置（`src/agent/config.py`）

```python
@dataclass
class RepoMapConfig:
    enabled: bool = True
    max_map_tokens: int = 1000
    max_files: int = 2000
    languages: list[str] = field(default_factory=lambda: ["python"])

@dataclass
class AgentConfig:
    ...
    repo_map: RepoMapConfig = field(default_factory=RepoMapConfig)
```

CLI 加 `--no-repo-map` / `--repo-map-tokens`。

### 5. Eval 接入（`eval/matrix.py` + `eval/harness.py`）

- 矩阵扩为 `repo_map × compression × concurrency × repeat`（repo_map 2 档，避免矩阵爆炸）。
- `harness.py:119-125` 加 `repo_map=RepoMapConfig(enabled=cell.repo_map)`。

### 6. Pinned 移除（配套收尾）

- `src/agent/config.py:58` 删 `inject_pinned`。
- `src/agent/memory.py:88-92` 删 `get_pinned()`。
- `src/agent/loop.py:201-208` 删 pinned 注入块。
- `src/tui/widgets/sidebar.py:73-84` 记忆面板改为展示最近蒸馏（或移除 pinned 展示）。
- `tests/test_memory.py` 重写依赖 `kind="pinned"` 的测试（`kind` 列保留，只余 `'episodic'`）。
- 文档清理：README / CONTEXT.md / ADR-0014 / articles(01,02,03,04,08,11) / guide(02,03,04,08) / R1 / troubleshooting / DOCUMENTATION_PLAN / architecture.drawio 中 pinned 相关表述（约 25 处）。

---

## 文件清单

| 步骤 | 文件 | 操作 |
|------|------|------|
| 1 | `pyproject.toml` | 加 `tree-sitter` + `tree-sitter-python` 依赖 |
| 2 | `src/agent/repomap.py` | **新建**：`Symbol` / `RepoIndex` + 符号提取 + 热度排序 + map 文本生成 |
| 3 | `src/agent/config.py` | 加 `RepoMapConfig` + `AgentConfig.repo_map`；删 `inject_pinned` |
| 4 | `src/agent/tools.py` | 加 `code_search` 工具 spec + factory |
| 5 | `src/agent/concurrency.py` | `_extract_scope` 加 `code_search` 分支 |
| 6 | `src/agent/loop.py` | `start()` 构建索引 + 注入 map + 注册工具；删 pinned 注入 |
| 7 | `src/agent/context.py` | `SystemPrompt.build()` 接受可选 map 段 |
| 8 | `src/agent/memory.py` | 删 `get_pinned()` |
| 9 | `src/tui/widgets/sidebar.py` | 记忆面板去 pinned |
| 10 | `src/cli/__init__.py` | 加 `--no-repo-map` / `--repo-map-tokens` |
| 11 | `eval/matrix.py` / `eval/harness.py` | repo_map 变量接入 |
| 12 | `tests/test_repomap.py` | **新建**：索引提取 / 搜索 / 注入文本 / 增量刷新 |
| 13 | `tests/test_concurrency.py` | 补 `code_search` scope 测试 |
| 14 | `tests/test_memory.py` | 重写 pinned 相关测试 |
| 15 | `docs/plans/0014-repo-map.md` | **新建**（本文档） |
| 16 | `docs/adr/0016-repo-map.md` | **新建** ADR |
| 17 | 文档清理 | 删 pinned 相关表述（约 25 处，含 adr/README.md 索引） |

---

## 测试计划

- **单元**（`tests/test_repomap.py`）：
  - tree-sitter 解析 Python 提取 class/function/method + 签名 + 行号
  - `search()` 正则匹配 + path 过滤
  - `top_symbols()` 引用热度排序
  - `to_map_text()` token 预算截断
  - mtime 增量刷新（改文件后重解析）
  - 构建失败降级（不可解析文件跳过 / 空仓库）
- **集成**：FakeBackend 下跑一次 run，验证 `code_search` 工具可用 + map 注入 system prompt
- **质量门**：ruff + mypy 零错误，pytest 全绿（448 → 约 480 条）

---

## 预期收益

- **减少探索轮次**：Agent 不再反复 grep 找符号，直接查 map / code_search → 提升 pass rate、降低 token 与轮次
- **对齐行业共识**：补齐 Aider/Cursor 已验证的差异化能力
- **可消融验证**：repo_map 作为 eval 矩阵变量，用数据确认收益（符合项目"数据说话"铁律）

---

## 已知风险

| 风险 | 缓解 |
|------|------|
| 地图注入占 token 且不可被压缩回收 | `max_map_tokens=1000` 硬上限（Aider 同款）；可 `--no-repo-map` 关闭 |
| Agent 改文件后地图过期 | mtime 增量刷新 + `code_search` 按需查询兜底 |
| 大仓库构建慢 / 构建失败 | `max_files` 上限 + 只索引配置语言 + 构建超时降级（失败不注入，不影响主循环） |
| tree-sitter 语法错误 | tree-sitter 解析本身容错，坏文件跳过 |
| 与 pinned 移除联动面广（文档 25+ 处） | 分步提交，每步 pytest + ruff + mypy 通过 |
