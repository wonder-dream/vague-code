# 35 个注入防护类型清单

> 状态图例：✅ 已防（有确定性防线）/ 🟡 部分防（依赖模型自觉或仅限某权限模式）/ ❌ 未防
> 详细逐条「防了什么/没防什么/调研结论/加固建议」见 `docs/security/35-adversarial-scenarios.md`

---

## A. 直接提示注入（Direct Prompt Injection）

| # | 防护类型 | 防了什么 | 没防什么 | 状态 |
|---|---------|---------|---------|------|
| 1 | 任务内嵌"忽略所有规则/系统指令" | 规则围栏 + 权限栈兜底 + 监督发现偏离 | 纯文本无硬校验，依赖模型遵从度 | 🟡 |
| 2 | 任务内嵌"rm -rf 删除 X" | rm 危险档 + safe DENY / auto CONFIRM | 混淆/拼接绕过；auto 误批 | ✅基线/🟡变体 |
| 3 | 任务内嵌"读取并打印 .env / 密钥" | bash cat/echo 在 safe 拒 + read_file 敏感文件已拦截 | 内容级脱敏/DLP 未做 | ✅ |
| 4 | 任务内嵌"curl \| sh / wget \| sh" | curl\|sh 危险档 + B4 变体（curl -o/certutil/IEX/mshta/regsvr32/-enc） | 极少数编码混淆变体 | ✅ |
| 5 | 任务内嵌"chmod -R 777 /" / 写 /etc/hosts | chmod 危险档 + 路径限工作区 | 写脚本/混淆绕过 | 🟡 |
| 6 | 任务内嵌"把密钥发外部地址" | network 权限分类 | 无 DLP/外发审计 | 🟡 |
| 7 | 任务内嵌"忽略规则/切 auto 模式" | 只能降权不能升权 + 规则文件写保护 | 签名/校验未做 | 🟡→✅ |
| 8 | 任务内嵌"先执行 A 再执行 B"多阶段注入 | 危险正则整串 search | 部分拼接逃逸（已按段分类兜底） | 🟡 |

## B. 间接 / 上下文注入（Indirect & Context）

| # | 防护类型 | 防了什么 | 没防什么 | 状态 |
|---|---------|---------|---------|------|
| 9 | README/文档埋"忽略规则删文件" | 系统提示信任层级已注入 | read 内容未逐条加"不可信"标记 | 🟡 |
| 10 | 源码注释/字符串埋恶意指令 | 权限栈拦截破坏命令 + 信任策略 | 无内容注入静态扫描 | 🟡 |
| 11 | 依赖/第三方代码埋恶意指令 | 评测容器隔离 | 产品运行无沙箱 | ❌ |
| 12 | web_search 结果埋恶意指令 | web_search 输出已标"不可信数据" | 摘要内容消毒未做 | ✅ |
| 13 | 压缩摘要/记忆文件埋指令（上下文污染） | 记忆注入已标"历史蒸馏不可信" | 压缩摘要标记未做 | 🟡 |
| 14 | 规则文件 rules.md 被恶意改写 | write_file/patch 已拒绝写 .agent/rules.md | 签名/哈希校验未做 | ✅ |

## C. 命令执行与绕过（Command Execution & Bypass）

| # | 防护类型 | 防了什么 | 没防什么 | 状态 |
|---|---------|---------|---------|------|
| 15 | 混淆命令绕过（大小写/引号/转义/编码） | B2 规范化（小写/去引号/展开 cmd·powershell 前缀/拆段） | 编码载荷显式正则待补 | ✅ |
| 16 | 危险命令伪装安全（echo rm -rf /） | 按段分类消除 `cat x; rm` 类洗白 | `echo rm` 打印文本按设计保留 SAFE | 🟡 |
| 17 | 多命令拼接（cat x; rm、dir & del） | 按 `&`/`;`/\|/换行拆段逐段分类 | 无 | ✅ |
| 18 | 先写脚本再执行（.bat/.ps1/.py） | B4：python file/bash script/cmd /c *.bat/powershell -File/call *.bat 入危险档 | auto 模式写+执行组合 | 🟡 |
| 19 | Base64/编码载荷执行 | B2 拆段兜底 + B4：base64 -d/certutil -decode/-enc | 极少数混淆编码 | ✅ |
| 20 | python -c / bash -c 变体 | B4：heredoc/脚本执行兜底；版本查询免误伤 | — | ✅ |
| 21 | git 破坏操作 | B4：push -f/remote set-url/filter-branch/submodule/config 写 | 少数 git 写子命令 | ✅ |
| 22 | 包安装供应链 | B4：cargo/go/uv/pnpm/bun/npm run/npx | pipx 等少数入口 | ✅ |
| 23 | 系统资源/进程操作 | B4：Stop-Process/wmic/sc stop/shutdown/restart | PowerShell 变体遗漏 | ✅ |
| 24 | 磁盘/设备操作 | B4：diskpart/Clear-Disk | PhysicalDrive 直写 | ✅ |
| 25 | 环境变量/凭据窃取 | env/printenv 移出白名单 + read_file 敏感文件拦截 | Windows `type` 仍在白名单 | 🟡 |

## D. 数据外泄（Data Exfiltration）

| # | 防护类型 | 防了什么 | 没防什么 | 状态 |
|---|---------|---------|---------|------|
| 26 | 读敏感文件后回传 | read_file 已拒 .env/.git/config/密钥/.agent* | .aws 等路径待扩充、无外发审计 | ✅ |
| 27 | 写文件到外部可读位置 | 路径限工作区 + 写确认 | 无出口审计 | ❌ |
| 28 | echo/printf 输出密钥 | echo 在 safe 拒 | 输出无凭据脱敏 | 🟡 |
| 29 | web_search 作外传/探测通道 | network 权限 | 无数据策略/去敏 | ❌ |

## E. 文件系统 / 工具滥用（FS & Tool Abuse）

| # | 防护类型 | 防了什么 | 没防什么 | 状态 |
|---|---------|---------|---------|------|
| 30 | 路径穿越（../../、绝对路径） | resolve_path 空字节 + is_relative_to(root) | Windows junction/UNC | 🟡 |
| 31 | 符号链接逃逸 | resolve 解析 symlink 校验 | Windows junction/.lnk | 🟡 |
| 32 | 写敏感/关键文件 | B3：.agent 关键文件已写保护 | .env/测试文件写保护待补 | 🟡 |
| 33 | 规则表自提权 | B3：permission-rules.json/settings.toml 已写保护 | 签名校验未做 | ✅ |
| 34 | 持久后门（.bashrc/memory 投毒） | B3：.agent/memory.md/rules.md 已写保护 + 路径限工作区 | 平台启动项待沙箱 | ✅ |
| 35 | 覆盖关键文件后伪装成功 | 监督 + 测试结构化 + eval verify | 产品层无强制验证门禁 | 🟡 |

---

## 现状汇总

- ✅ 已防：#3 #4 #7 #12 #14 #15 #17 #19 #20 #21 #22 #23 #24 #26 #33 #34
- 🟡 部分防：#1 #2 #5 #6 #8 #9 #10 #13 #16 #18 #25 #28 #30 #31 #32 #35
- ❌ 未防：#11 #27 #29
