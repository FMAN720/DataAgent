# 第一阶段实施报告

日期：2026-10-22。范围：修复基础问题、补齐回归测试、保存升级前后基线。第二阶段的 PostgreSQL/pgvector 迁移、embedding API 接入、文档检索和异步队列尚未实施。

## 环境与授权

- 使用项目已有 `.venv`：Python 3.12.8、pytest 9.1.1，以及已有的项目依赖。
- 未执行依赖安装、模型下载、镜像拉取或中间件部署；未修改 `.env` 或现有业务演示库。
- `pypdf`、`asyncpg` 和 `ruff` 当前未安装。没有为测试自动安装它们。
- 用户明确允许将演示问题、业务口径和 SQLite 演示库的查询结果发送到现有 DeepSeek API，完成修改前后评测。评测使用 `deepseek-chat`，temperature=0，react 模式，启用反思，max_steps=12，concurrency=3，参考日期为 2025-12-31。
- Codex 的 Windows 沙箱内，Python asyncio 初始化阻塞在本机 socketpair；使用获准的沙箱外命令完成测试和评测。未为此修改项目事件循环或安装其他运行时。

## 已完成清单

- [x] 保存修改前的 55 项测试基线与真实模型题库结果。
- [x] 工具执行前强制校验本轮白名单；空列表禁止所有工具；最后一步即使模型意外返回工具调用也不会执行；拒绝结果保留正确的 tool_call_id。
- [x] 上传文件采用 UUID 磁盘文件名、可配置大小上限和分段限量读取。
- [x] 校验当前支持的后缀、MIME、UTF-8 文本和 PDF 文件头；拒绝类型伪装、空文件、空解析结果。
- [x] 解析失败不写入文件；数据库写入失败或请求取消时回滚并清理文件；回滚本身失败时仍尝试清理。
- [x] 保留 TXT/Markdown 与可选 PDF 能力，补齐 Markdown 缺失 MIME 和 UTF-8 BOM 的处理；解析和分块在线程池运行。
- [x] 审查模型结果经过严格类型校验，不再把字符串 `false` 等强转为有效结论。
- [x] 审查状态区分 passed、failed、unavailable、skipped；不可用时 passed 和 quality_score 均为 null。
- [x] 审查错误与最终状态可在 HTTP 响应、SSE 和追踪中观察；修正后的答案复核一次，不无限重试。
- [x] 就绪检查真实探测分析库；核心能力不可用返回 503；文档依赖不可用时保留核心服务并标记 degraded。
- [x] 文档存储启动时检查连接与表结构；未就绪时接口返回 503；不自动创建数据库或表。
- [x] 请求使用当前 FastAPI 应用自己的文档 session factory，不会误用其他应用遗留的全局工厂。
- [x] 关闭服务时释放聊天客户端和分析库资源。
- [x] 修复保留的长期记忆示例中过滤字符串的双引号、反斜线和控制字符转义。
- [x] 更新配置示例、README 与备用模块处置记录。

## 验证结果

### 离线测试

| 执行 | 结果 | 记录 |
|---|---|---|
| 修改前 | 55 passed，3.54 秒 | [baseline-tests.xml](baseline-tests.xml) |
| 最终回归 | 116 passed，5.18 秒 | [regression-tests.xml](regression-tests.xml) |

新增 61 个参数化测试实例，使用脚本模型、真实 SQLite 演示库及模拟文档存储，未调用外部模型。原有 SQL 只读、自纠错、并行工具、多轮问答、SSE 等场景继续通过。

编译检查 `python -m compileall -q app eval tests` 通过，Git 空白检查通过。未安装 ruff，因此未声称通过 ruff 检查。

### 真实模型回归

| 执行 | 题库通过数 | 平均耗时 | 平均 ReAct Token | 记录 |
|---|---|---|---|---|
| 修改前首次 | 12/12 | 5.25 秒 | 8891.92 | [baseline-before.json](baseline-before.json) |
| 原始代码补录轨迹 | 11/12 | 5.61 秒 | 8654.67 | [baseline-before-traces.json](baseline-before-traces.json) |
| 修改后 | 12/12 | 5.34 秒 | 8887.33 | [baseline-after.json](baseline-after.json) |

首次基线只保存逐题答案与统计。随后使用 Git HEAD 的原始源码快照重新运行，补全逐题 SQL、工具输出、审查结果和 Span。源码快照保存在本地 `baseline-source.zip` 和 `baseline-source/`，已从 Git 待提交列表排除，未覆盖当前源码。

三次评测的 SQLite 数据库 SHA-256 相同。Git 快照使用 LF，而工作目录部分文件使用 CRLF，因此题库和口径文件的原始字节哈希可能不同；已比较解析后的 JSON 与归一化文本，确认题库及口径内容一致。

补录轨迹时 `east_nov_sales` 失败：原始模型把“上个月”解释成 2025 年 12 月，预期应是 11 月；审查模型仍通过。该失败被完整保留，没有修改题库或删除结果。相同配置下仍有模型波动，因此这些单次结果不能证明准确率或性能提升。

本阶段结论是：基础边界修复后，离线回归通过，原有题库在修改后运行中保持 12/12；主要收益是可确定验证的权限、失败处理和审查状态改善。

### 统计限制

- 当前题库沿用既有的数值/文本命中评分，不等于完整的语义正确率。
- `llm_calls` 和 `usage` 仍只统计 ReAct 执行及修正，不包含规划与审查；最终复核会额外调用模型。因此表中 Token 不能用于推断完整请求费用。
- 平均耗时包含审查，但单次 API 运行容易受服务端状态影响；不根据本次差异宣称加速。
- 模型使用服务端别名，供应商可能调整实际版本；已记录运行日期和依赖版本。

## 备用模块处置

| 模块或定义 | 处置 | 原因 |
|---|---|---|
| `app/core/tools/builtin/search.py` | 删除，并清理导出 | 未被主流程、测试或 lab 引用，现有产品分析使用受控业务资料 |
| `app/core/memory/manager.py` | 删除 | 未接入主流程，当前记忆由 SessionStore 管理 |
| `app/infrastructure/cache/redis_cache.py` | 删除，并清理导出 | 未使用的语义答案缓存缺少数据版本、权限与口径边界；后续 Redis 用于队列、限流时按实际需求接入 |
| `TaskStatus`、`DocumentUploadRequest` | 删除，并清理导出 | 没有调用方，后续任务模型按真实需求设计 |
| ETL、检索、重排、数据库 ORM | 保留 | 后续文档能力、pgvector 适配和持久化需要改造复用 |
| `short_term.py` | 保留但未启用 | 后续会话与 Redis 集成时改造，当前仍使用 InMemorySessionStore |
| `long_term.py`、Milvus 适配 | 保留为未启用参考代码 | 字符串转义已修复；未来 pgvector 方案需要适配，当前没有连接 Milvus |
| Conversation、Message、TraceLog ORM | 保留但未启用 | 已确定后续会话、轨迹持久化需求，第一阶段不进行数据库迁移 |

删除的已跟踪源码可从 Git HEAD 或本地原始源码快照恢复。用户已有的 `lab/`、原评测 JSON 和其他测试临时目录没有被清理或覆盖。

## 新配置与接口行为

| 配置 | 默认值 | 含义 |
|---|---|---|
| `DOCUMENTS_ENABLED` | true | 可选文档功能；false 时不初始化文档库 |
| `UPLOAD_MAX_BYTES` | 10485760 | 单文件上限，10 MiB |
| `UPLOAD_DIR` | 项目内 `uploads/` | 服务端生成文件名的受控存储目录 |

`/health` 保持进程存活探针。`/health/ready` 检查已初始化的 Agent 和分析库，不额外发送付费聊天请求。当前环境缺少 asyncpg，预期是核心可用时返回 200 + degraded，文档接口返回 503。缺少 pypdf 时 PDF 上传明确返回 503，不会自动下载安装。

`reflection_status` 是最终答案的审查状态。审查不通过最多修正一次，再复核一次；复核仍失败或不可用时如实返回。`success=true` 仅表示答案生成成功。

## 尚未实施与后续任务

1. 统一 PostgreSQL/pgvector 存储、schema 发现、上传表格 JOIN 和数据库迁移。
2. embedding 使用 API，聊天模型与向量模型独立配置；不需要为此下载本地模型。
3. 业务文档混合检索、时间和权限过滤、稳定引用、文档版本管理。
4. 指标语义层与结构化分析状态；将参考日期显式纳入规划和审查上下文，针对本次日期歧义补专门评测。
5. 结构化证据校验、语义评分、完整调用成本统计、真实 PostgreSQL/PDF 集成验证。
6. Redis/Celery 和多人使用能力按后续阶段接入。

后续任何依赖安装、模型下载、镜像拉取或中间件部署，都需要先取得用户同意。
