"""工具基类：统一 name、description、parameters 与 execute 接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class ToolParameter(BaseModel):
    """JSON Schema 风格的单个参数描述（简化）。"""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    # type 为 array 时的元素 schema，例如 {"type": "string"}
    items: dict[str, Any] | None = None
    enum: list[Any] | None = None


class ToolError(Exception):
    """工具可预期的失败（参数错误、SQL 报错等），消息会原样反馈给模型以便自我修正。"""


class BaseTool(ABC):
    """所有 Agent 工具的抽象基类。"""

    name: str = "base_tool"
    description: str = "基础工具"

    def __init__(self) -> None:
        self.parameters: list[ToolParameter] = []

    def schema_parameters(self) -> dict[str, Any]:
        """导出为 OpenAI tools 风格的 parameters 结构。"""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in self.parameters:
            prop: dict[str, Any] = {"type": p.type, "description": p.description}
            if p.items is not None:
                prop["items"] = p.items
            if p.enum is not None:
                prop["enum"] = p.enum
            properties[p.name] = prop
            if p.required:
                required.append(p.name)
        return {"type": "object", "properties": properties, "required": required}

    def to_openai_tool(self) -> dict[str, Any]:
        """导出为 Chat Completions ``tools`` 参数中的一项。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema_parameters(),
            },
        }

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """执行工具逻辑；子类实现具体行为。"""
