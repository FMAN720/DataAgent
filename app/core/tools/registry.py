"""工具注册中心：集中管理可用工具，导出 function calling schema 并统一执行。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.core.tools.base import BaseTool, ToolError


@dataclass
class ToolOutcome:
    """一次工具调用的结果；content 是回传给模型的字符串。"""

    ok: bool
    content: str
    duration_ms: int


def _to_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[输出过长，已截断，原长度 {len(text)} 字符]"


class ToolRegistry:
    """工具注册中心：管理所有可用工具。"""

    def __init__(self, timeout_seconds: float = 30.0, max_output_chars: int = 8000) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._timeout = timeout_seconds
        self._max_output_chars = max_output_chars

    def register(self, tool: BaseTool) -> None:
        """注册工具；同名覆盖并记录日志。"""
        if tool.name in self._tools:
            logger.warning("工具 [{}] 已存在，将被覆盖", tool.name)
        self._tools[tool.name] = tool
        logger.info("已注册工具: {}", tool.name)

    def get_tool(self, name: str) -> BaseTool:
        """按名称获取工具。"""
        if name not in self._tools:
            raise KeyError(f"未注册的工具: {name}")
        return self._tools[name]

    def get_all_tools(self) -> list[BaseTool]:
        """返回全部工具列表。"""
        return list(self._tools.values())

    def list_tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def to_openai_tools(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        """导出 function calling 的 tools 定义；names 为 None 时导出全部。"""
        tools = (
            self._tools.values()
            if names is None
            else [self._tools[n] for n in names if n in self._tools]
        )
        return [t.to_openai_tool() for t in tools]

    def get_tools_description(self) -> str:
        """生成所有工具的自然语言描述（用于不支持 function calling 的模型）。"""
        lines: list[str] = []
        for t in self._tools.values():
            params = ", ".join(f"{p.name}: {p.type}" for p in t.parameters) or "无"
            lines.append(f"- {t.name}: {t.description}（参数: {params}）")
        return "\n".join(lines) if lines else "（当前无可用工具）"

    async def invoke(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        """
        执行工具并把结果统一为字符串。

        所有失败（未知工具、缺参数、超时、执行异常）都转成 ok=False 的结果而不是抛出，
        这样 Agent 循环可以把错误交给模型，让它决定修正参数还是换方法。
        """
        start = time.perf_counter()

        def done(ok: bool, content: str) -> ToolOutcome:
            elapsed = int((time.perf_counter() - start) * 1000)
            return ToolOutcome(
                ok=ok, content=_truncate(content, self._max_output_chars), duration_ms=elapsed
            )

        tool = self._tools.get(name)
        if tool is None:
            return done(
                False,
                _to_text(
                    {
                        "error": f"未知工具: {name}",
                        "available_tools": self.list_tool_names(),
                    }
                ),
            )

        missing = [p.name for p in tool.parameters if p.required and p.name not in arguments]
        if missing:
            return done(False, _to_text({"error": f"缺少必填参数: {', '.join(missing)}"}))

        try:
            result = await asyncio.wait_for(tool.execute(**arguments), timeout=self._timeout)
        except TimeoutError:
            logger.warning("工具 [{}] 超时（{}s）", name, self._timeout)
            return done(False, _to_text({"error": f"工具执行超时（{self._timeout:.0f} 秒）"}))
        except (ToolError, ValueError, TypeError) as exc:
            # 可预期的失败：参数不对、SQL 报错等，交给模型修正
            return done(False, _to_text({"error": str(exc)}))
        except Exception as exc:
            logger.exception("工具 [{}] 执行异常", name)
            return done(False, _to_text({"error": f"工具内部错误: {type(exc).__name__}: {exc}"}))

        return done(True, _to_text(result))
