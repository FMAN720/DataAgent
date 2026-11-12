"""第 12 课：真实 HTTP 路由、SSE、trace 与真实熔断器；无网络。"""
import json

from course_support import ScriptedLLM, demo_resources, prepare_output, reply, run, tool_call
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.chat import router
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.memory.session import InMemorySessionStore
from app.infrastructure.llm.circuit_breaker import CircuitBreaker


def http_demo(registry):
    # 单独挂真实路由；不运行生产 lifespan，不创建真实模型客户端或文档库连接。
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.agent = AgentOrchestrator(
        ScriptedLLM([reply("", tool_call("run_sql", sql="SELECT COUNT(*) FROM regions")),
                     reply("共有 5 个区域。")]),
        registry, InMemorySessionStore(), system_prompt="SYSTEM", enable_reflection=False)
    with TestClient(app) as client:
        with client.stream("POST", "/api/v1/chat/stream", json={"question": "有几个区域？"}) as response:
            events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
        print("SSE 顺序:", [event["type"] for event in events])
        assert [e["type"] for e in events] == ["start", "llm", "tool", "llm", "final"]
        trace_id = events[0]["trace_id"]
        trace = client.get(f"/api/v1/traces/{trace_id}").json()
        print("trace 操作:", [s["operation"] for s in trace["spans"]])
        print("空问题状态码:", client.post("/api/v1/chat", json={"question": ""}).status_code)
        app.state.agent = None
        print("未初始化状态码:", client.post("/api/v1/chat", json={"question": "hi"}).status_code)


async def circuit_demo():
    breaker = CircuitBreaker(failure_threshold=2, name="course")
    async def fail():
        raise ValueError("模拟接口失败")
    print("初始熔断状态:", breaker.state.value)
    for _ in range(3):
        try:
            await breaker.call(fail)
        except Exception as exc:
            print(type(exc).__name__, str(exc), "state=", breaker.state.value)
    assert breaker.state.value == "open"


if __name__ == "__main__":
    prepare_output()
    with demo_resources() as (_, __, registry):
        http_demo(registry)
    run(circuit_demo())
