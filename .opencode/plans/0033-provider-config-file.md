# 0033: 配置文件简化 provider 配置（对齐 opencode）

- **日期**: 2026-08-10
- **状态**: approved（用户确认：vague-code.json 两级合并 + 加 init 命令）

## 目标

中转站等自定义端点配置一次，日常零参数启动（对齐 opencode 的 opencode.json 模式）。

```bash
vague-code init                 # 生成模板
# 编辑 vague-code.json 填中转站信息
vague-code tui                  # 零参数直接用默认 provider/model
```

## 设计

- 配置文件：项目 `vague-code.json` + 全局 `~/.config/vague-code/config.json`，两级合并（项目优先，providers 深合并）
- 结构：`defaultProvider` / `defaultModel` / `providers{name: {baseUrl, apiKeyEnv, protocol?, models?}}`
- 取值优先级：CLI 参数 > 项目配置 > 全局配置 > 内置默认
- `--provider` 放开 choices：内置（deepseek/openai/anthropic）+ 任意自定义名
- 自定义 provider 默认 OpenAI 协议（`protocol: "anthropic"` 可选走 Anthropic codec）
- `.env` 继续只存 key；`apiKeyEnv` 指向其变量名
- 非法 JSON / 缺失 → warnings + 回退（不崩溃）

## 改动清单

1. `vague_code/config.py`（新）：load_config() 两级加载合并 + init 模板生成
2. `vague_code/cli/__init__.py`：
   - `--provider` 去掉 choices
   - 三入口 parse 后从配置补缺省参数（provider/model/base_url/api_key_env）
   - `_provider_settings` 支持自定义 provider（查配置）
   - 新增 `vague-code init` 子命令（在 main 入口最前分派）
3. `vague_code/tui/commands/handlers.py`：MODELS 分组——自定义 provider 用配置 models
4. README：教程重写（配置一次 vs 每次敲参数）
5. 测试：test_config.py（新）+ test_cli + test_commands

## 验证

全量 pytest + ruff/mypy + 真实冒烟（fox 配置零参数直连中转站）。
