#!/bin/bash
cd /home/vague/vague-code
export PATH="$HOME/.local/bin:$PATH"
source .venv/bin/activate

echo "Installing project dependencies..."
uv sync 2>&1 | tail -5
echo "Done"
