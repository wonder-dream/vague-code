#!/bin/bash
# WSL eval runner - reads API key from environment
cd /home/vague/xcode
export PATH="$HOME/.local/bin:$PATH"
source .venv/bin/activate

echo "=== Running 3-task baseline (real API) ==="

python3 << 'PYEOF'
import json, subprocess, os, shutil, sys
from pathlib import Path

sys.path.insert(0, ".")
from src.agent.loop import Agent
from src.agent.config import AgentConfig, MemoryConfig
from src.agent.backend import DeepSeekBackend

api_key = os.environ.get("DEEPSEEK_API_KEY", "")
if not api_key:
    print("ERROR: DEEPSEEK_API_KEY not set")
    sys.exit(1)

all_tasks = json.load(open("eval/tasks.json"))
tasks = all_tasks[:3]
base_workdir = "/tmp/xcode_eval_real"
os.makedirs(base_workdir, exist_ok=True)

for i, task in enumerate(tasks):
    instance_id = task["instance_id"]
    print(f"\n[{i+1}/3] {instance_id}...")
    
    repo_url = f"https://github.com/{task['repo']}.git"
    commit = task["base_commit"]
    workdir = f"{base_workdir}/{instance_id}"
    
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
    
    print(f"  Cloning {repo_url} @ {commit[:8]}...")
    subprocess.run(
        ["git", "clone", "--depth=1", repo_url, workdir],
        capture_output=True, timeout=300, check=True,
    )
    subprocess.run(
        ["git", "checkout", commit],
        cwd=workdir, capture_output=True, timeout=30, check=True,
    )
    print(f"  Repo ready.")
    
    config = AgentConfig(
        max_turns=30, model="deepseek-v4-flash",
        concurrent_tools=False, permission_mode="auto",
        memory=MemoryConfig(enabled=False),
    )
    config.compression.enabled = False
    
    agent = Agent(config, DeepSeekBackend())
    try:
        traj = agent.run(task["problem_statement"], workdir)
        events = traj.events
        last = events[-1] if events else None
        reason = last.payload.get("reason", "?") if last else "?"
        turns = sum(1 for e in events if e.type == "turn_start")
        tokens = sum(
            e.payload.get("usage", {}).get("input_tokens", 0) + e.payload.get("usage", {}).get("output_tokens", 0)
            for e in events if e.type == "llm_response"
        )
        print(f"  Result: end={reason}, turns={turns}, tokens={tokens}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
    
    shutil.rmtree(workdir, ignore_errors=True)

print("\n=== Done ===")
PYEOF
