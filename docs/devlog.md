## 2026-07-20（Day 1）

**做了**
- 环境搭建：uv + Python 3.12 + DeepSeek API 跑通
- 手写 40 行最小 Agent 循环，理解 while 循环出口 = 没有 tool_calls
- v0 完成：自定义 IR + DeepSeek codec，17 条测试全绿
- 完成人生第一次 code review：4 个探针，1 个真 bug（空 messages 未 fail-fast），2 个改进项

**卡在哪 / 怎么解决**
- OPENSSL_Uplink 报错 → 查出是 SSLKEYLOGFILE 环境变量冲突 → 清除解决
- 探针 3 报 AttributeError → 是我探针写错不是代码错 → 教训：探针报错先怀疑探针

**学到的（自己的话）**
- LLM API 是无状态的，"记忆"就是 messages 数组本身
- 实测 DeepSeek 上下文缓存命中：cache_read=256 / input=281

**明天第一件事**：让 plan agent 读文档 5.1，出 Agent Loop 实现计划

---

## 2026-07-21（Day 2）

**做了**
- 将 day0 的裸 while 循环重构为库形态：`Agent(config, backend).run(task, workdir) → Trajectory`
- 新建 `AgentConfig`、`ModelBackend` 协议、`DeepSeekBackend`、`Trajectory`（SQLite 事件流存储）
- 工具系统独立为 `src/agent/tools.py`：`Tool` dataclass + `bind(workdir)` 工厂模式
- CLI 骨架（argparse + Rich 渲染）
- 补完 ADR-0004（工具注册表）、ADR-0005（流式事件 IR）、ADR-0006（重试与检查点）
- 补完实现计划 0002～0004
- 76 条测试全绿 + ruff/mypy

**学到的**
- 一份"实现计划 → 代码 → 测试"的流水线比直接写代码快两倍（因为有计划，AI agent 不需要猜上下文）
- ADR 是异步决策的关键：写计划时看到 ADR-0006 已预定 `TransportConfig`、`retry/timeout` 旋钮位置，后续设计就可以直接引用而不回退

---

## 2026-07-22（Day 3）

**做了**
- 实现统一流式事件模型（9 种 `StreamEvent` dataclass + `StreamEventVisitor` 协议）
- DeepSeek codec 新增 `DeepSeekStreamDecoder`（5 步状态机：thinking 边界推断、tool_call 按 index 追踪、finish/usage 延迟发射）
- `ModelBackend` 扩展 `stream()` 方法，`_stream_from` 适配器兼容非流式后端
- `RunHandle` 迭代器模式：CLI 实时拉取事件，`Agent.run()` 向后兼容
- `TransportConfig` 引入，传输层语义与业务配置分离
- CLI 加入 `RichStreamVisitor` + `--stream`/`--no-stream`
- 7 套 golden fixture 快照 + 额外 10+ 流式边界单测

**学到的**
- 流式代码里 bug 最多的点永远是 JSON 增量拼接——集中在一个 `_StreamAggregator` 里，只写一次，所有 codec 复用
- `hasattr(backend, "stream")` 适配器让 FakeBackend（仅 `complete`）零改动就能进流式管线——评测时流式/非流式切换零成本
- 工具参数 `json.loads` 放在 `MessageEnd` 之后批量执行，不在每个 `ToolUseEnd` 时逐个解析——这保证了冲突可串行化的基准前提（模型输出顺序 = 串行序）

---

## 2026-07-23（Day 4）

**做了**
- 实现 ADR-0006 全部 10 节：两层重试（SDK `max_retries=2` + Loop 指数退避全抖动）、异常细分类（10 种 + 1 兜底）、异常驱动 retry 决策
- `RetryPolicy` 纯函数 + `classify_llm_error` 可脱离 Agent 单独单测
- `RetryNotice` 作为第 10 种 StreamEvent：CLI 实时打印 `⚠ 请求失败，N 秒后重试（第 n 次）`
- 检查点机制：每轮 LLM 响应后、工具执行前 `traj.persist()`，崩溃恢复走事务语义（"全回滚"）
- `Trajectory.from_db` + `Agent.resume()`：从 SQLite 恢复轨迹，识别未完成工具并重做
- 完成两轮 review（我报 bug → AI 复核 → 发现 P0 off-by-one → 修复 + 回归测试）
- CLI 测试套件：29 个 mock 管线测试 + 4 个子进程测试 + `--export-jsonl` 目录检测
- 元数据行（`Run X finished`）从默认输出移到 `--verbose`
- 205 条测试全绿，ruff/mypy 通

**卡在哪 / 怎么解决**
- 第一版 resume 的 turn 计算用 `_count_turns`（`max(turn) + 1`）推导，导致工具事件挂错 turn → 改用 `last_llm.turn` 做权威 turn
- 回归测试用 `max_turns=5` 掩盖了 off-by-one（崩溃于 T=0 → resume 跳过了 T=1 的 LLM 调用，5 轮看不出来）→ 教训：边界回归必须用**最小能暴露 bug 的数值**（`max_turns=2`）

**学到的**
- Review 产出不是你报对了几个 bug，而是你**报了候选问题**——被降级不丢人，被验证才是真 bug
- 持久化只有两处（checkpoint + finally），所以所有终态回复都和 `run_end` 同批原子落盘——这是崩溃恢复正确性的根基，面试能讲"为什么只有两个 persist 点而不是到处写"
- CLI 测试分四层（参数 → 配置传递 → mock 全管道 → 子进程）——每一层都在上一层失败不了的窗口里找漏洞

---

## 已知记录（未补）

待补：Day 2 的"卡在哪"没有记，因为当天都是顺产没有阻塞。Day 3 也没有阻塞项——流式 codec 的单测一次通过后没有回退，只有正常的迭代。Day 4 的阻塞项全在"怎么解决"里记了。
---

## 2026-08-04��TUI v2 ������д��

**����**
- ѧϰ�ο�����`tui-reference-pack/`��firstcoder �� Textual TUI��3900 �� / 30 �ļ� / 267 ���ԣ��������ܹ�ѧϰ�ʼ���Աȱ���
- �� `docs/plans/0017-tui-rewrite.md` �� 6 ����̱���д `src/tui/`��ÿ�������ύ��
  - M1 �Ǽ�+spike��e585fa4�����·ֲ� + �ο������� + XClawMarkdown �� textual 8.2.8 ����ֲ��֤
  - M2 �¼�����48bc6f1����`XClawAgentRunner` �߳��� + ��ʽ Markdown ���㻺�� + agent С�Ģ٣�on_tool_result �� tool id��
  - M3 ���߻����3b3ab67����thinking/streaming/running ���� + �غ� metrics + ����״̬��
  - M4 ����ϵͳ��905d0d3����CompositeCommandHandler + picker + ������ʷ + Esc �����ж� + guidance ���У�agent С�Ģۣ�
  - M5 Ȩ����飨5ed8656����prewrite diff + �ܾ����ɷ����ջ���agent С�Ģڣ�
  - M6 ��β��1131b20����resume �켣�ط� + ����
- ���� ADR-0019��TUI v2 �ֲ���д����ȫ���ĵ�ͬ��

**ѧ����**
- �ο�������ʽ��Ⱦ���㻺�壨�¼��߳���+���ʺ� �� UI �߳� buffer �� 0.2s timer flush + update future guard���ǽ��"��ʽ Markdown ����˸�������򡢲������"��������������ÿ delta ȫ��ƴ�ӵ� v1 ǿһ������
- �¼�ͨ�����Գ��� UI ��ȷ�Ե�������tool call ������result �߻ص����� id �� �޷�����״̬��ͳһ��"�ص�ֱ�ﵥһ��ʵԴ��transcript��+ token ���ڹ���"���ط�/�ж�/�ָ�����ѻ��
- Textual �Ŀӣ�`run_worker(thread=True)` ��**�������ý��**�����߳���ִ�е��ñ�����UI �߳���������`is_mounted` �ǽ��� widget �ķ������ǲ������ԣ�mixin ׮�������ڱ� App ��ʵ������MRO������� TYPE_CHECKING ��

**�����һ����**������ճ�����壨Agent Ŀǰֻ���������ı�������� eval �����ֻ���

---

## 2026-08-07：10 题基线 + 消融实验（78 runs，\.08）

**做了**
- 重跑 08-06 定案被中断的 10 题全量基线：核心层 10 实例 x k3 + 消融层 8 实例 x 3 关闭配置 x k2 = 78 runs，3 进程并行（WMI），11:12 启动 16:24 全部完成，零异常
- 核心层 pass^3 = 8/10 满分（80%），达标 ADR-0020（>=2/3）；21612 最弱 1/3、13031 2/3，失败模式均为 no_diff 零编辑
- 消融：关 RepoMap 零损失 16/16；关压缩 15/16、关并发 14/16，损失全集中在 21612
- 监督复验：stagnant 1.3%、监督增量 6.8%（\.71/\.08），ADR-0020 标准 4/5 双双达标
- 压缩验证定案：87 runs 累计（9+78）全部仅 stale_snip，后半段四层零触发
- 产出报告：runs/eval/b10_baseline_report.md + 交接 docs/handoff/2026-08-07-xclaw-baseline-complete.md

**卡在哪 / 怎么解决**
- 实例执行顺序与直觉不符：cli 按 tasks.json 原始顺序过滤而非 --instances 参数顺序（13480 先于 20590）——查 harness 源码确认，不影响结果
- results JSON 只在进程结束才落盘，中途看不到判定——看 db run_end + stats 预判，权威数字等进程结束

**学到的**
- 消融实验的区分度受任务集难度分布限制：8 题全过时三变量开关无差异，损失的区分度全压在 21612 一题上——结论要带"任务集偏易"的限定
- 失败模式随配置迁移（21612：基线 no_diff -> 关并发 f2p:fail）说明配置影响的是修复行为路径而非简单好坏

**明天第一件事**：21612 失败分类（P2 八类），或压缩后半段验证（调低 auto_compact_threshold 0.85->0.5 单题跑）
