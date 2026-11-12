"""数据分析 Agent 对话 API：非流式返回完整轨迹，流式（SSE）实时推送每个步骤。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from app.core.agent.orchestrator import AgentOrchestrator, AgentResponse
from app.core.memory.redis_session import SessionStoreUnavailable
from app.models.schemas import AgentChatRequest, AgentChatResponse, ReflectionOut, ToolStepOut

router = APIRouter(tags=["chat"])


def _get_agent(request: Request) -> AgentOrchestrator:
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        reason = getattr(request.app.state, "agent_error", None) or "Agent 未初始化"
        raise HTTPException(status_code=503, detail=reason)
    return agent


def _to_schema(resp: AgentResponse) -> AgentChatResponse:
    return AgentChatResponse(
        answer=resp.answer,
        success=resp.success,
        session_id=resp.session_id,
        trace_id=resp.trace_id,
        mode=resp.mode,
        steps=[
            ToolStepOut(
                step=s.step,
                tool=s.tool,
                arguments=s.arguments,
                ok=s.ok,
                output=s.output,
                duration_ms=s.duration_ms,
            )
            for s in resp.steps
        ],
        plan=resp.plan,
        reflection=ReflectionOut(**resp.reflection.to_dict()) if resp.reflection else None,
        reflection_status=resp.reflection_status,
        revised=resp.revised,
        llm_calls=resp.llm_calls,
        usage=resp.usage,
        duration_ms=resp.duration_ms,
        error=resp.error,
        citations=resp.citations,
        citation_errors=resp.citation_errors,
        analysis=resp.analysis,
        needs_clarification=resp.needs_clarification,
        usage_breakdown=resp.usage_breakdown,
        execution_llm_calls=resp.execution_llm_calls,
        question=resp.question,
        tables=resp.tables,
        charts=resp.charts,
        analysis_state=resp.analysis_state,
        archive_status=resp.archive_status,
        archive_error=resp.archive_error,
        context_applied=resp.context_applied,
        created_at=resp.created_at,
        validation=resp.validation,
        business_profile=resp.business_profile,
        task_id=resp.task_id,
        task_attempt=resp.task_attempt,
    )


def run_options(body: AgentChatRequest) -> dict[str, Any]:
    options: dict[str, Any] = {"mode": body.mode}
    if body.business_profile_id is not None:
        options["business_profile_id"] = body.business_profile_id
    if body.resume_task_id is not None:
        options["resume_task_id"] = body.resume_task_id
    return options


@router.post("/chat", response_model=AgentChatResponse)
async def chat(body: AgentChatRequest, request: Request) -> AgentChatResponse:
    """提问并等待完整结果。响应中的 steps 是 Agent 实际执行的每一次工具调用。"""
    agent = _get_agent(request)
    resp = await agent.run(body.question, body.session_id, **run_options(body))
    return _to_schema(resp)


def _sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n".encode()


@router.post("/chat/stream")
async def chat_stream(body: AgentChatRequest, request: Request) -> StreamingResponse:
    """
    流式输出 Agent 执行过程（Server-Sent Events）。

    事件类型：start / plan / llm / tool / reflection / revision / final / error。
    """
    agent = _get_agent(request)
    # SSE 响应头发出之后无法再改为 503，先探测会话依赖。
    model_check = getattr(agent, "check_model_ready", None)
    if model_check is not None:
        model_check()
    await agent.check_session_ready()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def on_event(event: dict[str, Any]) -> None:
        await queue.put(event)

    async def runner() -> None:
        try:
            await agent.run(body.question, body.session_id, **run_options(body), on_event=on_event)
        except SessionStoreUnavailable as exc:
            await queue.put({"type": "error", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("chat_stream 执行失败")
            await queue.put({"type": "error", "error": str(exc)})
        finally:
            await queue.put(None)

    async def stream() -> AsyncIterator[bytes]:
        task = asyncio.create_task(runner())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse(event)
        finally:
            # 客户端断开时取消后台任务，避免继续消耗 token
            if not task.done():
                task.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def clear_session(session_id: str, request: Request) -> None:
    """清除指定会话的上下文，不删除执行轨迹、上传文档或业务数据。"""
    await _get_agent(request).clear_session(session_id)


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str, request: Request) -> dict[str, Any]:
    """查看一次请求的追踪记录（进程内存储，重启后丢失）。"""
    agent = _get_agent(request)
    rec = agent.tracer.get_trace(trace_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="trace 不存在或已过期")
    return {
        "trace_id": rec.trace_id,
        "spans": [
            {
                "span_id": s.span_id,
                "parent_span_id": s.parent_span_id,
                "operation": s.operation,
                "duration_ms": int((s.end_time - s.start_time) * 1000) if s.end_time else None,
                "result": s.result,
                "error": s.error,
            }
            for s in rec.spans
        ],
    }
