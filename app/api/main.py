"""FastAPI 入口：M1 提供 research 与 reports 两个端点。

M3 起 report 与 trace 一同持久化到 SQLite，并新增 GET /api/traces/{id}。
"""

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from app.config import Settings
from app.graph.factory import build_default_app


class ResearchRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500, description="研究型问题")
    user_id: str = Field(default="default", description="用户标识（M2 起关联跨会话记忆）")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    app.state.settings = settings
    app.state.graph = build_default_app(settings)
    app.state.reports = {}  # M3 换 SQLite 持久化
    yield


app = FastAPI(
    title="Deep Research API",
    description="垂直领域（技术选型调研）多智能体研究系统",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "mode": app.state.settings.mode}


@app.post("/api/research", status_code=201)
def create_research(req: ResearchRequest) -> dict:
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    try:
        final_state = app.state.graph.invoke(
            {"question": req.question, "user_id": req.user_id, "run_id": run_id},
            config={"recursion_limit": max(app.state.settings.max_steps * 4, 64)},
        )
    except GraphRecursionError as exc:
        raise HTTPException(status_code=504, detail="研究流程超出步数上限，已中止") from exc
    report = final_state.get("report")
    if report is None:
        raise HTTPException(status_code=500, detail="研究流程未产出报告")
    app.state.reports[report.report_id] = report
    return report.model_dump()


@app.get("/api/reports/{report_id}")
def get_report(report_id: str) -> dict:
    report = app.state.reports.get(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return report.model_dump()
