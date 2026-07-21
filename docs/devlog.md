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