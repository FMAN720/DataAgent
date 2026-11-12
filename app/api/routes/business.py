"""Local business definitions and explicit task inspection / recovery. No automatic model calls."""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, ValidationError, field_validator

from app.analytics.semantics import BusinessProfile, ContractModel
from app.api.routes.chat import _get_agent, _to_schema
from app.api.routes.model_settings import local_store
from app.models.enums import AgentMode
from app.models.schemas import AgentChatResponse

router = APIRouter(tags=["local-business-workspace"])


class ProfileUpdate(ContractModel):
    expected_revision: int = Field(ge=0)
    profile: BusinessProfile


class ResumeTask(ContractModel):
    confirmed: bool = Field(strict=True)

    @field_validator("confirmed")
    @classmethod
    def explicitly_confirmed(cls, value):
        if not value:
            raise ValueError("恢复任务须明确确认重新查询及可能的模型费用")
        return value


def body_schema(model):
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value):
        if isinstance(value, dict):
            if "$ref" in value:
                return inline(definitions[value["$ref"].rsplit("/", 1)[-1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value

    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": inline(schema)}},
        }
    }


async def validated_body(request: Request, model):
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(415, "请使用 JSON 提交配置或恢复确认")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 131072:
            raise HTTPException(413, "配置文件超过 128 KiB，请拆分业务范围")
    try:
        return model.model_validate_json(bytes(content))
    except ValidationError as exc:
        errors = [
            {"loc": list(error["loc"]), "msg": error["msg"]}
            for error in exc.errors(include_input=False, include_context=False, include_url=False)
        ]
        raise HTTPException(422, errors) from None


@router.get("/business-profiles")
async def profiles(request: Request):
    local_store(request)
    return {"profiles": await asyncio.to_thread(request.app.state.semantic_catalog.list)}


@router.get("/business-profiles/{identifier}")
async def profile(
    identifier: str, request: Request, revision: int | None = Query(default=None, ge=1)
):
    local_store(request)
    if identifier == "ecommerce_v1":
        if revision not in (None, 1):
            raise HTTPException(404, "内置业务口径只有 v1")
        from app.analytics.metrics import METRICS

        return {
            **request.app.state.semantic_catalog.snapshot().reference,
            "readonly": True,
            "definitions": [metric.public() for metric in METRICS.values()],
        }
    return await asyncio.to_thread(
        request.app.state.workspace_store.get_profile, identifier, revision
    )


@router.put("/business-profiles/{identifier}", openapi_extra=body_schema(ProfileUpdate))
async def update_profile(identifier: str, request: Request):
    local_store(request)
    body = await validated_body(request, ProfileUpdate)
    if identifier != body.profile.id:
        raise HTTPException(422, "路径 id 与业务口径 id 须一致")
    return await asyncio.to_thread(
        request.app.state.semantic_catalog.save, body.profile, body.expected_revision
    )


@router.get("/analysis-tasks")
async def tasks(request: Request, limit: int = Query(default=30, ge=1, le=100)):
    local_store(request)
    return {"tasks": await asyncio.to_thread(request.app.state.workspace_store.list_tasks, limit)}


@router.get("/analysis-tasks/{task_id}")
async def task(task_id: UUID, request: Request):
    local_store(request)
    return await asyncio.to_thread(request.app.state.workspace_store.get_task, str(task_id))


@router.post(
    "/analysis-tasks/{task_id}/resume",
    response_model=AgentChatResponse,
    openapi_extra=body_schema(ResumeTask),
)
async def resume(task_id: UUID, request: Request):
    local_store(request)
    await validated_body(request, ResumeTask)
    stored = await asyncio.to_thread(
        request.app.state.workspace_store.get_task, str(task_id), include_events=False
    )
    if not stored["resumable"]:
        raise HTTPException(409, "只能恢复失败、停止或已中断的任务")
    agent = _get_agent(request)
    agent.check_model_ready()
    response = await agent.run(
        stored["question"],
        stored["session_id"],
        mode=AgentMode(stored["mode"]),
        business_profile_id=stored["profile_ref"]["id"],
        resume_task_id=stored["id"],
    )
    return _to_schema(response)
