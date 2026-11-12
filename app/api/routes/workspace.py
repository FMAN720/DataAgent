"""Same-origin workspace; no framework runtime, CDN, or automatic model calls."""

import importlib.util
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from app.config import Settings, get_settings

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
router = APIRouter(tags=["workspace"])


@router.get("/", include_in_schema=False)
async def workspace():
    return FileResponse(
        WEB_ROOT / "index.html",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        },
    )


@router.get("/workspace-config", include_in_schema=False)
async def workspace_config(request: Request, settings: Settings = Depends(get_settings)):
    model_store = getattr(request.app.state, "local_model_store", None)
    runtime = getattr(request.app.state, "local_runtime_models", None)
    return {
        "api_prefix": settings.api_prefix,
        "model_configuration_required": True,
        "model_configured": bool(model_store and model_store.public()["configured"]),
        "business_profiles_available": getattr(request.app.state, "semantic_catalog", None)
        is not None,
        "analysis_tasks_available": getattr(request.app.state, "workspace_store", None) is not None,
        "embedding_enabled": bool(runtime and runtime.available("embedding")),
        "pdf_available": importlib.util.find_spec("pypdf") is not None,
        "xlsx_available": importlib.util.find_spec("openpyxl") is not None,
        "upload_max_bytes": settings.upload_max_bytes,
        "import_max_bytes": settings.import_max_bytes,
        "datasets_available": getattr(request.app.state, "dataset_service", None) is not None,
        "analysis_history_available": getattr(request.app.state, "analysis_archive", None)
        is not None,
        "sql_examples_available": getattr(request.app.state, "sql_examples", None) is not None,
        "documents_available": getattr(request.app.state, "knowledge_service", None) is not None,
        "vision_available": getattr(request.app.state, "vision_service", None) is not None,
        "vision_recognition_available": bool(
            getattr(request.app.state, "vision_service", None)
            and runtime
            and runtime.available("vision")
        ),
        "vision_model": model_store.public("vision")["model"] if model_store else "",
        "vision_max_bytes": settings.vision_max_bytes,
        "vision_max_pdf_pages": settings.vision_max_pdf_pages,
        "vision_max_pages_per_request": settings.vision_max_pages_per_request,
    }
