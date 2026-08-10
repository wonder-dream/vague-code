#!/bin/bash
cd /home/vague/vague-code
export DEEPSEEK_API_KEY="sk-REPLACED"
export PATH="$HOME/.local/bin:$PATH"
source .venv/bin/activate

python3 << 'PYEOF'
import json
from pathlib import Path
from collections import defaultdict

results = json.load(open("eval/results_ablation.json"))

# Group by configuration
cells_data = defaultdict(lambda: {"pass": 0, "total": 0, "tokens": 0, "turns": 0, "reclaim": 0})

for key, r in results.items():
    if "cell" not in r: continue
    c = r["cell"]
    label = f"comp={'1' if c['compression'] else '0'}_conc={'1' if c['concurrency'] else '0'}"
    cells_data[label]["total"] += 1
    if r.get("reason") == "end_turn":
        cells_data[label]["pass"] += 1
    cells_data[label]["tokens"] += r.get("input_tokens", 0) + r.get("output_tokens", 0)
    cells_data[label]["turns"] += r.get("turns", 0)
    cells_data[label]["reclaim"] += r.get("total_reclaimed", 0)

# Markdown report
lines = [
    "# vague-code Ablation Results",
    "",
    "| Compression | Concurrency | Pass Rate | Avg Turns | Avg Tokens | Reclaimed |",
    "|------------|-------------|-----------|-----------|------------|-----------|",
]

for label in sorted(cells_data.keys()):
    d = cells_data[label]
    comp = "✓" if "comp=1" in label else "✗"
    conc = "✓" if "conc=1" in label else "✗"
    avg_tokens = d["tokens"] // max(d["total"], 1)
    avg_turns = d["turns"] / max(d["total"], 1)
    pass_rate = f"{d['pass']}/{d['total']} ({d['pass']*100//max(d['total'],1)}%)"
    reclaim_k = d["reclaim"] // 1000
    lines.append(f"| {comp} | {conc} | {pass_rate} | {avg_turns:.1f} | {avg_tokens:,} | {reclaim_k}K |")

lines.append("")
lines.append("## Baseline (no compression, 30 tasks)")
baseline = json.load(open("eval/results_baseline.json"))
b_pass = sum(1 for r in baseline.values() if r.get("reason") == "end_turn")
b_total = len(baseline)
b_tokens = sum(r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in baseline.values())
b_turns = sum(r.get("turns", 0) for r in baseline.values())
lines.append(f"- Pass rate: {b_pass}/{b_total} ({b_pass*100//max(b_total,1)}%)")
lines.append(f"- Avg tokens: {b_tokens//max(b_total,1):,}")
lines.append(f"- Avg turns: {b_turns/max(b_total,1):.1f}")
lines.append(f"- Total cost: ~¥{(b_tokens * 0.025 / 1_000_000 + b_tokens * 0.025 / 1_000_000):.2f} (estimated)")

json.dump(cells_data, open("eval/summary_cache.json", "w"), indent=2)

Path("eval/results.md").write_text("\n".join(lines), encoding="utf-8")
print("Report saved to eval/results.md")
PYEOF
