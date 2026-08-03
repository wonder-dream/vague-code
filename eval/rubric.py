from __future__ import annotations

from dataclasses import dataclass, field


# ── 锚定 rubric（P1：每维度写清 1/3/5 分长什么样，不是干巴巴的 0-5） ────

@dataclass
class RubricDimension:
    name: str
    label: str
    description: str
    anchors: dict[int, str] = field(default_factory=dict)  # {1: ..., 3: ..., 5: ...}

    def to_prompt(self) -> str:
        lines = [f"- **{self.name}（{self.label}）**: {self.description}"]
        for score in sorted(self.anchors):
            lines.append(f"  - {score} 分: {self.anchors[score]}")
        return "\n".join(lines)


@dataclass
class Rubric:
    task_type: str
    dimensions: list[RubricDimension]
    scale_min: int = 0
    scale_max: int = 5

    def to_prompt(self) -> str:
        lines = [
            f"评分量表: {self.scale_min}-{self.scale_max}（整数）",
            "每个维度按锚定示例打分，不得给锚点之间的小数。",
            "",
        ]
        for d in self.dimensions:
            lines.append(d.to_prompt())
            lines.append("")
        return "\n".join(lines)


DEFAULT_FIX_RUBRIC = Rubric(
    task_type="fix_bug",
    dimensions=[
        RubricDimension(
            name="final_correctness",
            label="最终改动正确性",
            description="Agent 留下的 diff 是否真正解决任务问题。可结合 F2P/P2P 验收信号。",
            anchors={
                1: "diff 与问题无关或方向错误，F2P 必然挂，或根本没有 diff",
                3: "diff 触及正确位置但实现有缺陷，边缘情况未覆盖，或引入了新问题",
                5: "diff 精确修复根因，F2P/P2P 全过，无多余改动",
            },
        ),
        RubricDimension(
            name="root_cause",
            label="根因诊断准确性",
            description="Agent 是否正确定位了问题根源并基于此修改。",
            anchors={
                1: "完全误解问题，诊断方向错误，改动针对错误对象",
                3: "定位到相关代码区域，但根因判断部分错误或未解释机制",
                5: "精确指出根因，并能解释其机制（如代码/日志/复现证据支撑）",
            },
        ),
        RubricDimension(
            name="diff_quality",
            label="diff 质量与安全",
            description="改动是否引入新 bug、破坏既有功能、或使用危险操作。",
            anchors={
                1: "删除了既有功能/使用了危险命令/明显引入新 bug",
                3: "可工作但有重复代码、硬编码或与代码库风格不一致",
                5: "简洁、一致、无副作用，符合代码库既有风格",
            },
        ),
        RubricDimension(
            name="efficiency",
            label="过程效率",
            description="工具使用是否最小充分，是否有验证闭环（read→edit→test）。",
            anchors={
                1: "大量冗余探索、重复工具调用，远超过必要步数",
                3: "基本合理但存在多余步骤或未做验证",
                5: "最小步数达成，read→edit→test 闭环完整",
            },
        ),
    ],
)


DEFAULT_EXPLAIN_RUBRIC = Rubric(
    task_type="explain",
    dimensions=[
        RubricDimension(
            name="accuracy",
            label="准确性",
            description="解释是否与技术事实一致。",
            anchors={1: "存在事实性错误", 3: "基本正确但有次要偏差", 5: "完全准确"},
        ),
        RubricDimension(
            name="clarity",
            label="清晰度",
            description="是否结构清晰、可读、直达要点。",
            anchors={1: "混乱难懂", 3: "尚可但有冗余", 5: "精炼有条理"},
        ),
        RubricDimension(
            name="completeness",
            label="完整性",
            description="是否覆盖关键点。",
            anchors={1: "遗漏关键点", 3: "覆盖主体缺细节", 5: "关键点全覆盖"},
        ),
    ],
)


RUBRICS = {
    DEFAULT_FIX_RUBRIC.task_type: DEFAULT_FIX_RUBRIC,
    DEFAULT_EXPLAIN_RUBRIC.task_type: DEFAULT_EXPLAIN_RUBRIC,
}


def get_rubric(task_type: str | None = None) -> Rubric:
    return RUBRICS.get(task_type or "fix_bug", DEFAULT_FIX_RUBRIC)


JUDGE_SYSTEM_PREFIX = (
    "你是严格、无偏见的代码 Agent 评测员。你只根据给定的任务、Agent 轨迹与产出 diff 打分，"
    "按锚定示例评分，不得被输出长度或花哨措辞影响。最终必须只输出一个 JSON 对象，"
    "格式：{\"dimensions\": {\"<维度名>\": <0-5整数>, ...}, \"verdict\": \"pass\"|\"partial\"|\"fail\", "
    "\"justification\": \"<中文理由，1-3 句>\"}"
)
