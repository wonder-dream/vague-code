# Handoff 2026-08-05 深夜：并发调度修复（R7）+ 并发触发率探针 + 8 题双因子小消融（$3.05）

> 本会话完成三件事：① `concurrency.py` 三个缺陷修复（P0 根级 glob/全库 grep 不参与冲突检测、P1 Windows 路径大小写、P2 超时后等待慢任务）并补 8 个回归测试，全量 708 测试通过；② 真实轨迹重放 + 墙钟实验三层验证并发语义；③ 8 题小消融（基线 5/8 vs 关压缩 4/8 vs 关 RepoMap 4/8，$3.05）产出首份真实 pass rate + token/成本对比，并发现两个反直觉结论（压缩 vs KV Cache 张力、短任务只触发 stale_snip）与一个 harness 并行竞态缺陷（U5）。**代码未提交。**

---

## 一、本会话已完成（可验证）

| 项 | 内容 | 证据 |
|---|---|---|
| 修复 P0 | `_extract_scope`：根级 glob（`**/*.py`、`*.py`）与无 path 的 grep 此前提取出空字符串 PREFIX，`_scopes_conflict` 空路径直接跳过 → 与写操作判为不冲突，可与 write_file 并行读到半写状态。改为空 prefix 时返回 `WORKSPACE+READ` | concurrency.py:80-93 |
| 修复 P1 | `_normalize_path` 在 `os.name=="nt"` 时归一化为小写：`SRC/A.PY` vs `src/a.py` 在 Windows 上同一文件，此前判为不冲突；glob prefix 同样归一化（`_pattern_prefix` 只换斜杠） | concurrency.py:50-55, 82 |
| 修复 P2 | `execute_concurrent` 原用 `with ThreadPoolExecutor`，超时抛 TimeoutError 后 with 退出隐式 `shutdown(wait=True)` 等待慢任务（bash 最长 30s）。改为 `try/finally + shutdown(wait=False, cancel_futures=True)` | concurrency.py:175-220 |
| 测试 | 新增 8 个用例：P0×5（scope 归 WORKSPACE、与写冲突、双全库 grep 仍并行）、P1×2（平台分支大小写断言）、P2×1（monkeypatch `_CONCURRENT_TIMEOUT=0.1` + 慢 handler，断言 <1s 返回且标超时）。`test_concurrency.py` 41 个全过，全量 **708 passed**（含全部 tests/） | `uv run pytest tests/ -q` |
| 验证 1 | 真实轨迹重放：探针题 sympy-12481（C_X_M_r0，max_turns 结束）4 个多调用 turn 分组全部正确（turn0/1 read+grep 同组并行，turn2/16 bash+bash 分 2 组串行） | `runs/eval/sympy__sympy-12481__C_X_M_r0.db` |
| 验证 2 | 墙钟实验：read+read 同组 1.00s（并行）、bash+bash 分 2 组 2.00s（串行）、glob根级+write 2.00s（P0 修复生效，修复前 1s 错误并行）；三组结果顺序全部保序 | 临时脚本已删 |
| 探针 1 | 并发触发率统计（历史 109 run / 2613 工具轮）：多调用轮次 **31.6%**，但可并行部分集中于纯读组合（read+read 70、grep+read 69、glob+grep 43…），bash 参与组合 ~410 对全部 WORKSPACE 冲突串行 → **真正可并行 ~10-12% 轮次**，且读操作本地毫秒级收益趋零 | `runs/eval/*.db` 扫描 |
| 消融 | 8 题双因子小消融（tasks_ablation 子集：sympy 6 + sphinx 1 + pytest 1，max_turns 25，k=1，三 cell 并行 + 污染补跑），总成本 **$3.05** | 见第二节 |
| 文档 | known-issues：R7 三修复入"已修复"表、U4（resume×并发重跑）与 U5（harness workdir 竞态）入"未修复"；README 评测结果页写入消融数字 | docs/known-issues.md / README.md |

## 二、8 题小消融数据（2026-08-05，无 checkout 错误，全部 8×3=24 格有数据）

| cell | pass | 输入 token | 缓存命中率 | 成本 | 备注 |
|---|---|---|---|---|---|
| **基线全开 C_X_M** | **5/8 (62.5%)** | 5.65M | 27% | $1.352 | stale_snip 回收 14.1 万 token（5 题触发） |
| 关压缩 nc_X_M | 4/8 | 7.92M | 93% | $0.783 | 输入 token 多 29% |
| 关 RepoMap C_X_nm | 4/8 | 7.01M | 80% | $0.911 | |

逐题（P=pass, F=fail）：12419 F/F/F；12481 P/P/P；15345 P/F/P；20590 F/P/P；21612 F/F/F；23262 P/P/F；sphinx-8595 P/P/P；pytest-7432 P/F/F。

**结论（诚实口径，面试可用）：**
1. pass rate 方向性支持压缩与 RepoMap 设计（基线 5/8 最高），n=8 差异 1 题，只能说"未见损害、方向一致"，不可声称统计显著
2. **压缩 vs KV Cache 张力（最有价值的发现）**：压缩减少 29% 输入 token，但改写历史 → 前缀断裂 → cache 命中率 93%→27%（单 token 价差 4 倍），净成本反而 $0.78→$1.35（+73%）。含义：压缩省的是"重复发送"，代价是"缓存失效"；短任务上压缩的经济账可能是负的
3. **五层流水线在短任务上只有 stale_snip 生效**：microcompact/structured_snip/auto_compact/truncate 全部零触发（8 题均未推到 50%/85% 阈值）。全链路收益需长任务（或降低阈值）验证

## 三、教训与缺陷（本会话暴露）

### U5 — eval harness workdir 跨进程竞态（必读，**已修复 R8**）

`_set_workdir` 的 workdir = `base_dir/instance_id`，**不按 cell 隔离**。三 cell 并行跑同批 instances：进程 A 的 `_force_remove`/clone 删除进程 B 正在使用的目录 → `checkout failed: WinError 2` / clone 冲突。本次 24 个 run 中 4 个被污染（base-12419、nc-23262、nm-12419、nm-23262），靠串行补跑 5 个 run 修复（补跑成本 ~$1）。**已修复（R8）**：workdir 与 `.restore_` 临时目录按 `instance_id__cell_label` 隔离，无 cell 参数调用保持原行为（旧测试兼容）。

### 并行评测正确姿势（已验证）

- 不同 **cell** 可并行（DB/manifest key 按 cell 隔离 ✓）；不同 **instance** 可并行（workdir 不同 ✓）；**同 instance 不同 cell 不可并行**（workdir 共享 ✗）
- 本次事故链：批 1 中 base-12419 与 nm-12419 同 instance 并行 → 杀进程后仍残留 `_force_remove` 破坏 → 补跑时无竞争才成功

## 四、待办（按优先级）

| 优先级 | 事项 | 说明 |
|---|---|---|
| ✅ 已修 | **U5**：`_set_workdir` workdir 加 cell 后缀（R8，本会话顺手完成） | workdir 与 `.restore_` 均按 `instance_id__cell_label` 隔离；`test_eval_harness.py` 21 个用例全过；**下次并行评测前无需再等修复** |
| P1 | 提交本次改动（concurrency.py / test_concurrency.py / harness.py / known-issues / README / 本 handoff） | 全部未提交 |
| P1 | 20 题基线消融 k=3（约 $12-15，8-10 小时并行） | 上一 handoff 的 P1 未变；建议修 U5 后并行跑 |
| P1 | 长任务压缩验证：挑 1-2 道需要 >30 轮的题（或把 auto_compact_threshold 临时调低）验证 microcompact/auto_compact 触发与收益 | 短任务完全测不到五层流水线后半段 |
| P2 | 压缩 × cache 张力的缓解实验：如压缩时保留 system prompt 前缀不变 / 摘要替换而非删除消息（保前缀） | 若成立，是简历上第二个亮点 |

## 五、关键命令

```bash
# 单测
uv run pytest tests/test_concurrency.py -q
uv run pytest tests/ -q --tb=short

# 静态检查
uv run ruff check src/agent/concurrency.py
uv run mypy src/agent/concurrency.py   # 注意：loop.py:315 有既有 mypy error（guidance str/list），与本次无关

# 单 cell 单题探针（--fresh 重跑）
python -m eval.cli --tasks eval/tasks.json --config C_X_M_r0 --repeat 1 --fresh --instances sympy__sympy-12481 --max-turns 25 --max-cost 1 --out eval/report_probe.md

# 8 题双因子消融（修 U5 后可三进程并行；当前需串行或按 instance 分批）
python -m eval.cli --tasks eval/tasks.json --config C_X_M_r0 --repeat 1 --instances <8题> --max-turns 25 --max-cost 2 --out eval/report_base.md
python -m eval.cli --tasks eval/tasks.json --config nc_X_M_r0 --repeat 1 --instances <8题> --max-turns 25 --max-cost 1.5 --out eval/report_nc.md
python -m eval.cli --tasks eval/tasks.json --config C_X_nm_r0 --repeat 1 --instances <8题> --max-turns 25 --max-cost 1.5 --out eval/report_nm.md
```

## 六、成本账（供参考）

本次实验总成本 **$3.05**：8 题基线 $1.35（--fresh 含重跑 12419/23262）+ 关压缩 $0.78 + 关 RepoMap $0.91 + 补跑 ~$0.4。验证了"8 题小消融 $3 以内"的估算；20 题 × k=3 全量约 $12-15。
