# 第三阶段：文档索引、混合检索与引用

完成日期：2026-10-22。按用户选择，本阶段完成通用代码及离线验收，Embedding API 保持关闭。未下载或安装新依赖、中间件、镜像或本地模型，未向外部模型 API 发送文档或问题。

## 完成清单

- [x] 独立的 Embedding API 配置及兼容 `/embeddings` 的 HTTP 客户端。
- [x] 批量请求、有限并发、超时、429/5xx 重试、返回顺序及向量有效性检查。
- [x] 文档原始内容 SHA-256 去重，数据库唯一约束处理并发上传。
- [x] 上传状态与索引状态分开，索引任务持久化到 PostgreSQL。
- [x] SKIP LOCKED 领取、租约心跳、过期回收和写入 token 校验。
- [x] 分批提交向量；失败/重启后复用已完成的分块，未完成索引不参与向量检索。
- [x] BM25、pgvector 余弦检索及 RRF 排名融合，支持文档范围、模型隔离、去重与上下文预算。
- [x] Agent 文档检索工具、结构化引用、缺失/虚构引用校验，普通响应与 SSE 一致。
- [x] 状态、索引、手动重试、强制重建、删除及失败清理接口。
- [x] 全量回归、真实数据库集成、离线检索比较和数据库重启恢复验收。

待服务商确定后，再做真实 Embedding API 连通性、维度、质量、阈值及费用验收。本阶段没有将合成向量的结果当成真实模型能力。

## 主要代码

| 位置 | 职责 |
| --- | --- |
| `app/knowledge/embedding.py` | 默认禁用的独立 HTTP 客户端、重试、维度与数值检查、进程内用量 |
| `app/knowledge/service.py` | 索引入队、文档去重与删除、范围过滤、关键词/向量/混合检索 |
| `app/knowledge/worker.py` | 后台任务、租约、部分续跑、重试及过期工作者写入拦截 |
| `app/knowledge/tools.py` | `search_documents` 工具、文档作为不可信证据的系统指令 |
| `app/knowledge/citations.py` | 从实际工具结果构造引用并校验最终回答 |
| `app/api/routes/document.py` | 文档上传、索引状态、检索、重试、重建、删除接口 |
| `migrations/versions/0002_document_indexing.py` | 增量迁移：文档内容哈希/索引状态、任务表、模型检索索引 |
| `eval/phase3_offline.py` | 可重复运行的合成向量管道验收，可显式选择重启专用数据库 |

数据库已升级到 `0002_document_indexing`。保留原有业务表、原始 SQLite 文件和第二阶段账号权限；本轮只新增 `agent` 内的列、表与普通索引。应用启动依然不执行迁移。

## 处理流程

上传 TXT/Markdown → 校验、内容去重 → 解析分块并保存 → 可立即做 BM25 检索。

只有 Embedding 显式启用并配置完整时，上传才会同时创建索引任务：pending → processing → succeeded。文档的索引状态对应 pending → processing → ready；失败为 failed，尚未索引为 not_indexed。上传成功的 `status=ready` 不代表向量索引已经完成。

后台工作者按小批次请求向量并提交。任务领取使用 PostgreSQL 行锁及 SKIP LOCKED；每次领取获得新的租约 token，写入时再次核对。任务被重新领取、重建或文档删除后，旧工作者不能继续提交。取消任务会尝试重新排队；进程异常退出则依赖租约过期恢复。

相同内容上传直接复用已有文档；相同模型已完成的普通索引请求不重复消费 API。手动 retry 复用已提交分块；force 重建会清除当前目标模型的分块向量并重新生成。模型切换按 API 地址、模型、维度、版本的指纹隔离，旧模型向量保留在存储中，只有文档当前完成的目标模型参与向量检索。删除文档时统一级联清理历史向量和任务。

删除先保存 deleting 状态并从检索结果隐藏，再删除受控上传目录内的文件及数据库记录。文件占用或数据库中断时保留可重试状态，后台工作者及重复 DELETE 会继续清理。数据库中的任意外部路径、符号链接不会被当成上传文件删除。

## 检索与引用

- BM25 复用已有中英文分词，并新增批量构建，避免逐块重算全部词频统计。语料来自 PostgreSQL，重启后可以重建，无需 Redis/Milvus。
- 向量使用 pgvector 的余弦距离；先检查所选模型是否有完成的索引，没有时不请求查询向量。相似度阈值由 `RAG_MIN_SIMILARITY` 控制。
- hybrid 用 RRF 融合两路排名；向量 API 失败会显式提示并降级为 BM25。显式 vector 模式的 API 错误会返回 503。
- `document_ids` 在查询层过滤。空列表表示空范围；不存在、删除中及其他模型的向量不会被混入。
- 结果按分块内容去重。`RAG_CONTEXT_MAX_BYTES` 限制片段正文的 UTF-8 字节数；工具另外将完整 JSON 控制在 7000 字符以内，避免工具输出截断成非法 JSON。
- 引用格式为 `[D:分块UUID]`。文件名、文档 ID 和摘要来自本轮实际成功的工具结果，不能由模型自报。虚构引用、得到证据后未引用且未明确拒答的回答会标记失败，也不写入会话历史。

文档片段和文件名作为不可信资料处理，系统提示明确禁止执行其中的指令。实际工具执行仍受已有工具白名单、SQL 校验、只读事务和数据库权限约束。该实现并不意味着已通过真实模型的所有提示注入测试或证明回答中的每个语义判断都正确。

## 接口

| 方法 | 路径（统一前缀 `/api/v1`） | 行为 |
| --- | --- | --- |
| POST | `/documents/upload` | 返回文档 ID、duplicate、上传状态及独立 index_status |
| GET | `/documents` | 文档列表，包含 index_status/index_error |
| GET | `/documents/{id}/index` | 文档/任务状态、完成分块、模型指纹、错误及当前进程 API 用量 |
| POST | `/documents/{id}/index` | 索引入队；重复请求复用已存在任务 |
| POST | `/documents/{id}/index?force=true` | 强制重新生成当前模型向量 |
| POST | `/documents/{id}/retry` | 重新排队失败任务，保留可复用的已完成分块 |
| POST | `/documents/search` | query、mode、document_ids、top_k；返回片段及可引用 ID |
| DELETE | `/documents/{id}` | 完成返回 deleted；待清理返回 202/cleanup_pending |

示例检索请求：

```json
{
  "query": "净销售额应该排除哪些订单？",
  "mode": "keyword",
  "top_k": 5
}
```

默认关闭 Embedding 时，`/documents/search` 使用本地 BM25；hybrid 会返回明确的降级提示。`/chat` 和 `/chat/stream` 已接入文档工具，但使用真实聊天模型回答时会将召回片段发送给现有聊天 API；本轮没有执行这种外部调用，回答链路使用脚本模型验证。

## 验收证据

- [完整测试](tests.xml)：199 项通过，其中 153 项来自前两阶段，本阶段新增 46 项。覆盖 Mock HTTP、真实 PostgreSQL/pgvector、并发去重、失败重试、租约回收、旧任务写回拒绝、文件清理恢复和引用校验。
- [离线检索与重启验收](offline-retrieval.json)：3 份合成文档、12 个问题、3 种检索模式，共 36 次检索。专用数据库实际重启后，3 条已提交的 pending 索引任务仍存在并完成处理；最后清理自身合成数据。外部模型调用次数为 0。
- [业务查询一致性](sql-parity.json)：12/12 标准 SQL 在 SQLite 与 PostgreSQL 中结果一致。
- [实际配置启动烟测](readiness.json)：HTTP 200、核心与文档检索 ready/up、Embedding disabled。数据库迁移版本为 `0002_document_indexing`，验收后合成文档/分块/向量/任务均已清理，业务订单保留 9143 条。

离线评测中的向量由显式的规则映射产生，包含近义词和无答案测试，用来验证向量通路、排名融合及过滤逻辑。报告中的召回、MRR 和耗时不能用于判断任何真实 Embedding 模型的效果、线上性能或费用。

## 现在如何使用

本机增量迁移已经执行。重启 Python 应用即可加载本轮代码；启动本身不调用 Embedding API：

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

上传文档和本地检索可以通过 `/docs` 直接操作。旧文档也可直接关键词检索，向量索引需在服务商配置完成后单独入队。

其他已有第二阶段的环境需先执行增量迁移：

```powershell
.venv\Scripts\python.exe -m app.infrastructure.database.manage upgrade
```

API 服务商、模型、实际维度和外发/费用授权确定之后，再在本地 `.env` 或 `.env.phase2` 中配置独立的 `EMBEDDING_API_BASE`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL`、`EMBEDDING_DIMENSIONS`。确认后将 `EMBEDDING_ENABLED` 改为 true 并重启应用；聊天 Key 不会自动被复用。API 客户端要求返回标准 data/index/embedding 格式，目前维度配置必须与服务商实际输出相同。

参数默认值及完整说明见 `.env.example`。当前进程的真实有效配置已检查为 `embedding_enabled=false`、API 未配置；未自动写入任何服务商或密钥。

复现离线验收：

```powershell
$env:RUN_POSTGRES_TESTS='1'
.venv\Scripts\python.exe -m pytest tests --basetemp=.pytest_tmp_phase3_verify -q
.venv\Scripts\python.exe -m eval.phase3_offline
# 只有确认专用数据库没有其他任务使用时，再显式执行重启验收：
.venv\Scripts\python.exe -m eval.phase3_offline --restart-database
.venv\Scripts\python.exe -m eval.phase2_parity --out reports/phase3/sql-parity.json
```

## 当前边界

这是面向现有单用户本机项目的检索实现。`document_ids` 是范围过滤，不提供多租户身份鉴权。BM25 当前按请求从选定语料构建，默认最多 20000 块；更大语料需要缩小范围或另行优化。向量索引仍为精确检索，固定模型后的 ANN 索引和重排留待真实数据评测。

任务采用至少一次执行。已提交的分块可以复用，但远端已经处理、响应或本地提交尚未完成时发生中断，仍可能重复请求。API 用量是当前进程的请求/文本/token 计数，不是完整账单或持久化成本审计。

真实服务商连通性、真实语义召回和聊天回答质量尚未验收。PDF 可选依赖未安装，Excel/CSV 导入、前端管理界面、生产鉴权及会话/Trace 持久化不在本次修改内。

实现参考：[PostgreSQL 行锁与 SKIP LOCKED](https://www.postgresql.org/docs/16/sql-select.html)、[pgvector 的 Python/SQLAlchemy 适配](https://github.com/pgvector/pgvector-python)。
