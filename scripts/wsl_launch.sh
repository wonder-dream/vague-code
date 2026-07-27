#!/bin/bash
export DEEPSEEK_API_KEY="sk-REPLACED"
cd /home/vague/xcode
export PATH="$HOME/.local/bin:$PATH"
source .venv/bin/activate
exec bash scripts/wsl_run_baseline.sh
