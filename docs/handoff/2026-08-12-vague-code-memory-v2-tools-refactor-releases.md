# Handoff 2026-08-12：记忆 v2 + 工具系统企业级重构 + 模型选择界面 + 发布 v0.1.18→v0.1.22

> 本会话完成五件大事：**① 记忆系统重构 v2**（SQLite 移除 → `.agent/memory.md` 文件式记忆，ADR-0014）；**② 工具系统企业级重构**（class-based 抽象层 + 元数据内聚，ADR-0004；实现层五层改造，plans/0019）；**③ 新增 web_search 工具**（DDG + network 权限首次落地）；**④ TUI 三连**（SetupWizard 美化 / 独立模型选择界面 ModelPicker / 无 key 切换自动引导补缺）；**⑤ 发布 v0.1.18→v0.1.22 共 5 个版本**（含 3 轮发布修复）。教学线已按用户确认关闭。工作区干净，全部已提交推送。

---

## 一、记忆系统 v2（ADR-0014 重构，`aec4661`+`a2d559e`）

**SQLite 记忆库 + memory_search 工具整体移除**（蒸馏产物即上下文，DB 检索是多余分层；对齐 Claude Code auto memory / Codex memories / PI 会话档案）。

- **`memory_file.py`**：`MemoryFile` 分块解析 / `append`（sha256 前 12 位幂等去重）/ `remove_sections(run_id)` / `inject_text`（200 行·25KB 截尾，UTF-8 安全）；按 workdir 物理隔离（`.agent/` 已 gitignore）；进程内按路径加锁
- **写入双时点**：auto_compact 摘要落盘 + 会话结束（`run()`/`chat_end()`）一次 LLM 总结（`_distill_session`，`distill_model` 可配，异常静默降级，`memory_distill` 事件）
- **读取**：system prompt 注入「## 项目记忆」段（repo map 同位）
- **顺修**：`chat_resume` 补设 `_workdir`（既有缺陷）；`MemoryConfig{memory_file, session_end_distill, distill_model}` 取代 `memory_db_path`
- TUI 删会话清理改 `remove_sections`；旧 `runs/memory.db` 不迁移

## 二、工具系统 class-based 重构（ADR-0004，`d213e97`+`e2608f2`+`0c70f6e`）

**动机**：tools.py（定义）/ permission.py（权限分支）/ concurrency.py（scope 分支）三处按工具名硬编码割裂。调研 opencode/Codex/PI 源码后定案。

- **`tools/` 包**：`base.py`（Tool ABC 模板方法 + 两态错误 ToolError 层次 + 资源模型 OpType/ScopeType/ResourceScope + normalize_path/pattern_prefix）+ `fs.py`（5 文件工具）+ `bash_tool.py` + `code_search.py` + `truncate.py`（统一截断 2000 行/50KB，对齐 PI truncate.ts）
- **元数据内聚**：`permission_class()` / `resource_scope()` 迁入工具定义——permission.py `evaluate(mode, permission_class, operation, rules)` 删工具名分支；concurrency.py `_scope_for` 删 `_extract_scope` 分支
- **修 bug**：`code_search` 权限 read（原默认走 write 策略）
- **ToolResult 结构化**（对齐 opencode ExecuteResult）：output + metadata（截断统计），tool_result 事件与 Block.meta 附带
- **两态错误**（对齐 Codex RespondToModel|Fatal）：ToolInputError(ValueError)/ToolPathError(PermissionError)/ToolNotFoundError(FileNotFoundError, 含 Did you mean? 建议)/ToolExistsError(FileExistsError)/ToolExecutionError(RuntimeError)——多继承内置异常保测试兼容
- **行为变更**：read_file 上限 10MB→50KB

## 三、工具实现层重构（plans/0019，`83351a5`~`4e480a6`）

| 工具 | 改动 |
|---|---|
| read_file | `offset`/`limit`（1-indexed 流式行读 + 行区间头）、目录读取、二进制检测（28 扩展名黑名单 + NUL + 非可打印>30%）、单行截断 2000、读入预算耗尽显式截断标记 |
| glob | 结果字典序排序（确定性）+ `path` 参数 |
| write_file / patch | 原子写（tempfile + os.replace，新文件 0644 覆盖保留 mode） |
| grep | **ripgrep 驱动**（`ripgrep==14.1.0` core 依赖；`--sort path` 确定性 + .gitignore + 二进制跳过）+ `ignore_case`/`literal`/`context` + 行内截断 500；rg 不可用降级纯 Python（`_rg_path`：PATH → Scripts 目录） |
| bash | `timeout` 参数（默认 30）；输出 >50KB 落盘 `full_output_path`（`on_truncated` hook，模型用 read_file 读回） |
| code_search | `k` 参数（1-50 默认 20）+ signature 行内截断 200 |
| **web_search（新）** | DuckDuckGo（`ddgs>=9.14.4` 零 key）；`permission="network"`（预留分类首次落地：SAFE 拒绝/NORMAL 确认/AUTO 放行）；**动态注入**（`config.web_search.enabled`，评测零影响）；httpx 尊重 HTTP_PROXY |

**B6 stdin 传输否决**（Windows cmd 实测）：启动横幅污染输出 + multiline 仍坏——保留 shell=True + 临时脚本 hack。

## 四、TUI（`2cdcb11` / `373b7b0`+`f8e2fab`）

- **SetupWizard 美化**（v0.1.20）：半透明遮罩 + 暗色圆角对话框 + 品牌绿标题 + provider 卡片化 + 输入框聚焦绿边 + 按钮右对齐（对齐项目主题 #7bba55/#1e2126）
- **ModelPicker 独立模型选择界面**（v0.1.22，对齐 opencode）：`/model` 打开居中面板——搜索过滤 + 滚动列表 + ↑/↓ 选择 + Enter 确认 + Esc 取消 + provider 徽章 + `[需配置]` 标记；确认复用 `_apply_model_change`
- **无 key 引导补缺**：`_apply_model_change` 统一前置 key 检查（欢迎页/同 provider 原不查 key 直接切 → 缺失弹 SetupWizard 预选）

## 五、发布序列（5 个版本，全部 PyPI 已发布）

| 版本 | 内容 | CI 修复 |
|---|---|---|
| 0.1.18 | UA 适配 + / 浮层 Enter 修复 | — |
| 0.1.19 | 工具重构 + web_search | ① pyproject 漏 ddgs 依赖（本机已装掩盖）②③ bash 测试 Windows `type` 命令 Linux 不兼容（平台分支） |
| 0.1.20 | SetupWizard 美化 | — |
| 0.1.21 | **ripgrep 15.1.0→14.1.0**（15.1.0 无 Windows wheel，用户 sdist 源码构建失败；14.1.0 全平台 wheel） | — |
| 0.1.22 | ModelPicker + 无 key 引导 | ① 测试缺 key mock（新 key 检查行为）② SetupWizard 轮询预算 10s→20s |

**发布教训**：① 发布前自查"本机环境 vs CI 干净环境"差异（pip 已装的依赖、.env 里的 key、Windows 专用命令）——本轮 3 类坑全是这个；② 依赖选型查 wheel 平台覆盖（`pip download --only-binary` 实测）；③ 新行为改变旧测试语义时先查测试是否依赖隐式环境（key mock）。

## 六、待办（下次会话）

| 优先级 | 事项 | 说明 |
|---|---|---|
| P1 | UA 适配真实链路实测 | 代码完成（v0.1.18），需真实 key 连 code.newcli.com/claude 验证；foxcode key 已暴露建议轮换 |
| P1 | Claude tokenizer API 对齐实测 | 需真实 usage key；`count_tokens` 对比脚本可重建（messages/count_tokens 端点 + UA=claude-cli/1.0.66） |
| P2 | Polyglot repeat≥3 采样 | 验证 100% 稳定性（~$40） |
| P2 | 评测复验 | 本轮工具/记忆改动不涉及 eval 链路（web_search 动态注入、记忆 harness 已禁），但若发新版建议跑一次 polyglot fake 冒烟确认 |
| P3 | 密钥轮换 | 用户暂缓 |

## 七、坑与注意事项（本会话新增）

1. **Textual ModalScreen**：`compose` 不能 yield Screen（只能 `push_screen`）；Screen 自定义方法避免 `_render` 命名（与 Textual 内部冲突，返回 None 直接崩）；push_screen 异步切换需轮询 `app.screen`（测试 0.05s×100）
2. **PowerShell 引号**：单引号里 `\r\n` 是字面（正则替换字符串用反引号转义）；`-replace` 的替换文本不做转义解释
3. **truncate 字节限**：行间换行计入（+1 字节/行保守修正）；读入预算与截断同阈值会导致 truncate 永不触发——预算耗尽时工具侧显式截断标记
4. **rg 集成**：`--max-files` 在 rg 15 不存在（rc=2 会静默降级掩盖真实路径）；ripgrep pip 包无 Python 模块（console script rg.exe 在 Scripts）
5. **CI 平台差异**：Linux `type` 是内建命令（显示命令类型无输出）；POSIX 权限 mode 测试需 skipif
6. **旧坑继续有效**：网络/缓存、manifest 语义、`--fresh` 多进程互斥、PowerShell 引号写临时 .py、Windows 文件锁（_force_remove）

## 八、面试故事线增量

1. **记忆 v2**：SQLite 记忆是过度设计（蒸馏产物即上下文，检索与注入两套机制服务同一件事）→ 砍库改文件（对齐 Claude auto memory/Codex memories/PI 会话档案），项目隔离从字段过滤变物理文件级，可 diff/可编辑/零 schema
2. **工具系统**：三处按工具名硬编码分支（定义/权限/并发）→ 调研 opencode/Codex/PI 源码后 class-based 元数据内聚 + 模板方法 + 两态错误 + 结构化输出——新增工具 = 子类 + 注册
3. **web_search**：network 权限分类从预留到首次落地；动态注入设计保证评测零影响（Polyglot 反作弊不被破坏）
4. **发布工程**：5 个版本 3 轮 CI 修复——"本机过/CI 挂"三连（依赖漏配/平台命令/隐式 key），沉淀自查清单

## 九、参考文件

- 决策：`docs/adr/0014-memory-system.md`（v2）、`docs/adr/0004-tool-registry-factory.md`（重构 + 实施补充）
- 计划：`docs/plans/0019-tool-implementation-refactor.md`
- 教学线：已关闭（3 份教学交接删除，articles 资料保留）
- 上一交接：`docs/handoff/2026-08-11-vague-code-eval-complete-and-releases.md`
