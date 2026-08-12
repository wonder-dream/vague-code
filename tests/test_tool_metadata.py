"""工具元数据一致性测试（ADR-0004 重构：permission/scope 声明与行为断言）。

权限分类与并发 scope 由工具元数据声明（permission.py / concurrency.py 消费），
本文件断言每工具的声明与预期一致，防止回归到按工具名硬编码分支。
"""

from __future__ import annotations

from pathlib import Path


from vague_code.agent.ir import ToolUseBlock
from vague_code.agent.tools import DEFAULT_TOOLS, bind_tools
from vague_code.agent.tools.base import OpType, ScopeType


def _tool(name: str, workdir: str = "."):
    return bind_tools(DEFAULT_TOOLS, workdir)[name]


def _scope(name: str, input: dict, workdir: str = "."):
    call = ToolUseBlock(id="c1", name=name, input=input)
    return _tool(name, workdir).resource_scope(call.input)


# ── 权限分类声明 ────────────────────────────────────────────────────────────

def test_permission_classes() -> None:
    assert _tool("read_file").permission == "read"
    assert _tool("glob").permission == "read"
    assert _tool("grep").permission == "read"
    assert _tool("write_file").permission == "write"
    assert _tool("patch").permission == "write"
    assert _tool("bash").permission == "bash_safe"


def test_bash_permission_class_dynamic() -> None:
    assert _tool("bash").permission_class({"command": "ls -la"}) == "bash_safe"
    assert _tool("bash").permission_class({"command": "rm -rf /tmp"}) == "bash_dangerous"


def test_all_registry_names_match_class_name() -> None:
    for key, tool_cls in DEFAULT_TOOLS.items():
        assert key == tool_cls.name, f"注册表 key {key} != 工具名 {tool_cls.name}"


# ── 并发 scope 声明 ─────────────────────────────────────────────────────────

def test_scope_read_exact(tmp_path: Path) -> None:
    s = _scope("read_file", {"path": "a.py"}, str(tmp_path))
    assert (s.op_type, s.scope_type, s.path) == (OpType.READ, ScopeType.EXACT, "a.py")


def test_scope_write_new_is_structural(tmp_path: Path) -> None:
    s = _scope("write_file", {"path": "new.txt"}, str(tmp_path))
    assert s.op_type == OpType.STRUCTURAL_WRITE


def test_scope_write_existing_is_write(tmp_path: Path) -> None:
    (tmp_path / "exist.txt").write_text("x", encoding="utf-8")
    s = _scope("write_file", {"path": "exist.txt"}, str(tmp_path))
    assert s.op_type == OpType.WRITE


def test_scope_glob_prefix_and_root(tmp_path: Path) -> None:
    s = _scope("glob", {"pattern": "src/**/*.py"}, str(tmp_path))
    assert (s.scope_type, s.path) == (ScopeType.PREFIX, "src")
    s2 = _scope("glob", {"pattern": "**/*.py"}, str(tmp_path))
    assert s2.scope_type == ScopeType.WORKSPACE


def test_scope_grep_path_and_root(tmp_path: Path) -> None:
    s = _scope("grep", {"pattern": "TODO", "path": "src/"}, str(tmp_path))
    assert (s.scope_type, s.path) == (ScopeType.PREFIX, "src/")
    s2 = _scope("grep", {"pattern": "TODO"}, str(tmp_path))
    assert s2.scope_type == ScopeType.WORKSPACE


def test_scope_bash_workspace_write(tmp_path: Path) -> None:
    s = _scope("bash", {"command": "ls"}, str(tmp_path))
    assert (s.op_type, s.scope_type) == (OpType.WRITE, ScopeType.WORKSPACE)


def test_scope_patch_exact_write(tmp_path: Path) -> None:
    s = _scope("patch", {"path": "a.py"}, str(tmp_path))
    assert (s.op_type, s.scope_type, s.path) == (OpType.WRITE, ScopeType.EXACT, "a.py")


def test_code_search_scope_read_exact(tmp_path: Path) -> None:
    from vague_code.agent.tools.code_search import CodeSearchTool

    tool = CodeSearchTool(str(tmp_path), None)
    call = ToolUseBlock(id="c1", name="code_search", input={"query": "foo", "path": "src/"})
    s = tool.resource_scope(call.input)
    assert (s.op_type, s.scope_type, s.path) == (OpType.READ, ScopeType.EXACT, "src/")
    assert tool.permission == "read"  # 修复：旧实现默认走 write 策略


# ── 统一截断元数据 ──────────────────────────────────────────────────────────

def test_tool_result_metadata_on_truncation(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text(("y" * 100 + "\n") * 600, encoding="utf-8")
    result = _tool("read_file", str(tmp_path))({"path": "big.txt"})
    assert "输出截断于" in result.output  # 读入预算截断（模型可见标记）
    assert result.metadata["title"] == "read_file"


def test_tool_result_metadata_untouched(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_text("ok", encoding="utf-8")
    result = _tool("read_file", str(tmp_path))({"path": "small.txt"})
    assert result.metadata["truncated"] is False
    assert result.output.endswith("ok")
