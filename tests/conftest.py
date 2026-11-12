"""测试公共夹具：演示库、工具注册表、可编排脚本的假 LLM。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.analytics import AnalyticsDatabase, GlossaryStore, build_analytics_tools
from app.core.tools.registry import ToolRegistry
from app.demo import build_demo_db
from app.infrastructure.llm.model_router import LLMResponse, ToolCall

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GLOSSARY = PROJECT_ROOT / "app" / "demo" / "glossary.md"

_previous_environment = {}


def pytest_configure(config):
    # Process-local only: keep the user's persistent BGE configuration enabled.
    for key, value in {
        "EMBEDDING_ENABLED": "false",
        "DOCUMENTS_ENABLED": "false",
        "DATASETS_ENABLED": "false",
        "OPENAI_API_KEY": "",
        "SESSION_BACKEND": "memory",
        "ANALYSIS_HISTORY_ENABLED": "false",
        "VISION_ENABLED": "false",
        "VISION_API_KEY": "",
        "LOCAL_MODEL_SETTINGS_PATH": str(
            PROJECT_ROOT / ".pytest_tmp_model_settings" / f"unused-{uuid.uuid4().hex}.json"
        ),
        "LOCAL_WORKSPACE_PATH": str(
            PROJECT_ROOT / ".pytest_tmp_workspace" / f"unused-{uuid.uuid4().hex}.sqlite3"
        ),
    }.items():
        _previous_environment[key] = os.environ.get(key)
        os.environ[key] = value
    from app.config import get_settings

    get_settings.cache_clear()


def pytest_unconfigure(config):
    for key, value in _previous_environment.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    """MockTransport stays usable; real database connections must stay on loopback."""
    original_resolve, original_connect = socket.getaddrinfo, socket.socket.connect

    def allowed(host):
        if host in (None, "localhost", b"localhost"):
            return True
        if isinstance(host, bytes):
            host = host.decode("ascii")
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def resolve(host, *args, **kwargs):
        if not allowed(host):
            raise AssertionError("Offline tests cannot resolve external hosts")
        return original_resolve(host, *args, **kwargs)

    def connect(sock, address):
        if isinstance(address, tuple) and not allowed(address[0]):
            raise AssertionError("Offline tests cannot connect to external hosts")
        return original_connect(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "connect", connect)


@pytest.fixture(scope="session")
def demo_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("data") / "ecommerce.db"
    build_demo_db(path)
    return path


@pytest.fixture()
def db(demo_db_path: Path):
    database = AnalyticsDatabase(
        f"sqlite:///{demo_db_path.as_posix()}", max_rows=50, timeout_seconds=5
    )
    yield database
    database.dispose()


@pytest.fixture(scope="session")
def glossary() -> GlossaryStore:
    return GlossaryStore.from_file(GLOSSARY)


@pytest.fixture()
def registry(db: AnalyticsDatabase, glossary: GlossaryStore) -> ToolRegistry:
    reg = ToolRegistry(timeout_seconds=10)
    for tool in build_analytics_tools(db, glossary):
        reg.register(tool)
    return reg


def run(coro):
    return asyncio.run(coro)


def tool_call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(
        id=f"call_{uuid.uuid4().hex[:8]}",
        name=name,
        arguments=json.dumps(arguments, ensure_ascii=False),
    )


def reply(content: str = "", *calls: ToolCall) -> LLMResponse:
    return LLMResponse(
        content=content, model_id="fake", tool_calls=list(calls), usage={"total_tokens": 10}
    )


Responder = LLMResponse | Callable[[list[dict[str, Any]], dict[str, Any]], LLMResponse]


class ScriptedLLM:
    """
    按顺序返回预设响应的假 LLM，并记录每次收到的 messages / kwargs。

    预设项可以是 LLMResponse，也可以是 (messages, kwargs) -> LLMResponse 的函数，
    后者用于根据上一步的工具结果动态构造响应、或断言模型看到了什么。
    """

    def __init__(self, script: list[Responder]) -> None:
        self.script = list(script)
        self.calls: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        snapshot = [dict(m) for m in messages]
        self.calls.append((snapshot, kwargs))
        if not self.script:
            raise AssertionError(f"ScriptedLLM 脚本已用完，第 {len(self.calls)} 次调用未预期")
        item = self.script.pop(0)
        return item(snapshot, kwargs) if callable(item) else item
