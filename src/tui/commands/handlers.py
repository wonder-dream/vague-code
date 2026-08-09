"""Help, session, model, and permission slash command handlers."""

from __future__ import annotations

from src.tui.commands.core import CommandHandler, CommandResult
from src.tui.picker import TuiPickerItem
from src.tui.session_lib import list_recent_runs

_HELP_TEXT = """\
可用命令：
  /help            显示本帮助
  /new             清空对话并开始新会话
  /clear           清空对话视图
  /resume          选择历史会话继续（picker）
  /compact         手动压缩当前会话上下文（LLM 摘要）
  /save [path]     导出轨迹为 JSONL
  /model [name]    切换模型（picker 或直接指定）
  /mode <mode>     切换权限模式（safe/normal/autoedit/auto）
  /permissions     列出持久化权限规则
  exit             退出 TUI

快捷键：
  Ctrl+C           复制选中文本（有选中时）/ 中断运行
  Esc              聚焦输入框（运行中按两次中断）
  ↑/↓              输入历史
  T                折叠/展开 thinking"""


class HelpCommandHandler(CommandHandler):
    name = "help"

    def __init__(self, app=None) -> None:
        self._app = app

    def handle(self, text: str) -> CommandResult:
        if not self._match(text, "/help"):
            return CommandResult()
        return CommandResult(handled=True, output=_HELP_TEXT)


class SessionCommandHandler(CommandHandler):
    name = "session"

    def __init__(self, app) -> None:
        self._app = app

    def handle(self, text: str) -> CommandResult:
        if self._match(text, "/resume"):
            return self._resume(text)
        if self._match(text, "/new"):
            return CommandResult(
                handled=True, action={"type": "new_session", "text": ""}
            )
        if self._match(text, "/clear"):
            return CommandResult(handled=True, action={"type": "clear_output"})
        if self._match(text, "/compact"):
            return CommandResult(handled=True, action={"type": "compact_session"})
        if self._match(text, "/save"):
            return self._save(text)
        return CommandResult()

    def _resume(self, text: str) -> CommandResult:
        parts = text.strip().split(maxsplit=1)
        run_id = parts[1].strip() if len(parts) > 1 else ""
        if run_id:
            return CommandResult(
                handled=True, action={"type": "resume_session", "run_id": run_id}
            )
        app = self._app
        runs = list_recent_runs(app._config.db_path)
        if not runs:
            return CommandResult(handled=True, output="No sessions to resume.")
        items = [
            TuiPickerItem(
                id=run.run_id,
                label=(run.task or "?")[:40],
                detail=f"{'会话' if run.mode == 'chat' else '任务'} · {run.status} · {run.run_id}",
            )
            for run in runs
        ]
        return CommandResult(
            handled=True,
            action={
                "type": "open_picker",
                "kind": "resume",
                "title": "Select a session to resume:",
                "items": [{"id": i.id, "label": i.label, "detail": i.detail} for i in items],
            },
        )

    def _save(self, text: str) -> CommandResult:
        parts = text.strip().split(maxsplit=1)
        path = parts[1].strip() if len(parts) > 1 else ""
        app = self._app
        if app._trajectory is None:
            return CommandResult(handled=True, output="No trajectory to save.")
        try:
            app._trajectory.export_jsonl(path or f"runs/{app._trajectory.run_id}.jsonl")
            return CommandResult(handled=True, output=f"Saved to: {path or f'runs/{app._trajectory.run_id}.jsonl'}")
        except Exception as e:
            return CommandResult(handled=True, output=f"Save failed: {e}")


class ModelCommandHandler(CommandHandler):
    name = "model"

    MODELS = (
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-chat",
        "deepseek-reasoner",
    )

    def __init__(self, app) -> None:
        self._app = app

    def handle(self, text: str) -> CommandResult:
        if not self._match(text, "/model"):
            return CommandResult()
        parts = text.strip().split(maxsplit=1)
        model = parts[1].strip() if len(parts) > 1 else ""
        if model:
            return CommandResult(
                handled=True,
                action={"type": "model_changed", "provider": "deepseek", "model": model},
            )
        items = [
            TuiPickerItem(id=m, label=m, detail="deepseek")
            for m in self.MODELS
        ]
        return CommandResult(
            handled=True,
            action={
                "type": "open_picker",
                "kind": "model",
                "title": "Select a model:",
                "items": [{"id": i.id, "label": i.label, "detail": i.detail} for i in items],
            },
        )


class PermissionCommandHandler(CommandHandler):
    name = "permission"

    MODES = ("safe", "normal", "autoedit", "auto")

    def __init__(self, app) -> None:
        self._app = app

    def handle(self, text: str) -> CommandResult:
        if self._match(text, "/mode"):
            parts = text.strip().split(maxsplit=1)
            mode = parts[1].strip().lower() if len(parts) > 1 else ""
            if mode not in self.MODES:
                return CommandResult(
                    handled=True,
                    output=f"Unknown mode: {mode or '(empty)'} — use one of {', '.join(self.MODES)}",
                )
            self._app._config.permission_mode = mode
            self._app._refresh_topbar()
            return CommandResult(handled=True, output=f"Mode set to {mode}.")
        if self._match(text, "/permissions"):
            rules = self._app._load_permission_rules()
            if not rules:
                return CommandResult(handled=True, output="No persistent permission rules.")
            lines = [f"  {r['pattern']} -> {r.get('action', 'allow')}" for r in rules]
            return CommandResult(handled=True, output="Persistent permission rules:\n" + "\n".join(lines))
        return CommandResult()
