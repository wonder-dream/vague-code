from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from eval.matrix import EvalCell, TaskResult


def _cell_key(cell: EvalCell) -> str:
    return (f"compression={'1' if cell.compression else '0'}"
            f"_concurrency={'1' if cell.concurrency else '0'}"
            f"_repo_map={'1' if cell.repo_map else '0'}")


def _passk(by_cell: dict[str, list[TaskResult]]) -> tuple[list[tuple[str, int, int, int]], int, int]:
    """τ-bench pass^k：同一 (任务, 配置) 的 k 次重复全部 verified 才计过。

    度量可靠性而非单次运气（消融实验因变量升级为 pass^k）。
    """
    rows: list[tuple[str, int, int, int]] = []
    total_num = total_den = 0
    for key in sorted(by_cell.keys()):
        by_inst: dict[str, list[TaskResult]] = defaultdict(list)
        for r in by_cell[key]:
            by_inst[r.instance_id].append(r)
        k = len({r.cell.repeat for r in by_cell[key]})
        if k == 0:
            continue
        num = sum(1 for rs in by_inst.values() if all(r.verified is True for r in rs))
        den = len(by_inst)
        total_num += num
        total_den += den
        rows.append((key, k, num, den))
    return rows, total_num, total_den


def generate_report(results: list[TaskResult], output_path: str) -> None:
    # 按 cell 聚合
    by_cell: dict[str, list[TaskResult]] = defaultdict(list)
    for r in results:
        by_cell[_cell_key(r.cell)].append(r)

    lines: list[str] = []
    lines.append("# 消融实验结果\n")
    lines.append(f"总任务数: {len(set(r.instance_id for r in results))}")
    lines.append(f"总运行次数: {len(results)}\n")

    # 汇总表
    lines.append("## 汇总\n")
    lines.append("| 压缩 | 并发 | RepoMap | 重复 | 通过率 | 平均轮次 | 平均 input tokens | code_search | stale回收 | micro回收 | ssnip回收 | auto回收 | truncate回收 |")
    lines.append("|------|------|---------|------|--------|----------|-------------------|-------------|-----------|-----------|-----------|----------|--------------|")

    for key in sorted(by_cell.keys()):
        cell_results = by_cell[key]
        cell = cell_results[0].cell
        passed = [r for r in cell_results if r.passed is True]

        total_tokens = sum(r.stats.get("total_input_tokens", 0) for r in cell_results)
        total_turns = sum(r.stats.get("total_turns", 0) for r in cell_results)
        n = len(cell_results)

        code_search = sum(r.stats.get("code_search_calls", 0) for r in cell_results)
        stale = sum(r.stats.get("stale_snip_reclaimed", 0) for r in cell_results)
        micro = sum(r.stats.get("microcompact_reclaimed", 0) for r in cell_results)
        ssnip = sum(r.stats.get("structured_snip_reclaimed", 0) for r in cell_results)
        auto_ = sum(r.stats.get("auto_compact_reclaimed", 0) for r in cell_results)
        trun = sum(r.stats.get("truncate_reclaimed", 0) for r in cell_results)

        pass_rate = f"{len(passed) / n * 100:.0f}%" if n > 0 else "-"
        avg_turns = f"{total_turns / n:.1f}" if n > 0 else "-"
        avg_tokens = f"{total_tokens // n:,}" if n > 0 else "-"

        lines.append(
            f"| {'✓' if cell.compression else '✗'} | {'✓' if cell.concurrency else '✗'} "
            f"| {'✓' if cell.repo_map else '✗'} | {cell.repeat} | {pass_rate} | {avg_turns} | {avg_tokens} "
            f"| {code_search:,} | {stale:,} | {micro:,} | {ssnip:,} | {auto_:,} | {trun:,} |"
        )

    # 每任务的细节
    lines.append("\n## 逐任务细节\n")
    lines.append("| 任务ID | 配置 | 通过 | verified | 判定 | 轮次 | input tokens | run_end_reason |")
    lines.append("|--------|------|------|----------|------|------|--------------|----------------|")

    for r in sorted(results, key=lambda x: (x.instance_id, _cell_key(x.cell))):
        lines.append(
            f"| {r.instance_id[:40]} | {_cell_key(r.cell)} "
            f"| {'✓' if r.passed else '✗' if r.passed is False else '?'} "
            f"| {'✓' if r.verified else '✗' if r.verified is False else '-'} "
            f"| {r.verdict_reason or '-'} "
            f"| {r.stats.get('total_turns', '-')} "
            f"| {r.stats.get('total_input_tokens', 0):,} "
            f"| {r.stats.get('run_end_reason', '-')} |"
        )

    # pass^k 可靠性（仅当存在真验收结果时）
    has_verified = any(r.verified is not None for r in results)
    if has_verified:
        rows, total_num, total_den = _passk(by_cell)
        lines.append("\n## pass^k 可靠性（τ-bench：k 次全过才计过）\n")
        lines.append("| 配置 | k | 全过任务数 | 任务总数 | pass^k |")
        lines.append("|------|---|------------|----------|--------|")
        for key, k, num, den in rows:
            lines.append(f"| {key} | {k} | {num} | {den} | {num / den * 100:.0f}% |")
        if total_den:
            lines.append(
                f"\n整体 pass^k: {total_num}/{total_den} = {total_num / total_den * 100:.0f}%"
            )

    # P0.5 确定性轨迹指标
    if any(r.stats.get("metrics") for r in results):
        lines.append("\n## 轨迹指标（P0.5 确定性，平均 per run）\n")
        lines.append("| 配置 | 工具数 | 冗余read | 冗余grep | 错误调用 | read→edit | edit→test | 权限deny | 触碰测试文件 |")
        lines.append("|------|--------|----------|----------|----------|-----------|-----------|----------|---------------|")
        for key in sorted(by_cell.keys()):
            ms = [r.stats["metrics"] for r in by_cell[key] if r.stats.get("metrics")]
            if not ms:
                continue
            n = len(ms)

            def avg(k: str) -> float:
                return round(sum(m.get(k, 0) for m in ms) / n, 2)

            touches = sum(
                1 for r in by_cell[key] if r.stats.get("touches_test_files"))
            lines.append(
                f"| {key} | {avg('tool_total')} | {avg('redundant_reads')} "
                f"| {avg('redundant_greps')} | {avg('error_calls')} "
                f"| {avg('read_before_edit_rate')} | {avg('edit_then_test')} "
                f"| {avg('permission_denies')} | {touches} |"
            )

    # P2 失败模式分布（仅当存在真验收结果时）
    if has_verified:
        from eval.classify import CLASS_LABELS, classify

        counts: dict[str, int] = {}
        for r in results:
            cls = classify(r)
            counts[cls] = counts.get(cls, 0) + 1
        lines.append("\n## 失败模式分布（P2 分类）\n")
        lines.append("| 类别 | 数量 | 占比 |")
        lines.append("|------|------|------|")
        for cls in sorted(counts, key=lambda c: -counts[c]):
            pct = counts[cls] / len(results) * 100
            lines.append(f"| {CLASS_LABELS[cls]} | {counts[cls]} | {pct:.0f}% |")

    # 错误列表
    errors = [r for r in results if r.error]
    if errors:
        lines.append("\n## 错误\n")
        for r in errors:
            lines.append(f"- `{r.instance_id}` ({_cell_key(r.cell)}): {r.error}")

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"Report saved to {output_path}")
