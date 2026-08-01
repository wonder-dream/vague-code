# T2：修一个真实 Bug

**谁需要读：** 想看 XClaw 如何完成一个完整的修 Bug 流程的开发者
**前置阅读：** T1（第一个任务）
**读完能做什么：** 观察 Agent 从搜索到验证的完整链路，理解不同权限模式的行为

---

## 1. Bug 介绍

**目标项目：** `tests/_target_bug/`（一个简易库存管理系统）

**Bug 位置**（`src/stats.py:22-24`）：

```python
for p in products:
    if p.stock == 0:
        pass            # ← Bug: 应该是 continue，不是 pass
```

文档说 "Only products with stock > 0 should be counted"。但 `pass` 什么都不做，循环继续执行——缺货产品（p4 Jeans stock=0, p5 Tablet stock=0）也被计入统计。结果 Electronics 分类的 avg_price 被拉低（期望 799.99，实际 599.99）。

**测试文件：** `tests/test_catalog.py`

**验证命令：**

```bash
cd tests/_target_bug
uv run pytest tests/test_catalog.py::test_category_breakdown -v
```

预期：FAILED（由于 bug 存在）。修复后这条测试会变为通过。

---

## 2. 启动 Agent 修 Bug

```bash
python -m src.cli --max-turns 30 \
  "修复 category_breakdown 中 stock 为 0 的产品被错误计入统计的 bug。
  文档说只统计 stock > 0 的产品，但代码中 pass 没有 continue" \
  tests/_target_bug
```

任务描述包含了：问题位置（category_breakdown）、现象（stock=0 错误计入）、期望（只统计 stock>0）、线索（pass→continue）。好的描述让 Agent 少走弯路。

---

## 3. 全程观察——Turn by Turn

**Turn 1：探索代码**

Agent 先搜索和阅读代码来定位问题：

```
[Model: deepseek-v4-flash]

[Thinking] 我需要找到 category_breakdown 方法的实现，检查 stock==0 的处理逻辑...

[Tool call] glob tests/_target_bug/src/*.py
[Tool result] stats.py, models.py, repo.py

[Tool call] read_file tests/_target_bug/src/stats.py
[Tool result]  (完整展示 stats.py 内容，第 22-24 行即为 bug 位置)
```

Agent 先 glob 确认文件结构，再 read_file 读 stats.py 全文。找到 bug 后进入下一轮。

**Turn 2：修复代码**

确认了 bug 位置后，Agent 执行修复：

```
[Thinking] bug 在第 23 行。if p.stock == 0: pass。
文档说 'Only products with stock > 0 should be counted'。
应该改为 continue 来跳过 stock=0 的产品。

[Tool call] patch(stats.py, old_str="if p.stock == 0:\n                pass",
                                new_str="if p.stock == 0:\n                continue")

[Permission] Tool: patch → CONFIRM  (按 Y 放行)
[Tool result] 已将 975 字符写入 src/stats.py
```

如果是 normal 模式，你需要在 Permission 弹窗时按 Y 放行。这让你有机会审查 Agent 的修改意图。

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
========== 7 passed in 0s ==========
```

所有 7 个测试通过——fail-to-pass（category_breakdown）从 FAIL 变 PASS，pass-to-pass 也都保持通过。

**Turn 4：end_turn**

```
[end_turn] 修复完毕。已将 category_breakdown 中的 'pass' 改为 'continue'，
现在缺货产品（stock=0）不再被计入统计。
```

**手动验证：**

```bash
cd tests/_target_bug
git diff src/stats.py
```

输出展示 `pass` → `continue` 的精确改动。

---

## 4. 对比不同权限模式的效果

### safe 模式（只读）

```bash
python -m src.cli --max-turns 10 \
  "修复 category_breakdown 的 bug" \
  tests/_target_bug --permission-mode safe
```

Agent 只能读不能写。结果：它能分析出问题，但只能报告不能修复——`"需要将 stats.py 第 23 行的 pass 改为 continue"`。适合代码审查场景。

### normal 模式（默认）

写操作需确认，按 Y 放行。日常开发推荐模式——你有机会审查 Agent 的每一次修改。

### autoedit 模式

```bash
python -m src.cli --max-turns 10 \
  "修复 bug" tests/_target_bug \
  --permission-mode autoedit
```

文件修改自动放行不弹窗，但 bash 命令仍需确认。适合你信任 Agent 的文件修改能力、但仍想控制命令执行的场景。

### auto 模式

```bash
python -m src.cli --max-turns 10 \
  "修复 bug" tests/_target_bug \
  --permission-mode auto
```

零交互，全部自动执行。唯一防线：危险命令（rm、curl|sh 等）仍需确认。

### 对比总结

| 模式 | 读文件 | 写文件 | 安全命令 | 危险命令 | 本任务表现 |
|------|--------|--------|---------|---------|-----------|
| safe | ✓ | ✗ | ✗ | ✗ | 只读分析，不能修复 |
| normal | ✓ | 确认（按 Y） | 确认 | 确认 | 交互式修复 |
| autoedit | ✓ | ✓ 自动 | 确认 | 确认 | 自动修文件，命令需确认 |
| auto | ✓ | ✓ 自动 | ✓ 自动 | 确认 | 全自动修复 |

---

## 5. 失败的修 Bug 场景——模糊任务描述

### 给出模糊描述

```bash
python -m src.cli --max-turns 8 "修一下 inventory bug" tests/_target_bug
```

Agent 的行为差异明显：反复 grep 搜索关键字段 → 尝试多种修改 → 跑测试失败 → 再尝试。`max_turns=8` 切断时，测试可能仍然失败——Agent 没找到正确的 bug 位置。

### 如何写更好的任务描述

| 差 | 好 | 更好 |
|----|----|------|
| "修 bug" | "修复 category_breakdown 的 bug" | "修复 category_breakdown 中 stock=0 产品被错误计入统计的 bug，期望 Electronics avg_price=799.99，目前是 599.99" |
| "添加功能" | "添加一个按价格范围过滤的函数" | "在 ProductRepo 中新增 search_by_price_range(min, max) 方法，返回价格范围内的产品列表" |

**好描述的 4 要素：**
1. **问题现象**——什么不对（avg_price 被拉低）
2. **期望行为**——应该是什么（只统计 stock>0）
3. **代码位置**——可选但有助于加速（category_breakdown 方法）
4. **验证方式**——如何判断修复完成（测试通过 / 数值正确）

---

## 下一篇

→ **T3：扩展 XClaw**：添加新工具、新厂商、新任务。

**相关链接：** 07-permission-system.md、05-tool-system.md
