# 项目来源与许可

DataAgent 由 [bcefghj/ai-agent-interview-guide](https://github.com/bcefghj/ai-agent-interview-guide) 的 `project-python` 示例发展而来。上游采用 MIT 许可证，本仓库的 [LICENSE](../LICENSE) 保留其原始版权声明和完整许可文本。

本仓库由 [Jiaqi Fan / FMAN720](https://github.com/FMAN720) 维护，首次公开版本来自本机已经持续开发的数据分析项目快照。后续实现包括电商分析工具与评测、PostgreSQL/Redis 持久化、文档检索与表格导入、图片核对、个人模型设置、版本化业务口径、数值校验和可恢复分析任务。基础示例的来源不作为独立原创成果声明。

发布快照包含应用、测试、迁移、合成样例及历史验收材料。真实 `.env`、随机数据库密码、个人模型配置、业务数据库、用户上传文件、虚拟环境和测试缓存不随仓库发布。演示数据库由 `app.demo.seed` 在本机生成。

`reports/` 与 `.impeccable/review/` 中的记录对应各阶段当时的代码、环境和检查范围。它们不表示新安装环境已经通过集成测试，也不表示历史真实模型评测覆盖了后续增加的全部功能。当前运行方式以根目录 README 为准。

本项目继续采用 MIT 许可证。依赖的第三方包保留各自的许可证；聊天、图片和向量 API 由使用者自行配置并遵守相应服务条款。
