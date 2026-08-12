"""工具输出统一截断（对齐 PI truncate.ts / opencode truncate 的业界共识）。

行 + 字节双限，先到先胜；不截半行（整行裁剪）。结构化统计供 metadata 使用。
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024


@dataclass
class TruncationResult:
    content: str
    truncated: bool
    truncated_by: str | None  # "lines" | "bytes" | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    max_lines: int
    max_bytes: int


def truncate_output(
    text: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """按行限与字节限截断文本（整行裁剪，不截半行）。

    行限优先；字节限按 UTF-8 字节数逐行累积，超限时保留最后一个完整行。
    """
    total_bytes = len(text.encode("utf-8"))
    truncated_by: str | None = None

    lines = text.splitlines()
    total_lines = len(lines)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated_by = "lines"

    out_lines: list[str] = []
    acc_bytes = 0
    for line in lines:
        # 行字节 + 行间换行（保守：多算 1 字节保证 join 后不超限）
        line_bytes = len(line.encode("utf-8")) + 1
        if acc_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        acc_bytes += line_bytes
        out_lines.append(line)

    content = "\n".join(out_lines)
    output_lines = len(out_lines)
    output_bytes = len(content.encode("utf-8"))
    return TruncationResult(
        content=content,
        truncated=truncated_by is not None,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=output_lines,
        output_bytes=output_bytes,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )
