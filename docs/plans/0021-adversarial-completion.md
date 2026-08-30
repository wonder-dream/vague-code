# 0021: 35 项注入防护剩余项逐项落地方案

> 来源：`docs/security/35-adversarial-scenarios.md` 未完成项（🟡16 + ❌3 = 19 项）
> 方法：按 TDD（先红后绿），每项独立 commit；完成即回填状态 + 补 `adversarial_tasks_extended.json` 用例。

---

## 阶段 0（P0）— 纯本地逻辑、低风险

| 顺序 | 项 | 文件 | 要点 |
|------|----|------|------|
| 0.1 | #25 凭据补盲 | `permission.py`、`tools/fs.py` | `type` 移出安全白名单；B1 清单扩 `.aws/credentials`、`.npmrc`、`.pypirc`、`.netrc`、`.ssh/config`、`.git/credentials` |
| 0.2 | #32 关键文件写保护扩充 | `tools/fs.py` | `.env`、`.git/**`、`tests/**`、`.aws/**`、`.ssh/**` 写保护 |
| 0.3 | #30/#31 路径加固 | `tools/base.py`、`tools/fs.py` | 拒 UNC；junction/reparse 检测；symlink 默认拒绝；glob/grep 二次校验 |
| 0.4 | #9/#10/#13 不可信标记补齐 | `tools/fs.py`、`context_compress.py`、`context.py` | read_file 返回内容包 `mark_untrusted`；压缩摘要标注来源 |
| 0.5 | #15 编码载荷正则补全 | `permission.py` | `openssl enc -d`、`certutil -urlcache`、`%COMSPEC%`、`cmd /v:on /c` |
| 0.6 | #8 多阶段注入 | `permission.py` | cmd `for ... do` / `if ... (` 分支段提取分类 |

**验收**：每项有单测；`type .env`、写 `.env`/`tests/**`、junction 逃逸、read 内容不可信、编码执行链、`for do rm` 全部拦截。

---

## 阶段 1（P1）— 交互/策略设计

| 顺序 | 项 | 文件 | 要点 |
|------|----|------|------|
| 1.1 | #1 指令来源分级 | `context.py`、`loop.py` | 触发词 soft 拦截 + `security_hint` 事件 |
| 1.2 | #2 auto 误批防护 | `permission.py`、`loop.py` | `CRITICAL_COMMANDS` 高危二次确认（auto 也 CONFIRM） |
| 1.3 | #5 系统敏感路径黑名单 | `permission.py`/`path_policy.py`、`tools/base.py` | 系统路径前缀黑名单，写入/执行拒绝 |
| 1.4 | #16 echo 参数级意图检测 | `permission.py` | echo/printf 参数危险词 soft 告警，重定向到脚本→危险 |
| 1.5 | #35 产品层 verify 门禁 | `loop.py`、`config.py` | `require_verify` 配置；未跑测试禁止 end_turn |

**验收**：触发词 hint 事件；auto 高危 CONFIRM；系统路径拒绝；echo 脚本重定向危险；未验证不可完成。

---

## 阶段 2（P2）— 数据策略 / 审计

| 顺序 | 项 | 文件 | 要点 |
|------|----|------|------|
| 2.1 | #28 输出凭据脱敏 | `redact.py`（新建）、`tools/base.py` | `redact_secrets` 统一脱敏 |
| 2.2 | #6/#27/#29 外发/写出口审计 | `web_search.py`、`loop.py` | 查询去敏；network/写共享目录 `security_alert` |

**验收**：密钥输出脱敏；外发/写敏感目录产生审计事件。

---

## 阶段 3（P3）— 产品级沙箱（可选）

| 顺序 | 项 | 文件 | 要点 |
|------|----|------|------|
| 3.1 | #11 沙箱 | `bash_tool.py`、`config.py` | `sandbox` 配置（none/docker/wsl），执行层隔离 |

**验收**：沙箱模式 `rm -rf /` 不影响宿主；SWE/Polyglot 不回归。

---

## 执行节奏

```
阶段0（0.1→0.6）：逐项 1 commit，纯代码+单测
阶段1（1.1→1.5）：依赖阶段0 #25/#32 落地
阶段2（2.1→2.2）：redact.py 先行
阶段3（3.1）：独立里程碑
每项完成 → 更新 35-adversarial-scenarios.md 状态 + adversarial_tasks_extended.json
```
