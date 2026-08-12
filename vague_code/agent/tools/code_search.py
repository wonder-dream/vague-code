"""code_search 工具（class-based）：基于 tree-sitter 符号索引，动态注入。

权限分类 = read（修复旧实现默认走 write 策略的缺陷）；资源 scope = EXACT READ。
"""

from __future__ import annotations

from vague_code.agent.tools.base import OpType, ScopeType, Tool

MAX_GREP_RESULTS = 500


class CodeSearchTool(Tool):
    name = "code_search"
    description = ("在工作区代码库中按符号名（函数/类/方法）搜索定义位置。"
                   "返回 file:line: signature 列表。当需要定位某个函数或类在哪里定义时使用。")
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要搜索的符号名或正则表达式"},
            "path": {"type": "string", "description": "过滤文件路径（可选）"},
            "k": {"type": "integer", "description": "最大返回条数（1-50，默认 20）"},
        },
        "required": ["query"],
    }
    permission = "read"
    op_type = OpType.READ
    scope_type = ScopeType.EXACT
    MAX_SIGNATURE_LENGTH = 200

    def __init__(self, workdir: str, repo_index):
        super().__init__(workdir)
        self._repo_index = repo_index

    def scope_path(self, input: dict) -> str:
        return input.get("path") or ""

    def run(self, input: dict) -> str:
        query = input.get("query", "")
        if not query:
            return "需要提供搜索查询内容。"
        path = input.get("path") or None
        k = max(1, min(int(input.get("k", 20) or 20), 50))
        results = self._repo_index.search(query, k=k, path=path)
        if not results:
            return f"未找到与 {query!r} 匹配的符号。"
        lines = []
        for s in results:
            sig = s.signature
            if len(sig) > self.MAX_SIGNATURE_LENGTH:
                sig = sig[: self.MAX_SIGNATURE_LENGTH] + "..."
            lines.append(f"{s.file}:{s.line}: {sig}")
        if len(lines) > MAX_GREP_RESULTS:
            lines = lines[:MAX_GREP_RESULTS]
            lines.append(f"... 已显示 {MAX_GREP_RESULTS} 条结果，输出已截断")
        return "\n".join(lines)
