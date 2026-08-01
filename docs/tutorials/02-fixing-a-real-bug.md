# 细纲：02-fixing-a-real-bug.md

**预估行数：** ~400 行
**定位：** 展示 XClaw 如何完成一个完整的修 Bug 流程，对比不同权限模式。

---

## 开头

- **谁需要读：** 想看 XClaw 如何完成一个完整的修 Bug 流程的开发者
- **前置阅读：** T1（第一个任务）
- **读完能做什么：** 观察 Agent 从搜索到验证的完整链路，理解不同权限模式的行为

---

## 细纲

### 1. Bug 介绍（~40 行）

**目标项目：** `tests/_target_bug/`（一个简易库存管理系统）

**源码：** `tests/_target_bug/src/stats.py`

**Bug 位置（`stats.py:22-24`）：**
```python
for p in products:
    if p.stock == 0:
        pass            # ← Bug: 应该是 continue，不是 pass
```

- 文档说"Only products with stock > 0 should be counted"
- 但 `pass` 什么都不做，循环继续执行 → 缺货产品（p4 Jeans stock=0, p5 Tablet stock=0）也被计入
- 结果：avg_price 被拉低（Electronics 期望 799.99，实际 599.99）

**测试文件：** `tests/_target_bug/tests/test_catalog.py`

**验证命令：**
```bash
cd tests/_target_bug
uv run pytest tests/test_catalog.py::test_category_breakdown -v
```
预期：FAILED（由于 bug）

### 2. 启动 Agent 修 Bug（~20 行）

```bash
python -m src.cli --max-turns 30 \
  "修复 category_breakdown 中 stock 为 0 的产品被错误计入统计的 bug。\
  文档说只统计 stock > 0 的产品，但代码中 pass 没有 continue" \
  tests/_target_bug
```

### 3. 全程观察——Turn by Turn（~200 行）

**Turn 1：探索代码**

Agent 输出展示：
```
[Model: deepseek-v4-flash]

[Thinking] 我需要找到 category_breakdown 方法的实现，检查 stock==0 的处理逻辑...

[Tool call] glob tests/_target_bug/src/*.py
[Tool result] stats.py, models.py, repo.py

[Tool call] read_file tests/_target_bug/src/stats.py
[Tool result]
```
（完整展示 stats.py 内容，行号标注 bug 位置）

**Turn 2：修复代码**

Agent 输出展示：
```
[Thinking] bug 在第 23 行。if p.stock == 0: pass。
文档说'Only products with stock > 0 should be counted'。
应该改为 continue 来跳过 stock=0 的产品。

[Tool call] patch(stats.py, old_str="if p.stock == 0:\n                pass",
                                new_str="if p.stock == 0:\n                continue")

[Permission] Tool: patch → CONFIRM  (按 Y 放行)
[Tool result] Wrote 975 chars to src/stats.py
```

**Turn 3：运行测试验证**

```
[Tool call] bash "uv run pytest tests/test_catalog.py -v"
[Tool result]
========== test session starts ==========
test_add_and_get_product PASSED
test_search_by_category PASSED
test_top_rated PASSED
test_category_breakdown PASSED    ← 修复后通过了
test_inventory_summary PASSED
========== 7 passed in 0.15s ==========
```

**Turn 4：end_turn**
```
[end_turn] 修复完毕。已将 category_breakdown 中的 'pass' 改为 'continue'，
现在缺货产品（stock=0）不再被计入统计。
```

**验证修复（手动）：**
```bash
cd tests/_target_bug
git diff src/stats.py
```
输出 diff 展示 `pass` → `continue` 的改动。

### 4. 对比不同权限模式的效果（~60 行）

**safe 模式（只读）**
```bash
python -m src.cli --max-turns 10 \
  "修复 category_breakdown 的 bug" \
  tests/_target_bug --permission-mode safe
```
- 观察：Agent 只能读文件、搜索代码
- 结果：提出建议但不能修改
- 输出：`"需要将 stats.py 第 23 行的 pass 改为 continue"`（只能报告不执行）
- 适用场景：代码审查、安全分析

**normal 模式（默认）**
- 写操作需确认 → 按 `Y` 放行
- 日常开发推荐模式

**autoedit 模式**
```bash
python -m src.cli --max-turns 10 \
  "修复 bug" tests/_target_bug \
  --permission-mode autoedit
```
- 文件修改自动放行，不弹窗
- bash 命令仍需确认

**auto 模式**
```bash
python -m src.cli --max-turns 10 \
  "修复 bug" tests/_target_bug \
  --permission-mode auto
```
- 零交互，全部自动执行
- 唯一保留防线：危险命令（rm/curl|sh 等）仍需确认

**对比总结表：**

| 模式 | 读文件 | 写文件 | 安全命令 | 危险命令 | 本任务表现 |
|------|--------|--------|---------|---------|-----------|
| safe | ✓ | ✗ | ✗ | ✗ | 只读分析，不能修复 |
| normal | ✓ | 确认（按 Y） | 确认 | 确认 | 交互式修复 |
| autoedit | ✓ | ✓ 自动 | 确认 | 确认 | 自动修文件，命令需确认 |
| auto | ✓ | ✓ 自动 | ✓ 自动 | 确认 | 全自动修复 |

### 5. 失败的修 Bug 场景——模糊任务描述（~40 行）

**给出模糊描述：**
```bash
python -m src.cli --max-turns 8 "修一下 inventory bug" tests/_target_bug
```

**观察：**
- Agent 反复 grep 搜索关键字段 → 尝试多种修改 → 跑测试失败 → 再尝试
- `max_turns=8` 切断时，pending tool calls 被记录但未执行
- 测试可能仍然失败（没找到正确的 bug 位置）

**如何写更好的任务描述：**

| 差 | 好 | 更好 |
|----|----|------|
| "修 bug" | "修复 category_breakdown 的 bug" | "修复 category_breakdown 中 stock=0 产品被错误计入统计的 bug，期望 Electronics avg_price=799.99，目前是 599.99" |
| "添加功能" | "添加一个按价格范围过滤的函数" | "在 ProductRepo 中新增 search_by_price_range(min, max) 方法，返回价格范围内的产品列表" |

**好描述的要素：**
1. 问题现象（什么不对）
2. 期望行为（应该是什么）
3. 代码位置（可选，但有助于加速）
4. 验证方式（如何判断修复完成）

---

## 结尾

**下一篇推荐：** → T3：扩展 XClaw（添加新工具/新厂商/新任务）
**相关链接：** 07-permission-system.md（权限系统详解）、05-tool-system.md（工具系统详解）

---

## 本文件说明

这是文档 `02-fixing-a-real-bug.md` 的细纲（大纲）。实际写作时需在 `tests/_target_bug` 目录下实际运行每个命令以获取真实输出。四种权限模式的对比需逐个截取实际输出。
