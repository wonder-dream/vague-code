# Handoff 2026-08-06：Phase 1 小样本验收全过（$2.96）+ 10 题基线已定案被中断 + 机器环境大清理

> 本会话完成两件事：① **Phase 1 小样本验收**（3 题 × k=3 × 40 轮 + supervisor，9 runs，$2.96）——ADR-0020 验收标准 3/4/5 **全部达成**，报告在 `runs/eval/p1_k3_acceptance.md`；② **10 题全量基线已定案**（8 题消融集 + 13031/13480，含消融层 ofat，78 runs ≈ $26-30），15:52 启动 3 进程并行，**因机器卡顿排查被中断**（跑 ~15 分钟，无有效结果）。③ 排查并解决了机器卡顿（元凶 Chill With You 挂机游戏 + 网盘 shell 扩展 + 三套杀软冗余），顺带做了一轮进程/自启大清理（迅雷彻底根除等）——**环境已为基线长跑就绪**。代码零改动。

---

## 一、本会话已完成（可验证）

### 1. Phase 1 小样本验收（主任务，✅ 全过）

| 验收标准（ADR-0020） | 结果 | 证据 |
|---|---|---|
| 3. pass^3 ≥ 2/3 | ✅ **2/3 题** | 21612 ✓✓✓、24213 ✓✓✓、16792 ✗✓✓（r0 no_diff 零编辑，行为与 08-05 验收一致） |
| 4. stagnant < 15% | ✅ **11%**（1/9） | 24213 r1 判停于编辑完成后验证收尾（run verified=True，**非误杀**） |
| 5. 监督增量 < 15% | ✅ **2.3%-10.0%，均值 5.4%** | sup_calls 6-14/run |

- 监督行为（66 调用）：on_track 26 / needs_verification 26 / stuck 10 / off_track 1 / 解析失败跳过 3
- stuck 无误杀正例：16792 r0 turn 18 stuck 后 turn 24 恢复 on_track（探索在动），40 轮零编辑未判 stagnant
- off_track 纠偏正例：21612 r0 t18 准确定位应改 `_parse_latex.py convert_frac` 而非 printing 代码
- 成本口径：9 runs $2.96（均值 $0.33/run）——**全量成本估算以此为准**（修正 handoff 原估）
- 速度实测：3 runs/实例 ≈ 35-50 分钟（**比 handoff 预估快 ~3 倍**，DeepSeek 服务今天较快）

### 2. 压缩验证（副产物，结论明确）

9 个 40 轮 run 的 compression 事件：**全部仅 stale_snip（39-40 次/run），microcompact / structured_snip / auto_compact / truncate 零触发**。
→ 40 轮内 token 利用率达不到 50%/65%/85% 阈值；**后半段无收益可测，需 >40 轮或调低阈值**（保留为后续实验）。

### 3. 10 题基线已定案、被中断（下一步主任务）

- **定案**：核心层 10 题 = 8 题消融集（12419/12481/15345/20590/21612/23262/sphinx-8595/pytest-7432）+ 13031/13480；含消融层（ofat：3 关闭配置 × k=2）
- 总 runs = 10×3 + 8×3×2 = **78**，预算 **$26-30**（--max-cost 12/12/8 三进程分配）
- **中断**：15:52 启动 → ~16:05 因排查机器卡顿 taskkill 全部 3 进程。无有效结果（仅 12419 r0 跑了 ~15 轮）。**子集文件已生成**：`%TEMP%\opencode\ablation_p{1,2,3}.json`（在临时目录，重跑需重新生成或保留）
- 环境现已就绪（机器清理见下），可直接重跑

### 4. 机器环境大清理（非项目代码，但为基线长跑扫清障碍）

| 项 | 处理 |
|---|---|
| 卡顿元凶 | **Chill With You**（Steam 挂机游戏，GPU 30-50% + CPU 131.9min/4.8h）→ 杀（用户要留着，玩完记得退） |
| 杀软 | 卸载微软电脑管家 + 阿里保护（AlibabaProtect 服务删除），**只留火绒** |
| explorer shell 扩展 | 123云盘/百度网盘/OneDrive 共 20 CLSID LoadWithoutCOM + 2 DLL 改名（`.disabled`），全部生效；恢复=删值/改回名 |
| 进程/自启清理 | 迅雷（XLServicePlatform 禁用 + Edge 扩展禁用 + BHO 目录改名，**彻底**）、深信服 eaio_service（服务删除）、Xbox 游戏栏/手机连接/Widgets/CmdPal/OneDrive/WPS/百度网盘/Ableton/Epic/Adobe CC/Discord/MuseHub/RadminVPN/Ollama 自启 |
| 保留 | Chill With You/Steam/Wallpaper/Edge/网易云/QQ/微信/Typora/PowerToys/火绒/RIME/clash-verge/GHelper/Everything/aTrust |

---

## 二、待办（按优先级）

| 优先级 | 事项 | 说明 |
|---|---|---|
| **P0** | **10 题基线重跑**（含消融层，78 runs，$26-30，5-7h） | 3 进程并行（核心+消融子集切分见下节命令）；产出 pass^3 + 消融对比（压缩/RepoMap/并发），统计口径对齐 ADR-0020 |
| P1 | 压缩后半段验证 | 调低 `auto_compact_threshold`（0.85→0.5）单题跑，验证 microcompact/auto_compact 触发与收益；或找 >40 轮任务 |
| P2 | 压缩 × KV Cache 张力缓解实验 | 保前缀/摘要替换（08-05 handoff 原项，若成立是简历亮点） |
| P2 | 权限矩阵 safe 档 / gold 轨迹标注 / judge 抽评 30 条 | 早前 handoff 原项未动 |
| P3 | 环境策展 | sklearn/astropy 8 题待 Linux/CI（MSVC 编译依赖）；sphinx-8721 判别器不复现 |

---

## 三、关键命令

```bash
# 单测（无代码改动，仅回归参考）
python -m pytest tests/ -q --ignore=tests/tui --ignore=tests/test_repomap.py --ignore=tests/test_truncate.py

# 10 题基线：3 进程并行（每进程一个终端；全部 --fresh；实例集互斥无 workdir 冲突）
# P1: 核心 12419,12481,15345,13031 + 消融子集 p1
python -m eval.cli --tasks eval/tasks.json --instances sympy__sympy-12419,sympy__sympy-12481,sympy__sympy-15345,sympy__sympy-13031 --ablation-tasks %TEMP%\opencode\ablation_p1.json --design ofat --repeat 3 --ablation-repeat 2 --max-turns 40 --supervisor --fresh --max-cost 12 --out runs/eval/b10_p1.md
# P2: 核心 20590,21612,23262,13480 + 消融子集 p2（max-cost 12）
# P3: 核心 sphinx-8595,pytest-7432 + 消融子集 p3（max-cost 8）
```

> 注意：ablation 子集 json 在临时目录（`C:\Users\vague-dream\AppData\Local\Temp\opencode\`），若被清理，从 `eval/tasks_ablation.json` 按 instance_id 过滤重新生成（P1: 3 题、P2: 3 题、P3: 2 题）。

## 四、坑与注意事项（本会话新增）

1. **后台长跑的正确姿势**：CLI 串行 for 循环（harness.py:364），并行=多进程；WMI 启动可脱离 bash 工具进程树（`([wmiclass]'Win32_Process').Create('cmd /c cd /d D:\document\xcode && python -m eval.cli ... > log 2>&1')`）；`Start-Process`/`cmd start /b` 启动的进程会被 bash 工具超时连带杀掉
2. **多进程全用 `--fresh`**：manifest 整表读改写（harness.py:42-46），跨进程会互相覆盖；--fresh 免除依赖，代价是重复 cell 重跑
3. **stdout 全缓冲**：eval 日志文件在进程结束前基本为空，进度看轨迹 db（`runs/eval/*__C_X_M_r*.db` 的 events 表，按 run_id 过滤）
4. 机器已清理，但 **Chill With You 会随 Steam 复活**（用户要留着）——长跑期间它吃 GPU 不影响 CPU 型 eval，但若又卡，先看 `nvidia-smi -l 2`
5. 早前 handoff 的坑继续有效：网络/缓存、manifest 语义、PowerShell 引号（长查询写临时 .py）、Windows 文件锁（_force_remove）、run_id 过滤、cache 单价 0.07

## 五、面试故事线增量

1. 验收闭环：监督实现后 k=1 是抽签 → **k=3 统计口径**（pass^3 2/3 题、stagnant 11%、监督成本 5.4%）——ADR-0020 验收 3/4/5 首次全绿，数字可报
2. **压缩实证发现**：40 轮长任务仍只触发 stale_snip（9/9 run）→ 五层流水线后半段在短任务集上无收益，设计假设被数据修正（诚实口径）
3. 环境工程（旁支）：Windows 评测机的性能诊断——卡顿排查从"句柄泄漏"（不是）→ GPU 元凶（挂机游戏）→ shell 扩展（网盘×3）→ 三套杀软冗余，用 perfmon/nvidia-smi 数据链定位，非猜测

## 六、参考文件

- Phase 1 验收报告：`runs/eval/p1_k3_acceptance.md`（本会话产出，数据齐）
- 结果数据：`runs/eval/results_20260806-{150535,151758,153647}.json`（16792/24213/21612 各 3 cell；`results_20260806-144209.json` 是 p1_test 中断残留，忽略）
- 轨迹：`runs/eval/sympy__sympy-{16792,21612,24213}__C_X_M_r{0,1,2}.db`（新 run 见验收报告）
- 中断残留：`runs/eval/sympy__sympy-12419__C_X_M_r0.db`（16:11 写入、run 无 run_end，重跑 --fresh 覆盖即可，无需清理）
- 决策/计划：`docs/adr/0020-supervision-agent.md`、`docs/plans/0018-supervision-agent.md`
- 上一交接：`docs/handoff/2026-08-05-xclaw-supervision-implementation.md`（P1 口径、坑、命令）
