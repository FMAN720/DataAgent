"""应用配置：基于 pydantic-settings，支持环境变量与 .env 文件。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.infrastructure.llm.local_settings import default_model_settings_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.phase2"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="DataAgent", description="服务名称")
    app_env: str = Field(default="development", description="运行环境")
    debug: bool = Field(default=False, description="调试模式")
    api_prefix: str = Field(default="/api/v1", description="API 前缀")
    host: str = Field(default="0.0.0.0", description="监听地址")
    port: int = Field(default=8000, description="监听端口")

    openai_api_key: str = Field(default="", repr=False, description="OpenAI API Key")
    openai_api_base: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI 兼容 API Base（需支持 function calling）",
    )
    openai_model: str = Field(default="gpt-4o-mini", description="默认对话模型")
    local_model_settings_path: Path = Field(
        default_factory=default_model_settings_path,
        repr=False,
        description="当前操作系统用户的本机模型配置；工作台不使用 .env 的聊天 Key",
    )
    local_model_settings_trust_proxy: bool = Field(
        default=False, description="仅本机端口映射的 Docker 允许私网桥接请求；不可用于公网部署"
    )
    local_workspace_path: Path = Field(
        default_factory=lambda: default_model_settings_path().with_name("workspace.sqlite3"),
        repr=False,
        description="本机业务口径版本与任务记录，不含 API Key",
    )

    # ---- 数据分析 Agent ----
    analytics_db_url: str = Field(
        repr=False,
        default=f"sqlite:///{(PROJECT_ROOT / 'data' / 'ecommerce.db').as_posix()}",
        description="被分析的业务库（同步 SQLAlchemy URL）。SQLite 演示库不存在时启动自动生成",
    )
    analytics_reference_date: str = Field(
        default="2025-12-31",
        pattern=r"^(today|\d{4}-\d{2}-\d{2})$",
        description="视为「今天」的日期；演示数据覆盖 2025 全年。接入真实业务库时设为 today，每次请求使用当天日期",
    )
    analytics_db_schemas: list[str] = Field(
        default=["business", "uploads"],
        description="PostgreSQL 分析表的 schema 白名单（SQLite 忽略）",
    )
    schema_cache_ttl_seconds: float = Field(
        default=60, ge=0, description="表结构缓存秒数，0 表示每次刷新"
    )
    glossary_path: str = Field(
        default=str(PROJECT_ROOT / "app" / "demo" / "glossary.md"),
        description="业务口径文档（Markdown，按 ## 标题切块后建 BM25 索引）",
    )
    sql_max_rows: int = Field(default=200, description="单次查询返回给模型的最大行数")
    max_listed_tables: int = Field(
        default=30, ge=1, le=500, description="list_tables 最多返回的表数；超过时按问题检索相关表"
    )
    sql_timeout_seconds: float = Field(default=15.0, description="单条 SQL 超时")
    agent_max_steps: int = Field(default=12, description="单轮对话最多调用模型的次数")
    agent_temperature: float = Field(default=0.0, description="Agent 推理温度")
    enable_reflection: bool = Field(default=True, description="是否对答案做数据一致性审查")
    reflection_max_evidence_chars: int = Field(
        default=24000,
        ge=4000,
        le=64000,
        description="逐项审查证据的字符预算；超出时明确返回未完成，不静默切断记录",
    )
    session_backend: Literal["memory", "redis"] = "memory"
    session_max_turns: int = Field(default=10, ge=1, le=100, description="每个会话保留的历史轮数")
    session_ttl_seconds: int = Field(default=3600, ge=1, description="最后一次成功写入后的有效秒数")
    session_max_sessions: int = Field(default=1000, ge=1, description="仅进程内存储的会话容量")
    redis_url: str = Field(
        default="redis://127.0.0.1:16379/0", repr=False, description="会话 Redis URL"
    )
    redis_key_prefix: str = Field(default="analyst:session:v1:", min_length=1, pattern=r"^\S+$")
    redis_timeout_seconds: float = Field(default=1.0, gt=0, le=3)
    redis_max_connections: int = Field(default=20, ge=1, le=200)
    analysis_history_enabled: bool = Field(
        default=True, description="使用 PostgreSQL 保存分析记录和可信条件"
    )

    # 图片/扫描件先留在本机草稿；识别与向量索引分别征得用户确认。
    vision_enabled: bool = False
    vision_api_base: str = "https://api.siliconflow.cn/v1"
    vision_api_key: str = Field(default="", repr=False)
    vision_model: str = ""
    vision_timeout_seconds: float = Field(default=90, gt=0, le=180)
    vision_max_output_tokens: int = Field(default=4096, ge=256, le=8192)
    vision_dir: Path = Field(default=PROJECT_ROOT / "data" / "vision")
    vision_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    vision_max_pixels: int = Field(default=16_000_000, ge=10000, le=40_000_000)
    vision_max_pdf_pages: int = Field(default=20, ge=1, le=100)
    vision_max_pages_per_request: int = Field(default=5, ge=1, le=10)

    # ---- 文档上传 API（可选，需要 PostgreSQL）----
    documents_enabled: bool = Field(default=True, description="启用可选文档功能")
    upload_dir: Path = Field(default=PROJECT_ROOT / "uploads", description="上传文件存储目录")
    upload_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0, description="单文件上传大小上限")
    database_url: str = Field(
        repr=False,
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/agent_db",
        description="SQLAlchemy 异步数据库 URL（推荐 postgresql+asyncpg）",
    )
    # 默认禁用外部向量调用；独立于聊天模型，不复用聊天 Key。
    embedding_enabled: bool = False
    embedding_api_base: str = ""
    embedding_api_key: str = Field(default="", repr=False)
    embedding_model: str = ""
    embedding_dimensions: int | None = Field(default=None, ge=1)
    embedding_revision: str = "v1"
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    embedding_timeout_seconds: float = Field(default=30, gt=0, le=120)
    embedding_max_retries: int = Field(default=2, ge=0, le=5)
    embedding_concurrency: int = Field(default=2, ge=1, le=16)
    index_poll_seconds: float = Field(default=2, gt=0)
    index_lease_seconds: float = Field(default=180, ge=10)
    index_max_attempts: int = Field(default=3, ge=1, le=10)
    index_retry_seconds: float = Field(default=30, ge=0)
    rag_top_k: int = Field(default=5, ge=1, le=10)
    rag_context_max_bytes: int = Field(default=4000, ge=128, le=16000)
    rag_max_corpus_chunks: int = Field(default=20000, ge=1)
    rag_min_similarity: float = Field(default=0.35, ge=-1, le=1)
    rag_bge_m3_min_similarity: float = Field(default=0.6, ge=-1, le=1)
    rag_model_min_similarity: dict[str, float] = Field(
        default_factory=dict,
        description='按 Embedding 模型名单独设置的相似度阈值，优先于上面两项，例如 {"BAAI/bge-m3": 0.6}',
    )
    rag_hybrid_require_vector_match: bool = True
    datasets_enabled: bool = True
    import_db_url: str = Field(
        default="", repr=False, description="仅有 uploads 写权限的导入账号 URL"
    )
    import_dir: Path = Field(default=PROJECT_ROOT / "data" / "imports")
    import_max_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    import_max_rows: int = Field(default=50000, ge=1)
    import_max_columns: int = Field(default=100, ge=1, le=1000)
    import_max_cell_chars: int = Field(default=16000, ge=1)
    import_xlsx_max_uncompressed_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    import_poll_seconds: float = Field(default=2, gt=0)
    import_lease_seconds: float = Field(default=120, ge=10)
    import_batch_size: int = Field(default=500, ge=1, le=5000)
    import_max_attempts: int = Field(default=3, ge=1, le=10)

    log_level: str = Field(default="INFO", description="日志级别")

    def min_similarity_for(self, model: str) -> float:
        """向量检索的相似度阈值：先看按模型的配置，BGE-M3 有单独校准的默认值，其余用通用阈值。"""
        if model in self.rag_model_min_similarity:
            return self.rag_model_min_similarity[model]
        if model == "BAAI/bge-m3":
            return self.rag_bge_m3_min_similarity
        return self.rag_min_similarity


@lru_cache
def get_settings() -> Settings:
    return Settings()
