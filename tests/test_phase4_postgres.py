"""Real PostgreSQL imports; only synthetic fixtures, never call an external model."""

import asyncio
import json
import os
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import SetTableComment

from app.analytics import AnalyticsDatabase, GlossaryStore, build_analytics_tools
from app.config import Settings, get_settings
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.memory.session import InMemorySessionStore
from app.core.tools.registry import ToolRegistry
from app.datasets.parser import DatasetError
from app.datasets.service import DatasetService, DatasetWorker, ImportLeaseLost, table_definition
from app.datasets.tools import ListDatasetsTool
from app.infrastructure.database.manage import load_config
from app.infrastructure.database.models import UploadedDataset, _utcnow
from app.infrastructure.database.session import init_engine
from app.main import create_app
from tests.conftest import ScriptedLLM, reply, run, tool_call
from tests.test_phase4_parser import xlsx

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Opt in with RUN_POSTGRES_TESTS=1"
    ),
]
CSV = b"region_code,amount,external_id\nEAST,12.30,001\nNORTH,0.10,002\n"


@pytest.fixture
def env(tmp_path):
    values = load_config()
    settings = Settings(
        _env_file=None,
        database_url=values["DATABASE_URL"],
        import_db_url=values["IMPORT_DB_URL"],
        analytics_db_url=values["ANALYTICS_DB_URL"],
        import_dir=tmp_path / "imports",
        import_batch_size=2,
        embedding_enabled=False,
        embedding_api_key="",
    )
    engine = init_engine(settings.database_url, poolclass=NullPool)
    importer = init_engine(settings.import_db_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    db = AnalyticsDatabase(settings.analytics_db_url)
    owned = set()
    service = DatasetService(factory, importer, settings, db.refresh_schema)
    worker = DatasetWorker(service, dataset_ids=owned)

    async def stage(raw=CSV, filename="synthetic.csv", **kwargs):
        value = await service.stage(raw, filename, **kwargs)
        owned.add(value["id"])
        return value

    async def exists(dataset):
        async with importer.connect() as connection:
            return await connection.run_sync(
                lambda c: inspect(c).has_table(dataset["table"].split(".")[1], schema="uploads")
            )

    async def due(dataset_id):
        async with factory() as session, session.begin():
            await session.execute(
                update(UploadedDataset)
                .where(UploadedDataset.id == dataset_id)
                .values(available_at=_utcnow() - timedelta(seconds=1))
            )

    app = create_app()
    app.state.dataset_service = service
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    state = SimpleNamespace(
        service=service,
        factory=factory,
        importer=importer,
        settings=settings,
        worker=worker,
        stage=stage,
        exists=exists,
        due=due,
        db=db,
        owned=owned,
        client=client,
    )
    yield state
    client.close()

    async def cleanup():
        for dataset_id in owned:
            await service.delete(dataset_id)
        await engine.dispose()
        await importer.dispose()

    run(cleanup())
    db.dispose()


def test_preview_confirm_exact_values_join_and_delete(env):
    async def scenario():
        await env.service.check_schema()
        initial = [table.qualified_name for table in await env.db.list_tables()]
        # 本机库里可能有用户自己导入的数据；只断言本测试创建的数据集
        preexisting = {d["id"] for d in (await ListDatasetsTool(env.service).execute())["datasets"]}
        staged = await env.stage()
        assert staged["status"] == "previewed" and not await env.exists(staged)
        assert staged["preview_rows"][0] == [2, "EAST", "12.30", "001"]
        assert "storage_path" not in staged
        listed = (await ListDatasetsTool(env.service).execute())["datasets"]
        assert {d["id"] for d in listed} == preexisting
        queued = await env.service.confirm(staged["id"], display_name="区域补充金额（元）")
        assert queued["status"] == "queued" and not await env.exists(staged)
        assert await env.worker.run_once()
        ready = await env.service.get(staged["id"])
        assert ready["status"] == "ready" and ready["imported_rows"] == ready["row_count"] == 2
        assert staged["table"] in [table.qualified_name for table in await env.db.list_tables()]
        result = await env.db.execute(
            f"SELECT external_id, amount FROM {staged['table']} ORDER BY _row_number"
        )
        assert result.rows == [["001", Decimal("12.30")], ["002", Decimal("0.10")]]
        joined = await env.db.execute(
            f"SELECT r.region_name, SUM(u.amount) FROM {staged['table']} u JOIN business.regions r USING(region_code) GROUP BY r.region_name ORDER BY r.region_name"
        )
        assert len(joined.rows) == 2 and sum(row[1] for row in joined.rows) == Decimal("12.40")
        tools = await ListDatasetsTool(env.service).execute(query="区域")
        assert tools["datasets"][0]["table"] == staged["table"]
        with pytest.raises(DBAPIError, match="read-only|permission denied"):
            # Reader remains read-only even when bypassing the application's SQL validator.
            await asyncio.to_thread(lambda: _reader_write(env.db._engine, staged["table"]))
        assert await env.service.delete(staged["id"])
        assert not await env.exists(staged)
        assert [table.qualified_name for table in await env.db.list_tables()] == initial
        assert not list(env.settings.import_dir.iterdir())

    run(scenario())


def _reader_write(engine, table):
    with engine.begin() as connection:
        connection.execute(text(f"DELETE FROM {table}"))


def test_concurrent_dedup_and_idempotent_confirmation(env):
    async def scenario():
        first, second = await asyncio.gather(env.stage(), env.stage())
        assert first["id"] == second["id"] and first["duplicate"] != second["duplicate"]
        assert len(list(env.settings.import_dir.iterdir())) == 1
        await asyncio.gather(env.service.confirm(first["id"]), env.service.confirm(first["id"]))
        claims = await asyncio.gather(
            env.worker.claim(), DatasetWorker(env.service, dataset_ids=env.owned).claim()
        )
        assert sum(claim is not None for claim in claims) == 1
        await env.worker.process(next(claim for claim in claims if claim))
        assert not await env.worker.run_once()
        assert (await env.service.confirm(first["id"]))["status"] == "ready"
        assert len((await env.db.execute(f"SELECT * FROM {first['table']}")).rows) == 2

    run(scenario())


def test_api_xlsx_selected_sheet_type_review_and_errors(env):
    raw = xlsx([["ignored"], [1]], second=[["id", "amount"], ["001", 12.3], ["002", 0.1]])
    response = env.client.post(
        "/api/v1/datasets/preview",
        files={
            "file": (
                "synthetic.xlsx",
                raw,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"sheet_name": "第二表"},
    )
    assert response.status_code == 200
    staged = response.json()
    env.owned.add(staged["id"])
    assert staged["options"]["sheet_name"] == "第二表"
    url = "/api/v1/datasets/" + staged["id"]
    assert (
        env.client.post(url + "/import", json={"type_overrides": {"id": "integer"}}).status_code
        == 422
    )
    assert (
        env.client.post(
            url + "/import",
            json={"display_name": "Excel 验收", "type_overrides": {"amount": "text"}},
        ).status_code
        == 202
    )
    run(env.worker.run_once())
    assert env.client.get(url).json()["status"] == "ready"
    assert env.client.get("/api/v1/datasets?ready_only=true").json()[0]["id"] == staged["id"]
    assert run(
        env.db.execute(f"SELECT amount FROM {staged['table']} ORDER BY _row_number")
    ).rows == [["12.3"], ["0.1"]]
    assert env.client.post(url + "/retry").json()["status"] == "ready"
    assert env.client.delete(url).json()["status"] == "deleted"
    assert env.client.get(url).status_code == 404
    assert env.client.get("/api/v1/datasets/not-a-uuid").status_code == 422
    assert (
        env.client.post(
            "/api/v1/datasets/preview",
            files={"file": ("old.xls", b"bad", "application/octet-stream")},
        ).status_code
        == 415
    )
    assert (
        env.client.post(
            "/api/v1/datasets/preview", files={"file": ("bad.csv", b"a\na", "image/png")}
        ).status_code
        == 415
    )
    formula = xlsx([["a"], ["=SUM(A1:A2)"]])
    assert (
        env.client.post(
            "/api/v1/datasets/preview",
            files={"file": ("bad.xlsx", formula, "application/octet-stream")},
        ).status_code
        == 422
    )
    env.settings.import_max_bytes = 2
    assert (
        env.client.post(
            "/api/v1/datasets/preview", files={"file": ("a.csv", b"a\n123", "text/csv")}
        ).status_code
        == 413
    )
    assert not list(env.settings.import_dir.iterdir())


def test_source_change_and_path_escape_are_rejected_before_ddl(env):
    async def scenario():
        staged = await env.stage()
        path = env.settings.import_dir / (staged["id"] + ".csv")
        original = path.read_bytes()
        path.write_bytes(b"a\nchanged")
        with pytest.raises(DatasetError, match="不一致"):
            await env.service.confirm(staged["id"])
        path.write_bytes(original)
        async with env.factory() as session:
            dataset = await session.get(UploadedDataset, staged["id"])
            dataset.storage_path = str(env.settings.import_dir.parent / "outside.csv")
            with pytest.raises(DatasetError, match="路径"):
                env.service.managed_path(dataset)
        assert not await env.exists(staged)

    run(scenario())


@pytest.mark.parametrize("failure_point", ["batch", "after_commit"])
def test_rollback_or_committed_table_recovery_never_duplicates(env, failure_point):
    async def scenario():
        staged = await env.stage(b"id,value\n001,1.01\n002,2.02\n003,3.03\n")
        await env.service.confirm(staged["id"])
        original = env.worker.checkpoint
        fired = False

        async def fail_once(claim, **kwargs):
            nonlocal fired
            trigger = (
                kwargs.get("ready")
                if failure_point == "after_commit"
                else kwargs.get("progress") == 2
            )
            if trigger and not fired:
                fired = True
                raise RuntimeError("synthetic interrupted metadata commit")
            return await original(claim, **kwargs)

        env.worker.checkpoint = fail_once
        assert await env.worker.run_once()
        assert fired and (await env.service.get(staged["id"]))["status"] == "queued"
        assert await env.exists(staged) is (failure_point == "after_commit")
        await env.due(staged["id"])
        recovered = DatasetWorker(env.service, dataset_ids=env.owned)
        assert await recovered.run_once()
        state = await env.service.get(staged["id"])
        assert state["status"] == "ready" and state["attempts"] == 2
        assert (
            await env.db.execute(f"SELECT COUNT(*), SUM(value) FROM {staged['table']}")
        ).rows == [[3, Decimal("6.06")]]

    run(scenario())


def test_expired_lease_and_scope_prevent_stale_or_foreign_imports(env):
    async def scenario():
        staged = await env.stage()
        await env.service.confirm(staged["id"])
        assert await DatasetWorker(env.service, dataset_ids=[]).claim() is None
        old = await env.worker.claim()
        async with env.factory() as session, session.begin():
            await session.execute(
                update(UploadedDataset)
                .where(UploadedDataset.id == staged["id"])
                .values(locked_until=_utcnow() - timedelta(seconds=1))
            )
        current = await env.worker.claim()
        assert old[1] != current[1]
        with pytest.raises(ImportLeaseLost):
            await env.worker.process(old)
        await env.worker.process(current)
        assert (await env.service.get(staged["id"]))["status"] == "ready"

    run(scenario())


def test_delete_during_import_rolls_back_and_does_not_resurrect(env):
    async def scenario():
        staged = await env.stage(b"id\n001\n002\n003\n")
        await env.service.confirm(staged["id"])
        reached, release = asyncio.Event(), asyncio.Event()
        original = env.worker.checkpoint

        async def pause(claim, **kwargs):
            if kwargs.get("progress") == 2:
                reached.set()
                await release.wait()
            return await original(claim, **kwargs)

        env.worker.checkpoint = pause
        importing = asyncio.create_task(env.worker.run_once())
        await asyncio.wait_for(reached.wait(), 5)
        deleting = asyncio.create_task(env.service.delete(staged["id"]))
        try:
            for _ in range(100):
                if (await env.service.get(staged["id"]))["status"] == "deleting":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("delete did not invalidate the import lease")
        finally:
            release.set()
        assert await asyncio.wait_for(deleting, 5)
        await asyncio.wait_for(importing, 5)
        assert not await env.exists(staged)
        with pytest.raises(DatasetError):
            await env.service.get(staged["id"])

    run(scenario())


def test_retry_corrupt_source_and_provenance_guard(env):
    async def scenario():
        staged = await env.stage()
        await env.service.confirm(staged["id"])
        path = env.settings.import_dir / (staged["id"] + ".csv")
        original = path.read_bytes()
        path.write_bytes(b"bad")
        await env.worker.run_once()
        assert (await env.service.get(staged["id"]))["status"] == "failed" and not await env.exists(
            staged
        )
        path.write_bytes(original)
        await env.service.confirm(staged["id"], retry=True)
        await env.worker.run_once()
        table = staged["table"].split(".")[1]
        async with env.importer.begin() as connection:
            marker = await connection.run_sync(
                lambda c: inspect(c).get_table_comment(table, schema="uploads")["text"]
            )
            await connection.execute(text(f"COMMENT ON TABLE {staged['table']} IS 'unrelated'"))
        try:
            with pytest.raises(DatasetError, match="生成"):
                await env.service.delete(staged["id"])
            assert await env.exists(staged)
        finally:
            async with env.importer.begin() as connection:
                table_object = table_definition(
                    staged["id"],
                    (await env.service.get(staged["id"]))["columns"],
                    marker=json.loads(marker),
                )
                await connection.run_sync(lambda c: c.execute(SetTableComment(table_object)))
            await env.service.finish_delete(staged["id"])

    run(scenario())


def test_agent_uses_dataset_metadata_schema_and_join_with_scripted_model(env):
    async def scenario():
        staged = await env.stage()
        await env.service.confirm(staged["id"])
        await env.worker.run_once()
        tools = ToolRegistry()
        glossary = GlossaryStore.from_file(env.settings.glossary_path)
        for tool in build_analytics_tools(env.db, glossary):
            tools.register(tool)
        sql = f"SELECT SUM(u.amount) AS total FROM {staged['table']} u JOIN business.regions r USING(region_code)"

        def answer(messages, kwargs):
            result = json.loads(
                next(m["content"] for m in reversed(messages) if m["role"] == "tool")
            )
            assert Decimal(str(result["rows"][0][0])) == Decimal("12.40")
            return reply("补充金额合计 12.40 元。")

        llm = ScriptedLLM(
            [
                reply("", tool_call("list_datasets", query="synthetic")),
                reply(
                    "", tool_call("describe_tables", tables=[staged["table"], "business.regions"])
                ),
                reply("", tool_call("run_sql", sql=sql)),
                answer,
            ]
        )
        agent = AgentOrchestrator(
            llm, tools, InMemorySessionStore(), system_prompt="分析数据", enable_reflection=False
        )
        agent.enable_dataset_tools(env.service)
        result = await agent.run("关联区域表，计算上传数据的金额总和")
        assert result.success and "12.40" in result.answer
        assert [step.tool for step in result.steps] == [
            "list_datasets",
            "describe_tables",
            "run_sql",
        ]
        assert not any("import" in name or "delete" in name for name in tools.list_tool_names())

    run(scenario())
