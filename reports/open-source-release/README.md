# DataAgent 首次开源准备（2026-11-12）

本次从当前开发项目导出独立的 DataAgent 源码快照，保留 MIT 许可与上游来源，更新仓库名称、安装入口、包元数据及工作台名称，并加入贡献指南和 GitHub Actions 离线检查。

## 本地验证

| 检查 | 结果 |
|---|---|
| Python 3.12 离线回归 | 536 通过，97 跳过；25.67 秒 |
| Node.js 工作台逻辑 | 55 通过，0 失败 |
| Ruff 静态与格式 | 通过；171 个 Python 文件符合格式 |
| mypy app eval | 通过；110 个源文件 |
| Markdown 本地相对链接 | 无缺失目标 |
| 发布文件检查 | 未检出本机真实模型 Key、随机数据库密码或已知格式令牌 |
| Git 忽略规则 | 真实环境文件、运行数据、上传、虚拟环境与测试缓存均被排除；保留 `.env.example` |

97 项跳过的测试需要显式启用专用 PostgreSQL/Redis，本次没有启用，也没有调用真实聊天、向量或视觉模型。Windows 沙箱中的第一次 Python 测试停在 asyncio 本机 socketpair 初始化；停止该进程后，在沙箱外运行同一套离线测试得到上表结果，未更改测试断言。

历史测试转录中的空白保持原样；发布代码与文档按 Git 属性统一文本行尾。GitHub 上的检查状态以 [Actions](https://github.com/FMAN720/DataAgent/actions/workflows/ci.yml) 为准，本地通过不代替远端 CI 结果。
