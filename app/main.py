"""FastAPI 应用入口：启动时组装数据分析 Agent。"""

import asyncio
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError

from app.analysis.workspace import WorkspaceError, WorkspaceStore
from app.analytics.semantics import SemanticCatalog
from app.api.routes import (
    analysis,
    chat,
    dataset,
    document,
    health,
    model_settings,
    vision,
    workspace,
)
from app.config import Settings, get_settings
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.react_agent import ChatLLM
from app.infrastructure.llm.local_settings import LocalModelClient, LocalModelError, LocalModelStore


def build_agent(
    settings: Settings,
    *,
    exclude_tools: frozenset[str] = frozenset(),
    llm: ChatLLM | None = None,
    workspace_store: WorkspaceStore | None = None,
) -> AgentOrchestrator:
    """按配置组装 Agent。单独成函数，测试和评测脚本可以复用。

    :param exclude_tools: 不注册的工具名，评测对照实验用（例如去掉 search_glossary）
    """
    from app.analytics import AnalyticsDatabase, GlossaryStore, build_analytics_tools
    from app.analytics.prompts import build_system_prompt
    from app.core.memory.redis_session import build_session_store
    from app.core.tools.registry import ToolRegistry
    from app.demo import ensure_demo_db
    from app.infrastructure.llm.model_router import ModelConfig, ModelRouter

    # 先生成演示库：即使没有配置 Key，也可以直接用 SQL 工具查看数据
    if settings.analytics_db_url.startswith("sqlite:///"):
        ensure_demo_db(Path(settings.analytics_db_url[len("sqlite:///") :]))

    if llm is None and not settings.openai_api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY，无法调用模型")

    sessions = build_session_store(settings)

    db = AnalyticsDatabase(
        settings.analytics_db_url,
        max_rows=settings.sql_max_rows,
        timeout_seconds=settings.sql_timeout_seconds,
        schemas=settings.analytics_db_schemas,
        schema_cache_ttl_seconds=settings.schema_cache_ttl_seconds,
    )
    glossary = GlossaryStore.from_file(settings.glossary_path)
    registry = ToolRegistry(timeout_seconds=settings.sql_timeout_seconds + 5)
    for tool in build_analytics_tools(db, glossary, max_listed_tables=settings.max_listed_tables):
        if tool.name not in exclude_tools:
            registry.register(tool)
    if workspace_store is not None:
        from app.analytics.semantic_tools import GetBusinessDefinitionTool, QueryBusinessMetricTool

        for tool in (GetBusinessDefinitionTool(), QueryBusinessMetricTool(db)):
            if tool.name not in exclude_tools:
                registry.register(tool)

    llm = llm or ModelRouter(
        [
            ModelConfig(
                model_id=settings.openai_model,
                api_key=settings.openai_api_key,
                base_url=settings.openai_api_base or None,
            )
        ]
    )

    async def check_analysis_database() -> None:
        await db.execute("SELECT 1")

    async def close_resources() -> None:
        try:
            close = getattr(llm, "aclose", None)
            if close is not None:
                await close()
        finally:
            db.dispose()

    return AgentOrchestrator(
        llm,
        registry,
        sessions,
        system_prompt=build_system_prompt(db.dialect, settings.analytics_reference_date),
        max_steps=settings.agent_max_steps,
        temperature=settings.agent_temperature,
        enable_reflection=settings.enable_reflection,
        reflection_max_evidence_chars=settings.reflection_max_evidence_chars,
        readiness_check=check_analysis_database,
        close_callback=close_resources,
        schema_refresh_callback=db.refresh_schema,
        model_context=llm.session if isinstance(llm, LocalModelClient) else None,
        model_check=llm.check_ready if isinstance(llm, LocalModelClient) else None,
        workspace=workspace_store,
        semantic_catalog=SemanticCatalog(workspace_store),
    )


def configure_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level.upper())


def _start_agent(
    app: FastAPI, settings: Settings, stack: AsyncExitStack
) -> AgentOrchestrator | None:
    try:
        agent = build_agent(
            settings,
            llm=LocalModelClient(app.state.local_model_store),
            workspace_store=app.state.workspace_store,
        )
    except Exception as exc:  # noqa: BLE001 - 让服务照常启动，/chat 返回 503 说明原因
        app.state.agent_error = f"Agent 初始化失败: {exc}"
        logger.error(app.state.agent_error)
        return None
    app.state.agent = agent
    stack.push_async_callback(agent.aclose)
    return agent


async def _start_documents(app: FastAPI, settings: Settings, stack: AsyncExitStack) -> None:
    """文档库所在的 PostgreSQL；索引、导入、图片草稿都依赖它。未安装 asyncpg 或连不上时只禁用这些功能。"""
    if not settings.documents_enabled:
        return
    engine = None
    try:
        from app.infrastructure.database.session import (
            check_document_storage,
            configure_session,
            init_engine,
        )

        engine = init_engine(settings.database_url)
        await asyncio.wait_for(check_document_storage(engine), timeout=3.0)
        app.state.document_session_factory = configure_session(engine)
    except Exception as exc:  # noqa: BLE001
        app.state.document_error = "文档服务不可用，请检查数据库、表结构及可选驱动"
        logger.warning("文档库未启用（{}），/documents 接口不可用", type(exc).__name__)
        if engine is not None:
            await engine.dispose()
        return
    app.state.document_engine = engine
    app.state.document_error = None
    stack.push_async_callback(engine.dispose)


async def _start_knowledge(
    app: FastAPI,
    settings: Settings,
    agent: AgentOrchestrator | None,
    stack: AsyncExitStack,
) -> None:
    factory = app.state.document_session_factory
    if factory is None:
        return
    try:
        from app.knowledge.service import KnowledgeService
        from app.knowledge.worker import IndexWorker

        service = KnowledgeService(factory, settings, local_models=app.state.local_runtime_models)
        await asyncio.wait_for(service.check_schema(), timeout=3)
        app.state.knowledge_service = service
        if isinstance(agent, AgentOrchestrator):
            agent.enable_document_search(service)
        worker = IndexWorker(service)
        worker.start()
        stack.push_async_callback(worker.stop)
    except Exception as exc:  # noqa: BLE001
        app.state.knowledge_error = "文档索引未就绪，请执行数据库迁移"
        logger.warning("文档检索不可用: {}", type(exc).__name__)


async def _start_datasets(
    app: FastAPI,
    settings: Settings,
    agent: AgentOrchestrator | None,
    stack: AsyncExitStack,
) -> None:
    factory = app.state.document_session_factory
    if not (settings.datasets_enabled and factory is not None and settings.import_db_url):
        return
    try:
        from sqlalchemy.engine import make_url

        from app.datasets.service import DatasetService, DatasetWorker
        from app.infrastructure.database.session import init_engine

        urls = [
            make_url(value)
            for value in (settings.import_db_url, settings.database_url, settings.analytics_db_url)
        ]
        targets = {(url.host, url.port, url.database) for url in urls}
        if any(url.get_backend_name() != "postgresql" for url in urls) or len(targets) != 1:
            raise ValueError("导入、存储和分析连接必须指向同一个 PostgreSQL 数据库")
        import_engine = init_engine(settings.import_db_url)
        stack.push_async_callback(import_engine.dispose)
        service = DatasetService(
            factory,
            import_engine,
            settings,
            getattr(agent, "refresh_analytics_schema", None),
        )
        await asyncio.wait_for(service.check_schema(), timeout=3)
        app.state.dataset_service = service
        if isinstance(agent, AgentOrchestrator):
            agent.enable_dataset_tools(service)
        worker = DatasetWorker(service)
        worker.start()
        stack.push_async_callback(worker.stop)
    except Exception as exc:  # noqa: BLE001
        app.state.dataset_error = "导入未就绪，请检查独立导入账号和迁移"
        logger.warning("表格导入不可用: {}", type(exc).__name__)


async def _start_vision(app: FastAPI, settings: Settings, stack: AsyncExitStack) -> None:
    factory = app.state.document_session_factory
    if factory is None:
        return
    try:
        from app.vision.service import VisionService

        service = VisionService(
            factory,
            settings,
            local_models=app.state.local_runtime_models,
            datasets=app.state.dataset_service,
            knowledge=app.state.knowledge_service,
        )
        await asyncio.wait_for(service.check_schema(), timeout=3)
        app.state.vision_service = service
    except Exception as exc:  # noqa: BLE001
        logger.warning("图片草稿未启用，请检查识别草稿表迁移: {}", type(exc).__name__)


async def _start_history(
    app: FastAPI,
    settings: Settings,
    agent: AgentOrchestrator | None,
    stack: AsyncExitStack,
) -> None:
    if not settings.analysis_history_enabled:
        return
    archive = None
    try:
        from app.analysis.archive import AnalysisArchive

        archive = AnalysisArchive.from_settings(settings)
        await archive.check_ready()
    except Exception as exc:  # noqa: BLE001
        logger.warning("分析历史未启用: {}", type(exc).__name__)
        if archive is not None:
            await archive.aclose()
        return
    app.state.analysis_archive = archive
    stack.push_async_callback(archive.aclose)
    if isinstance(agent, AgentOrchestrator):
        agent.enable_history(archive)

    # SQL 示例库与历史共用连接；表不存在（未执行 0006 迁移）时只关闭示例功能
    try:
        from app.analysis.examples import SqlExampleStore

        examples = SqlExampleStore(archive.engine)
        await examples.check_ready()
    except Exception as exc:  # noqa: BLE001
        logger.warning("SQL 示例库未启用，请执行数据库迁移: {}", type(exc).__name__)
        return
    app.state.sql_examples = examples
    if isinstance(agent, AgentOrchestrator):
        agent.enable_sql_examples(examples)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """按依赖顺序启用各功能；可选功能失败只关闭它自己。退出时按启用的相反顺序释放资源。"""
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("启动 {} ({})", settings.app_name, settings.app_env)

    state = app.state
    state.agent = state.agent_error = None
    state.document_engine = state.document_session_factory = None
    state.document_error = "文档服务已禁用"
    state.knowledge_service = state.knowledge_error = None
    state.dataset_service = state.dataset_error = None
    state.vision_service = None
    state.analysis_archive = state.sql_examples = None
    state.local_model_store = LocalModelStore(settings.local_model_settings_path)
    from app.infrastructure.llm.local_runtime import LocalRuntimeModels

    state.local_runtime_models = LocalRuntimeModels(state.local_model_store, settings)
    state.local_model_settings_trust_proxy = settings.local_model_settings_trust_proxy
    state.workspace_store = WorkspaceStore(settings.local_workspace_path)
    state.semantic_catalog = SemanticCatalog(state.workspace_store)

    try:
        async with AsyncExitStack() as stack:
            agent = _start_agent(app, settings, stack)
            await _start_documents(app, settings, stack)
            await _start_knowledge(app, settings, agent, stack)
            await _start_datasets(app, settings, agent, stack)
            await _start_vision(app, settings, stack)
            await _start_history(app, settings, agent, stack)
            yield
    finally:
        logger.info("关闭 {}", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        description="电商数据分析 Agent：用自然语言查询业务数据库",
        debug=settings.debug,
        lifespan=lifespan,
    )

    @application.exception_handler(SQLAlchemyError)
    async def storage_unavailable(request, exc):
        logger.warning("数据库操作未完成: {}", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请稍后重试"})

    from app.core.memory.redis_session import SessionStoreUnavailable

    @application.exception_handler(SessionStoreUnavailable)
    async def session_unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    from app.analysis.archive import HistoryUnavailable

    @application.exception_handler(HistoryUnavailable)
    async def history_unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    from app.vision.types import VisionError

    @application.exception_handler(VisionError)
    async def vision_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    application.include_router(health.router, prefix=settings.api_prefix)
    application.include_router(model_settings.router, prefix=settings.api_prefix)
    from app.api.routes import business

    application.include_router(business.router, prefix=settings.api_prefix)
    application.include_router(chat.router, prefix=settings.api_prefix)
    application.include_router(analysis.router, prefix=settings.api_prefix)
    application.include_router(document.router, prefix=settings.api_prefix)
    application.include_router(dataset.router, prefix=settings.api_prefix)
    application.include_router(vision.router, prefix=settings.api_prefix)
    application.include_router(workspace.router)
    application.mount("/assets", StaticFiles(directory=workspace.WEB_ROOT), name="workspace-assets")

    @application.exception_handler(LocalModelError)
    async def local_model_error(request, exc: LocalModelError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @application.exception_handler(WorkspaceError)
    async def workspace_error(request, exc: WorkspaceError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    return application


app = create_app()
