#!/bin/bash
set -e

cd /home/vague/vague-code

# Source uv
export PATH="$HOME/.local/bin:$PATH"

# Set up Python
uv python install 3.12
uv venv
source .venv/bin/activate

# Install deps
uv pip install datasets swebench tiktoken

echo "=== Environment ready ==="
which python3
python3 --version
