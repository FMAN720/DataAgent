# 视觉模型配置启用

2026-10-25，用户选择 DeepSeek V4.1 Flash 1017，并已明确要求复用项目现有硅基流动 Key。

- API Base：`https://api.siliconflow.cn/v1`
- API 模型 ID：`deepseek-ai/DeepSeek-V4.1-Flash`
- `VISION_ENABLED=true`，视觉配置与 Embedding 使用同一已有 Key；没有申请新 Key，没有在报告中记录密钥。
- 仅修改视觉运行配置；聊天模型、BGE-M3 的模型与向量检索配置未改。

[硅基流动官方模型中心](https://www.siliconflow.cn/models)标明该模型支持图像与文本理解；[2026-10-21 官方更新公告](https://docs.siliconflow.cn/docs/release-notes/overview)说明模型 ID 保持不变，请求已由 1017 版本提供服务，API ID 不追加 `-1017`。

已重启本机工作台并通过只读检查：就绪状态为 ready，视觉客户端初始化成功，工作台返回正确模型名和可识别状态。记录见 [configuration.json](configuration.json)。

没有下载安装、没有发送图片/文档、没有执行聊天或 Embedding 付费推理。当前验证的是配置加载，不代表真实账户额度、图片识别质量或 JSON 转录兼容性已实测。后续每次在工作台勾选同意发送所选页并点击“识别所选页”才会调用模型；不会自动识别或自动重试。

第三轮最初关闭视觉 API 时的离线验收和截图保留在 `reports/vision-round3/`，属于历史验证记录。启用后的状态以本记录为准。
