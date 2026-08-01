# Troubleshooting

**谁需要读：** 遇到问题的所有用户
**前置阅读：** 无（按问题检索）
**读完能做什么：** 快速诊断和解决常见问题

---

## 1. 安装问题

| 症状 | 诊断 | 解决方案 |
|------|------|---------|
| `ModuleNotFoundError: No module named 'src'` | 工作目录不对 / 依赖未安装 | `cd xclaw && uv sync` |
| `ImportError: cannot import name` | Python 版本 < 3.12 | `python --version` → 升级到 3.12+ |
| `uv: command not found` | uv 未安装 | `pip install uv` |
| `Could not find a version` | 依赖冲突 | `uv sync --reinstall` |

---

## 2. API 问题

| 症状 | 诊断 | 解决方案 |
|------|------|---------|
| `DEEPSEEK_API_KEY not found` | 环境变量未设置 | 检查 `.env` → `echo DEEPSEEK_API_KEY=sk-xxx > .env` |
| `AuthenticationError` | API Key 无效 | 重新生成：https://platform.deepseek.com/api_keys |
| `RateLimitError` 重试耗尽 | 请求频率过高 | `--retry-max-delay-s 300` |
| `APITimeoutError` | 网络问题 | `--timeout-s 300` 或检查网络连接 |
| `404 Model not found` | 模型名不对 | 确认 `--model deepseek-v4-flash` |
| `InternalServerError` | API 服务故障 | 检查 https://status.deepseek.com → 等待恢复 |

---

## 3. 工具问题

| 症状 | 诊断 | 解决方案 |
|------|------|---------|
| `PermissionError: 检测到路径穿越` | Agent 尝试访问外部文件 | 检查任务描述，确保路径参数是相对路径 |
| `FileNotFoundError` | 文件不存在 | Agent 自动重新检查，正常行为 |
| 多次匹配错误（patch） | old_str 不够精确 | Agent 会自动增加上下文，正常行为 |
| `RuntimeError: 命令在 30 秒后超时` | 命令超 30 秒 | Agent 会尝试更简单的命令 |
| 乱码 | Windows 编码 | 使用 Windows Terminal |

---

## 4. 压缩问题

| 症状 | 诊断 | 解决方案 |
|------|------|---------|
| Agent "忘记"了之前看过的文件 | auto_compact 丢失了关键信息 | 增加 `auto_compact_keep_turns` 或关闭压缩 |
| `compression_error` 在轨迹中 | 压缩代码异常 | 降级到 truncation 兜底，不影响主循环 |
| 压缩回收 token 很少（<10K） | 短会话（<30 轮） | 正常行为，压缩目标 30+ 轮 |
| 关闭压缩后还慢 | 问题不在压缩 | 检查 LLM 延迟和工具执行时间 |

---

## 5. 权限问题

| 症状 | 诊断 | 解决方案 |
|------|------|---------|
| Agent 不能修改文件 | safe 模式 | `/mode normal` |
| 每次写文件都要确认 | normal 模式默认 | `Ctrl+Y` 持久化或 `/mode autoedit` |
| 危险命令一直被拒绝 | 即使 auto 模式也需要确认 | `Ctrl+Y` 持久化确认 |
| CLI 中工具被跳过 | CLI 无交互确认 | 切 `--permission-mode autoedit` |

---

## 6. 记忆问题

| 症状 | 诊断 | 解决方案 |
|------|------|---------|
| 记忆搜索返回空 | 没有已蒸馏的记忆 | 需要足够长的会话触发 auto_compact |
| `memory.db` 越来越大 | 每条摘要都写入 | 手动清理旧记录 |

---

## 7. TUI 问题

| 症状 | 诊断 | 解决方案 |
|------|------|---------|
| 界面乱码 | 终端不兼容 | 使用 Windows Terminal / iTerm2 |
| `xcode tui` 找不到 | 未安装 wrapper | `python -m src.cli tui "task"` |
| Agent 卡住无法停止 | Ctrl+C 信号问题 | `/quit` 退出重启 |
| resume 失败 | run_id 或工具集不匹配 | 检查 run_id 和工具注册 |

---

## 下一篇

→ **faq.md**——常见设计决策问答。
