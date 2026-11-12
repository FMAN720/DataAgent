# 参与 DataAgent

欢迎通过 Issue 报告可复现的问题，或通过 Pull Request 提交范围清晰的修复和改进。较大的功能变更建议先在 Issue 中讨论数据流、权限边界与验证方式。

## 开发环境

需要 Python 3.11+；前端测试使用 Node.js 22+，不需要安装前端依赖。

```bash
git clone https://github.com/FMAN720/DataAgent.git
cd DataAgent
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt -r requirements-optional.txt
```

## 提交前检查

```bash
python -m pytest
node --test tests/workspace.test.cjs
ruff check app eval tests lab
ruff format --check app eval tests lab
mypy app eval
```

默认 Python 测试使用脚本模型并阻断外部网络；需要专用 PostgreSQL 或 Redis 的集成测试会跳过。显式集成测试的准备方式与开关见 [README](README.md#测试与评测)。不要为提交修复运行付费评测或连接生产数据库。

PR 请说明触发条件、修改后的行为与实际验证结果；错误修复应包含能重现问题的回归检查。业务样例须使用合成数据，提交前检查 diff 中是否包含 `.env`、API Key、数据库密码或个人文件。

提交贡献即表示你有权提供这些改动，并同意按本项目的 MIT 许可证发布。
