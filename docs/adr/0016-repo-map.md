---
status: accepted
date: 2026-08-01
---

# 0016: Repo Map 代码库符号索引（tree-sitter）

## 背景

vague-code 的"代码理解"完全依赖 `grep`/`glob`/`read_file` 三个无状态工具，每次 run 都由 Agent 反复搜索定位符号，**没有持久索引**。在 SWE-bench 30 题评测中，Agent 大量轮次浪费在"反复 grep 找文件定位符号"上。

主流 Coding Agent 的差异化能力在"代码库理解"（Aider repo map、Cursor 向量索引），而非"跨会话对话记忆"。本 ADR 决定补齐代码理解能力。

## 决策

1. **用 tree-sitter，不用 embedding**——tree-sitter 纯本地、零外部服务（符合 ADR-0014 约束），embedding 需要外部模型/API。
2. **v1 仅支持 Python**——SWE-bench 30 题全为 Python，评测可验证；后续加语言只需新增语法包。
3. **双通道注入**——`code_search` 工具（按需查询）+ system prompt 注入 top-N 符号地图（`max_map_tokens=1000`，Aider 默认配置）。
4. **pinned 移除**——pinned（全局记忆库、无项目隔离）被判定为伪需求，`.agent/rules.md` 层级加载（ADR-0008）可完整替代。工程预算转向代码理解。

## 约束

1. **地图 token 硬上限**——地图注入 system prompt 后不可被压缩回收，必须 `max_map_tokens` 硬上限（默认 1000）
2. **降级安全**——构建失败/超时 → 不注入、不注册工具，不影响主循环
3. **增量刷新**——`Agent.start()` 构建一次 + mtime 增量刷新，写操作后不失效
4. **可消融**——repo_map 作为 eval 矩阵变量，用数据验证收益

## 架构

```python
@dataclass
class Symbol:
    name: str          # 符号名
    kind: str          # class / function / method
    file: str          # 相对 workdir 路径
    line: int          # 起始行号
    signature: str     # 签名文本
    ref_count: int     # 引用次数（近似图排序）

class RepoIndex:
    def build(self, workdir, max_files=2000) -> None: ...
    def search(self, query, k=20, path=None) -> list[Symbol]: ...
    def top_symbols(self, k=100) -> list[Symbol]: ...
    def refresh(self, paths) -> list[str]: ...
    def to_map_text(self, max_tokens=1000) -> str: ...
```

- 提取：tree-sitter 解析 `.py` 文件，提取 class/function/method + 签名 + 行号；解析容错，坏文件跳过
- 热度排序：按符号名在文件间的 identifier 出现次数降序（v1 近似 Aider graph ranking）
- 注入：`SystemPrompt.build()` 之后追加 `## 代码库符号地图` 段
- 工具：`code_search` 动态注册（仿 memory_search 模式），scope 为 `(EXACT, READ)`

## Consequences

- `Agent.start()` 构建一次 RepoIndex，绑定 `_repo_index`
- 地图注入吃固定 token，但换来 Agent 定位符号的轮次大幅减少
- eval 矩阵扩为 `compression × concurrency × repo_map × repeat`
- 关联实现：`docs/plans/0014-repo-map.md`
