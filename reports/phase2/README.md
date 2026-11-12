# 第二阶段：PostgreSQL 与 pgvector 数据底座

完成日期：2026-10-22。已在用户同意的范围内安装依赖、启动专用数据库、迁移现有演示数据，并完成真实数据库验收。

## 完成清单

- [x] PostgreSQL + pgvector 容器、健康检查、本机端口与持久卷。
- [x] 同一数据库中的 `business`、`uploads`、`agent` 三个 schema。
- [x] 迁移、应用存储、导入、只读分析四个独立账号与数据库权限。
- [x] Alembic 初始迁移，版本 `0001_phase2_storage`。
- [x] SQLite 演示数据完整迁移；逐表行数和 SHA-256 一致。
- [x] 分析工具支持 schema、完整表名、同名表检查、标识符转义与缓存刷新。
- [x] 标准答案查询兼容 PostgreSQL；12 条标准 SQL 跨库结果一致。
- [x] 真实上传、向量存取、权限拒绝、事务回滚、跨 schema JOIN、超时恢复和重启持久性检查。

## 当前部署

| 项目 | 实际值 |
| --- | --- |
| 镜像 | `pgvector/pgvector:0.8.7-pg16` |
| 镜像摘要 | `sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a` |
| PostgreSQL / vector 扩展 | `16.15` / `0.8.7` |
| 容器 | `data-analyst-phase2-postgres` |
| 地址 | `127.0.0.1:15432` |
| 数据库 | `analyst_phase2` |
| 持久卷 | `data-analyst-phase2-postgres-data` |
| Python 依赖 | asyncpg `0.32.0`、psycopg2-binary `2.9.13`、pgvector `0.5.1`、alembic `1.20.1` |
| 新增传递依赖 | Mako `1.4.3`、MarkupSafe `3.0.4` |

只使用新建的项目容器和卷。未调整其他项目的 MySQL、Redis 或 RabbitMQ 服务，也未安装本地 Embedding 模型、Milvus 或 PDF 解析依赖。

数据库随机密码及连接配置位于项目根目录 `.env.phase2`，已被 Git 和 Docker 构建上下文排除。应用配置依次读取 `.env`、`.env.phase2`；进程环境变量优先。原 `.env` 中的聊天模型配置仍可使用。调试表示隐藏数据库连接串与 API Key；测试调试期间出现过的本轮数据库凭据已全部轮换，最终测试报告不含密码。

## 数据与权限

| Schema | 内容 | 使用账号与权限 |
| --- | --- | --- |
| `business` | 10 张原有电商业务表 | `analyst_migrator` 管理；`analyst_reader` 只读 |
| `uploads` | 预留后续表格导入数据 | `analyst_importer` 建表和读写；未来新表自动授予 reader SELECT |
| `agent` | documents、document_chunks、chunk_embeddings、conversations、messages、trace_logs | migrator 管理；`analyst_app` 仅 CRUD |

另有 `agent.alembic_version` 记录迁移版本，应用账号无权访问。四个业务账号均无超级用户、建库、建角色、复制或绕过行级权限能力；已撤销 PUBLIC 的数据库连接、临时表和 public schema 权限。应用启动不执行 DDL，也不用迁移账号。

分析连接同时使用应用 SQL 校验、只读事务及只读账号。测试主动关闭账号的默认只读模式并绕过应用校验，仍被数据库权限拒绝。分析账号不能读取 `agent` 文档。跨 schema 查询使用 `business.orders` / `uploads.xxx` 等完整名称，允许同库 JOIN；存在同名表时工具要求明确 schema。

迁移保留全部金额的「分」单位，以 BIGINT 存储。日期暂时保留 ISO 文本，维持现有 `LIKE`、`substr` 和业务口径的兼容性；后续如采用 DATE/TIMESTAMP，需要另行迁移并适配 SQL。向量列暂不固定维度，每条记录保存模型、版本和维度，并由数据库检查实际维度；尚未选择 Embedding API，因此尚未建固定维度的 ANN 索引。

表结构缓存默认 60 秒，由 `SCHEMA_CACHE_TTL_SECONDS` 控制；程序可在导入后调用 `AnalyticsDatabase.refresh_schema()` 即时更新。

## 迁移与验证结果

原 SQLite 文件没有被覆盖或重建。迁移通过只读 SQLite 快照与 PostgreSQL 单事务执行；目标已有相同数据时跳过，有不同数据时中止，不覆盖目标。已实际测试重复导入及不同数据拒绝。

| 表 | 行数 |
| --- | ---: |
| regions | 5 |
| categories | 7 |
| products | 30 |
| users | 1,215 |
| orders | 9,143 |
| order_items | 15,286 |
| payments | 8,501 |
| refunds | 522 |
| refund_requests | 814 |
| user_events | 52,873 |
| 合计 | 88,396 |

- [数据迁移记录](import.json)：10 张表按主键排序，逐行序列化计算哈希，行数与全部字段值一致，包括 NULL、日期和整数金额。
- [查询一致性报告](parity.json)：12/12 标准 SQL 结果一致，数字容差 `0.000001`。本轮没有调用模型 API，因此这不是一轮新的模型回答准确率评测。
- [完整测试报告](tests.xml)：153 项通过，耗时 14.44 秒。其中 119 项为离线回归，34 项为显式启用的真实 PostgreSQL 集成测试。
- [单独离线回归](tests-offline.xml)：119 项通过、34 项 PostgreSQL 测试未选中，耗时 8.65 秒。
- [重启持久性报告](persistence.json)：提交合成文档和向量后实际重启专用容器，验证文档、向量及 9,143 条订单仍存在，再清理合成测试数据。

标准 SQL 有两处兼容性修正：退款率从 `SUM(status = 'refunded')` 改为 CASE 计数；客单价除法提前乘 `1.0`，避免整数除法丢失小数。现有 12 题都使用相同的可移植 SQL 运行两种数据库。

## 现在如何启动

在项目根目录执行。数据库已经部署，无需再次安装、初始化或导入：

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

若应用已在运行，需重启应用才能加载 `.env.phase2`。就绪接口为 `http://127.0.0.1:8000/api/v1/health/ready`，健康时返回 `ready`。真实上传集成测试已验证 TXT 上传、分块入库、文档列表及数据库连接，不消耗模型 API。

本次部署采用宿主机 Python 应用连接 Docker 数据库。原 `app` 容器仍是 SQLite 的轻量运行方式；不要把指向 `127.0.0.1:15432` 的宿主机 URL 直接传入 app 容器。若以后将整个应用容器化，需要安装 PostgreSQL 可选依赖并改用容器网络地址。

已有数据库再次启动只需要：

```powershell
docker compose --env-file .env.phase2 --profile postgres up -d --no-deps --pull never postgres
```

在全新环境中，确认依赖和镜像下载得到授权后，才按以下顺序部署：

```powershell
# Python 依赖清单位于 requirements-postgres.txt；镜像见上述版本。
.venv\Scripts\python.exe -m app.infrastructure.database.manage init
docker compose --env-file .env.phase2 --profile postgres up -d --no-deps --pull never --wait postgres
.venv\Scripts\python.exe -m app.infrastructure.database.manage bootstrap
.venv\Scripts\python.exe -m app.infrastructure.database.manage upgrade
.venv\Scripts\python.exe -m app.infrastructure.database.manage import-demo --out reports/phase2/import.json
```

`init` 仅创建新凭据文件，已存在时会拒绝覆盖。不能通过重新生成密码初始化现有持久卷。后续普通结构升级使用 `manage upgrade`，或 `.venv\Scripts\alembic.exe upgrade head`。如需轮换当前项目密码，可执行 `manage rotate-passwords`，随后用上述 Compose 命令更新容器配置并重启应用连接池。

验证命令：

```powershell
$env:RUN_POSTGRES_TESTS='1'
.venv\Scripts\python.exe -m pytest tests --basetemp=.pytest_tmp_phase2_verify -q
.venv\Scripts\python.exe -m eval.phase2_parity
# 下面命令会重启专用数据库容器，只应在没有其他任务使用它时执行：
.venv\Scripts\python.exe -m eval.phase2_persistence
```

若只想暂时回到 SQLite，在当前 PowerShell 覆盖配置后重启应用即可，不必删除 PostgreSQL 数据或卷：

```powershell
$env:ANALYTICS_DB_URL='sqlite:///data/ecommerce.db'
$env:DOCUMENTS_ENABLED='false'
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 下一阶段边界

当前已经具备业务数据、文档元数据/分块与向量的数据库存储能力。Embedding API 调用、文档混合检索、Excel/CSV 导入接口、自动索引构建尚未接入；`uploads` 已通过合成数据验证建表、权限和业务 JOIN。会话和 Trace 表已纳入迁移，但问答会话/Trace 的在线流程仍使用原内存实现。

重启持久性已验证，备份恢复、生产身份认证、多租户隔离、迁移大表的在线变更未包含在本阶段。本轮也未执行会删除表的 Alembic downgrade。

实现依据：[PostgreSQL schema](https://www.postgresql.org/docs/16/ddl-schemas.html)、[权限](https://www.postgresql.org/docs/16/ddl-priv.html)、[pgvector](https://github.com/pgvector/pgvector)、[SQLAlchemy 向量适配](https://github.com/pgvector/pgvector-python)、[Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html)。
