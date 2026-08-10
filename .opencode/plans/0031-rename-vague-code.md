# 0031: 全仓库重命名为 vague-code（PyPI 发布准备）

- **日期**: 2026-08-10
- **状态**: approved（用户确认：不保留 XClaw，全部改为 vague-code）

## 命名映射（vague-code 已核实 PyPI 可用）

| 层 | 旧 | 新 |
|---|---|---|
| PyPI/项目名 | xcode | `vague-code` |
| import 根（目录） | `src/` | `vague_code/` |
| console 脚本 | `xcode` | `vague-code` |
| 产品显示名 | XClaw | `vague-code` |
| 类名标识符 | VagueCodeApp / VagueCodeAgentRunner / VagueCodeViewMixin / VagueCodeMarkdown / VagueCodeScreen 等 | `VagueCode*` 驼峰对应 |

## 影响面（已盘点）

- py 代码：374 处 `src.*` import / 62 文件；XClaw 标识符+字符串待 grep 全量盘点
- uv.lock:1820 项目名；pyproject；.gitignore（xcode.egg-info）；.github/test.yml
- 文档：docs+.opencode 125 个 md + README/CHANGELOG/AGENTS.md
- 排除：`tests/_target_bug/`（独立 fixture 仓库，自带 src/，pytest 已排除）

## 执行步骤

1. `git mv src vague_code`；py 代码批量替换：
   - `src.` → `vague_code.`；`from src import` → `from vague_code import`
   - 字符串 `"XClaw"` → `"vague-code"`；类标识符 `XClaw` → `VagueCode`（驼峰）
   - `src/` 路径字符串 → `vague_code/`（pyproject/CI 同步）
2. pyproject：`name="vague-code"`、`[project.scripts] vague-code = "vague_code.cli:main"`、
   mypy overrides `vague_code.*`、补元数据（description/readme/license/authors/classifiers/urls）、
   `[tool.setuptools.package-data] vague_code.tui = ["*.tcss"]`
3. 补 `vague_code/cli/__main__.py`（`python -m vague_code.cli` 可用）
4. uv.lock 项目名 → vague-code；.gitignore/test.yml 路径更新
5. 测试文件引用同步 + 全量 pytest 回归
6. 文档替换（docs/.opencode/README/CHANGELOG/审查.md/死代码.md/AGENTS.md）
7. 构建验证：`python -m build` + `twine check` + 全新 venv 安装冒烟
8. CI：test.yml 加 pytest；新增 publish.yml（tag 触发 + PyPI trusted publishing）
9. 全量回归（pytest/ruff/mypy）+ commit

## 验证标准

- 全量 775+ tests 通过；ruff/mypy 零错误
- `grep -r "XClaw\|src\.\|src/"` 仅剩预期内引用（_target_bug 除外）
- wheel 安装后 `vague-code --help`、TUI 启动、`python -m vague_code.cli` 可用
