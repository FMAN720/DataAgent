# 第七阶段：可观测性、评测与项目交付

日期：2026-10-22。按用户本轮选择，完成代码和离线验证，**不运行付费 API 评测**。没有下载安装依赖、模型、容器镜像或中间件，没有数据库迁移，没有新增外部模型请求。使用现有虚拟环境、Docker 中的 PostgreSQL/pgvector 和 Node。

## 完成内容

| 能力 | 交付 |
| --- | --- |
| 全链路用量 | 规划、执行、修正、初审与复审全部计数；聊天用量和 Embedding 输入用量分开；返回阶段耗时、逻辑调用数、HTTP 尝试数、失败数和缺失用量标记 |
| 并发隔离 | ContextVar 绑定单轮请求，并发会话各自计数；结束和异常时恢复上下文 |
| 接口兼容 | HTTP、SSE final 和 trace 均携带 usage_breakdown；execution_llm_calls 保留旧的执行/修正口径，llm_calls 改为全部聊天阶段 |
| 测试隔离 | pytest 仅在测试进程内关闭外部模型，阻止非回环网络连接；本地 PostgreSQL 可测试，MockTransport 不访问真实网络；不改写 .env |
| 50 题评测集 | SQL、周期对比、文档、上传表格、证据不足/拒答各 10 题；每类 6 道开发题、4 道留出题，合计 30/20 |
| 独立标准答案 | 从固定种子的 9,143 笔订单演示库及 4 行合成预算表执行 31 条标准 SQL；15 份仓库内合成文档提供来源要求；标准答案不进入 Agent 提示词 |
| 判分 | 同时检查最终数字/文本、实际查询证据、所需文档引用、对比总量核对、澄清时停止业务查询；空结果与失败单独保留 |
| 消融设计 | 完整配置 50 题；10 道固定代表题分别关闭向量检索、规划、审查，共 30 次对照；比较使用同一题目的配对结果 |
| 实验可复现 | 固定用例、数据和实现指纹；保留每题回答、工具轨迹、检查项、调用统计；断点续跑不重置预算，修改实现后不能在原冻结实验上覆盖留出成绩 |
| 外发与预算限制 | 真实运行须单独授权并显式开启；限制目标域名、模型、输出长度、请求数、内容量和估算预算；请求发送前持久化预留，未知用量保留预算占用；OS 文件锁避免同目录双进程花同一预算 |
| 数据范围 | 业务查询使用独立合成 SQLite；文档使用评测专属 UUID 和向量版本，检索强制限定范围；清理核对归属，级联删除自己的向量与任务，不删除用户文件 |
| 日志 | 模型 Key 不进入配置 repr；模型路由和熔断失败日志仅保留错误类型，不展开服务商错误正文和 SDK 异常链 |

核心实现为 [metering.py](../../app/infrastructure/llm/metering.py)、[run_phase7.py](../../eval/run_phase7.py)、[预算限制](../../eval/phase7_budget.py)、[数据隔离](../../eval/phase7_fixtures.py)、[判分规则](../../eval/phase7_grader.py) 和 [50 道用例](../../eval/phase7_cases.py)。

## 验证结果与边界

最终 **329 项 Python 测试全部通过（0 跳过）**，结果见 [pytest.xml](pytest.xml)；**6 项前端回归全部通过**，见 [ui-tests.xml](ui-tests.xml)；**12/12 条标准 SQL 跨数据库对照一致**，见 [sql-parity.json](sql-parity.json)。包括真实本地 PostgreSQL/pgvector 的索引、检索、任务租约、权限和清理验证，Embedding 使用合成向量；聊天使用脚本模型或模拟 HTTP。新增 31 项 Python 测试。4 个脚本分析集成场景另存为 [offline-analysis.json](offline-analysis.json)，不属于真实模型评测。

工作台已恢复到 <http://127.0.0.1:18080/>，就绪状态 ready，BGE-M3 保持启用，新增响应字段已加载，见 [runtime-smoke.json](runtime-smoke.json)。恢复前确认文档与待处理索引任务均为 0，没有因启动触发新的外部调用。Python 编译、JavaScript 语法、Git 空白检查通过，交付文件扫描未发现已配置的模型 Key 或数据库密码，见 [credential-audit.json](credential-audit.json)。机器可读验收汇总见 [verification.json](verification.json)。

第一轮全量测试为 325 通过、1 失败，保存在 [pytest-initial-failed.xml](pytest-initial-failed.xml)：新增测试隔离默认关闭文档服务，而旧 PostgreSQL API 测试依赖其默认开启。已在该测试配置中明确 documents_enabled=True，保留原有 ready、上传、列表断言。没有修改生产就绪判断或放宽断言。

[prepared.json](prepared.json) 保存 50 题、31 条独立 SQL 结果及数据指纹；[summary.json](summary.json) 的 live_status 为 not_run、completed_runs 为 0。这是“真实模型评测未运行”，不能解释为准确率 0%，也不能把单元测试通过率解释为真实问答准确率。

当前可以证明：代码路径、计数、规则、SQL 结果、权限、隔离与故障恢复符合测试。尚不能证明：50 题真实模型通过率、规划/反思带来的提升、模型实际 p50/p95、真实费用下降或一般业务问题的准确率。规则判分仍可能漏掉语义错误、周期标签交换、答案中额外的无据断言；即使后续全部通过，也需要逐题复核和更多独立数据。留出题目前仅做设计与 SQL 校验，未观察模型回答；后续若根据留出失败调参，必须标为事后分析并另建新的留出集。

普通请求的 usage_complete=false 表示用量不完整或存在失败/重试。HTTP 数是客户端尝试数，包含 SDK 重试，不证明服务商实际收到请求；断网或预发送预算拦截也不能据此推算账单。Embedding 耗时包含批次排队及重试，阶段耗时不相加代替端到端耗时；缓存命中 Token 是输入 Token 的子集，不重复相加。

本机 BGE-M3 在此前授权后已启用，本阶段没有把它关掉。测试通过进程环境隔离，不能根据测试开关推断真实工作台配置。此前启用验证见 [Embedding 记录](../embedding-activation/README.md)。

## 复现

仅准备合成数据和标准答案，不访问外部 API：

```powershell
.venv\Scripts\python.exe -m eval.run_phase7
```

常规离线测试；加入 RUN_POSTGRES_TESTS 后运行现有本地数据库集成验证：

```powershell
$env:RUN_POSTGRES_TESTS='1'
.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_tmp_phase7_local
node --test tests/workspace.test.cjs
.venv\Scripts\python.exe -m eval.phase2_parity --out reports/phase7/sql-parity.json
```

运行数据库集成测试前停止使用同一数据库队列的工作台 worker，以免并发领取测试任务。测试只清理自己创建并标记的合成资源。

真实评测入口是 `eval.run_phase7` 的 `--live` 和 `--approved-synthetic-api-budget` 两个参数；本轮用户选择离线，**没有执行该入口**。以后获得单独授权才运行，不因配置了 Key 而默认允许付费调用。

预置上限为：600 次聊天 HTTP、250 次标准 BGE-M3 HTTP、累计 800 万请求字符、每次聊天输出最多 2048 Token；以输入 10 元/百万 Token、输出 30 元/百万 Token 的保守计价上限累计，预留后超过 30 元则停止。这是脚本约束和估算口径，不是服务商实际资费或账单承诺。未知用量按预留计入；服务商报告用量超过预留时终止后续请求，不能撤销已完成请求。服务商、模型、资费或范围变化时应重新确认。已有账本不因重启自动清零。

## 面向简历和面试

项目已经具有工具选择、观察结果、SQL 自纠错、可选规划、证据审查、有限修正、多轮上下文与 RAG 的完整 Agent 链路。适合定位为“可演示、具备工程验证的数据分析 Agent 项目”；生产多租户隔离、登录权限、跨进程会话与追踪持久化仍有工作要做，不能直接称为企业生产平台。

可以据实使用的 4 行描述：

- 基于 FastAPI 和原生 function calling 实现电商分析 Agent，支持查表结构、业务口径检索、只读 SQL、自纠错和 SSE 执行轨迹。
- 接入 PostgreSQL/pgvector 与 API Embedding，实现文档混合检索、来源引用校验、CSV/Excel 导入及跨业务表查询，索引/导入任务支持租约与恢复。
- 实现规划、证据审查与一次受限修正；周期对比使用同一只读快照核对分项和独立总量，区分统计贡献与因果假设。
- 建立分阶段 Token/HTTP/耗时统计和 50 题合成评测协议，固定开发/留出划分、独立 SQL 标准答案及 3 组消融方案；真实模型效果待授权验证。

讲解时应能回答：

1. **为什么属于 Agent？** 模型在循环中选择工具并根据观察调整动作；规划与审查是可选角色模块，不等于多个独立自治 Agent 协商。
2. **为什么 SQL 只读还要数据库权限？** 应用校验和数据库权限分别防止错误操作；超时、行数上限及审查解决不同问题。
3. **为什么选 pgvector？** 复用现有 PostgreSQL 保存元数据、任务与向量，当前规模无需再引入独立向量中间件；这不是大规模吞吐的验证结论。
4. **审查通过就一定正确吗？** 不能；审查本身可能漏判、失败或拿不到完整证据，所以状态独立返回，失败或未完成结果不作为可信事实进入后续会话。
5. **如何证明优化有效？** 用冻结数据、留出题和配对消融对照回答；本轮没有真实模型成绩，不编造准确率或提升幅度。
6. **什么还没完成？** 真实模型效果验证、多租户认证授权、跨进程状态和生产部署压测；应根据目标岗位选下一项，而非继续堆中间件。
