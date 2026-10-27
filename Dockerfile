FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY app/ app/
COPY knowledge_base/ knowledge_base/
COPY mcp_server/ mcp_server/
COPY data/ data/
COPY eval/ eval/

RUN pip install --no-cache-dir .

EXPOSE 8000

# 默认离线模式：无需 API Key 即可端到端运行
ENV DR_MODE=offline DR_MCP_KB_ENABLED=true

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
