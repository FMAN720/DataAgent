"""Dedicated local PostgreSQL review gates, exact imports and provenance; no APIs."""

import os
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.pool import NullPool

from app.analytics import AnalyticsDatabase
from app.config import Settings, get_settings
from app.datasets.service import DatasetService, DatasetWorker
from app.datasets.tools import ListDatasetsTool
from app.infrastructure.database.manage import load_config
from app.infrastructure.database.models import VisionDraft, _utcnow
from app.infrastructure.database.session import init_engine
from app.knowledge.service import KnowledgeService
from app.main import create_app
from app.vision.service import VisionService
from app.vision.types import VisionError
from tests.conftest import run
from tests.test_phase5_documents import pdf_bytes
from tests.test_vision import RESULT, png

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Explicit local PostgreSQL opt-in"
    ),
]


class Provider:
    model = "synthetic-vision"
    profile = "synthetic-profile"

    def __init__(self):
        self.calls = 0
        self.fail = False

    async def extract(self, raw):
        self.calls += 1
        if self.fail and self.calls == 2:
            raise VisionError("synthetic failure", 502)
        return deepcopy(RESULT), {"requests": 1, "tokens": {}, "usage_complete": False}


@pytest.fixture
def env(tmp_path):
    values = load_config()
    settings = Settings(
        _env_file=None,
        database_url=values["DATABASE_URL"],
        import_db_url=values["IMPORT_DB_URL"],
        analytics_db_url=values["ANALYTICS_DB_URL"],
        vision_dir=tmp_path / "vision",
        upload_dir=tmp_path / "uploads",
        import_dir=tmp_path / "imports",
        embedding_enabled=False,
    )
    engine = init_engine(settings.database_url, poolclass=NullPool)
    importer = init_engine(settings.import_db_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    database = AnalyticsDatabase(settings.analytics_db_url)
    datasets = DatasetService(factory, importer, settings, database.refresh_schema)
    knowledge = KnowledgeService(factory, settings)
    provider = Provider()
    vision = VisionService(
        factory, settings, provider=provider, datasets=datasets, knowledge=knowledge
    )
    owned = []
    docs = []
    tables = []
    # 本机库里可能有用户自己导入的数据；测试只断言自己创建的资源
    preexisting = {d["id"] for d in run(datasets.list(ready_only=True))}

    async def stage(raw=None, name="synthetic.png"):
        value = await vision.stage(
            raw or png(), name, "application/pdf" if name.endswith(".pdf") else "image/png"
        )
        owned.append(value["id"])
        return value

    app = create_app()
    app.state.vision_service = vision
    app.state.knowledge_service = knowledge
    app.state.dataset_service = datasets
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    yield SimpleNamespace(
        vision=vision,
        provider=provider,
        stage=stage,
        client=client,
        settings=settings,
        factory=factory,
        datasets=datasets,
        knowledge=knowledge,
        database=database,
        owned=owned,
        docs=docs,
        tables=tables,
        preexisting=preexisting,
    )
    client.close()

    async def cleanup():
        for identifier in tables:
            await datasets.delete(identifier)
        for identifier in docs:
            await knowledge.delete_document(identifier)
        async with factory() as session, session.begin():
            await session.execute(delete(VisionDraft).where(VisionDraft.id.in_(owned)))
        await importer.dispose()
        await engine.dispose()

    run(cleanup())
    database.dispose()


def _ready_ids(env):
    return {d["id"] for d in run(env.datasets.list(ready_only=True))}


def test_local_preview_does_not_create_retrievable_or_queryable_data(env):
    value = run(env.stage())
    assert value["status"] == "previewed" and env.provider.calls == 0
    assert run(env.vision.image(value["id"], 1)).startswith(b"\x89PNG")
    assert not run(env.knowledge.search("合成", mode="keyword")).hits
    assert _ready_ids(env) == env.preexisting
    fresh = VisionService(env.factory, env.settings)
    assert run(fresh.get(value["id"]))["content_hash"] == value["content_hash"]


def test_consent_and_revision_gate_before_remote_request(env):
    value = run(env.stage())
    for version, consent in ((value["revision"], False), (99, True)):
        with pytest.raises(VisionError):
            run(env.vision.recognize(value["id"], version, [1], consent))
    assert env.provider.calls == 0
    result = run(env.vision.recognize(value["id"], 0, [1], True))
    with pytest.raises(VisionError):
        run(env.vision.table(value["id"], result["revision"], 1, 0, False))
    assert _ready_ids(env) == env.preexisting


def test_successful_pages_are_reused_after_a_partial_failure(env):
    value = run(env.stage(pdf_bytes("One", "Two"), "synthetic.pdf"))
    env.provider.fail = True
    with pytest.raises(VisionError):
        run(env.vision.recognize(value["id"], 0, [1, 2], True))
    failed = run(env.vision.get(value["id"]))
    assert failed["status"] == "failed" and list(failed["pages"]) == ["1"]
    assert len(failed["attempts"]) == 2
    env.provider.fail = False
    complete = run(env.vision.recognize(value["id"], failed["revision"], [1, 2], True))
    assert env.provider.calls == 3 and set(complete["pages"]) == {"1", "2"}
    run(env.vision.recognize(value["id"], complete["revision"], [1, 2], True))
    assert env.provider.calls == 3


def test_manual_review_exact_dataset_import_and_source_are_preserved(env):
    value = run(env.stage())
    value = run(env.vision.edit(value["id"], 0, 1, RESULT, manual=True))
    result = run(env.vision.table(value["id"], value["revision"], 1, 0, True))
    data = result["dataset"]
    env.tables.append(data["id"])
    assert data["status"] == "previewed" and data["preview_rows"][0][1:] == ["001", "12.30"]
    assert (
        data["options"]["vision_source"]["sha256"] == value["content_hash"]
        and env.provider.calls == 0
    )
    assert {
        d["id"] for d in run(ListDatasetsTool(env.datasets).execute())["datasets"]
    } == env.preexisting
    run(env.datasets.confirm(data["id"]))
    run(DatasetWorker(env.datasets, dataset_ids={data["id"]}).run_once())
    tool = next(
        d
        for d in run(ListDatasetsTool(env.datasets).execute())["datasets"]
        if d["id"] == data["id"]
    )
    assert tool["vision_source"]["human_confirmed"] and tool["vision_source"]["page_numbers"] == [1]
    rows = run(
        env.database.execute("SELECT * FROM " + data["table"] + " ORDER BY _row_number")
    ).rows
    assert str(rows[0][1]) == "001" and str(rows[0][2]) == "12.30" and rows[1][2] is None


def test_edited_document_keeps_original_page_without_implicit_embedding(env):
    value = run(env.stage(pdf_bytes("First", "Second"), "synthetic.pdf"))
    result = {**RESULT, "text": "人工核对：第二页合成净销售额资料"}
    value = run(env.vision.edit(value["id"], 0, 2, result, manual=True))
    published = run(env.vision.document(value["id"], value["revision"], [2], True))
    env.docs.append(published["document_id"])
    detail = run(env.knowledge.details(published["document_id"]))
    assert {chunk["page_number"] for chunk in detail["chunks"]} == {2}
    assert (
        detail["metadata"]["vision_source"]["partial_source"]
        and detail["index_status"] == "not_indexed"
    )
    assert (
        env.provider.calls == 0
        and run(env.knowledge.search("第二页合成净销售额", mode="keyword")).hits
    )
    duplicate = run(env.vision.document(value["id"], published["draft"]["revision"], [2], True))
    assert duplicate["document_id"] == published["document_id"]


def test_changed_source_cannot_be_published_and_stale_edits_do_not_replace_raw(env):
    value = run(env.stage())
    recognized = run(env.vision.recognize(value["id"], 0, [1], True))
    edited = {**RESULT, "text": "校正后的文字"}
    saved = run(env.vision.edit(value["id"], recognized["revision"], 1, edited))
    assert (
        saved["pages"]["1"]["raw"]["text"] == RESULT["text"]
        and saved["pages"]["1"]["edited"]["text"] == edited["text"]
    )
    with pytest.raises(VisionError):
        run(env.vision.edit(value["id"], recognized["revision"], 1, RESULT))
    path = env.settings.vision_dir / (value["id"] + ".png")
    path.write_bytes(b"changed")
    with pytest.raises(VisionError):
        run(env.vision.table(value["id"], saved["revision"], 1, 0, True))


def test_expired_attempt_is_fenced_without_auto_resubmission(env):
    value = run(env.stage())

    async def interrupt():
        async with env.factory() as session, session.begin():
            item = await session.get(VisionDraft, value["id"])
            item.status = "recognizing"
            item.lease_token = "00000000-0000-0000-0000-000000000001"
            item.locked_until = _utcnow() - timedelta(seconds=1)

    run(interrupt())
    recovered = run(env.vision.get(value["id"]))
    assert recovered["status"] == "failed" and env.provider.calls == 0
    with pytest.raises(VisionError):
        run(env.vision._progress(value["id"], "00000000-0000-0000-0000-000000000001", done=True))


def test_http_gate_schema_source_response_and_date_parsing(env):
    response = env.client.post(
        "/api/v1/vision/drafts/upload", files={"file": ("synthetic.png", png(), "image/png")}
    )
    assert response.status_code == 200
    value = response.json()
    env.owned.append(value["id"])
    base = "/api/v1/vision/drafts/" + value["id"]
    assert "storage_path" not in value
    assert (
        env.client.post(base + "/recognize", json={"revision": 0, "pages": [1]}).status_code == 422
    )
    assert env.provider.calls == 0
    image = env.client.get(base + "/pages/1/image")
    assert image.status_code == 200 and image.headers["cache-control"] == "no-store"
    assert (
        env.client.put(
            base + "/pages/1", json={"revision": 0, "manual": True, "result": RESULT}
        ).status_code
        == 200
    )
    value = env.client.get(base).json()
    doc = env.client.post(
        base + "/document",
        json={
            "revision": value["revision"],
            "pages": [1],
            "reviewed": True,
            "document_date": "2025-12-01",
        },
    )
    assert doc.status_code == 200, doc.text
    env.docs.append(doc.json()["document_id"])
    assert run(env.knowledge.details(doc.json()["document_id"]))["document_date"] == "2025-12-01"
