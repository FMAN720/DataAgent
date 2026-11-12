"""Actual storage/filter/source contracts, driven by synthetic local documents."""

from datetime import date

from app.core.agent.orchestrator import AgentOrchestrator
from app.core.memory.session import InMemorySessionStore
from app.infrastructure.database.models import Document
from app.knowledge.types import EmbeddingError
from tests.conftest import ScriptedLLM, reply, run, tool_call
from tests.test_phase3_postgres import env, pytestmark  # noqa: F401 - 复用 fixture 与 postgres 标记
from tests.test_phase5_documents import docx_bytes, pdf_bytes


def upload(env, raw, filename, mime, **form):
    response = env.client.post(
        "/api/v1/documents/upload", files={"file": (filename, raw, mime)}, data=form
    )
    assert response.status_code == 200, response.text
    env.owned.add(response.json()["document_id"])
    return response.json()["document_id"]


def test_date_and_type_filters_apply_to_keyword_vector_and_hybrid(env):
    async def scenario():
        ids = []
        for day, kind in (
            (date(2024, 12, 31), "weekly_report"),
            (date(2025, 12, 31), "weekly_report"),
            (None, "weekly_report"),
            (date(2025, 12, 31), "review"),
        ):
            doc = await env.add([f"净销售额资料 {day} {kind}"])
            ids.append(doc)
            async with env.factory() as session, session.begin():
                row = await session.get(Document, doc)
                row.document_date, row.document_type = day, kind
            await env.enqueue(doc)
            await env.worker.run_once()
        for mode in ("keyword", "vector", "hybrid"):
            result = await env.service.search(
                "净销售额",
                mode=mode,
                document_ids=ids,
                date_from="2025-12-31",
                date_to="2025-12-31",
                document_type="weekly_report",
            )
            assert [hit.document_id for hit in result.hits] == [ids[1]]
            assert result.hits[0].document_date == date(2025, 12, 31)
            assert result.hits[0].document_type == "weekly_report"

    run(scenario())


def test_docx_metadata_edit_and_scoped_source_detail(env):
    doc = upload(
        env,
        docx_bytes(),
        "policy.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        document_date="2025-12-31",
        document_type="review",
    )
    url = "/api/v1/documents/" + doc
    detail = env.client.get(url).json()
    assert detail["document_date"] == "2025-12-31" and detail["document_type"] == "review"
    assert "storage_path" not in detail and detail["chunks"][1]["section"] == "活动规则"
    assert detail["chunks"][1]["page_number"] is None
    chunk = detail["chunks"][1]["id"]
    assert env.client.get(url, params={"chunk_id": chunk}).json()["chunks"][0]["id"] == chunk
    foreign = upload(env, b"# Other\nforeign source", "other.md", "text/markdown")
    foreign_chunk = env.client.get("/api/v1/documents/" + foreign).json()["chunks"][0]["id"]
    assert env.client.get(url, params={"chunk_id": foreign_chunk}).status_code == 404
    assert env.client.get(url, params={"offset": 1, "limit": 1}).json()["has_more"]
    assert (
        env.client.patch(url, json={"document_date": None, "document_type": "support"}).status_code
        == 200
    )
    assert env.client.get(url).json()["document_date"] is None
    assert (
        env.client.get(
            "/api/v1/documents", params={"date_from": "2025-01-01", "date_to": "2025-12-31"}
        ).json()
        == []
    )
    assert (
        env.client.get(
            "/api/v1/documents", params={"date_from": "2025-12-31", "date_to": "2025-01-01"}
        ).status_code
        == 422
    )
    assert env.client.patch(url, json={"document_type": "execute"}).status_code == 422


def test_pdf_source_and_citation_keep_page_and_date(env, registry):
    doc = upload(
        env,
        pdf_bytes("First page rules", "Second page evidence"),
        "policy.pdf",
        "application/pdf",
        document_date="2025-10-01",
        document_type="reference",
    )
    detail = env.client.get("/api/v1/documents/" + doc).json()
    assert [chunk["page_number"] for chunk in detail["chunks"]] == [1, 2]
    reference = detail["chunks"][1]["reference"]

    async def scenario():
        llm = ScriptedLLM(
            [
                reply(
                    "",
                    tool_call(
                        "search_documents",
                        query="Second page evidence",
                        document_ids=[doc],
                        date_from="2025-10-01",
                        date_to="2025-10-01",
                    ),
                ),
                reply(f"第二页记载了相应证据，见 policy.pdf [{reference}]"),
            ]
        )
        agent = AgentOrchestrator(
            llm, registry, InMemorySessionStore(), system_prompt="分析资料", enable_reflection=False
        )
        agent.enable_document_search(env.service)
        result = await agent.run("第二页写了什么？")
        assert result.success and result.citations[0].page_number == 2
        assert result.citations[0].document_date == date(2025, 10, 1)

    run(scenario())


def test_low_similarity_cannot_bypass_gate_via_keyword_but_api_outage_is_explicit(env):
    async def scenario():
        doc = await env.add(["净销售额明确的口径证据"])
        await env.enqueue(doc)
        await env.worker.run_once()
        original = env.service.vector_search

        async def low(*args):
            return []

        env.service.vector_search = low
        result = await env.service.search("净销售额", mode="hybrid", document_ids=[doc])
        assert not result.hits and result.mode == "hybrid" and "阈值" in result.warnings[0]
        assert (await env.service.search("净销售额", mode="keyword", document_ids=[doc])).hits

        async def unavailable(*args):
            raise EmbeddingError("synthetic_timeout", retryable=True)

        env.service.vector_search = unavailable
        fallback = await env.service.search("净销售额", mode="hybrid", document_ids=[doc])
        assert (
            fallback.hits
            and fallback.mode == "keyword"
            and "synthetic_timeout" in fallback.warnings[0]
        )
        env.service.vector_search = original

    run(scenario())


def test_upload_invalid_docx_leaves_no_file_or_metadata(env):
    before = env.client.get("/api/v1/documents").json()
    response = env.client.post(
        "/api/v1/documents/upload",
        files={"file": ("bad.docx", b"PK\x03\x04broken", "application/octet-stream")},
    )
    assert response.status_code == 422
    assert env.client.get("/api/v1/documents").json() == before
