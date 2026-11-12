# DataAgent

[![CI](https://github.com/FMAN720/DataAgent/actions/workflows/ci.yml/badge.svg)](https://github.com/FMAN720/DataAgent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**在本机用自然语言分析数据库、表格和业务文档，并核对答案背后的查询与证据。**

DataAgent 是基于 FastAPI 和原生 function calling 的数据分析工作台，包含只读 SQL、版本化业务口径、文档混合检索、图片人工核对、分析任务恢复和结果导出。内置可重建的合成电商数据，可以从 SQLite 最小模式开始。

[快速开始](#快速开始最小模式) · [使用指南](docs/user-guide/README.md) · [贡献指南](CONTRIBUTING.md) · [项目来源](docs/OPEN_SOURCE.md)

用自然语言提问，Agent 查询数据并给出答案；工具记录、程序校验、资料来源和导出在“分析详情”中按需查看。每位使用者在自己的电脑配置自己的模型 API。除了内置的电商演示库，还可以分析上传的 CSV/XLSX 表格、业务文档，以及经人工核对的图片和扫描件。

本轮核心优化增加了本机版本化业务口径、数字与单位的程序校验，以及可查看阶段、手动恢复的分析任务。见[核心分析指南](docs/core-analysis.md)和[本轮验收](reports/core-analysis-upgrade/README.md)。

执行过程示意（数字为演示库的真实结果）：

```
问：12 月相比 11 月，哪个品类销售额环比下降最多？

Agent：
  search_glossary("品类销售额")      → 口径：SUM(order_items.item_amount)，有效订单，排除测试账号
  describe_tables(["orders", ...])  → 字段、注释（单位：分）、外键、样例行
  run_sql(...)                      → 报错 no such column: category
  run_sql(...)                      → 修正后返回 7 个品类两个月的数据
  （审查：数字与查询结果一致，口径正确）

答：家用电器下降最多，从 234,927.75 元降到 84,642.95 元，环比 -64.0%。
   口径：品类销售额（订单明细成交额），已排除测试账号，按下单时间统计。
```

## 架构

```
浏览器工作台 /  ─┐
POST /chat(/stream) ─► AgentOrchestrator
                │ 1. 读取会话历史（内存或 Redis）和 PostgreSQL 中已确认的分析条件
                │ 2. [plan_execute 模式] PlannerAgent 拆分析步骤，注入 system prompt
                │ 3. ReActAgent：function calling 循环
                │       LLM ──tool_calls──► ToolRegistry ──► 表结构 / 只读 SQL / 口径检索
                │        ▲                                   固定指标 / 数据体检 / 周期对比
                │        └──── role=tool 结果 ◄────────────── 文档检索 / 上传表格 / 计算器
                │ 4. ReflectionAgent：把答案和本轮全部工具证据（E 编号）交给审查模型逐项核对
                │ 5. 审查不通过 → 修正一次并复核；复核失败也不会无限重试
                │ 6. 生成结果表和图表，保存报告快照；用户可把确认正确的 SQL 存为示例
                ▼
             答案 + 证据 + 审查状态 + 每一步工具调用 + 用量 + trace_id
```

| 模块 | 位置 | 要点 |
|------|------|------|
| Agent 循环 | `app/core/agent/react_agent.py` | 原生 function calling；并行 tool_calls；工具报错回传给模型；最后一步收回工具强制作答 |
| 编排 | `app/core/agent/orchestrator.py` | 会话、规划、反思、修正、追踪、历史串联；事件回调驱动 SSE |
| 反思 | `app/core/agent/reflection.py` | 证据是本轮实际工具结果；逐项给出 supported / conflicting / insufficient |
| 只读 SQL | `app/analytics/engine.py` | 应用层校验（token 级，不误杀 `created_at`）+ 只读账号与只读事务 + 超时 + 行数上限 |
| 口径检索 | `app/analytics/glossary.py` | 业务口径 Markdown 按 `##` 切块，BM25（中文 bigram）检索 |
| 固定指标 | `app/analytics/metrics.py` | 版本化电商指标契约；程序生成 SQL，前置检查和统计共用同一快照 |
| 自定义业务口径 | `app/analytics/semantics.py`、`semantic_tools.py` | 不可变版本；声明表、字段、单位、粒度及筛选，程序查询单事实表指标 |
| 程序校验与任务 | `app/analysis/validation.py`、`workspace.py` | 带单位数值的证据关联；本机任务阶段、执行权和手动恢复，恢复重新查询 |
| 数据体检 | `app/analytics/quality.py` | 全量聚合检查缺失、空白、数值范围、期望唯一键和日期边界；不修改原始数据 |
| 变化拆解 | `app/analytics/comparison.py` | 先确认口径；同一只读快照做分项与独立总量核对、金额贡献和比例对称分解 |
| 证据封装 | `app/analytics/evidence.py` | 完整工具记录和 E 编号；预算不足时明确标记未完成 |
| 文档知识库 | `app/knowledge/`、`app/etl/` | TXT/Markdown/DOCX/文本 PDF 解析分块；持久化索引任务；pgvector 向量检索与 BM25 混合；引用校验 |
| 表格导入 | `app/datasets/` | CSV/XLSX 预览、字段类型确认、后台事务导入、租约恢复、来源追踪 |
| 图片/扫描件 | `app/vision/` | 本地页图预览、经同意的视觉识别、人工核对稿，再转为表格或带页码的资料 |
| 分析交付 | `app/analysis/` | PostgreSQL 保存报告快照和已确认条件；用真实结果生成图表；CSV/XLSX/Markdown 导出；用户确认的 SQL 示例库 |
| 会话存储 | `app/core/memory/session.py`、`redis_session.py` | 默认进程内存；可选 Redis 共享历史，整轮原子写入、窗口裁剪、TTL |
| 模型路由 | `app/infrastructure/llm/` | 多模型优先级 + 熔断 + 降级；全链路用量统计 |
| 工作台 | `app/web/` | 同源原生 HTML/CSS/JavaScript，无前端框架、CDN 或远程字体 |
| 演示数据 | `app/demo/` | 固定种子生成的电商库、口径文档和 15 份合成业务文档 |
| 评测 | `eval/` | 50 道合成评测题与 3 组消融（主评测）；12 道 SQL 对照题；各阶段离线验收脚本 |

向量检索由 PostgreSQL + pgvector 完成，不依赖单独的向量数据库。早期模板中的 Milvus 封装、重排序、RAG 生成和摘要记忆模块已删除。

## 两种运行方式

| | 最小模式 | 完整模式 |
|---|---|---|
| 依赖 | `requirements.txt` | 另加 PostgreSQL 依赖和按需的可选依赖 |
| 分析库 | 自动生成的 SQLite 演示库 `data/ecommerce.db` | PostgreSQL 的 `business` / `uploads` schema |
| 可用功能 | 问答、口径检索、固定指标、对比、审查、SSE | 另有文档库、表格导入、图片核对、分析历史与导出 |

PostgreSQL 不可用时，文档、导入、图片和历史功能会自动关闭，问答照常运行；`/health/ready` 返回 `degraded` 并说明原因。

## 快速开始（最小模式）

需要 Python 3.11+ 和一个支持 function calling 的 OpenAI 兼容接口。

```bash
git clone https://github.com/FMAN720/DataAgent.git
cd DataAgent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env             # 配置本机数据服务；聊天 API 在工作台“API 设置”填写
```

```bash
uvicorn app.main:app --reload --port 8000
```

首次启动会自动生成演示库 `data/ecommerce.db`（约 9000 笔订单，几秒钟）。打开 <http://127.0.0.1:8000/> 使用数据工作台，或打开 <http://127.0.0.1:8000/docs> 调试接口。工作台的操作步骤见[使用指南](docs/user-guide/README.md)。

每个使用者在自己的电脑运行 Agent，在左侧“API 设置”分别配置聊天分析、图片/OCR 和向量检索。选择供应商会填入 API 地址，填写自己的 Key 后点击“获取模型”，再从列表选择；供应商未提供列表时可手动填写。获取只请求模型目录，保存不运行模型；各用途的测试请求使用合成材料并可能计费。配置即时用于下一次调用，不需要修改代码或重启。详见[模型设置指南](docs/user-guide/model-settings.md)。

三种用途的 Key 分别存于当前操作系统用户的配置目录，GET 不回显，浏览器不持久存储 Key。Windows 使用当前账户的 DPAPI 加密；其他系统使用仅当前用户可读写的文件。网页的三种用途均不回退使用开发者 `.env` 中的模型 Key。旧版聊天配置可直接读取，首次保存时升级为多用途格式。图片识别仍需逐次同意外发；向量索引需明确启用，更换模型不会自动批量重建。分享项目时不要包含真实 `.env`、运行数据和本机配置文件。本轮面向各自本机运行，不提供多人服务器的账号及数据隔离。

默认 Compose 只发布本机 `127.0.0.1:8000`，为 Docker 桥接地址显式启用设置访问，并将个人配置保存在 `data-analyst-app-model` 本机数据卷中，容器重建后保留。容器内使用 Linux 文件权限保护；不要将此设置及端口映射改为公网部署。

工作台默认进入 Agent 对话首页：左侧是历史会话与数据入口，中央可直接提问或选择示例，点击“发送”才分析。消息区只显示答案；工具记录、审查、图表、结果表、来源和导出集中在每条回答的“分析详情”，点击后从右侧打开，手机上为抽屉。消息区独立滚动，底部输入框可持续追问。“添加数据”连接表格、文档和图片核对。原布局验收见[Agent 工作区验收](reports/agent-workspace/README.md)，本次个人 API 与详情拆分见[本轮验收](reports/personal-api-details/README.md)。

左侧“业务口径”可导入或编辑高级 JSON 定义，保存不调用模型；配置自定义口径后，新分析可在输入框下方选择。字段映射须按真实数据确认，执行时检查类型、唯一键和数据质量。已有会话绑定原版本。左侧“分析任务”可查看阶段和尝试记录；恢复失败或中断任务需再次确认，会重新查询当前数据并可能产生模型费用。程序校验在“分析详情”显示覆盖范围，不把部分验证当作完整认证。具体模板及限制见[核心分析指南](docs/core-analysis.md)。

首次使用可点击“5 步了解分析助手”，或随时打开右上角“使用指南”。指南可跳过和重看，示例不会自动提交或覆盖已有草稿。早期改版记录见[工作台改版](reports/workspace-redesign/README.md)与[新手指南及可读性验收](reports/onboarding-readability/README.md)。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "2025 年 12 月退款率是多少？"}'
```

演示数据覆盖 2025 全年，Agent 把 `ANALYTICS_REFERENCE_DATE`（默认 `2025-12-31`）当作"今天"，所以"上个月"指 2025 年 11 月。接入真实业务库时设为 `today`，每次请求使用当天日期。

## Windows 日常启动（完整模式）

完成下面的本机完整模式初始化后，日常双击项目目录中的 [`start-agent.cmd`](start-agent.cmd)，或在 PowerShell 执行：

```powershell
cd DataAgent
.\start-agent.cmd
```

入口自动定位项目及现有虚拟环境；Docker 未运行时尝试打开 Docker Desktop（支持当前用户安装路径），等待引擎就绪，再启动 PostgreSQL / Redis 并等待健康检查。随后启动 18080 端口的工作台，确认分析库、会话和已启用的文档、导入、历史模块就绪后打开浏览器。复用现有数据卷和个人 API 配置；启动过程中遇到错误会停止并保留提示窗口。

使用时保持启动窗口打开，按 Ctrl+C 停止应用。已就绪的工作台会直接复用；如果端口上是之前依赖缺失时启动的应用，入口会提示在原窗口按 Ctrl+C 后重新启动，因为文档和历史模块需要在应用启动时重新加载。仅手动执行 `uvicorn` 时，`Application startup complete` 只代表网页进程启动，数据服务还需另行就绪。

可使用 `start-agent.cmd --no-browser` 仅启动而不自动打开网页；缺少 Docker、虚拟环境、`.env.phase2` 或本机镜像时，先完成首次环境准备。该入口适用于 Windows 本机完整模式；最小 SQLite 模式仍按上文启动。启动故障与验证记录见[日常启动修复](reports/local-start/README.md)。

## 完整模式部署

配置按 `.env`、`.env.phase2`、进程环境变量的顺序覆盖。`.env.phase2` 由下面的 `init` 生成，内含数据库随机密码，已被 Git 和 Docker 构建上下文排除。

```bash
pip install -r requirements-postgres.txt
python -m app.infrastructure.database.manage init
docker compose --env-file .env.phase2 --profile postgres up -d --no-deps --wait postgres
python -m app.infrastructure.database.manage bootstrap
python -m app.infrastructure.database.manage upgrade
python -m app.infrastructure.database.manage import-demo
```

这会创建同库的 `business`（演示业务表）、`uploads`（导入表格）、`agent`（文档、向量、会话、报告、图片草稿）三个 schema，以及迁移、应用、导入、只读分析四个权限独立的账号。启动应用不会执行迁移或安装依赖；数据库结构升级使用 `manage upgrade`。权限划分、回退和密码轮换见[第二阶段报告](reports/phase2/README.md)。

可选功能及其依赖：

| 功能 | 依赖 | 开关与说明 |
|---|---|---|
| 文本 PDF 上传 | `requirements-documents.txt`（pypdf） | DOCX、TXT、Markdown 只用标准库 |
| XLSX 导入 | `requirements-imports.txt`（openpyxl） | CSV 只用标准库；`DATASETS_ENABLED`，需要 `.env.phase2` 中的 `IMPORT_DB_URL` |
| 图片/扫描件 | `requirements-vision.txt`（Pillow、pypdfium2） | 本地预览不需要模型；识别需 `VISION_ENABLED=true` 和独立的 `VISION_*` 配置 |
| 向量检索 | 无额外 Python 包 | `EMBEDDING_ENABLED=true` 并填写 `EMBEDDING_*`；关闭时文档仍可用 BM25 检索 |
| Redis 共享会话 | `requirements-redis.txt` | `SESSION_BACKEND=redis`，见下文 |
| 分析历史 | 复用 PostgreSQL | `ANALYSIS_HISTORY_ENABLED`，默认开启 |

Embedding 和视觉识别都会把内容发送到外部 API 并产生费用，默认关闭，Key 不会自动继承 `OPENAI_API_KEY`。视觉识别即使启用，每次也要在工作台勾选同意并点击识别才会调用。

### Redis 共享会话

`SESSION_BACKEND=redis` 时，Agent 从 Redis 读取历史，并在答案通过审查后写回最近 N 轮问答（默认 10 轮，最后一次写入后 3600 秒过期，读取不续期）。Redis 不可用时返回 503，已开始的 SSE 发送 `error`，不会静默换成空白的本地会话。

```bash
pip install -r requirements-redis.txt
docker compose --profile redis up -d --no-deps redis
```

然后在 `.env` 设置 `SESSION_BACKEND=redis`、`REDIS_URL=redis://127.0.0.1:16379/0` 并重启应用。Redis 只保存问答历史；工具结果、执行轨迹和报告仍在进程内存或 PostgreSQL。同一会话并发提交的多个问题按完成顺序写回，客户端应等上一轮结束再追问。详见 [Redis 实施说明](reports/redis/README.md)。

### Docker

```bash
docker compose up -d --build app
```

镜像内置 SQLite 演示库，默认只安装核心依赖。可选功能用构建参数开启，例如完整模式：

```bash
INSTALL_POSTGRES=true INSTALL_REDIS=true INSTALL_DOCUMENTS=true INSTALL_IMPORTS=true INSTALL_VISION=true docker compose up -d --build app
```

参数与依赖文件一一对应：`INSTALL_POSTGRES`、`INSTALL_REDIS`、`INSTALL_DOCUMENTS`、`INSTALL_IMPORTS`、`INSTALL_VISION`。上传文档、导入源文件和图片草稿分别保存在 `data-analyst-app-uploads`、`data-analyst-app-imports`、`data-analyst-app-vision` 三个数据卷，容器重建后保留。容器内连接同一 compose 网络的 PostgreSQL 时，数据库地址应为 `postgres:5432`，而不是 `.env.phase2` 中给本机用的 `127.0.0.1:15432`。

> 安全提示：接口没有鉴权，docker-compose 只绑定 `127.0.0.1`。对外暴露前需要加认证和限流，否则任何人都能消耗你的模型额度并查询数据库。`.env.example` 中 `DEBUG=true` 只适合本机开发。

## 返回结果

- `steps`：Agent 实际执行的每一次工具调用（参数、结果、耗时）。`session_id` 带回下一次请求即可追问（「那 10 月呢？」）。
- `success` 表示生成了答案；`reflection_status` 单独表示审查结果：`passed` / `failed` / `unavailable` / `skipped`。审查不可用时 `reflection.passed` 和 `quality_score` 为 `null`，不会伪装成通过。`reflection.checks` 给出逐项结论和对应的 E 证据编号。审查未通过或未完成的回答不写入后续会话记忆。
- `analysis`：口径、对比明细、数据体检、固定指标、核对结果和限制说明。`needs_clarification=true` 时，Agent 停止同批其他工具，先请用户补充口径。
- `citations`：文档问答的引用，只接受本轮工具实际返回的片段。
- `llm_calls`、`usage`：包含规划、执行、修正和全部审查；`usage_breakdown.stages` 给出各阶段调用数、实际 HTTP 请求数、Token、耗时和失败数，Embedding Token 单独统计。缺少用量、调用失败或发生重试时 `usage_complete=false`。Token 是服务商返回的用量，不是账单金额。
- SSE 的 `final` 事件携带同样的内容。

证据字符预算由 `REFLECTION_MAX_EVIDENCE_CHARS` 控制（默认 24000），超出时明确标记审查未完成，不静默截断。

## Agent 工具

| 工具 | 作用 |
|---|---|
| `list_tables`、`describe_tables` | 表清单、字段、注释、外键、样例行。表超过 `MAX_LISTED_TABLES`（默认 30）时，`list_tables` 按问题用 BM25 只返回最相关的表 |
| `run_sql` | 只读 SQL；跨 schema 用完整名称，如 `business.orders`、`uploads.xxx` |
| `search_glossary` | 检索业务口径文档 |
| `get_metric_definition`、`query_metric` | 净销售额、有效订单数、退款率的固定口径；单周期或双周期对比。仅适用 `ecommerce_v1` 电商模型 |
| `get_business_definition`、`query_business_metric` | 仅在自定义业务口径下提供；按当前版本生成单事实表指标查询，固定筛选不可移除 |
| `profile_dataset` | 业务表或导入表的数据体检 |
| `prepare_analysis`、`compare_periods` | 先确认指标、维度、周期和筛选，再做变化拆解；不接受模型提交的业务数值 |
| `list_datasets` | 已导入的表格及来源（文档库和导入就绪时注册） |
| `search_documents` | 业务文档检索，返回可校验的引用（文档库就绪时注册） |
| `search_sql_examples` | 召回用户确认过的同类问题 SQL 写法，只作参考，须重新执行（分析历史就绪且已执行迁移 0006 时注册） |
| `calculator` | 安全的算术表达式计算 |

固定指标和对比工具有程序约束；通用 `run_sql` 只保证只读和证据审查，不能据此认为自由 SQL 的业务语义已被自动验证。

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/chat` | question、可选 session_id / mode / business_profile_id / resume_task_id，返回答案、校验和任务引用 |
| POST | `/api/v1/chat/stream` | 同上，SSE 推送 task / phase 及 start / plan / llm / tool / reflection / revision / final 事件 |
| GET / PUT | `/api/v1/business-profiles/{id}` | 查看或创建不可变口径版本；列表见 `GET /business-profiles`，写入须提供 expected_revision |
| GET | `/api/v1/analysis-tasks`、`/{id}` | 查看本机任务、阶段和尝试记录 |
| POST | `/api/v1/analysis-tasks/{id}/resume` | confirmed=true 后恢复失败、取消或中断任务；重新查询，可能计费 |
| GET | `/api/v1/traces/{trace_id}` | 一次请求的 span 树 |
| DELETE | `/api/v1/sessions/{session_id}` | 清除该会话上下文并推进版本；保留已保存的报告、上传资料和执行轨迹 |
| GET | `/api/v1/conversations` | 最近的持久化会话，默认 30 条，limit 最大 50 |
| GET | `/api/v1/conversations/{session_id}` | 恢复报告和已确认条件，默认最近 50 次，limit 最大 100 |
| GET | `/api/v1/analyses/{report_id}` | 一次已完成分析的不可变报告快照 |
| GET | `/api/v1/analyses/{report_id}/export` | format=csv/xlsx/md；CSV 可用 table_id 选择结果表；不调用模型、不重跑 SQL |
| POST | `/api/v1/analyses/{report_id}/sql-example` | 把这份报告最后一条成功 SQL 存为示例；审查未通过的报告不接受 |
| GET | `/api/v1/sql-examples` | 已保存的 SQL 示例，默认 50 条 |
| DELETE | `/api/v1/sql-examples/{id}` | 删除示例 |
| GET | `/api/v1/health` | 存活探针 |
| GET | `/api/v1/health/ready` | 核心问答不可用返回 503；核心可用、可选功能不可用时返回 200 + `degraded` |
| POST | `/api/v1/documents/upload` | TXT、Markdown、DOCX、文本 PDF；可加 document_date 与 document_type |
| GET | `/api/v1/documents` | 文档列表；存储未就绪时返回 503 |
| GET | `/api/v1/documents/{id}` | 来源详情及分页片段；可用 chunk_id 定位引用，返回页码 / 章节 |
| PATCH | `/api/v1/documents/{id}` | 更新业务日期、类型；不重新发送正文或生成向量 |
| POST | `/api/v1/documents/search` | 关键词/向量/混合检索；支持 document_ids、top_k、date_from/date_to、document_type |
| GET | `/api/v1/documents/{id}/index` | 索引状态、进度、错误及当前进程 Embedding 用量 |
| POST | `/api/v1/documents/{id}/index` | 入队索引；`?force=true` 强制重建当前模型索引 |
| POST | `/api/v1/documents/{id}/retry` | 手动重试失败任务，复用已完成的向量分块 |
| DELETE | `/api/v1/documents/{id}` | 删除文档、分块、向量和任务；文件占用时返回 202 等待清理 |
| POST | `/api/v1/datasets/preview` | CSV/XLSX 上传与预览；CSV 可选编码和分隔符，Excel 可选工作表 |
| GET | `/api/v1/datasets` | 导入数据集列表；`?ready_only=true` 只返回可分析的数据 |
| GET | `/api/v1/datasets/{id}` | 字段映射、预览、行数、进度、状态及错误 |
| POST | `/api/v1/datasets/{id}/import` | 提交显示名称和字段类型覆盖（`{}` 表示接受推断类型），确认后排队入库 |
| POST | `/api/v1/datasets/{id}/retry` | 按原确认字段重试失败导入 |
| DELETE | `/api/v1/datasets/{id}` | 删除受来源标记保护的导入表、源文件与元数据 |
| POST | `/api/v1/vision/drafts/upload` | PNG/JPG/静态 WEBP/PDF 本地草稿，不自动识别 |
| GET | `/api/v1/vision/drafts` | 最近 50 份图片草稿 |
| GET | `/api/v1/vision/drafts/{id}` | 状态、版本、按页首次转录/修改稿及尝试记录 |
| GET | `/api/v1/vision/drafts/{id}/pages/{number}/image` | 本地归一化 PNG 页图，不外发 |
| POST | `/api/v1/vision/drafts/{id}/recognize` | 提交 revision/pages/consent；仅主动同意后调用视觉 API |
| PUT | `/api/v1/vision/drafts/{id}/pages/{number}` | 保存本页核对稿；支持明确标记 manual 的人工转录 |
| POST | `/api/v1/vision/drafts/{id}/table` | 人工确认后转表格预览，仍需确认字段后导入 |
| POST | `/api/v1/vision/drafts/{id}/document` | 人工确认后保存所选页参考资料；默认不发送 Embedding |

主要上限：文档 10 MiB（`UPLOAD_MAX_BYTES`）；表格 20 MiB、5 万行、100 列（`IMPORT_MAX_*`）；图片 10 MiB、PDF 最多 20 页、每次识别最多 5 页（`VISION_MAX_*`）。

## 测试与评测

首次开源准备（2026-11-12）：536 项离线 Python 测试及 55 项前端测试通过；97 项 PostgreSQL/Redis 集成测试未启用。详细范围见[发布验证记录](reports/open-source-release/README.md)，远端检查见 [GitHub Actions](https://github.com/FMAN720/DataAgent/actions)。

```bash
python -m pytest              # 脚本模型，离线运行，阻断外网
node --test tests/workspace.test.cjs   # 工作台前端逻辑
ruff check app eval tests lab          # 静态检查，规则见 pyproject.toml
ruff format --check app eval tests lab # 代码格式检查
mypy app eval                        # 应用和评测代码类型检查
```

默认跳过需要真实数据库的集成测试，设置 `RUN_POSTGRES_TESTS=1`、`RUN_REDIS_TESTS=1` 后运行。测试用脚本化的假 LLM 驱动真实工具和真实演示库，覆盖 SQL 报错自我修正、并行工具调用、非法 JSON 参数、步数上限兜底、反思修正、多轮会话、SSE 事件顺序、SQL 安全、上传与导入边界、权限隔离和图片核对。本轮新增口径版本、恢复冲突、数值校验及 21 个财务/库存/客服场景，独立运行入口为 `python -m eval.core_regression`；最新测试结果见[本轮验收](reports/core-analysis-upgrade/README.md)。这些是程序验证，不代表真实模型的回答准确率。

### 主评测：50 道合成题

```bash
python -m eval.run_phase7     # 只准备合成库与标准答案，不访问外部 API
```

50 题分为 30 道开发题、20 道留出题，覆盖 SQL、周期对比、文档、表格与证据不足的处理；标准答案在独立的合成 SQLite 库上计算。另有关键词检索、关闭规划、关闭审查三组配对消融，各 10 题。真实模型评测需要 `--live --approved-synthetic-api-budget`，会产生 API 费用；`--limit N` 可以先试跑几题核对用量。

2026-11-01 用 DeepSeek（服务端实际模型 `deepseek-flash`）和 BGE-M3 跑了一次，下一请求的预留将超过 30 元预算上限时停止，完成 80 次中的 76 次。聊天保守估算累计 29.69 元，不是服务商账单：

| 方案 | 通过 / 完成 |
|---|---|
| 基线 | **46 / 50**（开发题 29/30，留出题 17/20） |
| 只用关键词检索 | 9 / 10 |
| 关闭规划 | 8 / 10 |
| 关闭审查 | 4 / 6 |

每组消融只有 6–10 题，单题之差不具统计意义；判分是规则检查，不判断语义或因果是否正确。失败原因、用量和费用见[真实评测报告](reports/live-eval/README.md)，协议设计见[第七阶段报告](reports/phase7/README.md)。

2026-11-03 已完成收尾复核：76 份结果的离线重评分与原判分一致；515 项 Python 测试（含 PostgreSQL/Redis 集成）、16 项前端测试、12 项跨数据库 SQL 对照全部通过，类型、静态与格式检查通过。本次没有新增付费模型请求，详见[收尾验收记录](reports/live-eval/README.md#收尾复核2026-11-03)。

### SQL 对照：12 道口径陷阱题

```bash
python -m eval.run_eval                    # 用真实模型跑 12 道题
python -m eval.run_eval --no-glossary      # 去掉口径检索做对照
python -m eval.run_eval --no-reflection
python -m eval.run_eval --mode plan_execute
```

`eval/questions.json` 每道题都有标准 SQL，判分只看最终答案里有没有出现正确数字（允许四舍五入、「万」单位）。这组题也用于 SQLite 与 PostgreSQL 的结果一致性测试。题目刻意埋了口径陷阱：

| 陷阱 | 不查口径的典型错误 |
|------|------|
| 测试账号每月刷单 | 11 月净销售额 1,148,843.98（错）vs 1,047,258.23（对） |
| GMV ≠ 销售额 | GMV 含已退款订单，两者差约 10 万 |
| `refund_requests` 含被驳回的申请 | 用申请表算退款率会偏高 |
| 金额单位是分 | 结果差 100 倍 |
| 区域存英文编码 | `WHERE region = '华东'` 查不到数据 |
| 下单端 vs 拉新渠道 | `orders.channel` 和 `users.acquire_channel` 混用 |

## 换成自己的数据库

1. 创建只有 SELECT 权限的数据库账号（应用层校验和只读事务是兜底，不能替代权限控制）。
2. 设置 `ANALYTICS_DB_URL`，例如 `postgresql+psycopg2://readonly:***@host:5432/shop`，依赖见 `requirements-postgres.txt`；`ANALYTICS_DB_SCHEMAS` 填实际 schema，例如 `["public"]`。如果已有 `.env.phase2`，修改该文件或用进程环境变量覆盖。
3. 给表和字段加注释（PostgreSQL `COMMENT ON`），`describe_tables` 会读出来给模型看。注释写得越清楚，SQL 越准。
4. 把你们的指标口径写进 Markdown（每个指标一个 `##` 小节），设置 `GLOSSARY_PATH`。
5. 把 `ANALYTICS_REFERENCE_DATE` 设为 `today`，每次请求使用当天日期。
6. `query_metric` 的固定指标只适用于演示库的 `ecommerce_v1` 表结构，换库后需要在 `app/analytics/metrics.py` 重新定义，或只使用 `run_sql`。
7. 把 `eval/questions.json` 和 `eval/phase7_cases.py` 换成你们的真实问题和标准答案。

## 文档索引

| 文档 | 内容 |
|---|---|
| [使用指南](docs/user-guide/README.md) | 工作台操作步骤和练习文件 |
| [PRODUCT.md](PRODUCT.md)、[DESIGN.md](DESIGN.md) | 产品定位与界面设计约定 |
| [design/](design/) | 工作台、结果交付、图片核对三块界面的设计说明 |
| [reports/](reports/) | 各阶段实施报告和验收记录（第一至七阶段、可靠性第 1 轮、交付第 2 轮、图片第 3 轮、Redis、真实模型评测） |
| [一周面试学习路线](reports/interview/一周面试学习路线.md) | 围绕本项目的面试准备 |

reports 下各报告描述的是当时的验收状态，例如早期报告中"Embedding 关闭"已被后来的启用记录取代；当前行为以本 README 为准。

## 后续可以做的

- 固定指标的前置检查要求澄清时，Agent 常把问题原样抛给用户，而不是先换一种查询方式；真实评测中 3 道失败题与此有关
- 补跑剩余 4 次消融，并扩大每组消融的题量，才能比较各模块的作用
- 扩大 BGE-M3 评测集，校准无答案问题的相似度阈值
- 用小规模合成图实测视觉识别，评估小字、复杂表头、跨页表格和识别失败的处理
- SQL 示例库目前按问题文本 BM25 召回；示例多了以后可以改用 pgvector 语义召回
- 登录与资料权限、保留期限及部署运维；当前是本机单用户工作台

## 目录

```
DataAgent/
├── app/
│   ├── main.py                  # 入口：build_agent() 组装依赖，lifespan 按配置启用各功能
│   ├── config.py
│   ├── api/routes/              # chat、analysis、document、dataset、vision、health、workspace
│   ├── analytics/               # 只读 SQL、口径库、固定指标、数据体检、对比、证据、prompt
│   ├── analysis/                # 报告快照、可信条件、图表与导出
│   ├── knowledge/               # 文档索引任务、Embedding API、混合检索、引用
│   ├── datasets/                # CSV/XLSX 解析、类型确认、事务导入、租约与恢复
│   ├── vision/                  # 图片/PDF 页图、视觉接口、核对草稿与发布
│   ├── etl/                     # 文档解析与分块
│   ├── web/                     # 同源工作台
│   ├── core/agent/              # ReAct、编排、规划、反思
│   ├── core/tools/              # 工具基类、注册中心、计算器
│   ├── core/memory/             # 会话存储（内存 / Redis）
│   ├── core/rag/                # BM25 关键词检索（口径库、文档库共用）
│   ├── demo/                    # 演示库生成脚本、口径文档、合成业务文档
│   └── infrastructure/          # 模型路由与熔断、用量统计、追踪、数据库模型与迁移管理
├── migrations/                  # Alembic 迁移（0001–0006）
├── eval/                        # 评测集、评测与离线验收脚本
├── tests/                       # pytest 与前端 node 测试
├── lab/                         # 配套课程的练习脚本
├── docs/user-guide/             # 使用指南、截图与练习文件
├── design/                      # 界面设计说明
├── reports/                     # 各阶段实施报告与验收记录
├── requirements.txt             # 核心依赖（SQLite 最小模式）
├── requirements-postgres.txt    # PostgreSQL / pgvector / Alembic
├── requirements-redis.txt       # Redis 共享会话
├── requirements-documents.txt   # 文本 PDF
├── requirements-imports.txt     # XLSX
├── requirements-vision.txt      # 图片与扫描 PDF
├── requirements-optional.txt    # 以上可选依赖的合集
└── requirements-dev.txt
```

## 许可证

本项目采用 [MIT 许可证](LICENSE)。项目由 `ai-agent-interview-guide` 的 Python 示例发展而来，已保留原始许可；来源与后续实现说明见[开源说明](docs/OPEN_SOURCE.md)。
