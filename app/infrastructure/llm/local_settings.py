"""Per-installation model credentials. Never fall back to the developer's .env Key."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.infrastructure.llm.model_router import LLMResponse, ModelConfig, ModelRouter


class LocalModelError(RuntimeError):
    def __init__(self, message: str, status_code: int = 428):
        super().__init__(message)
        self.status_code = status_code


def default_model_settings_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config")
    return root / "DataAnalystAgent" / "model.json"


ModelPurpose = Literal["chat", "vision", "embedding"]
MODEL_PURPOSES: tuple[ModelPurpose, ...] = ("chat", "vision", "embedding")


class ModelEndpointInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr | None = None

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        try:
            url = urlsplit(value)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
                or any(char.isspace() or ord(char) < 32 for char in value)
            ):
                raise ValueError
            _ = url.port
            if url.hostname in {"deepseek.com", "www.deepseek.com"}:
                raise ValueError(
                    "官网不是 API 接口，请选择 DeepSeek 供应商或填写 https://api.deepseek.com"
                )
        except ValueError as exc:
            if str(exc).startswith("官网不是"):
                raise
            raise ValueError("请输入不含账号、查询参数或片段的 HTTP(S) API 基础地址") from None
        return value.rstrip("/")

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        key = value.get_secret_value().strip()
        if not key or len(key) > 8192 or any(ord(char) < 32 for char in key):
            raise ValueError("API Key 不能为空或包含控制字符，且不能超过 8192 字符")
        return SecretStr(key)


class ModelSettingsInput(ModelEndpointInput):
    model: str = Field(min_length=1, max_length=256)
    enabled: bool = Field(default=True, strict=True)
    dimensions: int | None = Field(default=None, ge=1, le=16384, strict=True)

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("模型名称不能包含控制字符")
        return value


class ModelProfile(ModelSettingsInput):
    api_key: SecretStr


def _windows_protect(payload: bytes, *, decrypt: bool = False) -> bytes:
    """Windows DPAPI binds the secret to the current OS account; no extra package."""

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(payload)
    source = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    windows_library = getattr(ctypes, "WinDLL")
    crypt32 = windows_library("crypt32", use_last_error=True)
    kernel32 = windows_library("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(Blob),
    ]
    function.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError("本机凭据加密服务不可用")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(output.data)


def _seal(key: str) -> str:
    if os.name == "nt":
        return "dpapi-v1:" + base64.b64encode(_windows_protect(key.encode())).decode("ascii")
    return "local-v1:" + key


def _unseal(value: str) -> str:
    if value.startswith("dpapi-v1:") and os.name == "nt":
        return _windows_protect(base64.b64decode(value[9:], validate=True), decrypt=True).decode()
    if value.startswith("local-v1:") and os.name != "nt":
        return value[9:]
    raise ValueError("不支持的本机凭据格式")


class LocalModelStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def _profiles(self) -> dict[str, dict]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError()
            if raw.get("version") == 1:
                return {"chat": {key: value for key, value in raw.items() if key != "version"}}
            if raw.get("version") != 2 or not isinstance(raw.get("profiles"), dict):
                raise ValueError()
            profiles = raw["profiles"]
            if any(
                key not in MODEL_PURPOSES or not isinstance(value, dict)
                for key, value in profiles.items()
            ):
                raise ValueError()
            return profiles
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError):
            raise LocalModelError("本机 API 配置无法读取，请重新填写并保存。", 409) from None

    def read(self, purpose: ModelPurpose = "chat") -> ModelProfile | None:
        try:
            raw = self._profiles().get(purpose)
            if raw is None:
                return None
            return ModelProfile(
                base_url=raw["base_url"],
                model=raw["model"],
                api_key=SecretStr(_unseal(raw["api_key"])),
                enabled=raw.get("enabled", True),
                dimensions=raw.get("dimensions"),
            )
        except (OSError, ValueError, KeyError, TypeError):
            raise LocalModelError("本机 API 配置无法读取，请重新填写并保存。", 409) from None

    def require(self, purpose: ModelPurpose = "chat") -> ModelProfile:
        profile = self.read(purpose)
        if profile is None or not profile.enabled:
            raise LocalModelError("请先在 API 设置中配置你自己的接入地址、模型和 Key。")
        return profile

    def public(self, purpose: ModelPurpose = "chat") -> dict[str, Any]:
        try:
            profile = self.read(purpose)
        except LocalModelError as exc:
            return {
                "configured": False,
                "base_url": "",
                "model": "",
                "has_api_key": False,
                "error": str(exc),
                "storage": "os-user-local",
            }
        return {
            "configured": profile is not None and profile.enabled,
            "base_url": profile.base_url if profile else "",
            "model": profile.model if profile else "",
            "has_api_key": profile is not None,
            "enabled": profile.enabled if profile else False,
            "dimensions": profile.dimensions if profile else None,
            "storage": "os-user-local",
        }

    def resolve_endpoint(
        self, update: ModelEndpointInput, purpose: ModelPurpose = "chat"
    ) -> SecretStr:
        key = update.api_key
        if key is None:
            current = self.read(purpose)
            if current is None:
                raise LocalModelError("请填写当前用途的 API Key；不同用途不会自动共用 Key。", 422)
            if current.base_url != update.base_url:
                raise LocalModelError("更换 API 接入地址后，请重新输入对应的 Key。", 422)
            key = current.api_key
        return key

    def resolve(self, update: ModelSettingsInput, purpose: ModelPurpose = "chat") -> ModelProfile:
        url = urlsplit(update.base_url)
        if (
            purpose != "chat"
            and url.scheme == "http"
            and url.hostname not in {"127.0.0.1", "localhost", "::1"}
        ):
            raise LocalModelError("图片与向量服务需要 HTTPS 地址；本机回环地址可使用 HTTP。", 422)
        if purpose == "embedding" and update.enabled and update.dimensions is None:
            raise LocalModelError("请先测试向量模型获取维度，或填写供应商提供的向量维度。", 422)
        if purpose != "embedding" and update.dimensions is not None:
            raise LocalModelError("只有向量模型需要配置维度。", 422)
        return ModelProfile(
            **{**update.model_dump(), "api_key": self.resolve_endpoint(update, purpose)}
        )

    def save(self, profile: ModelProfile, purpose: ModelPurpose = "chat") -> None:
        with self._lock:
            try:
                profiles = self._profiles()
            except LocalModelError:
                # A fresh explicit Key can recover an unreadable file. Preserve the
                # original privately rather than silently discarding unknown profiles.
                try:
                    fd, _ = tempfile.mkstemp(prefix=".model-recovery-", dir=self.path.parent)
                    with os.fdopen(fd, "wb") as backup:
                        backup.write(self.path.read_bytes())
                except OSError:
                    raise LocalModelError(
                        "无法保留损坏的配置，请检查本机配置目录权限。", 503
                    ) from None
                profiles = {}
            profiles[purpose] = {
                **profile.model_dump(exclude={"api_key"}),
                "api_key": _seal(profile.api_key.get_secret_value()),
            }
            self._write(profiles)

    def _write(self, profiles: dict[str, dict]) -> None:
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            raw = {"version": 2, "profiles": profiles}
            fd, temporary = tempfile.mkstemp(prefix=".model-", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(raw, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise LocalModelError(
                "无法保存本机 API 配置，请检查本机配置目录的写入权限。", 503
            ) from None
        finally:
            if temporary is not None:
                with suppress(OSError):
                    Path(temporary).unlink(missing_ok=True)

    def delete(self, purpose: ModelPurpose = "chat") -> None:
        try:
            with self._lock:
                profiles = self._profiles()
                profiles.pop(purpose, None)
                if profiles:
                    self._write(profiles)
                else:
                    self.path.unlink(missing_ok=True)
        except OSError:
            raise LocalModelError("无法清除本机 API 配置，请检查配置目录权限。", 503) from None


class LocalModelClient:
    """One immutable client per running turn; settings changes affect the next turn."""

    def __init__(self, store: LocalModelStore):
        self.store = store
        self._current: ContextVar[ModelRouter | None] = ContextVar(
            "local_model_client", default=None
        )

    def check_ready(self) -> None:
        self.store.require()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[None]:
        profile = self.store.require()
        try:
            router = ModelRouter(
                [
                    ModelConfig(
                        model_id=profile.model,
                        api_key=profile.api_key.get_secret_value(),
                        base_url=profile.base_url,
                    )
                ]
            )
        except Exception:
            raise LocalModelError("无法创建模型连接，请检查本机 API 设置。", 422) from None
        token = self._current.set(router)
        try:
            yield
        finally:
            self._current.reset(token)
            await router.aclose()

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        router = self._current.get()
        if router is None:
            raise LocalModelError("请从已配置 API 的分析会话发起请求。")
        return await router.chat(messages, **kwargs)

    async def aclose(self) -> None:
        # Each session owns and closes its router, including aborted SSE requests.
        return None
