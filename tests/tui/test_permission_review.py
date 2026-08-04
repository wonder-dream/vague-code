"""Prewrite review computation and permission-feedback loop tests."""

from pathlib import Path

from src.agent.permission import Operation
from src.agent.prewrite import compute_prewrite_review
from src.tui.views.review import render_prewrite_review


def _write(tmp_path: Path, rel: str, content: str) -> None:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


# ── prewrite diff ────────────────────────────────────────────────────────────

def test_write_file_create_review(tmp_path: Path) -> None:
    payload = compute_prewrite_review(
        "write_file", {"path": "a.py", "content": "print(1)"}, str(tmp_path)
    )
    assert payload is not None
    files = payload["files"]
    assert len(files) == 1
    assert files[0]["operation"] == "CREATE"
    assert files[0]["added_lines"] == 1
    assert "+print(1)" in files[0]["diff"]


def test_write_file_modify_review(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    payload = compute_prewrite_review(
        "write_file", {"path": "a.py", "content": "x = 2\n"}, str(tmp_path)
    )
    files = payload["files"]
    assert files[0]["operation"] == "MODIFY"
    assert files[0]["added_lines"] == 1
    assert files[0]["removed_lines"] == 1
    assert "-x = 1" in files[0]["diff"]
    assert "+x = 2" in files[0]["diff"]


def test_patch_review_applies_replacement(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "old line\nkeep\n")
    payload = compute_prewrite_review(
        "patch", {"path": "a.py", "old_str": "old line", "new_str": "new line"}, str(tmp_path)
    )
    files = payload["files"]
    assert files[0]["removed_lines"] == 1
    assert files[0]["added_lines"] == 1
    assert "+new line" in files[0]["diff"]


def test_patch_without_match_returns_no_files(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "content\n")
    payload = compute_prewrite_review(
        "patch", {"path": "a.py", "old_str": "missing", "new_str": "x"}, str(tmp_path)
    )
    assert payload["files"] == []


def test_unchanged_write_returns_empty_files(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "same\n")
    payload = compute_prewrite_review(
        "write_file", {"path": "a.py", "content": "same\n"}, str(tmp_path)
    )
    assert payload["files"] == []
    assert payload["summary"]["added_lines"] == 0


def test_unknown_tool_and_path_traversal(tmp_path: Path) -> None:
    assert compute_prewrite_review("bash", {"command": "ls"}, str(tmp_path)) is None
    payload = compute_prewrite_review(
        "write_file", {"path": "../escape.txt", "content": "x"}, str(tmp_path)
    )
    assert payload is None


def test_render_prewrite_review_colors() -> None:
    payload = {
        "files": [
            {
                "path": "a.py",
                "operation": "MODIFY",
                "added_lines": 1,
                "removed_lines": 1,
                "diff": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
            }
        ],
        "summary": {"added_lines": 1, "removed_lines": 1},
    }
    text = render_prewrite_review(payload)
    plain = text.plain
    assert "Review before writing" in plain
    assert "a.py" in plain
    assert "+x = 2" in plain


# ── feedback propagation ─────────────────────────────────────────────────────

def test_operation_feedback_field() -> None:
    op = Operation(tool_name="write_file", input={"path": "a.py", "content": "x"})
    assert op.review is None
    assert op.feedback is None
    op.review = {"files": []}
    op.feedback = "不要覆盖配置文件"
    assert op.feedback == "不要覆盖配置文件"
    assert op.review == {"files": []}
