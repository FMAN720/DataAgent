# 第四阶段：CSV / Excel 导入与 BGE-M3 真实联调

完成日期：2026-10-22。本阶段交付表格导入后端，以及硅基流动 Embedding API 的小规模合成数据验收。未增加前端导入页面。

## 授权与实际变更

- 用户明确同意在现有 `.venv` 安装 `openpyxl` 及必要依赖。实际新增 `openpyxl==3.1.5`、`et-xmlfile==2.0.0`，依赖入口为 `requirements-imports.txt`。CSV 使用 Python 标准库。
- 用户明确允许最多 50 次 HTTP 请求（含重试）、5 万输入字符的 BGE-M3 合成数据联调。实际 27 次、331 字符，服务返回用量共 277 input tokens，失败 0 次。金额未从 token 数推算，实际计费以服务商账单为准。
- 未下载本地 Embedding 模型、BM25 包、pandas、新镜像或新中间件。复用现有 PostgreSQL / pgvector、独立 importer 账号和持久卷。
- 数据库已应用 `0003_dataset_imports`，新增 `agent.uploaded_datasets` 元数据表。业务表和原始 SQLite 文件保留。应用启动不会自行安装或迁移。
- 持续配置仍为 `EMBEDDING_ENABLED=false`。本次真实调用只来自 `eval/phase3_offline.py` 中公开可查看的合成 DOCS / CASES，没有读取用户上传文档；评测创建的数据在结束时删除。

## 新增能力

1. CSV 支持 UTF-8（含 BOM）、GB18030，逗号、分号、Tab、竖线分隔，以及引号、多行文本。
2. XLSX 支持指定工作表，默认首个可见表；返回工作表名供选择。拒绝公式、错误单元格、宏包和异常 XML，不执行文件内表达式。
3. 先保存源文件、显示字段映射与前 10 条预览，再由显式确认请求启动导入。预览阶段不创建分析表。
4. 推断文本、64 位整数、精确小数、布尔、日期、无时区时间和有时区时间；确认时可以覆盖类型，但转换必须无损。`001` 保留文本，金额不自动转换单位。空单元格为 SQL NULL，字符串 `NULL` 仍为文本。
5. 中文和特殊表头映射到唯一的受控列名；保留原始列名作为注释。表名固定生成 `uploads.dataset_<UUID去连字符>`，不能通过文件名或显示名称指定 SQL 表名。
6. `_row_number` 保存源逻辑记录序号（表头为 1；CSV 含引号多行时按记录而非物理行计数），支持回查来源。
7. PostgreSQL 保存导入状态、进度、错误及重试信息，后台使用 SKIP LOCKED、租约、心跳与 token 校验。重启后领取排队或过期任务。
8. 建表、分批插入和全量行摘要校验在同一事务内完成，失败回滚。数据已提交而状态未提交时，恢复任务核对来源、字段和数据摘要，不追加重复行、不覆盖其他表。
9. 相同文件和解析选项按指纹去重，并发由唯一约束兜底。删除先失效租约，再核对生成表的来源标记，清理表、受控源文件和元数据；文件占用则保存待清理状态。
10. Agent 新增只读 `list_datasets`。通过字段描述和 `run_sql` 查询上传表，与 `business` 表 JOIN；导入完成和删除后刷新表结构缓存。Agent 没有导入、删除或建表工具。

导入连接仅拥有 `uploads` 写权限；应用账号只管理 `agent` 元数据，分析账号保持只读。启动校验拒绝管理员和业务表写账号，不额外授予 importer 进入业务 schema 的权限。

## 使用方法

本机依赖和迁移已完成。启动现有虚拟环境中的应用后，打开 `http://127.0.0.1:8000/docs`，按下列顺序调用。所有路径均有 `/api/v1` 前缀。

| 方法 | 路径 | 参数 / 行为 |
| --- | --- | --- |
| POST | `/datasets/preview` | multipart `file`；CSV 可加 `encoding=gb18030`、`delimiter=;`；XLSX 可加 `sheet_name` |
| GET | `/datasets` | 可选 `ready_only=true`；列表不含预览行，避免响应过大 |
| GET | `/datasets/{id}` | 来源、字段、预览、状态、行数、错误 |
| POST | `/datasets/{id}/import` | JSON：`display_name`、`type_overrides`；`{}` 接受预览推断结果；返回 202 |
| POST | `/datasets/{id}/retry` | 对失败任务按已确认的原字段重试，返回 202 |
| DELETE | `/datasets/{id}` | 成功为 deleted；文件待清理返回 202 / cleanup_pending |

确认示例（覆盖键必须用预览返回的物理列名）：

```json
{
  "display_name": "十月区域补充金额（元）",
  "type_overrides": {"external_id": "text", "amount": "decimal"}
}
```

状态为 `previewed → queued → importing → ready`。数据库暂时故障会有限重试，源文件变化或类型校验失败进入 `failed`。显式 retry 重新计数；重复确认 queued / importing / ready 不会重复创建任务。`imported_rows` 在 importing 阶段表示已处理但尚未提交的行，只有 ready 表示事务和元数据均完成。删除中为 deleting，不允许同指纹重新上传，直到清理完成。

导入完成后可以提问：“列出已导入的数据集”“关联区域表，计算上传数据的金额总和”。本次端到端验收使用脚本模型与真实 SQL；真实聊天仍会按现有配置向聊天 API 发送字段、样例和查询结果，本轮未作此类真实调用。

默认限制：20 MiB 文件、5 万逻辑数据行、100 列、单元格 1.6 万字符、XLSX 解压后 100 MiB / 2048 个 ZIP 条目 / 100 个工作表。CSV 空白记录也计入扫描行数上限。预览字符串显示最多 200 字符，入库值不截断。可通过 `.env.example` 中 `IMPORT_*` 调整。本地源文件默认存于已忽略的 `data/imports/`。

## 验证结果

- [全量测试](tests.xml)：230 项全部通过，含真实 PostgreSQL 集成；模型响应由脚本或合成向量提供。
- [业务 SQL 对照](sql-parity.json)：12 / 12 通过，SQLite 与 PostgreSQL 结果一致。
- [实际启动检查](runtime-smoke.json)：核心、文档检索、导入服务均就绪；Agent 注册 list_datasets；自动 Embedding 关闭，检查未调用模型。
- 新增导入检查覆盖：多工作表、编码、前导零、长小数、NULL、类型覆盖、非法表头、大小限制、公式与异常 XML、并发去重、并发领取、权限、源文件篡改、租约失效、导入中删除、回滚、提交后恢复和脚本 Agent 的跨 schema JOIN。
- 授权范围检查覆盖：真实联调工作者只能领取指定合成文档任务；预算在 HTTP transport 层统计每次发送尝试，失败和重试也计数。该检查本身使用内存 HTTP，不消耗实际 API 额度。

复现离线回归（PowerShell，使用现有环境）：

```powershell
$env:RUN_POSTGRES_TESTS='1'
.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_tmp_phase4_verify
.venv\Scripts\python.exe -m eval.phase2_parity --out reports/phase4/sql-parity.json
```

`python -m eval.phase3_live` 会实际调用服务商并产生费用，需按授权范围单独运行；不要把它作为常规离线回归命令。此次授权的联调已完成，无需重复运行。

## BGE-M3 小样本结果与限制

真实接口为 `https://api.siliconflow.cn/v1/embeddings`；配置 `EMBEDDING_API_BASE` 使用 `https://api.siliconflow.cn/v1`，客户端自行追加路径。模型 `BAAI/bge-m3`，维度 1024。完整逐题与用量记录见 [bge-m3-live.json](bge-m3-live.json)。

评测仅 3 篇合成文档、12 个问题，其中 10 个可回答、2 个无答案：

| 检索方式 | 可回答问题 Hit@1 | Hit@3 / MRR | 无答案正确过滤 | 平均检索耗时 |
| --- | --- | --- | --- | --- |
| 关键词 | 70% | 70% / 0.70 | 2 / 2 | 48.17 ms |
| 向量 | 100% | 100% / 1.00 | 1 / 2 | 285.42 ms |
| 混合 | 100% | 100% / 1.00 | 1 / 2 | 311.92 ms |

该结果证明 API、维度与真实向量存取管道可用；样本太少，不能代表业务整体准确率，也没有评估真实 LLM 的最终回答质量。当前阈值 0.35 对一个无答案问题误召回；本轮保留默认值，避免根据 12 题过拟合。下一步应扩大有答案 / 无答案样本，区分阈值校准集与验收集，记录分数分布及拒答表现后再调整。

## 尚未包含及后续顺序

1. 表格前端预览、工作表选择、字段确认、进度、来源查看页面。当前可通过 Swagger 完整使用后端。
2. 更大且独立的 BGE-M3 检索和拒答评测，再考虑阈值、重排与查询改写；任何新依赖、服务或真实数据外发仍需用户同意。
3. 选定真实业务样本后，评估聊天最终回答和完整模型调用费用。自动向量化需要另行明确上传文档外发范围。
4. 大文件流式处理、导入预览的自动过期清理、权限与多用户隔离、持久化会话和 trace。当前单文件受限解析，源文件及解析结果会占用内存；只适用于本地原型规模。

只支持 CSV / XLSX，旧 XLS 不支持；XLSX 读取底层单元格值，不还原数字格式、合并单元格、图表或公式结果。例如数字单元格用 `000` 格式显示的前导零不能恢复，作为标识符应在源文件中存文本。多工作表一次导入一张；同一文件不同工作表按不同解析选项保存。

迁移 downgrade 只移除元数据表，保留 uploads 数据表；回退前必须备份元数据和源文件，否则会失去这些导入表的来源与管理记录。正常删除应通过数据集接口完成。
