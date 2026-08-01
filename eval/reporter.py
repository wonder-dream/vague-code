from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from eval.matrix import EvalCell, TaskResult


def _cell_key(cell: EvalCell) -> str:
    return (f"compression={'1' if cell.compression else '0'}"
            f"_concurrency={'1' if cell.concurrency else '0'}"
            f"_repo_map={'1' if cell.repo_map else '0'}")


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
    lines.append("| 任务ID | 配置 | 通过 | 轮次 | input tokens | run_end_reason |")
    lines.append("|--------|------|------|------|--------------|----------------|")

    for r in sorted(results, key=lambda x: (x.instance_id, _cell_key(x.cell))):
        lines.append(
            f"| {r.instance_id[:40]} | {_cell_key(r.cell)} "
            f"| {'✓' if r.passed else '✗' if r.passed is False else '?'} "
            f"| {r.stats.get('total_turns', '-')} "
            f"| {r.stats.get('total_input_tokens', 0):,} "
            f"| {r.stats.get('run_end_reason', '-')} |"
        )

    # 错误列表
    errors = [r for r in results if r.error]
    if errors:
        lines.append("\n## 错误\n")
        for r in errors:
            lines.append(f"- `{r.instance_id}` ({_cell_key(r.cell)}): {r.error}")

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"Report saved to {output_path}")
