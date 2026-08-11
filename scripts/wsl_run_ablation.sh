#!/bin/bash
cd /home/vague/vague-code
export DEEPSEEK_API_KEY="sk-REPLACED"
export PATH="$HOME/.local/bin:$PATH"
source .venv/bin/activate

python3 << 'PYEOF'
import json, subprocess, os, shutil, sys, time
from pathlib import Path

sys.path.insert(0, ".")
from vague_code.agent.loop import Agent
from vague_code.agent.config import AgentConfig, MemoryConfig
from vague_code.agent.backend import DeepSeekBackend

api_key = os.environ.get("DEEPSEEK_API_KEY", "")
BASE = "/tmp/xcode_eval_ablation"
RESULTS_FILE = "eval/results_ablation.json"
os.makedirs(BASE, exist_ok=True)

# Load or init results
results = {}
if os.path.exists(RESULTS_FILE):
    results = json.load(open(RESULTS_FILE))
    print(f"Loaded {len(results)} existing results")

all_tasks = json.load(open("eval/tasks.json"))[:10]

# Build ablation matrix
matrix = []
for compression in [True, False]:
    for concurrency in [True, False]:
        for rep in range(3):
            matrix.append({"compression": compression, "concurrency": concurrency, "repeat": rep})

label = lambda c: f"comp={'1' if c['compression'] else '0'}_conc={'1' if c['concurrency'] else '0'}_r{c['repeat']}"
total = len(matrix) * len(all_tasks)
done = 0

for cell in matrix:
    cl = label(cell)
    for task_idx, task in enumerate(all_tasks):
        iid = task["instance_id"]
        key = f"{iid}__{cl}"
        done += 1
        
        if key in results:
            print(f"[{done}/{total}] SKIP {key}")
            continue
        
        print(f"\n[{done}/{total}] {key}...")
        repo_url = f"https://github.com/{task['repo']}.git"
        commit = task["base_commit"]
        workdir = f"{BASE}/{iid.replace('/', '_')}"
        
        if not os.path.exists(workdir):
            os.makedirs(workdir, exist_ok=True)
            subprocess.run(["git", "init"], cwd=workdir, capture_output=True, timeout=10)
            subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=workdir, capture_output=True, timeout=10)
            try:
                subprocess.run(["git", "fetch", "origin", commit, "--depth=1"], cwd=workdir, capture_output=True, timeout=120)
                subprocess.run(["git", "checkout", "FETCH_HEAD"], cwd=workdir, capture_output=True, timeout=30)
            except Exception as e:
                results[key] = {"error": f"clone: {e}"}
                json.dump(results, open(RESULTS_FILE, "w"), indent=2)
                continue
        else:
            # Reset repo to clean state before each run
            subprocess.run(["git", "checkout", "--", "."], cwd=workdir, capture_output=True, timeout=30)
            subprocess.run(["git", "clean", "-fd"], cwd=workdir, capture_output=True, timeout=30)
        
        config = AgentConfig(max_turns=30, model="deepseek-v4-flash",
            concurrent_tools=cell["concurrency"], permission_mode="auto",
            memory=MemoryConfig(enabled=False))
        config.compression.enabled = cell["compression"]
        
        start = time.time()
        agent = Agent(config, DeepSeekBackend(api_key=api_key))
        try:
            traj = agent.run(task["problem_statement"], workdir)
            events = traj.events
            last = events[-1] if events else None
            reason = last.payload.get("reason", "?") if last else "?"
            turns = sum(1 for e in events if e.type == "turn_start")
            llm = sum(1 for e in events if e.type == "llm_response")
            inp = sum(e.payload.get("usage", {}).get("input_tokens", 0) for e in events if e.type == "llm_response")
            out = sum(e.payload.get("usage", {}).get("output_tokens", 0) for e in events if e.type == "llm_response")
            comp_events = [e for e in events if e.type == "compression"]
            reclaim = sum(e.payload.get("before_tokens", 0) - e.payload.get("after_tokens", 0) for e in comp_events if e.payload.get("before_tokens"))
            results[key] = {
                "cell": cell, "reason": reason, "turns": turns, "llm": llm,
                "input_tokens": inp, "output_tokens": out,
                "compression_events": len(comp_events),
                "total_reclaimed": reclaim,
                "elapsed_s": round(time.time() - start, 1),
            }
            print(f"  {reason} turns={turns} inp={inp//1000}K out={out//1000}K reclaim={reclaim//1000}K {round(time.time()-start)}s")
        except Exception as e:
            results[key] = {"cell": cell, "error": f"agent: {e}"}
            print(f"  ERROR: {e}")
        
        json.dump(results, open(RESULTS_FILE, "w"), indent=2)

# Summary
print("\n" + "="*70)
print("ABLATION RESULTS")
print("="*70)
cells = {}
for k, v in results.items():
    if "cell" not in v: continue
    cl = label(v["cell"])
    cells.setdefault(cl, []).append(v)

for cl in sorted(cells.keys()):
    items = cells[cl]
    end = sum(1 for r in items if r.get("reason") == "end_turn")
    avg_inp = sum(r.get("input_tokens", 0) for r in items) // max(len(items), 1)
    avg_reclaim = sum(r.get("total_reclaimed", 0) for r in items) // max(len(items), 1)
    print(f"{cl}: end_turn={end}/{len(items)} avg_inp={avg_inp//1000}K reclaim={avg_reclaim//1000}K")

print("\nResults saved to", RESULTS_FILE)
PYEOF
