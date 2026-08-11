# 消融实验结果

总任务数: 225
总运行次数: 225
总成本: $9.9963（按评测时 cli 单价估）

## 汇总

| 压缩 | 并发 | RepoMap | 重复 | 通过率 | 平均轮次 | 平均 input tokens | code_search | stale回收 | micro回收 | ssnip回收 | auto回收 | truncate回收 | 成本($) |
|------|------|---------|------|--------|----------|-------------------|-------------|-----------|-----------|-----------|----------|--------------|---------|
| ✓ | ✓ | ✓ | 0 | 100% | 10.5 | 124,564 | 0 | 0 | 0 | 0 | 0 | 0 | $10.00 |
## 指标口径（ADR-0040）

| 指标 | 数值 | 分母 | 含义 |
|------|------|------|------|
| pass@1 | 100.00% | 224 题（有明确判分） | 模型代码能力口径 |
| e2e mean | 99.56% | 225 题（全题） | 整条链路成功率（异常按 0） |

## 失败分类分账（互斥分类学）

| 类别 | 数量 | 占比 | 归因 |
|------|------|------|------|
| 成功（verified） | 224 | 100% | 模型能力（通过） |
| 环境坏（sanity gate/依赖） | 1 | 0% | 环境（确定性剔除，不进能力分母） |

## 成本与 token 统计（per run 分位）

| 指标 | p50 | p90 | max |
|------|-----|-----|-----|
| input tokens | 74,926 | 222,311 | 1,238,346 |
| output tokens | 6,257 | 16,705 | 57,709 |
| cache-hit tokens | 69,248 | 217,856 | 1,212,928 |
| cost (USD) | 0.0283 | 0.0850 | 0.3834 |

## 声明

> 以上分数为**本地运行点估计**：任务集为本地子集、agent 为本项目实现、交互协议与异常计分与官方榜单不同，不能与任何官方 leaderboard 分数对比或排名。env_broken/infra 类与模型能力失败严格分账。

## 逐任务细节

| 任务ID | 配置 | 通过 | verified | 判定 | 轮次 | input tokens | run_end_reason |
|--------|------|------|----------|------|------|--------------|----------------|
| cpp/all-your-base | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 39,405 | end_turn |
| cpp/allergies | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 137,137 | end_turn |
| cpp/bank-account | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 55,800 | end_turn |
| cpp/binary-search-tree | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 26 | 331,385 | end_turn |
| cpp/circular-buffer | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 117,314 | end_turn |
| cpp/clock | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 93,257 | end_turn |
| cpp/complex-numbers | compression=1_concurrency=1_repo_map=1 | ? | - | dataset_defect | 11 | 111,129 | end_turn |
| cpp/crypto-square | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 78,212 | end_turn |
| cpp/diamond | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 54,474 | end_turn |
| cpp/dnd-character | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 158,378 | end_turn |
| cpp/gigasecond | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 18 | 125,973 | end_turn |
| cpp/grade-school | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 71,715 | end_turn |
| cpp/kindergarten-garden | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 66,274 | end_turn |
| cpp/knapsack | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 67,524 | end_turn |
| cpp/linked-list | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 19 | 178,341 | end_turn |
| cpp/meetup | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 19 | 755,837 | end_turn |
| cpp/parallel-letter-frequency | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 174,797 | end_turn |
| cpp/perfect-numbers | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 67,709 | end_turn |
| cpp/phone-number | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 65,073 | end_turn |
| cpp/queen-attack | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 78,565 | end_turn |
| cpp/robot-name | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 89,008 | end_turn |
| cpp/space-age | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 39,108 | end_turn |
| cpp/spiral-matrix | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 54,393 | end_turn |
| cpp/sublist | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 104,861 | end_turn |
| cpp/yacht | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 145,979 | end_turn |
| cpp/zebra-puzzle | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 15 | 222,311 | end_turn |
| go/alphametics | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 65,073 | end_turn |
| go/beer-song | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 60,800 | end_turn |
| go/book-store | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 7 | 40,573 | end_turn |
| go/bottle-song | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 29,283 | end_turn |
| go/bowling | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 52,373 | end_turn |
| go/connect | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 57,930 | end_turn |
| go/counter | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 112,088 | end_turn |
| go/crypto-square | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 39,227 | end_turn |
| go/dnd-character | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 21,504 | end_turn |
| go/dominoes | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 43,211 | end_turn |
| go/error-handling | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 38,530 | end_turn |
| go/food-chain | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 26,309 | end_turn |
| go/forth | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 76,358 | end_turn |
| go/hexadecimal | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 17,614 | end_turn |
| go/kindergarten-garden | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 46,550 | end_turn |
| go/ledger | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 213,011 | end_turn |
| go/markdown | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 7 | 72,518 | end_turn |
| go/matrix | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 26,658 | end_turn |
| go/octal | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 15,799 | end_turn |
| go/paasio | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 20,738 | end_turn |
| go/palindrome-products | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 55,066 | end_turn |
| go/pig-latin | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 31,655 | end_turn |
| go/poker | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 111,926 | end_turn |
| go/pov | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 76,246 | end_turn |
| go/protein-translation | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 24,712 | end_turn |
| go/react | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 69,254 | end_turn |
| go/robot-simulator | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 212,372 | end_turn |
| go/say | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 42,006 | end_turn |
| go/scale-generator | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 7 | 47,876 | end_turn |
| go/simple-linked-list | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 34,372 | end_turn |
| go/sublist | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 22,830 | end_turn |
| go/transpose | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 113,472 | end_turn |
| go/tree-building | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 7 | 42,676 | end_turn |
| go/trinary | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 18,836 | end_turn |
| go/two-bucket | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 7 | 75,919 | end_turn |
| go/variable-length-quantity | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 17,925 | end_turn |
| go/word-search | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 35,432 | end_turn |
| go/wordy | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 7 | 30,600 | end_turn |
| go/zebra-puzzle | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 32,881 | end_turn |
| java/affine-cipher | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 18 | 198,005 | end_turn |
| java/all-your-base | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 42,176 | end_turn |
| java/alphametics | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 251,893 | end_turn |
| java/bank-account | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 60,311 | end_turn |
| java/book-store | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 87,140 | end_turn |
| java/bottle-song | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 67,137 | end_turn |
| java/bowling | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 143,794 | end_turn |
| java/change | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 57,440 | end_turn |
| java/circular-buffer | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 54,041 | end_turn |
| java/connect | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 157,224 | end_turn |
| java/custom-set | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 66,980 | end_turn |
| java/dominoes | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 134,632 | end_turn |
| java/food-chain | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 63,596 | end_turn |
| java/forth | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 194,116 | end_turn |
| java/go-counting | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 68,941 | end_turn |
| java/hangman | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 177,460 | end_turn |
| java/house | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 133,123 | end_turn |
| java/kindergarten-garden | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 50,692 | end_turn |
| java/ledger | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 23 | 474,211 | end_turn |
| java/mazy-mice | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 21 | 1,142,509 | end_turn |
| java/ocr-numbers | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 58,094 | end_turn |
| java/palindrome-products | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 66,463 | end_turn |
| java/phone-number | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 52,444 | end_turn |
| java/pig-latin | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 76,434 | end_turn |
| java/poker | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 74,345 | end_turn |
| java/pov | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 133,999 | end_turn |
| java/protein-translation | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 62,659 | end_turn |
| java/pythagorean-triplet | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 87,351 | end_turn |
| java/queen-attack | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 52,844 | end_turn |
| java/rational-numbers | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 17 | 279,113 | end_turn |
| java/react | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 110,397 | end_turn |
| java/resistor-color-trio | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 74,926 | end_turn |
| java/rest-api | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 263,347 | end_turn |
| java/satellite | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 55,533 | end_turn |
| java/series | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 14,404 | end_turn |
| java/sgf-parsing | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 148,215 | end_turn |
| java/simple-linked-list | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 42,346 | end_turn |
| java/state-of-tic-tac-toe | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 94,178 | end_turn |
| java/transpose | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 131,171 | end_turn |
| java/tree-building | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 210,349 | end_turn |
| java/twelve-days | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 19 | 197,624 | end_turn |
| java/two-bucket | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 95,912 | end_turn |
| java/variable-length-quantity | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 54,365 | end_turn |
| java/word-search | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 18 | 385,567 | end_turn |
| java/wordy | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 46,418 | end_turn |
| java/zebra-puzzle | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 20 | 265,973 | end_turn |
| java/zipper | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 145,812 | end_turn |
| javascript/affine-cipher | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 14 | 168,977 | end_turn |
| javascript/alphametics | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 26 | 547,236 | end_turn |
| javascript/beer-song | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 161,383 | end_turn |
| javascript/binary | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 56,751 | end_turn |
| javascript/book-store | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 105,255 | end_turn |
| javascript/bottle-song | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 19 | 162,860 | end_turn |
| javascript/bowling | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 25 | 391,448 | end_turn |
| javascript/complex-numbers | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 29 | 507,584 | end_turn |
| javascript/connect | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 14 | 202,306 | end_turn |
| javascript/food-chain | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 15 | 132,592 | end_turn |
| javascript/forth | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 122,430 | end_turn |
| javascript/go-counting | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 115,066 | end_turn |
| javascript/grade-school | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 38 | 1,238,346 | end_turn |
| javascript/grep | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 14 | 155,114 | end_turn |
| javascript/house | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 70,113 | end_turn |
| javascript/killer-sudoku-helper | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 65,438 | end_turn |
| javascript/ledger | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 17 | 178,178 | end_turn |
| javascript/list-ops | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 15 | 122,444 | end_turn |
| javascript/meetup | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 15 | 167,523 | end_turn |
| javascript/ocr-numbers | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 68,208 | end_turn |
| javascript/palindrome-products | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 39 | 737,232 | end_turn |
| javascript/parallel-letter-frequency | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 111,451 | end_turn |
| javascript/phone-number | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 60,519 | end_turn |
| javascript/pig-latin | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 84,533 | end_turn |
| javascript/poker | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 126,625 | end_turn |
| javascript/promises | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 112,904 | end_turn |
| javascript/queen-attack | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 56,841 | end_turn |
| javascript/rational-numbers | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 17 | 238,007 | end_turn |
| javascript/react | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 103,421 | end_turn |
| javascript/rectangles | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 114,820 | end_turn |
| javascript/resistor-color-trio | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 77,800 | end_turn |
| javascript/rest-api | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 127,185 | end_turn |
| javascript/robot-name | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 87,073 | end_turn |
| javascript/say | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 14 | 95,235 | end_turn |
| javascript/scale-generator | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 139,944 | end_turn |
| javascript/simple-linked-list | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 95,079 | end_turn |
| javascript/space-age | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 52,063 | end_turn |
| javascript/state-of-tic-tac-toe | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 17 | 390,560 | end_turn |
| javascript/sum-of-multiples | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 69,159 | end_turn |
| javascript/tournament | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 85,898 | end_turn |
| javascript/transpose | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 15 | 219,292 | end_turn |
| javascript/triangle | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 31,851 | end_turn |
| javascript/twelve-days | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 92,885 | end_turn |
| javascript/two-bucket | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 262,508 | end_turn |
| javascript/variable-length-quantity | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 83,276 | end_turn |
| javascript/word-search | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 92,511 | end_turn |
| javascript/wordy | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 98,147 | end_turn |
| javascript/zebra-puzzle | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 83,999 | end_turn |
| javascript/zipper | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 82,258 | end_turn |
| python/affine-cipher | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 19,140 | end_turn |
| python/beer-song | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 39,216 | end_turn |
| python/book-store | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 21,590 | end_turn |
| python/bottle-song | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 7 | 32,917 | end_turn |
| python/bowling | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 109,284 | end_turn |
| python/connect | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 179,092 | end_turn |
| python/dominoes | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 14 | 159,180 | end_turn |
| python/dot-dsl | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 4 | 16,790 | end_turn |
| python/food-chain | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 21,920 | end_turn |
| python/forth | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 40,604 | end_turn |
| python/go-counting | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 20,901 | end_turn |
| python/grade-school | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 16,929 | end_turn |
| python/grep | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 26,114 | end_turn |
| python/hangman | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 20,279 | end_turn |
| python/list-ops | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 17,917 | end_turn |
| python/paasio | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 93,888 | end_turn |
| python/phone-number | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 21,345 | end_turn |
| python/pig-latin | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 22,584 | end_turn |
| python/poker | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 25,712 | end_turn |
| python/pov | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 27,362 | end_turn |
| python/proverb | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 13,140 | end_turn |
| python/react | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 81,147 | end_turn |
| python/rest-api | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 23,575 | end_turn |
| python/robot-name | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 12,427 | end_turn |
| python/scale-generator | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 27,560 | end_turn |
| python/sgf-parsing | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 59,389 | end_turn |
| python/simple-linked-list | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 24,670 | end_turn |
| python/transpose | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 33,955 | end_turn |
| python/tree-building | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 23,787 | end_turn |
| python/two-bucket | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 24,008 | end_turn |
| python/variable-length-quantity | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 19,080 | end_turn |
| python/wordy | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 66,233 | end_turn |
| python/zebra-puzzle | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 6 | 23,926 | end_turn |
| python/zipper | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 5 | 22,803 | end_turn |
| rust/accumulate | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 24 | 484,212 | end_turn |
| rust/acronym | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 38,944 | end_turn |
| rust/alphametics | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 233,113 | end_turn |
| rust/book-store | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 86,216 | end_turn |
| rust/bowling | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 149,410 | end_turn |
| rust/decimal | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 374,516 | end_turn |
| rust/dot-dsl | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 68,800 | end_turn |
| rust/doubly-linked-list | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 24 | 1,022,651 | end_turn |
| rust/fizzy | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 100,308 | end_turn |
| rust/forth | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 183,526 | end_turn |
| rust/gigasecond | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 21,956 | end_turn |
| rust/grade-school | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 27,598 | end_turn |
| rust/grep | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 66,797 | end_turn |
| rust/luhn-from | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 90,003 | end_turn |
| rust/macros | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 127,953 | end_turn |
| rust/nucleotide-codons | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 10 | 114,924 | end_turn |
| rust/ocr-numbers | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 75,481 | end_turn |
| rust/parallel-letter-frequency | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 62,345 | end_turn |
| rust/pig-latin | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 55,165 | end_turn |
| rust/poker | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 78,994 | end_turn |
| rust/react | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 13 | 241,802 | end_turn |
| rust/robot-name | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 11 | 74,219 | end_turn |
| rust/say | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 8 | 39,951 | end_turn |
| rust/scale-generator | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 86,788 | end_turn |
| rust/simple-cipher | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 9 | 53,094 | end_turn |
| rust/two-bucket | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 7 | 43,895 | end_turn |
| rust/variable-length-quantity | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 7 | 36,790 | end_turn |
| rust/word-count | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 7 | 31,437 | end_turn |
| rust/wordy | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 12 | 166,936 | end_turn |
| rust/xorcism | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | verify:pass | 29 | 1,086,447 | end_turn |

## pass^k 可靠性（τ-bench：k 次全过才计过）

| 配置 | k | 全过任务数 | 任务总数 | pass^k | pass@k（≥1 次过，Aider 口径） |
|------|---|------------|----------|--------|-------------------------------|
| compression=1_concurrency=1_repo_map=1 | 1 | 224 | 224 | 100% | 100%（224/224） |

整体 pass^k: 224/224 = 100%

## 逐题胜负表（单变量 on/off，pass^k 粒度）


### compression 开 vs 关（concurrency=True, repo_map=True）

- 开过/关不过: 224 题 → cpp/all-your-base, cpp/allergies, cpp/bank-account, cpp/binary-search-tree, cpp/circular-buffer, cpp/clock, cpp/crypto-square, cpp/diamond, cpp/dnd-character, cpp/gigasecond, cpp/grade-school, cpp/kindergarten-garden, cpp/knapsack, cpp/linked-list, cpp/meetup, cpp/parallel-letter-frequency, cpp/perfect-numbers, cpp/phone-number, cpp/queen-attack, cpp/robot-name, cpp/space-age, cpp/spiral-matrix, cpp/sublist, cpp/yacht, cpp/zebra-puzzle, go/alphametics, go/beer-song, go/book-store, go/bottle-song, go/bowling, go/connect, go/counter, go/crypto-square, go/dnd-character, go/dominoes, go/error-handling, go/food-chain, go/forth, go/hexadecimal, go/kindergarten-garden, go/ledger, go/markdown, go/matrix, go/octal, go/paasio, go/palindrome-products, go/pig-latin, go/poker, go/pov, go/protein-translation, go/react, go/robot-simulator, go/say, go/scale-generator, go/simple-linked-list, go/sublist, go/transpose, go/tree-building, go/trinary, go/two-bucket, go/variable-length-quantity, go/word-search, go/wordy, go/zebra-puzzle, java/affine-cipher, java/all-your-base, java/alphametics, java/bank-account, java/book-store, java/bottle-song, java/bowling, java/change, java/circular-buffer, java/connect, java/custom-set, java/dominoes, java/food-chain, java/forth, java/go-counting, java/hangman, java/house, java/kindergarten-garden, java/ledger, java/mazy-mice, java/ocr-numbers, java/palindrome-products, java/phone-number, java/pig-latin, java/poker, java/pov, java/protein-translation, java/pythagorean-triplet, java/queen-attack, java/rational-numbers, java/react, java/resistor-color-trio, java/rest-api, java/satellite, java/series, java/sgf-parsing, java/simple-linked-list, java/state-of-tic-tac-toe, java/transpose, java/tree-building, java/twelve-days, java/two-bucket, java/variable-length-quantity, java/word-search, java/wordy, java/zebra-puzzle, java/zipper, javascript/affine-cipher, javascript/alphametics, javascript/beer-song, javascript/binary, javascript/book-store, javascript/bottle-song, javascript/bowling, javascript/complex-numbers, javascript/connect, javascript/food-chain, javascript/forth, javascript/go-counting, javascript/grade-school, javascript/grep, javascript/house, javascript/killer-sudoku-helper, javascript/ledger, javascript/list-ops, javascript/meetup, javascript/ocr-numbers, javascript/palindrome-products, javascript/parallel-letter-frequency, javascript/phone-number, javascript/pig-latin, javascript/poker, javascript/promises, javascript/queen-attack, javascript/rational-numbers, javascript/react, javascript/rectangles, javascript/resistor-color-trio, javascript/rest-api, javascript/robot-name, javascript/say, javascript/scale-generator, javascript/simple-linked-list, javascript/space-age, javascript/state-of-tic-tac-toe, javascript/sum-of-multiples, javascript/tournament, javascript/transpose, javascript/triangle, javascript/twelve-days, javascript/two-bucket, javascript/variable-length-quantity, javascript/word-search, javascript/wordy, javascript/zebra-puzzle, javascript/zipper, python/affine-cipher, python/beer-song, python/book-store, python/bottle-song, python/bowling, python/connect, python/dominoes, python/dot-dsl, python/food-chain, python/forth, python/go-counting, python/grade-school, python/grep, python/hangman, python/list-ops, python/paasio, python/phone-number, python/pig-latin, python/poker, python/pov, python/proverb, python/react, python/rest-api, python/robot-name, python/scale-generator, python/sgf-parsing, python/simple-linked-list, python/transpose, python/tree-building, python/two-bucket, python/variable-length-quantity, python/wordy, python/zebra-puzzle, python/zipper, rust/accumulate, rust/acronym, rust/alphametics, rust/book-store, rust/bowling, rust/decimal, rust/dot-dsl, rust/doubly-linked-list, rust/fizzy, rust/forth, rust/gigasecond, rust/grade-school, rust/grep, rust/luhn-from, rust/macros, rust/nucleotide-codons, rust/ocr-numbers, rust/parallel-letter-frequency, rust/pig-latin, rust/poker, rust/react, rust/robot-name, rust/say, rust/scale-generator, rust/simple-cipher, rust/two-bucket, rust/variable-length-quantity, rust/word-count, rust/wordy, rust/xorcism
- 关过/开不过: 0 题 → -

### concurrency 开 vs 关（compression=True, repo_map=True）

- 开过/关不过: 224 题 → cpp/all-your-base, cpp/allergies, cpp/bank-account, cpp/binary-search-tree, cpp/circular-buffer, cpp/clock, cpp/crypto-square, cpp/diamond, cpp/dnd-character, cpp/gigasecond, cpp/grade-school, cpp/kindergarten-garden, cpp/knapsack, cpp/linked-list, cpp/meetup, cpp/parallel-letter-frequency, cpp/perfect-numbers, cpp/phone-number, cpp/queen-attack, cpp/robot-name, cpp/space-age, cpp/spiral-matrix, cpp/sublist, cpp/yacht, cpp/zebra-puzzle, go/alphametics, go/beer-song, go/book-store, go/bottle-song, go/bowling, go/connect, go/counter, go/crypto-square, go/dnd-character, go/dominoes, go/error-handling, go/food-chain, go/forth, go/hexadecimal, go/kindergarten-garden, go/ledger, go/markdown, go/matrix, go/octal, go/paasio, go/palindrome-products, go/pig-latin, go/poker, go/pov, go/protein-translation, go/react, go/robot-simulator, go/say, go/scale-generator, go/simple-linked-list, go/sublist, go/transpose, go/tree-building, go/trinary, go/two-bucket, go/variable-length-quantity, go/word-search, go/wordy, go/zebra-puzzle, java/affine-cipher, java/all-your-base, java/alphametics, java/bank-account, java/book-store, java/bottle-song, java/bowling, java/change, java/circular-buffer, java/connect, java/custom-set, java/dominoes, java/food-chain, java/forth, java/go-counting, java/hangman, java/house, java/kindergarten-garden, java/ledger, java/mazy-mice, java/ocr-numbers, java/palindrome-products, java/phone-number, java/pig-latin, java/poker, java/pov, java/protein-translation, java/pythagorean-triplet, java/queen-attack, java/rational-numbers, java/react, java/resistor-color-trio, java/rest-api, java/satellite, java/series, java/sgf-parsing, java/simple-linked-list, java/state-of-tic-tac-toe, java/transpose, java/tree-building, java/twelve-days, java/two-bucket, java/variable-length-quantity, java/word-search, java/wordy, java/zebra-puzzle, java/zipper, javascript/affine-cipher, javascript/alphametics, javascript/beer-song, javascript/binary, javascript/book-store, javascript/bottle-song, javascript/bowling, javascript/complex-numbers, javascript/connect, javascript/food-chain, javascript/forth, javascript/go-counting, javascript/grade-school, javascript/grep, javascript/house, javascript/killer-sudoku-helper, javascript/ledger, javascript/list-ops, javascript/meetup, javascript/ocr-numbers, javascript/palindrome-products, javascript/parallel-letter-frequency, javascript/phone-number, javascript/pig-latin, javascript/poker, javascript/promises, javascript/queen-attack, javascript/rational-numbers, javascript/react, javascript/rectangles, javascript/resistor-color-trio, javascript/rest-api, javascript/robot-name, javascript/say, javascript/scale-generator, javascript/simple-linked-list, javascript/space-age, javascript/state-of-tic-tac-toe, javascript/sum-of-multiples, javascript/tournament, javascript/transpose, javascript/triangle, javascript/twelve-days, javascript/two-bucket, javascript/variable-length-quantity, javascript/word-search, javascript/wordy, javascript/zebra-puzzle, javascript/zipper, python/affine-cipher, python/beer-song, python/book-store, python/bottle-song, python/bowling, python/connect, python/dominoes, python/dot-dsl, python/food-chain, python/forth, python/go-counting, python/grade-school, python/grep, python/hangman, python/list-ops, python/paasio, python/phone-number, python/pig-latin, python/poker, python/pov, python/proverb, python/react, python/rest-api, python/robot-name, python/scale-generator, python/sgf-parsing, python/simple-linked-list, python/transpose, python/tree-building, python/two-bucket, python/variable-length-quantity, python/wordy, python/zebra-puzzle, python/zipper, rust/accumulate, rust/acronym, rust/alphametics, rust/book-store, rust/bowling, rust/decimal, rust/dot-dsl, rust/doubly-linked-list, rust/fizzy, rust/forth, rust/gigasecond, rust/grade-school, rust/grep, rust/luhn-from, rust/macros, rust/nucleotide-codons, rust/ocr-numbers, rust/parallel-letter-frequency, rust/pig-latin, rust/poker, rust/react, rust/robot-name, rust/say, rust/scale-generator, rust/simple-cipher, rust/two-bucket, rust/variable-length-quantity, rust/word-count, rust/wordy, rust/xorcism
- 关过/开不过: 0 题 → -

### repo_map 开 vs 关（compression=True, concurrency=True）

- 开过/关不过: 224 题 → cpp/all-your-base, cpp/allergies, cpp/bank-account, cpp/binary-search-tree, cpp/circular-buffer, cpp/clock, cpp/crypto-square, cpp/diamond, cpp/dnd-character, cpp/gigasecond, cpp/grade-school, cpp/kindergarten-garden, cpp/knapsack, cpp/linked-list, cpp/meetup, cpp/parallel-letter-frequency, cpp/perfect-numbers, cpp/phone-number, cpp/queen-attack, cpp/robot-name, cpp/space-age, cpp/spiral-matrix, cpp/sublist, cpp/yacht, cpp/zebra-puzzle, go/alphametics, go/beer-song, go/book-store, go/bottle-song, go/bowling, go/connect, go/counter, go/crypto-square, go/dnd-character, go/dominoes, go/error-handling, go/food-chain, go/forth, go/hexadecimal, go/kindergarten-garden, go/ledger, go/markdown, go/matrix, go/octal, go/paasio, go/palindrome-products, go/pig-latin, go/poker, go/pov, go/protein-translation, go/react, go/robot-simulator, go/say, go/scale-generator, go/simple-linked-list, go/sublist, go/transpose, go/tree-building, go/trinary, go/two-bucket, go/variable-length-quantity, go/word-search, go/wordy, go/zebra-puzzle, java/affine-cipher, java/all-your-base, java/alphametics, java/bank-account, java/book-store, java/bottle-song, java/bowling, java/change, java/circular-buffer, java/connect, java/custom-set, java/dominoes, java/food-chain, java/forth, java/go-counting, java/hangman, java/house, java/kindergarten-garden, java/ledger, java/mazy-mice, java/ocr-numbers, java/palindrome-products, java/phone-number, java/pig-latin, java/poker, java/pov, java/protein-translation, java/pythagorean-triplet, java/queen-attack, java/rational-numbers, java/react, java/resistor-color-trio, java/rest-api, java/satellite, java/series, java/sgf-parsing, java/simple-linked-list, java/state-of-tic-tac-toe, java/transpose, java/tree-building, java/twelve-days, java/two-bucket, java/variable-length-quantity, java/word-search, java/wordy, java/zebra-puzzle, java/zipper, javascript/affine-cipher, javascript/alphametics, javascript/beer-song, javascript/binary, javascript/book-store, javascript/bottle-song, javascript/bowling, javascript/complex-numbers, javascript/connect, javascript/food-chain, javascript/forth, javascript/go-counting, javascript/grade-school, javascript/grep, javascript/house, javascript/killer-sudoku-helper, javascript/ledger, javascript/list-ops, javascript/meetup, javascript/ocr-numbers, javascript/palindrome-products, javascript/parallel-letter-frequency, javascript/phone-number, javascript/pig-latin, javascript/poker, javascript/promises, javascript/queen-attack, javascript/rational-numbers, javascript/react, javascript/rectangles, javascript/resistor-color-trio, javascript/rest-api, javascript/robot-name, javascript/say, javascript/scale-generator, javascript/simple-linked-list, javascript/space-age, javascript/state-of-tic-tac-toe, javascript/sum-of-multiples, javascript/tournament, javascript/transpose, javascript/triangle, javascript/twelve-days, javascript/two-bucket, javascript/variable-length-quantity, javascript/word-search, javascript/wordy, javascript/zebra-puzzle, javascript/zipper, python/affine-cipher, python/beer-song, python/book-store, python/bottle-song, python/bowling, python/connect, python/dominoes, python/dot-dsl, python/food-chain, python/forth, python/go-counting, python/grade-school, python/grep, python/hangman, python/list-ops, python/paasio, python/phone-number, python/pig-latin, python/poker, python/pov, python/proverb, python/react, python/rest-api, python/robot-name, python/scale-generator, python/sgf-parsing, python/simple-linked-list, python/transpose, python/tree-building, python/two-bucket, python/variable-length-quantity, python/wordy, python/zebra-puzzle, python/zipper, rust/accumulate, rust/acronym, rust/alphametics, rust/book-store, rust/bowling, rust/decimal, rust/dot-dsl, rust/doubly-linked-list, rust/fizzy, rust/forth, rust/gigasecond, rust/grade-school, rust/grep, rust/luhn-from, rust/macros, rust/nucleotide-codons, rust/ocr-numbers, rust/parallel-letter-frequency, rust/pig-latin, rust/poker, rust/react, rust/robot-name, rust/say, rust/scale-generator, rust/simple-cipher, rust/two-bucket, rust/variable-length-quantity, rust/word-count, rust/wordy, rust/xorcism
- 关过/开不过: 0 题 → -