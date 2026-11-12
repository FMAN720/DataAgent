"""Bounded OpenAI-compatible model discovery and explicit, synthetic connection probes."""

from __future__ import annotations

import json
import struct
import zlib

import httpx
from pydantic import SecretStr

from app.infrastructure.llm.local_settings import LocalModelError, ModelProfile


def api_client():
    return httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False)


async def provider_request(base_url: str, key: SecretStr, path: str, *, payload=None):
    """Never follow redirects, forward environment proxies, log or echo provider bodies."""
    try:
        async with api_client() as client:
            async with client.stream(
                "GET" if payload is None else "POST",
                base_url.rstrip("/") + "/" + path,
                headers={"Authorization": "Bearer " + key.get_secret_value()},
                **({"json": payload} if payload is not None else {}),
            ) as response:
                if response.status_code != 200:
                    messages = {
                        401: "Key 无效或已过期，请重新填写当前供应商的 Key。",
                        403: "当前 Key 没有访问权限，请检查供应商账户及模型授权。",
                        404: "供应商未提供此接口，或 API 接入地址不正确；模型列表不可用时可手动填写模型。",
                        429: "供应商限流或额度不足，请稍后重试。",
                    }
                    raise LocalModelError(
                        messages.get(
                            response.status_code,
                            f"供应商返回 HTTP {response.status_code}，请检查接入地址及服务状态。",
                        ),
                        502,
                    )
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 2_000_000:
                        raise LocalModelError("供应商响应过大，请使用更精确的接口。", 502)
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise ValueError()
                return value
    except LocalModelError:
        raise
    except (httpx.HTTPError, TimeoutError):
        raise LocalModelError(
            "无法连接供应商或请求超时，请检查 API 接入地址和网络。测试请求可能已计费，未自动重试。",
            502,
        ) from None
    except (ValueError, TypeError):
        raise LocalModelError(
            "供应商返回的格式不符合兼容接口，请核对 API 接入地址。", 502
        ) from None


async def list_models(base_url: str, key: SecretStr) -> dict:
    value = await provider_request(base_url, key, "models")
    rows = value.get("data")
    if not isinstance(rows, list):
        raise LocalModelError("供应商未返回有效模型列表；可以手动填写模型。", 502)
    models = set()
    for row in rows:
        identifier = row.get("id") if isinstance(row, dict) else None
        if (
            isinstance(identifier, str)
            and 0 < len(identifier) <= 256
            and not any(ord(char) < 32 for char in identifier)
            and key.get_secret_value() not in identifier
        ):
            models.add(identifier)
        if len(models) > 2000:
            raise LocalModelError("模型列表超过 2000 个，请使用更精确的供应商接入地址。", 502)
    return {
        "models": [{"id": identifier} for identifier in sorted(models, key=str.casefold)],
        "message": "模型列表由供应商返回，当前 Key 的实际权限及模型用途仍需测试确认。",
    }


async def check_embedding(profile: ModelProfile) -> dict:
    from app.knowledge.embedding import validate_vectors
    from app.knowledge.types import EmbeddingError

    value = await provider_request(
        profile.base_url,
        profile.api_key,
        "embeddings",
        payload={
            "model": profile.model,
            "input": ["Connection check."],
            "encoding_format": "float",
        },
    )
    try:
        rows = value["data"]
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
            or type(rows[0].get("index")) is not int
            or rows[0]["index"] != 0
        ):
            raise ValueError()
        vector = rows[0]["embedding"]
        dimensions = len(vector)
        if not 1 <= dimensions <= 16384:
            raise ValueError()
        validate_vectors([vector], 1, dimensions)
        if profile.dimensions is not None and profile.dimensions != dimensions:
            raise LocalModelError(
                f"实际向量维度为 {dimensions}，与填写值不一致，请修改后保存。", 422
            )
        return {"connected": True, "dimensions": dimensions}
    except LocalModelError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, EmbeddingError):
        raise LocalModelError("此模型未返回有效向量，请选择文本向量模型。", 502) from None


def _probe_png():
    """A deterministic TEST bitmap; stdlib only, no user image or font dependency."""
    letters = {
        "T": [31, 4, 4, 4, 4, 4, 4],
        "E": [31, 16, 16, 30, 16, 16, 31],
        "S": [31, 16, 16, 31, 1, 1, 31],
    }
    width, height, scale = 128, 64, 4
    pixels = bytearray([255] * (width * height))
    for index, letter in enumerate("TEST"):
        for y, row in enumerate(letters[letter]):
            for x in range(5):
                if row & (1 << (4 - x)):
                    for dy in range(scale):
                        for dx in range(scale):
                            pixels[
                                (18 + y * scale + dy) * width + 18 + index * 24 + x * scale + dx
                            ] = 0

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    raw = b"".join(b"\0" + pixels[y * width : (y + 1) * width] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


PROBE_PNG = _probe_png()
