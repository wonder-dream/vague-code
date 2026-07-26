---
status: accepted
date: 2026-07-26
---

# 0008: Rule File Hierarchical Loading

## 背景

系统提示（ADR-0007）包含一个 `[rules]` 段，允许用户或项目通过规则文件自定义 Agent 行为。
规则文件的定位策略决定了"用户把规则写在哪、Agent 怎么找到它"。

当前没有规则加载机制。本项目对标 CLAUDE.md（Claude Code）和 `.cursorrules`（Cursor）的设计——用户可以
在项目根目录放一个规则文件来控制 Agent 行为。

## 约束

1. **自然语言规则**：规则是自由格式的文本（"禁用 rm -rf""永远在修改前读文件"），不适合结构化格式
2. **项目可移植**：规则应随项目走，不依赖 Agent 的全局配置
3. **monorepo 兼容**：父项目规则应被子项目规则继承和覆盖

## Considered Options

| 决策点 | Options | 选出方案 |
|--------|---------|----------|
| 文件格式 | A: `.agent/rules.md` (Markdown) / B: 任意文件名 + 结构扩展 | A |
| 发现策略 | A: 单文件 workdir / B: 层级遍历（workdir → 根目录） / C: 不自动发现，由 config 指定 | B |
| 合并语义 | A: 子目录追加 / B: 子目录覆盖整个 rules 段 | A |
| 文件命名 | A: `.agent/rules.md` / B: `AGENTS.md` (Claude Code 风格) / C: `.cursorrules` | A |

## 决策

### 1. 文件格式：`.agent/rules.md`

使用 Markdown 格式的纯文本文件。无 frontmatter，无结构化前处理。
文件内容按原样拼入系统提示的 `[rules]` 段。

```markdown
# Project Rules

- Always read a file with read_file before editing it.
- Run tests after making changes.
- Use `python -m pytest` rather than bare `pytest`.
```

**为什么是 Markdown 不是 AGENTS.md**：
AGENTS.md（Claude Code 使用）和 `.cursorrules` 都是单文件的 root-only 设计。
`.agent/rules.md` 使用子目录 `.agent/` 命名空间，避免与项目本身的源文件冲突，
且在层级遍历时 `.agent/` 路径天然表明这是 Agent 配置，不会被误读为项目代码。

### 2. 发现策略：层级遍历

从 workdir 向上到文件系统根目录，沿途收集所有 `.agent/rules.md`：

```
/filesystem/root/
  project/                    ← workdir，读 .agent/rules.md
  ├── .agent/rules.md         → 追加
  │   └── (wip)               → 追加 (workdir 中的根)
  ├── src/
  └── tests/
```

**实现**：`Path.parents` 天然提供从 workdir 到根的路径序列。
倒序遍历（根在前，workdir 在最后），逐个读取沿途的规则文件。

```python
def _load_rules(workdir: str) -> str:
    root = Path(workdir).resolve()
    rules: list[str] = []
    for parent in reversed(root.parents):
        f = parent / ".agent" / "rules.md"
        if f.is_file():
            rules.append(f.read_text(encoding="utf-8"))
    f = root / ".agent" / "rules.md"  # workdir 自身
    if f.is_file():
        rules.append(f.read_text(encoding="utf-8"))
    return "\n\n".join(rules)
```

### 3. 合并语义：追加

后发现的文件追加在先发现文件之后，等效于"子目录覆盖父目录"（后追加的文本在后，模型更注意尾部）。
不处理显式覆盖、删除已有规则等复杂语义——v1 不做。

### 4. 不兼容的输入处理

| 情况 | 行为 |
|------|------|
| 文件不存在 | 静默忽略，系统提示的 `[rules]` 段为空 |
| 文件存在但内容为空 | 空字符串占位，不报错 |
| 文件名大小写敏感 | 统一用 `.agent/rules.md`（小写） |
| Windows 盘符根目录 | `Path.parents` 在盘符根停止，不会遍历到其他盘 |

## Consequences

- 规则文件自动发现，用户只需在项目目录放 `.agent/rules.md`
- 层级遍历兼容 monorepo，根目录规则作为基线，子目录规则针对性覆盖
- 与 `.gitignore`、`pyproject.toml` 等工具配置相区别（`.agent/` 命名空间）
- 后续可扩展：`.agent/ignore.md`（代码忽略规则）、`.agent/memory.md`（记忆种子）
- v3 可能增加文件名兼容（`AGENTS.md`、`.cursorrules` 降级读取）
