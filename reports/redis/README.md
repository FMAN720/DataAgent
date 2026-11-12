# Redis 会话存储实施说明

实施日期：2026-10-23。

## 解决的问题

原来的会话历史只在 Python 进程内保存。同一个 `session_id` 的追问落到另一个进程时，无法读到上一轮上下文。现在通过 `SessionStore` 接口选择 Redis，让不同应用实例共享最近的对话。

Redis 保存用户问题与经过原有校验的最终答案。业务表、文档、向量、导入任务和索引任务仍使用 PostgreSQL。没有缓存 SQL 结果、没有迁移任务队列，也没有启用额外的模型摘要调用。

请求流程：读取 Redis 历史 → 拼接上下文 → 原有 ReAct / 工具 / 可选规划与反思 → 校验答案 → 原子写入本轮问题和答案 → 返回结果。现有审查未通过或引用校验失败的答案仍不会进入历史。

## 实现

| 位置 | 职责 |
| --- | --- |
| `app/core/memory/session.py` | 统一接口与内存实现；修复过期历史直接追加时被重新带回的问题 |
| `app/core/memory/redis_session.py` | Redis 实现、可选客户端导入、工厂和安全错误 |
| `app/main.py` | 根据配置注入存储；关闭应用时释放连接；存储错误返回 503 |
| `app/core/agent/orchestrator.py` | 共用原有读写流程；增加会话就绪探测、清除和资源关闭；存储失败时结束错误轨迹 |
| `app/api/routes/chat.py` | SSE 建流前探测；建流后故障通过 `error` 事件返回；清除会话接口 |
| `app/api/routes/health.py` | 独立报告分析库和会话存储健康状态 |
| `docker-compose.yml` | 独立 Redis profile、容器、持久化卷与本机端口 |

Redis List 中每个元素存储一整轮 JSON，包含版本号、问题和答案。使用 SHA-256 后的会话 ID 构建带项目与版本前缀的键，读取时严格校验结构，只产生 user / assistant 角色消息。原始 ID 不直接进入键名。

一次事务执行 `RPUSH`、`LTRIM`、`EXPIRE`，避免不同客户端追加时丢失整轮或拆开问题与答案。默认保存最近 10 轮；最后一次成功追加后 3600 秒过期，读取不延长有效期。仅使用异步客户端，连接池上限默认 20，连接与命令超时默认 1 秒。

写入不自动重试：若 Redis 已执行事务而响应丢失，重试可能重复保存；此时提示本轮历史未能确认保存。模型若已生成答案，重新提交请求可能再次产生模型费用。选择 Redis 后故障不会静默回退到内存，也不会把损坏历史当作空历史继续请求模型。

`GET /api/v1/health/ready` 新增 `session_backend` 与 `session_store`。Redis 不可用时核心就绪状态为 503，`analysis_database` 仍独立反映实际状态。`/health` 存活探针不访问 Redis。SSE 建流前不可用返回 HTTP 503，建流后故障发送 `error`，不伪造 `final` 成功事件。

`DELETE /api/v1/sessions/{session_id}` 返回 204，只删除该会话上下文。不会删除文档、业务数据或进程中的执行轨迹。

## 本机部署与配置

用户明确同意后，在项目现有 `.venv` 中安装了 `redis==5.3.1` 及必要依赖 `PyJWT==2.15.1`。依赖声明为 `redis>=5.2,<6`，专用文件为 `requirements-redis.txt`；未安装整份可选依赖列表。

复用本机已有 `redis:8.0.3` 镜像，未拉取镜像。创建：

- 容器：`data-analyst-session-redis`。
- 持久化卷：`data-analyst-session-redis-data`。
- 端口：`127.0.0.1:16379` → 容器 `6379`。
- AOF：开启，`appendfsync everysec`。
- Redis 数据内存上限：128 MiB；策略 `noeviction`，达到上限时明确写入失败，避免悄悄淘汰正在使用的会话。
- Compose 使用 `pull_policy: never`，没有本地镜像时不会自动下载。

已在被 Git 忽略的 `.env` 中启用以下配置，保留原有模型、数据库和 Embedding 配置：

```dotenv
SESSION_BACKEND=redis
SESSION_MAX_TURNS=10
SESSION_TTL_SECONDS=3600
REDIS_URL=redis://127.0.0.1:16379/0
REDIS_KEY_PREFIX=analyst:session:v1:
REDIS_TIMEOUT_SECONDS=1
REDIS_MAX_CONNECTIONS=20
```

实施时原有 `127.0.0.1:18080` 服务仍运行旧版代码，未中断它。该旧进程重启后才会使用新存储；原来内存中的会话没有自动迁移到 Redis。独立测试应用已验证按配置装配 Redis 的完整路径。

本机应用启动前可检查 Redis：

```powershell
docker compose --profile redis up -d --no-deps --pull never redis
docker exec data-analyst-session-redis redis-cli ping
# 项目根目录中，用原有启动方式重启应用即可，例如：
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

如果应用也运行在 Docker，镜像构建时需设置 `INSTALL_REDIS=true` 安装客户端，例如 `docker compose build --build-arg INSTALL_REDIS=true app`。Compose 中应用默认连接 `redis:6379`；自定义连接可使用 `DOCKER_REDIS_URL`。本轮没有构建或启动应用 Docker 镜像。

选择 `SESSION_BACKEND=memory` 并重启可恢复最小运行模式；不要求安装 Redis 客户端。切换后不会读取另一后端的历史。`SESSION_MAX_SESSIONS` 只限制内存后端，Redis 使用 TTL、轮数和实例内存上限控制容量。

## 验证

- Python：**295 passed，68 skipped**，其中 Redis 专用测试 **34 项通过**，包含 **7 项真实 Redis 集成测试**。
- 前端既有测试：**6 项通过**。
- `pip check`：通过。
- Compose 配置检查：通过；专用容器为 healthy，`PING` 返回 `PONG`，端口仅绑定本机。
- Git 差异格式检查通过；`.env` 仍被 Git 忽略。

真实 Redis 验证覆盖：独立客户端共享历史与裁剪、精确清除且保留其他会话、写入续期而读取不续期、实际过期后重建、40 次并发追加不丢轮次且问题答案配对、独立操作系统进程读取、两个 Agent 之间追问、连接故障，以及两个 FastAPI 应用通过实际工厂共享上下文并执行 SSE 和清除接口。

普通测试覆盖无 Redis 依赖时的内存模式、缺失客户端错误、配置校验、敏感地址隐藏、损坏历史拒绝、读写及探测错误脱敏、HTTP / SSE 失败边界、写入失败不重试模型、独立健康状态与连接关闭。

PostgreSQL 专项本轮按原有 opt-in 机制跳过，避免与原有应用的后台工作进程争抢测试任务。未重新运行付费模型评测；所有聊天联调使用脚本化模型与合成对话，没有发送用户资料或调用付费 API。测试只清理自身唯一前缀对应的精确键，未调用 `FLUSHDB` / `FLUSHALL`。

复现本轮 Python 验证：

```powershell
$env:RUN_REDIS_TESTS='1'
.\.venv\Scripts\python.exe -X utf8 -m pytest -q --basetemp=.pytest_tmp_redis_verify --junitxml=reports/redis/pytest.xml
```

不设置 `RUN_REDIS_TESTS` 时，真实 Redis 测试同样默认跳过。结果见本目录的 `pytest.xml` 和 `verification.json`。

## 边界与面试表述

这是共享短期会话记忆，不是永久对话档案。TTL 会主动删除历史，AOF everysec 也不承诺故障时零丢失。整轮存储操作具有原子性，但没有为整个模型执行过程加分布式锁；同一会话的并发问题按完成顺序写回，交互端应等待上一轮结束再追问。

执行轨迹仍在进程内，文件仍在本地文件系统，接口仍没有用户认证与会话归属校验，因此不能把本次改动描述为完整的多租户或多机部署能力。

可以准确表述为：**在 FastAPI Agent 中实现可切换的 Redis 会话存储，通过事务追加、滑动窗口和 TTL 管理短期上下文，解决多应用实例之间的会话历史共享，并完成真实 Redis 跨进程、并发及故障验证。**

实现参考：[redis-py 异步连接与关闭](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)、[Redis pipelines 与 transactions 官方说明](https://redis.io/docs/latest/develop/clients/redis-py/transpipe/)。
