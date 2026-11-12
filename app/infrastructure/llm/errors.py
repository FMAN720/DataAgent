"""Safe provider failure categories; never expose provider bodies or credentials."""

import httpx
from openai import APIConnectionError, APITimeoutError


class ModelRequestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, kind="unknown"):
        self.status_code, self.kind = status_code, kind
        self.user_message = message
        super().__init__("所有候选模型均不可用：" + message)

    @classmethod
    def from_exception(cls, error):
        if isinstance(error, cls):
            return error
        if isinstance(error, (APITimeoutError, httpx.TimeoutException, TimeoutError)):
            return cls(
                "供应商响应超时，本次请求未完成；这不能说明 Key 无效。请求可能已计费，请核对供应商用量。",
                kind="timeout",
            )
        if isinstance(error, (APIConnectionError, httpx.TransportError)):
            return cls(
                "无法连接模型服务，请检查网络、DNS 或证书；尚未进行有效的 Key 校验。",
                kind="connection",
            )
        status = getattr(error, "status_code", None)
        if type(status) is not int:
            status = None
        if status == 400:
            body = getattr(error, "body", None)
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                body = body["error"]
            param = body.get("param") if isinstance(body, dict) else None
            allowed = {
                "tool_choice",
                "thinking",
                "reasoning_effort",
                "tools",
                "temperature",
                "max_tokens",
                "max_completion_tokens",
                "response_format",
                "messages",
                "model",
            }
            label = f"，参数 {param}" if isinstance(param, str) and param in allowed else ""
            return cls(
                f"供应商拒绝请求参数（HTTP 400{label}），请检查模型接口兼容性；不能据此判断 Key 无效。",
                status_code=status,
                kind="parameters",
            )
        messages = {
            401: (
                "供应商返回 HTTP 401：身份认证未通过，请确认 Key 属于当前 API 接入地址。",
                "authentication",
            ),
            402: ("供应商返回 HTTP 402：余额或计费账户不足，请检查供应商账户余额。", "balance"),
            403: ("供应商返回 HTTP 403：当前账户或 Key 没有此模型的调用权限。", "permission"),
            404: (
                "供应商返回 HTTP 404：模型或接口路径不存在，请核对接入地址和模型名称。",
                "not_found",
            ),
            429: (
                "供应商返回 HTTP 429：请求限流或配额不足，请检查供应商配额后再试。",
                "rate_limit",
            ),
        }
        if status in messages:
            message, kind = messages[status]
            return cls(message, status_code=status, kind=kind)
        if status is not None:
            return cls(
                f"供应商返回 HTTP {status}，本次请求未完成，请检查供应商服务状态。",
                status_code=status,
                kind="provider",
            )
        return cls(
            "模型请求未完成，未获得可识别的供应商响应；请稍后重试。尚不能判断 Key 或模型是否有效。"
        )
