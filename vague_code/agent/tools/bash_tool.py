"""bash 工具（class-based）：Windows chcp / multiline python / 测试结构化 / 交互确认引导。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from vague_code.agent.permission import classify_bash
from vague_code.agent.tools.base import (
    OpType,
    ScopeType,
    Tool,
    ToolExecutionError,
)

# bash 工具测试结果结构化（plans/0018）：测试类命令的判定关键词。
# 与 eval/metrics.py 的 TEST_KEYWORDS 同源但独立（产品层不依赖 eval 层）。
TEST_COMMAND_KEYWORDS = ("pytest", "unittest", "nose", "tox", "make test",
                         "run_tests", "run-tests", "nosetests")

# cmd.exe 交互确认提示（del/rmdir 等对目录或通配符参数会弹出确认）。
# stdin 为 DEVNULL 时读到 EOF 视为拒绝，命令静默失败（rc=1）。
INTERACTIVE_CONFIRM_PATTERNS = (
    r"are you sure \(y/n\)",
    r"是否确认\(y/n\)",
    r"确认删除",
    r"confirm.*\(y/n\)",
)

BASH_TIMEOUT_S = 30


def _is_test_command(command: str) -> bool:
    cmd = (command or "").strip().lower()
    return any(kw in cmd for kw in TEST_COMMAND_KEYWORDS)


def _summarize_test_output(stdout: str, stderr: str, exit_code: int) -> str:
    """从 pytest 风格输出提取结构化 PASS/FAIL 信号。

    规则：`N passed` / `M failed` / `X error`（pytest 汇总行）命中时按数量判
    PASS/FAIL；解析不出时 fallback exit code（0=PASS，非 0=FAIL）。
    返回一行 `[test] PASS (3 passed)` 或 `[test] FAIL (1 failed)`；无法判定
    时返回空串（保持原输出不变）。
    """
    text = stdout + "\n" + stderr
    m = re.search(r"(\d+)\s+passed", text)
    n_passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+)\s+failed", text)
    n_failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+)\s+error", text)
    n_error = int(m.group(1)) if m else 0
    if n_passed or n_failed or n_error:
        if n_failed or n_error:
            detail = []
            if n_failed:
                detail.append(f"{n_failed} failed")
            if n_error:
                detail.append(f"{n_error} error")
            return f"[test] FAIL ({', '.join(detail)})"
        return f"[test] PASS ({n_passed} passed)"
    if exit_code == 0:
        return "[test] PASS (exit 0)"
    return f"[test] FAIL (exit {exit_code})"


def _looks_like_interactive_confirm(stdout: str, exit_code: int) -> bool:
    """True when the command output shows cmd.exe 交互确认提示（stdin 无输入 → 静默拒绝）。"""
    if exit_code == 0 and not re.search(r"退出码|error", stdout, re.I):
        return False
    return any(re.search(p, stdout, re.I) for p in INTERACTIVE_CONFIRM_PATTERNS)


def _interactive_confirm_guidance(command: str, stdout: str, exit_code: int) -> str:
    return (
        f"退出码: {exit_code}\n标准输出:\n{stdout}\n\n"
        f"[交互确认提示] 命令触发了 cmd.exe 的删除确认（stdin 无输入被拒绝）。"
        f"本工具无法应答交互式提示。请改用非交互写法：\n"
        f"  - 删除文件：del /Q <文件>（或 python -c \"import os; os.remove(...)\"）\n"
        f"  - 删除目录：rmdir /S /Q <目录>（或 shutil.rmtree）\n"
        f"  - 注意本环境是 Windows cmd.exe：命令分隔符用 &，不用 ;\n"
    )


class BashTool(Tool):
    name = "bash"
    description = ("执行 shell 命令并返回其输出。分别返回标准输出和标准错误输出。"
                   "注意：本环境是 Windows，实际使用 cmd.exe（非 bash）。"
                   "多命令分隔符用 &（不用 ;）；删除文件用 del /Q 或 python os.remove，"
                   "删除目录用 rmdir /S /Q，避免交互确认提示导致失败。")
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "cwd": {"type": "string", "description": "命令的工作目录（默认: 工作目录根路径）"},
            "timeout": {"type": "integer", "description": "超时秒数（默认 30）"},
        },
        "required": ["command"],
    }
    # 权限分类动态（safe/dangerous 由命令内容判定）；资源覆盖整个工作区
    permission = "bash_safe"
    op_type = OpType.WRITE
    scope_type = ScopeType.WORKSPACE

    def permission_class(self, input: dict) -> str:
        command = input.get("command", "")
        level = classify_bash(str(command))
        return "bash_safe" if level.value == "safe" else "bash_dangerous"

    def on_truncated(self, full_output: str, tr) -> dict:
        """输出超限 → 完整输出落盘（对齐 PI fullOutputPath），模型可用 read_file 读回。"""
        try:
            path = Path(tempfile.gettempdir()) / f"vaguecode_bash_{uuid.uuid4().hex[:8]}.out"
            path.write_text(full_output, encoding="utf-8")
            return {"full_output_path": str(path)}
        except OSError:
            return {}

    def run(self, input: dict) -> str:
        command = self.extract(input, "command")
        cwd_str = self.extract_optional(input, "cwd")
        if cwd_str:
            cwd_path = self.resolve_path(cwd_str)
        else:
            cwd_path = self.root
        timeout_s = int(input.get("timeout", BASH_TIMEOUT_S) or BASH_TIMEOUT_S)
        temp_script: Path | None = None
        rewritten = _rewrite_multiline_python(command)
        if rewritten is not None:
            temp_script, command = rewritten
        if os.name == "nt" and not command.strip().lower().startswith("chcp"):
            command = f"chcp 65001 >nul & {command}"
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                env=env,
            )
            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True, timeout=5,
                    )
                proc.kill()
                try:
                    stdout_bytes, stderr_bytes = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    stdout_bytes = exc.stdout or b""
                    stderr_bytes = exc.stderr or b""
                stdout_partial = stdout_bytes.decode("utf-8", errors="replace")
                stderr_partial = stderr_bytes.decode("utf-8", errors="replace")
                raise ToolExecutionError(
                    f"命令在 {timeout_s} 秒后超时\n"
                    f"部分标准输出:\n{stdout_partial}\n"
                    f"部分标准错误输出:\n{stderr_partial}"
                )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            # 测试结果结构化（plans/0018 #5）：测试类命令追加 PASS/FAIL 行，给模型明确信号
            if _is_test_command(command):
                verdict = _summarize_test_output(stdout, stderr, proc.returncode)
                if verdict:
                    return (f"退出码: {proc.returncode}\n标准输出:\n{stdout}\n"
                            f"标准错误输出:\n{stderr}\n\n{verdict}")
            if _looks_like_interactive_confirm(stdout, proc.returncode):
                return _interactive_confirm_guidance(command, stdout, proc.returncode)
            return f"退出码: {proc.returncode}\n标准输出:\n{stdout}\n标准错误输出:\n{stderr}"
        finally:
            if temp_script is not None:
                try:
                    temp_script.unlink(missing_ok=True)
                except OSError:
                    pass


def _rewrite_multiline_python(command: str) -> tuple[Path, str] | None:
    """Rewrite `python -c "multi-line code"` to a temp script file.

    cmd.exe corrupts -c arguments containing newlines (rc=0, no output);
    running the code from a .py file preserves newlines. Returns
    (script_path, rewritten_command), or None when not affected.
    """
    _MULTILINE_PY_C = re.compile(r'python\s+-c\s+"([\s\S]*?)"')
    if "\n" not in command:
        return None
    match = _MULTILINE_PY_C.search(command)
    if match is None:
        return None
    code = match.group(1)
    if "\n" not in code:
        return None
    script = Path(tempfile.gettempdir()) / f"vaguecode_{uuid.uuid4().hex[:8]}.py"
    try:
        script.write_text(code, encoding="utf-8")
    except OSError:
        return None
    rewritten = command[: match.start()] + f'python "{script}"' + command[match.end():]
    return script, rewritten
