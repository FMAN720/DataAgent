"""第 07～14 课的离线实验支架；业务逻辑仍使用 app 中的真实实现。"""
from __future__ import annotations

import asyncio
import copy
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger

from app.analytics import AnalyticsDatabase, GlossaryStore, build_analytics_tools
from app.core.tools.registry import ToolRegistry
from app.demo import build_demo_db
from app.infrastructure.llm.model_router import LLMResponse, ToolCall


def prepare_output():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logger.remove()


def reply(content="", *calls):
    return LLMResponse(content=content, model_id="course-fake", tool_calls=list(calls),
                       usage={"total_tokens": 10})


def tool_call(name, **arguments):
    return ToolCall(id=f"call_{name}", name=name,
                    arguments=json.dumps(arguments, ensure_ascii=False))


class ScriptedLLM:
    """只按剧本返回数据；函数项可检查输入，异常项可模拟故障。"""
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat(self, messages, **kwargs):
        snapshot = copy.deepcopy(messages)
        self.calls.append((snapshot, kwargs))
        if not self.script:
            raise AssertionError("剧本已用完，请检查模型调用顺序")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(snapshot, kwargs) if callable(item) else item


@contextmanager
def demo_resources(max_rows=200, timeout_seconds=5):
    """在临时目录生成固定种子的课程库，不读取 .env，不碰用户业务库。"""
    with tempfile.TemporaryDirectory(prefix="analyst_course_") as directory:
        path = Path(directory) / "ecommerce.db"
        build_demo_db(path)
        db = AnalyticsDatabase(f"sqlite:///{path.as_posix()}", max_rows=max_rows,
                               timeout_seconds=timeout_seconds)
        glossary = GlossaryStore.from_file(ROOT / "app/demo/glossary.md")
        registry = ToolRegistry()
        for tool in build_analytics_tools(db, glossary):
            registry.register(tool)
        try:
            yield db, glossary, registry
        finally:
            db.dispose()


def run(coro):
    return asyncio.run(coro)
