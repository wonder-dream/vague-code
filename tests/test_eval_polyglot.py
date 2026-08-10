"""Aider Polyglot 评测运行器测试（ADR-0040）。docker 调用以 mock 隔离。"""

from __future__ import annotations

from eval.polyglot import (
    load_polyglot_tasks,
    prepare_task,
    verify_in_container,
)


def _make_dataset(tmp_path) -> dict:
    """构造迷你数据集：python 1 题 + cpp 1 题。"""
    root = tmp_path / "dataset"
    py = root / "python" / "exercises" / "practice" / "hello-world"
    (py / ".docs").mkdir(parents=True)
    (py / "hello_world.py").write_text("def hello():\n    return 'TODO'\n", encoding="utf-8")
    (py / "hello_world_test.py").write_text("def test_hello():\n    pass\n", encoding="utf-8")
    (py / ".docs" / "instructions.md").write_text("实现 hello() 返回 'Hello, World!'", encoding="utf-8")
    (py / ".docs" / "instructions.append.md").write_text("补充说明：接口不可改。", encoding="utf-8")
    (py / ".meta").mkdir()
    (py / ".meta" / "example.py").write_text("def hello():\n    return 'Hello, World!'\n", encoding="utf-8")

    cpp = root / "cpp" / "exercises" / "practice" / "hello-world"
    (cpp / ".docs").mkdir(parents=True)
    (cpp / "hello_world.cpp").write_text("", encoding="utf-8")
    (cpp / "hello_world.h").write_text("", encoding="utf-8")
    (cpp / "CMakeLists.txt").write_text("", encoding="utf-8")
    (cpp / ".docs" / "instructions.md").write_text("实现 hello()", encoding="utf-8")
    return root


def test_load_polyglot_tasks(tmp_path) -> None:
    root = _make_dataset(tmp_path)
    tasks = load_polyglot_tasks(root)
    ids = {t["instance_id"] for t in tasks}
    assert ids == {"python/hello-world", "cpp/hello-world"}
    py = next(t for t in tasks if t["instance_id"] == "python/hello-world")
    assert "Hello, World!" in py["problem_statement"]
    assert "补充说明" in py["problem_statement"]
    assert py["language"] == "python"
    assert py["exercise"] == "hello-world"


def test_prepare_task_excludes_answer_and_docs(tmp_path) -> None:
    root = _make_dataset(tmp_path)
    task = next(t for t in load_polyglot_tasks(root) if t["language"] == "python")
    dest = prepare_task(tmp_path / "runs", task)
    assert (dest / "hello_world.py").is_file()
    assert (dest / "hello_world_test.py").is_file()
    assert not (dest / ".meta").exists(), "参考答案不得进入考场"
    assert not (dest / ".docs").exists(), "题目描述已被放入 prompt，不重复落盘"


def test_verify_python_pass_and_fail(tmp_path, monkeypatch) -> None:
    from eval import polyglot as pg

    calls: list[str] = []

    def fake_docker(args, timeout_s=900):
        calls.append(" ".join(args))
        return type("P", (), {"returncode": 0, "stdout": "16 passed", "stderr": ""})()

    monkeypatch.setattr(pg, "_docker", fake_docker)
    task = {"instance_id": "python/hello-world", "language": "python",
            "exercise": "hello-world", "source_dir": str(tmp_path)}
    ok, reason, tail = verify_in_container(task, tmp_path)
    assert ok is True and reason == "verify:pass"
    assert "--network" in calls[0] and "host" in calls[0]
    assert "/workspace" in calls[0]
    assert "vague-eval" in calls[0]

    monkeypatch.setattr(pg, "_docker",
                        lambda args, timeout_s=900: type("P", (), {
                            "returncode": 1, "stdout": "", "stderr": "16 failed"}))
    ok, reason, tail = verify_in_container(task, tmp_path)
    assert ok is False and reason == "verify:fail(exit 1)"


def test_verify_cpp_builds_and_runs(tmp_path, monkeypatch) -> None:
    from eval import polyglot as pg

    captured: list[str] = []

    def fake_docker(args, timeout_s=900):
        captured.append(" ".join(args))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(pg, "_docker", fake_docker)
    task = {"instance_id": "cpp/hello-world", "language": "cpp",
            "exercise": "hello-world", "source_dir": str(tmp_path)}
    ok, reason, _ = verify_in_container(task, tmp_path)
    assert ok is True
    joined = " ".join(captured)
    assert "cmake" in joined
    assert "./build/hello_world" in joined  # 连字符转下划线
