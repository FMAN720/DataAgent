"""
被分析业务库的只读访问层：SQL 安全校验 + 只读连接 + 超时 + 行数限制。

安全分两层，不依赖单一手段：
1. 应用层校验：只允许单条 SELECT / WITH 语句，按 token 拦截写操作关键字
   （而不是子串匹配，避免误杀 created_at 这类字段名）。
2. 数据库层只读：SQLite 用 ``mode=ro`` 打开文件并设置 ``PRAGMA query_only``；
   PostgreSQL 在只读事务中执行。生产环境还应使用只有 SELECT 权限的账号。
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool

_FORBIDDEN_KEYWORDS = {
    # 不含 replace：它也是 SQLite 的字符串函数；真正的 REPLACE INTO 会被开头必须是 SELECT/WITH 拦住
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
    "merge",
    "grant",
    "revoke",
    "attach",
    "detach",
    "pragma",
    "vacuum",
    "reindex",
    "copy",
    "call",
    "exec",
    "execute",
    "into",
}
_STRING_OR_COMMENT = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*[\s\S]*?\*/")
_WORD = re.compile(r"[a-z_]+")


class SQLValidationError(ValueError):
    """SQL 未通过安全校验。"""


def validate_readonly_sql(sql: str) -> str:
    """校验 SQL 只读且为单条语句，返回去掉结尾分号的 SQL。"""
    s = sql.strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    if not s:
        raise SQLValidationError("SQL 为空")

    # 去掉字符串字面量和注释后再检查，避免 WHERE reason = 'delete' 之类被误判
    stripped = _STRING_OR_COMMENT.sub(" ", s).lower()
    if ";" in stripped:
        raise SQLValidationError("只允许执行单条 SQL 语句")
    words = _WORD.findall(stripped)
    if not words or words[0] not in ("select", "with"):
        raise SQLValidationError("只允许 SELECT 查询（可以使用 WITH 子句）")
    bad = sorted(set(words) & _FORBIDDEN_KEYWORDS)
    if bad:
        raise SQLValidationError(f"查询包含禁止的关键字: {', '.join(bad)}")
    return s


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: int


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    comment: str = ""


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    foreign_keys: list[str] = field(default_factory=list)
    comment: str = ""
    schema: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name

    def render(self) -> str:
        """渲染为给模型阅读的表结构文本。"""
        lines = [f"表 {self.qualified_name}" + (f"：{self.comment}" if self.comment else "")]
        for c in self.columns:
            null = "" if c.nullable else " NOT NULL"
            note = f"  -- {c.comment}" if c.comment else ""
            lines.append(f"  {c.name} {c.type}{null}{note}")
        for fk in self.foreign_keys:
            lines.append(f"  外键: {fk}")
        return "\n".join(lines)


def _sqlite_column_comments(ddl: str) -> dict[str, str]:
    """从 SQLite 建表语句中提取 `-- 注释`（SQLite 本身不存列注释）。"""
    comments: dict[str, str] = {}
    for line in ddl.splitlines():
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s+[A-Za-z]+.*?--\s*(.+)$", line)
        if m:
            comments[m.group(1)] = m.group(2).strip()
    return comments


class AnalyticsDatabase:
    """业务库只读访问。所有阻塞调用在线程池中执行。"""

    def __init__(
        self,
        url: str,
        *,
        max_rows: int = 200,
        timeout_seconds: float = 15.0,
        schemas: list[str] | tuple[str, ...] = ("business", "uploads"),
        schema_cache_ttl_seconds: float = 60.0,
    ) -> None:
        self.url = url
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self.schemas = tuple(dict.fromkeys(schemas))
        if not self.schemas or any(not s or "\x00" in s for s in self.schemas):
            raise ValueError("至少指定一个有效的分析 schema")
        self.schema_cache_ttl_seconds = schema_cache_ttl_seconds
        self._schema_loaded_at = 0.0
        self._engine = self._create_engine(url)
        self._is_sqlite = self._engine.dialect.name == "sqlite"
        self._schema_cache: dict[str, TableInfo] | None = None
        self._lock = threading.Lock()

    def _create_engine(self, url: str) -> Engine:
        if url.startswith("sqlite:///"):
            path = Path(url[len("sqlite:///") :])
            if not path.exists():
                raise FileNotFoundError(f"分析库不存在: {path}")
            uri = f"file:{path.as_posix()}?mode=ro"

            def connect() -> sqlite3.Connection:
                conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
                conn.execute("PRAGMA query_only = ON")
                return conn

            # 显式用 QueuePool：sqlite:// 默认的 SingletonThreadPool 不适合线程池并发
            return create_engine("sqlite://", creator=connect, poolclass=QueuePool)

        engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
        if engine.dialect.name == "postgresql":

            @event.listens_for(engine, "begin")
            def _readonly(conn: Any) -> None:  # noqa: ANN401
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                quote = engine.dialect.identifier_preparer.quote_identifier
                path = ", ".join(["pg_catalog", *(quote(s) for s in self.schemas), "pg_temp"])
                conn.exec_driver_sql("SET LOCAL search_path TO " + path)
                conn.exec_driver_sql(
                    f"SET LOCAL statement_timeout = {max(1, int(self.timeout_seconds * 1000))}"
                )

        return engine

    @property
    def dialect(self) -> str:
        return self._engine.dialect.name

    # ------------------------------------------------------------------ schema
    def _load_schema(self, force_refresh: bool = False) -> dict[str, TableInfo]:
        with self._lock:
            if (
                not force_refresh
                and self._schema_cache is not None
                and time.monotonic() - self._schema_loaded_at < self.schema_cache_ttl_seconds
            ):
                return self._schema_cache
            tables: dict[str, TableInfo] = {}
            ddl_map: dict[str, str] = {}
            with self._engine.connect() as conn:
                insp = inspect(conn)
                if self._is_sqlite:
                    for name, ddl in conn.execute(
                        text("SELECT name, sql FROM sqlite_master WHERE type = 'table'")
                    ):
                        ddl_map[name] = ddl or ""
                for schema in [None] if self._is_sqlite else self.schemas:
                    for name in insp.get_table_names(schema=schema):
                        if name.startswith("sqlite_"):
                            continue
                        comments = _sqlite_column_comments(ddl_map.get(name, ""))
                        cols = []
                        for c in insp.get_columns(name, schema=schema):
                            cols.append(
                                ColumnInfo(
                                    name=c["name"],
                                    type=str(c["type"]),
                                    nullable=bool(c.get("nullable", True)),
                                    comment=c.get("comment") or comments.get(c["name"], ""),
                                )
                            )
                        fks = [
                            f"{', '.join(fk['constrained_columns'])} -> "
                            f"{str(fk['referred_schema']) + '.' if fk.get('referred_schema') else ''}"
                            f"{fk['referred_table']}({', '.join(fk['referred_columns'])})"
                            for fk in insp.get_foreign_keys(name, schema=schema)
                        ]
                        try:
                            tcomment = (insp.get_table_comment(name, schema=schema) or {}).get(
                                "text"
                            ) or ""
                        except NotImplementedError:
                            tcomment = ""
                        table = TableInfo(
                            name=name,
                            columns=cols,
                            foreign_keys=fks,
                            comment=tcomment,
                            schema=schema,
                        )
                        tables[table.qualified_name] = table
            self._schema_cache = tables
            self._schema_loaded_at = time.monotonic()
            return tables

    async def refresh_schema(self) -> list[TableInfo]:
        """导入/删除表后主动刷新；其它调用最多缓存一个 TTL。"""
        return list((await asyncio.to_thread(self._load_schema, True)).values())

    @staticmethod
    def _resolve_table(tables: dict[str, TableInfo], name: str) -> TableInfo | None:
        if name in tables:
            return tables[name]
        matches = [
            t
            for t in tables.values()
            if t.qualified_name.lower() == name.lower() or t.name.lower() == name.lower()
        ]
        if len(matches) > 1:
            raise SQLValidationError(f"表名有歧义，请使用完整 schema.表名: {name}")
        return matches[0] if matches else None

    async def list_tables(self) -> list[TableInfo]:
        tables = await asyncio.to_thread(self._load_schema)
        return list(tables.values())

    async def describe(self, names: list[str]) -> tuple[list[TableInfo], list[str]]:
        """返回 (找到的表, 不存在的表名)。"""
        tables = await asyncio.to_thread(self._load_schema)
        found, missing = [], []
        for n in names:
            t = self._resolve_table(tables, n.strip())
            (found.append(t) if t else missing.append(n))
        return found, missing

    async def sample_rows(self, table: str, limit: int = 3) -> QueryResult:
        tables = await asyncio.to_thread(self._load_schema)
        info = self._resolve_table(tables, table)
        if info is None:
            raise SQLValidationError(f"表不存在: {table}")
        if limit < 0:
            raise SQLValidationError("样例行数不能小于 0")
        quote = self._engine.dialect.identifier_preparer.quote_identifier
        identifier = quote(info.name)
        if info.schema:
            identifier = quote(info.schema) + "." + identifier
        return await self.execute(f"SELECT * FROM {identifier} LIMIT {int(limit)}")

    # ----------------------------------------------------------------- execute
    def _execute_sync(self, sql: str, cancel_event: threading.Event) -> QueryResult:
        start = time.perf_counter()
        with self._engine.connect() as conn:
            if self._is_sqlite:
                raw: Any = conn.connection.driver_connection
                # SQLite 没有语句级超时，用 progress handler 实现取消
                raw.set_progress_handler(lambda: 1 if cancel_event.is_set() else 0, 10_000)
            try:
                result = conn.execute(text(sql))
                columns = list(result.keys())
                fetched = result.fetchmany(self.max_rows + 1)
            finally:
                if self._is_sqlite:
                    raw.set_progress_handler(None, 0)
                conn.rollback()
        truncated = len(fetched) > self.max_rows
        rows = [list(r) for r in fetched[: self.max_rows]]
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=truncated,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )

    async def execute(self, sql: str) -> QueryResult:
        """校验后执行只读查询；超时、语法错误统一抛出带可读信息的异常。"""
        safe_sql = validate_readonly_sql(sql)
        cancel = threading.Event()
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._execute_sync, safe_sql, cancel),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            cancel.set()
            raise SQLValidationError(
                f"查询超过 {self.timeout_seconds:.0f} 秒被中止，请缩小时间范围或减少 JOIN"
            ) from exc
        except SQLAlchemyError as exc:
            # 把数据库原始报错交给模型，它通常能据此修正 SQL
            orig = getattr(exc, "orig", None) or exc
            logger.info("SQL 执行失败: {} | {}", orig, safe_sql[:300])
            raise SQLValidationError(f"SQL 执行出错: {orig}") from exc

    def _execute_many_sync(
        self,
        statements: list[str],
        cancel: threading.Event,
        validate_first: Callable[[QueryResult], None] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> list[QueryResult]:
        """One repeatable snapshot prevents changing data from falsifying reconciliation."""
        results = []
        deadline = time.perf_counter() + self.timeout_seconds
        with self._engine.connect() as conn:
            if self._is_sqlite:
                raw: Any = conn.connection.driver_connection
                raw.set_progress_handler(lambda: 1 if cancel.is_set() else 0, 10000)
            elif self.dialect == "postgresql":
                conn = conn.execution_options(isolation_level="REPEATABLE READ")
            try:
                if self._is_sqlite:
                    conn.exec_driver_sql("BEGIN")  # sqlite legacy SELECT does not begin a snapshot.
                for sql in statements:
                    if cancel.is_set():
                        raise SQLValidationError("对比查询已取消")
                    if self.dialect == "postgresql":
                        remaining_ms = max(1, int((deadline - time.perf_counter()) * 1000))
                        conn.exec_driver_sql(f"SET LOCAL statement_timeout = {remaining_ms}")
                    started = time.perf_counter()
                    result = conn.execute(text(sql), parameters or {})
                    columns, rows = list(result.keys()), result.fetchmany(self.max_rows + 1)
                    result.close()
                    results.append(
                        QueryResult(
                            columns,
                            [list(row) for row in rows[: self.max_rows]],
                            len(rows) > self.max_rows,
                            int((time.perf_counter() - started) * 1000),
                        )
                    )
                    if len(results) == 1 and validate_first is not None:
                        validate_first(results[0])
            finally:
                if self._is_sqlite:
                    raw.set_progress_handler(None, 0)
                conn.rollback()
        return results

    async def execute_many(self, statements: list[str]) -> list[QueryResult]:
        """Bounded comparison queries; the same guards and timeout as ordinary SQL apply."""
        if not 1 <= len(statements) <= 2 or any(not isinstance(sql, str) for sql in statements):
            raise SQLValidationError("对比需要一到两条只读 SQL 文本")
        return await self.execute_snapshot(statements)

    async def execute_snapshot(
        self,
        statements: list[str],
        *,
        validate_first: Callable[[QueryResult], None] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> list[QueryResult]:
        """最多四条受控查询共用快照；首条检查不通过时，不执行后续统计。"""
        if not 1 <= len(statements) <= 4 or any(not isinstance(sql, str) for sql in statements):
            raise SQLValidationError("快照需要一到四条只读 SQL 文本")
        safe = [validate_readonly_sql(sql) for sql in statements]
        cancel = threading.Event()
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._execute_many_sync, safe, cancel, validate_first, parameters)
                if parameters is not None
                else asyncio.to_thread(self._execute_many_sync, safe, cancel, validate_first),
                timeout=self.timeout_seconds,
            )
        except (TimeoutError, asyncio.CancelledError) as exc:
            cancel.set()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise SQLValidationError("对比查询超时，请缩小时间范围或维度粒度") from exc
        except SQLAlchemyError as exc:
            raise SQLValidationError(
                f"对比 SQL 执行出错: {getattr(exc, 'orig', None) or exc}"
            ) from exc

    def dispose(self) -> None:
        self._engine.dispose()
