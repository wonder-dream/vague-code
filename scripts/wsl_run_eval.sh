#!/bin/bash
cd /home/vague/vague-code
export PATH="$HOME/.local/bin:$PATH"
source .venv/bin/activate

echo "=== Running 3-task baseline (real API) ==="

python3 << 'PYEOF'
import json, subprocess, os, shutil, sys
from pathlib import Path

sys.path.insert(0, ".")
from vague_code.agent.loop import Agent
from vague_code.agent.config import AgentConfig, MemoryConfig
from vague_code.agent.backend import DeepSeekBackend

api_key = os.environ.get("DEEPSEEK_API_KEY", "")
if not api_key:
    print("ERROR: DEEPSEEK_API_KEY not set")
    sys.exit(1)

all_tasks = json.load(open("eval/tasks.json"))
tasks = all_tasks[:3]
base_workdir = "/tmp/xcode_eval_real"
os.makedirs(base_workdir, exist_ok=True)

def clone_at_commit(repo_url, commit, workdir):
    """Minimal fetch of a specific commit (no full history)."""
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir, exist_ok=True)
    subprocess.run(["git", "init"], cwd=workdir, capture_output=True, check=True, timeout=10)
    subprocess.run(
        ["git", "remote", "add", "origin", repo_url],
        cwd=workdir, capture_output=True, check=True, timeout=10,
    )
    subprocess.run(
        ["git", "fetch", "origin", commit, "--depth=1"],
        cwd=workdir, capture_output=True, check=True, timeout=120,
    )
    subprocess.run(
        ["git", "checkout", "FETCH_HEAD"],
        cwd=workdir, capture_output=True, check=True, timeout=30,
    )

for i, task in enumerate(tasks):
    instance_id = task["instance_id"]
    print(f"\n[{i+1}/3] {instance_id}...")
    
    repo_url = f"https://github.com/{task['repo']}.git"
    commit = task["base_commit"]
    workdir = f"{base_workdir}/{instance_id}"
    
    print(f"  Fetching {repo_url} @ {commit[:12]}...")
    try:
        clone_at_commit(repo_url, commit, workdir)
    except Exception as e:
        print(f"  Clone ERROR: {e}")
        continue
    print(f"  Repo size: {sum(f.stat().st_size for f in Path(workdir).glob('**/*') if f.is_file()) // 1024}KB")
    
    config = AgentConfig(
        max_turns=30, model="deepseek-v4-flash",
        concurrent_tools=False, permission_mode="auto",
        memory=MemoryConfig(enabled=False),
    )
    config.compression.enabled = False
    
    agent = Agent(config, DeepSeekBackend(api_key=api_key))
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
        api_calls = sum(1 for e in events if e.type == "llm_response")
        print(f"  Result: end={reason}, turns={turns}, api_calls={api_calls}, tokens={tokens}")
    except Exception as e:
        print(f"  AGENT ERROR: {type(e).__name__}: {e}")
    
    shutil.rmtree(workdir, ignore_errors=True)

print("\n=== Done ===")
PYEOF
