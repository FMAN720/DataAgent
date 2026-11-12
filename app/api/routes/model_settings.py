"""Local Agent model settings; secrets never appear in API responses."""

from __future__ import annotations

import ipaddress
from contextlib import suppress
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from app.infrastructure.llm.errors import ModelRequestError
from app.infrastructure.llm.local_settings import (
    MODEL_PURPOSES,
    LocalModelStore,
    ModelEndpointInput,
    ModelProfile,
    ModelPurpose,
    ModelSettingsInput,
)
from app.infrastructure.llm.model_router import ModelConfig, ModelRouter

router = APIRouter(prefix="/model-settings", tags=["local-model-settings"])
SETTINGS_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": ModelSettingsInput.model_json_schema()}},
    }
}


def local_store(request: Request) -> LocalModelStore:
    host = request.client.host if request.client else ""
    hostname = request.url.hostname or ""
    try:
        local_host = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        local_host = hostname == "localhost" or host == "testclient" and hostname == "testserver"
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "testclient"  # Starlette's in-process test transport, not a TCP IP.
    if not local and getattr(request.app.state, "local_model_settings_trust_proxy", False):
        # Compose publishes only 127.0.0.1; the container sees its bridge gateway.
        # This explicit opt-in never admits public peers or non-loopback Host names.
        try:
            address = ipaddress.ip_address(host)
            bridge = any(
                address in ipaddress.ip_network(network)
                for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
            local = bridge and request.url.hostname in {"127.0.0.1", "localhost", "::1"}
        except ValueError:
            pass
    origin = request.headers.get("origin")
    try:
        same_origin = not origin or (urlsplit(origin).scheme, urlsplit(origin).netloc) == (
            request.url.scheme,
            request.url.netloc,
        )
    except ValueError:
        same_origin = False
    if not local or not local_host or not same_origin:
        raise HTTPException(403, "API 设置仅供运行 Agent 的本机使用。")
    store = getattr(request.app.state, "local_model_store", None)
    if store is None:
        raise HTTPException(503, "本机 API 设置服务尚未就绪。")
    return store


async def validated_update(request: Request, *, endpoint=False):
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(415, "请使用 JSON 提交模型设置。")
    try:
        import json

        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > 65536:
                raise HTTPException(413, "模型设置超过大小限制。")
        raw = json.loads(body)
        return (ModelEndpointInput if endpoint else ModelSettingsInput).model_validate(raw)
    except ValidationError as exc:
        # Default request validation echoes the invalid input. Never echo a Key.
        errors = [
            {"loc": list(error["loc"]), "msg": error["msg"]}
            for error in exc.errors(include_input=False, include_context=False, include_url=False)
        ]
        raise HTTPException(422, errors) from None
    except (ValueError, TypeError):
        raise HTTPException(422, "模型设置格式不正确。") from None


def settings_view(store, purpose):
    return {
        **store.public(purpose),
        "purpose": purpose,
        "profiles": {kind: store.public(kind) for kind in MODEL_PURPOSES},
    }


@router.get("")
async def get_model_settings(request: Request, purpose: ModelPurpose = "chat"):
    return settings_view(local_store(request), purpose)


@router.put("", openapi_extra=SETTINGS_BODY)
async def save_model_settings(request: Request, purpose: ModelPurpose = "chat"):
    store = local_store(request)
    profile = store.resolve(await validated_update(request), purpose)
    store.save(profile, purpose)
    return settings_view(store, purpose)


@router.delete("")
async def clear_model_settings(request: Request, purpose: ModelPurpose = "chat"):
    store = local_store(request)
    store.delete(purpose)
    return settings_view(store, purpose)


@router.post(
    "/models",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": ModelEndpointInput.model_json_schema()}},
        }
    },
)
async def discover_models(request: Request, purpose: ModelPurpose = "chat"):
    from app.infrastructure.llm.provider_api import list_models

    store = local_store(request)
    update = await validated_update(request, endpoint=True)
    return await list_models(update.base_url, store.resolve_endpoint(update, purpose))


@router.post("/check", openapi_extra=SETTINGS_BODY)
async def check_model_settings(request: Request, purpose: ModelPurpose = "chat"):
    store = local_store(request)
    update = await validated_update(request)
    profile = ModelProfile(
        **{**update.model_dump(), "api_key": store.resolve_endpoint(update, purpose)}
    )
    if purpose == "embedding":
        from app.infrastructure.llm.provider_api import check_embedding

        return await check_embedding(profile)
    if purpose == "vision":
        from app.config import get_settings
        from app.infrastructure.llm.provider_api import PROBE_PNG
        from app.vision.provider import VisionProvider

        settings = get_settings().model_copy(
            update={
                "vision_enabled": True,
                "vision_api_base": profile.base_url,
                "vision_api_key": profile.api_key.get_secret_value(),
                "vision_model": profile.model,
                "vision_max_output_tokens": 512,
            }
        )
        provider = None
        try:
            provider = VisionProvider(settings)
            result, _ = await provider.extract(PROBE_PNG)
            if "TEST" not in result["text"].upper():
                raise ValueError("probe_text_not_recognized")
            return {"connected": True, "vision_supported": True}
        except Exception:
            raise HTTPException(
                502, "图片连接测试未通过，请检查地址、Key、模型图片输入及转录格式支持。"
            ) from None
        finally:
            if provider is not None:
                await provider.aclose()
    llm = None
    try:
        llm = ModelRouter(
            [
                ModelConfig(
                    model_id=profile.model,
                    base_url=profile.base_url,
                    api_key=profile.api_key.get_secret_value(),
                )
            ],
            timeout_seconds=20,
            max_retries=0,
        )
        response = await llm.chat(
            [{"role": "user", "content": "Call connection_check with an empty object."}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "connection_check",
                        "description": "Confirm the connection; no data is queried.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            tool_choice="auto",
            max_tokens=256,
        )
        return {
            "connected": True,
            "tools_supported": any(call.name == "connection_check" for call in response.tool_calls),
        }
    except Exception as exc:
        # Keep the HTTP/error category, never the raw exception, body or Key.
        raise HTTPException(502, ModelRequestError.from_exception(exc).user_message) from None
    finally:
        if llm is not None:
            with suppress(Exception):
                await llm.aclose()
