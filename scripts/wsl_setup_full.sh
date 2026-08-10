#!/bin/bash
set -e

echo "=== Setting up vague-code in WSL2 ==="

cd /home/vague/vague-code || {
    # Copy project if not exists
    cp -r /mnt/d/document/vague-code /home/vague/
    cd /home/vague/vague-code
}

export PATH="$HOME/.local/bin:$PATH"

# Install uv if not present
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# Clean venv and recreate
rm -rf .venv
uv venv
source .venv/bin/activate

# Install dependencies
uv pip install datasets swebench tiktoken

echo "=== Checking SWE-bench Lite ==="
python3 -c "
from datasets import load_dataset
ds = load_dataset('princeton-nlp/SWE-bench_Lite', split='test')
print(f'OK: {len(ds)} tasks loaded')
"

echo "=== Checking eval framework ==="
python3 -c "
from eval.matrix import build_matrix
m = build_matrix(3)
print(f'Matrix: {len(m)} cells')
from eval.reporter import generate_report
print(f'Reporter: OK')
"

echo "=== All ready ==="
