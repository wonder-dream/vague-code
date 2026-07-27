#!/bin/bash
cd /home/vague/xcode
export DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY"
export PATH="$HOME/.local/bin:$PATH"
source .venv/bin/activate
nohup bash scripts/wsl_run_baseline.sh > eval/run_baseline.log 2>&1 &
echo "PID: $!"
