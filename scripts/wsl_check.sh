#!/bin/bash
set -e
cd /home/vague/vague-code
export PATH="$HOME/.local/bin:$PATH"
source .venv/bin/activate

uv pip install datasets swebench tiktoken 2>&1 | tail -5

python3 -c "
from datasets import load_dataset
ds = load_dataset('princeton-nlp/SWE-bench_Lite', split='test')
print(f'OK: {len(ds)} tasks loaded')
"
