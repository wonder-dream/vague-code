# XClaw Ablation Results

| Compression | Concurrency | Pass Rate | Avg Turns | Avg Tokens | Reclaimed |
|------------|-------------|-----------|-----------|------------|-----------|
| ✗ | ✗ | 25/30 (83%) | 20.8 | 635,446 | 0K |
| ✗ | ✓ | 28/30 (93%) | 20.3 | 614,753 | 0K |
| ✓ | ✗ | 23/30 (76%) | 22.2 | 735,471 | 8K |
| ✓ | ✓ | 22/30 (73%) | 21.6 | 759,153 | 5K |

## Baseline (no compression, 30 tasks)
- Pass rate: 18/30 (60%)
- Avg tokens: 931,372
- Avg turns: 23.5
- Total cost: ~¥1.40 (estimated)