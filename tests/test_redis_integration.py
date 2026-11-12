"""专用本机 Redis 验证；只创建合成对话和唯一前缀，不清空数据库。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.memory.redis_session import RedisSessionStore, SessionStoreUnavailable
from tests.conftest import ScriptedLLM, reply, run

pytestmark = [
    pytest.mark.redis,
    pytest.mark.skipif(
        os.environ.get("RUN_REDIS_TESTS") != "1",
        reason="Set RUN_REDIS_TESTS=1 for local Redis",
    ),
]


@asynccontextmanager
async def stores(*, max_turns=10, ttl=30):
    settings = Settings(
        _env_file=None,
        session_backend="redis",
        redis_url="redis://127.0.0.1:16379/0",
        redis_key_prefix=f"analyst:test:{uuid.uuid4().hex}:",
        session_max_turns=max_turns,
        session_ttl_seconds=ttl,
    )
    first, second = (RedisSessionStore.from_settings(settings) for _ in range(2))
    try:
        await first.check_ready()
        yield first, second
    finally:
        # 删除测试自己的精确键，不扫描/删除其他项，不调用 FLUSHDB。
        for sid in ("shared", "other", "parallel", "subprocess"):
            await first.clear(sid)
        await first.aclose()
        await second.aclose()


def test_independent_clients_share_window_and_clear_is_isolated():
    async def scenario():
        async with stores(max_turns=2) as (first, second):
            await first.append_turn("other", "keep", "keep answer")
            await first.append_turn("shared", "q1", "a1")
            await second.append_turn("shared", "q2", "a2")
            await first.append_turn("shared", "q3", "a3")
            assert await second.get_history("shared") == [
                {"role": "user", "content": "q2"},
                {"role": "assistant", "content": "a2"},
                {"role": "user", "content": "q3"},
                {"role": "assistant", "content": "a3"},
            ]
            assert await first._client.llen(first._key("shared")) == 2
            await second.clear("shared")
            assert not await first.get_history("shared")
            assert await first.get_history("other")

    run(scenario())


def test_expiry_and_writes_refresh_ttl_but_reads_do_not():
    async def scenario():
        async with stores(ttl=3) as (first, second):
            await first.append_turn("shared", "q1", "a1")
            key = first._key("shared")
            await first._client.pexpire(key, 300)
            await second.get_history("shared")
            assert 0 < await first._client.pttl(key) <= 300
            await second.append_turn("shared", "q2", "a2")
            assert await first._client.pttl(key) > 1000
            await first._client.pexpire(key, 50)
            await asyncio.sleep(0.1)
            assert not await second.get_history("shared")
            await second.append_turn("shared", "new", "new answer")
            assert len(await first.get_history("shared")) == 2

    run(scenario())


def test_concurrent_appends_do_not_lose_or_split_turns():
    async def scenario():
        async with stores(max_turns=100) as (first, second):
            await asyncio.gather(
                *[
                    (first if i % 2 else second).append_turn("parallel", f"q{i}", f"a{i}")
                    for i in range(40)
                ]
            )
            messages = await first.get_history("parallel")
            assert len(messages) == 80
            assert {m["content"] for m in messages[::2]} == {f"q{i}" for i in range(40)}
            for question, answer in zip(messages[::2], messages[1::2]):
                assert question["content"][1:] == answer["content"][1:]

    run(scenario())


def test_separate_os_process_reads_same_history():
    async def scenario():
        async with stores() as (first, _):
            await first.append_turn("subprocess", "synthetic question", "synthetic answer")
            code = """
import asyncio, json, sys
from redis.asyncio import Redis
async def main():
    r = Redis.from_url('redis://127.0.0.1:16379/0', decode_responses=True)
    try:
        print(json.dumps(await r.lrange(sys.argv[1], 0, -1)))
    finally:
        await r.aclose()
asyncio.run(main())
"""
            completed = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-X", "utf8", "-c", code, first._key("subprocess")],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            assert json.loads(json.loads(completed.stdout)[0])["assistant"] == "synthetic answer"

    run(scenario())


def test_followup_moves_between_agent_instances(registry):
    async def scenario():
        async with stores() as (first, second):
            one = AgentOrchestrator(
                ScriptedLLM([reply("first answer")]),
                registry,
                first,
                system_prompt="SYSTEM",
                enable_reflection=False,
            )

            def followup(messages, kwargs):
                assert messages[1:3] == [
                    {"role": "user", "content": "first question"},
                    {"role": "assistant", "content": "first answer"},
                ]
                return reply("second answer")

            two = AgentOrchestrator(
                ScriptedLLM([followup]),
                registry,
                second,
                system_prompt="SYSTEM",
                enable_reflection=False,
            )
            await one.run("first question", "shared")
            result = await two.run("followup", "shared")
            assert result.success and result.answer == "second answer"

    run(scenario())


def test_real_redis_connection_failure_is_explicit():
    async def scenario():
        # 动态取得本机未监听端口，不停止任何现有 Redis。
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        settings = Settings(
            _env_file=None, redis_url=f"redis://127.0.0.1:{port}/0", redis_timeout_seconds=0.1
        )
        store = RedisSessionStore.from_settings(settings)
        try:
            with pytest.raises(SessionStoreUnavailable):
                await store.get_history("s")
            with pytest.raises(SessionStoreUnavailable):
                await store.check_ready()
        finally:
            await store.aclose()

    run(scenario())


def test_configured_backend_is_wired_through_two_fastapi_apps(monkeypatch, tmp_path):
    import app.main as main
    from app.infrastructure.llm import local_settings

    prefix = f"analyst:test:{uuid.uuid4().hex}:"
    settings = Settings(
        _env_file=None,
        openai_api_key="synthetic-not-used",
        local_model_settings_path=tmp_path / "personal-model.json",
        local_workspace_path=tmp_path / "workspace.sqlite3",
        session_backend="redis",
        redis_url="redis://127.0.0.1:16379/0",
        redis_key_prefix=prefix,
        documents_enabled=False,
        datasets_enabled=False,
        embedding_enabled=False,
        enable_reflection=False,
    )

    class LocalLLM(ScriptedLLM):
        closed = False

        async def aclose(self):
            self.closed = True

    def check_history(messages, kwargs):
        assert {"role": "assistant", "content": "first synthetic answer"} in messages
        return reply("second synthetic answer")

    llms = [LocalLLM([reply("first synthetic answer")]), LocalLLM([check_history])]
    remaining = iter(llms)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(local_settings, "ModelRouter", lambda configs: next(remaining))
    first_app, second_app = main.create_app(), main.create_app()
    try:
        with TestClient(first_app) as first, TestClient(second_app) as second:
            assert (
                first.put(
                    "/api/v1/model-settings",
                    json={
                        "base_url": "https://offline.test/v1",
                        "model": "synthetic",
                        "api_key": "offline-personal-key",
                    },
                ).status_code
                == 200
            )
            one = first.post(
                "/api/v1/chat", json={"question": "synthetic q", "session_id": "shared"}
            )
            assert one.status_code == 200
            two = second.post(
                "/api/v1/chat/stream", json={"question": "followup", "session_id": "shared"}
            )
            events = [
                json.loads(line[6:]) for line in two.text.splitlines() if line.startswith("data: ")
            ]
            assert (
                events[-1]["type"] == "final" and events[-1]["answer"] == "second synthetic answer"
            )
            ready = second.get("/api/v1/health/ready").json()
            assert ready["session_store"] == "up" and ready["session_backend"] == "redis"
            assert first.delete("/api/v1/sessions/shared").status_code == 204
        assert all(llm.closed for llm in llms)
    finally:

        async def cleanup():
            store = RedisSessionStore.from_settings(settings)
            try:
                await store.clear("shared")
            finally:
                await store.aclose()

        run(cleanup())
