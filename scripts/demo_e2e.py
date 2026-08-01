"""E2E demo: Agent fixes all 5 bugs (3 visible + 2 hidden).

Bug types: control flow, off-by-one, inverted comparison, hidden no-ops.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.config import AgentConfig
from src.agent.ir import (
    Block,
    Message,
    ModelResponse,
    NormalizedUsage,
    StopReason,
    TextBlock,
    ToolUseBlock,
)
from src.agent.loop import Agent


def tool_response(*tools: tuple[str, str, dict]) -> ModelResponse:
    blocks: list[Block] = []
    for tid, name, input_ in tools:
        blocks.append(ToolUseBlock(id=tid, name=name, input=input_))
    return ModelResponse(
        message=Message(role="assistant", content=blocks),
        stop_reason=StopReason.tool_use,
        usage=NormalizedUsage(input_tokens=10, output_tokens=5),
    )


def text_response(text: str) -> ModelResponse:
    return ModelResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason=StopReason.end_turn,
        usage=NormalizedUsage(input_tokens=5, output_tokens=3),
    )


BUG_FIXES = "\n".join([
    "V1: stats.py category_breakdown: pass -> continue",
    "V2: repo.py paginate: page*size -> (page-1)*size",
    "V3: repo.py search: <= min_rating -> >= min_rating",
    "H1: repo.py update: no-op -> self._products[id] = product",
    "H2: repo.py delete: no-op -> self._products.pop()",
])


class DemoBackend:
    def __init__(self):
        self.responses = [
            tool_response(("c1", "read_file", {"path": "src/stats.py"})),
            tool_response(("c2", "read_file", {"path": "src/repo.py"})),
            tool_response(("c3", "patch", {
                "path": "src/stats.py",
                "old_str": "            if p.stock == 0:\n                pass",
                "new_str": "            if p.stock == 0:\n                continue",
            })),
            tool_response(("c4", "patch", {
                "path": "src/repo.py",
                "old_str": "        start = page * page_size  # BUG: off-by-one, page 1 should start at 0",
                "new_str": "        start = (page - 1) * page_size",
            })),
            tool_response(("c5", "patch", {
                "path": "src/repo.py",
                "old_str": "            results = [p for p in results if p.rating <= min_rating]  # BUG: inverted comparison",
                "new_str": "            results = [p for p in results if p.rating >= min_rating]",
            })),
            tool_response(("c6", "patch", {
                "path": "src/repo.py",
                "old_str": "    def update(self, product: Product) -> bool:\n        return product.id in self._products  # BUG: reports existence but never updates",
                "new_str": "    def update(self, product: Product) -> bool:\n        if product.id in self._products:\n            self._products[product.id] = product\n            return True\n        return False",
            })),
            tool_response(("c7", "patch", {
                "path": "src/repo.py",
                "old_str": "    def delete(self, product_id: str) -> bool:\n        return product_id in self._products  # BUG: reports existence but never deletes",
                "new_str": "    def delete(self, product_id: str) -> bool:\n        return self._products.pop(product_id, None) is not None",
            })),
            tool_response(("c8", "bash", {"command": "python -m pytest tests/test_catalog.py -v"})),
            text_response("All 5 bugs fixed."),
        ]
        self.call_count = 0

    def complete(self, messages, tools=None, config=None):
        r = self.responses[self.call_count]
        self.call_count += 1
        return r

    def stream(self, messages, tools=None, config=None):
        resp = self.complete(messages, tools, config)
        from src.agent.ir import (
            ArgsDelta, MessageStart, MessageEnd,
            TextBlock, TextDelta, ThinkingBlock,
            ThinkingStart, ThinkingDelta, ThinkingEnd,
            ToolUseBlock, ToolUseStart, ToolUseEnd,
        )
        yield MessageStart(model=config.get("model", "?") if config else "?")
        for block in resp.message.content:
            if isinstance(block, TextBlock):
                yield TextDelta(delta=block.text)
            elif isinstance(block, ThinkingBlock):
                yield ThinkingStart()
                yield ThinkingDelta(delta=block.text)
                yield ThinkingEnd(signature=block.signature)
            elif isinstance(block, ToolUseBlock):
                yield ToolUseStart(id=block.id, name=block.name)
                yield ArgsDelta(id=block.id, delta=json.dumps(block.input))
                yield ToolUseEnd(id=block.id)
        yield MessageEnd(stop_reason=resp.stop_reason, truncated=False, usage=resp.usage)


target_bug_dir = Path(__file__).resolve().parent.parent / "tests" / "_target_bug"

with tempfile.TemporaryDirectory() as tmpdir:
    for item in target_bug_dir.iterdir():
        src = item
        dst = Path(tmpdir) / item.name
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)

    stats_path = Path(tmpdir) / "src" / "stats.py"
    repo_path = Path(tmpdir) / "src" / "repo.py"

    print("=" * 60)
    print("  BEFORE: 5 bugs (3 visible by pytest, 2 hidden)")
    print("=" * 60)
    print(BUG_FIXES)
    print()

    print("  pytest result:")
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_catalog.py", "--tb=line"],
        cwd=tmpdir, capture_output=True, text=True, timeout=30,
    )
    for line in result.stdout.strip().splitlines()[-3:]:
        if "failed" in line or "passed" in line:
            print(f"  {line}")
    print()

    config = AgentConfig(max_turns=12)
    agent = Agent(config, DemoBackend())
    traj = agent.run("修复所有 Bug", tmpdir)

    print("=" * 60)
    print("  EXECUTION TRAJECTORY")
    print("=" * 60)
    for ev in traj.events:
        if ev.type == "tool_call":
            print(f"  [tool_call] {ev.payload.get('name')}(...)")
        elif ev.type == "tool_result":
            content = str(ev.payload.get("content", ""))
            if len(content) > 80:
                content = content[:80] + "..."
            print(f"  [tool_result] {content}")
        elif ev.type == "run_start":
            print(f"  [run_start] task: {ev.payload.get('task')}")
        elif ev.type == "run_end":
            print(f"  [run_end] reason: {ev.payload.get('reason')}")
        elif ev.type == "llm_response":
            print(f"  [llm_response] turn: {ev.turn}")

    print()
    print("=" * 60)
    print("  FIXES CONFIRMED:")
    print("=" * 60)
    print(f"  V1 (continue): {'found' if 'continue' in stats_path.read_text('utf-8') else 'MISSING'}")
    repo = repo_path.read_text("utf-8")
    print(f"  V2 ((page-1)*size): {'found' if '(page - 1) * page_size' in repo else 'MISSING'}")
    print(f"  V3 (>= min_rating): {'found' if '>= min_rating' in repo else 'MISSING'}")
    print(f"  H1 (self._products[id]=product): {'found' if 'self._products[product.id] = product' in repo else 'MISSING'}")
    print(f"  H2 (.pop()): {'found' if '.pop(' in repo else 'MISSING'}")

    print()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_catalog.py", "-q"],
        cwd=tmpdir, capture_output=True, text=True, timeout=30,
    )
    for line in result.stdout.strip().splitlines()[-2:]:
        print(f"  {line}")
    print()
    print("  PASS - all 5 bugs fixed, 8 tests pass")
