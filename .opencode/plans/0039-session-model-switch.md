# 0039: 会话级模型隔离 + 跨 provider 切换 + 无 key 引导 + 取消回退

- **日期**: 2026-08-10
- **状态**: approved（用户确认：会话级隔离模型加入本计划，先落盘完整计划，暂不改代码）

## 背景与现状

1. **模型/backend 是 app 全局单例**：`XClawApp._config`/`_backend` 唯一，
   所有会话共用；`/model` 只改 `_config.model` 不换 backend。
2. **隐患**：跨 provider 改模型后，旧会话仍用旧 backend（如 deepseek 端点）但
   `config.model` 已是 gpt-5.6-sol → 请求模型名与端点不匹配（404）。
3. **需求**：会话之间可以各用不同模型（provider/model/backend 隔离）；
   会话内 `/model` 可跨 provider 切换；目标 provider 无 API key 时弹 SetupWizard
   配置后才能用；取消则回退原模型。

## 设计

### 会话级状态（核心）

`SessionState` 新增持有自己的运行时模型状态：

| 字段 | 说明 |
|---|---|
| `provider: str` | 会话所属 provider（默认 app 级默认） |
| `model: str` | 会话当前模型（默认 app 级默认） |
| `backend` | 会话的后端实例（惰性：首轮建 agent 时解析 key 构造并缓存） |

- `/model` 切换只作用于当前会话；其他会话不受影响
- topbar 显示当前会话的 provider/model
- 会话内跨 provider 切换 = 给该会话 agent 换 backend + 更新 config.model
  （保留会话上下文/消息，不重建 agent）

### 跨 provider 切换流（作用于当前会话）

```
会话中 /model gpt-5.6-sol（当前 provider=deepseek）
  → resolve_model_provider("gpt-5.6-sol") = openai（内置目录 + 配置文件 models 反查）
  → 目标 provider == 会话 provider？是 → 直切（仅 model）
  → 否（跨 provider）：
      解析 openai 的 key（项目 .env → 全局 .env → 环境变量）
      ├─ 有 key → 会话级直切：state.agent._backend = build_backend(openai, key, ...)
      │           + state.agent.config.model / state.model / state.provider / state.backend 更新
      └─ 无 key → push SetupWizard(preselect=openai, preselect_model=gpt-5.6-sol, cancellable=True)
           ├─ 完成 → 写全局配置 + 会话级切换（复用 _apply_setup，结果落会话）
           └─ 取消（Esc / 取消按钮）→ dismiss，会话状态零改动 = 回退原模型
```

### /model 无参 picker

显示**全部 provider 的模型**（detail 标注服务商，如 `openai/gpt-5.6-sol`），
选择后走同一套会话级切换逻辑。

## 改动清单

### 1. `vague_code/agent/config.py`（会话级 agent 可换后端）

- `Agent` 无需新接口：`_backend`/`config.model` 已有，直接赋值（项目已有
  runner/app 直接读写私有属性的惯例，0030 的 `_chat_*` 同理）

### 2. `vague_code/config.py`（模型目录解析）

- 新增 `resolve_model_provider(model, file_config) -> str | None`：
  遍历内置目录 + 配置文件 providers.models，反查模型所属 provider
- 新增 `all_provider_models(file_config) -> list[tuple[str, str]]`：
  全部 (model, provider) 列表（picker 用）

### 3. `vague_code/tui/session.py`（会话级状态）

- `SessionState` 加字段：`provider: str`、`model: str`、`backend: object | None`
- 惰性语义：首轮建 agent 时用 `state.provider`/`state.model` 解析

### 4. `vague_code/tui/app.py`（切换逻辑会话化）

- `_new_session_agent(state)`：按 `state.provider`/`state.model` 解析 key →
  `build_backend` → Agent；缓存 `state.backend`；默认取 app 级默认
- `model_changed` 分支重写（会话级，见切换流）：
  - 同 provider：仅改 `state.model` + `state.agent.config.model`
  - 跨 provider 有 key：换 `state.agent._backend` + 更新 state 三件套
  - 跨 provider 无 key：弹 SetupWizard（预选 + 可取消）
- `_apply_setup` 结果会话化：写全局配置 + 更新当前会话 state + 换 agent backend
- `_topbar_text`：显示当前会话的 provider/model（无会话回退 app 默认）
- 新会话默认：从 app 默认（config.model/provider）播种到 state

### 5. `vague_code/tui/commands/handlers.py`（/model 支持跨 provider）

- `_models()` → `all_provider_models`（全部 provider）
- picker items：detail=provider，label=model
- `/model <name>`：action 带 `resolve_model_provider` 解析的 provider
- 提示语：切换目标 provider 无 key 时由 app 弹窗，handler 不改

### 6. `vague_code/tui/screens/setup.py`（预选 + 可取消）

- `SetupWizard(app, preselect=None, preselect_model="", cancellable=False)`
  - `preselect`：RadioSet 预选 provider（on_mount 高亮对应项）
  - `preselect_model`：内置 provider 的默认模型用它（非内置默认表）
  - `cancellable=True`：Esc + 新增"取消并保留原模型"按钮 → `dismiss(False)`
    （首次启动引导保持不可取消）
- `_finish` 成功路径：用 preselect_model 优先于 DEFAULT_MODELS

### 7. 测试

- `resolve_model_provider` / `all_provider_models` 单测（内置 + 配置 + 冲突模型名）
- `SessionState` 惰性字段默认值
- `/model` 同 provider 直切（state.model 更新，其他会话不受影响）
- 跨 provider 无 key → SetupWizard 弹出且预选 provider/模型正确
- Esc 取消 → `state.model`/`state.provider`/`agent._backend` 全部不变（回退）
- wizard 完成 → 会话级切换 + agent backend 替换 + 全局配置写入断言
- 双会话并发：会话 A deepseek / 会话 B openai，topbar 各显示各的
- 既有 800 测试全量回归

## 执行顺序（一步一项）

1. config.py：`resolve_model_provider` / `all_provider_models` + 单测
2. session.py：SessionState 字段 + 单测
3. app.py：`_new_session_agent` 会话化 + topbar 会话化
4. app.py：`model_changed` 会话级切换（同/跨 provider + key 检查）
5. screens/setup.py：preselect + cancellable
6. app.py：无 key 弹窗接线 + 取消回退
7. handlers.py：/model 全 provider picker + provider 解析
8. 双会话隔离集成测试 + 全量回归
9. CHANGELOG + 提交 + 发布

## 验证标准

- 800+ 测试全绿，ruff/mypy 零错误
- 行为验证：
  - 会话 A deepseek-v4-pro / 会话 B gpt-5.6-sol 并行互不影响
  - `/model gpt-5.6-sol`（deepseek 会话）无 openai key → 弹引导；取消 → 模型不变；
    配置成功 → 会话 B 用 gpt-5.6-sol，会话 A 仍 deepseek
  - topbar 跟随当前会话显示 provider/model
- 发布 v0.1.11
