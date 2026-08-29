# 0021: 记忆卫生——事实校验、冲突检查、修订/作废、清理

ADR-0014 v2 记忆系统（文件式 `.agent/memory.md`）的已知弱点：**蒸馏内容不自动校验、不自动纠错**。
错误记忆（如误记"项目用 MySQL"，实际 SQLite）会被全文注入每轮 system prompt，持续误导 agent。
本文档补 4 个低成本机制，全部坚持"零外部服务、零向量库、规则优先、LLM 辅助"的项目哲学。

决策记录建议后续以 `docs/adr/0021-memory-hygiene.md` 形式固化（本文档先落实现方案）。

---

## 决策汇总

| # | 决策点 | 定案 |
|---|--------|------|
| Q1 | 写入前事实校验 | **规则化校验器**（grep 依赖/import/文件存在性），`mode: warn/block/off`，默认 `warn`（不阻断蒸馏、不误杀） |
| Q2 | 蒸馏冲突检查 | **蒸馏 prompt 注入已有 memory**，输出协议扩展 `[修正: 旧标题]` / `[作废]` 标记，LLM 只负责"指出"，系统负责"执行" |
| Q3 | 修订/作废语义 | **`MemoryFile.replace()` / `deprecate()`**，作废条目保留但加 `[stale]` 标记，注入时仍可见（防止"看不见的错误"变成"看不见的更正"） |
| Q4 | 清理能力 | **`remove_by_title()` / `remove_by_keyword()`**，与现有 `remove_sections()` 共用同一块解析内核 |

范围声明：**不做** embedding/RAG、不做自动知识图谱、不做跨会话自动回滚。纠错主通道仍是人工 + 可审计事件。

---

## 实现清单

### 1. 校验器：`vague_code/agent/memory_validator.py`（新建）

纯函数、无 LLM、无外部服务。输入一条记忆内容 + workdir，输出结构化判定。

```python
from dataclasses import dataclass, field
from pathlib import Path
import re

@dataclass
class FactCheckResult:
    level: str                      # "verified" | "unverified" | "contradicted"
    evidence: list[str] = field(default_factory=list)  # 命中的文件/行
    rule: str | None = None

class MemoryValidator:
    """规则化事实校验器：针对声明性事实（技术栈/路径/命令）做 repo 证据核对。"""

    # 声明性事实模式 → 校验规则
    RULES = [
        # 数据库/技术栈：有显式声明，则在依赖清单 + 源码 import 里找证据
        {
            "name": "db_stack",
            "pattern": re.compile(r"(MySQL|PostgreSQL|SQLite|Redis|MongoDB|DynamoDB)", re.I),
            "evidence_files": ["pyproject.toml", "requirements*.txt", "package.json",
                               "Cargo.toml", "go.mod", "Gemfile", "composer.json"],
        },
        # 文件/路径声明：检查存在性
        {
            "name": "path",
            "pattern": re.compile(r"(?:文件|路径|file|path)[:：]?\s*([\w./\\-]+\.\w+)", re.I),
            "evidence_kind": "exists",
        },
    ]

    def __init__(self, workdir: str):
        self._workdir = Path(workdir)

    def check(self, content: str) -> FactCheckResult:
        """对一条记忆做全部规则校验：
        - 命中任一规则但证据存在 → verified
        - 命中规则但无证据 → unverified
        - 命中规则且有反证（如代码里 import 了另一技术栈）→ contradicted
        - 未命中任何规则 → verified（不适用规则，放行）
        """
        ...
```

**判定语义**
- `verified`：声明有 repo 证据支持，正常写入。
- `unverified`：有声明但找不到证据（不一定是错，可能是新依赖），默认 `warn` 模式仍写入，标题加 `⚠ unverified` 标记。
- `contradicted`：有反证（依赖里是 SQLite 但记忆说 MySQL），`block` 模式拒绝写入；`warn` 模式写入并加 `[可能矛盾]` 标记。

**接入点（`loop.py`）**
- `_distill_session` 的每个 block 写入前调用 `validator.check(body)`。
- auto_compact 摘要落盘同样过校验（同一 `MemoryFile.append` 上游）。
- 拒绝/降级时 emit 新事件 `memory_rejected`（`trajectory.py` 增枚举），内容含 `level / rule / evidence`，可审计。

### 2. 冲突检查：蒸馏 prompt 注入已有 memory（`loop.py`）

`_MEMORY_DISTILL_PROMPT` 增加 `{existing_memory}` 占位，注入 `mf.inject_text()`（限长内，无则不注入）：

```
你是会话记忆整理器。……
输出格式：
## 短标题
内容

若某条新要点与"已有记忆"矛盾，必须用以下标记，而不是追加一条新的：
## [修正: 旧标题] 新标题
新内容
## [作废] 旧标题
原因

已有记忆：
{existing_memory}
```

**执行语义（解析后）**
- `## 标题` → 普通 `append()`（幂等不变）。
- `## [修正: 旧标题] 新标题` → `MemoryFile.replace(old_title, new_title, content)`；精确标题匹配不到则降级为普通 append + warn（不静默丢内容）。
- `## [作废] 旧标题` → `MemoryFile.deprecate(old_title, reason)`。
- 修正/作废成功后 emit `memory_distill`（复用）并在 payload 加 `action: "replace"|"deprecate"`。

> 设计取舍：LLM 只负责"指出要修正/作废哪条"，匹配与执行交给确定性的标题匹配，避免模糊语义匹配的不确定性。标题是蒸馏时 LLM 生成的短标题，会话内通常稳定，精确匹配够用。

### 3. 修订/作废语义：`vague_code/agent/memory_file.py`（扩展）

抽一个内部块解析内核，三个删除/改写方法共用（为机制 4 打基础）：

```python
def _iter_blocks(self) -> Iterator[dict]:
    """解析文件为块：{title, source, created, hash, body, start_line, end_line}"""
    ...

def replace(self, old_title: str, title: str, content: str,
            source_session: str | None = None) -> bool:
    """精确匹配 old_title 块，替换其标题与内容；保留/更新 source 与 hash。"""

def deprecate(self, title: str, reason: str = "",
              source_session: str | None = None) -> bool:
    """把旧条目标记为作废：标题加 ~~title~~，内容前加 `> [stale] <reason>`。"""

def remove_by_title(self, title_or_substring: str) -> int:
    """按标题（子串匹配）删除块，返回删除数。"""

def remove_by_keyword(self, keyword: str) -> int:
    """按内容关键字删除块，返回删除数。"""

def list_sections(self) -> list[dict]:
    """列出全部块元信息，供 TUI/CLI 展示与人工清理。"""
```

**注入表现（`inject_text` 不变，但作废可见）**
- 作废条目**不隐藏**——注入时保留 `[stale]` 标记，让 agent 和人看到"这是被推翻的结论，别再用"。这比静默删除更安全。
- `unverified` / 可能矛盾标记同理，保留可见。

### 4. 清理：复用块解析内核

`remove_sections(run_id)` 重构为基于 `_iter_blocks()` 的通用删除；新增 `remove_by_title` / `remove_by_keyword` 均走同一内核。TUI 侧可后续在会话列表加"清理记忆"入口（本期只做文件 API + 单测，TUI 入口不扩）。

### 配置（`vague_code/agent/config.py`）

```python
@dataclass
class MemoryValidationConfig:
    enabled: bool = True
    mode: Literal["warn", "block", "off"] = "warn"   # 默认 warn：不阻断蒸馏
    # 注：off = 完全关闭校验（eval 兼容/降级路径）

# MemoryConfig 新增字段
validation: MemoryValidationConfig = field(default_factory=MemoryValidationConfig)
```

---

## 验收标准

1. [ ] 单元测试 `MemoryValidator`：db_stack 规则命中 SQLite 证据 → verified；声明 MySQL 但依赖只有 SQLite → contradicted；声明不存在的路径 → unverified
2. [ ] 单元测试 `MemoryFile`：`replace`（命中/未命中降级）、`deprecate`（标记 + 保留可见）、`remove_by_title` / `remove_by_keyword` / `remove_sections` 共用内核不回归
3. [ ] 单元测试 `_distill_session`：FakeBackend 返回 `[修正: 旧标题]` / `[作废]` 格式 → 正确执行 replace/deprecate；返回普通 `##` → 仍走 append；解析异常静默降级不中断
4. [ ] `memory_rejected` 事件在 block 模式拒绝时落轨迹，payload 含 level/rule/evidence
5. [ ] `--fake` 链路：记忆校验开关开启不破坏 fake 冒烟；`mode=off` 时行为与现状完全一致（无回归）
6. [ ] 手工验证：往 `.agent/memory.md` 写一条 `## 项目用 MySQL`，在 SQLite 项目里蒸馏应被标记/拒绝，并可 `remove_by_keyword("MySQL")` 一键清掉

---

## 成本与风险

- **成本**：机制 1/3/4 为纯规则/文件操作，零 LLM 成本；机制 2 仅复用已有蒸馏调用（多注入一段已有 memory，token 增量 ≈ 注入上限内，可忽略）。
- **风险：误杀**——规则校验可能把"新引入但还没落地的技术栈"判 unverified。对策：默认 `warn` 而非 `block`，只标记不拒绝，`block` 留给显式开启的严格模式。
- **风险：标题匹配失败**——`[修正: 旧标题]` 精确匹配不到时降级为 append + warn，不丢内容，可人工合并。
- **范围声明**：不做自动回滚/知识图谱/embedding；纠错主通道仍是人工 + 可审计事件 + 可见标记。

---

## 关联

- 基础：`docs/adr/0014-memory-system.md`、`docs/articles/08-memory-system.md`
- 建议后续：`docs/adr/0021-memory-hygiene.md` 决策记录；`CONTEXT.md` Memory System 条目补一句"校验/修订/作废"
