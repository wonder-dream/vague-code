# 0037: 模型目录更新（现行模型）+ TUI 首次引导（Setup Wizard）

- **日期**: 2026-08-10
- **状态**: approved（用户确认"落盘执行"）

## 阶段 A — 模型目录与端点更新（2026-08 官方核实）

| 厂商 | 现行模型（保留） | 删除 | 其他修正 |
|---|---|---|---|
| DeepSeek | `deepseek-v4-flash`(1M)、`deepseek-v4-pro` | deepseek-chat / deepseek-reasoner | v4-pro 窗口 64K→**1M**（官方实际 1M） |
| OpenAI | gpt-5.6-sol/terra/luna(1.05M) | —（已最新） | — |
| Anthropic | claude-fable-5 / opus-5 / sonnet-5(1M)、haiku-4-5(200K) | claude-sonnet-4-5 / opus-4-8 | **默认端点改官方 https://api.anthropic.com**（修正伪接入）；默认模型 → claude-fable-5 |

- AnthropicBackend 默认 model → `claude-fable-5`（backend.py）
- `claude-` 前缀窗口回退 1M（haiku-4-5 200K 精确条目优先）
- DeepSeek Anthropic 端点仅作为自定义 provider 可选（引导里不再出现）

## 阶段 B — TUI 首次引导（Setup Wizard）

- CLI `_tui_main` 缺 key 不退出：`backend=None + needs_setup=True` 启动 TUI
- `SetupWizard(ModalScreen)`：RadioSet 选 provider（内置3+自定义中转）→ 动态输入
  （内置=key 掩码；自定义=baseUrl+key+模型名+协议）→ 【测试连接】→ 完成
- 配置写全局：key → `~/.config/vague-code/.env`；provider → 合并
  `~/.config/vague-code/config.json`；key 读取链加全局 .env 层
- `_build_backend` 从 cli 移入 config.py（解 tui↔cli 循环依赖）
- 无跳过入口；内置默认模型 = 阶段 A 新默认（flash / gpt-5.6-sol / claude-fable-5）

## 验证

全量 pytest + ruff/mypy + 提交发布（v0.1.9）。
