#!/bin/bash
cd /home/vague/vague-code
export DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY"
export PATH="$HOME/.local/bin:$PATH"
source .venv/bin/activate
nohup bash scripts/wsl_run_baseline.sh > eval/run_baseline.log 2>&1 &
echo "PID: $!"
