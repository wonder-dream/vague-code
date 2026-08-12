# 0019：工具实现层重构 + web_search 工具

日期：2026-08-12 ｜ 状态：approved（用户确认）

## 背景

ADR-0004 class-based 抽象层落地后，工具内部实现仍有改进空间。调研 opencode / Codex / PI 实现后定案五层改动：

- **A**：确定性 + 健壮性（glob 排序/path、write/patch 原子写、read 单行截断/二进制检测）
- **B**：read 能力补全（offset/limit 行读、目录读取、行区间头）
- **C**：grep 现代化（ripgrep 驱动 + ignore_case/literal/context 参数 + 行内截断 500 + Python 降级）
- **D**：bash（timeout 参数、输出超限写文件、stdin 命令传输）+ code_search（k 参数、行内截断）
- **E**：web_search 新工具（DuckDuckGo 零 key + network 权限分类首次落地）

## 调研依据（2026-08-12，源码核实）

| 参考 | 结论 |
|---|---|
| opencode `read.ts` | offset/limit（1-indexed 行读）、目录读取、二进制检测（28 扩展名黑名单 + NUL + 非可打印 >30%）、单行截断 2000 字符 |
| opencode `glob.ts` | `path` 参数限定目录、limit 100、count/truncated metadata |
| opencode `grep.ts` | ripgrep 驱动、`Found N matches` 输出头、单文件搜索支持 |
| opencode `websearch.ts` | provider 可插拔（Exa/Parallel）、独立 `websearch` 权限分类、25s 超时 |
| PI `bash.ts` | `timeout` 参数（可选）、命令经 stdin 传输（commandTransport）、killProcessTree、fullOutputPath 超限写文件、PI_* 会话 env |
| PI `grep.ts` | ignoreCase/literal/context/limit 参数、GREP_MAX_LINE_LENGTH=500、GrepOperations 可插拔 |
| PI `truncate.ts` | 2000 行/50KB 双限、结构化 TruncationResult（已落地 ADR-0004） |

> PI 的 tmux 文档是 TUI 平台按键配置（extended-keys csi-u），与 bash 后端无关——已核实。

## 改动清单

### A 层：确定性 + 健壮性
- **glob**：结果排序（字典序确定性）；`path` 参数（搜索目录，resolve_path 校验 + 目录检查）；metadata count/truncated
- **write_file / patch**：原子写（tempfile + os.replace）；新文件 0644，覆盖保留原 mode
- **read_file**：单行截断（>2000 字符截断 + suffix）；二进制检测（前 4096 字节采样：NUL 或非可打印 >30% → 提示文本）

### B 层：read 能力补全（签名变化，模型可见）
- 参数：`offset`（1-indexed，默认 1）、`limit`（默认 2000）
- 流式逐行读取（readline 循环），字节总量受统一截断上限约束
- 目录 path → 排序条目列表
- 输出头：`第 X-Y 行（共 N 行）：`（模型明确行区间位置）

### C 层：grep 现代化
- 依赖：`ripgrep>=15.1.0`（pip 包含二进制 wheel，core 依赖）
- 调用：`rg --line-number --no-heading --color never --sort path [--ignore-case] [--fixed-strings] [--context N] -g '!dir/**'(EXCLUDED_DIRS) pattern [path]`
- 参数：`ignore_case` / `literal` / `context`（PI 同款）；行内截断 500
- 结果截断 500 条（现常量）；尊重 .gitignore（rg 默认）
- **降级**：rg 不可用 → warn + 纯 Python 回退（保留现有实现）
- 输出格式保持 `file:line: content`（模型兼容）

### D 层：bash + code_search
- **bash**：`timeout` 参数（可选秒数，默认 30）；输出超限（>50KB）→ 完整输出写 `<temp>/vaguecode_bash_<id>.out`，metadata `full_output_path`（模型用 read_file 读回）
- **bash B6**：命令经 stdin 传输（对齐 PI commandTransport）——先 Windows cmd 冒烟验证，失败则保留 shell=True + multiline hack
- **code_search**：`k` 参数（1-50，默认 20）；signature 行内截断 200 字符

### E 层：web_search 新工具
- `tools/web_search.py`：`WebSearchTool`（class-based）
- 后端：ddgs 库（DuckDuckGo，零 key，`ddgs>=9.14.4`）；`provider` 配置留可插拔
- 参数：`query`（必填）、`max_results`（默认 5，1-10）
- 输出：每条 `标题\nURL\n摘要`
- 权限：`permission="network"`（复用 permission.py 预留分类；SAFE 拒绝/NORMAL 确认/AUTO 放行）
- scope：READ + WORKSPACE
- **动态注入**（不在 DEFAULT_TOOLS，loop 按 `config.web_search.enabled` 注入）→ 评测 harness/polyglot 零影响
- 配置：`WebSearchConfig{enabled=True, provider="ddg", max_results=5}`
- 网络：httpx 尊重 HTTP_PROXY/HTTPS_PROXY（中国大陆需代理可达 DDG）
- 失败：网络错误/无结果 → 友好提示文本（工具输出语义）

## 签名变更汇总（模型可见，回归重点）

```
read_file: {path, offset?=1, limit?=2000}              # 目录 path → 条目列表
glob:      {pattern, path?}
grep:      {pattern, path?, include?, ignore_case?, literal?, context?}
bash:      {command, cwd?, timeout?}
code_search: {query, path?, k?=20}
web_search: {query, max_results?=5}                    # 新增
```

## 边界（不做）

- bash 后台任务（PI 设计原则同款）；shell 可配置；bash AST 权限预扫描（opencode 太重）
- web_search：不做 contentMaxCharacters 提炼（provider 能力）；不做 web_fetch；不做多后端（provider 留扩展位）
- grep：不学 opencode limit=100（保持 500 结果上限）

## 验证

- 每层跑对应测试 + 最终全量回归（829 基线）
- 冒烟：offset 行读/目录/二进制/原子写/排序确定性/rg 输出解析/降级路径/stdin 传输（Windows cmd）/真实 web_search 一次查询（代理链路）
- ruff + mypy

## Commit 序列

1. `feat(tools): read offset/limit + 目录 + 二进制检测 + 单行截断`
2. `feat(tools): glob 排序 + path；write/patch 原子写`
3. `feat(tools): grep ripgrep 现代化 + 参数扩展`
4. `feat(tools): bash timeout + 输出落盘 + stdin（验证后）；code_search k`
5. `feat(tools): web_search（DDG + network 权限）`
6. `test + docs`
