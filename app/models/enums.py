"""应用级枚举定义（Agent 模式与审查状态）。"""

from enum import Enum


class AgentMode(str, Enum):
    """Agent 推理模式。"""

    REACT = "react"
    PLAN_EXECUTE = "plan_execute"


class ReflectionStatus(str, Enum):
    """答案审查与答案生成成功是两个独立状态。"""

    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"
