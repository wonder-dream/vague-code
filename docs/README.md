# XClaw 文档索引

XClaw 的文档按用途分层。**文章（articles/）是面向阅读的成品文档**；ADR / 计划 / 交接是工程记录。

---

## 阅读入口

| 想做什么 | 去哪里 |
|----------|--------|
| 从零了解 XClaw（架构/子系统/教程/参考） | [`articles/README.md`](articles/README.md)（24 篇成品文章，6 阶段阅读路径） |
| 跑评测 / 理解评测体系 | [`../eval/README.md`](../eval/README.md)（现行）+ [`plans/0016-eval-methods.md`](plans/0016-eval-methods.md)（设计）+ [`handoff/2026-08-03-xclaw-eval-system.md`](handoff/2026-08-03-xclaw-eval-system.md)（重建与策展全记录） |
| 项目总览与验收标准 | [`Coding Agent 项目开发文档.md`](Coding%20Agent%20项目开发文档.md)（主项目文档） |
| 遇到问题 | [`known-issues.md`](known-issues.md) + [`articles/troubleshooting.md`](articles/troubleshooting.md) + [`articles/faq.md`](articles/faq.md) |
| 面试准备 | [`interview/design-questions.md`](interview/design-questions.md) + [`articles/README.md`](articles/README.md) 的阅读路径 |

---

## 目录结构

| 目录 | 内容 | 状态 |
|------|------|------|
| [`articles/`](articles/) | 24 篇成品文章（概念 0-2 / 子系统 3-12 / 教程 T1-T4 / 参考 R1-R5 / 补充） | ✅ 现行 |
| [`adr/`](adr/) | 18 篇架构决策记录（0001-0018） | ✅ 历史决策 |
| [`plans/`](plans/) | 16 篇实现方案（0001-0016） | ✅ 历史计划 |
| [`handoff/`](handoff/) | 会话交接记录（含 2026-08-03 评测体系全量总结） | ✅ 历史 |
| [`audit/`](audit/) | 5 篇代码审计报告（batch-01 ~ batch-05） | ✅ 历史 |
| [`reviews/`](reviews/) | 1 篇代码审查 | ✅ 历史 |
| [`blog/`](blog/) | 1 篇技术博客（压缩流水线） | ✅ 可引用 |
| [`interview/`](interview/) | 面试设计问题 | ✅ 现行 |

> 已清理：`guide/`、`tutorials/`、`reference/`（细纲草稿，已被 articles/ 成稿取代）、
> 根目录 `faq.md`/`troubleshooting.md`（成稿在 articles/）、`DOCUMENTATION_PLAN.md`（写作计划已完成）。

---

## 根目录文档

| 文档 | 用途 |
|------|------|
| [`Coding Agent 项目开发文档.md`](Coding%20Agent%20项目开发文档.md) | 项目主文档：定位、量化验收标准、架构、四周边计划、简历预案 |
| [`known-issues.md`](known-issues.md) | 已知问题与未修复 bug 跟踪 |
| [`devlog.md`](devlog.md) | 开发日志（Day 1 起） |
| [`architecture.drawio`](architecture.drawio) | 架构图（Draw.io） |

---

## 评测体系导航（2026-08 升级后）

```
eval/README.md                    现行用法（CLI、模块表、已知限制）
docs/plans/0016-eval-methods.md   设计（P0 真验收 → P0.5 轨迹指标 → P1 judge → P2 分类）
docs/handoff/2026-08-03-xclaw-eval-system.md  全量总结（重建、策展、踩坑、待办、面试故事）
docs/articles/12-evaluation-harness.md        v0.1 结构讲解（含过时声明）
docs/articles/T4-running-ablation-experiments.md  v0.1 消融教程（含过时声明）
```
