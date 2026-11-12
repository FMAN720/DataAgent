"""API 请求与响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.knowledge.types import DocumentCitation
from app.models.enums import AgentMode, ReflectionStatus


class AgentChatRequest(BaseModel):
    """数据分析 Agent 对话请求。多轮对话靠 session_id 关联，历史由服务端保存。"""

    question: str = Field(min_length=1, max_length=2000, description="用户问题")
    session_id: str | None = Field(default=None, description="会话 ID；不传则新建会话")
    mode: AgentMode = Field(
        default=AgentMode.REACT, description="react：直接执行；plan_execute：先规划再执行"
    )
    business_profile_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    resume_task_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")


class ToolStepOut(BaseModel):
    step: int
    tool: str
    arguments: dict[str, Any]
    ok: bool
    output: str
    duration_ms: int


class ReflectionOut(BaseModel):
    status: ReflectionStatus
    passed: bool | None
    quality_score: int | None
    issues: list[str] = Field(default_factory=list)
    summary: str = ""
    error: str | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    evidence_complete: bool = True


class AgentChatResponse(BaseModel):
    """数据分析 Agent 响应：答案 + 完整执行轨迹，便于前端展示「Agent 做了什么」。"""

    answer: str
    success: bool
    session_id: str
    trace_id: str
    mode: AgentMode
    steps: list[ToolStepOut] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)
    reflection: ReflectionOut | None = None
    reflection_status: ReflectionStatus = ReflectionStatus.SKIPPED
    revised: bool = Field(default=False, description="是否因审查不通过而修正过答案")
    llm_calls: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    duration_ms: int = 0
    error: str | None = None
    citations: list[DocumentCitation] = Field(default_factory=list)
    citation_errors: list[str] = Field(default_factory=list)
    analysis: dict[str, Any] = Field(default_factory=dict)
    needs_clarification: bool = False
    usage_breakdown: dict[str, Any] = Field(default_factory=dict)
    execution_llm_calls: int = 0
    question: str = ""
    tables: list[dict[str, Any]] = Field(default_factory=list)
    charts: list[dict[str, Any]] = Field(default_factory=list)
    analysis_state: dict[str, Any] = Field(default_factory=dict)
    archive_status: str = "disabled"
    archive_error: str | None = None
    context_applied: bool = False
    created_at: str | None = None
    validation: dict[str, Any] = Field(default_factory=dict)
    business_profile: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None
    task_attempt: int = 1


class DocumentUploadResponse(BaseModel):
    """文档上传响应。"""

    document_id: str
    filename: str
    status: str
    chunk_count: int = 0
    message: str = "ok"
    duplicate: bool = False
    index_status: str = "not_indexed"


class DocumentInfo(BaseModel):
    """文档列表项。"""

    id: str
    filename: str
    mime_type: str | None = None
    status: str
    created_at: str | None = None
    index_status: str = "not_indexed"
    index_error: str | None = None
    document_date: str | None = None
    document_type: str = "reference"
    chunk_count: int = 0


class RetrievalResult(BaseModel):
    """检索单条结果。"""

    id: str
    content: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str = "keyword"
