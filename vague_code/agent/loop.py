from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from vague_code.agent.permission import Decision, Operation

from vague_code.agent.backend import ModelBackend
from vague_code.agent.config import AgentConfig
from vague_code.agent.memory_file import MemoryFile
from vague_code.agent.ir import (
    ArgsDelta,
    Block,
    Message,
    MessageEnd,
    MessageStart,
    ModelResponse,
    NormalizedUsage,
    RetryNotice,
    StopReason,
    StreamDisconnect,
    StreamEvent,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ThinkingEnd,
    ThinkingStart,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    ToolUseEnd,
    ToolUseStart,
)
from vague_code.agent.retry import (
    RetryPolicy,
    classify_llm_error,
    estimate_input_tokens,
)
from vague_code.agent.tools import DEFAULT_TOOLS, Tool, bind_tools
from vague_code.agent.trajectory import EventType, Trajectory


# ── Memory distill prompt (ADR-0014) ────────────────────────────────────────

_MEMORY_DISTILL_PROMPT = (
    "你是会话记忆整理器。根据下面的会话任务，总结本次会话值得长期记住的内容"
    "（项目约定、构建/测试命令、踩坑解法、用户偏好等），最多 3 条。\n"
    "若无值得记住的内容，只输出「无」。\n"
    "输出格式（markdown，每条以 ## 标题开头，多条用空行分隔）：\n\n"
    "## 短标题\n内容\n\n任务：{task}"
)


def _response_text(resp: ModelResponse) -> str:
    """取 ModelResponse 的纯文本（str 或 TextBlock 列表）。"""
    content = resp.message.content
    if isinstance(content, str):
        return content
    parts = []
    for b in content:
        if isinstance(b, TextBlock):
            parts.append(b.text)
    return "".join(parts)


# ── Aggregator ──────────────────────────────────────────────────────────────

class _StreamAggregator:
    """Accumulates StreamEvent delta events into a ModelResponse."""

    def __init__(self):
        self._text = StringIO()
        self._thinking = StringIO()
        self._thinking_sig: str | None = None
        self._tool_buffers: dict[str, StringIO] = {}
        self._tool_names: dict[str, str] = {}
        self._tool_order: list[str] = []

    def feed(self, ev: StreamEvent) -> None:
        if isinstance(ev, TextDelta):
            self._text.write(ev.delta)
        elif isinstance(ev, ThinkingStart):
            pass
        elif isinstance(ev, ThinkingDelta):
            self._thinking.write(ev.delta)
        elif isinstance(ev, ThinkingEnd):
            self._thinking_sig = ev.signature
        elif isinstance(ev, ToolUseStart):
            if ev.id in self._tool_buffers:
                import warnings
                warnings.warn(f"Duplicate ToolUseStart id={ev.id}; resetting buffer")
                self._tool_buffers[ev.id] = StringIO()
            else:
                self._tool_buffers[ev.id] = StringIO()
                self._tool_order.append(ev.id)
            self._tool_names[ev.id] = ev.name
        elif isinstance(ev, ArgsDelta):
            buf = self._tool_buffers.get(ev.id)
            if buf is None:
                raise ValueError(f"ArgsDelta for unknown tool_use_id: {ev.id}")
            buf.write(ev.delta)
        elif isinstance(ev, ToolUseEnd):
            pass
        elif isinstance(ev, MessageEnd):
            # final assembly already done in result()
            pass

    def result(self, message_end: MessageEnd) -> ModelResponse:
        blocks: list[Block] = []
        think_text = self._thinking.getvalue()
        if think_text:
            blocks.append(ThinkingBlock(text=think_text, signature=self._thinking_sig))
        text = self._text.getvalue()
        if text:
            blocks.append(TextBlock(text=text))
        for tid in self._tool_order:
            raw = self._tool_buffers[tid].getvalue()
            name = self._tool_names[tid]
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {}
            blocks.append(ToolUseBlock(id=tid, name=name, input=parsed))
        if not blocks:
            blocks.append(TextBlock(text=""))
        return ModelResponse(
            message=Message(role="assistant", content=blocks),
            stop_reason=message_end.stop_reason,
            usage=message_end.usage or NormalizedUsage(),
        )


# ── RunHandle ──────────────────────────────────────────────────────────────

class RunHandle:
    """Live run handle: iterate for StreamEvents, then read .trajectory."""

    def __init__(self, generator: Iterator[StreamEvent], traj: Trajectory):
        self._generator = generator
        self._traj = traj
        self._finished = False

    def __iter__(self) -> Iterator[StreamEvent]:
        return self

    def __next__(self) -> StreamEvent:
        if self._finished:
            raise StopIteration
        try:
            return next(self._generator)
        except StopIteration:
            self._finished = True
            raise

    def close(self) -> None:
        if not self._finished:
            self._generator.close()  # type: ignore[attr-defined]
            self._finished = True

    def __enter__(self) -> RunHandle:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def trajectory(self) -> Trajectory:
        if not self._finished:
            raise RuntimeError("run not exhausted")
        return self._traj


# ── Supervision Agent（ADR-0020 / plans-0018）──────────────────────────────

SUPERVISION_ASSESSMENTS = ("on_track", "off_track", "needs_verification", "stuck", "done")

SUPERVISION_SYSTEM_PROMPT = """你是代码修复任务的监督者（Supervisor）。你只"看"和"说"，不执行任何工具。

对主 agent 的进展做五值评估：
- on_track: 方向正确，正在有效推进。注意：持续读取新文件、grep 新位置、深入理解代码——即使尚未编辑——是有效推进，应判 on_track。复杂任务的第一次编辑往往发生在 20-30 轮之后，探索本身不是停滞。
- off_track: 偏离了任务方向
- needs_verification: 已有产出但尚未用测试验证
- stuck: 原地打转——连续重复相同的命令/反复读同一文件、没有新的探索动作，且没有编辑产出
- done: 任务已完成（工作区 diff 非空 且 尾部测试类命令 exit 0 通过）

只输出一个 JSON 对象，不要输出其他文字：
{"assessment": "<五值之一>", "guidance": "<给主 agent 的中文导航建议，1-3 句>", "evidence": "<判定依据的简要引用>"}"""


def _extract_json_obj(raw: str) -> dict | None:
    """从模型输出中提取 JSON 对象（仿 eval/judge.py 的 _extract_json）。"""
    for m in re.finditer(r"\{.*\}", raw, re.DOTALL):
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                return d
        except json.JSONDecodeError:
            continue
    return None


# ── Agent ──────────────────────────────────────────────────────────────────

class Agent:
    def __init__(
        self,
        config: AgentConfig,
        backend: ModelBackend,
        tools: dict[str, type[Tool]] | None = None,
    ):
        self.config = config
        self.backend = backend
        self._on_permission: Callable[[Operation, Decision], Decision] | None = None
        self.on_tool_result: Callable[[str, str, str, bool], None] | None = None
        self.on_state_change: Callable[[str, dict], None] | None = None
        self.guidance_provider: Callable[[], list[str]] | None = None
        self._permission_rules: list = []
        self._memory_files: dict[str, MemoryFile] = {}
        self._workdir: str = ""
        self._repo_index: object | None = None
        self._chat_traj: Trajectory | None = None
        self._chat_messages: list[Message] | None = None
        self._chat_bound_tools: dict[str, Tool] | None = None
        self._chat_turn: int = 0
        self._tool_registry = tools if tools is not None else DEFAULT_TOOLS
        for key, tool in self._tool_registry.items():
            if key != tool.name:
                raise ValueError(f"Registry key '{key}' does not match tool name '{tool.name}'")
        self._tool_specs = [t.spec() for t in self._tool_registry.values()]

    def run(self, task: str, workdir: str) -> Trajectory:
        handle = self.start(task, workdir)
        for _ in handle:
            pass
        traj = handle.trajectory
        self._distill_session(traj)
        return traj

    def start(self, task: str, workdir: str, identity: str | None = None) -> RunHandle:
        traj, messages, bound_tools = self._init_run(task, workdir, identity=identity)
        if messages is None:
            return RunHandle(iter([]), traj)
        gen = self._run_gen(traj, messages, [0], bound_tools)
        return RunHandle(gen, traj)

    # ── Chat session (multi-turn conversation, ADR-0025) ─────────────────────

    def chat(self, text: str, workdir: str) -> RunHandle:
        """会话内连续对话：首轮初始化会话，后续轮延续上下文与 turn 计数。

        每轮结束（end_turn）后会话暂停而非终止；`chat_end()` 显式结束。
        跨进程续会话用 `chat_resume(run_id)`。
        """
        if self._chat_messages is None:
            traj, messages, bound_tools = self._init_run(text, workdir, mode="chat")
            if messages is None:
                return RunHandle(iter([]), traj)
            self._chat_traj = traj
            self._chat_messages = messages
            self._chat_bound_tools = bound_tools
        else:
            completed = self._complete_pending_tools(self._chat_turn, text)
            if not completed:
                self._chat_messages.append(Message(role="user", content=text))
            assert self._chat_traj is not None
            self._chat_traj.emit(
                EventType.user_message, turn=self._chat_turn, payload={"text": text},
            )
        assert self._chat_traj is not None and self._chat_messages is not None
        assert self._chat_bound_tools is not None
        gen = self._run_gen(
            self._chat_traj, self._chat_messages, [self._chat_turn],
            self._chat_bound_tools, chat_mode=True,
        )
        return RunHandle(gen, self._chat_traj)

    def chat_resume(self, run_id: str) -> RunHandle:
        """恢复一个历史会话（mode=chat 的 run）：重建消息历史后继续对话。

        与 `resume()` 的区别：resume 是断点续跑（重放/继续未完成工具执行）；
        chat_resume 只恢复对话上下文，不重放工具。
        """
        if self._chat_messages is not None:
            raise ValueError("已有活动会话，请先 chat_end() 再恢复其他会话")
        traj = Trajectory.from_db(run_id, self.config.db_path)
        messages = traj.to_messages()
        workdir = ""
        for e in traj.events:
            if e.type == EventType.run_start:
                workdir = e.payload.get("workdir", "")
                break
        self._workdir = workdir
        bound_tools = {name: t.bind(workdir) for name, t in self._tool_registry.items()}
        last_llm = next((e for e in reversed(traj.events) if e.type == EventType.llm_response), None)
        self._chat_traj = traj
        self._chat_messages = messages
        self._chat_bound_tools = bound_tools
        self._chat_turn = (last_llm.turn or 0) + 1 if last_llm else 0
        self._complete_pending_tools((last_llm.turn or 0) if last_llm else 0)
        gen = self._run_gen(
            traj, messages, [self._chat_turn], bound_tools, chat_mode=True,
        )
        return RunHandle(gen, traj)

    def chat_end(self) -> None:
        """结束当前会话：emit run_end(reason=chat_end) + 落库 + 会话记忆蒸馏 + 清空。"""
        if self._chat_traj is None:
            return
        if not any(e.type == EventType.run_end for e in self._chat_traj.events):
            self._chat_traj.emit(EventType.run_end, payload={"reason": "chat_end"})
        self._persist(self._chat_traj)
        self._distill_session(self._chat_traj)
        self._chat_traj = None
        self._chat_messages = None
        self._chat_bound_tools = None
        self._chat_turn = 0

    def compact_chat(self, keep_turns: int | None = None) -> dict:
        """手动压缩当前会话（对应 opencode /compact）。

        绕过压缩阈值，强制执行 stale_snip + auto_compact（LLM 摘要），
        只保留 system + 摘要 + 最近 keep_turns 轮。压缩后同步内存消息、
        写入轨迹 compression 事件并落库。返回 {"before", "after", "affected"}。
        """
        if self._chat_messages is None or self._chat_traj is None:
            raise ValueError("当前没有活动会话")
        from vague_code.agent.context_compress import auto_compact, stale_snip
        from vague_code.agent.context_tokens import (
            compute_budget,
            set_tokenizer_for_model,
            should_skip_thinking,
        )

        set_tokenizer_for_model(self.config.model)
        budget = compute_budget(self.config.model)
        cfg = self.config.compression
        skip_thinking = should_skip_thinking(self.config.model)
        messages = self._chat_messages

        messages, stale_report = stale_snip(
            messages, cfg.stale_snip_keep_recent, self._tool_specs, skip_thinking
        )
        messages, compact_report = auto_compact(
            messages, self.backend, self.config.model,
            cfg.auto_compact_keep_turns if keep_turns is None else keep_turns,
            self._tool_specs, skip_thinking,
        )
        self._chat_messages = messages

        for report in (stale_report, compact_report):
            self._chat_traj.emit(EventType.compression, turn=self._chat_turn, payload={
                "layer": report.layer,
                "before_tokens": report.before_tokens,
                "after_tokens": report.after_tokens,
                "affected": report.affected,
                "budget": budget,
                "skip_thinking": report.skip_thinking,
                **({"detail": report.detail} if report.detail else {}),
            })
        self._persist(self._chat_traj)
        self._fire_state_change("compression", {
            "layer": "manual_compact",
            "before": compact_report.before_tokens,
            "after": compact_report.after_tokens,
            "budget": budget,
            "affected": compact_report.affected,
            "utilization": round(compact_report.after_tokens / budget, 4) if budget > 0 else 0.0,
        })
        return {
            "before": compact_report.before_tokens,
            "after": compact_report.after_tokens,
            "affected": compact_report.affected,
            "summary": str(compact_report.detail.get("summary_text") or ""),
        }

    @property
    def in_chat(self) -> bool:
        return self._chat_messages is not None

    def summarize(self, task: str, reply: str, max_chars: int = 15) -> str:
        """Generate a short session title via a light LLM call (ADR-0026).

        Falls back to a truncated task on any failure. Does not consume a turn
        or write trajectory events.
        """
        from vague_code.agent.ir import TextBlock

        try:
            messages = [
                Message(
                    role="system",
                    content="为下面这段对话生成一个 15 字以内的中文标题，只输出标题本身，不要引号。",
                ),
                Message(role="user", content=f"任务：{task}\n回复：{reply[:500]}"),
            ]
            resp = self.backend.complete(
                messages, tools=None, config={"model": self.config.model},
            )
            text = "".join(
                b.text for b in resp.message.content if isinstance(b, TextBlock)
            ).strip()
            text = text.strip("""'\"「」『』""").strip()
            if text:
                return text[:max_chars]
        except Exception:
            pass
        return (task or "会话").strip()[:max_chars]

    def _init_run(
        self, task: str, workdir: str, *, mode: str | None = None,
        identity: str | None = None,
    ) -> tuple[Trajectory, list[Message] | None, dict[str, Tool]]:
        """初始化一次运行/会话：traj + 首条消息 + 绑定工具（start 与 chat 首轮共用）。"""
        self._workdir = workdir
        run_id = uuid.uuid4().hex[:12]
        traj = Trajectory(run_id=run_id, config=self.config)

        from vague_code.agent.context import SystemPrompt

        # Repo map: build symbol index + optional injection (degradable)
        self._repo_index = None
        repo_map_text = ""
        if self.config.repo_map.enabled:
            try:
                from vague_code.agent.repomap import RepoIndex
                index = RepoIndex(workdir=workdir, max_files=self.config.repo_map.max_files)
                index.build()
                if index.size > 0:
                    self._repo_index = index
                    repo_map_text = index.to_map_text(self.config.repo_map.max_map_tokens)
            except Exception as e:
                import warnings
                warnings.warn(f"Failed to build repo index: {e}", stacklevel=2)
                self._repo_index = None

        system_prompt = SystemPrompt(workdir, identity=identity).build()
        if repo_map_text:
            system_prompt += "\n\n## 代码库符号地图\n" + repo_map_text

        # Memory: inject project memory (distilled history, capped)
        memory_text = ""
        if self.config.memory.enabled:
            mf = self._get_memory_file(workdir)
            if mf is not None:
                memory_text = mf.inject_text()
        if memory_text:
            system_prompt += (
                "\n\n## 项目记忆（历史会话蒸馏，可编辑 .agent/memory.md）\n" + memory_text
            )

        traj.emit(EventType.run_start, payload={
            "task": task,
            "workdir": workdir,
            "system_prompt": system_prompt,
            "config": self.config.to_public_dict(),
            "tools": sorted(self._tool_registry.keys()),
            **({"mode": mode} if mode else {}),
        })

        try:
            bound_tools = bind_tools(self._tool_registry, workdir)
        except Exception as e:
            traj.emit(EventType.error, payload={"kind": "tool_bind_error", "message": str(e)})
            traj.emit(EventType.run_end, payload={"reason": "tool_bind_error"})
            self._persist(traj)
            return traj, None, {}

        messages: list[Message] = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=task),
        ]

        # Repo map: register code_search tool when index is available
        if self._repo_index is not None:
            from vague_code.agent.tools.code_search import CodeSearchTool
            self._tool_specs.append(CodeSearchTool.spec())
            bound_tools["code_search"] = CodeSearchTool(workdir, self._repo_index)

        return traj, messages, bound_tools

    # ── Memory (file-based, ADR-0014) ────────────────────────────────────────

    def _memory_path(self, workdir: str) -> Path | None:
        """memory 文件路径：相对路径按 workdir 解析（项目隔离），绝对路径直用。"""
        if not workdir:
            return None
        p = Path(self.config.memory.memory_file)
        if p.is_absolute():
            return p
        return Path(workdir) / p

    def _get_memory_file(self, workdir: str) -> MemoryFile | None:
        """按 workdir 懒建 MemoryFile（失败降级为 None，不中断运行）。"""
        if not self.config.memory.enabled:
            return None
        path = self._memory_path(workdir)
        if path is None:
            return None
        key = str(path.resolve())
        if key not in self._memory_files:
            self._memory_files[key] = MemoryFile(path)
        return self._memory_files[key]

    def _distill_session(self, traj: Trajectory) -> None:
        """会话结束蒸馏：一次 LLM 总结 → 追加到项目记忆文件。失败静默降级。

        run() 收尾与 chat_end() 调用；非交互（CLI 任务）同样执行。
        """
        if not self.config.memory.enabled or not self.config.memory.session_end_distill:
            return
        mf = self._get_memory_file(self._workdir)
        if mf is None:
            return
        task = ""
        for e in traj.events:
            if e.type == EventType.run_start:
                task = e.payload.get("task", "")
                break
        if not task.strip():
            return
        try:
            model = self.config.memory.distill_model or self.config.model
            resp = self.backend.complete(
                [Message(role="user", content=_MEMORY_DISTILL_PROMPT.format(task=task[:2000]))],
                tools=None,
                config={"model": model},
            )
            text = _response_text(resp)
            appended = 0
            for block in re.split(r"(?m)^## ", text.strip()):
                lines = [ln for ln in block.strip().splitlines() if ln.strip()]
                if not lines:
                    continue
                if lines[0] == "无" and len(lines) == 1:
                    continue
                title = lines[0][:60]
                body = "\n".join(lines[1:]).strip()
                if body and mf.append(title=title, content=body, source_session=traj.run_id):
                    appended += 1
            if appended:
                traj.emit(EventType.memory_distill, payload={
                    "run_id": traj.run_id,
                    "appended": appended,
                    "memory_file": str(mf.path),
                })
        except Exception as e:
            import warnings
            warnings.warn(f"Failed to distill session memory: {e}", stacklevel=2)

    def _run_gen(
        self,
        traj: Trajectory,
        messages: list[Message],
        turn_box: list[int],
        bound_tools: dict[str, Tool],
        *,
        chat_mode: bool = False,
    ) -> Iterator[StreamEvent]:
        try:
            policy = RetryPolicy.from_config(self.config.transport)
            self._stuck_streak = 0
            self._edits_at_last_stuck = 0
            while turn_box[0] < self.config.max_turns:
                turn = turn_box[0]
                # 周期监督（ADR-0020 #2）：每 period 轮一次，turn=0 时无轨迹可看，跳过
                if (self.config.supervision.enabled and turn > 0
                        and turn % self.config.supervision.period == 0):
                    verdict = self._run_supervision(traj, turn, mode="periodic")
                    if verdict is not None:
                        assessment, guidance = verdict
                        if self._maybe_stop_supervision(traj, turn, assessment):
                            return
                        if guidance:
                            messages.append(Message(
                                role="user",
                                content=f"[监督反馈 {assessment}]\n{guidance}",
                            ))
                queued_guidance = self._drain_guidance()
                if queued_guidance:
                    messages.append(Message(role="user", content="\n".join(queued_guidance)))
                traj.emit(EventType.turn_start, turn=turn)
                self._fire_state_change("turn_start", {"turn": turn})

                call_config = {
                    "model": self.config.model,
                    "stream": self.config.transport.stream,
                    "max_tokens": self.config.max_output_tokens,
                }
                if self.config.reasoning_effort:
                    call_config["reasoning_effort"] = self.config.reasoning_effort

                retry_index = 0
                resp: ModelResponse | None = None

                from vague_code.agent.context import compress_chain
                from vague_code.agent.context_tokens import (
                    compute_budget,
                    count_tokens,
                    set_tokenizer_for_model,
                    should_skip_thinking,
                )

                set_tokenizer_for_model(self.config.model)
                budget = compute_budget(self.config.model)
                cfg = self.config.compression
                skip_thinking = should_skip_thinking(self.config.model)

                try:
                    messages, reports = compress_chain(
                        messages, self._tool_specs, cfg, budget,
                        backend=self.backend, model=self.config.model,
                        skip_thinking=skip_thinking,
                        events=traj.events,
                    )
                except Exception as e:
                    traj.emit(EventType.error, turn=turn, payload={
                        "kind": "compression_error",
                        "message": str(e),
                    })
                    reports = []
                    try:
                        from vague_code.agent.context_compress import truncate
                        messages, _ = truncate(messages, budget, self._tool_specs, skip_thinking)
                    except Exception as _ce:
                        traj.emit(EventType.error, turn=turn, payload={
                            "kind": "truncation_fallback_failed",
                            "message": str(_ce),
                        })
                if chat_mode:
                    self._chat_messages = messages
                if reports:
                    for r in reports:
                        traj.emit(EventType.compression, turn=turn, payload={
                            "layer": r.layer,
                            "before_tokens": r.before_tokens,
                            "after_tokens": r.after_tokens,
                            "affected": r.affected,
                            "budget": budget,
                            "skip_thinking": r.skip_thinking,
                            **({"detail": r.detail} if r.detail else {}),
                        })
                else:
                    total = count_tokens(messages, self._tool_specs, skip_thinking=skip_thinking)
                    traj.emit(EventType.compression, turn=turn, payload={
                        "layer": "budget",
                        "before_tokens": total,
                        "after_tokens": total,
                        "affected": 0,
                        "budget": budget,
                        "skip_thinking": skip_thinking,
                        "utilization": round(total / budget, 4) if budget > 0 else 0.0,
                    })
                if reports:
                    r_latest = reports[-1]
                    self._fire_state_change("compression", {
                        "layer": r_latest.layer,
                        "before": r_latest.before_tokens,
                        "after": r_latest.after_tokens,
                        "budget": budget,
                    })
                else:
                    self._fire_state_change("compression", {
                        "layer": "budget", "utilization": 0.0, "budget": budget,
                    })

                # Memory: auto_compact distillation → memory.md
                if self.config.memory.enabled:
                    mf = self._get_memory_file(self._workdir)
                    if mf is not None:
                        for r in reports:
                            if r.layer == "auto_compact" and r.affected > 0 and r.detail.get("summary_text"):
                                summary = r.detail["summary_text"]
                                mf.append(
                                    title=summary.strip().splitlines()[0][:40],
                                    content=summary,
                                    source_session=traj.run_id,
                                )

                while True:
                    aggregator = _StreamAggregator()
                    message_end: MessageEnd | None = None
                    buffered: list[tuple[float, StreamEvent]] = []

                    try:
                        for ev in self._stream_from(messages, self._tool_specs, call_config):
                            buffered.append((time.time(), ev))
                            aggregator.feed(ev)
                            yield ev
                            if isinstance(ev, MessageEnd):
                                message_end = ev
                        if message_end is None:
                            raise StreamDisconnect("Stream ended without MessageEnd")
                        resp = aggregator.result(message_end)
                    except Exception as e:
                        decision = classify_llm_error(e)
                        if not decision.retryable or not policy.enabled or retry_index >= policy.max_attempts:
                            if decision.retryable and policy.enabled:
                                traj.emit(EventType.error, turn=turn, payload={
                                    "kind": "retry_exhausted",
                                    "attempts": retry_index,
                                    "last_error_kind": decision.error_kind,
                                    "message": str(e),
                                })
                            else:
                                traj.emit(EventType.error, turn=turn, payload={"kind": decision.error_kind, "message": str(e)})
                            traj.emit(EventType.run_end, payload={"reason": decision.error_kind})
                            return

                        delay = policy.delay(retry_index)
                        traj.emit(EventType.retry, turn=turn, payload={
                            "attempt": retry_index + 1,
                            "delay_s": delay,
                            "reason": decision.reason,
                            "exception": type(e).__name__,
                            "estimated_input_tokens": estimate_input_tokens(messages, self._tool_specs, skip_thinking=skip_thinking),
                        })
                        yield RetryNotice(attempt=retry_index + 1, delay_s=delay, reason=decision.reason)
                        time.sleep(delay)
                        retry_index += 1
                        continue

                    for ts, ev in buffered:
                        traj.emit(EventType.stream_event, turn=turn, payload=ev.to_dict(), ts=ts)
                    break

                traj.emit(EventType.llm_response, turn=turn, payload={
                    "stop_reason": resp.stop_reason.value,
                    "usage": resp.usage.to_dict(),
                    "blocks": [b.to_dict() for b in resp.message.content],
                })
                self._fire_state_change("llm_response", {
                    "turn": turn, "usage": resp.usage.to_dict(),
                    "stop_reason": resp.stop_reason.value,
                })

                if resp.stop_reason in (StopReason.end_turn, StopReason.stop_sequence):
                    # 完成校验（ADR-0020 #3）：主 agent 声明完成时监督者全局判定
                    if self.config.supervision.enabled:
                        verdict = self._run_supervision(traj, turn, mode="final")
                        if verdict is not None:
                            assessment, guidance = verdict
                            if self._maybe_stop_supervision(traj, turn, assessment):
                                return
                            if guidance:
                                messages.append(Message(
                                    role="user",
                                    content=f"[监督反馈 {assessment}]\n{guidance}",
                                ))
                            turn_box[0] += 1
                            continue
                    if chat_mode:
                        assert self._chat_messages is not None
                        self._chat_messages.append(resp.message)
                        self._chat_turn = turn_box[0] + 1
                        return
                    traj.emit(EventType.run_end, payload={"reason": "end_turn"})
                    return

                if resp.stop_reason in (StopReason.max_tokens, StopReason.content_filter, StopReason.unknown):
                    if chat_mode:
                        assert self._chat_messages is not None
                        self._chat_messages.append(resp.message)
                        self._chat_turn = turn_box[0] + 1
                    traj.emit(EventType.run_end, payload={"reason": resp.stop_reason.value})
                    return

                if resp.stop_reason == StopReason.tool_use:
                    tool_uses = [b for b in resp.message.content if isinstance(b, ToolUseBlock)]
                    if not tool_uses:
                        traj.emit(EventType.error, turn=turn, payload={"kind": "empty_tool_use", "message": "Model returned tool_use with no ToolUseBlock"})
                        traj.emit(EventType.run_end, payload={"reason": "empty_tool_use"})
                        return
                    if turn + 1 >= self.config.max_turns:
                        traj.emit(EventType.run_end, payload={
                            "reason": "max_turns",
                            "pending_tool_calls": len(tool_uses),
                        })
                        return

                    messages.append(resp.message)
                    self._checkpoint(traj)
                    tool_results: list[Block] = []

                    # Permission check pre-pass (covers ALL tools)
                    from vague_code.agent.permission import Decision, PermissionMode
                    perm_mode = PermissionMode(self.config.permission_mode)
                    allowed_tool_uses: list[ToolUseBlock] = []
                    for block in tool_uses:
                        decision, content, is_error = self._check_tool_permission(
                            block, perm_mode, turn, traj, check_confirm=True, tools=bound_tools,
                        )
                        if decision == Decision.DENY:
                            traj.emit(EventType.tool_call, turn=turn, payload={
                                "id": block.id, "name": block.name, "input": block.input,
                            })
                            traj.emit(EventType.tool_result, turn=turn, payload={
                                "tool_use_id": block.id, "content": content, "is_error": True,
                            })
                            tool_results.append(ToolResultBlock(
                                tool_use_id=block.id, content=content, is_error=True,
                            ))
                            continue
                        allowed_tool_uses.append(block)

                    if not allowed_tool_uses:
                        messages.append(Message(role="user", content=tool_results))
                        turn_box[0] += 1
                        continue

                    if self.config.concurrent_tools and len(allowed_tool_uses) > 1:
                        from vague_code.agent.concurrency import execute_concurrent
                        try:
                            con_results = execute_concurrent(allowed_tool_uses, bound_tools)
                        except Exception as e:
                            traj.emit(EventType.error, turn=turn, payload={"kind": "concurrent_execution_error", "message": str(e)})
                            traj.emit(EventType.run_end, payload={"reason": "concurrent_execution_error"})
                            return
                        result_by_id = {r.tool_use_id: r for r in con_results}
                        for block in allowed_tool_uses:
                            traj.emit(EventType.tool_call, turn=turn, payload={"id": block.id, "name": block.name, "input": block.input})
                            result = result_by_id.get(block.id)
                            if result is None:
                                content = f"[工具 {block.name} 缺少结果]"
                                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": content, "is_error": True})
                                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=content, is_error=True))
                                self._fire_on_tool_result(block.id, block.name, content, True)
                            else:
                                content = self._truncate_tool_content(result.content)
                                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": result.tool_use_id, "content": content, "is_error": result.is_error})
                                tool_results.append(ToolResultBlock(tool_use_id=result.tool_use_id, content=content, is_error=result.is_error))
                                self._fire_on_tool_result(result.tool_use_id, block.name, content, result.is_error)
                    else:
                        for block in allowed_tool_uses:
                            traj.emit(EventType.tool_call, turn=turn, payload={"id": block.id, "name": block.name, "input": block.input})
                            tool = bound_tools.get(block.name)
                            if tool is None:
                                error_msg = f"未知工具: {block.name}"
                                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": error_msg, "is_error": True})
                                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=error_msg, is_error=True))
                                self._fire_on_tool_result(block.id, block.name, error_msg, True)
                                continue
                            try:
                                tool_result = tool(block.input)
                                content = self._truncate_tool_content(tool_result.output)
                                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": content, "is_error": False, "metadata": tool_result.metadata})
                                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=content, meta=tool_result.metadata))
                                self._fire_on_tool_result(block.id, block.name, content, False)
                            except Exception as e:
                                error_msg = f"{type(e).__name__}: {e}"
                                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": error_msg, "is_error": True})
                                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=error_msg, is_error=True))
                                self._fire_on_tool_result(block.id, block.name, error_msg, True)

                    messages.append(Message(role="user", content=tool_results))
                    turn_box[0] += 1
                    self._checkpoint(traj)

            traj.emit(EventType.run_end, payload={"reason": "max_turns"})
        finally:
            self._persist(traj)

    def _checkpoint(self, traj: Trajectory) -> None:
        try:
            traj.persist()
        except Exception:
            import warnings
            warnings.warn(f"Checkpoint persist failed for run {traj.run_id}", stacklevel=2)

    def _check_tool_permission(
        self,
        block: ToolUseBlock,
        perm_mode,
        turn: int,
        traj: Trajectory,
        *,
        check_confirm: bool = True,
        tools: dict[str, Tool] | None = None,
    ) -> tuple:
        """Evaluate permission for a single tool block.

        Returns (decision, content, is_error).
        decision is ALLOW → content="", is_error=False.
        decision is DENY → content is the human-readable error, is_error=True.
        """
        from vague_code.agent.permission import Decision, PermissionMode, Operation, evaluate
        tool = tools.get(block.name) if tools else None
        permission_class = tool.permission_class(block.input) if tool else "write"
        op = Operation(
            tool_name=block.name, input=block.input,
            command=block.input.get("command", "") if block.name == "bash" else "",
        )
        decision = evaluate(perm_mode, permission_class, op, rules=self._permission_rules)
        traj.emit(EventType.permission_check, turn=turn, payload={
            "tool": block.name, "decision": decision.value,
            "command": (op.command or "")[:200],
        })

        if decision == Decision.DENY:
            content = f"权限不足：当前模式 {perm_mode.value} 禁止此操作"
            if perm_mode == PermissionMode.SAFE:
                content += "\n\n提示：使用 `/mode normal` 切换到更高权限模式。"
            return Decision.DENY, content, True

        if decision == Decision.CONFIRM and check_confirm:
            if self._on_permission:
                from vague_code.agent.prewrite import compute_prewrite_review
                op.review = compute_prewrite_review(
                    block.name, block.input, getattr(self, "_workdir", "")
                )
                decision = self._on_permission(op, decision)
            else:
                decision = Decision.DENY
            if decision == Decision.DENY:
                content = "权限不足"
                if op.feedback:
                    content += f"：{op.feedback}"
                if not self._on_permission:
                    content = "权限不足：无交互确认可用"
                return Decision.DENY, content, True

        return Decision.ALLOW, "", False

    def _drain_guidance(self) -> list[str]:
        if self.guidance_provider is None:
            return []
        try:
            return list(self.guidance_provider())
        except Exception:
            return []

    # ── Supervision Agent（ADR-0020 / plans-0018）──────────────────────────

    def _run_supervision(
        self,
        traj: Trajectory,
        turn: int,
        mode: str = "periodic",
    ) -> tuple[str, str] | None:
        """单次监督调用：构造输入 → backend.complete（无工具）→ 解析五值 JSON。

        解析失败重试 1 次后跳过（返回 None）。每次调用（含失败）落
        `supervision` 事件供审计。返回 (assessment, guidance) 或 None。
        """
        cfg = self.config.supervision
        model = cfg.model or self.config.model
        prompt = self._supervision_input(traj, turn, mode)

        for attempt in range(2):
            try:
                resp = self.backend.complete(
                    [
                        Message(role="system", content=SUPERVISION_SYSTEM_PROMPT),
                        Message(role="user", content=prompt),
                    ],
                    tools=None,
                    config={"model": model},
                )
            except Exception as e:
                traj.emit(EventType.error, turn=turn, payload={
                    "kind": "supervision_error", "message": str(e),
                })
                return None

            parsed: dict | None = None
            text = "".join(b.text for b in resp.message.content if isinstance(b, TextBlock))
            data = _extract_json_obj(text)
            if data and data.get("assessment") in SUPERVISION_ASSESSMENTS:
                parsed = {
                    "assessment": data["assessment"],
                    "guidance": str(data.get("guidance", "")),
                    "evidence": str(data.get("evidence", "")),
                }
            traj.emit(EventType.supervision, turn=turn, payload={
                "mode": mode,
                "attempt": attempt + 1,
                "assessment": parsed["assessment"] if parsed else None,
                "guidance": parsed["guidance"] if parsed else "",
                "evidence": parsed["evidence"] if parsed else "",
                "parse_error": "" if parsed else "invalid_json",
                "usage": resp.usage.to_dict(),
                "input_chars": len(prompt),
            })
            if parsed:
                return parsed["assessment"], parsed["guidance"]
        return None

    def _supervision_input(self, traj: Trajectory, turn: int, mode: str) -> str:
        """监督输入构造：任务文本（仅周期监督）+ 过程信号 + diff stat + 尾部轨迹。"""
        cfg = self.config.supervision
        sections: list[str] = []

        if mode == "periodic":
            task = ""
            for e in traj.events:
                if e.type == EventType.run_start:
                    task = e.payload.get("task", "")
                    break
            if task:
                sections.append(f"## 任务\n{task[:2000]}")

        stats = self._supervision_process_signals(traj)
        sections.append(
            "## 过程信号\n"
            f"- 已进行 turn: {stats['turns']}；工具调用: {stats['tool_total']}"
            f"（bash {stats['bash_calls']} 次，去重 {stats['unique_commands']} 种，"
            f"重复 {stats['repeated_commands']} 次）\n"
            f"- 编辑（write_file/patch）: {stats['edits']} 次；工具错误: {stats['errors']} 次\n"
            f"- 探索: 读取/搜索过 {stats['unique_files']} 个不同路径"
            f"（最近一次新路径于 turn {stats['last_new_file_turn']}）\n"
            f"- 测试信号: PASS {stats['test_pass']} / FAIL {stats['test_fail']}"
        )

        diff_stat = self._supervision_diff_stat()
        if diff_stat:
            sections.append(f"## 工作区 diff stat\n{diff_stat}")

        transcript = self._supervision_transcript(
            traj, max_turns=12, max_chars=cfg.max_input_tokens * 3,
        )
        sections.append(f"## 轨迹（尾部 12 轮）\n{transcript}")
        sections.append("只输出 JSON：{\"assessment\": \"...\", \"guidance\": \"...\", \"evidence\": \"...\"}")
        return "\n\n".join(sections)

    @staticmethod
    def _supervision_process_signals(traj: Trajectory) -> dict:
        stats = {"turns": 0, "tool_total": 0, "bash_calls": 0, "edits": 0,
                 "errors": 0, "test_pass": 0, "test_fail": 0,
                 "unique_files": 0, "last_new_file_turn": -1,
                 "unique_commands": 0, "repeated_commands": 0}
        seen_files: set[str] = set()
        seen_cmds: set[str] = set()
        for e in traj.events:
            if e.type == EventType.turn_start:
                stats["turns"] += 1
            elif e.type == EventType.tool_call:
                stats["tool_total"] += 1
                name = e.payload.get("name", "")
                inp = e.payload.get("input") or {}
                if name == "bash":
                    stats["bash_calls"] += 1
                    cmd = str(inp.get("command", ""))
                    if cmd in seen_cmds:
                        stats["repeated_commands"] += 1
                    else:
                        seen_cmds.add(cmd)
                elif name in ("write_file", "patch"):
                    stats["edits"] += 1
                elif name in ("read_file", "grep", "code_search"):
                    path = str(inp.get("path") or "")
                    if path and path not in seen_files:
                        seen_files.add(path)
                        stats["last_new_file_turn"] = e.turn if e.turn is not None else stats["turns"]
            elif e.type == EventType.tool_result:
                if e.payload.get("is_error"):
                    stats["errors"] += 1
                content = e.payload.get("content", "")
                if "[test] PASS" in content:
                    stats["test_pass"] += 1
                elif "[test] FAIL" in content:
                    stats["test_fail"] += 1
        stats["unique_files"] = len(seen_files)
        stats["unique_commands"] = len(seen_cmds)
        return stats

    def _supervision_diff_stat(self) -> str:
        workdir = getattr(self, "_workdir", "")
        if not workdir:
            return ""
        try:
            proc = subprocess.run(
                ["git", "-C", workdir, "diff", "--stat", "HEAD"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=10,
            )
            if proc.returncode != 0:
                return ""
            return proc.stdout.strip()[:2000]
        except Exception:
            return ""

    @staticmethod
    def _supervision_transcript(
        traj: Trajectory, max_turns: int = 12, max_chars: int = 24000,
    ) -> str:
        """尾部轨迹转写：按 turn 收集最近的 tool_call/tool_result/agent 文本。"""
        total_turns = sum(1 for e in traj.events if e.type == EventType.turn_start)
        min_turn = max(0, total_turns - max_turns) if total_turns > max_turns else 0

        lines: list[str] = []
        for e in traj.events:
            t = e.turn if e.turn is not None else -1
            if t < min_turn:
                continue
            if e.type == EventType.turn_start:
                lines.append(f"--- turn {t} ---")
            elif e.type == EventType.llm_response:
                for b in e.payload.get("blocks", []):
                    if b.get("type") == "text" and b.get("text", "").strip():
                        lines.append(f"[agent] {b['text'][:400]}")
            elif e.type == EventType.tool_call:
                lines.append(
                    f"[tool_call] {e.payload.get('name')}"
                    f"({json.dumps(e.payload.get('input', {}), ensure_ascii=False)[:300]})"
                )
            elif e.type == EventType.tool_result:
                content = e.payload.get("content", "")
                if len(content) > 600:
                    content = content[:600] + f"...[truncated {len(content)} chars]"
                err = "(!error)" if e.payload.get("is_error") else ""
                lines.append(f"[tool_result{err}] {content}")

        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[-max_chars:] + "\n...[transcript truncated]"
        return text

    @staticmethod
    def _count_edits(traj: Trajectory) -> int:
        return sum(
            1 for e in traj.events
            if e.type == EventType.tool_call and e.payload.get("name") in ("write_file", "patch")
        )

    def _maybe_stop_supervision(self, traj: Trajectory, turn: int, assessment: str) -> bool:
        """按监督评估决定是否终止。返回 True 表示已 emit run_end 终止。

        stuck 判停（ADR-0020 #4）：连续 stuck_limit 次 stuck 且**两次判定之间**
        零编辑才终止；两次 stuck 之间发生过编辑说明有推进，累计清零。
        """
        cfg = self.config.supervision
        if assessment == "done":
            traj.emit(EventType.run_end, payload={"reason": "supervisor_done"})
            return True
        if assessment == "stuck":
            edits = self._count_edits(traj)
            if self._stuck_streak > 0 and edits != self._edits_at_last_stuck:
                self._stuck_streak = 0
            self._stuck_streak += 1
            self._edits_at_last_stuck = edits
            if self._stuck_streak >= cfg.stuck_limit:
                traj.emit(EventType.run_end, payload={"reason": "stagnant"})
                return True
        else:
            self._stuck_streak = 0
        return False

    def add_permission_rule(self, pattern: str, action: str = "allow") -> None:
        from vague_code.agent.permission import Decision, PermissionRule
        self._permission_rules.append(PermissionRule(
            pattern=pattern,
            action=Decision.ALLOW if action == "allow" else Decision.DENY,
        ))

    def _fire_state_change(self, kind: str, payload: dict) -> None:
        if self.on_state_change:
            self.on_state_change(kind, payload)

    def _fire_on_tool_result(self, tool_id: str, tool_name: str, content: str, is_error: bool) -> None:
        if self.on_tool_result:
            self.on_tool_result(tool_id, tool_name, content, is_error)

    @staticmethod
    def _truncate_tool_content(content: str, max_chars: int = 50_000) -> str:
        if len(content) > max_chars:
            return content[:max_chars] + (
                f"\n\n[... 输出截断于 {max_chars} 字符，"
                f"总计: {len(content)} 字符]"
            )
        return content

    TERMINAL_STOP_REASONS = {"end_turn", "stop_sequence", "max_tokens", "content_filter", "unknown"}

    def resume(self, traj: Trajectory) -> Trajectory:
        self._validate_consistent(traj)

        if any(e.type == EventType.run_end for e in traj.events):
            return traj

        workdir = ""
        for e in traj.events:
            if e.type == EventType.run_start:
                workdir = e.payload.get("workdir", "")
                break

        bound_tools = {name: t.bind(workdir) for name, t in self._tool_registry.items()}
        messages = traj.to_messages()

        last_llm = next((e for e in reversed(traj.events) if e.type == EventType.llm_response), None)
        if last_llm is None:
            next_turn = 0
        else:
            sr = last_llm.payload.get("stop_reason")
            T = last_llm.turn or 0
            if sr in self.TERMINAL_STOP_REASONS:
                reason = "end_turn" if sr in ("end_turn", "stop_sequence") else sr
                traj.emit(EventType.run_end, payload={"reason": reason})
                self._persist(traj)
                return traj
            if T + 1 >= self.config.max_turns:
                n = sum(1 for b in last_llm.payload.get("blocks", []) if b.get("type") == "tool_use")
                traj.emit(EventType.run_end, payload={"reason": "max_turns", "pending_tool_calls": n})
                self._persist(traj)
                return traj
            self._execute_pending_tools(traj, messages, T, bound_tools)
            next_turn = T + 1

        gen = self._run_gen(traj, messages, [next_turn], bound_tools)
        for _ in gen:
            pass
        return traj

    def _validate_consistent(self, traj: Trajectory) -> None:
        for e in traj.events:
            if e.type == EventType.run_start:
                saved = e.payload.get("config", {})
                if saved.get("model") and saved["model"] != self.config.model:
                    import warnings
                    warnings.warn(f"Resuming with model {self.config.model}, original was {saved['model']}")
                stored_tools = e.payload.get("tools")
                if stored_tools is not None:
                    current_tools = sorted(self._tool_registry.keys())
                    if not set(stored_tools).issubset(set(current_tools)):
                        raise ValueError(
                            f"Tool registry mismatch on resume: stored={stored_tools}, current={current_tools}"
                        )
                else:
                    import warnings
                    warnings.warn("Resuming run without 'tools' field in run_start — skipping consistency check")
                return

    def _complete_pending_tools(self, turn: int, extra_text: str = "") -> bool:
        """补执行 chat 会话中断时悬挂的 tool_use（B3）。

        中断发生在工具执行中时，assistant 消息的 tool_calls 没有对应结果；
        直接续聊会让 codec 发出无结果的 tool_calls 导致 API 400。此方法在
        进入下一轮生成前复用 `_execute_pending_tools` 补执行并回填结果。
        `extra_text`（用户续聊文本）合并进结果消息，避免连续 user 消息。
        返回 True 表示发生了补执行（结果已回填到消息尾部）。
        """
        assert self._chat_traj is not None
        assert self._chat_messages is not None
        assert self._chat_bound_tools is not None
        if not self._execute_pending_tools(
            self._chat_traj, self._chat_messages, turn, self._chat_bound_tools
        ):
            return False
        if extra_text and extra_text.strip():
            last = self._chat_messages[-1]
            if last.role == "user":
                last.content.append(TextBlock(text=extra_text))
        return True

    def _execute_pending_tools(
        self,
        traj: Trajectory,
        messages: list[Message],
        turn: int,
        bound_tools: dict[str, Tool],
    ) -> bool:
        if not messages or messages[-1].role != "assistant":
            return False
        last_msg = messages[-1]
        pending = [
            b for b in last_msg.content
            if isinstance(b, ToolUseBlock)
            and not any(
                e.type == EventType.tool_result and e.payload.get("tool_use_id") == b.id
                for e in traj.events
            )
        ]
        if not pending:
            return False

        tool_results: list[Block] = []
        for block in pending:
            traj.emit(EventType.tool_call, turn=turn, payload={"id": block.id, "name": block.name, "input": block.input})

            # Permission check (same as _run_gen pre-pass)
            from vague_code.agent.permission import Decision, PermissionMode
            perm_mode = PermissionMode(self.config.permission_mode)
            decision, content, is_error = self._check_tool_permission(
                block, perm_mode, turn, traj, check_confirm=False, tools=bound_tools,
            )
            if decision == Decision.DENY:
                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": content, "is_error": True})
                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=content, is_error=True))
                continue

            tool = bound_tools.get(block.name)
            if tool is None:
                err = f"Unknown tool: {block.name}"
                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": err, "is_error": True})
                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=err, is_error=True))
                continue
            try:
                result = tool(block.input)
                content = self._truncate_tool_content(result.output)
                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": content, "is_error": False, "metadata": result.metadata})
                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=content, meta=result.metadata))
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": err, "is_error": True})
                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=err, is_error=True))

        messages.append(Message(role="user", content=tool_results))
        self._checkpoint(traj)
        return True

    def _stream_from(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        config: dict,
    ) -> Iterator[StreamEvent]:
        if config.get("stream") and hasattr(self.backend, "stream"):
            yield from self.backend.stream(messages, tools, config)
        else:
            # Adapter: wrap non-streaming complete() into events
            resp = self.backend.complete(messages, tools, config)
            yield MessageStart(model=config.get("model", "?"))
            for block in resp.message.content:
                if isinstance(block, TextBlock):
                    yield TextDelta(delta=block.text)
                elif isinstance(block, ThinkingBlock):
                    yield ThinkingStart()
                    yield ThinkingDelta(delta=block.text)
                    yield ThinkingEnd(signature=block.signature)
                elif isinstance(block, ToolUseBlock):
                    yield ToolUseStart(id=block.id, name=block.name)
                    yield ArgsDelta(id=block.id, delta=json.dumps(block.input, ensure_ascii=False))
                    yield ToolUseEnd(id=block.id)
            yield MessageEnd(
                stop_reason=resp.stop_reason,
                finish_reason=None,
                truncated=False,
                usage=resp.usage,
            )

    def _persist(self, traj: Trajectory) -> None:
        try:
            traj.persist()
        except Exception:
            import warnings
            warnings.warn("Failed to persist trajectory", stacklevel=2)
            try:
                recovery_path = Path(traj.config.db_path).with_suffix(f".{traj.run_id}.recovery.jsonl")
                recovery_path.parent.mkdir(parents=True, exist_ok=True)
                traj.export_jsonl(recovery_path)
                warnings.warn(f"Trajectory exported to recovery file: {recovery_path}", stacklevel=2)
            except Exception as _re:
                traj.emit(EventType.error, payload={
                    "kind": "persist_recovery_failed",
                    "message": str(_re),
                })
            last = traj.events[-1] if traj.events else None
            if last and last.type != EventType.run_end:
                traj.emit(EventType.run_end, payload={"reason": "persist_failed"})



