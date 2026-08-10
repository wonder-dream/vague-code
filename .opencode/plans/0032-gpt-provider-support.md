# 0032: GPT 系列 API 支持（CLI + TUI）

- **日期**: 2026-08-10
- **状态**: approved（用户确认"TUI 也要支持 GPT"）

## 背景

codec/协议层已兼容 OpenAI 协议（`DeepSeekBackend` 即标准 OpenAI SDK），但 CLI/TUI
入口堵死：`--provider` 仅 deepseek/anthropic、base_url 写死、key 只认
DEEPSEEK_API_KEY/ANTHROPIC_API_KEY、`/model` 列表只有 deepseek 模型、
tokenizer 全局固定为 DeepSeek 词表、`CONTEXT_WINDOWS` 无 GPT 条目。

## 设计

统一以 `--provider` + 可覆盖参数配置：

| provider | base_url 默认 | key env 默认 |
|---|---|---|
| `deepseek`（不变） | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `openai`（新增） | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `anthropic`（不变） | DeepSeek Anthropic 兼容端点 | `ANTHROPIC_API_KEY` |
| 任意 | `--base-url` 覆盖 | `--api-key-env` 覆盖 |

用法：`vague-code tui --provider openai --model gpt-4o`（或 `--base-url https://api.openai.com/v1 --api-key-env OPENAI_API_KEY`）。TUI 内 `/model` 在当前 provider 的模型列表内切换；运行时跨 provider 重建 backend 不做（v2 候选）。

## 改动清单

1. **`vague_code/cli/__init__.py`**（main/_tui_main/_chat_main 三入口）
   - `--provider` choices 加 `openai`；新增 `--base-url`、`--api-key-env`
   - `_resolve_api_key` 泛化：`--api-key-env` 优先，否则按 provider 默认
   - deepseek 分支 base_url 用 `args.base_url or 默认`；`_tui_main` 透传 provider

2. **`vague_code/tui/__init__.py`**：`main()` 签名加 `provider: str = "deepseek"`，透传 XClawApp

3. **`vague_code/tui/app.py`**：`XClawApp.__init__` 加 `provider` 参数；`_topbar_text` 用
   `self._provider`（顺带修复 topbar 目前显示 "?" 的问题）；`model_changed` action 的
   provider 用 `self._provider`

4. **`vague_code/tui/commands/handlers.py`**：`ModelCommandHandler.MODELS` 按 provider
   分组（deepseek: 现有 4 个；openai: gpt-4o/gpt-4.1/gpt-4.1-mini/o3-mini/o4-mini；
   anthropic: claude-sonnet-4-5/claude-opus-4-8），无参 `/model` 显示当前 provider 列表

5. **`vague_code/agent/context_tokens.py`**
   - `CONTEXT_WINDOWS` 补：`gpt-4o` 128K、`gpt-4.1` 1M、`gpt-4.1-mini` 1M、
     `o3-mini` 200K、`o4-mini` 200K
   - 新增 `set_tokenizer_for_model(model)`：`gpt-*`/`o1-*`/`o3-*`/`o4-*` → tiktoken
     cl100k；其余维持 deepseek_tokenizer（双 encoder 模块级缓存，与全局 config.model
     语义一致）

6. **`vague_code/agent/loop.py`**：`_run_gen` 每轮顶部调用
   `set_tokenizer_for_model(self.config.model)`（`/model` 切换下一轮生效）

7. **README.md**：配置章节补 GPT 用法示例与 `--provider openai` 说明

8. **测试**
   - `test_cli.py`：`--provider openai` 默认 base_url/key env；`--base-url`/`--api-key-env`
     覆盖透传（monkeypatch 捕获 backend 构造参数）
   - `test_commands.py`：MODELS 按 provider 分组、切换 action 的 provider 字段
   - `test_context_tokens.py`：`set_tokenizer_for_model` 切换、新 CONTEXT_WINDOWS 条目

## 不做（v1 范围外）

- TUI 运行时跨 provider 重建 backend（切换端点需要重建 agent，复杂；v2 候选）
- eval harness 的 OpenAI 支持（eval 链仍限 DeepSeek，README 注明）

## 验证

- 全量 pytest（775+新增）+ ruff/mypy
- `vague-code --help`/`vague-code tui --help` 显示新参数
- TUI 冒烟：`/model` 列表按 provider 正确（fake backend 测试）
