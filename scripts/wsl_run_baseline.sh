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
if not api_key: print("ERROR: DEEPSEEK_API_KEY not set"); sys.exit(1)

BASE = "/tmp/xcode_eval_real"
RESULTS_FILE = "eval/results_baseline.json"
os.makedirs(BASE, exist_ok=True)

# Load or init results
results = {}
if os.path.exists(RESULTS_FILE):
    results = json.load(open(RESULTS_FILE))
    print(f"Loaded {len(results)} existing results")

all_tasks = json.load(open("eval/tasks.json"))
print(f"Total tasks: {len(all_tasks)}")

for idx, task in enumerate(all_tasks):
    iid = task["instance_id"]
    if iid in results and results[iid].get("reason"):
        print(f"[{idx+1}/{len(all_tasks)}] SKIP {iid} (already done)")
        continue
    
    print(f"\n[{idx+1}/{len(all_tasks)}] {iid}...")
    repo_url = f"https://github.com/{task['repo']}.git"
    commit = task["base_commit"]
    workdir = f"{BASE}/{iid.replace('/', '_')}"
    
    # Clone
    if not os.path.exists(workdir):
        os.makedirs(workdir, exist_ok=True)
        subprocess.run(["git", "init"], cwd=workdir, capture_output=True, timeout=10)
        subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=workdir, capture_output=True, timeout=10)
        try:
            subprocess.run(["git", "fetch", "origin", commit, "--depth=1"], cwd=workdir, capture_output=True, timeout=120)
            subprocess.run(["git", "checkout", "FETCH_HEAD"], cwd=workdir, capture_output=True, timeout=30)
        except Exception as e:
            results[iid] = {"error": f"clone: {e}"}
            json.dump(results, open(RESULTS_FILE, "w"), indent=2)
            continue
    
    # Run agent
    config = AgentConfig(max_turns=30, model="deepseek-v4-flash",
        concurrent_tools=False, permission_mode="auto",
        memory=MemoryConfig(enabled=False))
    config.compression.enabled = False
    
    start = time.time()
    agent = Agent(config, DeepSeekBackend(api_key=api_key))
    try:
        traj = agent.run(task["problem_statement"], workdir)
        events = traj.events
        last = events[-1] if events else None
        reason = last.payload.get("reason", "?") if last else "?"
        turns = sum(1 for e in events if e.type == "turn_start")
        tc = sum(1 for e in events if e.type == "tool_call")
        tr = sum(1 for e in events if e.type == "tool_result")
        llm = sum(1 for e in events if e.type == "llm_response")
        inp = sum(e.payload.get("usage", {}).get("input_tokens", 0) for e in events if e.type == "llm_response")
        out = sum(e.payload.get("usage", {}).get("output_tokens", 0) for e in events if e.type == "llm_response")
        elapsed = time.time() - start
        results[iid] = {
            "reason": reason, "turns": turns,
            "tool_calls": tc, "tool_results": tr, "llm_calls": llm,
            "input_tokens": inp, "output_tokens": out,
            "elapsed_s": round(elapsed, 1),
        }
        print(f"  end={reason} turns={turns} llm={llm} inp={inp//1000}K out={out//1000}K {elapsed:.0f}s")
    except Exception as e:
        results[iid] = {"error": f"agent: {e}"}
        print(f"  ERROR: {e}")
    
    json.dump(results, open(RESULTS_FILE, "w"), indent=2)
    shutil.rmtree(workdir, ignore_errors=True)

# Summary
print("\n" + "="*60)
print("BASELINE RESULTS (no compression, no concurrency)")
print("="*60)
passed = sum(1 for r in results.values() if r.get("reason") == "end_turn")
tokens = sum(r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in results.values())
total_turns = sum(r.get("turns", 0) for r in results.values())
print(f"Completed: {sum(1 for r in results.values() if r.get('reason'))}/{len(all_tasks)}")
print(f"end_turn:  {passed}")
print(f"Total turns: {total_turns}")
print(f"Total tokens: {tokens:,}")
print(f"Avg tokens/task: {tokens // max(len(results), 1):,}")
PYEOF
