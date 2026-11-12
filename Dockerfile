FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# 可选功能依赖：构建时用 --build-arg 开启，默认只装核心依赖
#   INSTALL_POSTGRES  PostgreSQL 分析库、文档库、导入、图片草稿和分析历史
#   INSTALL_REDIS     Redis 共享会话
#   INSTALL_DOCUMENTS 文本 PDF 解析
#   INSTALL_IMPORTS   XLSX 导入
#   INSTALL_VISION    图片与扫描 PDF
ARG INSTALL_POSTGRES=false
ARG INSTALL_REDIS=false
ARG INSTALL_DOCUMENTS=false
ARG INSTALL_IMPORTS=false
ARG INSTALL_VISION=false
COPY requirements-postgres.txt requirements-redis.txt requirements-documents.txt \
     requirements-imports.txt requirements-vision.txt ./
RUN set -e; \
    if [ "$INSTALL_POSTGRES" = "true" ]; then pip install -r requirements-postgres.txt; fi; \
    if [ "$INSTALL_REDIS" = "true" ]; then pip install -r requirements-redis.txt; fi; \
    if [ "$INSTALL_DOCUMENTS" = "true" ]; then pip install -r requirements-documents.txt; fi; \
    if [ "$INSTALL_IMPORTS" = "true" ]; then pip install -r requirements-imports.txt; fi; \
    if [ "$INSTALL_VISION" = "true" ]; then pip install -r requirements-vision.txt; fi

COPY app ./app
COPY eval ./eval
COPY migrations ./migrations
COPY alembic.ini pyproject.toml README.md LICENSE ./

# 构建时生成演示库，容器启动即可用
RUN python -m app.demo.seed /app/data/ecommerce.db

# 以非 root 用户运行；上传、导入和图片草稿目录挂载为数据卷
RUN useradd --create-home appuser \
    && mkdir -p /app/uploads /app/data/imports /app/data/vision /home/appuser/.config/DataAnalystAgent \
    && chown -R appuser /app /home/appuser/.config \
    && chmod 700 /home/appuser/.config/DataAnalystAgent
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
