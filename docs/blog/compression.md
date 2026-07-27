# 我如何实现一个 Coding Agent 的上下文压缩

## 背景

Coding Agent 的瓶颈之一：**上下文窗口不够用**。一个编码会话可能持续几十轮对话，每轮都包含文件内容、工具输出、代码 patch 等。LLM 的上下文窗口（即使 1M）也架不住逐轮累计。

直接砍掉旧消息会丢上下文——模型会忘记已经做了哪些修改、看过哪些文件。需要一种**按精准度排序**的回收策略：先删确定冗余的，再缩超长输出，最后才盲砍。

---

## 方案：四层压缩流水线

```
stale_snip → microcompact → auto_compact → truncation
```

**排序原则**：从最精准（零 loss）到最盲目（硬截断），前一层回收不够才进下一层。

---

### Layer 1：stale_snip（每轮无条件跑）

**问题**：Agent 一轮调用 3 个 read_file 读同一个文件，只有最后一次有用。

解法：追踪每个文件的读取历史。同一文件被反复读取时，只保留最后一次的完整内容，之前的全部替换为：

```
[stale: superseded by later read of a.py]
```

成本：零（只做字符串替换，不调 LLM）。零信息损失（原文存在 trajectory 事件流里）。

实现细节：用 `tool_use_id` 把 assistant 消息中的 ToolUseBlock 和 user 消息中的 ToolResultBlock 配对。按 `（tool_name, path）` 为键做分组映射，每个路径/工具组合保留最后一次，其余标记 stale。

边界情况：

- 同一文件先 read 后 write → write 不是 read 工具，不触发 stale
- 不同工具读同一文件（read vs grep）→ 工具名不同，各自独立追踪
- glob 模式 `*.py` → 路径 key 从 pattern 提取目录前缀

---

### Layer 2：microcompact（条件触发，util > 0.5）

**问题**：bash 命令输出 50K 字符的日志，里面 80% 是重复的中间行。

解法：对超过 4000 字符的工具输出，保留 head 20 行 + tail 10 行，中间行折叠：

```
[compacted: 12480 chars, 520 lines]
--- head (20 lines) ---
line 1
line 2
...
--- tail (10 lines) ---
line 511
...
line 520
```

同时 `meta["compacted"]` 记录原文长度和 tool_use_id，模型可通过重新 read 获取全量。

经验：工具输出的关键信息通常在头部（命令、错误类型）和尾部（退出码、summary），中间是真噪声。

---

### Layer 3：auto_compact（util > 0.85）

**问题**：会话已经 20 轮了，消息太多，前两层收不回足够 token。

解法：调用 LLM 自身对旧轮次做全文摘要，用一条 summary user message 替换：

```
[Session summary]
The user needed to fix 3 bugs in stats.py and repo.py.
Completed: pass→continue in stats.py list comprehension.
Completed: pagination offset fix in repo.py.
Pending: min_rating comparison inversion.
```

只有这个层有 LLM 调用成本，但它回收的信号最准——LLM 知道什么值得保留。

实现注意：摘要请求的 ToolUseBlock 和 ToolResultBlock context 也要传给 LLM（不只是 TextBlock 的文字），否则摘要会丢失工具调用信息。

---

### Layer 4：truncate（兜底）

**问题**：前三层全跑完仍然超 budget。

解法：保留 `system + 首次用户任务` 作为 prefix，从尾部贪心回填最近的消息对，直到 budget 被填满。多出来的消息被丢弃，在最后一个保留的消息后插入 truncation marker。

```
[truncated: dropped 14 messages to fit token budget]
```

配对齐一性：ToolUseBlock 和它的 ToolResultBlock 必须同进同出，否则 API 报错。

---

## 消融数据

用 SWE-bench Lite 30 题在 DeepSeek V4 Flash 上跑消融实验（10 题 × 4 配置 × 3 重复 = 120 run）：

| Compression | Concurrency | Pass Rate | Avg Tokens |
|---|---|---|---|
| ✗ | ✗ | 83% | 635K |
| ✗ | ✓ | **93%** | 614K |
| ✓ | ✗ | 76% | 735K |
| ✓ | ✓ | 73% | 759K |

关键发现：

1. **并发独立提升 10pp（83%→93%），同时 token 降低 3%**——多工具并行执行不增加语义轮次
2. **压缩在短会话中未产生正收益（76% vs 83%）**——30 轮以下利用率不足，压缩 LLM 调用成本超过回收
3. **压缩 + 并发存在负协同（73% < 76% 和 93%）**——auto_compact 的摘要调用受并发影响

这正是设计预期：压缩在 30+ 轮长会话中才有价值，短会话不适合。简历上可以坦诚地说"在 30+ 轮长会话中压缩率 X%，短会话中自动降级"。

---

## 教训

1. **纯函数设计是可持续的**：每层 `messages → messages`，不改 trajectory，resume 后重新压缩。

2. **从第一天就埋观测点**：每层 emit `EventType.compression`（before/after tokens），消融数据从事件流聚合——零额外工作。

3. **分层阈值是需求导向的**：每个用户／项目的上下文使用模式不同，可配置的阈值让你不用改压缩代码。

4. **压缩不落盘**：trajectory 存的是原始消息，不是压缩版。这意味着：
   - resume 重建完整历史后重新压缩，压缩版本不会泄漏到持久层
   - 崩溃恢复后自动重新压缩，无持久化一致性风险

---

## 后续

- **per-message token 缓存**：当前 truncate 贪心循环每次复算全量 token，复杂度 O(P×C)。缓存 per-message token 可降为 O(C)。
- **长会话专门评测**：30+ 轮场景的压缩收益需要专门评测，当前 30 题 SWE-bench Lite 全是短会话。
- **语义去重**：记忆系统需要，压缩流水线也可复用（检测 nearly-identical 工具输出）。
